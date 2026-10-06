"""HD-06 retention vs deletion-obligation conflict -- the honest-disclosure guard.

WHY THIS TEST EXISTS
--------------------
The retention subsystem enforces a hard invariant: an ``IMMUTABLE_ORIGINAL``
(among them the audit/evidence chain) is NEVER deleted, overwritten, or
destroyed -- not by any policy, not even ``DELETE_AFTER_DAYS``. That is a sound
evidence-preservation guarantee. But it has a legal mirror image that the code
alone does not surface:

    A data-subject erasure request (GDPR Art. 17 / CCPA §1798.105 / PIPL Art. 47)
    CANNOT be honored by deletion for any original that is personal data.

This is the U52 / U36 / U37 conflict. It is a HUMAN DECISION (HD-06) which the
team must NOT auto-resolve. What the team CAN and MUST do is keep the disclosure
honest and two-sided: the "never delete" invariant is BOTH an evidence-
preservation guarantee AND an erasure limitation, and the subsystem must not be
readable as "we delete your data on request."

This module is the executable form of that honest-disclosure obligation. It ties
the WARNING to the BEHAVIOUR so documentation and reality cannot drift apart in
either direction (the same discipline as
``test_retention_legal_hold_volatility.py``). It does NOT implement any erasure
policy -- that would be overstepping the HUMAN DECISION boundary.

WHAT IS ASSERTED
----------------
1. The hard guard is real (originals survive ``DELETE_AFTER_DAYS``) -- so the
   erasure limitation is not a hypothetical; it is enforced behaviour.
2. The erasure limitation is disclosed in BOTH the lifecycle and manager
   docstrings -- and the disclosure names the governing HUMAN DECISION (HD-06 /
   U36), so a reader is not left to infer policy from code.
3. There is NO silent erasure surface on ``RetentionManager`` -- no
   ``erase`` / ``forget`` / ``delete_subject`` / ``request_erasure`` method that
   could imply erasure is honored. If such a method is ever added, this test
   fails and forces the conscious decision (and the honest caveat) it requires.
4. No outward product doc makes an uncaveated "erasure implemented / supported"
   claim (mirrors the DATA-SUBJECT-RIGHTS lint rule over the real surfaces).
"""
from __future__ import annotations

import inspect
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
RETENTION_PKG = REPO_ROOT / "src" / "kernels" / "retention"
LIFECYCLE = RETENTION_PKG / "lifecycle.py"
MANAGER = RETENTION_PKG / "manager.py"

if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# Affirmative "erasure implemented / supported" phrases (mirrors the
# DATA-SUBJECT-RIGHTS rule in scripts/lint_audit_claims.py claims list).
DATA_SUBJECT_RIGHTS_AFFIRMATIVE = [
    re.compile(r"data[- ]?subject\s+rights?\s+(?:are\s+)?(?:\w+\s+){0,3}"
               r"(supported|implemented|available|enforced)", re.IGNORECASE),
    re.compile(r"(数据主体)?(访问|更正|删除|被遗忘)权(?:已|已经)?(实现|支持|提供)"),
]
# Tokens that, if present near an affirmative claim, make it an honest caveat
# rather than a false assurance.
ERASURE_CAVEAT_TOKENS = [
    "HUMAN DECISION", "HUMAN-DECISION", "HD-06", "U36", "U37", "U52",
    "Frozen", "TBD", "冻结", "待定", "未实现", "缺失", "缺少",
    "no data-subject", "no framework", "NOT implemented", "not yet",
]


def _module_doc(path: Path) -> str:
    text = path.read_text(encoding="utf-8")
    # First triple-quoted string in the file is the module docstring.
    return text.split('"""', 2)[1]


# --------------------------------------------------------------------------- #
# 1. The hard guard is real (so the erasure limitation is enforced behaviour).
# --------------------------------------------------------------------------- #

def test_immutable_original_survives_delete_after_days_policy() -> None:
    """The no-delete invariant is not advisory -- the manager enforces it.

    Even under DELETE_AFTER_DAYS with an original far past its horizon, the
    manager refuses deletion and rewrites the decision to "keep". This is the
    behaviour that makes erasure-by-deletion structurally impossible for
    originals, which is precisely why the erasure limitation must be disclosed.
    """
    from src.kernels.retention.config import RetentionConfig, RetentionMode
    from src.kernels.retention.lifecycle import LifecycleState
    from src.kernels.retention.manager import RetentionManager
    from src.kernels.retention.policy import ConfigurableRetentionPolicy

    mgr = RetentionManager(
        policy=ConfigurableRetentionPolicy(),
        config=RetentionConfig(
            mode=RetentionMode.DELETE_AFTER_DAYS, delete_after_days=1
        ),
    )
    mgr.register_original("o1", b"audit evidence row")
    old_meta = mgr.get("o1")
    assert old_meta is not None
    # Force it past any horizon.
    old_meta.created_at = old_meta.created_at - 10 * 86400.0
    decision = mgr.evaluate("o1")

    assert mgr.get("o1") is not None, "original must not be deleted"
    assert decision.action != "delete_original"
    assert decision.action == "keep", (
        "policy asked to delete an original; the hard guard must rewrite the "
        "decision to 'keep', not silently delete"
    )
    assert mgr.get("o1").state in (
        LifecycleState.ACTIVE, LifecycleState.ARCHIVED, LifecycleState.HELD,
    )


# --------------------------------------------------------------------------- #
# 2. The erasure limitation is disclosed -- and names the HUMAN DECISION.
# --------------------------------------------------------------------------- #

