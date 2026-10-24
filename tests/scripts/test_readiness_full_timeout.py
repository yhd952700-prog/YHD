"""The full-suite gate must outlive a healthy pytest -- and a killed pytest
must never read as a pass.

P0 (2026-10-24): ``_execute_full_suite`` capped the subprocess at 1800s while
the measured full-suite runtime is ~2678s (44m38s, 6423 tests on the reference
Windows host). The gate timed out first, the JUnit document was never written,
and a healthy run could only surface as ERROR. These tests pin:

1. the subprocess cap is comfortably above the measured runtime,
2. ``LIUHAO_READINESS_FULL_TIMEOUT_S`` overrides it for slow environments
   (and a bad override falls back to the default),
3. a gate-killed run (rc=124, no JUnit, no summary) is ERROR -- never PASS,
4. a printed pytest completion summary still counts as a verdict even under
   rc=124, so real failures stay NOT VERIFIED rather than becoming ERROR,
5. the release-readiness workflow job timeout covers the subprocess cap plus
   install/collection overhead, so the runner cannot preempt the gate's own
   honest classification,
6. the etcd service pins an image that actually contains a shell: docker's
   ``--health-cmd`` is always executed CMD-SHELL (``/bin/sh -c``), and the
   previous ``quay.io/coreos/etcd:v3.5.34`` image is scratch (three binaries,
   no shell), so every probe died before it started (CI run 40118666865).
"""

from __future__ import annotations

import importlib.util
import pathlib
import re

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
_SCRIPT = _REPO_ROOT / "scripts" / "verify_readiness.py"
_WORKFLOWS = _REPO_ROOT / ".github" / "workflows"

#: Measured full-suite runtime on the reference host, 2026-10-24
#: (6423 tests in 44m38s). The cap must stay meaningfully above this.
_MEASURED_SUITE_S = 2678


def _load():
    assert _SCRIPT.is_file(), "missing %s" % _SCRIPT
    spec = importlib.util.spec_from_file_location(
        "verify_readiness_under_test", _SCRIPT
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


MODULE = _load()
ERROR = MODULE.ERROR
NOT_VERIFIED = MODULE.NOT_VERIFIED


# --------------------------------------------------------------------------- #
# timeout sizing
# --------------------------------------------------------------------------- #
def test_full_suite_timeout_covers_measured_runtime() -> None:
    """The cap must be >= ~2x the measured 2678s suite runtime."""
    assert MODULE._FULL_SUITE_TIMEOUT_S >= 2 * _MEASURED_SUITE_S


def test_execute_full_suite_passes_the_cap_to_run(monkeypatch) -> None:
    """Regression: the 1800s literal must not come back."""
    captured = {}

    def fake_run(cmd, *a, **k):
        captured["timeout"] = k.get("timeout")
        return 0, "1 passed\n"

    monkeypatch.delenv("LIUHAO_READINESS_FULL_TIMEOUT_S", raising=False)
    monkeypatch.setattr(MODULE, "_run", fake_run)
    MODULE._execute_full_suite()
    assert captured["timeout"] == MODULE._FULL_SUITE_TIMEOUT_S


def test_full_suite_timeout_env_override(monkeypatch) -> None:
    captured = {}

    def fake_run(cmd, *a, **k):
        captured["timeout"] = k.get("timeout")
        return 0, ""

    monkeypatch.setattr(MODULE, "_run", fake_run)
    monkeypatch.setenv("LIUHAO_READINESS_FULL_TIMEOUT_S", "7200")
    MODULE._execute_full_suite()
    assert captured["timeout"] == 7200


def test_full_suite_timeout_bad_override_falls_back(monkeypatch) -> None:
    monkeypatch.setenv("LIUHAO_READINESS_FULL_TIMEOUT_S", "not-a-number")
    assert MODULE._full_suite_timeout() == MODULE._FULL_SUITE_TIMEOUT_S
    monkeypatch.setenv("LIUHAO_READINESS_FULL_TIMEOUT_S", "-30")
    assert MODULE._full_suite_timeout() == MODULE._FULL_SUITE_TIMEOUT_S


# --------------------------------------------------------------------------- #
# timeout classification honesty
# --------------------------------------------------------------------------- #
def test_gate_killed_run_is_error_never_pass() -> None:
    """rc=124 (subprocess timeout), no JUnit, no summary -> did not finish."""
    status, evidence = MODULE._verdict_from_run(
        124, "TIMEOUT", None, subject="full suite", pass_detail="full run",
    )
    assert status == ERROR, (status, evidence)
    assert "did not complete" in evidence


def test_timeout_rc_with_failures_is_still_a_verdict() -> None:
    """A printed completion summary is a verdict even under rc=124: real
    failures stay NOT VERIFIED, not a verification-process ERROR."""
    status, _ = MODULE._verdict_from_run(
        124, "TIMEOUT\n3 failed, 100 passed in 90.0s\n", None,
        subject="full suite", pass_detail="full run",
    )
    assert status == NOT_VERIFIED


# --------------------------------------------------------------------------- #
# workflow compatibility
# --------------------------------------------------------------------------- #
def _readiness_job_block(text: str) -> str:
    """The ``readiness:`` job block (everything before the next job key)."""
    return text.split("independent-verification:")[0]


def test_workflow_job_timeout_covers_suite_cap_plus_overhead() -> None:
    """The GH job timeout must exceed the subprocess cap by enough room for
    checkout + pip install + collection + the other 11 checks, so the runner
    never kills what the gate would have classified honestly."""
    text = (_WORKFLOWS / "release-readiness.yml").read_text(encoding="utf-8")
    m = re.search(r"timeout-minutes:\s*(\d+)", _readiness_job_block(text))
    assert m, "readiness job must declare timeout-minutes"
    job_s = int(m.group(1)) * 60
    assert job_s >= MODULE._FULL_SUITE_TIMEOUT_S + 15 * 60, (
        f"job timeout {job_s}s must exceed suite cap "
        f"{MODULE._FULL_SUITE_TIMEOUT_S}s plus >=15min overhead"
    )


# --------------------------------------------------------------------------- #
# etcd service image must contain a shell (P0 root-cause pin)
# --------------------------------------------------------------------------- #
def test_etcd_service_uses_a_shell_bearing_image() -> None:
    """quay.io/coreos/etcd:v3.5.34 is scratch (3 binaries, no /bin/sh) while
    docker --health-cmd is always CMD-SHELL -> probes can never execute.
    Pin the shell-bearing replacement so a silent revert fails here.

    The check parses the actual ``image:`` lines: the dead image is still
    legitimately *mentioned* in comments that document the root cause."""
    image_line = re.compile(r"(?m)^\s*image:\s*[\"']?([^\s\"'#]+)")
    for name in ("ci.yml", "release-readiness.yml"):
        text = (_WORKFLOWS / name).read_text(encoding="utf-8")
        images = image_line.findall(text)
        assert images, f"{name}: no image references found"
        assert not any("quay.io/coreos/etcd" in img for img in images), (
            f"{name}: {images}"
        )
        assert any("bitnamilegacy/etcd:3.5" in img for img in images), (
            f"{name}: {images}"
        )
        assert "ALLOW_NONE_AUTHENTICATION" in text, name
        assert "/opt/bitnami/etcd/bin/etcdctl" in text, name
