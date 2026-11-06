"""Identity Kernel — Agent identity + permissions

The Identity Kernel manages agent identity, permissions, and audit trails.
Supports scope-aware filtering (L0-L7) and complete audit logging.

**This module is the single authoritative identity implementation**
(PHASE 3.6 / T-M, closing F29 ⑥ and F30 ①). ``src.identity`` is an archived
non-authoritative copy with zero importers; nothing may import it. The claim is
machine-checked by ``scripts/verify_single_identity_root.py``, which parses every
Python file for real import statements rather than grepping text, so a prose
mention can neither satisfy nor violate it.

依据 Definition Lock §112: Identity Kernel 必须能够
- create_identity(principal, permissions, scope, trust_score)
- grant_permission(identity, permission, scope)
- audit_trail(identity.id, scope)
- Support scope-aware filtering L0-L7
- Provide complete audit trail for all permission operations
"""
from __future__ import annotations
from src.kernels._base import KernelLifecycle, KernelStateError

from dataclasses import dataclass, field
from datetime import datetime
from src._time import utc_now
from enum import Enum
import hashlib
import logging
import os
import re
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple
import uuid
import threading

from src.kernels._crosscutting import kernel_action, mark_action_denied
from . import _persistence
from . import _accounts
from . import _backup

logger = logging.getLogger("liuhao.kernel.identity")

# --------------------------------------------------------------------------- #
# T-M — declared single identity root
# --------------------------------------------------------------------------- #
# Two identity implementations existed and neither said which was authoritative.
# Declaring the winner is what F29 ⑥(b) requires; the CI assertion in
# ``scripts/verify_single_identity_root.py`` is ⑥(c)'s import-side check. The
# *startup* half of ⑥(c) (refuse to boot if the running implementation's
# fingerprint differs from a frozen value) is deliberately NOT implemented here:
# it turns an availability switch, so its blast radius has to be understood
# before it is armed. It is recorded as a residual risk, not silently added.

#: The one identity implementation the system is allowed to use.
AUTHORITATIVE_IDENTITY_MODULE = "src.kernels.identity"

#: Copies that exist for historical reasons and may never be imported.
NON_AUTHORITATIVE_IDENTITY_MODULES = ("src.identity",)


def identity_implementation_fingerprint(path: Optional[str] = None) -> str:
    """SHA-256 of this module's source, so "which implementation ran" is answerable.

    Independent verification is impossible without this: two builds that differ
    only inside the identity implementation are, from the outside, the same
    build. Returning a fingerprint makes the difference observable.

    ``path`` is for tests that want to fingerprint a *different* file without
    importing it; the default is this module's own source.
    """
    target = path or __file__
    with open(target, "rb") as handle:
        return hashlib.sha256(handle.read()).hexdigest()


def identity_implementation_manifest() -> Dict[str, Any]:
    """The declared identity root, as data.

    Reports the authority, the archived copies, and the fingerprint of each, so a
    verification step can compare what is declared against what is present
    without trusting either.
    """
    from pathlib import Path

    root = Path(__file__).resolve().parents[3]
    manifest: Dict[str, Any] = {
        "authoritative": AUTHORITATIVE_IDENTITY_MODULE,
        "non_authoritative": list(NON_AUTHORITATIVE_IDENTITY_MODULES),
        "fingerprints": {},
    }
    for module_name in (AUTHORITATIVE_IDENTITY_MODULE,) + NON_AUTHORITATIVE_IDENTITY_MODULES:
        candidate = root.joinpath(*module_name.split(".")[1:], "__init__.py")
        entry = {"present": candidate.is_file(), "path": str(candidate)}
        if candidate.is_file():
            entry["fingerprint"] = identity_implementation_fingerprint(str(candidate))
        manifest["fingerprints"][module_name] = entry
    return manifest


#: Principal / id of the built-in internal service identity.
#:
#: Canonical home: the Identity Kernel owns which identities exist. The
#: Policy Kernel references the identity only through its ``metadata["kind"]``
#: marker and never needs this name; ``_crosscutting`` imports it lazily to
#: attribute kernel actions.
INTERNAL_SERVICE_PRINCIPAL = "liuhao-internal-service"

#: ``metadata`` key holding an identity's kind, and its two sanctioned values.
#:
#: These three names are the single source of truth for "is this actor a
#: human?". Both consumers -- the Policy Kernel's ``_is_verified_human`` and
#: the sovereignty channel's ``_validated_principal`` -- must ask
#: ``is_human_identity`` rather than re-deriving the answer from raw metadata.
METADATA_KIND_KEY = "kind"
SERVICE_KIND = "service"
HUMAN_KIND = "human"

#: Where registered human identities are persisted.
#:
#: Re-exported from :mod:`._persistence`, which owns the storage layer and
#: documents each backend. Kept importable from here because operator tooling
#: (``scripts/register_human_identity.py``) has always resolved the variable
#: through this module.
HUMAN_IDENTITIES_FILE_ENV = _persistence.HUMAN_IDENTITIES_FILE_ENV

#: Which persistent backend to use (``"file"`` default, or ``"sqlite"``), and
#: the SQLite path when that backend is selected. See :mod:`._persistence`.
HUMAN_IDENTITIES_BACKEND_ENV = _persistence.HUMAN_IDENTITIES_BACKEND_ENV
HUMAN_IDENTITIES_DB_ENV = _persistence.HUMAN_IDENTITIES_DB_ENV

#: Env var holding the key used to authenticate registry rows (HC-11 / U6).
#: Re-exported from :mod:`._persistence` for the same reason as the location
#: variables above -- operator tooling and tests resolve it through this module.
HUMAN_IDENTITIES_INTEGRITY_KEY_ENV = _persistence.HUMAN_IDENTITIES_INTEGRITY_KEY_ENV

#: ``metadata`` key for a human-friendly name, shown by operators' tooling.
#:
#: Must stay equal to ``_persistence.FIELD_DISPLAY_NAME``: the store serialises
#: a registration under this exact key and the kernel reads it back out of
#: ``AgentIdentity.metadata``. ``tests/kernels/identity/test_human_identity_persistence.py``
#: pins the two together so they cannot drift apart silently.
METADATA_DISPLAY_NAME_KEY = "display_name"

# --------------------------------------------------------------------------- #
# PHASE 3.6 / A1 containment — identity namespaces (R-15b)
# --------------------------------------------------------------------------- #
#
# Before this block existed, three key conventions shared one dict:
#
#   built-ins  ``_identities["system"]``, ``_identities["liuhao-internal-service"]``
#   humans     ``_identities[principal]``                 (``id == principal``)
#   agents     ``_identities[str(uuid4())[:8]]``
#
# and **nothing guarded the boundaries**. Measured failure (R-15, PHASE 3.5):
# ``_seed_human_identities`` deduplicated only against ``_principal_index``
# (principal -> id) and never against ``_identities`` (the id namespace). A
# registry row whose principal equalled a *known agent id* therefore passed the
# check, was built with ``id = principal`` and **overwrote that agent's slot**,
# carrying a hardcoded ``trust_score = 1.0`` plus the row's permissions. That is
# deterministic privilege escalation available on day one, needing no birthday
# luck, and it left no audit trace -- the collision branch only logged a
# warning. The registry itself is a plain unsigned JSON file, so the attacker's
# only prerequisite is write access to it (PHASE 3.5 §R-15b).
#
# The fix has two halves:
#
# 1. **Disjoint namespaces.** A human id is always ``human:<principal>``; an
#    agent id is always 8 hex chars. An id can no longer be in both namespaces,
#    so the collision is structurally impossible rather than merely detected.
# 2. **A single guarded writer.** Every write into ``_identities`` /
#    ``_principal_index`` goes through :meth:`IdentityManager._claim_slot`,
#    which refuses occupied slots, refuses cross-namespace overwrites (= the
#    structural form of "trust inheritance prohibition"), and **audits** every
#    refusal.
#
# The prefix is *reserved*: no identity may be created with a principal that
# starts with it, which is what stops an agent from shadowing a human's id
# string (or a registration from nesting ``human:human:x``).

#: Reserved id prefix for human identities.
HUMAN_ID_PREFIX = "human:"

#: Namespace tags carried by :attr:`AgentIdentity.namespace`.
NAMESPACE_HUMAN = "human"
NAMESPACE_SERVICE = "service"
NAMESPACE_AGENT = "agent"

#: Trust score given to a seeded human when the registry row carries no
#: explicit ``trust_score``. Kept at the historical 1.0 so no existing
#: deployment changes behaviour -- but no longer *hardcoded*: the number is now
#: read from the row and recorded alongside provenance, so "a registered human
#: is maximally trusted" is a stated fact about a record rather than an
#: artefact of the seeding loop. (Nothing in the policy or sovereignty layer
#: consults ``trust_score`` when deciding whether an actor is human -- verified
#: by ``grep -rn trust_score src/kernels/policy src/kernels/_sovereignty.py``,
#: zero hits -- so this value carries no authority by itself.)
SEEDED_HUMAN_TRUST_SCORE = 1.0

#: Hex widths an agent identity id may have, narrowest first.
#:
#: **This tuple is the single source of truth for "what shape can an agent id
#: have?"** Both the allocator (:meth:`IdentityManager._allocate_agent_id`) and
#: the reserved-id predicate (:func:`is_agent_id`) are derived from it, so the
#: allocator cannot be widened without the check widening with it.
#:
#: This coupling is the fix for a measured A1 containment gap: the predicate
#: was pinned to ``^[0-9a-f]{8}$`` while the allocator widened to 16 hex on
#: saturation, so ``is_agent_id(<16 hex>)`` was False, a HUMAN whose principal
#: equalled a widened agent id was admitted as ``human:<id>``, and the id
#: lookup and the principal lookup then resolved that one name to two different
#: subjects (agent/admin vs human/admin) -- the exact impersonation A1 exists
#: to prevent.
_AGENT_ID_HEX_WIDTHS: tuple = (8, 16)

#: Width of a normal agent id, and of the widened one used on saturation.
#: Derived, not restated: the lengths are written down exactly once, above.
_PRIMARY_AGENT_ID_WIDTH = _AGENT_ID_HEX_WIDTHS[0]
_WIDENED_AGENT_ID_WIDTH = _AGENT_ID_HEX_WIDTHS[-1]

#: Pattern of an agent identity id -- every width the allocator can mint.
_AGENT_ID_RE = re.compile(
    r"^(?:%s)$" % "|".join(r"[0-9a-f]{%d}" % width for width in _AGENT_ID_HEX_WIDTHS)
)

#: How many times :meth:`IdentityManager._allocate_agent_id` retries at the
#: primary width before widening the id. Sixteen misses on the primary hex
#: space means the space is saturated, not unlucky.
_AGENT_ID_ALLOCATION_ATTEMPTS = 16


def is_agent_id(value: str) -> bool:
    """True iff ``value`` is shaped like an agent identity id."""
    return bool(isinstance(value, str) and _AGENT_ID_RE.match(value))


def normalize_principal(principal: str) -> str:
    """Strip any reserved id prefix from ``principal``.

    ``"human:x"`` and ``"x"`` name the *same* registration: a human id is
    always ``human:<principal>``, so the two inputs produce an identical
    ``AgentIdentity.id`` and an identical ``_principal_index`` key, and the
    namespace + collision checks that decide whether the write is allowed run
    on the normalized name either way.

    This is deliberately a *normalization*, not a refusal. Refusing the
    already-prefixed form would add no safety -- the ambiguity R-15b exploited
    came from the id *string space* being shared, not from this spelling -- and
    it would break callers that already write ``human:<name>``. All prefixes are
    stripped, so ``"human:human:x"`` collapses to ``"x"`` rather than nesting.
    """
    value = principal if isinstance(principal, str) else str(principal)
    while value.startswith(HUMAN_ID_PREFIX):
        value = value[len(HUMAN_ID_PREFIX):]
    return value


