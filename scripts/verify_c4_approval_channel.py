"""Reproducible verification for Policy C-4 (audited approval channel).

Run:  .venv/Scripts/python.exe scripts/verify_c4_approval_channel.py
Exit code 0 = all assertions green.

What this proves:
  1. The kernel enforcement switch exists and **defaults to OFF** (production
     stays record-only / L1); its selection syntax is fail-loud.
  2. Arming the switch really arms the C-2 gate for *production* call sites
     (which keep ``enforce=False``) -- no 43-point edit required.
  3. Approval grants are bounded (TTL + ceiling), revocable, least-privilege
     (HIGH/CRITICAL only, known actions only) and human-only (service identities
     and unknown/inactive principals refused).
  4. The audit chain closes: grant issue/revoke events exist AND an action
     executed under a window carries ``sovereignty_grant`` in its own record.
  5. SAFETY GUARDS: no production ``@kernel_action`` flips ``enforce=True``;
     no statically-resolvable opener of a sovereignty channel under ``src/``;
     the approval request
     model cannot name the approver (the principal is token-only); and exactly
     three surfaces arm the switch for a deployed environment: the production
     deployment manifest (``docker-compose.prod.yml``), the staging deployment
     contract (``infra/staging/docker-compose.yml``) and the cloud-bundle
     launcher template (``scripts/build_cloud_bundle.py``), each resolving to the
     CRITICAL tier **minus the audited exemptions** (C-6; D24 narrowed the
     deployment to CRITICAL only). Staging is a *deployed* pre-prod environment,
     so mirroring production's CRITICAL there is the fail-closed-correct posture;
     it exercises the real gate before prod instead of staying record-only.
     Local dev, CI and the Dockerfile must never arm it, so the suite keeps
     exercising the record-only (L1) contract.

     Bounds of that last claim, stated rather than left implicit: the scan now
     covers ``scripts/`` as well (the exclusion was removed), so the bundle
     launcher is in scope and asserted to arm exactly CRITICAL -- not
     ``HIGH,CRITICAL``. A Python write of the variable whose **value is a
     variable** (a verification harness probing reachability, e.g.
     ``scripts/verify_armed_actions_are_inert.py``) is *not* classified as a
     deployment-arming site, because it ships no default spec; only a literal
     spec write is. "Exactly three surfaces arm a shipped default, all CRITICAL"
     is therefore what is asserted, and it holds.

     Bounds of the opener claim too, stated rather than left implicit: the scan
     covers ``src/`` only -- not ``tests/`` or ``scripts/``, which must be able to
     open a window to exercise the channel -- and it resolves symbol *names*, so a
     call whose symbol cannot be resolved statically (:func:`getattr` with a
     runtime name) is out of scope. The label says "statically-resolvable opener
     ... under src/" for exactly that reason. This file previously asserted the
     unqualified "no production kernel code opens a sovereignty channel" over a
     ``src/kernels/``-only scan, which is the defect the c3 guard names in its own
     comment ("the label must not be broader than what the checker actually
     proves").

C-6 update (2026-09-12, Round 75)
---------------------------------
Until Round 75 this file asserted "the production manifest arms CRITICAL exactly"
and "no HIGH action is armed" -- the correct invariant while the HIGH call-site
audit was still outstanding. The audit is now done (every HIGH action measured
unreachable from production, except ``capability.register``), so those two checks
were replaced by the stronger, more specific pair:

* the armed set must equal ``(HIGH | CRITICAL) - EXEMPT_ACTIONS`` -- i.e. arming
  nothing extra *and* silently dropping nothing; and
* no exempt action may be armed, and naming one in the spec must raise.

D24 (2026-09-12): the deployment was narrowed from ``HIGH,CRITICAL`` to
``CRITICAL`` only, so the check below now compares against the CRITICAL tier
minus exemptions. The HIGH-tier audit is still complete (every HIGH action
measured unreachable, one exempted); it is simply no longer armed by default.

Reachability itself is not asserted here (a static file cannot see it); it is
measured dynamically by ``scripts/verify_armed_actions_are_inert.py``.
"""

