"""Tests for the machine-normalized performance gate in bench_audit_append.

These prove two things without running the (slow, subprocess-heavy) benchmark:

1. A profile mismatch makes the numeric gate SKIP -- it must NOT fail CI and
   must NOT pretend to pass. It should ask for a re-baseline instead.
2. The machine-independent invariants still block on a violation, on every
   profile.

The benchmark module is loaded directly from its file path so we don't depend
on `scripts/` being an importable package.
"""
import importlib.util
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

_SPEC = importlib.util.spec_from_file_location(
    "bench_audit_append_under_test",
    ROOT / "scripts" / "bench_audit_append.py",
)
bench = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(bench)


def _fake_run(eps, lost_appends=False, intact_after_kill=True,
              contiguous_seq=True, chain_verifies=True):
    """A minimal `run()`-shaped result for exercising the gate logic."""
    return {
        "single_append": {"events_per_sec": eps, "latency_ms_p95": 1.0},
        "batched_append": [
            {"batch_size": 10, "events_per_sec": eps * 5},
            {"batch_size": 100, "events_per_sec": eps * 7},
        ],
        "multi_process": {
            "aggregate_events_per_sec_excluding_startup": eps * 10,
            "aggregate_events_per_sec_including_startup": eps * 9,
            "no_lost_appends": not lost_appends,
            "contiguous_seq": contiguous_seq,
            "rows_committed": 100,
            "rows_expected": 100,
        },
        "recovery": {
            "recovery_seconds": 0.5,
            "chain_intact_after_kill": intact_after_kill,
        },
        "chain_verifies": chain_verifies,
    }


PROFILE_A = "Linux|py3.12|Intel|t4|quick"
PROFILE_B = "Windows|py3.13|AMD|t16|quick"


def test_profile_mismatch_skips_numeric_gate(tmp_path):
    baseline = tmp_path / "bench_baseline.json"
    # Baseline captured on PROFILE_A only.
    doc = {
        "schema": bench.BASELINE_SCHEMA,
        "default_profile": PROFILE_A,
        "profiles": {PROFILE_A: _fake_run(1000)},
    }
    baseline.write_text(json.dumps(doc), encoding="utf-8")

    status, failures, label, msg = bench.decide_gate(
        str(baseline), PROFILE_B, _fake_run(1), 0.25)

    assert status == "SKIP", "profile mismatch must SKIP, not block"
    assert failures == [], "SKIP must not report failures"
    assert "re-baseline" in msg.lower()


def test_profile_match_blocks_on_real_regression(tmp_path):
    baseline = tmp_path / "bench_baseline.json"
    doc = {
        "schema": bench.BASELINE_SCHEMA,
        "default_profile": PROFILE_A,
        "profiles": {PROFILE_A: _fake_run(1000)},
    }
    baseline.write_text(json.dumps(doc), encoding="utf-8")

    # Current run is ~10x slower => a real regression on a matching profile.
    status, failures, label, msg = bench.decide_gate(
        str(baseline), PROFILE_A, _fake_run(100), 0.25)

    assert status == "GATE"
    assert failures, "matching profile with a regression must produce failures"


def test_invariants_block_on_lost_appends():
    current = _fake_run(1000, lost_appends=True)
    assert bench.check_invariants(current), "must report a failure"
    assert any("LOST" in f for f in bench.check_invariants(current))


def test_invariants_block_on_chain_break():
    current = _fake_run(1000, intact_after_kill=False)
    assert any("intact" in f for f in bench.check_invariants(current))


def test_invariants_pass_when_clean():
    assert bench.check_invariants(_fake_run(1000)) == []


def test_detect_profile_override_via_env(monkeypatch):
    monkeypatch.setenv("LIUHAO_BENCH_PROFILE", "pinned-profile")
    assert bench.detect_profile(quick=True) == "pinned-profile"


def test_detect_profile_includes_quick_flag(monkeypatch):
    monkeypatch.delenv("LIUHAO_BENCH_PROFILE", raising=False)
    full = bench.detect_profile(quick=False)
    quick = bench.detect_profile(quick=True)
    assert full.endswith("|full")
    assert quick.endswith("|quick")
    assert full.rsplit("|", 1)[0] == quick.rsplit("|", 1)[0]


def test_legacy_flat_baseline_skips(tmp_path):
    # The retired flat format has no "profiles" key -> must SKIP, not compare.
    baseline = tmp_path / "legacy.json"
    baseline.write_text(json.dumps(_fake_run(1000)), encoding="utf-8")
    status, failures, label, msg = bench.decide_gate(
        str(baseline), PROFILE_A, _fake_run(1), 0.25)
    assert status == "SKIP"
    assert failures == []
