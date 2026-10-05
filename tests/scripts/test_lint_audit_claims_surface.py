"""Governance tests for the HC-01 claim guard's NEGATION exclusion + scan surface.

WHY THIS FILE EXISTS
--------------------
Two defects were found by inspection and fixed (法度明, 2026-10):

1. **Scan-surface blind spot.** ``MASTER_PROJECT_READINESS.md`` and
   ``RELEASE-READINESS-CONTRACT.md`` are top-level project-face documents whose
   G10 / release-gate sections assert audit-integrity status outright, yet
   neither appeared in ``OUTWARD_SURFACE_FILES`` nor in the doc-lint workflow's
   path filter. An explicit run against them reported 4 un-caveated claims while
   a CI-mode run reported ``targets: 0 ... PASS`` — the guard was blind to the
   two documents most likely to be read as a readiness claim. They are now
   scanned; ``test_root_markdown_files_are_all_in_the_scan_surface`` stops the
   surface from silently shrinking again.

2. **The guard fired on honest NEGATIVES.** ``HC-01-AUDIT`` had no negation
   exclusion, so the disclosure sentence "no VERIFIED tamper-proof persistent
   audit trail today" (U40) was flagged as an overclaim — pressuring the author
   to delete the very sentence that tells the reader the guarantee is absent.
   An HC-01 ``negate`` list was added, deliberately scoped so that only an
   explicit negation of the claim itself suppresses a finding.

THE POINT OF THESE TESTS
------------------------
A guard that has been loosened must be proven to have kept its teeth. Every
``MUST_STILL_FIRE`` case below is an affirmative overclaim that must still be
caught; every ``MUST_NOT_FIRE`` case is an honest negative or an already-caveated
statement. If someone widens the negation regex to make a legitimate finding
disappear, these fail.
"""
from __future__ import annotations

import importlib.util
import os
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "lint_audit_claims.py"

_spec = importlib.util.spec_from_file_location(
    "lint_audit_claims_surface_under_test", SCRIPT
)
mod = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = mod  # required before exec_module so imports resolve
_spec.loader.exec_module(mod)

HC01 = "HC-01-AUDIT"


def _scan(text: str):
    with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False, encoding="utf-8") as fh:
        fh.write(text)
        path = fh.name
    try:
        return mod.scan_file(path, 2)
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


def _hc01_fires(text: str) -> bool:
    return any(g == HC01 for (_ln, g, _tok, _snip) in _scan(text))


# --------------------------------------------------------------------------- #
# The scan surface must cover every root-level markdown document.
# --------------------------------------------------------------------------- #

def test_root_markdown_files_are_all_in_the_scan_surface() -> None:
    """A root-level *.md that the guard does not name is invisible to CI.

    This is the regression test for the blind spot: the two root readiness
    documents were missing, and the only reason they are now covered is that
    they were added explicitly. Without this test, the next root document
    created would repeat the same silence.
    """
    root_md = {
        p.name
        for p in REPO_ROOT.glob("*.md")
        if p.is_file()
    }
    missing = sorted(root_md - set(mod.OUTWARD_SURFACE_FILES))
    assert not missing, (
        "root-level markdown file(s) not in lint_audit_claims."
        "OUTWARD_SURFACE_FILES: %s — an un-caveated audit claim there would "
        "not fail CI. Add the filename in the same commit that creates the "
        "document, and to .github/workflows/doc-lint.yml paths." % (missing,)
    )


def test_both_root_readiness_documents_are_scanned() -> None:
    """Named explicitly so the intent survives a future surface rewrite."""
    for name in ("MASTER_PROJECT_READINESS.md", "RELEASE-READINESS-CONTRACT.md"):
        assert name in mod.OUTWARD_SURFACE_FILES, name
        assert (REPO_ROOT / name).is_file(), name


def test_workflow_path_filter_covers_every_named_root_surface() -> None:
    """The workflow trigger must fire for the files the guard claims to scan.

    A file named in OUTWARD_SURFACE_FILES but absent from the workflow's
    ``paths:`` filter still never reaches the linter in CI — which is precisely
    how the blind spot survived. This asserts the two lists agree.
    """
    workflow = REPO_ROOT / ".github" / "workflows" / "doc-lint.yml"
    assert workflow.is_file(), workflow
    text = workflow.read_text(encoding="utf-8")
    for name in mod.OUTWARD_SURFACE_FILES:
        assert name in text, (
            "%s is scanned by the linter but is not in doc-lint.yml paths:, "
            "so editing it never triggers the guard" % name
        )


# --------------------------------------------------------------------------- #
# The negation exclusion must NOT have gutted the rule.
# --------------------------------------------------------------------------- #

# --- MUST STILL FIRE: affirmative overclaims (the guard's actual job) ---
def test_affirmative_tamper_proof_still_fires() -> None:
    assert _hc01_fires("The audit trail is tamper-proof and shipped.\n") is True


def test_affirmative_tamper_evident_still_fires() -> None:
    assert _hc01_fires("We provide tamper-evident logging with a hash chain.\n") is True


def test_affirmative_chinese_tamper_claim_still_fires() -> None:
    assert _hc01_fires("本系统已实现防篡改审计。\n") is True


def test_authoritative_evidence_source_still_fires() -> None:
    assert _hc01_fires(
        "Our store is the authoritative evidence source for all audit facts.\n"
    ) is True


def test_real_evidence_source_still_fires() -> None:
    assert _hc01_fires("kernels/audit 是真实证据源。\n") is True