from __future__ import annotations

import ast
import importlib
import os
import pathlib
import re
import subprocess
import sys
import time
from typing import Dict, List, Optional

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

SRC_DIR = REPO_ROOT / "src"
DEFINING_MODULE = (SRC_DIR / "kernels" / "_sovereignty.py").resolve()
POLICY_ROUTER = REPO_ROOT / "src" / "gateway" / "policy.py"

#: The only file allowed to arm the switch: the production deployment manifest.
#: Arming is a deliberate, reviewable deployment decision (C-5); dev and CI stay
#: off so the suite keeps exercising the record-only (L1) contract.
PROD_MANIFEST = "docker-compose.prod.yml"

#: The second legitimate arming site: the cloud-bundle launcher template.
#: ``scripts/build_cloud_bundle.py`` renders ``LIUHAO_KERNEL_POLICY_ENFORCE``
#: unconditionally into the generated ``serve.py`` (D24 requires it to be
#: CRITICAL, matching the production manifest). It is scanned alongside the
#: manifest and must resolve to exactly CRITICAL.
BUNDLE_LAUNCHER = "scripts/build_cloud_bundle.py"

#: The third legitimate arming site: the *staging* deployment contract
#: (``infra/staging/docker-compose.yml``, added by the G9 staging-verification
#: chain). Staging is a DEPLOYED pre-prod environment, not a local dev/CI run, so
#: mirroring production's CRITICAL enforcement there is the fail-closed-correct
#: posture -- it exercises the real gate before prod, instead of staying
#: record-only. It must also resolve to exactly CRITICAL. Local dev, CI and the
#: Dockerfile stay record-only (L1) on purpose, so the suite keeps exercising the
#: un-armed contract.
STAGING_MANIFEST = "infra/staging/docker-compose.yml"

RESULTS = []


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((ok, name, detail))
    mark = "PASS" if ok else "FAIL"
    print(f"[{mark}] {name}" + (f" -- {detail}" if detail else ""))


def _fresh_manager_with_human():
    from src.kernels.identity import IdentityManager, IdentityScope

    mgr = IdentityManager()
    human = mgr.create_identity(
        "human-c4-verify", scope=IdentityScope.L0, metadata={"kind": "human"}
    )
    return mgr, human


def _discover_enforce_flags() -> dict:
    """Map of action-name -> True for any production call site passing enforce=True."""
    enforced: dict = {}
    for path in SRC_DIR.rglob("*.py"):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for dec in node.decorator_list:
                if not isinstance(dec, ast.Call) or not dec.args:
                    continue
                func = dec.func
                fname = getattr(func, "id", None) or getattr(func, "attr", None)
                if fname != "kernel_action":
                    continue
                arg0 = dec.args[0]
                if not (isinstance(arg0, ast.Constant) and isinstance(arg0.value, str)):
                    continue
                for kw in dec.keywords:
                    if (
                        kw.arg == "enforce"
                        and isinstance(kw.value, ast.Constant)
                        and kw.value.value is True
                    ):
                        enforced[arg0.value] = True
    return enforced


#: Symbols that *open* the channel. Distinguished by name from the readers
#: (``get_active_sovereignty`` et al), so an over-broad "anything imported from
#: _sovereignty" rule -- which would flag legitimate reads -- is never needed.
#: ``grant_window`` is an opener by the module's own account: ``_sovereignty.py``
#: names it alongside ``human_sovereign`` / ``set_active_sovereignty`` as a way to
#: open the channel, and it returns a ``human_sovereign`` bound to the grant.
_OPENER_SYMBOLS = frozenset(
    {"human_sovereign", "set_active_sovereignty", "ActiveSovereignty", "grant_window"}
)


def _folded_str(node: ast.AST) -> Optional[str]:
    """Statically fold a string expression, or ``None`` if it is not one.

    Covers ``"set_active_" + "sovereignty"`` and placeholder-free f-strings --
    the shapes used to keep a symbol name out of a naive
    ``getattr(func, "id", None)`` comparison.
    """
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left = _folded_str(node.left)
        right = _folded_str(node.right)
        if left is not None and right is not None:
            return left + right
        return None
    if isinstance(node, ast.JoinedStr):
        parts = []
        for value in node.values:
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                parts.append(value.value)
            else:
                return None
        return "".join(parts)
    return None


