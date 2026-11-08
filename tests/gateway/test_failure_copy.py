"""失败路径文案：用户看到的是"发生了什么 + 怎么办"，不是英文异常或机器串。

覆盖能稳定复现的入口：

* 端口被占用 / PORT 填错（启动阶段，控制台输出）
* 未预期异常（500）与参数校验（400）的响应文案
* 身份 / 策略拒绝：内部治理术语、政策编号、脚本路径不上屏
* error.log：技术细节保留，但政策编号与本机绝对路径必须脱敏

数据目录不可写、磁盘满、杀毒拦截属于环境故障，无法在单测里稳定制造；
它们的文案走同一套兜底（``display.py`` 的中性中文），由
``test_diagnostic_bundle.py`` 的就绪检查字段白名单兜住"原始异常不进用户文件"。
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from src.gateway.__main__ import _print_bind_failure, resolve_bind
from src.gateway.display import (
    ACCOUNT_HIERARCHY_WORDS,
    machine_fragments,
    user_facing_detail,
)
from src.logging_config import DEFAULT_LOG_CONFIG, RedactingFormatter, load_uvicorn_log_config

#: 绝对路径 / 英文异常栈 / 内部治理术语的特征。用户文案里出现任意一个都算不合格。
_FORBIDDEN = (
    re.compile(r"[A-Za-z]:\\"),
    re.compile(r"Traceback \(most recent call last\)"),
    re.compile(r"\b[A-Za-z]+Error\b"),
    re.compile(r"WinError|Errno \d+|ENOSPC|EACCES"),
    re.compile(r"address already in use", re.IGNORECASE),
    re.compile(r"sovereignty|kernel|principal|grant_window", re.IGNORECASE),
    re.compile(r"\b(?:OD|LHX|C)-\d+", re.IGNORECASE),
    re.compile(r"\b(?:scripts|src)[\\/]", re.IGNORECASE),
)


def _assert_user_friendly(text: str) -> None:
    assert text.strip(), "文案不能为空"
    assert not machine_fragments(text), f"出现机器串：{machine_fragments(text)}"
    for word in ACCOUNT_HIERARCHY_WORDS:
        assert word not in text, f"出现层级词：{word}"
    for pattern in _FORBIDDEN:
        assert not pattern.search(text), f"出现英文异常/绝对路径：{pattern.pattern} -> {text}"


def _assert_no_internal_leak(text: str) -> None:
    """身份拒绝允许出现「管理员」这类正常角色词，只禁止内部术语与实现细节。"""
    assert text.strip(), "文案不能为空"
    for pattern in _FORBIDDEN:
        assert not pattern.search(text), f"出现内部术语/绝对路径：{pattern.pattern} -> {text}"


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


class TestIdentityRefusalCopy:
    """身份/策略拒绝：原始英文只进日志，``detail`` 只出中性中文。"""

    @pytest.mark.parametrize(
        ("status", "raw"),
        [
            (403, "token does not represent a human identity"),
            (
                403,
                "identity 'alice' is revoked in the trust registry; hire refused "
                "(human-sovereignty: revoked principal cannot be (re)hired)",
            ),
            (400, "unknown principal for sovereignty grant: 'alice'"),
            (400, "unknown kernel action(s): ['capability.retire']"),
        ],
    )
    def test_english_internal_refusals_become_neutral_chinese(self, status, raw):
        detail = user_facing_detail(raw, status)
        _assert_no_internal_leak(detail)
        assert re.search(r"[\u4e00-\u9fff]", detail)
        # 说清"发生了什么 + 下一步"：每个兜底都至少给一个动作。
        assert any(word in detail for word in ("重试", "联系管理员", "改用", "重新登录"))

    def test_chinese_refusal_keeps_meaning_and_drops_internal_terms(self):
        detail = user_facing_detail(
            "该操作需要管理员权限（admin 角色或 L0 人类主权，见 OD-010）。"
            "请让管理员授权后重试。",
            403,
        )
        assert "管理员权限" in detail
        assert "人类主权" not in detail
        assert "OD-010" not in detail
        assert "L0" not in detail
        assert "重试" in detail

    def test_script_path_is_not_shown(self):
        detail = user_facing_detail(
            "账号未登记，请运行 scripts/register_human_identity.py 后再试",
            400,
        )
        assert "register_human_identity" not in detail
        assert "相关说明" in detail

    def test_already_readable_copy_is_left_untouched(self):
        readable = "员工已停用，无法接受指派。请先启用员工后重试。"
        assert user_facing_detail(readable, 409) == readable

    def test_structured_detail_preserves_machine_code(self):
        detail = user_facing_detail(
            {"code": "human_required", "message": "token does not represent a human identity"},
            403,
        )
        assert detail["code"] == "human_required"
        assert "human identity" not in detail["message"]
        assert re.search(r"[\u4e00-\u9fff]", detail["message"])


class TestServerSurfaceCopy:
    """服务端**直接下发**给界面的文案：内部术语与英文异常不能回潮。

    ``user_facing_detail`` 只兜住异常出口；``detail=`` / ``"message"`` /
    ``"reason"`` / 降级 payload 里的字符串是另一条通路，必须逐处收口。
    """

    _SRC = Path(__file__).resolve().parents[2] / "src"

    def _read(self, rel: str) -> str:
        return (self._SRC / rel).read_text(encoding="utf-8")

    @pytest.mark.parametrize(
        ("rel", "banned", "expected"),
        [
            # 权限拒绝：不再出现「人类主权 / L0」这类内部层级。
            (
                "gateway/models.py",
                "L0 人类主权）可以修改模型配置",
                "只有管理员可以修改模型配置",
            ),
            ("upgrade/api.py", "token has no subject claim", "登录状态已失效"),
            ("upgrade/api.py", "服务/机器人主体", "管理员账号"),
            # 运维面：内核/实例属于实现词，换成「模块」。
            ("gateway/kernels.py", "内核已登记", "该模块已登记但尚未启用"),
            # 对话回复：主体 -> 账号。
            ("ai/liuhao.py", "当前主体未被授权", "当前账号未被授权"),
            # 审计校验：英文异常不再拼进 failures。
            ("gateway/audit.py", "pinpoint scan failed", "定位失败位置时没有运行成功"),
            ("gateway/health.py", "arming self-check failed", "安全策略自检暂时无法运行"),
            # 批量建员工 / 取消目标：英文异常类名与英文原因不上屏。
            ("gateway/ai_management.py", "human operator abort", "操作人主动中止"),
            # 知识库：不再把异常类名拼进对话提示。
            (
                "gateway/knowledge_context.py",
                "知识库不可用（",
                "知识库暂时不可用",
            ),
            (
                "gateway/data_portability.py",
                "知识库未启用：{exc}",
                "知识库暂时不可用",
            ),
            # 审批：defer 是内部状态名。
            ("gateway/policy.py", "（defer）", "动作被策略暂缓"),
        ],
    )
    def test_internal_jargon_is_replaced_with_neutral_chinese(
        self, rel: str, banned: str, expected: str
    ) -> None:
        source = self._read(rel)
        assert banned not in source, f"{rel} 仍把内部术语下发到界面：{banned}"
        assert expected in source, f"{rel} 缺少中性中文文案：{expected}"

    def test_degraded_dashboard_payload_does_not_echo_exceptions(self) -> None:
        """总览把 ``timeseries.error`` / ``breakdown.error`` 当空态提示直接渲染。"""
        source = self._read("gateway/dashboard.py")
        assert '"error": str(exc)' not in source
        assert "暂时读不到" in source

    def test_model_guidance_points_to_a_real_page(self) -> None:
        from src.ai.providers import (
            GENERATION_FAILURE_MESSAGE,
            MODEL_NOT_CONFIGURED_MESSAGE,
        )

        for text in (GENERATION_FAILURE_MESSAGE, MODEL_NOT_CONFIGURED_MESSAGE):
            assert "系统设置 → 模型管理" in text
            # 环境变量名与不存在的「设置 → 模型」页都不该出现在用户面前。
            assert "AI_PROVIDER_TYPE" not in text
            assert "「设置 → 模型」" not in text


class TestErrorLogRedaction:
    """``error.log`` 保留技术细节，但去掉政策编号与本机绝对路径。"""

    def _record(self, message: str, exc_info=None) -> logging.LogRecord:
        return logging.LogRecord(
            name="liuhao.test",
            level=logging.ERROR,
            pathname=r"D:\LiuHao-AI-OS\src\gateway\main.py",
            lineno=42,
            msg=message,
            args=(),
            exc_info=exc_info,
        )

    def test_policy_ids_and_absolute_paths_are_redacted(self):
        formatter = RedactingFormatter(format="%(levelname)s | %(message)s")
        output = formatter.format(
            self._record(
                r"denied at D:\LiuHao-AI-OS\src\kernels\_sovereignty.py "
                r"and D:\temp\run.ps1, policy OD-010 / LHX-C-010"
            )
        )
        assert "OD-010" not in output
        assert "LHX-C-010" not in output
        assert "D:\\LiuHao-AI-OS" not in output
        assert "D:\\temp" not in output
        # 文件名还在，排障线索不丢。
        assert "_sovereignty.py" in output
        assert "run.ps1" in output

    def test_traceback_paths_are_redacted_but_exception_stays(self):
        formatter = RedactingFormatter(format="%(levelname)s | %(message)s")
        try:
            raise RuntimeError("db_writer exploded")
        except RuntimeError:
            import sys

            output = formatter.format(
                self._record("unhandled", exc_info=sys.exc_info())
            )
        assert "D:\\LiuHao-AI-OS" not in output
        assert "RuntimeError" in output
        assert "db_writer exploded" in output

    def test_runtime_config_wraps_every_formatter(self):
        config = load_uvicorn_log_config(DEFAULT_LOG_CONFIG)
        for name, formatter in config["formatters"].items():
            assert formatter.get("()") == "src.logging_config.RedactingFormatter", name
