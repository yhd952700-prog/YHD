"""失败路径文案：用户看到的是"发生了什么 + 怎么办"，不是英文异常或机器串。

覆盖能稳定复现的入口：

* 端口被占用 / PORT 填错（启动阶段，控制台输出）
* 未预期异常（500）与参数校验（400）的响应文案

数据目录不可写、磁盘满、杀毒拦截属于环境故障，无法在单测里稳定制造；
它们的文案走同一套兜底（``display.py`` 的中性中文），由
``test_diagnostic_bundle.py`` 的就绪检查字段白名单兜住"原始异常不进用户文件"。
"""

from __future__ import annotations

import re

import pytest
from fastapi.testclient import TestClient

from src.gateway.__main__ import _print_bind_failure, resolve_bind
from src.gateway.display import ACCOUNT_HIERARCHY_WORDS, machine_fragments

#: 绝对路径 / 英文异常栈的特征。用户文案里出现任意一个都算不合格。
_FORBIDDEN = (
    re.compile(r"[A-Za-z]:\\"),
    re.compile(r"Traceback \(most recent call last\)"),
    re.compile(r"\b[A-Za-z]+Error\b"),
    re.compile(r"WinError|Errno \d+|ENOSPC|EACCES"),
    re.compile(r"address already in use", re.IGNORECASE),
)


def _assert_user_friendly(text: str) -> None:
    assert text.strip(), "文案不能为空"
    assert not machine_fragments(text), f"出现机器串：{machine_fragments(text)}"
    for word in ACCOUNT_HIERARCHY_WORDS:
        assert word not in text, f"出现层级词：{word}"
    for pattern in _FORBIDDEN:
        assert not pattern.search(text), f"出现英文异常/绝对路径：{pattern.pattern} -> {text}"


class TestPortFailures:
    def test_non_numeric_port_is_chinese_and_actionable(self, monkeypatch):
        monkeypatch.setenv("PORT", "abc")
        with pytest.raises(SystemExit) as exc:
            resolve_bind()
        message = str(exc.value.code)
        _assert_user_friendly(message)
        assert "数字" in message
        assert "重试" in message

    def test_out_of_range_port_is_chinese(self, monkeypatch):
        monkeypatch.setenv("PORT", "70000")
        with pytest.raises(SystemExit) as exc:
            resolve_bind()
        message = str(exc.value.code)
        _assert_user_friendly(message)
        assert "1-65535" in message

    def test_bind_failure_does_not_echo_raw_oserror(self, capsys):
        # 真实场景里这里就是 ``[WinError 10048] 通常每个套接字地址...``。
        _print_bind_failure(
            "127.0.0.1",
            8080,
            OSError(10048, "address already in use", None, 10048),
        )
        err = capsys.readouterr().err
        _assert_user_friendly(err)
        assert "8080" in err
        # 一句话说清怎么办：关掉占用程序，或换端口。
        assert "关闭" in err or "PORT" in err


class TestDataDirFailures:
    def test_unwritable_log_dir_prints_action_not_a_traceback(self, monkeypatch, capsys, tmp_path):
        """数据目录不可写 / 磁盘满 / 安全软件拦截 → 一句中文 + 下一步，不打栈。"""
        from src.gateway import __main__ as entry

        blocker = tmp_path / "blocked"
        blocker.write_text("not a directory", encoding="utf-8")
        monkeypatch.setattr(entry, "resolve_log_config_path", lambda *a, **k: "unused.yaml")
        monkeypatch.setattr(entry, "load_uvicorn_log_config", lambda *a, **k: {})
        monkeypatch.setattr(entry, "resolve_log_dir", lambda config=None: blocker / "logs")

        assert entry._run("127.0.0.1", 18099) == 1
        err = capsys.readouterr().err
        _assert_user_friendly(err)
        assert "数据目录" in err
        assert "磁盘" in err
        assert "重试" in err


class TestHttpFailureCopy:
    @pytest.fixture()
    def client(self):
        from src.gateway.main import get_app

        app = get_app()

        @app.get("/__probe_copy_500")
        def _boom_500():  # pragma: no cover - test-only route
            raise RuntimeError(
                "db_writer exploded at C:\\Users\\alice\\data\\audit.sqlite3, "
                "trace_id=abc123"
            )

        @app.get("/__probe_copy_400")
        def _boom_400():  # pragma: no cover - test-only route
            raise ValueError("internal_field_name must be one of [...]")

        with TestClient(app, raise_server_exceptions=False) as c:
            yield c

    def test_500_is_chinese_with_a_next_step_and_a_trace_id(self, client):
        response = client.get("/__probe_copy_500")
        assert response.status_code == 500
        body = response.json()

        detail = body["detail"]
        _assert_user_friendly(detail)
        assert "重试" in detail
        # snake_case 的 trace_id 不能出现在文案里，但结构化字段必须还在。
        assert "trace_id=" not in detail
        assert body.get("trace_id")
        assert response.headers.get("X-Trace-ID")

    def test_400_does_not_echo_the_raw_value_error(self, client):
        response = client.get("/__probe_copy_400")
        assert response.status_code == 400
        detail = response.json()["detail"]

        _assert_user_friendly(detail)
        assert "internal_field_name" not in detail
        assert "格式" in detail
        assert "重试" in detail
