#!/usr/bin/env python
"""CI gate: authorization grants must be enumerable + auditable, and a grant's
scope (every effect-changing parameter) must bind the executed action.

This is the alignment control for "授权时看到的目标 == 执行时的目标"
(authorized target == executed target). It encodes the decided mechanism as a
HARD invariant and discloses the owner-pending policy gap as an UNVERIFIED
finding -- never silently green.

Part A -- HARD (decided mechanism: whitelist fingerprint).
    The host_command ApprovalInterface fingerprints ALL seven
    HostCommandRequest fields plus a capability SUBSET, so a single human grant
    cannot silently cover a different directory, capability set, or actor. A
    grant is also one-shot (consume), so its usage count is enumerable.

Part B -- DISCLOSED (owner-pending policy decision, NOT enforced here).
    The sovereignty grant (C-3/C-4) and the policy approval request bind ACTION
    NAMES only; neither carries a `target` field. The enumeration/audit trail
    (list_grants / get_grant / grant_window / issue_grant) is present and usable,
    but the target binding is not yet enforced. Flagged UNVERIFIED so it cannot
    be mistaken for done. The policy default (fill-or-deny, union vs
    intersection, identity_approve visibility) is 鹿补之 + 王权衡's decision.

Exit code: 0 when every DECIDED (Part A) invariant holds. A Part B gap is a
loud WARN, not a hard fail, because it is explicitly an owner-pending decision.
"""

import os
import sys

# Allow running as `python scripts/verify_authorization_enumerable.py` from the
# repo root: the repo root (not scripts/) must be importable for `src.*`.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _hr(title: str) -> None:
    print("=" * 72)
    print(title)
    print("=" * 72)


def _check(label: str, ok: bool, detail: str = "") -> bool:
    mark = "PASS" if ok else "FAIL"
    print(f"  [{mark}] {label}" + (f" -- {detail}" if detail else ""))
    return ok


def _gap(label: str, detail: str) -> None:
    """A disclosed, owner-pending gap (NOT a hard gate failure)."""
    print(f"  [GAP] {label} -- {detail}")


def part_a_host_command() -> bool:
    """HARD: whitelist fingerprint + one-shot usage on the live host-command path."""
    _hr("Part A -- host_command ApprovalInterface (HARD invariant)")
    from src.ai.host_command.approval import ApprovalInterface
    from src.ai.host_command.models import HostCommandRequest

    iface = ApprovalInterface()

    base = HostCommandRequest(
        command="rm report.txt",
        adapter="shell",
        use_shell=False,
        actor="autonomous",
        cwd="/home/alice/workspace",
        capabilities_required=["fs.delete"],
    )
    ar = iface.request(base, reason="delete one report")
    iface.grant(ar.approval_id)

    # The grant's scope must be fully recorded -> enumerable.
    summary = ar.request_summary
    enum_ok = all(k in summary for k in ("command", "adapter", "use_shell",
                                         "actor", "cwd", "capabilities_required"))
    all_ok = _check("grant summary records every behavioural field (enumerable scope)",
                    enum_ok, str(sorted(summary.keys())))

    # Control: identical resubmission is allowed.
    all_ok &= _check("identical resubmission is allowed",
                     iface.granted_id_for(base) == ar.approval_id)

    # A different directory must NOT reuse the grant.
    other_dir = HostCommandRequest(
        command="rm report.txt", adapter="shell", use_shell=False,
        actor="autonomous", cwd="/etc/liuhao", capabilities_required=["fs.delete"],
    )
    all_ok &= _check("grant does NOT transfer to another directory",
                     iface.granted_id_for(other_dir) is None)

    # More capability must NOT reuse the grant.
    more_cap = HostCommandRequest(
        command="rm report.txt", adapter="shell", use_shell=False,
        actor="autonomous", cwd="/home/alice/workspace",
        capabilities_required=["fs.delete", "net.admin"],
    )
    all_ok &= _check("grant does NOT extend to more capability",
                     iface.granted_id_for(more_cap) is None)

    # A narrower capability re-submission (legitimate retry) is still allowed.
    narrow_cap = HostCommandRequest(
        command="rm report.txt", adapter="shell", use_shell=False,
        actor="autonomous", cwd="/home/alice/workspace",
        capabilities_required=["fs.delete"],
    )
    all_ok &= _check("narrower / identical-capability resubmission is allowed",
                     iface.granted_id_for(narrow_cap) == ar.approval_id)

    # A different actor must NOT reuse the grant.
    other_actor = HostCommandRequest(
        command="rm report.txt", adapter="shell", use_shell=False,
        actor="human-operator", cwd="/home/alice/workspace",
        capabilities_required=["fs.delete"],
    )
    all_ok &= _check("grant does not transfer to another actor",
                     iface.granted_id_for(other_actor) is None)

    # One-shot usage must be enumerable: after consume, the grant is gone.
    consumed = iface.consume(ar.approval_id)
    all_ok &= _check("grant is one-shot (consume invalidates it)", consumed)
    all_ok &= _check("consumed grant cannot be reused (usage count enumerable)",
                     iface.granted_id_for(base) is None)

    return all_ok


def part_b_sovereignty_and_policy() -> None:
    """DISCLOSED: enumeration APIs exist, but target binding is not yet enforced."""
    _hr("Part B -- sovereignty grant & policy approval (DISCLOSED / UNVERIFIED)")
    print("  Finding: authorization surfaces bind ACTION NAMES only; none carries a")
    print("  `target` field, so 'authorized target == executed target' is NOT yet")
    print("  enforced for the sovereignty grant and the policy approval request.")
    print("  The enumeration/audit trail exists and is usable; the target binding")
    print("  is owner-pending (鹿补之 + 王权衡). Listed, not silently green:")
    print()

    # Sovereignty: enumeration APIs present + no target field.
    from src.kernels._sovereignty import (
        list_grants, get_grant, grant_window, issue_grant,
    )
    from src.kernels._sovereignty import SovereigntyGrant
    api_ok = all(callable(f) for f in (list_grants, get_grant, grant_window, issue_grant))
    _check("sovereignty enumeration APIs present (list_grants/get_grant/grant_window/issue_grant)",
           api_ok)
    has_target = "target" in SovereigntyGrant.__dataclass_fields__
    if not has_target:
        _gap("SovereigntyGrant carries a `target` field (binds executed target)",
             "NO target field -> one grant authorizes ANY target; audit records no target")

    # Policy approval request: introspect if importable.
    try:
        from src.gateway.policy import ApprovalRequest
        pol_has_target = "target" in ApprovalRequest.model_fields
        if not pol_has_target:
            _gap("policy ApprovalRequest carries a `target` field",
                 "NO target field -> policy grant does not bind a target")
    except Exception as exc:  # pragma: no cover - env without fastapi etc.
        _check("policy ApprovalRequest importable for introspection",
               False, f"not introspected in this env: {exc!r}")


def main() -> int:
    _hr("GATE: authorization grants enumerable + auditable; scope binds execution")
    a_ok = part_a_host_command()
    part_b_sovereignty_and_policy()
    _hr("Summary")
    if a_ok:
        print("  Part A (decided mechanism): HARD PASS -- authorized target == executed")
        print("  target is enforced on the live host-command path; usage is enumerable.")
        print("  Part B (owner-pending): DISCLOSED UNVERIFIED -- sovereignty grant and")
        print("  policy approval have no `target` binding yet; decision is 鹿补之 + 王权衡.")
        print("  GATE: PASS (decided invariants enforced; pending gap disclosed).")
        return 0
    print("  Part A FAILED -- a decided authorization invariant is broken.")
    print("  GATE: FAIL.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
