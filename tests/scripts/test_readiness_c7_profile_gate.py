"""C7 (performance) numeric gate: the classification must be honest AND precise.

Measured fact this module pins down: ``scripts/bench_baseline.json`` holds
exactly one profile, ``Windows|py3.13.14|...|quick``. This repo's venv is
py3.12.14, so running the bench here prints ``PERFORMANCE GATE: SKIP`` with
``current profile: 'Windows|py3.12.14|...'`` -- the numeric gate does not run
on the Windows host either, not only on Linux CI as the old reason text
implied.

Two things are asserted:

1. A SKIP is never scored as a pass (the honesty direction).
2. The reason text names the profile actually seen and does not claim the
   degradation is Linux-only (the precision direction -- a misattributed
   limitation is how a gap gets filed under the wrong owner and never fixed).

Also covered: the ``LIUHAO_BENCH_PROFILE`` override, which forces the profile
key and can therefore make a numeric comparison across hardware that was never
baselined. That stays a PASS -- turning it red would be a false red for teams
that legitimately pin a self-hosted runner -- but the reason must say so.
"""

from __future__ import annotations

import importlib.util
import pathlib

import pytest

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
_SCRIPT = _REPO_ROOT / "scripts" / "verify_readiness.py"


def _load():
    assert _SCRIPT.is_file(), "missing %s" % _SCRIPT
    spec = importlib.util.spec_from_file_location("verify_readiness_under_test", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


MODULE = _load()
PASS = MODULE.PASS
NOT_VERIFIED = MODULE.NOT_VERIFIED


#: Verbatim shape printed by scripts/bench_audit_append.py --quick --gate.
_SKIP_OUT = (
    "=== PERFORMANCE GATE: SKIP ===\n"
    "  - profile mismatch -- re-baseline required. current profile: "
    "'Windows|py3.12.14|Intel64_Family_6_Model_63_Stepping_2,_GenuineIntel|t24|quick'; "
    "available: Windows|py3.13.14|Intel64_Family_6_Model_63_Stepping_2,_GenuineIntel|t24|quick\n"
    "  (numeric gate skipped; invariants remain the blocking check)\n"
)
_PASS_OUT = "=== PERFORMANCE GATE: PASS ===\n"


def test_skip_is_never_a_pass() -> None:
    status, reason = MODULE._c7_verdict(_SKIP_OUT, None)
    assert status == NOT_VERIFIED, (status, reason)


def test_skip_reason_names_the_profile_actually_seen() -> None:
    """A generic 'mismatch' reason cannot be acted on; name the profile."""
    _, reason = MODULE._c7_verdict(_SKIP_OUT, None)
    assert "Windows|py3.12.14|" in reason, reason


def test_skip_reason_does_not_blame_linux_only() -> None:
    """The old text said 'profile mismatch / CI Linux'.

    That misattributes the gap: the baseline is py3.13.14 and this repo runs
    py3.12.14, so the Windows host skips too. A limitation filed under the
    wrong platform is a limitation nobody fixes.
    """
    _, reason = MODULE._c7_verdict(_SKIP_OUT, None)
    assert "CI Linux" not in reason
    assert "Not Linux-specific" in reason, reason


def test_skip_reason_says_invariants_still_ran() -> None:
    """Do not overstate: the invariants ARE checked; only throughput is not."""
    _, reason = MODULE._c7_verdict(_SKIP_OUT, None)
    assert "Invariants were still checked" in reason, reason


def test_pass_without_override_is_a_clean_pass() -> None:
    status, reason = MODULE._c7_verdict(_PASS_OUT, None)
    assert status == PASS, (status, reason)
    assert "own profile" in reason


def test_pass_with_override_is_flagged_as_hardware_unproven() -> None:
    """An override forces the key; the PASS must say so rather than look clean."""
    status, reason = MODULE._c7_verdict(_PASS_OUT, "pinned-runner-1")
    assert status == PASS, "pinning a runner is legitimate; do not fake a red"
    assert "pinned-runner-1" in reason
    assert "ONLY if" in reason, reason


def test_skip_without_a_reported_profile_is_still_unverified() -> None:
    """Guard the parsing: a SKIP we cannot parse must not become a pass."""
    status, _ = MODULE._c7_verdict("=== PERFORMANCE GATE: SKIP ===\n", None)
    assert status == NOT_VERIFIED


@pytest.mark.parametrize("out", ["", "some unexpected crash\n", "GATE: PASS\n"])
def test_anything_unrecognised_is_unverified(out: str) -> None:
    """No branch may invent a pass."""
    assert MODULE._c7_verdict(out, None)[0] == NOT_VERIFIED


def test_profile_extraction_helper() -> None:
    assert MODULE._c7_profile_from(_SKIP_OUT) == (
        "Windows|py3.12.14|Intel64_Family_6_Model_63_Stepping_2,_GenuineIntel|t24|quick"
    )
    assert MODULE._c7_profile_from("no profile here") is None


def test_baseline_really_has_only_the_py313_profile() -> None:
    """Pin the premise. If a Linux/py3.12 profile is ever captured, this fails
    and the reason text above must be updated rather than left to rot."""
    import json

    path = _REPO_ROOT / "scripts" / "bench_baseline.json"
    assert path.is_file(), path
    doc = json.loads(path.read_text(encoding="utf-8"))
    profiles = sorted(doc.get("profiles", {}))
    assert profiles == [
        "Windows|py3.13.14|Intel64_Family_6_Model_63_Stepping_2,_GenuineIntel|t24|quick"
    ], profiles
