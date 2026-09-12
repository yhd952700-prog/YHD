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
     no production kernel code opens a sovereignty channel; the approval request
     model cannot name the approver (the principal is token-only); and exactly one
     file may arm the switch -- the production manifest, with a spec that resolves
     to the whole gated surface **minus the audited exemptions** (C-6). Dev, CI
     and the Dockerfile must never arm it, so the suite keeps exercising the
     record-only (L1) contract.

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

Reachability itself is not asserted here (a static file cannot see it); it is
measured dynamically by ``scripts/verify_armed_actions_are_inert.py``.
"""

from __future__ import annotations

import ast
import importlib
import os
import pathlib
import re
import sys
import time
from typing import Optional

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

KERNELS_DIR = REPO_ROOT / "src" / "kernels"
POLICY_ROUTER = REPO_ROOT / "src" / "gateway" / "policy.py"

#: The only file allowed to arm the switch: the production deployment manifest.
#: Arming is a deliberate, reviewable deployment decision (C-5); dev and CI stay
#: off so the suite keeps exercising the record-only (L1) contract.
PROD_MANIFEST = "docker-compose.prod.yml"

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
    for path in KERNELS_DIR.rglob("*.py"):
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


def _production_opens_sovereignty() -> bool:
    """True if any production kernel module OPENS a sovereignty channel."""
    markers = ("human_sovereign", "set_active_sovereignty", "ActiveSovereignty")
    for path in KERNELS_DIR.rglob("*.py"):
        if path.name == "_sovereignty.py":
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                func = node.func
                fname = getattr(func, "id", None) or getattr(func, "attr", None)
                if fname in markers:
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


def _armed_specs() -> dict:
    """Map every file that *arms* the switch to the spec it arms it with.

    Arming = assigning the variable in a config/deployment file, or *writing* it
    from Python (``os.environ[...] =`` / ``putenv`` / ``setdefault``). Prose and
    docstrings that merely name the variable are deliberately not hits -- the
    enforcement module and the decorator must be able to document the switch.
    """
    var = "LIUHAO_KERNEL_POLICY_ENFORCE"
    config_suffixes = {".yml", ".yaml", ".env", ".toml", ".ini", ".cfg", ".sh", ".json"}
    armed = {}
    for path in REPO_ROOT.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(REPO_ROOT).as_posix()
        if any(rel.startswith(p) for p in (".venv/", ".git/", "node_modules/",
                                           "tests/", "scripts/")):
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
        for line in text_.splitlines():
            if var not in line:
                continue
            if is_config:
                spec = _spec_from_line(line, var)
                if spec is None:
                    continue
            else:
                writes_env = ("putenv" in line or "setdefault" in line
                              or ("=" in line and "environ" in line))
                if not writes_env:
                    continue
                # A Python env write is always unexpected, whatever it sets.
                spec = _spec_from_line(line, var) or "<env-write>"
            armed[rel] = spec
            break
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
        for ev in audit_query(principal_id="kernel", limit=200, reverse=True):
            det = ev.get("details") or {}
            if det.get("action") == "capability.retire" and det.get("policy_decision") == "allow":
                details = det
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
    check("no production kernel code opens a sovereignty channel",
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
    unexpected = sorted(k for k in armed if k != PROD_MANIFEST)
    check("only the production manifest arms enforcement (dev/CI/Dockerfile never)",
          not unexpected,
          f"unexpected={unexpected}" if unexpected else f"armed via {PROD_MANIFEST} only")

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

        # C-6: the armed set must equal the whole gated surface minus the audited
        # exemptions -- not more (nothing armed by accident) and not less (nothing
        # silently dropped from a tier expansion).
        audited_surface = (high_actions | critical_actions) - set(enf.EXEMPT_ACTIONS)
        check("production manifest arms exactly the audited surface",
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
