"""日志保留：按天 + 按量双上限，报告可被诊断包读取。"""

from __future__ import annotations

import os
import time
from pathlib import Path

from src.logging_config import (
    DEFAULT_RETENTION_DAYS,
    RETENTION_REPORT_NAME,
    prune_log_retention,
    read_retention_report,
    retention_limits,
)


def _make(path: Path, size: int, age_days: float) -> None:
    path.write_bytes(b"x" * size)
    stamp = time.time() - age_days * 86400
    os.utime(path, (stamp, stamp))


def test_prune_deletes_old_backups_but_never_active_log(tmp_path: Path) -> None:
    _make(tmp_path / "liuhao.log", 10, 30)
    _make(tmp_path / "liuhao.log.1", 10, 20)
    _make(tmp_path / "liuhao.log.2", 10, 1)

    report = prune_log_retention(tmp_path, retention_days=14, max_total_bytes=10**9)

    assert (tmp_path / "liuhao.log").exists()
    assert not (tmp_path / "liuhao.log.1").exists()
    assert (tmp_path / "liuhao.log.2").exists()
    assert [item["name"] for item in report["deleted"]] == ["liuhao.log.1"]
    assert "超过保留天数" in report["deleted"][0]["reason"]
    assert {item["name"] for item in report["kept"]} == {"liuhao.log", "liuhao.log.2"}


def test_prune_enforces_total_size_from_oldest_backup(tmp_path: Path) -> None:
    for index, age in ((1, 3), (2, 2), (3, 1)):
        _make(tmp_path / f"error.log.{index}", 100, age)
    _make(tmp_path / "error.log", 100, 0)

    report = prune_log_retention(tmp_path, retention_days=0, max_total_bytes=250)

    assert not (tmp_path / "error.log.1").exists()
    assert not (tmp_path / "error.log.2").exists()
    assert (tmp_path / "error.log.3").exists()
    assert (tmp_path / "error.log").exists()
    assert report["total_bytes_after"] <= 250
    assert len(report["deleted"]) == 2
    assert all("超过总量上限" in item["reason"] for item in report["deleted"])


def test_retention_report_is_written_and_has_no_absolute_path(tmp_path: Path) -> None:
    _make(tmp_path / "desktop-gateway.log.1", 10, 30)

    prune_log_retention(tmp_path, retention_days=7, max_total_bytes=10**9)

    report = read_retention_report(tmp_path)
    assert report is not None
    assert report["retention_days"] == 7
    assert report["deleted"][0]["name"] == "desktop-gateway.log.1"
    text = (tmp_path / RETENTION_REPORT_NAME).read_text(encoding="utf-8")
    assert str(tmp_path) not in text


def test_retention_ignores_non_log_files(tmp_path: Path) -> None:
    _make(tmp_path / "keep.db", 10, 30)
    _make(tmp_path / "liuhao.log.1", 10, 30)

    prune_log_retention(tmp_path, retention_days=1, max_total_bytes=0)

    assert (tmp_path / "keep.db").exists()
    assert not (tmp_path / "liuhao.log.1").exists()


def test_retention_limits_come_from_env(monkeypatch) -> None:
    monkeypatch.setenv("LIUHAO_LOG_RETENTION_DAYS", "3")
    monkeypatch.setenv("LIUHAO_LOG_RETENTION_BYTES", "1024")
    assert retention_limits() == (3, 1024)

    monkeypatch.setenv("LIUHAO_LOG_RETENTION_DAYS", "not-a-number")
    assert retention_limits()[0] == DEFAULT_RETENTION_DAYS


def test_missing_log_dir_is_reported_not_raised(tmp_path: Path) -> None:
    report = prune_log_retention(tmp_path / "missing", retention_days=7, max_total_bytes=1024)

    assert "不存在" in report["status"]
    assert report["deleted"] == []