def test_bare_evidence_grade_claim_fires() -> None:
    """Regression for an asymmetry in the rule, not in the negation.

    The rule could excuse "no evidence-grade ..." but had no token for the
    AFFIRMATIVE form, so "our audit store is evidence-grade" -- exactly what
    HC-01 is not, per D19/D20 (telemetry-grade, not evidence-grade) -- passed
    silently. Adding the token changed nothing across the whole scanned
    surface, i.e. every honest use was already caveated.
    """
    for sentence in (
        "Our audit store is evidence-grade and ships today.",
        "This is an audit-grade audit source.",
        "本系统是证据级审计。",
    ):
        assert _hc01_fires(sentence + "\n") is True, sentence


def test_self_hashed_record_must_not_be_called_tamper_evident() -> None:
    """Regression for the falsified ``record_hash`` claim (2026-10).

    A self-hash living inside the record it covers is a corruption tripwire,
    not tamper-evidence. The wording "tamper-evident" applied to it must still
    be caught so the claim cannot creep back in.
    """
    assert _hc01_fires(
        "independent_verification.py emits a tamper-evident record "
        "(record_hash = sha256 of the canonical record).\n"
    ) is True


# --- MUST FIRE: overclaims DISGUISED as negatives ---------------------------
#
# These are the attacks that broke the first version of the HC-01 negation
# exclusion, which allowed up to 60 characters between the negation and the
# claim token. In every case "no / not / without" negates *doubt*, *question*,
# *worry*, or *only* -- NOT the audit claim -- so the sentence is an affirmative
# overclaim wearing a negation. All six slipped through once; they must never
# slip through again.
_ADVERSARIAL = [
    "There is no doubt: our audit store is tamper-proof.",
    "Without question, the audit trail is tamper-evident.",
    "Not only is it fast, the audit is tamper-proof.",
    "No serious auditor questions that we are tamper-proof.",
    "没有理由怀疑：本系统已实现防篡改审计。",
    "There is no need to worry — our evidence-grade audit is authoritative.",
]


def test_overclaims_disguised_as_negatives_still_fire() -> None:
    for sentence in _ADVERSARIAL:
        assert _hc01_fires(sentence + "\n") is True, (
            "an affirmative overclaim escaped by hiding behind a negation that "
            "scopes over the wrong word: %r" % sentence
        )


def test_no_doubt_idiom_is_explicitly_blocklisted() -> None:
    """The idiom blocklist must exist and be applied, not be decorative."""
    hc01_rule = next(r for r in mod.RULES if r["group"] == HC01)
    excluded = hc01_rule.get("negate_exclude")
    assert excluded, "HC-01-AUDIT must carry a negate_exclude idiom blocklist"
    assert any(p.search("There is no doubt: tamper-proof") for p in excluded), (
        "the 'no doubt' idiom must be in the blocklist"
    )
    assert any(p.search("not only") for p in excluded), (
        "the 'not only' idiom must be in the blocklist"
    )


def test_negation_must_be_adjacent_not_distant() -> None:
    """The first version's 60-char gap is what let the attacks through.

    A negation 40 characters away from the claim must NOT suppress it; that
    distance is the specific hole, so it is asserted directly rather than
    trusting the regex shape.
    """
    distant = (
        "We have no reason to doubt anything at all here: the audit store "
        "is tamper-proof."
    )
    assert _hc01_fires(distant + "\n") is True


# --- MUST NOT FIRE: honest negatives and already-caveated statements ---
def test_honest_negative_no_verified_tamper_proof_does_not_fire() -> None:
    """The U40 sentence this whole red line exists to produce."""
    assert _hc01_fires(
        "no VERIFIED tamper-proof persistent audit trail today; all internal "
        "audit is telemetry-grade, not evidence-grade.\n"
    ) is False


def test_honest_negative_not_tamper_evident_does_not_fire() -> None:
    assert _hc01_fires(
        "The system is not tamper-evident; it is telemetry-grade.\n"
    ) is False


def test_honest_negative_chinese_does_not_fire() -> None:
    assert _hc01_fires("本系统不具备防篡改能力。\n") is False


def test_honest_negative_no_evidence_grade_does_not_fire() -> None:
    assert _hc01_fires("There is no evidence-grade audit source here.\n") is False


def test_existing_unverified_caveat_still_suppresses() -> None:
    """The pre-existing caveat path must keep working (no regression)."""
    assert _hc01_fires(
        "tamper-evident audit logging — runtime integrity currently UNVERIFIED, "
        "forked chain, pending F1-F6.\n"
    ) is False


def test_volatile_caveat_still_suppresses() -> None:
    assert _hc01_fires(
        "CryptoAuditLogger is in-memory only, VOLATILE / NON-AUTHORITATIVE.\n"
    ) is False


# --------------------------------------------------------------------------- #
# The repo must currently be clean under the widened surface.
# --------------------------------------------------------------------------- #

def test_full_repo_surface_has_no_uncaveated_claims() -> None:
    """``--all`` over the whole surface must be green.

    This is the same invocation CI performs on the changed subset, run over
    everything, so a claim already committed cannot sit unnoticed until the
    next time somebody happens to edit that file.
    """
    class _Args:
        paths = []
        all = True
        base_dir = None

    targets = mod.collect_targets(_Args())
    assert len(targets) >= 100, (
        "scan surface collapsed to %d file(s) — the guard is about to become "
        "a no-op" % len(targets)
    )
    violations = []
    for path in targets:
        rel = os.path.relpath(path, REPO_ROOT).replace(os.sep, "/")
        for (ln, grp, tok, snip) in mod.scan_file(path, 1):
            violations.append("%s:%d [%s] %s" % (rel, ln, grp, tok))
    assert not violations, "un-caveated claim(s):\n  " + "\n  ".join(violations)