def test_erasure_limitation_disclosed_in_lifecycle_and_manager() -> None:
    """Two-direction drift guard between behaviour and disclosure.

    - Originals undeletable + limitation disclosed  -> correct, passes.
    - Originals undeletable + limitation removed   -> FAILS (undisclosed limit).
    - Originals deletable + limitation present      -> FAILS (stale warning).

    The disclosure must live in BOTH the lifecycle (the RecordClass linchpin) and
    the manager (the orchestrator a reader meets first). A whole-file "erasure"
    substring match is not enough -- that would pass on a stray comment and is
    exactly the hollow disclosure this guard exists to prevent.
    """
    from src.kernels.retention.lifecycle import RecordClass

    lifecycle_doc = _module_doc(LIFECYCLE)
    manager_doc = _module_doc(MANAGER)
    class_doc = inspect.getdoc(RecordClass) or ""

    erasure_token = re.compile(r"erasure|right to be forgotten|data-subject|"
                               r"GDPR Art\. 17|CCPA|PIPL", re.IGNORECASE)

    lifecycle_discloses = bool(erasure_token.search(lifecycle_doc + class_doc))
    manager_discloses = bool(erasure_token.search(manager_doc))

    assert lifecycle_discloses, (
        "src/kernels/retention/lifecycle.py must disclose that the no-delete "
        "invariant is also an erasure limitation (RecordClass docstring)"
    )
    assert manager_discloses, (
        "src/kernels/retention/manager.py must disclose that the no-delete "
        "invariant is also an erasure limitation (module docstring)"
    )


def test_erasure_disclosure_names_the_human_decision() -> None:
    """The disclosure must not read as a decision the team has made.

    The erasure / retention final policy is a HUMAN DECISION (HD-06). The docstring
    must reference it (HD-06 / U36 / U37 / U52 or 'HUMAN DECISION') so a reader
    knows the reconciliation is reserved to the owner, not auto-decided.
    """
    combined = (_module_doc(LIFECYCLE) + _module_doc(MANAGER)).lower()
    decision_markers = ["hd-06", "u36", "u37", "u52", "human decision"]
    assert any(m in combined for m in decision_markers), (
        "the erasure-limitation disclosure must name the governing HUMAN "
        "DECISION (HD-06 / U36 / U37 / U52) so it is not mistaken for a "
        "team-decided policy"
    )


# --------------------------------------------------------------------------- #
# 3. No silent erasure surface.
# --------------------------------------------------------------------------- #

def test_no_silent_erasure_method_on_retention_manager() -> None:
    """RetentionManager must not expose an erasure-looking public method.

    If a method named like ``erase`` / ``forget`` / ``delete_subject`` /
    ``request_erasure`` / ``right_to_erasure`` is ever added, it implies erasure
    is honored -- which is false for originals today (U36: no data-subject-rights
    mechanism). The test fails, forcing the author to either implement it
    correctly (with crypto-shred / exemption) or mark it explicitly, and to add
    the honest caveat. Absence of such a method is the honest state.
    """
    from src.kernels.retention.manager import RetentionManager

    erasure_lookalikes = re.compile(
        r"erase|forget|delete_subject|request_erasure|right_to_erasure|"
        r"purge_subject|remove_subject_data|wipe_subject",
        re.IGNORECASE,
    )
    public_members = [
        name for name in dir(RetentionManager) if not name.startswith("_")
    ]
    offending = [m for m in public_members if erasure_lookalikes.search(m)]
    assert not offending, (
        "RetentionManager exposes an erasure-looking public member %s -- this "
        "implies data-subject erasure is honored, which is false for originals "
        "(U36: no data-subject-rights mechanism). Implement it honestly or mark "
        "it NOT_IMPLEMENTED, and add the HD-06 caveat." % offending
    )


# --------------------------------------------------------------------------- #
# 4. No outward doc makes an uncaveated erasure-implemented claim.
# --------------------------------------------------------------------------- #

def test_no_uncaveated_erasure_claim_in_outward_docs() -> None:
    """Mirror the DATA-SUBJECT-RIGHTS lint rule over the real outward surfaces.

    An outward doc may NOT assert that data-subject erasure / rights are
    implemented or supported WITHOUT a HUMAN DECISION / Frozen / TBD / 未实现 /
    missing caveat. This keeps the external legal assurance honest (U36 / U37).
    """
    surfaces = [
        REPO_ROOT / "README.md",
        REPO_ROOT / "PRODUCT-READINESS.md",
        REPO_ROOT / "MASTER_PROJECT_READINESS.md",
        REPO_ROOT / "docs" / "autonomous" / "UNKNOWN-TO-OWNER.md",
        REPO_ROOT / "docs" / "autonomous" / "HUMAN-DECISION-HD06-U37.md",
    ]
    # Add any markdown under docs/product if present.
    product_dir = REPO_ROOT / "docs" / "product"
    if product_dir.is_dir():
        surfaces.extend(product_dir.glob("*.md"))

    for surface in surfaces:
        if not surface.is_file():
            continue
        lines = surface.read_text(encoding="utf-8").splitlines()
        for idx, line in enumerate(lines):
            for pat in DATA_SUBJECT_RIGHTS_AFFIRMATIVE:
                if pat.search(line):
                    # Look at this line + the next for a caveat token.
                    window = " ".join(lines[idx:idx + 2]).lower()
                    has_caveat = any(t.lower() in window
                                     for t in ERASURE_CAVEAT_TOKENS)
                    assert has_caveat, (
                        "%s:%d asserts a data-subject erasure/rights claim "
                        "without a HUMAN DECISION / Frozen / 未实现 caveat -- "
                        "this is a false external legal assurance (U36/U37): %r"
                        % (surface.name, idx + 1, line.strip())
                    )
