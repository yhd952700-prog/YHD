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
import re
from typing import Any, Dict, List, Optional, Set
import uuid
import threading

from src.kernels._crosscutting import kernel_action, mark_action_denied
from . import _persistence

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
