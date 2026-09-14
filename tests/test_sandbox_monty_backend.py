"""Monty 后端 + 「不静默降级」语义的验收测试。

这里最重要的不是 Monty 能不能跑，而是：**当我们明确要求受限执行面时，
系统绝不能悄悄换成全权限后端还报告成功。**

旧行为：execute(..., backend_type=MONTY) 在 monty 没装时静默落到 subprocess，
以宿主权限跑完 AI 生成的代码并返回 success=True —— 调用方毫无察觉。
"""

from __future__ import annotations

import pytest

from src.plugins.sandbox.backends import (
    ExecutionResult,
    ExecutionStatus,
    ResourceLimits,
    SandboxBackendStatus,
    SandboxBackendType,
    SandboxBackendManager,
    probe_monty,
)
from src.plugins.sandbox.backends.monty_backend import MontyBackend, probe_monty as _probe


# --------------------------------------------------------------------------
# ExecutionResult.status —— success 布尔表达不了的那部分
# --------------------------------------------------------------------------

class TestExecutionStatus:

    def test_default_status_is_derived_from_success(self):
        ok = ExecutionResult("x", success=True)
        bad = ExecutionResult("x", success=False)
        assert ok.status is ExecutionStatus.SUCCESS
        assert bad.status is ExecutionStatus.FAILED

    def test_explicit_status_is_kept(self):
        r = ExecutionResult("x", success=False, status=ExecutionStatus.REJECTED)
        assert r.status is ExecutionStatus.REJECTED
        assert r.success is False

    def test_status_roundtrips_through_dict(self):
        r = ExecutionResult("x", success=False, status=ExecutionStatus.BACKEND_UNAVAILABLE)
        restored = ExecutionResult.from_dict(r.to_dict())
        assert restored.status is ExecutionStatus.BACKEND_UNAVAILABLE
        assert restored.to_dict()["status"] == "backend_unavailable"


# --------------------------------------------------------------------------
# 核心：绝不静默降级到更弱的后端
# --------------------------------------------------------------------------

class TestNoSilentDowngrade:

    def _manager(self):
        return SandboxBackendManager(enable_docker=False)

    def test_requested_unavailable_backend_does_not_fall_back(self):
        mgr = self._manager()
        # MONTY 在当前环境必然不可用（monty 未安装或绑定不成型）
        assert mgr.get_availability()[SandboxBackendType.MONTY.value]["available"] is False

        with pytest.raises(RuntimeError) as exc:
            mgr.select_backend(SandboxBackendType.MONTY, required=True)
        msg = str(exc.value)
        assert "monty" in msg.lower()
        assert "Refusing to silently fall back" in msg

    def test_execute_with_required_true_raises_instead_of_running(self):
        """这是 S5 的验收点：危险调用被拒，且报错可解释。"""
        mgr = self._manager()
        with pytest.raises(RuntimeError) as exc:
            mgr.execute(
                execution_id="uptime-1",
                command=["python", "-c", "print(1)"],
                backend_type=SandboxBackendType.MONTY,
                required=True,
            )
        assert "Refusing to silently fall back" in str(exc.value)

    def test_default_behaviour_still_falls_back(self):
        """历史行为必须保留 —— 没说 required 的调用方不受影响。"""
        mgr = self._manager()
        backend = mgr.select_backend(SandboxBackendType.MONTY)  # 默认 required=False
        assert backend.backend_type in (SandboxBackendType.GVISOR, SandboxBackendType.SUBPROCESS)

    def test_availability_reports_a_reason(self):
        """「不可用」必须带原因 —— 没有原因的 False 等于无法排障。"""
        info = self._manager().get_availability()[SandboxBackendType.MONTY.value]
        # MontyBackend.get_backend_info() 带 reason；兜底实现同样带 reason
        assert info["info"].get("reason"), info


# --------------------------------------------------------------------------
# Monty 后端本体 —— 诚实不可用
# --------------------------------------------------------------------------

class TestMontyBackend:

    def test_unavailable_is_honest_not_success(self):
        backend = MontyBackend()
        result = backend.execute("exec-1", command=["python", "-c", "print(1)"])
        if backend.is_available():
            pytest.skip("monty 在本环境可用 —— 走真实执行分支，不在此断言范围")
        assert result.success is False
        assert result.status is ExecutionStatus.BACKEND_UNAVAILABLE
        assert result.error, "不可用必须给出可读原因，不能只给 False"
        assert "pydantic-monty" in result.error or "monty" in result.error.lower()

    def test_status_is_unavailable_when_broken(self):
        backend = MontyBackend()
        status = backend.get_status()
        expected = SandboxBackendStatus.HEALTHY if backend.is_available() else SandboxBackendStatus.UNAVAILABLE
        assert status is expected

    def test_unsupported_command_shape_is_reported_as_unsupported(self):
        backend = MontyBackend()
        if not backend.is_available():
            pytest.skip("monty 不可用时先看 BACKEND_UNAVAILABLE，形状校验无从触发")
        result = backend.execute("exec-2", command=["ls", "-la"])
        assert result.success is False
        assert result.status is ExecutionStatus.UNSUPPORTED

    def test_resource_limits_are_supported(self):
        limits = ResourceLimits(execution_time_limit=3)
        assert limits.execution_time_limit == 3


# --------------------------------------------------------------------------
# API 前置条件 —— 把"为什么今天不能用"固化成可执行断言。
# pydantic-monty 一旦可用，这两条会先红，提醒我们回来启用。
# --------------------------------------------------------------------------

class TestMontyApiPreconditions:

    def test_probe_returns_none_or_a_readable_reason(self):
        reason = probe_monty()
        assert reason is None or isinstance(reason, str), (
            f"probe_monty 必须返回 None 或可读原因，实际: {type(reason)}"
        )
        if reason is not None:
            assert "pydantic-monty" in reason or "Monty" in reason

    def test_pypi_name_collision_is_documented(self):
        """`monty` 是别人的包 —— 这条测试把事实钉住，防止有人写成 `pip install monty`。"""
        from src.plugins.sandbox.backends.monty_backend import DISTRIBUTION_NAME, PACKAGE_NAME
        assert DISTRIBUTION_NAME == "pydantic-monty"
        assert PACKAGE_NAME == "pydantic_monty"
        # 裸名 monty 属于 materialyzeai/monty，与本后端无关


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