def is_reserved_principal(principal: str) -> bool:
    """True when ``principal`` cannot name a registration at all.

    Only the degenerate case (``""``, ``"human:"``) qualifies: after prefix
    stripping there is no name left, so the id would be a bare prefix.
    """
    return not normalize_principal(principal).strip()


def namespace_disjoint(identity: AgentIdentity) -> bool:
    """True iff ``identity``'s id and namespace cannot collide with another's.

    This is the invariant ``_claim_slot`` enforces on every write: a human id
    must never be an agent-shaped id, and a non-human id must never wear the
    reserved human prefix.
    """
    if identity.namespace == NAMESPACE_HUMAN:
        return identity.id.startswith(HUMAN_ID_PREFIX) and not is_agent_id(identity.id)
    return not identity.id.startswith(HUMAN_ID_PREFIX)


def is_human_identity(identity: Optional[AgentIdentity]) -> bool:
    """Return True iff ``identity`` is a registered, ACTIVE *human*.

    This is a **positive allowlist**, not a reverse exclusion. An identity
    qualifies only by carrying ``metadata["kind"] == "human"``; merely *not*
    being a service is not evidence of being human.

    Why that distinction matters (Policy C-7): the previous test was
    ``metadata.get("kind") != "service"``, which **fails open** for any
    identity that simply lacks the marker. The built-in ``system`` identity
    (auto-created, ``metadata={}``) therefore satisfied "verified human" and
    could hold human sovereignty -- the audit trail recorded a machine as the
    approver of a CRITICAL action, defeating the accountability chain that
    OD-010 exists to establish.
    """
    if identity is None:
        return False
    if getattr(identity, "status", None) != IdentityStatus.ACTIVE:
        return False
    metadata = identity.metadata if isinstance(identity.metadata, dict) else {}
    return metadata.get(METADATA_KIND_KEY) == HUMAN_KIND


class IdentityScope(str, Enum):
    """Identity permission scope L0-L7."""
    L0 = "L0"  # Human only
    L1 = "L1"
    L2 = "L2"
    L3 = "L3"
    L4 = "L4"
    L5 = "L5"
    L6 = "L6"
    L7 = "L7"


class IdentityStatus(str, Enum):
    """Identity lifecycle status."""
    ACTIVE = "active"
    SUSPENDED = "suspended"
    DEACTIVATED = "deactivated"


@dataclass
class AgentIdentity:
    """Agent identity with permissions and trust."""
    id: str
    principal: str  # Unique principal identifier
    permissions: Set[str] = field(default_factory=set)
    scope: IdentityScope = IdentityScope.L1
    trust_score: float = 0.5
    status: IdentityStatus = IdentityStatus.ACTIVE
    created_at: datetime = field(default_factory=utc_now)
    last_modified: datetime = field(default_factory=utc_now)
    metadata: Dict[str, Any] = field(default_factory=dict)
    correlation_id: str = field(default_factory=lambda: str(uuid.uuid4()))

    # -- PHASE 3.6 / A1: immutable principal identity --------------------- #

    def __setattr__(self, name: str, value: Any) -> None:
        """``id`` and ``principal`` are immutable once set.

        The authorization model resolves a principal to an identity and then
        trusts *that identity's* permissions. An object whose ``principal`` can
        be reassigned is therefore an object whose authority can be moved to a
        different name after the fact -- identity laundering. Nothing in the
        kernel ever mutated these two fields (verified by
        ``grep -rn "\\.id = \\|\\.principal = " --include=*.py src/ tests/``,
        no hit on ``AgentIdentity``), so the guard costs nothing and closes the
        class rather than the instance.
        """
        if name in ("id", "principal") and name in self.__dict__:
            raise AttributeError(
                f"AgentIdentity.{name} is immutable: cannot set it to "
                f"{value!r} (current value {self.__dict__[name]!r})"
            )
        super().__setattr__(name, value)

    @property
    def namespace(self) -> str:
        """Which identity namespace this object belongs to."""
        kind = (self.metadata or {}).get(METADATA_KIND_KEY)
        if kind == HUMAN_KIND:
            return NAMESPACE_HUMAN
        if kind == SERVICE_KIND:
            return NAMESPACE_SERVICE
        return NAMESPACE_AGENT

    @property
    def fingerprint(self) -> str:
        """Canonical, collision-free identifier for this identity.

        This is the single primitive the audit side and the authorization side
        must both converge on (PHASE 3.6 / A2). It is derived from
        ``namespace`` + ``principal``, so it survives a restart, is stable for
        the life of the identity, and does **not** inherit the 32-bit
        collision surface of :attr:`id`.
        """
        return hashlib.sha256(
            f"{self.namespace}\x00{self.principal}".encode("utf-8")
        ).hexdigest()[:32]


@dataclass
class AuditEntry:
    """Audit log entry for permission operations."""
    id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    identity_id: str = ""
    operation: str = ""  # "grant", "revoke", "modify"
    permission: Optional[str] = None
    scope: IdentityScope = IdentityScope.L0
    result: str = ""  # "allowed", "denied", "audit"
    reason: str = ""
    timestamp: datetime = field(default_factory=utc_now)
    correlation_id: str = field(default_factory=lambda: str(uuid.uuid4()))


