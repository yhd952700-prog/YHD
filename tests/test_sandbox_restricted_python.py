"""RestrictedPython 受限执行面的行为契约测试。

这里**没有 skip**：所有用例都真跑真实子进程。
`RestrictedPython` 已声明为主依赖（189KiB 纯 Python、零传递依赖），
CI 的 `pip install -e .` 会装上，因此不存在"跳过了但看起来过了"的假绿。

本文件的核心不是"它能算对"，而是**它的边界必须被如实表达**：
- 能力隔离的每一条都要真的拦住（拦不住就是漏洞）
- 资源隔离做不到的事情必须**明确说做不到**（说成做得到才是事故）
"""
import pytest

from src.plugins.sandbox.backends import (
    SandboxBackendStatus,
    SandboxBackendType,
)
from src.plugins.sandbox.backends import restricted_python_backend as mod
from src.plugins.sandbox.backends.base import (
    ExecutionStatus,
    ResourceLimits,
)
from src.plugins.sandbox.backends.restricted_python_backend import (
    RestrictedPythonBackend,
    probe_restricted_python,
)

# 一发子进程约 0.7s（python 启动 + 装 RestrictedPython），留足余量
EXEC_TIMEOUT = 20


@pytest.fixture()
def backend() -> RestrictedPythonBackend:
    b = RestrictedPythonBackend()
    if not b.is_available():
        pytest.fail(
            f"RestrictedPython 不可用，本次运行无法验证受限执行面: "
            f"{probe_restricted_python()}。把它装回来再跑，不要改成 skip。"
        )
    return b


def run(backend: RestrictedPythonBackend, code: str, timeout: int = EXEC_TIMEOUT,
        limits: ResourceLimits = None):
    return backend.execute(
        f"exec-{abs(hash(code)) % 10**6}",
        command=["python", "-c", code],
        timeout=timeout,
        resource_limits=limits,
    )


class TestCapabilityIsolation:
    """这些是安全承诺，任何一条变成 success 都是漏洞。"""

    def test_arithmetic_and_result_variable(self, backend):
        r = run(backend, "result = 1 + 1")
        assert r.success is True
        assert r.status == ExecutionStatus.SUCCESS
        assert r.output == "2"

    def test_practical_python_still_works(self, backend):
        """太严的 builtins 会让后端"能跑但没用"，这条守住可用性下限。"""
        r = run(backend, "result = sum(i * i for i in range(100))")
        assert r.success is True, r.error
        assert r.output == "328350"

    def test_import_is_rejected(self, backend):
        r = run(backend, 'import os\nresult = os.system("echo pwned")')
        assert r.success is False
        assert r.status == ExecutionStatus.REJECTED
        assert "__import__" in (r.error or "")

    def test_open_is_rejected(self, backend):
        r = run(backend, 'result = open("C:/Windows/win.ini").read()')
        assert r.success is False
        assert r.status == ExecutionStatus.REJECTED
        assert "open" in (r.error or "")

    def test_eval_is_rejected_at_compile_time(self, backend):
        r = run(backend, 'result = eval("1+1")')
        assert r.success is False
        assert r.status == ExecutionStatus.REJECTED

    def test_dunder_attribute_escape_is_rejected(self, backend):
        r = run(backend, 'result = "".__class__.__mro__')
        assert r.success is False
        assert r.status == ExecutionStatus.REJECTED

    def test_getattr_by_string_is_rejected(self, backend):
        """字符串形式的属性访问能绕过编译期检查，必须靠 builtins 里没有 getattr 挡住。"""
        r = run(backend, 'result = getattr("x", "__class__")')
        assert r.success is False
        assert r.status == ExecutionStatus.REJECTED


