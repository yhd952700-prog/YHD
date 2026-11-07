"""启动自检 / 降级登记 / 引导日志的行为（故障 2、5、6 的日志证据）。

这些测试只钉"用户能不能从日志里读出来"，不测业务逻辑：
* 路径必须脱敏，但要保留"哪个库 / 哪个文件"；
* ``evt=startup.check`` 的字段名固定，且不含绝对路径；
* 库打不开时 ``evt=db.open.failed`` 进 ERROR，并进入同一份降级列表；
* 数据目录不可写时 ``bootstrap.log`` 仍有 path/winerror/errno/action。
"""
from __future__ import annotations

import logging

import pytest

from src.observability import boot_report


@pytest.fixture(autouse=True)
def _clean_state():
    boot_report.reset_degraded()
    yield
    boot_report.reset_degraded()


def test_display_path_keeps_relative_tail_and_hides_absolute(tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    db = data_dir / "audit.db"
    db.write_bytes(b"")

    assert boot_report.display_path(db, data_dir) == "data/audit.db"
    outside = boot_report.display_path(tmp_path / "elsewhere" / "x.db", data_dir)
    assert outside == "<本地路径>/x.db"
    assert str(tmp_path) not in outside


def test_key_fingerprint_never_contains_the_key():
    raw = "sk-drill-super-secret-value"
    fingerprint = boot_report.key_fingerprint(raw)
    assert fingerprint.startswith("sha256:")
    assert len(fingerprint) == len("sha256:") + 8
    assert raw[:8] not in fingerprint
    assert boot_report.key_fingerprint("") == "none"


def test_record_db_open_failure_logs_error_and_registers_degraded(caplog):
    with caplog.at_level(logging.ERROR, logger="liuhao.boot"):
        boot_report.record_db_open_failure(
            "audit",
            "D:/LiuHao/data/audit.db",
            RuntimeError("file is not a database"),
            action="隔离损坏库后重启",
            data_dir="D:/LiuHao/data",
        )
    line = caplog.records[-1].getMessage()
    assert "evt=db.open.failed" in line
    assert "subsystem=audit" in line
    assert "path=data/audit.db" in line
    assert "file is not a database" in line

    degraded = boot_report.degraded_components()
    assert degraded == [
        {
            "subsystem": "audit",
            "path": "data/audit.db",
            "sqlite_msg": "RuntimeError: file is not a database",
            "action": "隔离损坏库后重启",
        }
    ]
    # 同一子系统只登记一次（启动过程可能重复尝试打开）。
    boot_report.add_degraded("audit", "data/audit.db", "again", "again")
    assert len(boot_report.degraded_components()) == 1


def test_probe_db_reports_corruption_and_absence(tmp_path):
    corrupt = tmp_path / "audit.db"
    corrupt.write_bytes(b"this is definitely not sqlite")
    ok, message = boot_report.probe_db(corrupt)
    assert ok is False
    assert "not a database" in message.lower()

    assert boot_report.probe_db(tmp_path / "absent.db") == (None, "absent")
    # 探测不得创建空库。
    assert not (tmp_path / "absent.db").exists()


def test_startup_check_emits_one_line_with_all_required_fields(
    tmp_path, monkeypatch, caplog
):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    log_dir = data_dir / "logs"
    log_dir.mkdir()
    monkeypatch.setattr(
        boot_report,
        "provider_summary",
        lambda: {
            "provider": "openai",
            "model": "gpt-4o-mini",
            "key_fp": "sha256:abcd1234",
            "source": "registry",
        },
    )

    with caplog.at_level(logging.INFO, logger="liuhao.boot"):
        payload = boot_report.emit_startup_check(
            data_dir=data_dir,
            log_dir=log_dir,
            db_paths={"audit": str(data_dir / "audit.db")},
            host="127.0.0.1",
            port=18099,
            console_dist=None,
        )

    check_lines = [
        record.getMessage()
        for record in caplog.records
        if "evt=startup.check" in record.getMessage()
    ]
    assert len(check_lines) == 1
    line = check_lines[0]
    for field in (
        "data_dir=",
        "data_writable=",
        "log_dir=",
        "log_writable=",
        "db=[",
        "db_open=",
        "port=18099",
        "port_bindable=",
        "console_dist=",
        "console_index=",
        "provider=openai",
        "model=gpt-4o-mini",
        "key_fp=sha256:abcd1234",
    ):
        assert field in line, (field, line)
    assert str(tmp_path) not in line
    assert payload["data_writable"] is True
    assert payload["log_writable"] is True
    assert boot_report.last_startup_check()["port"] == 18099


def test_unwritable_data_dir_still_leaves_a_bootstrap_record(tmp_path, monkeypatch):
    bootstrap = tmp_path / "bootstrap.log"
    monkeypatch.setenv("LIUHAO_BOOTSTRAP_LOG", str(bootstrap))
    monkeypatch.setattr(
        boot_report,
        "provider_summary",
        lambda: {
            "provider": "mock",
            "model": "mock-model",
            "key_fp": "none",
            "source": "env",
        },
    )

    # 用一个"父路径是文件"的目录制造可移植的不可写场景（Windows/CI 通用）。
    blocker = tmp_path / "blocker"
    blocker.write_text("not a directory", encoding="utf-8")
    unwritable = blocker / "logs"

    payload = boot_report.emit_startup_check(
        data_dir=tmp_path / "data",
        log_dir=unwritable,
        db_paths={},
        host="127.0.0.1",
        port=18098,
        console_dist=None,
    )
    assert payload["log_writable"] is False
    assert any(
        item["subsystem"] == "log_dir" for item in boot_report.degraded_components()
    )

    text = bootstrap.read_text(encoding="utf-8")
    assert "evt=boot.log_dir.unwritable" in text
    assert "winerror=" in text
    assert "errno=" in text
    assert "action=" in text
