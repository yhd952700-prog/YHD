"""诊断包导出闸：脱敏残留必须在写 zip 之前被处理。

单独成文件而不是塞进 ``test_desktop_system_layer.py``：这条闸是安全边界，
不依赖窗口/托盘，跑起来应当是毫秒级，失败时也更容易定位到 ``diagnostics``。

语义（与网关导出闸一致）：命中的成员**剔除**，其余成员照常打包，包内补
``REDACTED-MEMBERS.txt``；只有"一个成员都留不下"才整体拒绝。
"""

from __future__ import annotations

import json
import logging
import zipfile

import pytest

from src.common.leak_scan import LOG_MEMBER_ALIAS_NOTE, SCAN_RULE_HIT_NAME
from src.desktop import diagnostics


def test_pre_write_gate_strips_half_redacted_member():
    """``Bearer sk-***REDACTED***abc123`` 不能被当成"已脱敏"。"""
    texts = {
        "logs/liuhao.log.txt": "Authorization: Bearer sk-***REDACTED***abc123\r\n",
        "logs/ok.log.txt": "plain line\r\n",
    }

    omitted = diagnostics._strip_plaintext_members(texts)

    assert [item["name"] for item in omitted] == ["logs/liuhao.log.txt"]
    assert omitted[0]["total"] >= 1
    assert "logs/liuhao.log.txt" not in texts
    assert "logs/ok.log.txt" in texts


def test_pre_write_gate_allows_fully_redacted_member():
    """完整占位符与 ``key=configured`` 这类状态词不能误报。"""
    texts = {
        "logs/liuhao.log.txt": (
            "Authorization: Bearer sk-***REDACTED***\r\n"
            "GET /v1/health?key=***REDACTED*** HTTP/1.1\r\n"
            "Provider initialized: key=configured\r\n"
        )
    }

    assert diagnostics._strip_plaintext_members(texts) == []
    assert "logs/liuhao.log.txt" in texts


def test_create_bundle_downgrades_and_lists_omitted_members(tmp_path, monkeypatch):
    """剔除后包真的生成：命中文件不在包里，清单与 README 说明准确。"""
    fake_root = tmp_path / "repo"
    fake_root.mkdir()
    fake_logs = tmp_path / "logs"
    fake_logs.mkdir()
    (fake_logs / "app.log").write_text(
        "Authorization: Bearer sk-***REDACTED***abc123\n", encoding="utf-8"
    )
    (fake_logs / "ok.log").write_text("plain line\n", encoding="utf-8")
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setattr(diagnostics, "discover_service", lambda *args, **kwargs: None)
    monkeypatch.setenv("LIUHAO_DATA_DIR", str(data_dir))

    omitted: list[dict] = []
    bundle_path = diagnostics.create_bundle(
        tmp_path / "out",
        root=fake_root,
        log_dir=fake_logs,
        ports=[65530],
        probe_network=False,
        omitted_out=omitted,
    )

    assert bundle_path.is_file()
    assert diagnostics.verify_bundle(bundle_path) == []
    assert [item["name"] for item in omitted] == ["logs/app.log.txt"]

    with zipfile.ZipFile(bundle_path) as archive:
        names = archive.namelist()
        assert "logs/app.log.txt" not in names
        assert "logs/ok.log.txt" in names
        assert "REDACTED-MEMBERS.txt" in names

        readme = archive.read("README.txt").decode("utf-8")
        assert "为保护你的密钥，1 个日志文件未包含" in readme
        assert "REDACTED-MEMBERS.txt" in readme
        # 防止被当成"有病毒/文件损坏"：README 首页要有一句话解释它是什么。
        assert "不是病毒或损坏文件" in readme
        # 支持人员点名要 error.log 时，包内 README 必须写清改名映射。
        assert LOG_MEMBER_ALIAS_NOTE in readme
        # 完整性自证：N 等于包内文件数，N + M 就是计划收集总数。
        assert f"本次包含 {len(names)} 个文件，未包含 1 个" in readme

        # 规则命中分布：只有规则名 / 文件 / 次数，不含任何命中内容。
        assert SCAN_RULE_HIT_NAME in names
        report = json.loads(archive.read(SCAN_RULE_HIT_NAME).decode("utf-8"))
        assert report["total_omitted"] == 1
        assert report["hits"] == [
            {"file": "logs/app.log.txt", "rules": {"脱敏残留": 1}, "total": 1}
        ]
        assert "abc123" not in archive.read(SCAN_RULE_HIT_NAME).decode("utf-8")

        redacted = archive.read("REDACTED-MEMBERS.txt").decode("utf-8")
        assert "logs/app.log.txt：命中 1 处（脱敏残留=1）" in redacted
        # 清单只写文件名与条数，不回显任何命中内容。
        assert "abc123" not in redacted