class IdentityManager:
    """Manages agent identities, permissions, and audit trails."""
    lifecycle: KernelLifecycle = KernelLifecycle.UNINITIALIZED

    def __init__(self):
        self._identities: Dict[str, AgentIdentity] = {}  # id -> AgentIdentity
        self._principal_index: Dict[str, str] = {}  # principal -> id
        self._audit_log: List[AuditEntry] = []
        self._lock = threading.RLock()

        # Default system identity
        if "system" not in self._identities:
            system_identity = AgentIdentity(
                id="system",
                principal="system",
                permissions={"admin"},
                scope=IdentityScope.L0,
                trust_score=1.0,
            )
            self._identities["system"] = system_identity
            self._principal_index["system"] = "system"

        # Built-in internal service identity (Policy C-1).
        #
        # Kernel actions are performed by the system's own code. Attributing
        # them to this principal (instead of an anonymous {"type": "system"}
        # actor that no built-in rule could ever allow) is what lets the
        # Policy Kernel record an informative verdict.
        #
        # ``metadata["kind"] == "service"`` is the marker the Policy Kernel
        # requires (``_is_verified_service``): it keeps this identity from
        # being usable as a human identity and vice versa. Scope is L0 and
        # ``permissions`` is empty on purpose -- the service holds no
        # authority of its own; it is only pre-approved for the action
        # allow-list held in the Policy Kernel.
        if INTERNAL_SERVICE_PRINCIPAL not in self._identities:
            service_identity = AgentIdentity(
                id=INTERNAL_SERVICE_PRINCIPAL,
                principal=INTERNAL_SERVICE_PRINCIPAL,
                permissions=set(),
                scope=IdentityScope.L0,
                trust_score=1.0,
                metadata={
                    "kind": "service",
                    "description": "Internal kernel service principal",
                },
            )
            self._identities[INTERNAL_SERVICE_PRINCIPAL] = service_identity
            self._principal_index[INTERNAL_SERVICE_PRINCIPAL] = INTERNAL_SERVICE_PRINCIPAL

        # Durable registry of humans, resolved once per manager. An
        # unconfigured store resolves to a JSON store with an empty location,
        # whose writes are no-ops -- so nothing changes for deployments that
        # never opted in.
        self._store = _persistence.resolve_human_identity_store()
        #: Reasons the registry refused an entry at boot. Non-empty means the
        #: store claimed more humans than the kernel admitted -- surfaced by
        #: :meth:`describe_identity_namespaces` so it cannot be a silent fact.
        self._registry_refusals: List[str] = []
        #: 账号树（principal -> AccountNode）。单机单根；由 store 派生 + 运行时
        #: 写入。空 dict 表示"尚未 bootstrap"，不是"没有账号"。
        self._accounts: Dict[str, _accounts.AccountNode] = {}
        self._accounts_booted = False
        if self._store.location and not _persistence.integrity_enforced():
            # A registry is in use but no integrity key is configured. Per the
            # "enforce key" posture (HC-11 / U6) the store therefore refuses
            # every row at load time (fail-closed): it holds zero admitted
            # humans until the key is set. This warning exists so the operator
            # knows WHY no humans were admitted, and that setting the variable is
            # required -- not optional -- for the registry to be usable.
            logger.warning(
                "human identity registry %r is NOT authenticated: %s is unset, "
                "so every stored row is refused at load (fail-closed) and the "
                "registry holds no admitted humans. Anything that can write that "
                "store can otherwise create or edit the rows that decide who "
                "holds sovereignty (PHASE 3.6 / A3). Set the variable to enable "
                "row authentication and admit the registered humans.",
                self._store.location,
                _persistence.HUMAN_IDENTITIES_INTEGRITY_KEY_ENV,
            )
        self._seed_human_identities()
        self._bootstrap_account_tree()

    # ------------------------------------------------------------------ #
    # PHASE 3.6 / A1: the single guarded writer
    # ------------------------------------------------------------------ #

    def _audit(
        self,
        *,
        identity_id: str,
        operation: str,
        scope: IdentityScope,
        result: str,
        reason: str,
        permission: Optional[str] = None,
    ) -> AuditEntry:
        """Append an identity audit entry.

        One helper, so no write path can *forget* to audit. The collision
        branch this replaces logged a warning and returned: the single most
        security-relevant event this kernel can observe -- one identity trying
        to take another's slot -- was the one event with no audit record.
        """
        entry = AuditEntry(
            identity_id=identity_id,
            operation=operation,
            permission=permission,
            scope=scope,
            result=result,
            reason=reason,
        )
        self._audit_log.append(entry)
        return entry

    def _record_denial_on_the_chain(
        self, *, principal: str, reason: str, action: str = "identity.register"
    ) -> None:
        """Write a refusal to the **authoritative** audit chain (A1 / U-1).

        ``_audit_log`` is an in-process list: it dies with the process, has no
        production reader, and is not part of the hash chain. Measured
        2026-09-21 on the pre-containment tree:
        ``grep -c "log_event" src/kernels/identity/*.py src/kernels/policy/*.py
        src/kernels/security/*.py`` returned **0** -- nothing on the governance
        path writes to the chain at all. A refusal recorded only in the local
        list is, evidentially, not recorded.

        The seed path needs this explicitly because ``_seed_human_identities``
        runs inside ``__init__`` and is deliberately *not* a ``@kernel_action``
        (a policy verdict would call back into ``IdentityManager`` and recurse
        without bound), so there is no decorator to carry the denial.

        A failure to write is logged at ERROR and never raised: an audit outage
        must not take down identity resolution.
        """
        try:
            from src.kernels.audit import log_event, AuditEventType, AuditScope

            log_event(
                event_type=AuditEventType.ACCESS_DENIED,
                principal_id=principal,
                scope=AuditScope.L0,
                outcome="denied",
                details={
                    "action": action,
                    "reason": reason,
                    "human_id_prefix": HUMAN_ID_PREFIX,
                    "registry_backend": self._store.backend_name,
                    "registry_location": self._store.location,
                },
            )
        except Exception as exc:  # pragma: no cover - defensive
            logger.error(
                "could not record identity refusal on the audit chain "
                "(principal=%r): %s", principal, exc,
            )

    def _allocate_agent_id(self) -> str:
        """Allocate an unused agent id.

        ``str(uuid.uuid4())[:8]`` is 32 bits, so a birthday collision is not
        hypothetical once identities accumulate (~1.2 % at 10k). Previously a
        colliding id silently overwrote the incumbent. Here a collision just
        picks another candidate, and the search is bounded -- it can only ever
        fail *closed* (wider id + loud log), never overwrite.

        Both widths come from ``_AGENT_ID_HEX_WIDTHS``, which :func:`is_agent_id`
        also derives from: widening here cannot leave the reserved-id check
        behind.
        """
        for _ in range(_AGENT_ID_ALLOCATION_ATTEMPTS):
            candidate = str(uuid.uuid4())[:_PRIMARY_AGENT_ID_WIDTH]
            if candidate not in self._identities:
                return candidate
        widened = uuid.uuid4().hex[:_WIDENED_AGENT_ID_WIDTH]
        logger.error(
            "agent id space appears saturated: %d consecutive 8-hex collisions; "
            "widening the id for this identity to %r (32-bit birthday collisions "
            "at scale are now a real failure mode, not a theoretical one)",
            _AGENT_ID_ALLOCATION_ATTEMPTS, widened,
        )
        return widened

    def _claim_slot(self, identity: AgentIdentity, *, origin: str) -> bool:
        """Claim ``identity.id`` and ``identity.principal`` for ``identity``.

        **The only writer** into ``_identities`` / ``_principal_index``. It
        replaces every plain ``self._identities[...] = identity`` assignment,
        because a plain assignment silently overwrites whatever occupied the
        slot -- which is precisely how R-15 escalated privilege.

        Refuses, returning ``False``, when:

        * the id slot is held by a different object -- **including** when the
          incumbent sits in another namespace. A human registration must never
          land on an agent slot and vice versa; that refusal is the structural
          form of "trust inheritance prohibition", because it removes the only
          mechanism by which one identity could acquire another's
          ``trust_score`` and permissions;
        * the principal already resolves to a different identity (no re-pointing);
        * the namespace invariant :func:`namespace_disjoint` is violated.

        A refusal is always audited as ``denied`` and logged at ERROR. Nothing
        here fails open and nothing here fails silently.
        """
        def _refuse(reason: str) -> bool:
            logger.error("refusing identity write (origin=%s): %s", origin, reason)
            self._audit(
                identity_id=identity.id,
                operation="create",
                scope=identity.scope,
                result="denied",
                reason=f"{reason} (origin={origin})",
            )
            self._registry_refusals.append(reason)
            # U-1: a refusal reported by returning is still a refusal. Without
            # this the enclosing @kernel_action records outcome="success", so an
            # impersonation attempt reads as a successful creation in the
            # authoritative chain. Harmless on the undecorated seed path: the
            # decorator clears the signal at the start of every wrapped call.
            mark_action_denied(reason)
            return False

        if not namespace_disjoint(identity):
            return _refuse(
                f"identity {identity.namespace}:{identity.principal!r} violates the "
                f"namespace invariant (id={identity.id!r})"
            )

        if identity.namespace == NAMESPACE_HUMAN and is_agent_id(identity.principal):
            # A human principal shaped like an agent id makes the two lookup
            # paths disagree: ``get_identity(p)`` resolves to the agent while
            # ``get_identity_by_principal(p)`` resolves to the human. That
            # ambiguity has no legitimate use, and the registry producing such
            # rows is attacker-writable -- so it is refused outright
            # (fail-closed) rather than tolerated with a comment.
            return _refuse(
                f"human principal {identity.principal!r} is shaped like an agent "
                f"id; the id-lookup and principal-lookup paths would disagree"
            )

        incumbent = self._identities.get(identity.id)
        if incumbent is not None and incumbent is not identity:
            return _refuse(
                f"id slot {identity.id!r} is occupied by "
                f"{incumbent.namespace}:{incumbent.principal!r}; refusing to "
                f"overwrite it with {identity.namespace}:{identity.principal!r}"
            )

        existing_id = self._principal_index.get(identity.principal)
        if existing_id is not None and existing_id != identity.id:
            return _refuse(
                f"principal {identity.principal!r} already resolves to "
                f"{existing_id!r}; refusing to re-point it at {identity.id!r}"
            )

        self._identities[identity.id] = identity
        self._principal_index[identity.principal] = identity.id
        return True

    def describe_identity_namespaces(self) -> Dict[str, Any]:
        """Report the id-namespace split and any registry refusal at boot.

        Exists so the containment is *observable*: a refused registration is a
        security event, and "the kernel loaded 1 of the 2 rows the store
        offered" must be answerable without reading a log file.
        """
        with self._lock:
            by_namespace: Dict[str, int] = {}
            for identity in self._identities.values():
                by_namespace[identity.namespace] = (
                    by_namespace.get(identity.namespace, 0) + 1
                )
            return {
                "human_id_prefix": HUMAN_ID_PREFIX,
                "identities_by_namespace": by_namespace,
                "registry_refusals": list(self._registry_refusals),
                "store": self.describe_store(),
                "registry_integrity": _persistence.describe_registry_integrity(
                    self._store
                ),
            }

    def _seed_human_identities(self) -> int:
        """Load registered humans from the configured store.

        Returns how many were loaded. **An unconfigured store means zero
        humans** -- that is deliberately fail-closed rather than a silent
        fallback to a machine identity (Policy C-7).

        A malformed or unreadable store is logged loudly and loads nothing.
        Crashing on it would take down every kernel consumer for a
        configuration problem; silently ignoring it would leave operators
        believing a human was registered when none was.

        Which store is in use -- the JSON seed file or SQLite
        (:mod:`._persistence`) -- is an operational choice this method does not
        need to know about.
        """
        store = self._store
        # ``include_extended=True`` asks for the containment columns
        # (``trust_score`` and the integrity verdict) that ``load_all()``
        # deliberately keeps OUT of its default shape -- the default shape is
        # pinned by tests and read by operator tooling, so the new data travels
        # on a side channel rather than mutating a public dict.
        entries = store.load_all(include_extended=True)
        if not entries:
            return 0

        loaded = 0
        for entry in entries:
            principal_as_supplied = str(entry.get(_persistence.FIELD_PRINCIPAL) or "").strip()
            principal = normalize_principal(principal_as_supplied).strip()
            if not principal:
                logger.error(
                    "skipping entry without a principal in %r (%s)",
                    store.location, store.backend_name,
                )
                continue
            if is_reserved_principal(principal_as_supplied):
                # Degenerate after normalization: a row whose principal was
                # nothing but the reserved prefix. Nothing to register.
                with self._lock:
                    self._audit(
                        identity_id=principal_as_supplied,
                        operation="create",
                        scope=IdentityScope.L0,
                        result="denied",
                        reason=(
                            f"registry principal {principal_as_supplied!r} has no "
                            f"name after stripping the reserved prefix {HUMAN_ID_PREFIX!r}"
                        ),
                    )
                    self._registry_refusals.append(
                        f"empty principal after prefix strip in registry row "
                        f"{principal_as_supplied!r}"
                    )
                    self._record_denial_on_the_chain(
                        principal=principal_as_supplied,
                        reason="registry principal has no name once its reserved prefix is stripped",
                    )
                logger.error(
                    "refusing human registration from %r: principal %r has no name "
                    "after stripping the reserved prefix %r",
                    store.location, principal_as_supplied, HUMAN_ID_PREFIX,
                )
                continue
            with self._lock:
                # Deduplicate against BOTH key spaces. Checking only
                # ``_principal_index`` is the bug this closes.
                identity_id = f"{HUMAN_ID_PREFIX}{principal}"
                if principal in self._principal_index or identity_id in self._identities:
                    self._audit(
                        identity_id=identity_id,
                        operation="create",
                        scope=IdentityScope.L0,
                        result="denied",
                        reason=(
                            f"human identity {principal!r} already registered "
                            f"(principal index or id slot occupied) -- refusing "
                            f"to overwrite the incumbent"
                        ),
                    )
                    self._registry_refusals.append(
                        f"duplicate human registration {principal!r}"
                    )
                    self._record_denial_on_the_chain(
                        principal=principal,
                        reason="human identity already registered",
                    )
                    logger.error(
                        "human identity %r already registered -- refusing to "
                        "overwrite the existing identity", principal,
                    )
                    continue
                # Built directly instead of via create_identity(): that method
                # is a @kernel_action, whose policy verdict calls back into
                # IdentityManager. Seeding happens *inside* __init__, before
                # the global singleton is published, so the callback would
                # construct another manager and recurse without bound. The
                # built-in system/service identities are built the same way.
                metadata: Dict[str, Any] = {
                    METADATA_KIND_KEY: HUMAN_KIND,
                    "seeded_from": store.location,
                    # Explicit provenance (A1): which store, which backend, and
                    # the integrity verdict that admitted this row.
                    "provenance": {
                        "record_source": str(
                            entry.get(_persistence.FIELD_SOURCE) or "registry"
                        ),
                        "store_backend": store.backend_name,
                        "store_location": store.location,
                        "integrity": entry.get(
                            _persistence.FIELD_INTEGRITY_STATUS, "unverified"
                        ),
                    },
                    "id_namespace": NAMESPACE_HUMAN,
                }
                display_name = entry.get(_persistence.FIELD_DISPLAY_NAME)
                if display_name:
                    metadata[METADATA_DISPLAY_NAME_KEY] = display_name
                if principal_as_supplied != principal:
                    metadata["principal_as_supplied"] = principal_as_supplied
                    metadata["principal_normalized"] = True
                registered_at = entry.get(_persistence.FIELD_REGISTERED_AT)
                if isinstance(registered_at, str) and registered_at.strip():
                    metadata["registered_at"] = registered_at
                else:
                    metadata["registered_at"] = utc_now().isoformat()
                try:
                    scope = IdentityScope(
                        entry.get(_persistence.FIELD_SCOPE) or IdentityScope.L0.value
                    )
                except ValueError:
                    scope = IdentityScope.L0
                # Trust comes from the row and is clamped -- never inherited
                # from whatever occupied the slot before (A1: trust-inheritance
                # prohibition).
                try:
                    trust_score = float(
                        entry.get(
                            _persistence.FIELD_TRUST_SCORE, SEEDED_HUMAN_TRUST_SCORE
                        )
                    )
                except (TypeError, ValueError):
                    trust_score = SEEDED_HUMAN_TRUST_SCORE
                trust_score = max(0.0, min(1.0, trust_score))
                identity = AgentIdentity(
                    id=identity_id,
                    principal=principal,
                    permissions=set(entry.get(_persistence.FIELD_PERMISSIONS) or []),
                    scope=scope,
                    trust_score=trust_score,
                    metadata=metadata,
                )
                if not self._claim_slot(identity, origin=f"seed:{store.backend_name}"):
                    continue
                self._audit(
                    identity_id=identity.id,
                    operation="create",
                    permission=None,
                    scope=identity.scope,
                    result="allowed",
                    reason=(
                        f"Human identity seeded for principal: {principal} "
                        f"(namespace={NAMESPACE_HUMAN}, trust={trust_score})"
                    ),
                )
            loaded += 1
        if loaded:
            logger.info(
                "loaded %d registered human identities from %s (%s)",
                loaded, store.location, store.backend_name,
            )
        return loaded

    def _persist_human_identity(self, identity: AgentIdentity) -> bool:
        """Write ``identity`` through to the store so it survives a restart.

        ``IdentityManager`` is memory-only. Registering a human without
        persisting it produces the worst possible failure mode for an approval
        channel: it *looks* wired for the lifetime of the process, and after
        the next restart nobody can approve a HIGH/CRITICAL action -- a silent
        outage that only appears when sovereignty is finally needed.

        Returns True when the store accepted the write. A ``False`` here means
        the registration is in-memory only, which is the pre-existing behaviour
        for an unconfigured store rather than an error.
        """
        metadata = identity.metadata if isinstance(identity.metadata, dict) else {}
        return self._store.upsert(
            {
                _persistence.FIELD_PRINCIPAL: identity.principal,
                _persistence.FIELD_DISPLAY_NAME: metadata.get(
                    METADATA_DISPLAY_NAME_KEY
                ),
                _persistence.FIELD_PERMISSIONS: sorted(identity.permissions),
                _persistence.FIELD_SCOPE: identity.scope.value,
                _persistence.FIELD_REGISTERED_AT: (
                    metadata.get("registered_at") or identity.created_at.isoformat()
                ),
                _persistence.FIELD_SOURCE: "runtime",
            }
        )

    def describe_store(self) -> Dict[str, Any]:
        """Report the identity store in use -- for boot logs and tooling."""
        return _persistence.describe_human_identity_store(self._store)

    def _get_identity(self, identity_id: str) -> Optional[AgentIdentity]:
        """Get identity by ID."""
        with self._lock:
            return self._identities.get(identity_id)

    @kernel_action("identity.create_identity")
    def create_identity(
        self,
        principal: str,
        permissions: Optional[Set[str]] = None,
        scope: IdentityScope = IdentityScope.L1,
        trust_score: float = 0.5,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> AgentIdentity:
        """Create a new identity.

        The id is allocated inside the namespace implied by ``metadata``:
        ``human:<principal>`` for a human (see :meth:`create_human_identity`),
        an 8-hex agent id otherwise. The reserved-prefix rule and the namespace
        invariant are both enforced on the write path, so no caller can place an
        identity in the wrong namespace or land on an occupied slot.
        """
        with self._lock:
            # Principal must be a non-empty string. The contract is
            # ``principal: str``; anything else -- ``None`` left behind by a
            # missing config value, ``0``/``False`` from a type confusion, an
            # empty or whitespace-only string -- is a caller bug that must NOT be
            # silently coerced into a phantom principal. The prior
            # normalisation turned ``None`` into the literal "None" and minted a
            # nameable subject ("human:None") that is_human_identity() would then
            # accept, so the minting primitive in this kernel could be reached
            # with no principal at all.
            if not isinstance(principal, str) or not principal.strip():
                self._audit(
                    identity_id=str(principal),
                    operation="create",
                    permission=None,
                    scope=scope,
                    result="denied",
                    reason=(
                        f"principal must be a non-empty string, got "
                        f"{type(principal).__name__}"
                    ),
                )
                logger.error(
                    "refusing identity creation: principal %r is not a "
                    "non-empty string",
                    principal,
                )
                mark_action_denied("principal is not a non-empty string")
                return None
            principal_as_supplied = principal
            principal = normalize_principal(principal).strip()
            if is_reserved_principal(principal_as_supplied):
                # Degenerate after normalization ("" or a bare "human:"): there
                # is no name left to identify the subject by, so the write is
                # refused rather than allowed to mint a prefix-only id.
                self._audit(
                    identity_id=principal_as_supplied,
                    operation="create",
                    permission=None,
                    scope=scope,
                    result="denied",
                    reason=(
                        f"principal {principal_as_supplied!r} is empty after "
                        f"stripping the reserved id prefix {HUMAN_ID_PREFIX!r}"
                    ),
                )
                logger.error(
                    "refusing identity creation: principal %r has no name after "
                    "stripping the reserved prefix %r",
                    principal_as_supplied, HUMAN_ID_PREFIX,
                )
                mark_action_denied("principal has no name once its reserved prefix is stripped")
                return None

            # Principal uniqueness: a principal identifies exactly one identity.
            if principal in self._principal_index:
                self._audit(
                    identity_id=self._principal_index[principal],
                    operation="create",
                    permission=None,
                    scope=scope,
                    result="denied",
                    reason=f"Principal {principal!r} already has an identity",
                )
                mark_action_denied("principal already has an identity")
                return None

            # Clamp trust score
            trust_score = max(0.0, min(1.0, trust_score))

            resolved_metadata = dict(metadata or {})
            is_human = resolved_metadata.get(METADATA_KIND_KEY) == HUMAN_KIND
            if principal_as_supplied != principal:
                # Provenance: the id is derived from the normalized name, so
                # record the spelling that actually arrived rather than letting
                # the two forms become indistinguishable in the record.
                resolved_metadata["principal_as_supplied"] = principal_as_supplied
                resolved_metadata["principal_normalized"] = True
            identity = AgentIdentity(
                id=(
                    f"{HUMAN_ID_PREFIX}{principal}"
                    if is_human
                    else self._allocate_agent_id()
                ),
                principal=principal,
                permissions=permissions or set(),
                scope=scope,
                trust_score=trust_score,
                metadata=resolved_metadata,
            )

            if not self._claim_slot(identity, origin="create_identity"):
                return None

            # Record audit event
            self._audit(
                identity_id=identity.id,
                operation="create",
                permission=None,
                scope=scope,
                result="allowed",
                reason=(
                    f"Identity created for principal: {principal} "
                    f"(namespace={identity.namespace})"
                ),
            )

            return identity

    def create_human_identity(
        self,
        principal: str,
        permissions: Optional[Set[str]] = None,
        trust_score: float = 1.0,
        display_name: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Optional[AgentIdentity]:
        """Register ``principal`` as a human who may hold sovereignty.

        The sanctioned way to create a human identity: it stamps
        ``metadata["kind"] == "human"`` -- the only marker
        :func:`is_human_identity` accepts -- so callers cannot forget it.
        Humans are ``L0`` (the scope the codebase already labels "Human only").

        Registration is written through to the configured store, so it survives
        a process restart. Before that, a human registered at runtime existed
        only in memory and the approval channel was empty again after the next
        boot.

        Returns the identity, or ``None`` if the principal already exists
        (matching :meth:`create_identity`).
        """
        merged: Dict[str, Any] = dict(metadata or {})
        merged[METADATA_KIND_KEY] = HUMAN_KIND
        if display_name:
            merged[METADATA_DISPLAY_NAME_KEY] = display_name
        merged.setdefault("registered_at", utc_now().isoformat())
        identity = self.create_identity(
            principal=principal,
            permissions=permissions,
            scope=IdentityScope.L0,
            trust_score=trust_score,
            metadata=merged,
        )
        if identity is not None:
            self._persist_human_identity(identity)
        return identity

    def get_identity(self, identity_id: str) -> Optional[AgentIdentity]:
        """Get identity by ID."""
        with self._lock:
            return self._get_identity(identity_id)

    def get_identity_by_principal(self, principal: str) -> Optional[AgentIdentity]:
        """Get identity by its principal identifier (P10 verification)."""
        with self._lock:
            identity_id = self._principal_index.get(principal)
            if identity_id is None:
                return None
            return self._identities.get(identity_id)

    # ------------------------------------------------------------------ #
    # 账号树（单机单根）
    # ------------------------------------------------------------------ #
    #
    # 与既有 RBAC/ABAC 的关系：这里**不另起一套权限系统**。identity 仍然回答
    # "你是谁 / 你的 scope 和 permission 是什么"；账号树只回答"你能作用于谁"。
    # 两者在 gateway 组合成最终判定（见 ``gateway/account_context.py``）。
    #
    # 所有写操作都遵循同一套顺序：定位 -> 树判定 -> 防提权 -> 持久化 -> 审计。
    # 任何一步拒绝都抛 :class:`AccountError`（稳定 code + 中性文案），绝不返回
    # 一个"看起来成功"的空结果。

    def _human_identities(self) -> List[AgentIdentity]:
        with self._lock:
            return [
                identity
                for identity in self._identities.values()
                if identity.namespace == NAMESPACE_HUMAN
            ]

    def _account_of(self, principal: str) -> Optional[_accounts.AccountNode]:
        with self._lock:
            return self._accounts.get(str(principal or "").strip())

    def get_account(self, principal: str) -> Optional[_accounts.AccountNode]:
        """公开读接口：未知主体返回 ``None``（调用方负责中性化）。"""
        return self._account_of(principal)

    def account_nodes(self) -> Dict[str, _accounts.AccountNode]:
        with self._lock:
            return dict(self._accounts)

    def account_root(self) -> Optional[_accounts.AccountNode]:
        with self._lock:
            roots = _accounts.roots(self._accounts)
        if not roots:
            return None
        return sorted(roots, key=lambda node: (node.created_at, node.principal))[0]

    def list_accounts(self) -> List[_accounts.AccountNode]:
        with self._lock:
            return sorted(self._accounts.values(), key=lambda node: node.principal)

    def subtree_principals(self, principal: str) -> List[str]:
        with self._lock:
            return _accounts.subtree_of(self._accounts, principal)

    def account_denial_reason(self, actor: str, target: str) -> Optional[str]:
        """返回写操作被拒的稳定 code；允许时返回 ``None``。"""
        with self._lock:
            if self._accounts.get(actor) is None:
                return _accounts.CODE_SUBTREE_DENIED
            if self._accounts.get(target) is None:
                return _accounts.CODE_SUBTREE_DENIED
            if not _accounts.is_within_subtree(self._accounts, actor, target):
                return _accounts.CODE_UPWARD_DENIED
            if not _accounts.can_manage(self._accounts, actor, target):
                return _accounts.CODE_LEAPFROG_DENIED
        return None

    def _persist_account(self, node: _accounts.AccountNode) -> bool:
        try:
            return bool(self._store.upsert_account(node.to_record()))
        except Exception as exc:  # noqa: BLE001 - 存储问题不能吞掉安全判定
            logger.error("could not persist account %r: %s", node.principal, exc)
            return False

    def _store_account(self, node: _accounts.AccountNode, *, persist: bool = True) -> None:
        with self._lock:
            self._accounts[node.principal] = node
        if persist:
            self._persist_account(node)

    def _account_audit(
        self,
        *,
        action: str,
        actor: str,
        target: str,
        outcome: str,
        details: Optional[Dict[str, Any]] = None,
    ) -> None:
        """账号树事件写入**哈希链审计**（不是内存日志）。

        ``log_event`` 失败时必须显式报错到日志，但不能反向让业务操作失败：
        审计不可用是运维问题，安全判定已在调用前完成。
        """
        payload: Dict[str, Any] = {
            "action": action,
            "actor": actor,
            "target": target,
        }
        if details:
            payload.update(details)
        try:
            from src.kernels.audit import AuditEventType, AuditScope, log_event

            event_type = (
                AuditEventType.ACCESS_DENIED
                if outcome == "denied"
                else AuditEventType.STATE_CHANGE
            )
            if action.startswith("account.permission"):
                event_type = (
                    AuditEventType.ROLE_REVOKE
                    if outcome == "revoked"
                    else AuditEventType.ROLE_GRANT
                )
            elif outcome == "allowed" and action.startswith("key."):
                event_type = AuditEventType.ACCESS_ALLOWED
            scope_name = "L0"
            identity = self.get_identity_by_principal(actor)
            if identity is not None:
                scope_name = identity.scope.value
            log_event(
                event_type=event_type,
                principal_id=actor,
                scope=AuditScope(scope_name),
                outcome=outcome,
                details=payload,
            )
        except Exception:  # noqa: BLE001
            logger.exception("account audit event lost (action=%s)", action)

    def _audit_denial(
        self, *, action: str, actor: str, target: str, code: str, **extra: Any
    ) -> None:
        self._account_audit(
            action=action,
            actor=actor,
            target=target,
            outcome="denied",
            details={"error_code": code, **extra},
        )

    def record_event(
        self,
        *,
        action: str,
        actor: str,
        target: str,
        outcome: str = "allowed",
        details: Optional[Dict[str, Any]] = None,
    ) -> None:
        """网关层账号/Key 事件的公共入口（写入同一哈希链，不另起审计流）。"""
        self._account_audit(
            action=action,
            actor=actor,
            target=target,
            outcome=outcome,
            details=details,
        )

    # ---- 启动 / 迁移 ---------------------------------------------------

    def _bootstrap_account_tree(self) -> int:
        """从身份库派生账号树；旧库自动迁移且幂等。

        规则：
        * 每个人类身份至多一个账号节点；
        * 树上只允许一个根；历史多人类库选"最早注册者"为根，其余改为它的
          直接成员（身份与权限原样保留，不丢失登录能力）；
        * 父不存在 / 成环的行被**拒收**（fail-closed），并记录到
          ``registry_refusals``；
        * 已存在的合法节点不会因为重启被改写（幂等）。
        """
        with self._lock:
            humans = {identity.principal for identity in self._human_identities()}
            loaded: Dict[str, _accounts.AccountNode] = {}
            for record in self._store.load_accounts():
                try:
                    node = _accounts.AccountNode.from_record(record)
                except Exception:  # noqa: BLE001
                    continue
                if node.principal not in humans:
                    self._registry_refusals.append(
                        f"account node {node.principal!r} has no human identity"
                    )
                    continue
                loaded[node.principal] = node

            nodes = dict(loaded)
            # 父缺失的行（含它的后代）无法证明位置，拒收。
            changed = True
            while changed:
                changed = False
                for principal, node in list(nodes.items()):
                    if node.parent_id and node.parent_id not in nodes:
                        nodes.pop(principal, None)
                        self._registry_refusals.append(
                            f"account node {principal!r} refers to a missing parent"
                        )
                        changed = True
            cycles = set(_accounts.detect_cycles(nodes))
            if cycles:
                for principal in cycles:
                    for doomed in _accounts.subtree_of(nodes, principal):
                        nodes.pop(doomed, None)
                self._registry_refusals.append(
                    "account tree contained a cycle; affected nodes were refused"
                )

            root_nodes = _accounts.roots(nodes)
            elected: Optional[_accounts.AccountNode] = None
            preferred_root = (
                os.environ.get("LIUHAO_ACCOUNT_ROOT_PRINCIPAL") or ""
            ).strip()
            if preferred_root:
                candidate = nodes.get(preferred_root)
                if candidate is not None and candidate.is_root:
                    elected = candidate
            if elected is None and root_nodes:
                elected = sorted(
                    root_nodes, key=lambda node: (node.created_at, node.principal)
                )[0]
            for node in root_nodes:
                if elected and node.principal != elected.principal:
                    # 历史多根：收敛为单根。深度/根随新父重建。
                    nodes[node.principal] = node.with_changes(
                        parent_id=elected.principal, depth=1
                    )
                    self._registry_refusals.append(
                        f"historical root {node.principal!r} attached under "
                        f"{elected.principal!r} (single-tree migration)"
                    )

            # 未入树的人类身份：首个成为根，其余挂在根下。
            for principal in sorted(humans):
                if principal in nodes:
                    continue
                if elected is None:
                    node = _accounts.AccountNode(
                        principal=principal,
                        parent_id=None,
                        depth=0,
                        root_id=principal,
                        permissions=frozenset(
                            self._identity_permissions(principal)
                        ),
                        created_at=self._identity_registered_at(principal),
                        updated_at=utc_now().isoformat(),
                        source="migration",
                    )
                    elected = node
                else:
                    node = _accounts.AccountNode(
                        principal=principal,
                        parent_id=elected.principal,
                        depth=1,
                        root_id=elected.principal,
                        permissions=frozenset(
                            self._identity_permissions(principal)
                        ),
                        created_at=self._identity_registered_at(principal),
                        updated_at=utc_now().isoformat(),
                        source="migration",
                    )
                nodes[principal] = node
                self._persist_account(node)

            # 重算根/深度（只对发生了收敛的树生效；正常树不变）。
            if elected is not None:
                self._recompute_tree(nodes, elected.principal)

            self._accounts = nodes
            self._accounts_booted = True
            return len(nodes)

    def _identity_permissions(self, principal: str) -> Set[str]:
        identity = self.get_identity_by_principal(principal)
        return set(identity.permissions) if identity is not None else set()

    def _identity_registered_at(self, principal: str) -> str:
        identity = self.get_identity_by_principal(principal)
        if identity is None:
            return utc_now().isoformat()
        metadata = identity.metadata or {}
        stamp = metadata.get("registered_at")
        if isinstance(stamp, str) and stamp.strip():
            return stamp
        return identity.created_at.isoformat()

    def _recompute_tree(
        self, nodes: Dict[str, _accounts.AccountNode], root_principal: str
    ) -> None:
        """按 parent 链重算 root_id / depth，并写回发生变化的节点。"""
        changed: List[_accounts.AccountNode] = []
        for node in list(nodes.values()):
            depth = 0
            cursor: Optional[_accounts.AccountNode] = node
            guard = 0
            while cursor is not None and cursor.parent_id and guard <= len(nodes):
                cursor = nodes.get(cursor.parent_id)
                depth += 1
                guard += 1
            if node.depth != depth or node.root_id != root_principal:
                updated = node.with_changes(depth=depth, root_id=root_principal)
                nodes[node.principal] = updated
                changed.append(updated)
        for node in changed:
            self._persist_account(node)

    def migrate_account_tree(self) -> Dict[str, Any]:
        """显式执行迁移（幂等），返回可展示的迁移报告。"""
        before = len(self.account_nodes())
        count = self._bootstrap_account_tree()
        nodes = self.account_nodes()
        return {
            "admitted": count,
            "before": before,
            "after": count,
            "roots": sorted(node.principal for node in nodes.values() if node.is_root),
            "backend": self._store.backend_name,
            "location": self._store.location,
            "next_step": "rollback: identity_manager.rollback_account_tree() "
            "(identities are never touched)",
        }

    def rollback_account_tree(self) -> Dict[str, Any]:
        """回滚账号树。

        只删除 ``account_tree`` / 文档里的 ``accounts`` 键；**身份行一个不动**，
        因此老账号仍能按原方式登录（会被下次 bootstrap 重新派生成根）。
        """
        store_ok = False
        try:
            store_ok = bool(self._store.drop_accounts())
        except Exception as exc:  # noqa: BLE001
            logger.error("could not drop account tree: %s", exc)
        with self._lock:
            self._accounts = {}
            self._accounts_booted = False
        return {
            "rolled_back": True,
            "store_purged": store_ok,
            "identities_preserved": len(self._human_identities()),
        }

    # ---- 根 / 成员创建 -------------------------------------------------

    def register_root_account(
        self,
        principal: str,
        permissions: Optional[Set[str]] = None,
        display_name: Optional[str] = None,
    ) -> Tuple[_accounts.AccountNode, str]:
        """创建**唯一**根账号。已存在根时返回 403（并写审计）。

        首个注册者自动成为主账号；此后自助注册根一律关闭。
        """
        principal = normalize_principal(principal).strip()
        if not principal:
            raise _accounts.AccountError(400, "ACCOUNT_PRINCIPAL_INVALID", "账号名无效")
        with self._lock:
            existing_root = self.account_root()
            if existing_root is not None:
                self._audit_denial(
                    action="account.root.register",
                    actor=principal,
                    target=existing_root.principal,
                    code=_accounts.CODE_ROOT_REGISTRATION_CLOSED,
                )
                raise _accounts.AccountError(
                    409,
                    _accounts.CODE_ROOT_REGISTRATION_CLOSED,
                    "系统已完成初始化，请使用已有账号登录",
                )
            identity = self.get_identity_by_principal(principal)
            if identity is not None and is_human_identity(identity):
                raise _accounts.AccountError(
                    409, "ACCOUNT_EXISTS", "该账号已存在"
                )
            if identity is not None:
                raise _accounts.AccountError(
                    409, "ACCOUNT_EXISTS", "该账号名已被占用"
                )
            created = self.create_human_identity(
                principal=principal,
                permissions=set(permissions or set()),
                display_name=display_name,
            )
            if created is None:
                raise _accounts.AccountError(409, "ACCOUNT_EXISTS", "该账号已存在")
            recovery_code = _accounts.generate_code("RC")
            node = _accounts.AccountNode(
                principal=principal,
                parent_id=None,
                depth=0,
                root_id=principal,
                permissions=frozenset(created.permissions),
                allow_superior_key_use=True,
                key_auth_source="root",
                # 恢复码不带本机完整性密钥：它必须能随备份包搬到另一台电脑上
                # 继续可用。码本身约 100 bit 高熵，无密钥哈希也足以抵抗枚举。
                recovery_hash=_accounts.hash_code(recovery_code),
                created_at=self._identity_registered_at(principal),
                updated_at=utc_now().isoformat(),
                source="bootstrap",
            )
            self._store_account(node)
            self._account_audit(
                action="account.root.registered",
                actor=principal,
                target=principal,
                outcome="allowed",
                details={"has_recovery_code": True},
            )
            return node, recovery_code

    def create_member_identity(
        self,
        principal: str,
        permissions: Set[str],
        display_name: Optional[str] = None,
    ) -> Optional[AgentIdentity]:
        """创建成员身份：仍是 human（可登录），但 scope=L3，不继承主权。"""
        metadata: Dict[str, Any] = {
            METADATA_KIND_KEY: HUMAN_KIND,
            "registered_at": utc_now().isoformat(),
            "account_role": "member",
        }
        if display_name:
            metadata[METADATA_DISPLAY_NAME_KEY] = display_name
        identity = self.create_identity(
            principal=principal,
            permissions=set(permissions),
            scope=IdentityScope.L3,
            trust_score=0.5,
            metadata=metadata,
        )
        if identity is not None:
            self._persist_human_identity(identity)
        return identity

    def create_member_account(
        self,
        actor: str,
        principal: str,
        permissions: Optional[Iterable[str]] = None,
        role: Optional[str] = None,
        display_name: Optional[str] = None,
        allow_key_sharing: bool = False,
    ) -> Tuple[_accounts.AccountNode, str]:
        """创建 ``actor`` 的直接下级（成员），返回一次性激活码。

        * 成员只能建自己的孩子；根账号可以建任意深度（仍受层级上限约束）。
        * 新建者不能授出自己没有的 scope（防提权）。
        * 激活码只返回一次，库里只存哈希。
        """
        principal = normalize_principal(principal).strip()
        if not principal:
            raise _accounts.AccountError(400, "ACCOUNT_PRINCIPAL_INVALID", "账号名无效")
        with self._lock:
            actor_node = self._accounts.get(actor)
            if actor_node is None:
                self._audit_denial(
                    action="account.member.create", actor=actor, target=principal,
                    code="ACCOUNT_SCOPE_DENIED",
                )
                raise _accounts.neutral_denied()
            if not actor_node.is_active:
                raise _accounts.AccountError(
                    403, "ACCOUNT_UNAVAILABLE", "该账号当前不可用"
                )
            if not self._identity_has_scope(actor, _accounts.SCOPE_MANAGE_CHILDREN):
                self._audit_denial(
                    action="account.member.create", actor=actor, target=principal,
                    code="ACCOUNT_SCOPE_DENIED",
                )
                raise _accounts.neutral_denied()
            existing = self.get_identity_by_principal(principal)
            if existing is not None:
                raise _accounts.AccountError(409, "ACCOUNT_EXISTS", "该账号已存在")
            try:
                wanted = _accounts.resolve_role(role, permissions)
            except _accounts.AccountError as exc:
                self._audit_denial(
                    action="account.member.create", actor=actor, target=principal,
                    code=exc.code,
                )
                raise
            decision, code = _accounts.grant_decision(
                self._accounts, actor, principal, wanted
            )
            if decision != "allow":
                self._audit_denial(
                    action="account.member.create", actor=actor, target=principal,
                    code=code or _accounts.CODE_PRIVILEGE_ESCALATION,
                )
                raise _accounts.AccountError(
                    403, _accounts.CODE_PRIVILEGE_ESCALATION, "无权授予该权限"
                )
            depth = actor_node.depth + 1
            if depth > _accounts.max_depth():
                self._audit_denial(
                    action="account.member.create", actor=actor, target=principal,
                    code=_accounts.CODE_DEPTH_EXCEEDED,
                )
                raise _accounts.depth_exceeded()
            identity = self.create_member_identity(
                principal=principal,
                permissions=set(wanted),
                display_name=display_name,
            )
            if identity is None:
                raise _accounts.AccountError(409, "ACCOUNT_EXISTS", "该账号已存在")
            activation_code = _accounts.generate_code("AC")
            node = _accounts.AccountNode(
                principal=principal,
                parent_id=actor_node.principal,
                depth=depth,
                root_id=actor_node.root_id,
                permissions=frozenset(wanted),
                allow_superior_key_use=bool(allow_key_sharing),
                key_auth_source=(
                    "created_default" if allow_key_sharing else "not_granted"
                ),
                activation_hash=_accounts.hash_code(
                    activation_code, (_persistence.integrity_key() or "")
                ),
                activation_expires_at=_accounts.expires_at(),
                created_at=self._identity_registered_at(principal),
                updated_at=utc_now().isoformat(),
                source="runtime",
            )
            self._store_account(node)
            self._account_audit(
                action="account.member.created",
                actor=actor,
                target=principal,
                outcome="allowed",
                details={
                    "permissions": sorted(wanted),
                    "activation_expires_at": node.activation_expires_at,
                },
            )
            return node, activation_code

    def register_self_service_account(
        self,
        principal: str,
        display_name: Optional[str] = None,
    ) -> _accounts.AccountNode:
        """自助注册：无邀请码、无审批，直接挂在**本机根账号的第 1 层**。

        硬边界（不随请求体变化）：

        * 上级恒为根账号，``depth`` 恒为 1；
        * 作用域恒为 :data:`_accounts.SELF_SERVICE_SCOPES`（最小权限，不能建下级/授权/借 Key）；
        * 不接受任何父子/深度/权限参数，调用方即使传入也一律忽略。

        没有根账号时拒绝（先走 ``register_root_account``）；重名返回 409。
        """
        principal = normalize_principal(principal).strip()
        if not principal:
            raise _accounts.AccountError(400, "ACCOUNT_PRINCIPAL_INVALID", "账号名无效")
        with self._lock:
            root = self.account_root()
            if root is None:
                raise _accounts.AccountError(
                    409,
                    _accounts.CODE_SELF_REGISTER_UNAVAILABLE,
                    "系统尚未初始化，请先完成首次设置",
                )
            if not root.is_active:
                raise _accounts.AccountError(
                    403, "ACCOUNT_UNAVAILABLE", "该账号当前不可用"
                )
            if self.get_identity_by_principal(principal) is not None:
                raise _accounts.AccountError(409, "ACCOUNT_EXISTS", "该账号已存在")
            wanted = frozenset(_accounts.SELF_SERVICE_SCOPES)
            identity = self.create_member_identity(
                principal=principal,
                permissions=set(wanted),
                display_name=display_name,
            )
            if identity is None:
                raise _accounts.AccountError(409, "ACCOUNT_EXISTS", "该账号已存在")
            node = _accounts.AccountNode(
                principal=principal,
                parent_id=root.principal,
                depth=1,
                root_id=root.principal,
                permissions=wanted,
                allow_superior_key_use=False,
                key_auth_source="not_granted",
                activation_hash="",
                activation_expires_at=None,
                created_at=self._identity_registered_at(principal),
                updated_at=utc_now().isoformat(),
                source="self_service",
            )
            self._store_account(node)
            self._account_audit(
                action="account.self_registered",
                actor=principal,
                target=principal,
                outcome="allowed",
                details={"permissions": sorted(wanted), "source": "self_service"},
            )
            return node

    def _identity_has_scope(self, principal: str, scope: str) -> bool:
        node = self._accounts.get(principal)
        if node is None:
            return False
        return node.has(scope)

    # ---- 激活 / 一次性码 -----------------------------------------------

    def verify_activation_code(self, principal: str, code: str) -> _accounts.AccountNode:
        """校验激活码但不消费；失败计数落库（限次）。"""
        key = f"activate:{principal}"
        _accounts.limiter.check(
            key, limit=_accounts.MAX_ACTIVATION_ATTEMPTS, window=3600
        )
        with self._lock:
            node = self._accounts.get(principal)
            if (
                node is None
                or not node.activation_hash
                or node.status == _accounts.ACCOUNT_STATUS_DELETED
            ):
                _accounts.limiter.register(key)
                raise _accounts.AccountError(
                    401, "ACTIVATION_CODE_INVALID", "激活信息无效或已过期"
                )
            if _accounts.is_expired(node.activation_expires_at):
                _accounts.limiter.register(key)
                raise _accounts.AccountError(
                    401, "ACTIVATION_CODE_INVALID", "激活信息无效或已过期"
                )
            if node.activation_attempts >= _accounts.MAX_ACTIVATION_ATTEMPTS:
                raise _accounts.AccountError(
                    429, "ACCOUNT_RATE_LIMITED", "尝试次数过多，请稍后重试"
                )
            if not _accounts.code_matches(
                code, node.activation_hash, (_persistence.integrity_key() or "")
            ):
                updated = node.with_changes(
                    activation_attempts=node.activation_attempts + 1
                )
                self._accounts[principal] = updated
                self._persist_account(updated)
                _accounts.limiter.register(key)
                self._audit_denial(
                    action="account.activate", actor=principal, target=principal,
                    code="ACTIVATION_CODE_INVALID",
                )
                raise _accounts.AccountError(
                    401, "ACTIVATION_CODE_INVALID", "激活信息无效或已过期"
                )
            return node

    def consume_activation_code(self, principal: str) -> _accounts.AccountNode:
        """消费已校验的激活码（一次性）。"""
        with self._lock:
            node = self._accounts.get(principal)
            if node is None:
                raise _accounts.AccountError(
                    401, "ACTIVATION_CODE_INVALID", "激活信息无效或已过期"
                )
            updated = node.with_changes(
                activation_hash="",
                activation_expires_at=None,
                activation_attempts=0,
                status=_accounts.ACCOUNT_STATUS_ACTIVE,
            ).bump()
            self._accounts[principal] = updated
            self._persist_account(updated)
            _accounts.limiter.reset(f"activate:{principal}")
            self._account_audit(
                action="account.activated",
                actor=principal,
                target=principal,
                outcome="allowed",
            )
            return updated

    def issue_activation_code(
        self, actor: str, target: str
    ) -> Tuple[str, Optional[str]]:
        """重新签发一次性激活码（只能作用于自己可管理的成员）。"""
        code = _accounts.generate_code("AC")
        stamp = _accounts.expires_at()
        with self._lock:
            self._require_can_manage(
                actor=actor, target=target, action="account.activation.issue"
            )
            node = self._accounts[target]
            updated = node.with_changes(
                activation_hash=_accounts.hash_code(
                    code, (_persistence.integrity_key() or "")
                ),
                activation_expires_at=stamp,
                activation_attempts=0,
            )
            self._accounts[target] = updated
            self._persist_account(updated)
            _accounts.limiter.reset(f"activate:{target}")
            self._account_audit(
                action="account.activation.issued",
                actor=actor,
                target=target,
                outcome="allowed",
                details={"activation_expires_at": stamp},
            )
            return code, stamp

    def _require_can_manage(self, *, actor: str, target: str, action: str) -> _accounts.AccountNode:
        if actor == target:
            raise _accounts.neutral_denied()
        with self._lock:
            actor_node = self._accounts.get(actor)
            target_node = self._accounts.get(target)
            if actor_node is None or target_node is None:
                self._audit_denial(
                    action=action, actor=actor, target=target,
                    code=_accounts.CODE_SUBTREE_DENIED,
                )
                raise _accounts.subtree_denied()
            if actor_node.root_id != target_node.root_id:
                self._audit_denial(
                    action=action, actor=actor, target=target,
                    code=_accounts.CODE_CROSS_SUBTREE_DENIED,
                )
                raise _accounts.subtree_denied(_accounts.CODE_CROSS_SUBTREE_DENIED)
            if target_node.status == _accounts.ACCOUNT_STATUS_DELETED:
                self._audit_denial(
                    action=action, actor=actor, target=target,
                    code=_accounts.CODE_SUBTREE_DENIED,
                )
                raise _accounts.subtree_denied()
            if not _accounts.can_manage(self._accounts, actor, target):
                self._audit_denial(
                    action=action, actor=actor, target=target,
                    code=_accounts.CODE_LEAPFROG_DENIED,
                )
                raise _accounts.neutral_denied()
            return target_node

    # ---- 权限 / Key 授权 -------------------------------------------------

    def grant_account_permissions(
        self,
        actor: str,
        target: str,
        grant: Optional[Iterable[str]] = None,
        revoke: Optional[Iterable[str]] = None,
    ) -> _accounts.AccountNode:
        """调整成员权限（只能调整自己可管理的位置；防提权）。"""
        grant_list = sorted({str(s).strip() for s in (grant or []) if str(s).strip()})
        revoke_list = sorted({str(s).strip() for s in (revoke or []) if str(s).strip()})
        if not grant_list and not revoke_list:
            raise _accounts.AccountError(400, "ACCOUNT_NO_CHANGE", "没有需要变更的权限")
        with self._lock:
            node = self._require_can_manage(
                actor=actor, target=target, action="account.permission.update"
            )
            decision, code = _accounts.grant_decision(
                self._accounts, actor, target, grant_list
            )
            if decision != "allow":
                self._audit_denial(
                    action="account.permission.grant", actor=actor, target=target,
                    code=code or _accounts.CODE_PRIVILEGE_ESCALATION,
                )
                raise _accounts.AccountError(
                    403, _accounts.CODE_PRIVILEGE_ESCALATION, "无权授予该权限"
                )
            decision, code = _accounts.revoke_decision(
                self._accounts, actor, target, revoke_list
            )
            if decision != "allow":
                self._audit_denial(
                    action="account.permission.revoke", actor=actor, target=target,
                    code=code or _accounts.CODE_PRIVILEGE_ESCALATION,
                )
                raise _accounts.AccountError(
                    403, _accounts.CODE_PRIVILEGE_ESCALATION, "无权授予该权限"
                )
            current = set(node.permissions)
            updated_permissions = frozenset((current | set(grant_list)) - set(revoke_list))
            updated = node.with_changes(permissions=updated_permissions).bump(
                "permission-change"
            )
            self._accounts[target] = updated
            self._persist_account(updated)
            if grant_list:
                self._account_audit(
                    action="account.permission.granted",
                    actor=actor,
                    target=target,
                    outcome="allowed",
                    details={"granted": grant_list, "permissions": sorted(updated_permissions)},
                )
            if revoke_list:
                self._account_audit(
                    action="account.permission.revoked",
                    actor=actor,
                    target=target,
                    outcome="revoked",
                    details={"revoked": revoke_list, "permissions": sorted(updated_permissions)},
                )
            return updated

    def set_key_sharing(
        self, actor: str, target: str, allow: bool
    ) -> _accounts.AccountNode:
        """设置成员是否允许其 Key 被上方账号使用（可撤销）。

        规格 §5.2：本人可在自己的 Key 设置中随时开启/撤销，不需要管理者身份；
        管理者也可代为调整。
        """
        with self._lock:
            if actor == target:
                node = self._accounts.get(actor)
                if node is None:
                    raise _accounts.neutral_denied()
            else:
                node = self._require_can_manage(
                    actor=actor, target=target, action="key.sharing.update"
                )
            if node.parent_id is None:
                # 根没有"上方账号"，该开关对根没有意义。
                raise _accounts.AccountError(
                    400, "ACCOUNT_NO_CHANGE", "该操作不适用于当前账号"
                )
            updated = node.with_changes(
                allow_superior_key_use=bool(allow),
                key_auth_source="explicit_toggle",
            )
            if actor != target:
                # 管理者代改他人共享 → 对方旧令牌即时失效（需要重新登录）。
                # 本人改自己的开关不影响自己的凭据，不 bump，避免自锁。
                updated = updated.bump("key-sharing")
            self._accounts[target] = updated
            self._persist_account(updated)
            self._account_audit(
                action="key.sharing.updated",
                actor=actor,
                target=target,
                outcome="allowed",
                details={"allow": bool(allow), "source": "explicit_toggle"},
            )
            return updated

    def key_borrow_status(self, actor: str, owner: str) -> Tuple[bool, Optional[str]]:
        """Key 借用判定，返回 ``(allowed, reason_code)``。

        定稿码（docs/account-hierarchy.md §5.2）：
        * ``ACCOUNT_KEY_UPWARD_DENIED`` / ``ACCOUNT_KEY_NOT_OWNED`` -- 越出子树；
        * ``ACCOUNT_KEY_CONSENT_REQUIRED`` -- 目标未开启共享授权（403）。
        """
        with self._lock:
            if not _accounts.can_borrow_key(self._accounts, actor, owner):
                actor_node = self._accounts.get(actor)
                owner_node = self._accounts.get(owner)
                if actor_node is None or owner_node is None:
                    return False, _accounts.CODE_KEY_UPWARD_DENIED
                if actor_node.depth > owner_node.depth:
                    return False, _accounts.CODE_KEY_UPWARD_DENIED
                return False, _accounts.CODE_KEY_NOT_OWNED
            if actor != owner and not _accounts.key_borrow_authorized(
                self._accounts, owner
            ):
                return False, _accounts.CODE_KEY_CONSENT_REQUIRED
            return True, None

    # ---- 状态：停用 / 删除 / 级联 ---------------------------------------

    def set_account_status(
        self,
        actor: str,
        target: str,
        status: str,
        reason: str = "",
    ) -> Dict[str, Any]:
        """停用 / 删除成员，级联整棵子树。

        * 审计记录**永不删除**（这里只改状态）；
        * 记忆与文件保留（"归档"而非抹除），返回 ``memory_archived=True``；
        * Key 只是标记 ``key_unbound_hint``，不物理删除，避免误删不可恢复。
        """
        if status not in (
            _accounts.ACCOUNT_STATUS_ACTIVE,
            _accounts.ACCOUNT_STATUS_SUSPENDED,
            _accounts.ACCOUNT_STATUS_DELETED,
        ):
            raise _accounts.AccountError(400, "ACCOUNT_STATUS_INVALID", "状态无效")
        with self._lock:
            target_node = self._require_can_manage(
                actor=actor, target=target, action="account.status.update"
            )
            if status == _accounts.ACCOUNT_STATUS_DELETED:
                if target_node.is_root:
                    self._audit_denial(
                        action="account.delete", actor=actor, target=target,
                        code=_accounts.CODE_DELETE_ROOT_FORBIDDEN,
                    )
                    raise _accounts.AccountError(
                        409,
                        _accounts.CODE_DELETE_ROOT_FORBIDDEN,
                        "该账号不能删除",
                    )
                if len(_accounts.subtree_of(self._accounts, target)) > 1:
                    self._audit_denial(
                        action="account.delete", actor=actor, target=target,
                        code=_accounts.CODE_DELETE_HAS_CHILDREN,
                    )
                    raise _accounts.AccountError(
                        409,
                        _accounts.CODE_DELETE_HAS_CHILDREN,
                        "该账号仍有关联的其他账号，请先处理关联账号或选择一并停用",
                    )
            affected: List[str] = []
            for principal in _accounts.subtree_of(self._accounts, target):
                node = self._accounts[principal]
                updated = node.with_changes(status=status).bump(f"status:{status}")
                self._accounts[principal] = updated
                self._persist_account(updated)
                affected.append(principal)
                identity = self.get_identity_by_principal(principal)
                if identity is not None:
                    if status == _accounts.ACCOUNT_STATUS_ACTIVE:
                        identity.status = IdentityStatus.ACTIVE
                    else:
                        identity.status = IdentityStatus.SUSPENDED
            self._account_audit(
                action="account.status.updated",
                actor=actor,
                target=target,
                outcome="allowed",
                details={
                    "status": status,
                    "affected_count": len(affected),
                    "reason": reason,
                    "audit_retained": True,
                    "memory_archived": True,
                    "key_unbound_hint": status != _accounts.ACCOUNT_STATUS_ACTIVE,
                },
            )
            return {
                "target": target,
                "status": status,
                "affected": affected,
                "audit_retained": True,
                "memory_archived": True,
                "key_unbound_hint": status != _accounts.ACCOUNT_STATUS_ACTIVE,
            }

    def bump_account_epoch(self, principal: str, reason: str = "") -> Optional[_accounts.AccountNode]:
        """让已签发凭据在下一个请求失效（权限/归属变更后调用）。"""
        with self._lock:
            node = self._accounts.get(principal)
            if node is None:
                return None
            updated = node.bump(reason)
            self._accounts[principal] = updated
            self._persist_account(updated)
            return updated

    # ---- 根恢复 / 备份 ---------------------------------------------------

    def rotate_recovery_code(self, principal: str) -> Tuple[str, str]:
        """为根账号轮换一次性恢复码（只存哈希，明文只返回一次）。"""
        with self._lock:
            root = self.account_root()
            if root is None or root.principal != principal:
                self._audit_denial(
                    action="account.recovery.rotate", actor=principal,
                    target=principal, code="ACCOUNT_SCOPE_DENIED",
                )
                raise _accounts.neutral_denied()
            code = _accounts.generate_code("RC")
            node = self._accounts[principal]
            updated = node.with_changes(recovery_hash=_accounts.hash_code(code))
            self._accounts[principal] = updated
            self._persist_account(updated)
            self._account_audit(
                action="account.recovery.rotated",
                actor=principal,
                target=principal,
                outcome="allowed",
            )
            return code, updated.updated_at

    def consume_recovery_code(self, code: str) -> str:
        """校验并消费根恢复码；返回根 principal。"""
        key = "recover:root"
        _accounts.limiter.check(
            key, limit=_accounts.MAX_RECOVERY_ATTEMPTS,
            window=_accounts.RECOVERY_ATTEMPT_WINDOW_SECONDS,
        )
        with self._lock:
            root = self.account_root()
            if root is None or not root.recovery_hash:
                _accounts.limiter.register(key)
                raise _accounts.AccountError(
                    401, "RECOVERY_CODE_INVALID", "恢复信息无效或已过期"
                )
            # 兼容旧版本：早期恢复码绑定本机完整性密钥；新码不再绑定。
            stored = root.recovery_hash
            legacy_key = _persistence.integrity_key() or ""
            if not (
                _accounts.code_matches(code, stored)
                or (legacy_key and _accounts.code_matches(code, stored, legacy_key))
            ):
                _accounts.limiter.register(key)
                self._audit_denial(
                    action="account.recovery.use", actor="anonymous",
                    target=root.principal, code="RECOVERY_CODE_INVALID",
                )
                raise _accounts.AccountError(
                    401, "RECOVERY_CODE_INVALID", "恢复信息无效或已过期"
                )
            updated = root.with_changes(recovery_hash="", status=_accounts.ACCOUNT_STATUS_ACTIVE).bump(
                "recovery"
            )
            self._accounts[root.principal] = updated
            self._persist_account(updated)
            _accounts.limiter.reset(key)
            self._account_audit(
                action="account.recovery.used",
                actor=root.principal,
                target=root.principal,
                outcome="allowed",
            )
            return root.principal

    def build_backup_payload(
        self, actor: str, scope: str = "all"
    ) -> Dict[str, Any]:
        """组装身份/账号部分的备份清单。

        ``scope="all"`` 仅根账号可用（整棵树）；成员只能 ``scope="self"``。
        凭据、模型 Key、文件、记忆由 gateway 对应 store 追加，见
        ``gateway/accounts.py``。
        """
        with self._lock:
            actor_node = self._accounts.get(actor)
            if actor_node is None:
                raise _accounts.neutral_denied()
            wanted = str(scope or "all").strip().lower()
            if wanted not in ("all", "self"):
                raise _accounts.AccountError(
                    400, "ACCOUNT_SCOPE_INVALID", "导出范围无效"
                )
            if wanted == "all":
                if not actor_node.is_root:
                    self._audit_denial(
                        action="account.backup.export", actor=actor, target=actor,
                        code="ACCOUNT_SCOPE_DENIED",
                    )
                    raise _accounts.neutral_denied()
                principals = sorted(self._accounts)
            else:
                # ``self`` 就是"只有本人"：成员不能借 self 带走整棵子树，
                # 根账号也不会因为 self 把全树打包。
                principals = [actor]
            identities = []
            for principal in principals:
                identity = self.get_identity_by_principal(principal)
                if identity is None:
                    continue
                identities.append(
                    {
                        "principal": principal,
                        "id": identity.id,
                        "permissions": sorted(identity.permissions),
                        "scope": identity.scope.value,
                        "trust_score": identity.trust_score,
                        "status": identity.status.value,
                        "metadata": dict(identity.metadata or {}),
                        "created_at": identity.created_at.isoformat(),
                    }
                )
            accounts = [self._accounts[p].to_record() for p in principals]
            return {
                "root_principal": self.account_root().principal
                if self.account_root()
                else actor,
                "scope": wanted,
                "identities": identities,
                "accounts": accounts,
            }

    def apply_backup_payload(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """把备份里的身份与账号树写回本机。

        仅允许在**本机没有根账号**时认领（防"把别人整机覆盖"）；恢复后仍保持
        单根。防重放由 gateway 的 bundle_id 回执负责。
        """
        if not isinstance(payload, dict):
            raise _accounts.AccountError(400, "BACKUP_INVALID", "凭据包无效")
        identities = payload.get("identities") or []
        accounts = payload.get("accounts") or []
        if not isinstance(identities, list) or not isinstance(accounts, list):
            raise _accounts.AccountError(400, "BACKUP_INVALID", "凭据包无效")
        with self._lock:
            if self.account_root() is not None:
                self._audit_denial(
                    action="account.backup.import", actor="anonymous",
                    target="-", code="IMPORT_NOT_ALLOWED",
                )
                raise _accounts.AccountError(
                    409, "IMPORT_NOT_ALLOWED", "本机已存在账号，无法导入"
                )
            nodes: Dict[str, _accounts.AccountNode] = {}
            for record in accounts:
                try:
                    node = _accounts.AccountNode.from_record(record)
                except Exception:  # noqa: BLE001
                    continue
                nodes[node.principal] = node
            if not nodes:
                raise _accounts.AccountError(400, "BACKUP_INVALID", "凭据包无效")
            roots_found = _accounts.roots(nodes)
            if len(roots_found) != 1:
                raise _accounts.AccountError(400, "BACKUP_INVALID", "凭据包无效")
            failures = _accounts.detect_cycles(nodes)
            if failures:
                raise _accounts.AccountError(400, "BACKUP_INVALID", "凭据包无效")
            restored = 0
            for entry in identities:
                if not isinstance(entry, dict):
                    continue
                principal = normalize_principal(str(entry.get("principal") or "")).strip()
                if principal not in nodes:
                    continue
                if self.get_identity_by_principal(principal) is not None:
                    continue
                try:
                    scope = IdentityScope(str(entry.get("scope") or "L3"))
                except ValueError:
                    scope = IdentityScope.L3
                metadata = dict(entry.get("metadata") or {})
                metadata[METADATA_KIND_KEY] = HUMAN_KIND
                identity = self.create_identity(
                    principal=principal,
                    permissions=set(entry.get("permissions") or []),
                    scope=scope,
                    trust_score=float(entry.get("trust_score") or 0.5),
                    metadata=metadata,
                )
                if identity is not None:
                    self._persist_human_identity(identity)
                    restored += 1
            for node in nodes.values():
                self._accounts[node.principal] = node
                self._persist_account(node)
            self._accounts_booted = True
            self._account_audit(
                action="account.backup.imported",
                actor="anonymous",
                target=nodes[roots_found[0].principal].principal,
                outcome="allowed",
                details={"accounts": len(nodes), "identities": restored},
            )
            return {
                "accounts": len(nodes),
                "identities": restored,
                "root_principal": roots_found[0].principal,
            }

    def record_import_receipt(self, bundle_id: str) -> bool:
        return bool(self._store.record_import_receipt(bundle_id))

    def has_import_receipt(self, bundle_id: str) -> bool:
        return bool(self._store.has_import_receipt(bundle_id))

    def account_registry_report(self) -> Dict[str, Any]:
        nodes = self.account_nodes()
        return {
            "accounts": len(nodes),
            "roots": sorted(n.principal for n in nodes.values() if n.is_root),
            "max_depth": _accounts.max_depth(),
            "store": self.describe_store(),
            "last_account_load": getattr(self._store, "last_account_report", {}),
        }

    def suspend_identity(self, principal: str, reason: str = "") -> bool:
        """Revoke ``principal``'s standing as a human who may hold sovereignty.

        Return True only when the identity is **actually** suspended -- i.e.
        when it can no longer satisfy :func:`is_human_identity`.

        Why this exists: deleting a row from the human-identity store removed
        the *persisted* registration but left the in-memory identity ACTIVE, so
        an approval the operator had just revoked still authorised a CRITICAL
        action until the next restart. Sovereignty therefore had a grant path
        and no *live* recall path: the operator-facing revoke command could
        only promise "this stops working after you restart".

        ``IdentityStatus.SUSPENDED`` was defined but read by no code at all;
        ``is_human_identity`` requires ACTIVE, so setting it here is what makes
        revocation take effect in the running process.

        SUSPENDED rather than deleted on purpose: revocation must be reversible
        and must not destroy the audit record of who was registered. Deleting
        the identity would make "who approved this last Tuesday?" unanswerable.

        Outstanding sovereignty grants are recalled too -- see
        :func:`src.kernels._sovereignty.revoke_grants_for_principal`. A
        suspended human holding a live grant is exactly the failure this method
        exists to prevent, so it refuses to report success if that recall did
        not happen.

        Returns False (and audits the refusal) when there is no such identity,
        when it is already suspended (idempotent, still True), or when the
        grant recall fails.
        """
        with self._lock:
            identity = self.get_identity_by_principal(principal)
            if identity is None:
                self._audit(
                    identity_id=principal,
                    operation="suspend",
                    scope=IdentityScope.L0,
                    result="denied",
                    reason=f"cannot suspend {principal!r}: no such identity",
                )
                mark_action_denied("no such identity to suspend")
                logger.error("refusing suspend: no identity for principal %r", principal)
                return False

            if identity.status == IdentityStatus.SUSPENDED:
                return True

            # Recall every grant held by this principal BEFORE the identity
            # stops being a human: order matters because the adjudication path
            # re-verifies humanness, and a grant we failed to recall would
            # otherwise linger as authority with no owner.
            try:
                from src.kernels._sovereignty import revoke_grants_for_principal
                revoked = revoke_grants_for_principal(principal, reason=reason)
            except Exception as exc:  # noqa: BLE001 - refusal, not a crash
                self._audit(
                    identity_id=identity.id,
                    operation="suspend",
                    scope=identity.scope,
                    result="denied",
                    reason=(
                        f"refusing to suspend {principal!r}: outstanding sovereignty "
                        f"grants could not be recalled ({exc})"
                    ),
                )
                mark_action_denied("grants could not be recalled")
                logger.error(
                    "refusing suspend of %r: grant recall failed: %s", principal, exc,
                )
                return False

            identity.status = IdentityStatus.SUSPENDED
            self._audit(
                identity_id=identity.id,
                operation="suspend",
                scope=identity.scope,
                result="allowed",
                reason=(
                    f"identity suspended and no longer counts as a human: "
                    f"{reason or 'no reason given'} "
                    f"(sovereignty grants recalled: {revoked})"
                ),
            )
            logger.warning(
                "suspended identity %r; it can no longer approve HIGH/CRITICAL "
                "actions and %d outstanding grant(s) were recalled",
                principal, revoked,
            )
            return True

    def list_identities(self, scope: Optional[IdentityScope] = None) -> List[AgentIdentity]:
        """List identities, optionally filtered by scope."""
        with self._lock:
            if scope:
                scope_order = {s: i for i, s in enumerate(IdentityScope)}
                min_idx = scope_order[scope]
                return [
                    ident for ident in self._identities.values()
                    if scope_order[ident.scope] >= min_idx
                ]
            return list(self._identities.values())

    @kernel_action("identity.grant_permission")
    def grant_permission(
        self,
        identity_id: str,
        permission: str,
        scope: IdentityScope = IdentityScope.L1,
        reason: str = ""
    ) -> bool:
        """Grant a permission to an identity."""
        with self._lock:
            identity = self._get_identity(identity_id)
            if not identity:
                # A refusal reported by returning is still a refusal: without
                # this, the enclosing @kernel_action stamps outcome="success"
                # and the chain records a grant to a principal that does not
                # exist as a successful permission grant.
                mark_action_denied(
                    f"identity {identity_id!r} not found; refusing to grant "
                    f"{permission!r} to an unknown principal"
                )
                return False

            # Scope check: permission scope cannot exceed identity scope
            scope_order = {s: i for i, s in enumerate(IdentityScope)}
            if scope_order[scope] > scope_order[identity.scope]:
                # Record denied audit
                audit = AuditEntry(
                    identity_id=identity_id,
                    operation="grant",
                    permission=permission,
                    scope=scope,
                    result="denied",
                    reason=f"Permission scope {scope} exceeds identity scope {identity.scope}",
                )
                self._audit_log.append(audit)
                # Same reason, same lie prevented: ``_audit_log`` is an
                # in-process list, not the authoritative chain.
                mark_action_denied(
                    f"permission scope {scope} exceeds identity scope "
                    f"{identity.scope} (identity={identity_id!r})"
                )
                return False

            identity.permissions.add(permission)
            identity.last_modified = utc_now()

            # Record audit event
            audit = AuditEntry(
                identity_id=identity_id,
                operation="grant",
                permission=permission,
                scope=scope,
                result="allowed",
                reason=reason or f"Permission {permission} granted to identity {identity_id}",
            )
            self._audit_log.append(audit)

            return True

    @kernel_action("identity.revoke_permission")
    def revoke_permission(
        self,
        identity_id: str,
        permission: str,
        reason: str = ""
    ) -> bool:
        """Revoke a permission from an identity."""
        with self._lock:
            identity = self._get_identity(identity_id)
            if not identity:
                # Same lie as the grant path: a bare ``return False`` reads as a
                # completed revocation on the authoritative chain unless the
                # refusal is declared here.
                mark_action_denied(
                    f"identity {identity_id!r} not found; refusing to revoke "
                    f"{permission!r} from an unknown principal"
                )
                return False

            if permission not in identity.permissions:
                mark_action_denied(
                    f"identity {identity_id!r} does not hold {permission!r}; "
                    f"nothing was revoked"
                )
                return False

            identity.permissions.discard(permission)
            identity.last_modified = utc_now()

            # Record audit event
            audit = AuditEntry(
                identity_id=identity_id,
                operation="revoke",
                permission=permission,
                scope=identity.scope,
                result="allowed",
                reason=reason or f"Permission {permission} revoked from identity {identity_id}",
            )
            self._audit_log.append(audit)

            return True

    def audit_trail(
        self,
        identity_id: str,
        scope: IdentityScope = IdentityScope.L0,
        since: Optional[datetime] = None
    ) -> List[AuditEntry]:
        """Get audit trail for an identity."""
        with self._lock:
            entries = self._audit_log

            if identity_id:
                entries = [e for e in entries if e.identity_id == identity_id]

            if scope:
                scope_order = {s: i for i, s in enumerate(IdentityScope)}
                min_idx = scope_order[scope]
                entries = [e for e in entries if scope_order[e.scope] >= min_idx]

            if since:
                entries = [e for e in entries if e.timestamp >= since]

            return sorted(entries, key=lambda e: e.timestamp, reverse=True)

    def check_permission(
        self,
        identity_id: str,
        permission: str,
        scope: IdentityScope = IdentityScope.L1,
    ) -> bool:
        """Check if an identity has a specific permission."""
        with self._lock:
            identity = self._get_identity(identity_id)
            if not identity:
                return False

            # Lifecycle enforcement: only ACTIVE identities may act.
            if identity.status != IdentityStatus.ACTIVE:
                return False

            # Scope check
            scope_order = {s: i for i, s in enumerate(IdentityScope)}
            if scope_order[scope] > scope_order[identity.scope]:
                return False

            return permission in identity.permissions

    def stats(self) -> Dict[str, Any]:
        """Get manager statistics."""
        with self._lock:
            return {
                "total_identities": len(self._identities),
                "active_identities": sum(1 for i in self._identities.values() if i.status == IdentityStatus.ACTIVE),
                "total_audit_entries": len(self._audit_log),
                "identities_by_scope": {
                    s.value: sum(1 for i in self._identities.values() if i.scope == IdentityScope(s))
                    for s in IdentityScope
                },
            }

    def initialize(self) -> None:
        self.lifecycle = KernelLifecycle.READY

    def shutdown(self) -> None:
        self.lifecycle = KernelLifecycle.STOPPED

    def pause(self) -> None:
        if self.lifecycle not in (KernelLifecycle.READY, KernelLifecycle.UNINITIALIZED):
            raise KernelStateError(f"cannot pause from {self.lifecycle}")
        self.lifecycle = KernelLifecycle.PAUSED

    def resume(self) -> None:
        if self.lifecycle is not KernelLifecycle.PAUSED:
            raise KernelStateError(f"cannot resume from {self.lifecycle}")
        self.lifecycle = KernelLifecycle.READY


# Global identity manager instance
_global_manager: Optional[IdentityManager] = None
_global_lock = threading.Lock()


def get_identity_manager() -> IdentityManager:
    """Get or create the global identity manager."""
    global _global_manager
    if _global_manager is None:
        with _global_lock:
            if _global_manager is None:
                _global_manager = IdentityManager()
                _global_manager.initialize()  # 存在即 READY：构造完成即视为就绪
    return _global_manager


# Convenience functions
def create_identity(
    principal: str,
    permissions: Optional[Set[str]] = None,
    scope: IdentityScope = IdentityScope.L1,
    trust_score: float = 0.5
) -> AgentIdentity:
    """Create a new identity."""
    return get_identity_manager().create_identity(principal, permissions, scope, trust_score)


def grant_permission(
    identity_id: str,
    permission: str,
    scope: IdentityScope = IdentityScope.L1,
    reason: str = ""
) -> bool:
    """Grant a permission to an identity."""
    return get_identity_manager().grant_permission(identity_id, permission, scope, reason)


def revoke_permission(
    identity_id: str,
    permission: str,
    reason: str = ""
) -> bool:
    """Revoke a permission from an identity."""
    return get_identity_manager().revoke_permission(identity_id, permission, reason)


def audit_trail(
    identity_id: str,
    scope: IdentityScope = IdentityScope.L0,
    since: Optional[datetime] = None
) -> List[AuditEntry]:
    """Get audit trail for an identity."""
    return get_identity_manager().audit_trail(identity_id, scope, since)


def check_permission(
    identity_id: str,
    permission: str,
    scope: IdentityScope = IdentityScope.L1
) -> bool:
    """Check if an identity has a specific permission."""
    return get_identity_manager().check_permission(identity_id, permission, scope)


def create_identity_with_permissions(
    principal: str,
    permissions: Set[str],
    scope: IdentityScope = IdentityScope.L1,
    trust_score: float = 0.5
) -> AgentIdentity:
    """Create identity and immediately grant permissions."""
    identity = create_identity(principal, permissions, scope, trust_score)
    for perm in permissions:
        grant_permission(identity.id, perm, scope)
    return identity


def get_identity_stats() -> Dict[str, Any]:
    """Get identity manager statistics."""
    return get_identity_manager().stats()


def resolve_principal_fingerprint(principal: str) -> Optional[str]:
    """Canonical fingerprint for ``principal``, or ``None`` if unregistered.

    **The single primitive the audit side and the authorization side must both
    converge on** (PHASE 3.6 / A2 keyspace convergence).

    Before this existed the two sides used different key spaces: the
    authoritative audit chain indexed identities by a unique **principal name**,
    while the authorization path indexed them by a **32-bit** ``id``
    (``uuid4()[:8]``). Two key spaces in different domains can never be
    cross-checked against one another, so when a 32-bit id collided the audit
    record stayed field-for-field true, ``verify_integrity()`` still passed, and
    nothing anywhere could falsify that one principal's authority had been
    exercised by another. Both sides now resolve through the same object, and
    :attr:`AgentIdentity.fingerprint` names it without a 32-bit surface.
    """
    manager = get_identity_manager()
    identity = manager.get_identity_by_principal(principal)
    if identity is None:
        identity = manager.get_identity(principal)
    return identity.fingerprint if identity is not None else None


def describe_identity_namespaces() -> Dict[str, Any]:
    """Report the id-namespace split and any boot-time registry refusal."""
    return get_identity_manager().describe_identity_namespaces()