def _import_aliases(tree: ast.AST) -> Dict[str, str]:
    """Map each locally bound name to the symbol it actually refers to.

    ``from src.kernels._sovereignty import human_sovereign as hs`` binds ``hs``
    to ``human_sovereign``, so an aliased call resolves back to the opener
    instead of looking like an unknown function.
    """
    aliases: Dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                aliases[alias.asname or alias.name] = alias.name
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.asname:
                    aliases[alias.asname] = alias.name.rsplit(".", 1)[-1]
    return aliases


def _callee_symbol(func: ast.AST, aliases: Dict[str, str]) -> Optional[str]:
    """The symbol a call target resolves to, or ``None`` if unresolvable.

    Handles ``f(...)``, ``mod.f(...)``, an aliased ``f``, and the folded
    ``getattr(obj, "a" + "b")(...)`` form -- where ``func`` is itself an
    ``ast.Call`` and both ``id`` and ``attr`` are ``None``, which is exactly why
    the previous marker comparison could never see it.
    """
    if isinstance(func, ast.Name):
        return aliases.get(func.id, func.id)
    if isinstance(func, ast.Attribute):
        return func.attr
    if isinstance(func, ast.Call):
        inner = func.func
        name = getattr(inner, "id", None) or getattr(inner, "attr", None)
        if name == "getattr" and len(func.args) >= 2:
            return _folded_str(func.args[1])
    return None


def _production_opens_sovereignty() -> bool:
    """True if any scanned production module OPENS a sovereignty channel.

    The claim is bounded to the shapes actually covered:

    * a **direct** call ``human_sovereign(...)`` / ``set_active_sovereignty(...)``
      / ``ActiveSovereignty(...)`` / ``grant_window(...)``;
    * an **attribute** call ``mod.set_active_sovereignty(...)``;
    * an **import alias** (``from ... import human_sovereign as hs`` then
      ``hs(...)``), resolved through the module's own import table;
    * a **folded** ``getattr(obj, "set_active_" + "sovereignty")(...)``;

    scanning ``src/`` (not only ``src/kernels/``). Reading the channel --
    ``get_active_sovereignty`` -- is the mechanism itself and is *not* flagged in
    any of the same four shapes: the predicate resolves to a symbol *name* and
    only the opener names above are openers.

    Deliberately out of scope, named rather than left implicit: ``tests/`` and
    ``scripts/`` are not scanned at all, because a harness must be able to open
    a window to exercise the channel. Neither is a call whose symbol cannot be
    resolved statically (e.g. ``getattr(obj, runtime_name)``). The defining
    module ``src/kernels/_sovereignty.py`` -- and only it, matched by resolved
    path (:data:`DEFINING_MODULE`) -- is skipped: it is where these symbols are
    *defined*, so a definition there is not a production opener. A different file
    that merely shares the basename is scanned like any other.
    """
    for path in SRC_DIR.rglob("*.py"):
        if path.resolve() == DEFINING_MODULE:
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except SyntaxError:
            continue
        aliases = _import_aliases(tree)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if _callee_symbol(node.func, aliases) in _OPENER_SYMBOLS:
                return True
    return False


def _approval_request_fields() -> set:
    """Field names declared on the approval request model (must exclude principal)."""
    tree = ast.parse(POLICY_ROUTER.read_text(encoding="utf-8"), filename=str(POLICY_ROUTER))
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == "ApprovalRequest":
            fields = set()
            for stmt in node.body:
                if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
                    fields.add(stmt.target.id)
            return fields
    return set()


