"""十源 packages 层 facade — 验证测试（Task #36）。

验证 ``LiuHao-O/packages/`` 的 33 个包目录被落地为**复用 src/ 的薄 facade**：
- 每个 IMPLEMENTED 包重新导出的符号与 ``src/`` 真实对象**同一**（identity 校验，
  证明是 re-export 而非复制/伪造 —— NO-FAKE）。
- NOT_IMPLEMENTED 包诚实导出空 ``__all__``，不伪造实现。
- 连字符导入屏障由 ``pyproject.toml`` 的 ``package-dir`` 解决。
"""

import importlib
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LHX = os.path.join(ROOT, "LiuHao-O")
if LHX not in sys.path:
    sys.path.insert(0, LHX)

# kernel 基础包的 Settings 在 import 时即实例化并要求这几个必填环境变量
# （fail-fast 设计）；测试侧用 setdefault 提供最小可导入配置，不覆盖真实环境。
os.environ.setdefault("LHX_POSTGRES_DSN", "postgresql://test:test@localhost:5432/test")
os.environ.setdefault("LHX_REDIS_DSN", "redis://localhost:6379")
os.environ.setdefault("LHX_SECRET_KEY", "test-secret-key")


# 期望映射：package -> (src 模块, 代表符号) 用于 identity 校验。
EXPECTED = {
    "agent": ("src.ai.agent_factory", "AgentFactory"),
    "analysis": ("src.ai.ada", "ComputeEngine"),
    "capability": ("src.kernels.capability", "CapabilityRegistry"),
    "context": ("src.kernels.context", "ContextKernel"),
    "economy": ("src.ai.economy", "EconomyEngine"),
    "evolution": ("src.ai.evolution", "EvolutionEngine"),
    "execution": ("src.kernels.execution", "ExecutionEngine"),
    "experience": ("src.ai.verification", "ExperienceEngine"),
    "goal": ("src.ai.goal_task_graph", "GoalTaskGraph"),
    "governance": ("src.ai.governance", "SecurityChain"),
    "identity": ("src.kernels.identity", "IdentityManager"),
    "memory": ("src.kernels.memory", "MemoryKernel"),
    "network": ("src.kernels.network", "NetworkBus"),
    "observability": ("src.observability", "ObservabilityStore"),
    "orchestration": ("src.ai.lcore", "LCore"),
    "organization": ("src.ai.organization", "Organization"),
    "perception": ("src.ai.perception", "WorldModel"),
    "planning": ("src.kernels.execution", "PlanBuilder"),
    "policy": ("src.kernels.policy", "PolicyEngine"),
    "realtime": ("src.ai.collaboration", "MessageBus"),
    "resource": ("src.kernels.resource", "ResourceQuotaManager"),
    "runtime": ("src.ai.runtime_loop", "RuntimeLoop"),
    "sandbox": ("src.ai.ada", "ComputeEngine"),
    "security": ("src.kernels.security", "SecurityEngine"),
    "task": ("src.ai.employee", "Task"),
    "tools": ("src.ai.tool_registry", "ToolRegistry"),
    "trust": ("src.kernels.trust", "TrustManager"),
    "verification": ("src.ai.verification", "VerificationEngine"),
    "world": ("src.ai.world_interface", "WorldInterface"),
}

NOT_IMPL = ["approval", "common", "reasoning"]

# 33 个包目录全集（kernel 为已存在的 foundation 实现，不在此映射内）
ALL_PACKAGES = sorted(set(EXPECTED) | set(NOT_IMPL) | {"kernel"})


def _import_pkg(name):
    return importlib.import_module("packages.%s" % name)


class TestFacadeReexportsRealObjects:
    def test_every_facade_reexports_identical_object(self):
        for pkg, (module, symbol) in EXPECTED.items():
            facade = _import_pkg(pkg)
            src_mod = importlib.import_module(module)
            # identity 校验：facade 导出的就是 src 里的那个对象，不是副本
            assert getattr(facade, symbol) is getattr(src_mod, symbol), (
                "%s.%s 不是 %s.%s 的真实 re-export" % (pkg, symbol, module, symbol)
            )

    def test_facade___all___is_nonempty_and_resolvable(self):
        for pkg in EXPECTED:
            facade = _import_pkg(pkg)
            assert facade.__all__, "%s 的 __all__ 不应为空" % pkg
            for name in facade.__all__:
                assert hasattr(facade, name), "%s.__all__ 含未知符号 %s" % (pkg, name)


class TestNotImplementedAreHonest:
    def test_not_implemented_export_nothing(self):
        for pkg in NOT_IMPL:
            facade = _import_pkg(pkg)
            assert facade.__all__ == [], "%s 不应导出任何符号" % pkg
            assert getattr(facade, "NOT_IMPLEMENTED", False) is True, (
                "%s 应诚实标注 NOT_IMPLEMENTED" % pkg
            )


class TestKernelFoundationPreserved:
    def test_kernel_foundation_still_imports(self):
        # kernel 包是已存在的 foundation 实现，落地不得破坏它
        kernel = _import_pkg("kernel")
        assert hasattr(kernel, "settings")
        assert hasattr(kernel, "check_health")
        assert hasattr(kernel, "check_ready")


class TestPackageRootAndBarrier:
    def test_packages_root_exports_version(self):
        pkg = importlib.import_module("packages")
        assert pkg.__version__ == "3.0.0"

    def test_pyproject_solves_hyphen_barrier(self):
        # 连字符导入屏障：LiuHao-O 目录名含连字符不可作为包名导入，
        # pyproject.toml 用 package-dir 把它映射为可导入的 liuhao_x。
        path = os.path.join(LHX, "pyproject.toml")
        assert os.path.exists(path)
        with open(path, encoding="utf-8") as f:
            text = f.read()
        assert 'package-dir = {"" = "packages"}' in text
        assert 'name = "liuhao-x"' in text

    def test_all_33_packages_are_importable(self):
        # 33 个包目录全部可导入（IMPLEMENTED facade 或诚实 NOT_IMPLEMENTED）
        for name in ALL_PACKAGES:
            _import_pkg(name)
