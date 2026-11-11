"""自升级模块单元测试。

覆盖：目录硬边界、版本行规范化、盖章版本断言派生、清单校验/篡改检测、
哈希链、连续失败锁死与解锁、策略拒绝清单。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.selfupgrade import paths
from src.selfupgrade.manifest import (
    canonical_json,
    normalize_content,
    relaxed_version_test,
    sha256_bytes,
    verify_manifest,
)
from src.selfupgrade.policy import PolicyError, load_policy
from src.selfupgrade.state import StateStore


def _write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def _make_lock(root: Path, *, isc_content: bytes | None = None) -> Path:
    lock = root / "release-lock"
    isc = isc_content or b'#define AppVersion "@VERSION@"\nDefaultDirName={code:GetDefaultDir}\n'
    _write(lock / "installer" / "liuhao.iss", isc)
    spec = {
        "schema": "liuhao.release-lock.files/1",
        "files": [
            {
                "target": "installer/liuhao.iss",
                "source": "installer/liuhao.iss",
                "mode": "normalized",
            }
        ],
    }
    _write(lock / "FILES.json", json.dumps(spec, ensure_ascii=False).encode("utf-8"))

    normalized = normalize_content("installer/liuhao.iss", "normalized", isc)
    manifest = {
        "schema": "liuhao.release-lock/1",
        "seq": 1,
        "source_commit": "0" * 40,
        "stamped_at": "2026-11-11T00:00:00+00:00",
        "git_author": "test",
        "entries": [
            {
                "target": "installer/liuhao.iss",
                "source": "installer/liuhao.iss",
                "mode": "normalized",
                "sha256": sha256_bytes(normalized),
                "size": len(normalized),
            }
        ],
    }
    manifest_bytes = (json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
    _write(lock / "MANIFEST.json", manifest_bytes)
    _write(
        lock / "CURRENT",
        json.dumps({"seq": 1, "manifest_sha256": sha256_bytes(manifest_bytes)}).encode("utf-8"),
    )
    body = {"seq": 1, "prev": "", "manifest_sha256": sha256_bytes(manifest_bytes)}
    body["line_sha256"] = sha256_bytes(canonical_json(body).encode("utf-8"))
    _write(lock / "STAMP-LOG.jsonl", (canonical_json(body) + "\n").encode("utf-8"))
    return lock


def test_install_target_guard_accepts_test_and_rejects_live():
    install, data = paths.assert_safe_test_target(r"D:\LiuHaoTest", r"D:\LiuHaoTestData")
    assert install.name == "LiuHaoTest"
    assert data.name == "LiuHaoTestData"

    with pytest.raises(paths.UnsafeTargetError):
        paths.assert_safe_test_target(r"D:\LiuHao", r"D:\LiuHaoTestData")
    with pytest.raises(paths.UnsafeTargetError):
        paths.assert_safe_test_target(r"D:\LiuHaoTest", r"D:\LiuHaoData")
    with pytest.raises(paths.UnsafeTargetError):
        paths.assert_safe_test_target(r"D:\LiuHaoTest\..\LiuHao", r"D:\LiuHaoTestData")
    with pytest.raises(paths.UnsafeTargetError):
        paths.assert_safe_test_target(r"D:\Temp\Elsewhere", r"D:\LiuHaoTestData")


def test_normalize_version_line_only():
    raw = b'#define AppVersion "0.2.2"\r\nOther=1\r\n'
    out = normalize_content("installer/liuhao.iss", "normalized", raw)
    assert b'"@VERSION@"' in out
    assert b"Other=1" in out
    assert normalize_content("src/version.py", "exact", raw) == raw


def test_relaxed_version_test_removes_literal_and_adds_console_assertion():
    source = 'def test_app_version():\n    assert APP_VERSION == "0.2.2"\n'
    out = relaxed_version_test(source)
    assert 'assert APP_VERSION == "0.2.2"' not in out
    assert "test_console_package_version_matches" in out
    assert 'apps" / "console" / "console" / "package.json' in out


def test_verify_manifest_pass(tmp_path: Path):
    lock = _make_lock(tmp_path)
    result = verify_manifest(tmp_path, lock)
    assert result.ok is True
    assert result.exit_code == 0
    assert result.seq == 1


def test_verify_manifest_reports_expected_actual_and_lines(tmp_path: Path):
    lock = _make_lock(tmp_path)
    tampered = b'#define AppVersion "@VERSION@"\nDefaultDirName=C:\\Users\\Public\\LiuHao\n'
    _write(lock / "installer" / "liuhao.iss", tampered)

    result = verify_manifest(tmp_path, lock)
    assert result.ok is False
    assert result.exit_code == 1
    assert len(result.failures) == 1
    failure = result.failures[0]
    assert failure.target == "installer/liuhao.iss"
    assert failure.expected_sha256 != failure.actual_sha256
    assert failure.diff_unavailable is True  # 测试仓库没有对应提交


def test_verify_manifest_detects_log_edit(tmp_path: Path):
    lock = _make_lock(tmp_path)
    log = lock / "STAMP-LOG.jsonl"
    log.write_text(log.read_text(encoding="utf-8").replace('"seq":1', '"seq":2'), encoding="utf-8")
    result = verify_manifest(tmp_path, lock)
    assert result.ok is False
    assert any("STAMP-LOG" in p for p in result.problems)


def test_verify_manifest_detects_manifest_edit(tmp_path: Path):
    lock = _make_lock(tmp_path)
    current = json.loads((lock / "CURRENT").read_text(encoding="utf-8"))
    current["manifest_sha256"] = "0" * 64
    (lock / "CURRENT").write_text(json.dumps(current), encoding="utf-8")
    result = verify_manifest(tmp_path, lock)
    assert result.ok is False
    assert any("CURRENT.manifest_sha256" in p for p in result.problems)


def test_state_locks_after_three_failures_and_boss_unlock(tmp_path: Path):
    store = StateStore(tmp_path / "broker")
    snap = store.load()
    for index in range(2):
        store.failure(snap, f"fail-{index}")
        assert snap.state == "FAILED"
    store.failure(snap, "fail-3")
    assert snap.state == "LOCKED"
    assert snap.consecutive_failures == 3

    store.unlock(snap, "Boss 手动解锁")
    assert snap.state == "IDLE"
    assert snap.consecutive_failures == 0


def test_state_recovers_from_transient_crash(tmp_path: Path):
    store = StateStore(tmp_path / "broker")
    snap = store.load()
    snap.state = "INSTALL"
    store.save(snap)
    recovered = store.load()
    assert recovered.state == "FAILED"
    assert recovered.consecutive_failures == 1


def test_policy_requires_refusal_list_and_test_targets(tmp_path: Path):
    lock = tmp_path / "release-lock"
    lock.mkdir()
    policy = {
        "max_consecutive_failures": 3,
        "target_install_dir": r"D:\LiuHaoTest",
        "target_data_dir": r"D:\LiuHaoTestData",
        "package_refusal": {"lock_files": ["release-lock/scripts/release/package-frozen.ps1"]},
    }
    (lock / "policy.json").write_text(json.dumps(policy), encoding="utf-8")
    loaded = load_policy(lock)
    assert loaded.max_consecutive_failures == 3

    policy["package_refusal"] = {}
    (lock / "policy.json").write_text(json.dumps(policy), encoding="utf-8")
    with pytest.raises(PolicyError):
        load_policy(lock)

    policy["package_refusal"] = {"lock_files": ["x"]}
    policy["target_install_dir"] = r"D:\LiuHao"
    (lock / "policy.json").write_text(json.dumps(policy), encoding="utf-8")
    with pytest.raises(paths.UnsafeTargetError):
        load_policy(lock)
