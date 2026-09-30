"""Tests for the U51 external anchor key ceremony tool + gate.

Proves the ceremony mints + pins an out-of-band external key, emits a
ceremony-attested anchored log, and the gate consumes the pinned key
(public-key-only) fail-closed. Mirrors the gate's own proof at the unit level
and adds negative controls (tampered anchored log, missing ceremony record).
"""
from __future__ import annotations

import base64
import json
import pathlib
import subprocess
import sys

import pytest

REPO = pathlib.Path(__file__).resolve().parents[1]
SCRIPTS = REPO / "scripts"
CEREMONY = SCRIPTS / "ceremony_anchor_key.py"
GATE = SCRIPTS / "verify_u51_external_anchor_ceremony.py"


def _run(args, cwd=REPO):
    return subprocess.run(
        [sys.executable, *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
    )


@pytest.fixture
def ceremony_dir(tmp_path):
    out = tmp_path / "external_anchor"
    res = _run([str(CEREMONY), "--out-dir", str(out)])
    assert res.returncode == 0, res.stdout + res.stderr
    return out


def test_ceremony_produces_artifacts(ceremony_dir):
    assert (ceremony_dir / "ceremony_record.json").is_file()
    assert (ceremony_dir / "anchor_key.pem").is_file()
    assert (ceremony_dir / "anchored_log.jsonl").is_file()


def test_ceremony_record_self_signature_verifies(ceremony_dir):
    sys.path.insert(0, str(REPO))
    from src.kernels.audit.dual_signature import (
        DEFAULT_SIGN_ALG,
        SIGN_ALGORITHMS,
        canonical_bytes,
    )

    record = json.loads(
        (ceremony_dir / "ceremony_record.json").read_text(encoding="utf-8")
    )
    claim = {
        k: record[k]
        for k in (
            "ceremony_id",
            "kind",
            "sig_alg",
            "key_id",
            "public_pem",
            "ceremony_at",
            "notes",
        )
    }
    sig = base64.b64decode(record["ceremony_claim_signature"])
    pub = record["public_pem"].encode("ascii")
    assert SIGN_ALGORITHMS[DEFAULT_SIGN_ALG].verify(pub, canonical_bytes(claim), sig)


def test_gate_passes_on_ceremony_artifacts(ceremony_dir):
    res = _run([str(GATE), "--dir", str(ceremony_dir)])
    assert res.returncode == 0, res.stdout + res.stderr
    assert "U51 CEREMONY OK" in res.stdout


def test_gate_fails_on_missing_ceremony_record(tmp_path):
    empty = tmp_path / "external_anchor"
    empty.mkdir()
    res = _run([str(GATE), "--dir", str(empty)])
    assert res.returncode != 0
    assert "CEREMONY GATE FAIL" in res.stdout + res.stderr


def test_gate_fails_on_tampered_anchored_log(ceremony_dir, tmp_path):
    # Copy artifacts to a writable dir, tamper the anchored log, run the gate.
    tamper = tmp_path / "tamper"
    tamper.mkdir()
    for name in ("ceremony_record.json", "anchored_log.jsonl"):
        (tamper / name).write_bytes((ceremony_dir / name).read_bytes())
    lines = (tamper / "anchored_log.jsonl").read_text(encoding="utf-8").splitlines()
    obj = json.loads(lines[0])
    obj["root"] = "f" * 64 if obj.get("root") != "f" * 64 else "e" * 64
    lines[0] = json.dumps(obj)
    (tamper / "anchored_log.jsonl").write_text(
        "\n".join(lines) + "\n", encoding="utf-8"
    )
    res = _run([str(GATE), "--dir", str(tamper)])
    assert res.returncode != 0
    assert "CEREMONY GATE FAIL" in res.stdout + res.stderr
