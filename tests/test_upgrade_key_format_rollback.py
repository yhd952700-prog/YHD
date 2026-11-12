"""回滚后"0 个可用账号"的防回归测试（身份签名密钥格式）。

覆盖的真实故障链：

    0.1.1 数据（明文密钥，N 条已签身份）
      → 0.1.2 启动，``ensure_integrity_key`` 把密钥迁移为包裹格式
      → 回滚到 0.1.1（备份**不含** integrity.key / human_identities.json）
      → 0.1.1 只 read_text()，把包裹文本当密钥 → N 条全部被拒绝 → 0 个账号

这些用例就是 CI 上"升级 → 回滚 → 仍能登录"的验证口径：
``rows_visible_legacy`` 必须等于 ``rows_signed``，且回滚编排必须在启动旧版
**之前**完成降级、在宣告 ``rolled_back`` 之前复核账号可见性。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List

import pytest

from src.common.integrity_key import KEY_FILE_REL, REGISTRY_FILE_REL, ensure_integrity_key
from src.kernels.identity._persistence import compute_row_tag
from src.upgrade import orchestrator
from src.upgrade.keyformat import (
    KEY_FORMAT_PLAINTEXT,
    KEY_FORMAT_WRAPPED,
    KeyFormatError,
    assess_key_visibility,
    check_startup_key_format,
    detect_key_format,
    downgrade_key_for_legacy,
    key_format_needs_downgrade,
    read_key_format_marker,
    required_min_build,
    stamp_key_format,
)
from src.upgrade.startup_guard import check_startup_data

PLAINTEXT_KEY = "a" * 64


def _rows(key: str, count: int = 3) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for index in range(count):
        row: Dict[str, Any] = {
            "principal": f"user{index}",
            "display_name": f"User {index}",
            "permissions": ["chat"],
            "scope": "L1",
            "trust_score": 0.5,
            "registered_at": "2026-01-01T00:00:00Z",
        }
        row["integrity"] = compute_row_tag(row, key)
        rows.append(row)
    return rows


def _make_legacy_data_dir(root: Path, count: int = 3, key: str = PLAINTEXT_KEY) -> Path:
    data_dir = root / "data"
    config = data_dir / "config"
    config.mkdir(parents=True)
    (data_dir / KEY_FILE_REL).write_text(key + "\n", encoding="utf-8")
    payload = {"humans": _rows(key, count)}
    (data_dir / REGISTRY_FILE_REL).write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return data_dir


@pytest.fixture()
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "LIUHAO_HUMAN_IDENTITIES_INTEGRITY_KEY",
        "LIUHAO_HUMAN_IDENTITIES_FILE",
        "LIUHAO_HUMAN_IDENTITIES_BACKEND",
        "LIUHAO_HUMAN_IDENTITIES_DB",
        "LIUHAO_DATA_DIR",
    ):
        monkeypatch.delenv(name, raising=False)


def test_legacy_data_dir_is_fully_visible_before_upgrade(tmp_path: Path, clean_env) -> None:
    data_dir = _make_legacy_data_dir(tmp_path)
    report = assess_key_visibility(data_dir)
    assert report["key_format"] == KEY_FORMAT_PLAINTEXT
    assert report["rows_signed"] == 3
    assert report["rows_visible_legacy"] == 3
    assert report["legacy_would_lock_out"] is False


def test_upgrade_migration_hides_every_row_from_legacy_semantics(
    tmp_path: Path, clean_env
) -> None:
    data_dir = _make_legacy_data_dir(tmp_path)
    before = assess_key_visibility(data_dir)
    assert before["rows_visible_legacy"] == 3

    # 这就是 0.1.2 启动器调用的同一个入口：原地迁移密钥格式。
    ensure_integrity_key(data_dir=data_dir, announce=False)

    after = assess_key_visibility(data_dir)
    assert detect_key_format(data_dir) == KEY_FORMAT_WRAPPED
    # 新版仍能看到全部账号——数据没坏。
    assert after["rows_visible_wrapped"] == 3
    # 旧版把包裹文本当密钥 -> 全部失败，这正是"被锁在门外"。
    assert after["rows_visible_legacy"] == 0
    assert after["legacy_would_lock_out"] is True


def test_downgrade_restores_legacy_login(tmp_path: Path, clean_env) -> None:
    data_dir = _make_legacy_data_dir(tmp_path)
    ensure_integrity_key(data_dir=data_dir, announce=False)
    assert assess_key_visibility(data_dir)["legacy_would_lock_out"] is True

    result = downgrade_key_for_legacy(data_dir, app_version="0.1.1")

    assert result["changed"] is True
    assert detect_key_format(data_dir) == KEY_FORMAT_PLAINTEXT
    restored = assess_key_visibility(data_dir)
    assert restored["rows_visible_legacy"] == 3
    assert restored["legacy_would_lock_out"] is False
    # 明文只是旧版能读，新版同样能读（解包状态为 legacy）。
    assert restored["rows_visible_wrapped"] == 3


def test_downgrade_is_noop_on_plaintext(tmp_path: Path, clean_env) -> None:
    data_dir = _make_legacy_data_dir(tmp_path)
    result = downgrade_key_for_legacy(data_dir)
    assert result["changed"] is False
    assert detect_key_format(data_dir) == KEY_FORMAT_PLAINTEXT


def test_downgrade_refuses_when_key_cannot_be_unwrapped(
    tmp_path: Path, clean_env, monkeypatch: pytest.MonkeyPatch
) -> None:
    data_dir = _make_legacy_data_dir(tmp_path)
    # 冒充"换机后解不开"的密文：带版本头，但本机没有对应明文。
    (data_dir / KEY_FILE_REL).write_text("LIUHAO-DPAPI-1:AAAA\n", encoding="utf-8")
    monkeypatch.setattr(
        "src.upgrade.keyformat.read_key_file_status",
        lambda _path: ("", "undecryptable"),
    )
    with pytest.raises(KeyFormatError) as excinfo:
        downgrade_key_for_legacy(data_dir)
    assert "解不开" in str(excinfo.value)


def test_needs_downgrade_only_for_pre_012_targets(tmp_path: Path, clean_env) -> None:
    data_dir = _make_legacy_data_dir(tmp_path)
    ensure_integrity_key(data_dir=data_dir, announce=False)
    report = assess_key_visibility(data_dir)
    assert key_format_needs_downgrade(report, "0.1.1") is True
    assert key_format_needs_downgrade(report, "0.1.2") is False
    assert key_format_needs_downgrade(report, "0.2.0") is False
    # 版本未知时保守降级。
    assert key_format_needs_downgrade(report, "") is True


def test_marker_records_minimum_readable_version(tmp_path: Path, clean_env) -> None:
    data_dir = _make_legacy_data_dir(tmp_path)
    stamp_key_format(data_dir, "0.1.2")
    assert read_key_format_marker(data_dir)["integrity_key"] == KEY_FORMAT_PLAINTEXT
    assert required_min_build(data_dir) is None

    ensure_integrity_key(data_dir=data_dir, announce=False)
    stamp_key_format(data_dir, "0.1.2")
    marker = read_key_format_marker(data_dir)
    assert marker["integrity_key"] == KEY_FORMAT_WRAPPED
    assert marker["min_version"] == "0.1.2"
    assert required_min_build(data_dir) == "0.1.2"


def test_startup_guard_refuses_old_build_instead_of_silent_lockout(
    tmp_path: Path, clean_env
) -> None:
    data_dir = _make_legacy_data_dir(tmp_path)
    ensure_integrity_key(data_dir=data_dir, announce=False)

    message = check_startup_key_format(data_dir, "0.1.1")
    assert message is not None
    assert "0 个可用账号" in message
    assert "key-compat --downgrade" in message

    # 新版本照常放行。
    assert check_startup_key_format(data_dir, "0.1.2") is None

    # 启动门禁用"本二进制版本"判断；把本二进制伪装成 0.1.1 才能验证拒绝分支。
    from src.upgrade import startup_guard

    monkeypatch = pytest.MonkeyPatch()
    try:
        monkeypatch.setattr(startup_guard, "APP_VERSION", "0.1.1")
        gate = check_startup_data(data_dir)
    finally:
        monkeypatch.undo()
    assert gate.ok is False
    assert gate.action == "key_format_blocked"
    assert "0 个可用账号" in gate.message


# ---------------------------------------------------------------------------
# 编排器：回滚序列必须"先降级、再启动、后复核"
# ---------------------------------------------------------------------------


class _FakeProc:
    def __init__(self) -> None:
        self.pid = 4321
        self.terminated = False

    def poll(self) -> int:
        return 0 if self.terminated else None

    def terminate(self) -> None:
        self.terminated = True

    def wait(self, timeout: float | None = None) -> int:
        return 0

    def kill(self) -> None:
        self.terminated = True


class _FakeManager:
    def __init__(self, version: str) -> None:
        self._version = version

    def rollback(self, backup_id: str | None = None) -> None:
        return None

    def current_version(self):
        return self._version, None


def _run_restore(
    monkeypatch: pytest.MonkeyPatch, data_dir: Path, version: str, tmp_path: Path
) -> Dict[str, Any]:
    journal_dir = tmp_path / "journal-data"
    journal_dir.mkdir(parents=True, exist_ok=True)
    journal = orchestrator.Journal(journal_dir)
    proc = _FakeProc()
    started: List[Dict[str, Any]] = []

    def fake_start(**_: Any) -> Dict[str, Any]:
        started.append({"called": True})
        return {"proc": proc, "health": {"ok": True}}

    monkeypatch.setattr(orchestrator, "_start_and_check", fake_start)
    result = orchestrator._restore_old_version(
        manager=_FakeManager(version),
        journal=journal,
        backup_id="bk-1",
        app_root=tmp_path,
        data_dir=data_dir,
        python="python",
        port=1234,
        health_timeout=1.0,
        reason="new build broken",
    )
    result["_started"] = started
    result["_journal"] = journal.data
    return result


def test_orchestrator_downgrades_key_before_starting_old_version(
    tmp_path: Path, clean_env, monkeypatch: pytest.MonkeyPatch
) -> None:
    data_dir = _make_legacy_data_dir(tmp_path)
    ensure_integrity_key(data_dir=data_dir, announce=False)
    assert detect_key_format(data_dir) == KEY_FORMAT_WRAPPED

    result = _run_restore(monkeypatch, data_dir, "0.1.1", tmp_path)

    assert result["phase"] == "rolled_back"
    assert result["_started"], "旧版本应该被启动"
    # 关键：启动旧版之前密钥已降级，回滚后账号可见。
    assert detect_key_format(data_dir) == KEY_FORMAT_PLAINTEXT
    assert result["_journal"]["data_compat"]["rows_visible_legacy"] == 3


def test_orchestrator_never_starts_old_version_when_downgrade_fails(
    tmp_path: Path, clean_env, monkeypatch: pytest.MonkeyPatch
) -> None:
    data_dir = _make_legacy_data_dir(tmp_path)
    ensure_integrity_key(data_dir=data_dir, announce=False)

    def boom(*_: Any, **__: Any) -> Dict[str, Any]:
        raise KeyFormatError("模拟降级失败")

    monkeypatch.setattr(orchestrator, "downgrade_key_for_legacy", boom)
    result = _run_restore(monkeypatch, data_dir, "0.1.1", tmp_path)

    assert result["phase"] == "manual_intervention"
    assert result["detail"]["failed_stage"] == "key_downgrade"
    assert result["_started"] == [], "降级失败时不得把旧版本拉起来"
    # 原密钥没被破坏。
    assert detect_key_format(data_dir) == KEY_FORMAT_WRAPPED


def test_orchestrator_does_not_report_rolled_back_when_zero_accounts(
    tmp_path: Path, clean_env, monkeypatch: pytest.MonkeyPatch
) -> None:
    data_dir = _make_legacy_data_dir(tmp_path)
    ensure_integrity_key(data_dir=data_dir, announce=False)

    monkeypatch.setattr(
        orchestrator,
        "_verify_account_visibility",
        lambda _data_dir: {
            "rows_signed": 3,
            "rows_visible_legacy": 0,
            "legacy_would_lock_out": True,
        },
    )
    result = _run_restore(monkeypatch, data_dir, "0.1.1", tmp_path)

    assert result["phase"] == "manual_intervention"
    assert result["detail"]["failed_stage"] == "login_visibility"
    assert "0 个能登录" in result["reason"]
