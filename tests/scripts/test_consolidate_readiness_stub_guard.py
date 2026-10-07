"""A hand-written ``{"status": "PASS"}`` must not be able to mint a RELEASE READY.

``eap/05-final-readiness-battery/consolidate_readiness.py`` is the aggregate
trust gate: it is the thing that finally says ``RELEASE READY``. Its own
``classify_check`` already degrades missing / empty / unrecognised evidence to
UNVERIFIED, so absent evidence cannot become a pass.

The residual gap this module closes is the opposite direction: a *present*
document whose entire content is a scalar ``PASS``. That is exactly the shape a
CI wrapper produces if it swallows a gate's non-zero exit and writes a
placeholder, and it is the shape anyone can hand-edit in thirty seconds. A
scalar verdict has to prove WHO produced it and HOW it exited before it is
allowed to mint trust.

These tests are the falsification for that claim -- without them, "we refuse
stub evidence" would be an assertion nobody can check.
"""

from __future__ import annotations

import importlib.util
import pathlib

import pytest

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
_SCRIPT = (
    _REPO_ROOT / "eap" / "05-final-readiness-battery" / "consolidate_readiness.py"
)


def _load():
    """Import the battery script by path (its directory is not an identifier)."""
    assert _SCRIPT.is_file(), "missing %s" % _SCRIPT
    spec = importlib.util.spec_from_file_location(
        "consolidate_readiness_under_test", _SCRIPT
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


MODULE = _load()
PASS = MODULE.PASS
FAIL = MODULE.FAIL
UNVERIFIED = MODULE.UNVERIFIED


def _classify(data):
    return MODULE.classify_check(data, "")[0]


def test_bare_pass_stub_is_not_trusted() -> None:
    """The exact stub shape the gap is about: nothing but a scalar PASS."""
    status, reason, details = MODULE.classify_check({"status": "PASS"}, "")
    assert status == UNVERIFIED, (status, reason)
    assert "provenance" in reason or "provenance" in "\n".join(details)


@pytest.mark.parametrize(
    "key",
    ["status", "verdict", "result", "overall", "summary_status"],
)
def test_every_scalar_verdict_key_requires_provenance(key: str) -> None:
    """No scalar alias may be a back door around the attestation."""
    assert _classify({key: "PASS"}) == UNVERIFIED
    assert _classify({key: "pass"}) == UNVERIFIED


def test_attested_pass_is_still_trusted() -> None:
    """A real gate's own report (script named, exited 0) must not be blocked."""
    status, reason, _ = MODULE.classify_check(
        {
            "status": "PASS",
            "generated_by": "scripts/verify_image_build.py",
            "exit_code": 0,
        },
        "",
    )
    assert status == PASS, (status, reason)


@pytest.mark.parametrize(
    "document",
    [
        {"status": "PASS", "generated_by": "scripts/verify_x.py", "exit_code": 1},
        {"status": "PASS", "generated_by": "scripts/verify_x.py"},
        {"status": "PASS", "exit_code": 0},
        {"status": "PASS", "generated_by": "a shell pipeline", "exit_code": 0},
        {"status": "PASS", "generated_by": "verify_x.py", "exit_code": "0"},
    ],
)
def test_incomplete_attestation_is_not_trusted(document: dict) -> None:
    """Naming a producer is not enough; the exit status must be there and 0."""
    assert _classify(document) == UNVERIFIED, document


def test_non_pass_scalars_are_unchanged() -> None:
    """Guard against a false red: the rule must only bite on PASS.

    A document reporting FAIL is already withholding trust; refusing it on
    metadata grounds would turn an honest failure into an argument about
    field names.
    """
    assert _classify({"status": "FAIL"}) == FAIL
    assert _classify({"status": "NOT_VERIFIED"}) == UNVERIFIED
    assert _classify({"status": "WHATEVER"}) == UNVERIFIED


def test_missing_and_unrecognised_evidence_still_degrade() -> None:
    """Regression: the pre-existing honest behaviour must survive."""
    assert MODULE.classify_check(None, "no evidence file was supplied")[0] == UNVERIFIED
    assert _classify({}) == UNVERIFIED
    assert _classify({"totally": "unknown shape"}) == UNVERIFIED


def test_real_check_list_shape_is_unaaffected() -> None:
    """No false red on the shape the real gates actually emit.

    verify_image_build.py / verify_deploy_readiness.py / verify_observability_
    loop.py all write a *list* of named checks (or ``links``), which is
    aggregated item by item and never touches the scalar path. That path must
    keep working exactly as before.
    """
    assert _classify({"checks": [{"name": "boot", "status": "PASS"}]}) == PASS
    assert _classify(
        {"links": [{"link": "L1", "status": "PASS"}, {"link": "L2", "status": "FAIL"}]}
    ) == FAIL


def test_real_repo_evidence_files_are_not_newly_rejected() -> None:
    """No false red on the evidence files that actually exist in this repo.

    If any shipped evidence document regressed from PASS to UNVERIFIED, this
    module would be manufacturing a red in order to prevent a green -- which is
    the same dishonesty in the other direction.
    """
    candidates = sorted(_REPO_ROOT.glob("scripts/*readiness*.json"))
    candidates += sorted(_REPO_ROOT.glob("scripts/*evidence*.json"))
    assert candidates, "no evidence documents found; the fixture set has moved"
    for path in candidates:
        data, reason = MODULE.load_evidence(path)
        assert data is not None, (path, reason)
        status = MODULE.classify_check(data, reason)[0]
        assert status != FAIL, (
            "%s classified as FAIL purely from its file contents -- the stub "
            "guard must never convert a shipped document into a failure" % path
        )
