"""日志保留设置的可见/可改层：持久化、30 天与 500 MiB 等价验证、三种极端。

这里不真等 24 小时：用可控的 ``now`` 与伪造 mtime 把时间压缩，用稀疏文件把
体积放大到 500 MiB，验证的仍然是 ``prune_log_retention`` 的真实分支。
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from src import logging_config
from src.logging_config import (
    DEFAULT_RETENTION_BYTES,
    DEFAULT_RETENTION_DAYS,
    log_usage,
    prune_log_retention,
    read_retention_report,
    retention_limits,
    save_retention_limits,
)

MIB = 1024 * 1024
#: 固定「现在」，避免测试受真实时间影响。
NOW = 1_800_000_000.0


def _touch(path: Path, size: int, age_days: float) -> None:
    """创建指定逻辑大小的稀疏文件，并把 mtime 拨到 ``age_days`` 天前。"""
    with open(path, "wb") as handle:
        handle.truncate(size)
    stamp = NOW - age_days * 86400
    os.utime(path, (stamp, stamp))


@pytest.fixture
def settings_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    target = tmp_path / "settings" / "log_retention_settings.json"
    monkeypatch.setenv("LIUHAO_LOG_RETENTION_SETTINGS_FILE", str(target))
    monkeypatch.delenv("LIUHAO_LOG_RETENTION_DAYS", raising=False)
    monkeypatch.delenv("LIUHAO_LOG_RETENTION_BYTES", raising=False)
    return target


def test_saved_limits_persist_and_survive_restart(settings_file: Path) -> None:
    """保存后重新读（等价于重启后再读）仍是新值，且写入的是可读 JSON。"""
    save_retention_limits(7, 100 * MIB)
    days, cap = retention_limits()
    assert (days, cap) == (7, 100 * MIB)

    payload = json.loads(settings_file.read_text(encoding="utf-8"))
    assert payload["retention_days"] == 7
    assert payload["max_total_bytes"] == 100 * MIB
    assert payload["updated_at"]


def test_env_still_wins_over_saved_limits(
    settings_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    save_retention_limits(7, 100 * MIB)
    monkeypatch.setenv("LIUHAO_LOG_RETENTION_DAYS", "3")
    monkeypatch.setenv("LIUHAO_LOG_RETENTION_BYTES", str(64 * MIB))
    assert retention_limits() == (3, 64 * MIB)
    assert logging_config.retention_env_overrides() == {
        "retention_days": True,
        "max_total_bytes": True,
    }


def test_broken_settings_file_falls_back_to_defaults(settings_file: Path) -> None:
    settings_file.parent.mkdir(parents=True, exist_ok=True)
    settings_file.write_text("{ not json", encoding="utf-8")
    assert retention_limits() == (DEFAULT_RETENTION_DAYS, DEFAULT_RETENTION_BYTES)


def test_30_day_equivalent_prune(tmp_path: Path) -> None:
    """伪造 40/20/5 天前的备份：只删超过 14 天的，活动日志与 5 天前的保留。"""
    logs = tmp_path / "logs"
    logs.mkdir()
    active = logs / "desktop-gateway.log"
    _touch(active, 2 * MIB, 0)
    _touch(logs / "desktop-gateway.log.1", 10 * MIB, 40)
    _touch(logs / "desktop-gateway.log.2", 10 * MIB, 20)
    _touch(logs / "desktop-gateway.log.3", 10 * MIB, 5)

    report = prune_log_retention(
        logs, retention_days=14, max_total_bytes=0, now=NOW
    )

    assert sorted(item["name"] for item in report["deleted"]) == [
        "desktop-gateway.log.1",
        "desktop-gateway.log.2",
    ]
    assert active.exists()
    assert (logs / "desktop-gateway.log.3").exists()
    assert report["status"] == "ok"
    assert report["total_bytes_before"] == 32 * MIB
    assert report["total_bytes_after"] == 12 * MIB
    kept = {item["name"]: item for item in report["kept"]}
    assert kept["desktop-gateway.log"]["active"] is True
    assert kept["desktop-gateway.log.3"]["age_days"] == pytest.approx(5, abs=0.02)
    assert read_retention_report(logs) == report


def test_500mib_growth_is_capped_by_total_size(tmp_path: Path) -> None:
    """连续增长到 600 MiB：按最旧优先删到 ≤ 256 MiB，活动日志始终保留。"""
    logs = tmp_path / "logs"
    logs.mkdir()
    active = logs / "desktop-gateway.log"
    _touch(active, 2 * MIB, 0)
    for index in range(6):
        _touch(logs / f"desktop-gateway.log.{index + 1}", 100 * MIB, index + 1)

    report = prune_log_retention(
        logs, retention_days=0, max_total_bytes=256 * MIB, now=NOW
    )

    assert active.exists()
    assert report["total_bytes_before"] == 602 * MIB
    assert report["total_bytes_after"] <= 256 * MIB
    assert report["total_bytes_after"] == 202 * MIB
    assert len(report["deleted"]) == 4
    assert {item["name"] for item in report["deleted"]} == {
        "desktop-gateway.log.6",
        "desktop-gateway.log.5",
        "desktop-gateway.log.4",
        "desktop-gateway.log.3",
    }
    assert report["status"] == "ok"
    assert report["failed"] == []
    assert log_usage(logs)["total_bytes"] == 202 * MIB


def test_non_log_files_are_never_touched(tmp_path: Path) -> None:
    logs = tmp_path / "logs"
    logs.mkdir()
    _touch(logs / "desktop-gateway.log", MIB, 0)
    _touch(logs / "desktop-gateway.log.1", MIB, 90)
    keep_txt = logs / "notes.txt"
    keep_txt.write_text("用户自己的文件", encoding="utf-8")
    keep_bak = logs / "desktop-gateway.log.bak"
    keep_bak.write_bytes(b"not a rotation slot")

    report = prune_log_retention(logs, retention_days=14, max_total_bytes=0, now=NOW)

    assert [item["name"] for item in report["deleted"]] == ["desktop-gateway.log.1"]
    assert keep_txt.exists()
    assert keep_bak.exists()
    assert report["failed"] == []


def test_locked_backup_is_reported_without_crashing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """被占用删不掉：不崩、继续删其它备份，并把可读原因写进报告。"""
    logs = tmp_path / "logs"
    logs.mkdir()
    active = logs / "desktop-gateway.log"
    _touch(active, MIB, 0)
    locked = logs / "desktop-gateway.log.1"
    _touch(locked, 10 * MIB, 40)
    _touch(logs / "desktop-gateway.log.2", 10 * MIB, 40)

    real_unlink = Path.unlink

    def deny(self: Path, *args, **kwargs):  # noqa: ANN001 - 模拟占用
        if self.name == locked.name:
            raise PermissionError(13, "in use")
        return real_unlink(self, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", deny)

    report = prune_log_retention(logs, retention_days=14, max_total_bytes=0, now=NOW)

    assert report["status"] == "部分完成"
    assert [item["name"] for item in report["failed"]] == [locked.name]
    assert "占用" in report["failed"][0]["reason"]
    assert [item["name"] for item in report["deleted"]] == ["desktop-gateway.log.2"]
    assert "没能删除" in report["notice"]
    assert active.exists()
    assert locked.exists()


def test_readonly_directory_reports_notice_without_crashing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """报告写不进去（目录只读）：清理本身不抛异常，返回可读提示。"""
    logs = tmp_path / "logs"
    logs.mkdir()
    _touch(logs / "desktop-gateway.log", MIB, 0)
    _touch(logs / "desktop-gateway.log.1", MIB, 40)

    def deny(*args, **kwargs):  # noqa: ANN001 - 模拟只读目录
        raise PermissionError(13, "read only")

    monkeypatch.setattr(logging_config, "replace_with_retry", deny)

    report = prune_log_retention(logs, retention_days=14, max_total_bytes=0, now=NOW)

    assert report["report_written"] is False
    assert "只读" in report["notice"]
    # 文件本身仍被按策略删掉了；只是报告没能落盘。
    assert [item["name"] for item in report["deleted"]] == ["desktop-gateway.log.1"]
    assert not (logs / "desktop-gateway.log.1").exists()