def test_renamed_log_backup_is_not_recollected_or_omitted(tmp_path, monkeypatch):
    """把日志改名成 ``.log.bak-*`` 后再导出：包里没有它，清单里也不再出现。"""
    fake_root = tmp_path / "repo"
    fake_root.mkdir()
    fake_logs = tmp_path / "logs"
    fake_logs.mkdir()
    leaky = fake_logs / "error.log"
    leaky.write_text(
        "Authorization: Bearer sk-***REDACTED***abc123\n", encoding="utf-8"
    )
    (fake_logs / "ok.log").write_text("plain line\n", encoding="utf-8")
    (fake_logs / "ok.log.1").write_text("rotated line\n", encoding="utf-8")
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setattr(diagnostics, "discover_service", lambda *args, **kwargs: None)
    monkeypatch.setenv("LIUHAO_DATA_DIR", str(data_dir))

    omitted: list[dict] = []
    first = diagnostics.create_bundle(
        tmp_path / "out1",
        root=fake_root,
        log_dir=fake_logs,
        ports=[65530],
        probe_network=False,
        omitted_out=omitted,
    )
    assert [item["name"] for item in omitted] == ["logs/issue.log.txt"]
    with zipfile.ZipFile(first) as archive:
        names = archive.namelist()
        assert "logs/issue.log.txt" not in names
        assert "REDACTED-MEMBERS.txt" in names
        # 成员名不带容易吓人的词（error.log 在包内叫 issue.log）。
        assert not any(
            word in name.lower()
            for name in names
            for word in ("error", "blocked", "failed")
        )
    assert diagnostics.verify_bundle(first) == []

    leaky.rename(leaky.with_name("error.log.bak-20261108"))

    second = diagnostics.create_bundle(
        tmp_path / "out2",
        root=fake_root,
        log_dir=fake_logs,
        ports=[65530],
        probe_network=False,
    )
    with zipfile.ZipFile(second) as archive:
        names = archive.namelist()
        assert "logs/issue.log.txt" not in names
        assert "REDACTED-MEMBERS.txt" not in names
        assert not any(".bak-" in name for name in names)
        # 桌面路径本来就收轮转文件；它不含密钥，照常保留。
        assert "logs/ok.log.txt" in names
        assert "logs/ok.log.1.txt" in names
        # 改名后没有剔除，完整性自证应写 M=0。
        readme = archive.read("README.txt").decode("utf-8")
        assert f"本次包含 {len(names)} 个文件，未包含 0 个" in readme
    # 备份文件还在磁盘上，证据没被删除。
    assert leaky.with_name("error.log.bak-20261108").is_file()


def test_create_bundle_refuses_only_when_every_member_is_contaminated(
    tmp_path, monkeypatch, caplog
):
    """一个成员都留不下时才整体拒绝，并且文案给出可执行的下一步。"""
    fake_root = tmp_path / "repo"
    fake_root.mkdir()
    fake_logs = tmp_path / "logs"
    fake_logs.mkdir()
    monkeypatch.setattr(diagnostics, "discover_service", lambda *args, **kwargs: None)
    monkeypatch.setenv("LIUHAO_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(diagnostics, "scan_text", lambda _text: {"脱敏残留": 1})
    caplog.set_level(logging.ERROR, logger="liuhao.audit")

    with pytest.raises(RuntimeError) as excinfo:
        diagnostics.create_bundle(
            tmp_path / "out",
            root=fake_root,
            log_dir=fake_logs,
            ports=[65530],
            probe_network=False,
        )

    message = str(excinfo.value)
    assert "没有可安全转发的文件" in message
    assert "下一步" in message
    assert "请重试" not in message
    # 整体拒绝时也写内部审计事件：只记成员名、规则名与条数，不记内容。
    assert "evt=diagnostic.bundle.blocked source=desktop" in caplog.text
    assert "rules=脱敏残留=" in caplog.text


def test_verify_bundle_flags_half_redacted_member(tmp_path):
    """生成后的二次自检也要能发现"只遮了一半"的成员。"""
    target = tmp_path / "bundle.zip"
    with zipfile.ZipFile(target, "w") as archive:
        archive.writestr("logs/liuhao.log.txt", "Bearer sk-***REDACTED***abc123\n")

    violations = diagnostics.verify_bundle(target)
    assert any("明文凭据" in item for item in violations), violations