class TestResourceBoundaries:
    """这里断言的是"做不到"，不是"做得到"。"""

    def test_cpu_bound_loop_is_killed_by_timeout(self, backend):
        r = run(backend, "x = 0\nfor i in range(10**9):\n    x = x + i\nresult = x",
                timeout=3)
        assert r.success is False
        # 必须是 timeout（真的跑了没跑完），不能是 rejected/failed 混过去
        assert r.status == ExecutionStatus.TIMEOUT

    def test_memory_limit_is_explicitly_not_enforced(self, backend):
        """**已知弱点，故意断言它会成功。**

        `[0] * 10**6` 会真的分配约 8MB 且不受任何限制。把它固化成断言，
        是为了防止将来有人看到"大内存分配失败了"就误以为有内存保护 ——
        那种失败只是 `bytearray` 这个 builtin 名字没被放进去的副作用。
        """
        r = run(backend, "result = len([0] * (10**6))")
        assert r.success is True
        assert r.output == "1000000"
        assert r.backend_info["memory_limit_enforced"] is False

    def test_unenforced_limits_are_reported_not_silently_ignored(self, backend):
        limits = ResourceLimits(memory_limit=1024, cpu_limit=1.0)
        r = run(backend, "result = 1 + 1", limits=limits)
        assert r.success is True
        unenforced = r.backend_info.get("unenforced_limits") or []
        assert "memory_limit" in unenforced
        assert "cpu_limit" in unenforced
        assert "warning" in r.backend_info


class TestHonestFailure:
    """谎报成功是这个项目反复清剿的缺陷家族，这里逐条堵。"""

    def test_unsupported_command_shape_is_not_success(self, backend):
        r = backend.execute("exec-shape", command=["python", "script.py"])
        assert r.success is False
        assert r.status == ExecutionStatus.UNSUPPORTED

    def test_backend_advertises_both_sides(self, backend):
        """既能做什么、不能做什么，都要出现在 info 里，供调用方做安全判断。"""
        info = backend.get_backend_info()
        assert info["capability_isolation"] is True
        assert info["cpu_timeout_enforced"] is True
        assert info["memory_limit_enforced"] is False
        assert info["license"] == "ZPL-2.1"

    def test_unavailable_backend_never_reports_available(self, monkeypatch):
        """强行模拟"没装"，验证不可用分支。

        不用 skip —— 本机装了就测不到这条，而"没装时会怎样"恰恰是最需要保证的。
        """
        monkeypatch.setattr(mod, "probe_restricted_python", lambda: "模拟：未安装")
        b = mod.RestrictedPythonBackend()
        assert b.is_available() is False
        assert b.get_status() == SandboxBackendStatus.UNAVAILABLE
        r = b.execute("exec-x", command=["python", "-c", "result = 1"])
        assert r.success is False
        assert r.status == ExecutionStatus.BACKEND_UNAVAILABLE
        assert "未安装" in (r.error or "")


class TestManagerIntegration:
    def test_registered_and_type_is_not_misreported(self):
        from src.plugins.sandbox.backends import SandboxBackendManager

        mgr = SandboxBackendManager(enable_docker=False)
        mgr._init_backends(enable_docker=False)
        b = mgr._backends.get(SandboxBackendType.RESTRICTED_PYTHON)
        assert b is not None, "受限后端必须注册，不可用也要带着原因注册"
        # 坏掉的后端不能谎报成别的后端类型
        assert b.backend_type == SandboxBackendType.RESTRICTED_PYTHON
        info = b.get_backend_info()
        assert info["backend"] == SandboxBackendType.RESTRICTED_PYTHON.value

    def test_required_backend_does_not_silently_downgrade(self, monkeypatch):
        """指定受限后端却不可用时，绝不能悄悄换成全权限的 subprocess。

        这是本仓出过的最危险的一类缺陷：调用方"要求隔离"，结果代码以宿主权限
        跑完还回复成功。用注入模拟不可用，确保它每次都被真的执行到。
        """
        from src.plugins.sandbox.backends import SandboxBackendManager

        monkeypatch.setattr(mod, "probe_restricted_python", lambda: "模拟：后端不可用")
        mgr = SandboxBackendManager(enable_docker=False)
        mgr._init_backends(enable_docker=False)
        assert mgr._backends[SandboxBackendType.RESTRICTED_PYTHON].is_available() is False
        with pytest.raises(RuntimeError):
            mgr.select_backend(SandboxBackendType.RESTRICTED_PYTHON, required=True)