def _spec_from_line(line: str, var: str) -> Optional[str]:
    """Pull the assigned spec value out of a config line.

    Handles the compose form ``- VAR=${VAR:-CRITICAL}`` (returns the ``:-``
    default) and the plain ``VAR=CRITICAL`` form. Returns ``None`` when the line
    only *mentions* the variable without assigning it.
    """
    idx = line.find(var)
    while idx != -1:
        rest = line[idx + len(var):].lstrip()
        if rest[:1] in ("=", ":"):
            value = rest[1:].strip().strip('"').strip("'")
            default = re.search(r":-([^}]*)\}", value)
            if default is not None:
                return default.group(1).strip()
            if value.startswith("${"):
                # ${VAR} with no default -> inherits the caller's environment.
                return ""
            parts = value.split()
            return parts[0] if parts else ""
        idx = line.find(var, idx + len(var))
    return None


def _tracked_files() -> List[pathlib.Path]:
    """Repository files tracked by git.

    Excludes untracked scratch (e.g. local verification helpers) and gitignored
    build output, so the check verifies the *committed, shippable* deployment
    artifacts -- the production manifest and the cloud-bundle launcher -- rather
    than whatever happens to sit in a working tree. Falls back to a full tree
    walk if git is unavailable.
    """
    try:
        out = subprocess.run(
            ["git", "ls-files", "-z"],
            cwd=str(REPO_ROOT), capture_output=True, text=True, check=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return [p for p in REPO_ROOT.rglob("*") if p.is_file()]
    return [REPO_ROOT / p for p in out.stdout.split("\0") if p]


def _attr_chain(node: ast.AST) -> str:
    """Render a dotted attribute/name chain as ``a.b.c`` (or '' if not a chain)."""
    parts: List[str] = []
    cur = node
    while isinstance(cur, ast.Attribute):
        parts.append(cur.attr)
        cur = cur.value
    if isinstance(cur, ast.Name):
        parts.append(cur.id)
    return ".".join(reversed(parts)) if parts else ""


def _python_arms_env_var(tree: ast.AST, var: str) -> Optional[str]:
    """Return the literal spec a Python file arms ``var`` with, else ``None``.

    Only real arming operations count -- ``os.environ.setdefault('VAR', 'VAL')``,
    ``os.putenv('VAR', 'VAL')``, or ``os.environ['VAR'] = 'VAL'``. A docstring or
    comment that merely *mentions* the pattern (e.g. an example embedded in a
    gate script's docstring) is NOT an AST call/assignment and is therefore
    ignored -- the regex-over-raw-text approach used previously mis-flagged such
    mentions as deployment-arming sites. A write whose value is not a string
    literal (a reachability probe) ships no default and is also ignored.
    """
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            fname = _attr_chain(node.func)
            if fname in ("os.environ.setdefault", "os.putenv") and len(node.args) >= 2:
                key, val = node.args[0], node.args[1]
                if (isinstance(key, ast.Constant) and isinstance(key.value, str)
                        and key.value == var):
                    if isinstance(val, ast.Constant) and isinstance(val.value, str):
                        return val.value
                    return None
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if (isinstance(target, ast.Subscript)
                        and _attr_chain(target.value) == "os.environ"
                        and isinstance(target.slice, ast.Constant)
                        and isinstance(target.slice.value, str)
                        and target.slice.value == var):
                    val = node.value
                    if isinstance(val, ast.Constant) and isinstance(val.value, str):
                        return val.value
                    return None
    return None


def _python_arming_from_text(text_: str, var: str) -> Optional[str]:
    """Return the literal spec a *file's raw text* arms ``var`` with, else None.

    Scans raw text -- including string literals such as a generated launcher
    template -- for ``os.environ.setdefault('VAR', 'VAL')``, ``os.putenv(...)``
    or ``os.environ['VAR'] = 'VAL'``. Used ONLY for the cloud-bundle launcher,
    which ships its arming inside a template string rather than an executable
    call. Every other Python file is checked via AST, so its docstrings and
    comments are never mis-classified as arming sites.
    """
    for line in text_.splitlines():
        if var not in line:
            continue
        patterns = [
            re.escape(var) + r'["\']\s*,\s*["\']([^"\']*)["\']',
            re.escape(var) + r'["\']\s*\]\s*=\s*["\']([^"\']*)["\']',
        ]
        for pat in patterns:
            m = re.search(pat, line)
            if m:
                return m.group(1).strip()
    return None


def _python_arming_for_file(rel: str, text_: str, var: str) -> Optional[str]:
    """Dispatch Python arming detection.

    The bundle launcher is read from raw text because its arming lives in a
    generated template string; every other Python file is checked via AST, so a
    docstring/comment that merely *mentions* the write pattern (e.g. an example
    inside a gate script's docstring) is never mis-classified as an arming site.
    """
    if rel == BUNDLE_LAUNCHER:
        return _python_arming_from_text(text_, var)
    try:
        tree = ast.parse(text_, filename="<" + rel + ">")
    except SyntaxError:
        return None
    return _python_arms_env_var(tree, var)


def _armed_specs() -> dict:
    """Map every file that *arms* the switch to the spec it arms it with.

    Arming = assigning the variable in a config/deployment file, or *writing* it
    from Python with a **literal** spec (``os.environ.setdefault("VAR", "VALUE")``
    / ``putenv`` / ``environ["VAR"] = "VALUE"``). Python detection is AST-based,
    so prose, docstrings and comments that merely *mention* the write pattern are
    not hits -- the enforcement module and the gate scripts are free to document
    or illustrate it. Only an actual ``os.environ[...]`` assignment or
    ``setdefault``/``putenv`` call with a literal value counts as an arming site.

    A Python write whose value is a *variable* (a verification harness probing
    reachability, e.g. ``scripts/verify_armed_actions_are_inert.py`` setting the
    variable to ``action`` / ``spec``) ships no default and is **not** counted as
    an arming site -- only a literal spec write is.

    The scan covers the whole repository **minus ``tests/``** (and ``.venv/``,
    ``.git/``, ``node_modules/``). ``scripts/`` is deliberately scanned so the
    cloud-bundle launcher is visible: D24 requires it to arm exactly CRITICAL,
    matching the production manifest. ``infra/staging/`` is likewise a deployed
    pre-prod contract that legitimately mirrors production's CRITICAL. Three
    surfaces may therefore arm the switch -- the production manifest, the staging
    contract and the bundle launcher -- and the caller asserts all three resolve
    to CRITICAL. Do not read a single hit here as "only one file in the
    repository arms this".
    """
    var = "LIUHAO_KERNEL_POLICY_ENFORCE"
    config_suffixes = {".yml", ".yaml", ".env", ".toml", ".ini", ".cfg", ".sh", ".json"}
    # Enumerated rather than a bare directory skip, so the exclusion is visible
    # at the point it is applied -- and its consequence is recorded above.
    # NOTE: ``scripts/`` is intentionally NOT excluded -- the cloud-bundle
    # launcher is a real arming site that D24 requires to be CRITICAL.
    excluded_roots = (".venv/", ".git/", "node_modules/", "tests/")
    armed = {}
    for path in _tracked_files():
        if not path.is_file():
            continue
        rel = path.relative_to(REPO_ROOT).as_posix()
        if any(rel.startswith(p) for p in excluded_roots):
            continue
        try:
            text_ = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if var not in text_:
            continue
        suffix = path.suffix.lower()
        is_config = suffix in config_suffixes or path.name.startswith("Dockerfile")
        if not is_config and suffix != ".py":
            continue
        if is_config:
            for line in text_.splitlines():
                if var not in line:
                    continue
                spec = _spec_from_line(line, var)
                if spec is not None:
                    armed[rel] = spec
                    break
        else:
            spec = _python_arming_for_file(rel, text_, var)
            if spec is not None:
                armed[rel] = spec
    return armed


def main() -> int:
    import src.kernels.identity as idmod

    enf = importlib.import_module("src.kernels._enforcement")
    xc = importlib.import_module("src.kernels._crosscutting")
    sov = importlib.import_module("src.kernels._sovereignty")

    # 1. the switch exists and defaults to OFF
    os.environ.pop(enf.ENV_VAR, None)
    enf.reload()
    snap = enf.describe()
    check("enforcement switch exists with a stable env var", enf.ENV_VAR, snap["env_var"])
    check("enforcement defaults to OFF", snap["enabled"] is False, f"count={snap['count']}")
    check("enforced_actions() empty by default", enf.enforced_actions() == frozenset())

    check("parse_spec('CRITICAL') -> 2 actions", len(enf.parse_spec("CRITICAL")) == 2)
    check(
        "parse_spec('HIGH') -> 14 actions (15 minus the audited exemption)",
        len(enf.parse_spec("HIGH")) == 14,
        f"got {len(enf.parse_spec('HIGH'))}",
    )
    check(
        "parse_spec explicit action",
        enf.parse_spec("capability.retire") == frozenset({"capability.retire"}),
    )
    check(
        "tier expansion drops every exempt action",
        not (enf.parse_spec("HIGH,CRITICAL") & set(enf.EXEMPT_ACTIONS)),
        f"exempt={sorted(enf.EXEMPT_ACTIONS)}",
    )
    for exempt in sorted(enf.EXEMPT_ACTIONS):
        try:
            enf.parse_spec(exempt)
            check(f"parse_spec rejects the exempt action {exempt!r}", False,
                  "no ValueError raised -- arming it would break production")
        except ValueError:
            check(f"parse_spec rejects the exempt action {exempt!r}", True)
    for bad in ("NOPE", "capability.reitre", "memory.store", "trust.assign_score", "LOW", "MEDIUM"):
        try:
            enf.parse_spec(bad)
            check(f"parse_spec rejects {bad!r}", False, "no ValueError raised")
        except ValueError:
            check(f"parse_spec rejects {bad!r}", True)

    # 2. arming the switch really arms production call sites
    mgr, human = _fresh_manager_with_human()
    orig = idmod.get_identity_manager
    idmod.get_identity_manager = lambda: mgr
    try:
        sov.clear_grants()
        sov.clear_active_sovereignty()

        @xc.kernel_action("capability.retire")  # NOTE: enforce NOT set
        def retire_disarmed():
            return "ran"

        check("switch OFF -> production-style call site is additive",
              retire_disarmed() == "ran")

        os.environ[enf.ENV_VAR] = "capability.retire"
        enf.reload()
        try:

            @xc.kernel_action("capability.retire")  # NOTE: enforce NOT set
            def retire_armed():
                return "ran"

            raised = False
            try:
                retire_armed()
            except xc.PolicyDeferredError as err:
                raised = err.verdict == "defer"
            check("switch ON -> production-style call site now defers (no grant)", raised)

            grant = sov.issue_grant(human.id, ["capability.retire"], reason="c4 verify")
            with sov.grant_window(grant):
                out = retire_armed()
            check("switch ON + grant -> action executes", out == "ran", str(out))
        finally:
            os.environ.pop(enf.ENV_VAR, None)
            enf.reload()

        # 3. grant mechanics
        grant = sov.issue_grant(human.id, ["capability.retire"], reason="mechanics")
        check("grant is active on issue", grant.is_active() and not grant.is_expired())
        check("grant retrievable by id", sov.get_grant(grant.grant_id) is grant)
        check("grant appears in listing",
              grant.grant_id in [g.grant_id for g in sov.list_grants()])
        check("grant to_dict has actions+remaining",
              grant.to_dict()["actions"] == ["capability.retire"]
              and grant.to_dict()["remaining_seconds"] > 0)

        revoked = sov.revoke_grant(grant.grant_id, revoked_by=human.id, reason="done")
        check("revoke marks the grant inactive", revoked.is_revoked and not revoked.is_active())
        check("revoke is idempotent",
              sov.revoke_grant(grant.grant_id).revoked_at == revoked.revoked_at)
        check("revoked grant hidden from default listing",
              grant.grant_id not in [g.grant_id for g in sov.list_grants()])
        try:
            sov.grant_window(revoked)
            check("revoked grant cannot open a window", False, "no ValueError")
        except ValueError:
            check("revoked grant cannot open a window", True)

        expired = sov.issue_grant(human.id, ["capability.retire"], ttl_seconds=0.01)
        time.sleep(0.05)
        try:
            sov.grant_window(expired)
            check("expired grant cannot open a window", False, "no ValueError")
        except ValueError:
            check("expired grant cannot open a window", True)
        check("expired grant hidden from default listing",
              expired.grant_id not in [g.grant_id for g in sov.list_grants()])

        for bad, label in (
            (["not.a.kernel.action"], "unknown action"),
            (["memory.store"], "LOW action"),
            (["trust.assign_score"], "MEDIUM action"),
            ([], "empty action set"),
        ):
            try:
                sov.issue_grant(human.id, bad)
                check(f"issue_grant rejects {label}", False, "no ValueError")
            except ValueError:
                check(f"issue_grant rejects {label}", True)

        for bad in (0, -1, sov.MAX_GRANT_TTL_SECONDS + 1):
            try:
                sov.issue_grant(human.id, ["capability.retire"], ttl_seconds=bad)
                check(f"issue_grant rejects ttl={bad}", False, "no ValueError")
            except ValueError:
                check(f"issue_grant rejects ttl={bad}", True)

        for bad, label in (
            ("does-not-exist", "unknown principal"),
            ("", "empty principal"),
            ("liuhao-internal-service", "service identity"),
        ):
            try:
                sov.issue_grant(bad, ["capability.retire"])
                check(f"issue_grant rejects {label}", False, "no ValueError")
            except ValueError:
                check(f"issue_grant rejects {label}", True)

        # 4. audit chain
        from src.kernels.audit import AuditEventType, audit_query

        def _events(outcome: str):
            return [
                e
                for e in audit_query(
                    event_type=AuditEventType.HUMAN_SOVEREIGNTY_OVERRIDE,
                    limit=80,
                    reverse=True,
                )
                if e.get("outcome") == outcome
            ]

        check("grant issue writes a 'granted' audit event", bool(_events("granted")))
        check("grant revoke writes a 'revoked' audit event", bool(_events("revoked")))

        chained = sov.issue_grant(human.id, ["capability.retire"], reason="chain")

        @xc.kernel_action("capability.retire", enforce=True)
        def retire_enforced():
            return "ran"

        with sov.grant_window(chained):
            retire_enforced()

        details = None
        # PHASE 3.6 / A2 + F26: the audit subject is no longer the literal string
        # "kernel" -- it is the *actual* acting principal (here, the delegated
        # human, because the grant window made the policy engine adjudicate as
        # that human). Pinning the old literal would find either nothing or a
        # stale row left in the shared audit store by an earlier run, and a stale
        # row is a green that proves nothing. So: find the event by WHAT HAPPENED,
        # then assert the attribution as a VALUE rather than reading a column.
        for ev in audit_query(limit=200, reverse=True):
            det = ev.get("details") or {}
            if det.get("action") == "capability.retire" and det.get("policy_decision") == "allow":
                details = det
                fingerprint = det.get("actor_fingerprint")
                check(
                    "allowed action audit carries a usable attribution",
                    isinstance(fingerprint, str)
                    and len(fingerprint) == 32
                    and all(c in "0123456789abcdef" for c in fingerprint),
                    f"actor_fingerprint={fingerprint!r} "
                    f"(F26: a non-empty column is not evidence)",
                )
                check(
                    "audit principal keyspace agrees with the actor identity id",
                    ev.get("principal_id") == det.get("actor_identity_id"),
                    f"principal_id={ev.get('principal_id')!r} vs "
                    f"actor_identity_id={det.get('actor_identity_id')!r}",
                )
                break
        check("allowed action audit carries sovereignty_grant",
              bool(details) and details.get("sovereignty_grant") == chained.grant_id,
              f"grant={getattr(details, 'get', lambda *_: None)('sovereignty_grant')}"
              if details else "no allow record")
        check("allowed action audit names human_sovereignty rule",
              bool(details) and details.get("policy_rule") == "human_sovereignty")
    finally:
        idmod.get_identity_manager = orig
        sov.clear_grants()
        sov.clear_active_sovereignty()

    # 5. safety guards
    flipped = _discover_enforce_flags()
    check("no production @kernel_action has enforce=True",
          not flipped, f"flipped={sorted(flipped)}" if flipped else "")
    check("no statically-resolvable opener of a sovereignty channel under src/",
          not _production_opens_sovereignty())

    fields = _approval_request_fields()
    check("approval request model exposes no principal/issued_by field",
          not ({"principal", "issued_by", "user_id"} & fields), f"fields={sorted(fields)}")
    check("approval request model declares actions/reason/ttl_seconds",
          {"actions", "reason", "ttl_seconds"} <= fields, f"fields={sorted(fields)}")

    from src.kernels._risk_classification import KERNEL_ACTION_RISK, RiskTier
    critical_actions = frozenset(
        a for a, r in KERNEL_ACTION_RISK.items() if r.tier is RiskTier.CRITICAL
    )
    high_actions = frozenset(
        a for a, r in KERNEL_ACTION_RISK.items() if r.tier is RiskTier.HIGH
    )

    armed = _armed_specs()
    allowed_arming = {PROD_MANIFEST, STAGING_MANIFEST, BUNDLE_LAUNCHER}
    unexpected = sorted(k for k in armed if k not in allowed_arming)
    check("only the production manifest, the staging contract, and the "
          "cloud-bundle launcher arm enforcement (dev/CI/Dockerfile never)",
          not unexpected,
          f"unexpected={unexpected}" if unexpected else f"armed via {sorted(armed)}")
    for site in sorted(allowed_arming):
        spec = armed.get(site)
        check(f"{site} arms exactly CRITICAL (D24 deployment truth)",
              spec == "CRITICAL",
              (f"{site} spec={spec!r}" if spec != "CRITICAL"
               else f"armed via {sorted(armed)}"))

    prod_spec = armed.get(PROD_MANIFEST)
    check("production manifest arms the switch explicitly",
          prod_spec is not None,
          f"{PROD_MANIFEST} spec={prod_spec!r}")

    if prod_spec is not None:
        try:
            armed_actions = enf.parse_spec(prod_spec)
            spec_error = None
        except ValueError as exc:
            armed_actions = frozenset()
            spec_error = str(exc)
        check("production manifest spec parses (no typo, no inert entry)",
              spec_error is None, spec_error or f"spec={prod_spec!r}")

        # C-6/D24: the armed set must equal the CRITICAL tier minus the audited
        # exemptions (D24 narrowed the deployment from HIGH,CRITICAL to CRITICAL)
        # -- not more (nothing armed by accident) and not less (nothing silently
        # dropped from the tier expansion).
        audited_surface = critical_actions - set(enf.EXEMPT_ACTIONS)
        check("production manifest arms exactly the audited (CRITICAL) surface",
              armed_actions == audited_surface,
              f"armed={len(armed_actions)} audited={len(audited_surface)} "
              f"missing={sorted(audited_surface - armed_actions)} "
              f"extra={sorted(armed_actions - audited_surface)}")
        check("no exempt action is armed in production",
              not (armed_actions & set(enf.EXEMPT_ACTIONS)),
              f"exempt_armed={sorted(armed_actions & set(enf.EXEMPT_ACTIONS))}")
        check("every exempt action carries a recorded reason",
              all(
                  isinstance(reason, str) and len(reason.strip()) > 20
                  for reason in enf.EXEMPT_ACTIONS.values()
              ),
              f"exempt={sorted(enf.EXEMPT_ACTIONS)}")
        check("reachability is measured by verify_armed_actions_are_inert.py",
              (REPO_ROOT / "scripts" / "verify_armed_actions_are_inert.py").is_file())

    failed = [r for r in RESULTS if not r[0]]
    print()
    if failed:
        print(f"RESULT: {len(failed)} FAILED / {len(RESULTS)} total")
        for _ok, name, detail in failed:
            print(f"  - {name} :: {detail}")
        return 1
    print(f"RESULT: ALL GREEN ({len(RESULTS)} checks)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
