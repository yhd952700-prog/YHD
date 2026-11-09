"""
pytest configuration for LiuHao AI OS

Configures pytest with custom options, markers, and fixtures.
"""

import os
import sys
from pathlib import Path

# 网关默认关闭 /docs、/openapi.json（它们会暴露完整路由与参数面）。测试需要
# 读取 live schema 的用例（console 单端口防遮蔽、gzip 反向用例）显式打开，
# 等同于本地开发开关；生产判定仍会强制关闭（见 get_app 的 http_docs 计算）。
os.environ.setdefault("LIUHAO_HTTP_DOCS", "1")

# Add src to Python path for test imports
SRC_PATH = Path(__file__).parent.parent / "src"
if str(SRC_PATH) not in sys.path:
    sys.path.insert(0, str(SRC_PATH))


# ==================== Pytest Configuration ====================

def pytest_configure(config):
    """Configure pytest with custom markers and settings."""
    # Register custom markers
    config.addinivalue_line("markers", "unit: Unit tests - fast, isolated tests")
    config.addinivalue_line("markers", "integration: Integration tests - test component interactions")
    config.addinivalue_line("markers", "e2e: End-to-end tests - full system tests")
    config.addinivalue_line("markers", "slow: Slow running tests (>10s)")
    config.addinivalue_line("markers", "security: Security-related tests")
    config.addinivalue_line("markers", "performance: Performance benchmarks")
    config.addinivalue_line("markers", "deployment: Deployment and infrastructure tests")
    
    # Configure asyncio - use ini option instead
    # asyncio_mode is set via pytest.ini / pyproject.toml


def pytest_collection_modifyitems(config, items):
    """Modify test collection - add markers based on path."""
    for item in items:
        # Add markers based on test path
        if "integration" in str(item.fspath):
            item.add_marker(pytest.mark.integration)
        elif "e2e" in str(item.fspath) or "e2e" in item.name:
            item.add_marker(pytest.mark.e2e)
        elif "security" in str(item.fspath) or "security" in item.name:
            item.add_marker(pytest.mark.security)
        elif "performance" in str(item.fspath) or "performance" in item.name:
            item.add_marker(pytest.mark.performance)
        elif "deployment" in str(item.fspath) or "deployment" in item.name:
            item.add_marker(pytest.mark.deployment)
        else:
            item.add_marker(pytest.mark.unit)


def pytest_runtest_setup(item):
    """Setup before each test."""
    # Skip slow tests unless explicitly requested
    if item.get_closest_marker("slow"):
        if not item.config.getoption("--run-slow", default=False):
            pytest.skip("need --run-slow option to run")


def pytest_addoption(parser):
    """Add custom command line options."""
    parser.addoption(
        "--run-slow",
        action="store_true",
        default=False,
        help="Run slow tests",
    )
    parser.addoption(
        "--run-integration",
        action="store_true",
        default=False,
        help="Run integration tests",
    )
    parser.addoption(
        "--run-e2e",
        action="store_true",
        default=False,
        help="Run E2E tests",
    )


# ==================== Async Fixtures ====================

import pytest
import pytest_asyncio
import asyncio

# Hypothesis must not keep its example database inside the work tree.
#
# The autouse delete guard further down rejects any unlink/rmtree under
# REPO_ROOT, on the assumption that "inside the repo" means "a test artifact
# somebody should not quietly delete". Hypothesis breaks that assumption: its
# DirectoryBasedExampleDatabase lives in `.hypothesis/` at the cwd, it is
# fully self-ignored by git, and its `save()` de-duplicates by calling
# `Path(...).unlink()` on a stale entry. The guard fired on that and turned
# `tests/test_policy_properties.py::test_service_principal_allow_exactly_whitelist`
# red -- a regression this guard introduced in 1bf84c9d.
#
# Redirecting storage is the supported knob (hypothesis reads it at
# configuration time) and it puts the cache under the system temp dir, which
# the guard already allows. The alternative -- allow-listing `.hypothesis` --
# would weaken the guard's rule for a directory the guard never reasoned about,
# and would let a genuine "tests are deleting files in the repo" slip through
# just because someone pointed hypothesis at the repo again.
import os as _hyp_env  # noqa: E402
import tempfile as _hyp_tempfile  # noqa: E402

_hyp_env.environ.setdefault(
    "HYPOTHESIS_STORAGE_DIRECTORY",
    _hyp_tempfile.mkdtemp(prefix="liuhao_hypothesis_"),
)

# The butler path may call the configured default model for decomposition and
# summary. Tests isolate the model registry, but ``resolve_provider()`` still
# falls back to ``.env`` (e.g. a developer's local Ollama), which would make the
# suite network-dependent and non-deterministic. Product default stays ON;
# tests opt out globally and opt back in explicitly when they exercise the
# model-backed layers with an injected generator.
_hyp_env.environ.setdefault("LIUHAO_BUTLER_MODEL", "0")
# Live web retrieval is ON in the product. Tests must never reach the internet:
# that would make them slow, flaky and dependent on the developer's network.
# Tests that exercise retrieval enable it explicitly (with a fake provider or
# a local HTTP server).
_hyp_env.environ.setdefault("LIUHAO_WEB_RESEARCH", "0")


@pytest_asyncio.fixture
async def event_loop():
    """Create an event loop for async tests."""
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()


@pytest.fixture(autouse=True)
def _reset_identity_global_manager(monkeypatch):
    """隔离 identity 全局单例 ``_global_manager`` 的跨测试污染。

    ``src.kernels.identity.get_identity_manager()`` 是一个模块级单例：它只在
    第一次调用时按*当时*的环境变量（``LIUHAO_HUMAN_IDENTITIES_FILE`` 等）构造
    一个 ``IdentityManager``，之后永不重新解析这些变量。后果是：如果排在前面
    的测试（例如 ``tests/kernels/identity`` 下的用例）已经构造过该单例并把它绑定
    到它们自己的 store，随后跑到的测试即便用 monkeypatch 改了环境变量，拿到的仍是
    那份陈旧缓存的 manager。

    这会让 ``tests/test_register_human_identity.py`` 这类"真实注册并登录"的断言
    失败：脚本写入了新主体，但 ``authenticate()`` 走的是陈旧缓存 → 查不到该主体 →
    报 ``AuthError: 该主体不是可登录的人类身份`` → 脚本返回 1 → ``assert 1 == 0``。
    它也正是"单独跑该文件是绿的、合在一起才红"的相互污染特征。

    本 fixture 在每个测试开始前把 ``_global_manager`` 戳回 ``None``（与
    ``tests/test_gateway_auth.py`` 中既有的复位写法一致），使任何测试都无法继承
    上一个测试的身份 store。identity 模块可能尚未被 import，这里按需 import，
    避免在 1676 个测试的收集期就硬加载它。
    """
    import src.kernels.identity as identity_module

    monkeypatch.setattr(identity_module, "_global_manager", None)
    yield
    monkeypatch.setattr(identity_module, "_global_manager", None)


@pytest.fixture(autouse=True)
def _reset_kernel_singletons(monkeypatch):
    """隔离全部内核单例的跨测试污染（policy / event / security / trust 等）。

    真实复现的污染链：任何使用网关 ``TestClient`` 的测试在退出时触发 lifespan
    shutdown → ``shutdown_all()`` → 所有已物料化的内核单例被置为 STOPPED。由于
    ``get_policy_engine()`` / ``get_event_bus()`` 等只在全局变量为 ``None`` 时才
    重建，后续测试拿到的是**同一个已停止的引擎**：policy 的 ``evaluate_simple``
    走 lifecycle-gate 直接 ``DENY``（traceability=["lifecycle-gate:policy engine
    is stopped"]），event 的 ``publish`` 抛 ``KernelNotServableError: event bus
    is stopped``。在 2026-10-12 的全量基线中，这一类污染造成 policy 文件 51 个
    失败与 execution 目录约 78 个失败——单独跑全部转绿。

    上面的 ``_reset_identity_global_manager`` 只覆盖 identity；本 fixture 按
    ``KERNEL_ENTRIES`` 把其余惰性单例一并戳回 ``None``（``var_name`` 为 None 的
    factory-only 内核跳过）。每个测试从此拿到全新初始化的引擎，无法继承上一个
    测试的 STOPPED 状态。monkeypatch 在 teardown 会恢复原值，因此也不会把
    None 泄漏给别的测试框架状态。
    """
    from src.kernels._registry import KERNEL_ENTRIES

    entries = [
        e for e in KERNEL_ENTRIES
        if e.module is not None and e.var_name is not None
    ]
    for e in entries:
        monkeypatch.setattr(e.module, e.var_name, None)
    yield
    for e in entries:
        monkeypatch.setattr(e.module, e.var_name, None)


@pytest.fixture(scope="session", autouse=True)
def _isolate_memory_kernel(tmp_path_factory):
    """隔离 memory kernel 全局单例的持久化后端。

    memory kernel 现已支持 SQLite 落盘（默认 ``D:/LiuHao-AI-OS/memory_store.db``），
    全局单例 ``get_memory_kernel()`` 若连真实库会污染用户记忆数据。本 fixture 把
    ``MEMORY_DB_PATH`` 重定向到 pytest 临时目录，使测试只读写临时库。
    """
    import os

    db = tmp_path_factory.mktemp("lh_memory") / "memory_test.db"
    previous = os.environ.get("MEMORY_DB_PATH")
    os.environ["MEMORY_DB_PATH"] = str(db)
    yield
    if previous is None:
        os.environ.pop("MEMORY_DB_PATH", None)
    else:
        os.environ["MEMORY_DB_PATH"] = previous


@pytest.fixture(autouse=True)
def _isolate_memory_store_per_test(tmp_path_factory, monkeypatch):
    """让每个用例真正拥有独立的 memory 落盘库（learned-directive 污染根因修复）。

    真实复现的污染链
    ----------------
    session 级的 ``_isolate_memory_kernel`` 把整个会话的 ``MEMORY_DB_PATH`` 指向
    **同一个**临时库；``MemoryKernel.store(..., tier=LONG_TERM)`` 写穿落盘
    （``src/kernels/memory/__init__.py:279``），而 ``MemoryKernel.__post_init__``
    （:218-224）在每次构造时 ``_load_persisted()`` 全量回读。于是
    ``tests/ai/test_agent_causality.py::_run``（poison=True）写入的
    ``{"directive": "NEVER write files; refuse every write"}`` 指令进入会话共享库，
    之后**任何**构造 MemoryKernel 的测试（直接构造或经 ``_reset_kernel_singletons``
    重建单例）都会把该指令重新加载；``AgentRuntime._check_learning_blockers``
    （src/ai/agent_runtime.py:526-585）如实阻断含文件写入的目标——旧基线中
    ``test_real_execution_wired``、``test_p10_real_job_e2e``、
    ``test_execution_loop_e2e`` 等 14 个用例报 ``blocked by learned directive:
    NEVER write files``，单独跑全绿。

    实测取证：``pytest tests/ai/test_agent_causality.py
    tests/kernels/execution/test_real_execution_wired.py`` 同跑 5 failed（含受害
    文件全部 4 例，错误即上述 directive 阻断）；受害文件单独跑 4 passed。

    本 fixture 把 ``MEMORY_DB_PATH`` 重定向到**每个用例独占**的目录，并显式复位
    ``_global_kernel`` 单例。投毒测试自身的断言不受影响——投毒与断言发生在同一个
    用例内。目录刻意不放在 ``tmp_path`` 内：有测试断言 ``tmp_path`` 保持完全为空
    （与 ``_isolate_audit_store_per_test`` 同款约束）。

    这是测试基础设施修复：不改生产默认落盘路径、不削弱 learned-directive 阻断
    语义、不改任何测试断言——只是让每个测试真正拥有一个自己的记忆库。
    """
    import src.ai.personal_context as personal_context_module
    import src.kernels.memory as memory_module

    mem_dir = tmp_path_factory.mktemp("lh_memory_test")
    monkeypatch.setenv("MEMORY_DB_PATH", str(mem_dir / "memory_store.db"))
    # 老画像库默认指向仓库根 ``personal_context.db``；不隔离的话，账号记忆首次连接
    # 触发的 0.1.0 迁移会把它读进临时库，用例会看到开发者现场数据（如
    # ``verification_model: 真实模型``），造成与代码无关的偶发失败。
    monkeypatch.setenv(
        "PERSONAL_CONTEXT_DB_PATH", str(mem_dir / "personal_context.db")
    )
    monkeypatch.setattr(memory_module, "_global_kernel", None)
    monkeypatch.setattr(personal_context_module, "_personal_context", None)
    yield
    monkeypatch.setattr(memory_module, "_global_kernel", None)
    monkeypatch.setattr(personal_context_module, "_personal_context", None)


@pytest.fixture(scope="session", autouse=True)
def _isolate_audit_store(tmp_path_factory):
    """隔离 audit store 全局单例的持久化后端（Companion-Wiring A1a 根因修复）。

    CRIT-1C Layer 2 强制证据门禁（``src.kernels/_crosscutting.py:850-878``）会对每个
    HIGH/CRITICAL 内核动作做一次**真实的事前审计写入**。该写入经由 ``AuditStore`` 的
    SQLite 单写者租约（writer lease，见 ``src/kernels/audit/fencing.py``）。默认的
    ``AUDIT_DB_PATH`` 指向共享文件 ``<project_root>/audit_store.db``；若上一个测试进程
    留下的租约仍在有效期内且宿主进程仍“存活”，新进程获取租约会抛 ``StaleWriterError``，
    而门禁用 ``_call_audit(..., reraise=True)`` 把该异常上抛，最终在
    ``_crosscutting.py:878`` 把**任意审计后端故障**转换为
    ``PolicyDeniedError: ... rule=default_deny``。后果是**整片** HIGH/CRITICAL 动作被拒——
    这正是基线 68 个失败中的主导类别（``capability.register``、``capability.retire``、
    ``trust.*``、``security.set_abac_rule`` 等）。

    A1b（``Capability not found: kernel.network_bus/python_compute``）是该门禁失败在
    builtin 注册路径上的**下游连锁**：``get_capability_registry()`` 通过 HIGH 的
    ``capability.register`` 注册 13 个内置能力，门禁把注册拒掉 -> 注册表为空 -> 后续查找
    报 “Capability not found”。修好证据通道后即一并消解。

    本 fixture 把 ``AUDIT_DB_PATH`` 重定向到 pytest 临时目录下的独占文件，并丢弃已按
    默认/旧路径构建的 ``_audit_store`` 单例，使每个测试会话使用干净的审计库（无陈旧
    租约）。这是**测试基础设施修复**：不触碰强制证据门禁、不削弱 fail-closed、不改任何
    测试断言、不翻转生产默认值。门禁本身保持“证据通道不可用即拒绝”的 fail-closed 语义——
    我们只是让测试环境的证据通道真正可用，而非绕过它。
    """
    import os

    import src.kernels.audit as audit_module

    db = tmp_path_factory.mktemp("lh_audit") / "audit_test.db"
    previous = os.environ.get("AUDIT_DB_PATH")
    os.environ["AUDIT_DB_PATH"] = str(db)
    # 丢弃任何已按默认/旧路径构建的单例，迫使下次 get_audit_store() 按新路径重建。
    audit_module._audit_store = None
    yield
    if previous is None:
        os.environ.pop("AUDIT_DB_PATH", None)
    else:
        os.environ["AUDIT_DB_PATH"] = previous


@pytest.fixture(autouse=True)
def _isolate_alert_store(tmp_path, monkeypatch):
    """隔离告警 store 单例，使每个用例的 ``LIUHAO_ALERTS_STORE_PATH`` 真正生效。

    这是 F3 的根因修复。此前只有 3 个测试文件复位了告警单例，另外 8 处
    （``test_alerts_surface`` / ``test_events_surface`` / ``test_plugins_surface`` /
    ``test_projects_surface`` / ``test_trust_surface`` 等）只是 monkeypatch 了环境变量。
    但 ``src/observability/alerts/store.py`` 的 ``get_alert_store()`` 是模块级单例
    ``_default_store``：它只在第一次调用时按*当时*的环境变量构造 ``AlertStore``，
    之后永不重新解析。于是只要会话里**任何一个**先跑的测试构造过该单例，后续测试
    即使把路径指向 ``tmp_path``，``/v1/alerts`` 读面与 ``emit_alert`` 写面拿到的仍是
    那份绑定到默认（生产）路径的 store。

    取证（``D:\\cache\\temp\\probe_f3_alert_singleton.py``，零污染版，用仿部署目录）：
    单例先绑定后 ``setenv`` 被完全忽略，``emit_alert`` 落在 ``tmp`` 之外。
    已实测确认的**真实**影响是打开默认库即触发一次 ``_save()``、刷新
    ``saved_at``（实测只改这一个字段，``alerts``/``rules``/``hash_chain`` 逐字节不变）；
    至于测试告警是否真的会堆进默认库，取决于会话中单例被污染的时机——风险真实，
    但本机当前 136 条 ``execution_kernel`` 告警经 ``fired_at`` 核验属更早的真实
    执行失败（10-02 09:51~11:17、10-04 09:06），**不是**测试产物，此处不作夸大断言。

    而 ``tests/gateway/test_alerts_surface.py::test_alerts_honest_empty_when_no_failures``
    断言 ``count == 0`` 却从不断言"是哪一个 store 应答"，因此是一个**假通过**：它看到
    的 0 只反映隔离，不证明真实告警不存在。修复后实测（``test_f3_bidirectional_proof.py``）
    经 ``emit_alert`` 写入一条真实告警后，``/v1/alerts`` 的 ``count`` 由 0 变 1 ——
    该断言第一次具备失败能力。

    本 fixture 在每个用例前后都把 ``_default_store`` 戳回 ``None``，并把路径重定向到
    该用例的 ``tmp_path``。这是测试基础设施修复：不改生产默认值、不削弱告警语义、不改
    任何测试断言——只是让每个测试真正拥有一个自己的告警 store。
    """
    import src.observability.alerts.store as store_mod

    monkeypatch.setenv(
        store_mod.ALERTS_STORE_PATH_ENV, str(tmp_path / "alerts.json")
    )
    monkeypatch.setattr(store_mod, "_default_store", None)
    yield
    monkeypatch.setattr(store_mod, "_default_store", None)


@pytest.fixture(autouse=True)
def _isolate_metric_collector(monkeypatch):
    """隔离进程级指标单例 ``_metric_collector`` 的跨测试污染。

    根因（2026-11-06 实测复现）：``src/observability/metrics.py:802`` 的
    ``_metric_collector`` 是模块级单例，请求总数与 5xx 计数在**整个 pytest 进程**内
    累积，任何一次 ``monkeypatch`` 都无法把它归零。``tests/gateway`` 里那些有意制造
    失败路径的用例累计出 ``error_percent = 5.26``（19 次请求 / 1 次 5xx）。随后
    ``tests/observability/test_alert_store_isolation.py::test_honest_empty_assertion_has_teeth``
    启动 app 时，lifespan 的告警评估线程在第一批 tick 就用这份**陈旧**指标判定
    ``err_rate_high`` 成立，把一条真实告警写进该用例刚隔离出来的 store，于是
    ``before.json()["count"] == 0`` 变成 1。

    复现命令（修复前，最小组合）::

        pytest tests/gateway tests/observability tests/test_quota_accounting.py \
               tests/test_providers.py tests/test_model_pricing.py \
               tests/test_model_routing_live_path.py -q
        -> 1 failed, 250 passed
           FAILED tests/observability/test_alert_store_isolation.py::test_honest_empty_assertion_has_teeth
           AssertionError: {'count': 1, 'alert_type': 'error_rate', ...}

    修复方式是**测试隔离**，不是改断言：每个用例前后把单例戳回 ``None``。
    生产语义不变——单例本就应当在整个进程生命周期内累积指标。
    """
    import src.observability.metrics as metrics_module

    monkeypatch.setattr(metrics_module, "_metric_collector", None)


@pytest.fixture(autouse=True)
def _isolate_human_identity_store_env(monkeypatch, tmp_path):
    """不让开发者本机的身份库环境变量泄漏进测试。

    根因（2026-11-06 实测）：外层 shell 设了
    ``LIUHAO_HUMAN_IDENTITIES_DB=D:\\LiuHao-AI-OS\\data\\human_identities.sqlite3``
    与 ``LIUHAO_HUMAN_IDENTITIES_INTEGRITY_KEY``，而
    ``resolve_human_identity_store()``（src/kernels/identity/_persistence.py:822）
    在**每次调用时**读环境：只设置 ``..._FILE`` 的用例照样被选中 SQLite 后端，
    于是打开真实的用户身份库。密钥不匹配的行被拒（`test_seeded_humans_are_loaded`
    等 5 条读到 0 行），断言 `list_humans() == []` 的用例则真的拿到了真人记录
    （`test_a_malformed_file_loads_nothing_and_does_not_crash` 等 3 条）。

    这既让结果随机器环境漂移，也违反了"测试不碰真实用户数据"。修法：每个用例前
    清掉四个选择变量，并复位 `_global_manager` 单例（否则首个用例构造出的、绑定
    真实库的 manager 会一直服务后续用例）。需要存储的用例自己 `monkeypatch.setenv`
    显式声明。
    """
    import src.kernels.identity as identity_module

    for var in (
        "LIUHAO_HUMAN_IDENTITIES_BACKEND",
        "LIUHAO_HUMAN_IDENTITIES_DB",
        "LIUHAO_HUMAN_IDENTITIES_FILE",
        "LIUHAO_HUMAN_IDENTITIES_INTEGRITY_KEY",
    ):
        monkeypatch.delenv(var, raising=False)
    # 默认数据目录（身份库/日志）指向用例的 tmp，避免默认路径落到开发者真实
    # 的 %LOCALAPPDATA%\LiuHao\data 或仓库 data/。
    monkeypatch.setenv("LIUHAO_DATA_DIR", str(tmp_path / "data"))
    # 桌面壳的用户级配置（desktop.json）与日志目录**不跟随 LIUHAO_DATA_DIR**：
    # 它们的默认值直接拼 %LOCALAPPDATA%\LiuHao，测试若碰 desktop.repair /
    # desktop.app 就会写进真实目录（现场实测：真实 desktop.json 的 data_dir
    # 被写成 D:\cache\temp\pytest-of-Administrator\...，安装版启动后按它找数据，
    # 用户看到的就是「账号全没了」）。这里显式改成用例 tmp。
    monkeypatch.setenv(
        "LIUHAO_DESKTOP_CONFIG", str(tmp_path / "localapp" / "LiuHao" / "desktop.json")
    )
    monkeypatch.setenv("LIUHAO_LOG_DIR", str(tmp_path / "data" / "logs"))
    monkeypatch.setattr(identity_module, "_global_manager", None)
    # RBAC 也有两个进程级单例：``src.security.rbac._default_rbac``（安全包门面
    # ``get_rbac_manager``）与 ``src.security.rbac_store._rbac_store``（RBAC 内核）。
    # 两者都在**首次调用时**绑定 ``store_path``，并一直服务到进程结束；只要在
    # 数据根生效前（或某个用例清空 ``LIUHAO_DATA_DIR`` 后）触发过一次首次调用，
    # 余下所有用例都会沿用那条路径写盘。按用例复位，等于把“调用时求值”落到
    # 每一个用例，而不是全进程只求值一次。
    import src.security.rbac as rbac_module
    import src.security.rbac_store as rbac_store_module

    monkeypatch.setattr(rbac_module, "_default_rbac", None)
    monkeypatch.setattr(rbac_store_module, "_rbac_store", None)
    # 会话存储也是模块级单例：``src.ai.conversation_store._conversation_store``
    # 在首次 ``get_conversation_store()`` 时按当时的 ``LIUHAO_DATA_DIR`` 绑定
    # 库路径，之后永不重解析。若不按用例复位，先跑用例创建的 store 会一直
    # 服务后续用例，``read_history`` 就从错误的库读（断言 0 == N）。
    import src.ai.conversation_store as conversation_store_module

    monkeypatch.setattr(conversation_store_module, "_conversation_store", None)
    # 首次解析会把旧位置 ``~/.liuhao/rbac.json`` 迁移到数据根；测试若让它指向
    # 真实用户目录，每个用例都会把开发者机器上那份真实库复制进 tmp，并带着真实
    # 角色运行（既污染断言结果，也把真实库当测试夹具）。这里按用例指到 tmp。
    monkeypatch.setattr(
        rbac_module,
        "_legacy_store_path",
        lambda: tmp_path / "legacy-home" / ".liuhao" / "rbac.json",
    )


@pytest.fixture(autouse=True)
def _isolate_audit_store_per_test(tmp_path_factory, monkeypatch):
    """让每个用例自己的 ``AUDIT_DB_PATH`` 重定向真正生效（F1 根因修复）。

    与 ``_isolate_alert_store`` 同构，但审计侧多两个必须讲清的约束。

    为什么需要
    ----------
    ``src/kernels/audit/__init__.py:2196`` 的 ``get_audit_store()`` 是模块级单例
    ``_audit_store``，而 ``AuditStore.__init__``（:378-391）**只在构造时**读
    ``AUDIT_DB_PATH``，之后永不重新解析。``_isolate_audit_store`` 只在**会话开始**
    时复位一次单例，因此任何一个测试在这之后 ``monkeypatch.setenv("AUDIT_DB_PATH",
    ...)`` 都是空的——实测 24 个提到该变量的测试文件里，19 个是这种 env-only 情形。

    已实测确认的假通过：``tests/kernels/audit/test_audit.py:256-258`` 在
    ``setenv("AUDIT_DB_PATH", tmp_path/"global_audit.db")`` 之后只断言
    ``get_audit_store() is get_audit_store()``——只验同一性、不验路径。我把单例
    故意污染到另一个库之后该断言**依然通过**（取证：tests 目录下的临时探针），
    也就是说它对"重定向是否生效"完全无感。修复后本 fixture 让路径断言可成立。

    约束一：必须一并复位 executor fence
    ------------------------------------
    ``src/kernels/execution/fence.py`` 的 ``attach_default_executor_fence()``
    （:1166-1173）会把审计 SQLite 连接交给 ``SqliteExecutorLease`` 并装进**模块级
    全局** ``_DEFAULT_FENCE``。若只复位 ``_audit_store`` 而不碰 fence，fence 会继续
    持有旧连接的第二个强引用——那条连接一旦被回收或关闭，执行路径就会拿到坏连接。
    本 fixture 把 fence 退回 ``None``：``get_executor_fence()``（:1017）会重建一个
    ``InMemoryExecutorLease``，它**不持有 SQLite 连接**，因此与审计单例无耦合。
    这只影响测试环境的围栏后端，不改生产装配（生产在 boot 时调用
    ``attach_default_executor_fence``，测试不去触发它）。

    约束二：不要给 ``AuditStore`` 加 ``close()``、也不改 ``shutdown()`` 语义
    ------------------------------------------------------------------
    ``AuditStore.shutdown()``（:2099-2100）只翻转 lifecycle 标志、不关连接，
    这是既有事实，别把它改成"真关闭"——生产代码路径可能依赖它只是打标志。
    句柄方面实测**不会**线性泄漏：连续 25 次函数级复位后活着的 sqlite 连接恒为
    2（当前 store + fence 持有的首个 store），旧 store 由 CPython 引用计数及时
    回收，泄漏是 O(1) 有界而非 O(N)。所以此处不额外做连接关闭，避免引入新故障。

    这是测试基础设施修复：不改生产默认值、不削弱 fail-closed 的强制证据门禁
    （CRIT-1C 依旧对 HIGH/CRITICAL 动作做真实审计写入）、不改任何测试断言。
    """
    import src.kernels.audit as audit_module
    import src.kernels.audit.chain_key as chain_key_module
    import src.kernels.execution.fence as fence_module

    # The database gets its OWN directory from tmp_path_factory -- deliberately
    # NOT tmp_path. Several tests assert that tmp_path stays completely empty
    # (tests/kernels/network/test_network.py::TestHistoryPersistence::
    # test_default_bus_writes_nothing does `list(tmp_path.iterdir()) == []`, and
    # tests/kernels/identity/test_human_identity_persistence.py::
    # test_an_unconfigured_store_never_creates_a_file compares the listing before
    # and after). Pointing AUDIT_DB_PATH inside tmp_path made the CRIT-1C evidence
    # gate legitimately create audit_store.db{,-wal,-shm} there and broke both; a
    # subdirectory inside tmp_path still broke the first one. Staying entirely
    # outside tmp_path keeps the audit trail away from directories those tests
    # inspect, while remaining per-test isolated.
    audit_dir = tmp_path_factory.mktemp("lh_audit_test")
    monkeypatch.setenv("AUDIT_DB_PATH", str(audit_dir / "audit_store.db"))
    monkeypatch.setattr(audit_module, "_audit_store", None)
    monkeypatch.setattr(fence_module, "_DEFAULT_FENCE", None)
    # 链密钥是进程级缓存：AUDIT_DB_PATH 每个用例都换目录，缓存必须一起清，
    # 否则上一个用例生成的密钥会被带到下一个用例（路径已变，状态却仍“可用”）。
    chain_key_module.reset_cache()
    yield
    monkeypatch.setattr(audit_module, "_audit_store", None)
    monkeypatch.setattr(fence_module, "_DEFAULT_FENCE", None)
    chain_key_module.reset_cache()


@pytest.fixture(autouse=True)
def _isolate_knowledge_memory_dir(tmp_path, monkeypatch):
    """让知识记忆的落盘目录真正离开仓库，并让删除不再被宿主守卫拦截。

    根因
    ----
    ``src/knowledge/memory.py`` 的 ``InMemoryBackend._save_to_file()``（:352-356）与
    ``_load_from_file()``（:382）读 ``os.getenv('MEMORY_PERSIST_DIR', 'memory_data')``。
    这个环境变量**全仓库只有这两处出现**——没有任何测试设置过它。于是兜底值
    ``memory_data`` 生效，而它是 **CWD 相对**的：进程 CWD 是仓库根，所以测试把
    记忆数据写进了 ``D:\\LiuHao-AI-OS\\memory_data\\``。

    后果分两层，第二层是本 fixture 存在的主要理由
    ----------------------------------------------
    1. 数据层面：``memory_data/*.json`` 已被 ``.gitignore:43`` 覆盖（实测
       ``git check-ignore`` 命中），所以不会污染提交；但 ``goal_task_graph_memories.json``
       之类文件会在测试期间被真实创建/改写。
    2. **破坏力层面**（2026-10-04 实测）：``tests/test_knowledge_memory.py`` 的
       ``setup_method``/``teardown_method`` 用**同样的 CWD 相对路径**调 ``os.remove``，
       而本机（CodeBuddy CLI）有个 ``sitecustomize.py`` shim 会 monkeypatch
       ``os.remove``/``os.unlink``/``shutil.rmtree``：同一 turn 内删除次数累计到 50
       之后，每一次删除都抛 ``SystemExit(1)``（``SAFE_DELETE_BULK_CONFIRM_REQUIRED``）。
       ``SystemExit`` 落在 xunit 钩子里会**打坏 pytest 的 fixture finalizer 栈**
       （``_assert not self._finalizers_``），session 级 ``tmp_path_factory`` 卡死，
       之后**所有**测试共用同一个 tmp 目录 —— 全量跑时实测 28 failed + 16 errors
       由此而来，和测试之间毫无关系。

    修法
    ----
    重定向环境变量，让写入落在 ``tmp_path`` 下。``tmp_path`` 在本机位于
    ``D:\\cache\\temp``，正是 shim 的 bypass 目录（``tempfile.gettempdir()``），
    因此删除不再被拦截。生产默认值 ``memory_data`` **保持不变**。

    与 F1/F3 fixture 同构：只改测试环境，不改产品行为、不改任何测试断言。
    """
    monkeypatch.setenv("MEMORY_PERSIST_DIR", str(tmp_path / "memory_data"))
    yield
    monkeypatch.delenv("MEMORY_PERSIST_DIR", raising=False)


# ==================== Guard: tests must not delete files in the work tree ====
#
# 为什么不能只靠"人记住"
# ----------------------
# 2026-10-04 实测事故：``tests/test_knowledge_memory.py`` 的 setup/teardown 用
# **CWD 相对路径** ``os.path.join('memory_data', 'xxx.json')`` 调 ``os.remove``。
# 进程 CWD 是仓库根，所以它删的是 ``D:/LiuHao-AI-OS/memory_data/`` 里的真实文件。
# 后果不是"那个测试挂了"，而是**间接因果**：
#
#   1. 本机有个 sitecustomize shim 会 patch os.remove/os.unlink/shutil.rmtree，
#      同一 turn 内删除累计到 50 次后每次抛 SystemExit(1)；
#   2. SystemExit 落在 xunit 的 setup_method/teardown_method 里，打坏 pytest 的
#      fixture finalizer 栈（``_assert not self._finalizers_``）；
#   3. session 级 tmp_path_factory 卡死，把**同一个目录**分给后续所有测试；
#   4. 全量跑时 28 failed + 16 errors，彼此毫无关系，症状是 FileExistsError
#      和 ``assert 80 == 40``。
#
# 任何人看到这种失败，第一反应都是"宿主环境不稳 / 这测试 flaky"。真正的原因藏在
# 另一个文件的一次删除里，而 pytest 永远不会把两者联系起来。代价是团队开始怀疑
# 自己的开发环境，而不是怀疑代码。
#
# 本护栏把这种间接因果变成**就地失败**：谁删、删哪个路径、调用栈在哪一行，当场
# 报错。宁可误报（能看懂即可），不可漏报。
#
# 判据（fail-closed）
# ----
#   一次删除只在**归属已被确认**时放行。归属确认只有两种来源：
#     1. 路径（目录项本身 + 解析后的最终目标）都落在**声明的临时根**内：
#        ``tempfile.gettempdir()``（隐式），或 pytest ``--basetemp``（显式）；
#     2. 路径位于仓库工作树内，且已由 ``allow_repo_deletion`` 登记（一次一用）。
#
#   **仓库归属优先于临时根**：若仓库工作树本身就在临时根之下（例如把工作树
#   建在 %TEMP% 里），仓库内的删除仍然拒绝。旧实现先判"在不在 temp 根下"再判
#   仓库，于是 temp 根内的仓库整树放行 —— 2026-11-06 实测真的删掉了
#   README.md / oss-ecosystem/capabilities/*.yaml / src/knowledge/memory.py，
#   级联出 19 failed + 9 errors 假失败，且整轮结论作废。
#
#   其余一切（不在仓库、也不在任何声明的临时根内）一律拒绝：无法确认归属时
#   宁可让测试红，也不删除文件。
#
#   符号链接：同时检查目录项路径（不解析）与最终目标（解析后）。任一侧落在
#   仓库内即按仓库归属处理；放行到临时根要求两侧都在临时根内，堵住
#   "临时区里的链接指向仓库外/仓库内"这类用链接改变归属的绕过。
#
#   拒绝统一以 ``pytest.fail(..., pytrace=False)`` 呈现，信息固定以
#   「护栏拒绝（fail-closed）」开头并注明「这不是产品错误」，避免把测试清理
#   越界误读成产品缺陷。
#
# 顺带收益：仓库根的 ``audit_store.db``（冻结的 HC-01 证据）也落在拒绝面内。
# 任何测试试图删除/覆盖它都会当场变红，而不是悄悄毁掉证据。

# 这些 import 故意留在文件中段（E402）：整个 guard 代码块必须能**原样切出来**
# 给子进程 conftest 用（见 tests/test_cwd_delete_guard.py 的 _guard_source），
# 块内自带import 才能独立成立。挪到文件头会切不出可用的子进程 conftest。
import os as _guard_os  # noqa: E402
import shutil as _guard_shutil  # noqa: E402
import tempfile as _guard_tempfile  # noqa: E402
import threading as _guard_threading  # noqa: E402
import traceback as _guard_traceback  # noqa: E402
from pathlib import Path as _GuardPath  # noqa: E402

# ``pytest`` 在下方 "Async Fixtures" 段以模块级名字导入；本 guard 代码块位于那段
# 之后，因此运行时引用同一个名字是安全的（定义期不求值）。这样避免重复 import
# 触发 E402，也避免同一模块出现两个 pytest 名字。

#: 仓库工作树根：``tests/conftest.py`` 上溯一级。
REPO_ROOT = _GuardPath(__file__).resolve().parent.parent

#: pytest 临时根。本机为 D:\\cache\\temp，恰好也是宿主 safe-delete shim 的 bypass 目录。
#: 它是**隐式**的归属标记：路径落在它下面即视为测试自己的临时区。
_TEMP_ROOT = _GuardPath(_guard_tempfile.gettempdir()).resolve()

#: **显式**声明的临时根（目前只有 pytest ``--basetemp``）。显式声明比隐式 temp
#: 根更强：即使仓库工作树本身落在这些根里，该根内的删除也放行——运行者明确
#: 声明过"这块归 pytest 管"。仓库本身（或仓库的祖先）永远不允许被登记。
_DECLARED_SCRATCH_ROOTS: set = set()

#: 当前正在运行的用例 nodeid。由 ``_record_current_test`` 逐用例更新，
#: 仅为让报错信息能直接点名是哪个测试干的（调用栈之外的第二重定位）。
_CURRENT_NODEID = {"nodeid": "<session setup>"}

#: 同一次逻辑删除的内层路由深度（线程本地）。
#:
#: 真实踩到的双记账坑（2026-10-12 复现，tests/_zz_debug_probe 探针实证）：
#: 原生 ``pathlib.Path.unlink`` 的实现内部就是 ``os.unlink(self)``
#: （pathlib.py:1342），而本护栏把两个入口都 patch 了。于是一次逻辑删除被
#: 校验两次：外层 ``guarded_path_unlink`` 按白名单放行并**消费登记**，内层
#: ``guarded_unlink`` 看到"登记已被消费"又把**同一次**删除拦下——文件没删成，
#: teardown 的腐化检测再报"删除后该路径又存在"，残留探针继而让后续所有
#: ``register(_SELF_PROBE)`` 报"登记时已经存在"，级联出整片假失败。
#: 是否存在这层内层路由取决于宿主是否在导入期重绑过 ``Path.unlink``
#: （如某些 CLI 的 sitecustomize shim 直连原生实现、不经过 ``os.unlink``），
#: 所以这是**环境相关**的假失败：有 shim 的机器上绿，原生 CPython 上红。
#:
#: 对策：外层包装在校验通过后、调用原生实现期间把深度加一；``_check`` 见到
#: 深度非零即直通。``shutil.rmtree`` 同理——按目录登记放行后，其内部对每个
#: 子项的 ``os.unlink`` 都属于同一次已授权删除（登记时目录必须不存在，整棵
#: 子树都是本次运行创建的），不应再逐个校验。对**未授权**目标的行为不变：
#: 外层 ``_check`` 在深度为零时先行拦截，根本走不到原生实现。
_guard_state = _guard_threading.local()


def _strip_windows_extended_prefix(path: str) -> str:
    """去掉 Windows 扩展长度前缀（``\\\\?\\``），让路径能按同一坐标系比较。

    实测根因（2026-11-08，pytest 9.1.1）：``--basetemp`` 复用时，
    ``_pytest.pathlib.rm_rf`` 会先做 ``ensure_extended_length_path()``，
    把目标变成 ``\\\\?\\D:\\cache\\...\\basetemp``；而护栏登记的
    ``--basetemp`` 根是普通形式 ``D:\\cache\\...\\basetemp``。两者字符串不等，
    ``_is_under`` 判定失败 → 每个用例 setup 都被 fail-closed 拒绝，
    表现为 ``0 passed / 8799 error`` 的“整场灾难性失败”。
    """
    if path.startswith("\\\\?\\UNC\\"):
        # \\?\UNC\server\share -> \\server\share
        return "\\\\" + path[len("\\\\?\\UNC\\"):]
    if path.startswith("\\\\?\\"):
        return path[len("\\\\?\\"):]
    return path


def _norm_lexical(path) -> _GuardPath:
    """删除目标的**目录项**路径：绝对化，但不跟随符号链接。

    相对路径按**进程 CWD** 解析——这正是 2026-10-04 事故的成因：CWD 相对路径
    会静默地指向仓库内的真实文件，所以必须先用 ``abspath``。

    不解析符号链接是刻意的：``os.remove`` / ``unlink`` 删的是**目录项本身**。
    仓库里一个指向外部的链接，解析后落在仓库外，删掉它却仍然在删仓库内容。
    """
    return _GuardPath(
        _strip_windows_extended_prefix(
            _guard_os.path.abspath(_guard_os.fspath(path))
        )
    )


def _norm_target(path) -> _GuardPath:
    """删除目标**最终指向的对象**（跟随符号链接）。用于白名单键与归属确认。"""
    return _norm_lexical(path).resolve()


def _is_under(child: _GuardPath, parent: _GuardPath) -> bool:
    """``child`` 是否等于 ``parent`` 或位于其之下（Windows 大小写不敏感）。"""
    try:
        child.relative_to(parent)
        return True
    except ValueError:
        return False


#: 调用栈里**不展示**的路径片段。pytest / pluggy / site-packages 的框架帧对
#: "是谁干的"这个问题零信息量，却会把真正的肇事前缀埋掉。实测：14 帧里有 11 帧
#: 是 ``_pytest/runner.py`` 与 ``pluggy``，删掉它们之后肇事那一行立刻可见。
_STACK_NOISE = (
    "site-packages",
    _guard_os.sep + "_pytest" + _guard_os.sep,
    "pluggy",
)


def _describe_stack(skip_frames: int = 2, keep: int = 8) -> str:
    """取最近若干帧调用栈，去掉护栏自身与 pytest 框架的帧。

    保留顺序为**由外到内**（真实调用顺序），并在开头标注省略了多少帧，
    这样"从哪条路走过去的"和"在哪一行干的"两件事都能一眼看到。
    """
    frames = _guard_traceback.format_stack()
    # format_stack()[-1] 是发起删除的那一帧；去掉护栏内部的 skip_frames 帧。
    frames = frames[:-skip_frames] if skip_frames else frames
    kept = [f for f in frames if not any(n in f for n in _STACK_NOISE)]
    dropped = len(frames) - len(kept)
    if len(kept) > keep:
        # 保留最内层 keep 帧（离肇事点最近），但仍按由外到内输出。
        dropped += len(kept) - keep
        kept = kept[-keep:]
    header = "  ...（省略 %d 帧 pytest 框架/中间帧）...\n" % dropped if dropped else ""
    return header + "".join(kept).rstrip()


def _guard_violation(kind: str, lexical: _GuardPath, resolved: _GuardPath, *,
                     in_repo: bool, registered=None):
    """就地失败（fail-closed）：路径 + 判定 + 是谁 + 调用栈 + 怎么豁免。

    报文固定以「护栏拒绝（fail-closed）」开头，并明确写「这不是产品错误」。
    没有这两句，一条护栏拒绝会被读成产品缺陷，排查方向直接跑偏。
    """
    if in_repo:
        reason = (
            "路径位于仓库工作树内（或符号链接指向仓库内）：%s"
            % os.path.relpath(str(lexical), str(REPO_ROOT))
        )
    else:
        reason = (
            "路径不在任何已声明的临时根内（仓库之外、temp 根之外），"
            "无法确认归属，按 fail-closed 拒绝"
        )
    lines = [
        "",
        "=" * 78,
        "护栏拒绝（fail-closed）：测试试图删除归属无法确认的路径。",
        "=" * 78,
        "  删除接口  : %s" % kind,
        "  目录项路径: %s" % lexical,
        "  解析后目标: %s" % resolved,
        "  当前用例  : %s" % _CURRENT_NODEID["nodeid"],
        "  拒绝原因  : %s" % reason,
        "",
        "这不是产品错误：产品代码没有报错，是测试的清理目标越界了；",
        "本护栏无法确认该路径归属于本次测试，因此拒绝删除而不是放行。",
        "",
        "判据：",
        "  1) 目录项与解析后目标都在 tempfile.gettempdir() (%s)" % _TEMP_ROOT,
        "     或 pytest --basetemp 声明的临时根内 → 放行；",
        "  2) 路径在仓库工作树 (%s) 内 → 默认拒绝，" % REPO_ROOT,
        "     除非用 allow_repo_deletion 登记过（一次登记只放行一次删除）；",
        "  3) 仓库归属优先于临时根：仓库建在 temp 根里，仓库内删除仍然拒绝；",
        "  4) 其余一切（仓库外、任何临时根外）→ 拒绝：无法确认归属时不删。",
        "",
    ]
    if in_repo:
        lines += [
            "如果这个路径确实是**本次测试自己创建**的产物，",
            "请用 allow_repo_deletion 显式登记并写明原因，例如：",
            "",
            "    def test_x(self, allow_repo_deletion):",
            "        allow_repo_deletion.register(CORPUS / '_probe.yaml',",
            "            reason='注入探针，验证门禁会变红')",
            "        (CORPUS / '_probe.yaml').unlink()",
            "",
        ]
    else:
        lines += [
            "如果这是测试自己创建的临时数据，请改用 pytest 的 tmp_path /",
            "tmp_path_factory（它们位于 tempfile.gettempdir() 之下）；",
            "不要用「看起来像临时目录」的路径来推断安全。",
            "",
        ]
    if registered:
        lines += [
            "注意：该路径**已经**登记在白名单里，但登记已被消费过",
            "（reason=%r）。一次登记只允许一次删除；请重新登记。" % registered,
            "",
        ]
    lines += ["调用栈（最近的帧）：", _describe_stack()]
    return pytest.fail("\n".join(lines), pytrace=False)


class _RepoDeletionAllowlist:
    """显式豁免登记表：只放行"本次运行中测试自己创建"的路径。

    三条硬规则，缺一不可：

    1. **必须带原因**，且非空。照抄 ``tests/test_guardrail_scripts.py`` 里
       ``DIAGNOSTIC_REPORTERS`` 的做法：没有原因的豁免等于没有豁免——下一个人
       无法判断这条是"深思熟虑"还是"懒得改"。
    2. **登记时路径必须不存在**。已存在的路径说明它不是本次运行创建的，
       登记它就是借护栏之手删除仓库的既有内容（可能还是被 git 跟踪的文件）。
    3. **一次登记只消费一次**。删除发生后条目即失效，防止一个登记被复用来
       连续删一批文件。
    """

    def __init__(self):
        #: key = 绝对路径字符串，value = 条目 dict
        self._entries: dict = {}

    def register(self, path, reason: str) -> "_GuardPath":
        """登记一个"我合法地创建并删除这个路径"。返回规范化后的绝对路径。"""
        if not isinstance(reason, str) or not reason.strip():
            raise AssertionError(
                "allow_repo_deletion.register(%s) 缺少原因：reason 必须是非空字符串。"
                "没有原因的豁免无法被复核，等于没有豁免。" % (path,)
            )
        lexical = _norm_lexical(path)
        target = lexical.resolve()
        lexical_in_repo = _is_under(lexical, REPO_ROOT)
        resolved_in_repo = _is_under(target, REPO_ROOT)
        if lexical_in_repo != resolved_in_repo:
            # 符号链接两侧归属不一致：链接是仓库的一部分，最终目标却不是（或相反）。
            # 登记它等于允许借链接改写归属，直接拒绝（fail-closed）。
            raise AssertionError(
                "allow_repo_deletion.register(%s)：符号链接两侧归属不一致"
                "（目录项=%s，解析后=%s）。这种路径不允许登记，删除会被拒绝。"
                % (path, lexical, target)
            )
        if not resolved_in_repo:
            raise AssertionError(
                "allow_repo_deletion.register(%s)：该路径不在仓库工作树 (%s) 之内，"
                "删除它本来就被放行，无需登记。登记是给「必须删仓库内路径」的用例用的。"
                % (target, REPO_ROOT)
            )
        key = str(target)
        if key in self._entries:
            raise AssertionError(
                "路径 %s 已被登记（reason=%r）。一次登记只允许一次删除；"
                "请在删除后重新登记，或修正为一次登记一次删除。"
                % (target, self._entries[key]["reason"])
            )
        if _GuardPath(key).exists() or _GuardPath(key).is_symlink():
            raise AssertionError(
                "拒绝登记 %s：登记时该路径**已经存在**，因此它不是本次运行中"
                "由测试创建的。豁免机制只放行「测试自己创建并跑完即删」的产物；"
                "已存在的路径（尤其是 git 跟踪的文件）不在豁免范围内。"
                % target
            )
        self._entries[key] = {
            "reason": reason,
            "nodeid": _CURRENT_NODEID["nodeid"],
            "consumed": False,
        }
        return target

    def consume(self, target: _GuardPath) -> None:
        """标记一条登记已被使用（由护栏在放行时调用）。"""
        entry = self._entries.get(str(target))
        if entry is not None:
            entry["consumed"] = True

    def lookup(self, target: _GuardPath):
        return self._entries.get(str(target))

    def forget(self, target: _GuardPath) -> None:
        self._entries.pop(str(target), None)

    def finalize(self) -> None:
        """用例结束时的腐化检测：登记了却没删（或删了又冒出来）都是登记有问题。

        无论检查是否通过都清空登记表：检查用的 ``pytest.fail`` 会中断用例，
        残留条目若不清掉，会让 fixture 的 teardown 再报一次同样的错，把真实
        失败现场糊掉两层。
        """
        problems = []
        try:
            for key, entry in self._entries.items():
                path = _GuardPath(key)
                if not entry["consumed"]:
                    problems.append(
                        "  %s\n    reason=%r\n    登记后从未被删除 —— 白名单条目腐化了："
                        "用例声称会清理这个路径但没有删。请补上删除，或撤掉登记。"
                        % (path, entry["reason"])
                    )
                elif path.exists() or path.is_symlink():
                    problems.append(
                        "  %s\n    reason=%r\n    登记的删除发生后该路径又存在了 —— "
                        "说明删除的不是本次运行创建的那一份，或删除后被重新创建。"
                        % (path, entry["reason"])
                    )
            if problems:
                pytest.fail(
                    "\nallow_repo_deletion 白名单腐化检测失败（用例 %s）：\n%s\n"
                    % (_CURRENT_NODEID["nodeid"], "\n".join(problems)),
                    pytrace=False,
                )
        finally:
            self._entries.clear()


#: 当前生效的白名单登记表。由 ``allow_repo_deletion`` fixture 逐用例替换，
#: 保证登记表不跨用例泄漏（跨用例共享会让"上一个用例登记了没删"变成
#: "下一个用例莫名报已被登记"这类极难定位的假失败）。
_ACTIVE_ALLOWLIST = [_RepoDeletionAllowlist()]


def _register_scratch_root(path) -> None:
    """登记一个由运行者**显式声明**的临时根（目前只有 pytest ``--basetemp``）。

    fail-closed 的第二道闸：登记本身也可能被滥用。若声明的是仓库本身、或仓库的
    祖先目录，登记它等于把整棵树划进放行区，因此直接忽略这条声明。
    """
    try:
        root = _GuardPath(str(path)).resolve()
    except OSError:
        return
    if root == REPO_ROOT or _is_under(REPO_ROOT, root):
        return
    _DECLARED_SCRATCH_ROOTS.add(root)


@pytest.fixture
def allow_repo_deletion():
    """显式豁免"本次测试自己创建、跑完即删"的仓库内路径。

    用法::

        def test_x(self, allow_repo_deletion):
            probe = allow_repo_deletion.register(
                REPO_ROOT / "oss-ecosystem" / "capabilities" / "_probe.yaml",
                reason="注入违规探针，验证门禁有牙；副本已改用 tmp_path，本条为历史遗留",
            )
            probe.write_text("...")
            probe.unlink()          # 这次删除被放行，且消费掉该登记

    没有它，任何删除仓库内路径的尝试都会 ``pytest.fail`` 并打印路径与调用栈。

    登记表是**每用例独立**的（不是模块级共享 dict）：否则一个用例登记了却没删，
    残留条目会让下一个用例的登记莫名其妙地报"已被登记"。独立性本身就是护栏的一部分。
    """
    allowlist = _RepoDeletionAllowlist()
    previous = _ACTIVE_ALLOWLIST[0]
    _ACTIVE_ALLOWLIST[0] = allowlist
    try:
        yield allowlist
    finally:
        _ACTIVE_ALLOWLIST[0] = previous
    # finalize 只做**检查**，清空由 _entries 的实例生命周期保证。
    allowlist.finalize()


@pytest.fixture(autouse=True)
def _record_current_test(request):
    """把当前用例的 nodeid 记进护栏的全局，供删除拦截信息点名。"""
    previous = _CURRENT_NODEID["nodeid"]
    _CURRENT_NODEID["nodeid"] = request.node.nodeid
    yield
    _CURRENT_NODEID["nodeid"] = previous


@pytest.fixture(scope="session", autouse=True)
def _forbid_deleting_repository_files(tmp_path_factory):
    """拦截 os.remove / os.unlink / os.rmdir / pathlib.Path.unlink/rmdir /
    shutil.rmtree 对归属无法确认的路径的删除（fail-closed）。

    判据与设计理由见本文件上方"Guard: tests must not delete files in the work
    tree"注释块（含 2026-10-04 那次 28 failed + 16 errors 的完整因果链）。

    为什么必须连 ``pathlib.Path.unlink`` 一起拦（实测踩到的坑）
    --------------------------------------------------------
    只 patch ``os.unlink`` **不够**。本机（CodeBuddy CLI）的 ``sitecustomize.py``
    shim 在导入期就重绑了 ``pathlib.Path.unlink = _safe_path_unlink``，而该函数在
    放行分支里直连原生实现 ``_orig_path_unlink``，**不经过** ``os.unlink``。
    于是 ``Path(...).unlink()`` 可以从 ``os.unlink`` 的补丁下方溜过去，护栏对它
    完全失明——而仓库里现役的测试恰恰大量用 ``Path.unlink()``
    （``tests/test_oss_ecosystem_entries.py:163``、``tests/test_knowledge_memory.py:48``）。
    这是本护栏第一版实测漏掉的洞，由 ``tests/test_cwd_delete_guard.py`` 的
    ``test_path_unlink_is_also_guarded`` 钉住。
    同理 ``Path.rmdir`` 与 ``shutil.rmtree`` 都在重绑之列，一并拦。

    豁免：``allow_repo_deletion`` 显式登记 + 必填原因 + 一次性消费 + 腐化检测。
    """
    import pathlib as _guard_pathlib

    originals = {
        "os.remove": _guard_os.remove,
        "os.unlink": _guard_os.unlink,
        "os.rmdir": _guard_os.rmdir,
        "pathlib.Path.unlink": _guard_pathlib.Path.unlink,
        "pathlib.Path.rmdir": _guard_pathlib.Path.rmdir,
        "shutil.rmtree": _guard_shutil.rmtree,
    }

    def _declared(p):
        return any(_is_under(p, root) for root in _DECLARED_SCRATCH_ROOTS)

    def _scratch(p):
        return _is_under(p, _TEMP_ROOT) or _declared(p)

    def _check(kind, path):
        if getattr(_guard_state, "depth", 0):
            # 同一次已授权删除的内层路由（Path.unlink→os.unlink、rmtree 子项）：
            # 外层包装已完成校验/消费登记，内层直通，避免一次删除被记两笔账。
            return
        # 目录项与最终目标都要看：删除作用在目录项上，但链接可能把"安全路径"
        # 指向仓库内，或把"仓库内路径"指向外部。任一侧越界都按越界处理。
        lexical = _norm_lexical(path)
        resolved = lexical.resolve()
        in_repo = _is_under(lexical, REPO_ROOT) or _is_under(resolved, REPO_ROOT)

        if in_repo:
            # 显式声明的临时根优先（例如 --basetemp 落在工作树内）：那里的内容
            # 是 pytest 自己管理的，不是仓库资产。仅凭"在 temp 根下"不够。
            if _declared(lexical) and _declared(resolved):
                return
            allowlist = _ACTIVE_ALLOWLIST[0]
            entry = allowlist.lookup(resolved)
            if entry is not None and not entry["consumed"]:
                allowlist.consume(resolved)
                return
            _guard_violation(
                kind, lexical, resolved, in_repo=True, registered=entry
            )

        # 仓库之外：只有两侧都在声明的临时根内才放行；否则归属无法确认 → 拒绝。
        if _scratch(lexical) and _scratch(resolved):
            return
        _guard_violation(kind, lexical, resolved, in_repo=False)

    def _call_original(fn, *args, **kwargs):
        """调用被包装的原生实现；期间深度加一，内层路由（见 _guard_state）直通。"""
        _guard_state.depth = getattr(_guard_state, "depth", 0) + 1
        try:
            return fn(*args, **kwargs)
        finally:
            _guard_state.depth -= 1

    def guarded_remove(path, *args, **kwargs):
        if kwargs.get("dir_fd") is None and not args:
            _check("os.remove", path)
        return _call_original(originals["os.remove"], path, *args, **kwargs)

    def guarded_unlink(path, *args, **kwargs):
        if kwargs.get("dir_fd") is None and not args:
            _check("os.unlink", path)
        return _call_original(originals["os.unlink"], path, *args, **kwargs)

    def guarded_rmdir(path, *args, **kwargs):
        # os.rmdir 是旧版护栏漏掉的删除接口：Path.rmdir 被拦住了，裸 os.rmdir
        # 没有，一个空仓库目录可以被它悄悄删掉。
        if kwargs.get("dir_fd") is None and not args:
            _check("os.rmdir", path)
        return _call_original(originals["os.rmdir"], path, *args, **kwargs)

    def guarded_path_unlink(self, missing_ok=False):
        _check("pathlib.Path.unlink", self)
        return _call_original(
            originals["pathlib.Path.unlink"], self, missing_ok=missing_ok
        )

    def guarded_path_rmdir(self):
        _check("pathlib.Path.rmdir", self)
        return _call_original(originals["pathlib.Path.rmdir"], self)

    def guarded_rmtree(path, *args, **kwargs):
        # rmtree 只接受路径参数（无 dir_fd），无条件校验。
        _check("shutil.rmtree", path)
        return _call_original(originals["shutil.rmtree"], path, *args, **kwargs)

    _guard_os.remove = guarded_remove
    _guard_os.unlink = guarded_unlink
    _guard_os.rmdir = guarded_rmdir
    _guard_shutil.rmtree = guarded_rmtree
    _guard_pathlib.Path.unlink = guarded_path_unlink
    _guard_pathlib.Path.rmdir = guarded_path_rmdir
    # --basetemp 是运行者**显式声明**的临时根，登记为"有主"区。
    # 必须赶在访问 tmp_path_factory.basetemp 之前登记：该属性首次求值时会
    # rm_rf 旧 basetemp，若旧目录恰在仓库内，迟一步登记就会触发护栏拒绝。
    _DECLARED_SCRATCH_ROOTS.clear()
    given = getattr(tmp_path_factory, "_given_basetemp", None)
    if given is not None:
        _register_scratch_root(given)
    basetemp = getattr(tmp_path_factory, "basetemp", None)
    if basetemp is not None:
        _register_scratch_root(basetemp)
    try:
        yield
    finally:
        _guard_os.remove = originals["os.remove"]
        _guard_os.unlink = originals["os.unlink"]
        _guard_os.rmdir = originals["os.rmdir"]
        _guard_shutil.rmtree = originals["shutil.rmtree"]
        _guard_pathlib.Path.unlink = originals["pathlib.Path.unlink"]
        _guard_pathlib.Path.rmdir = originals["pathlib.Path.rmdir"]
        _DECLARED_SCRATCH_ROOTS.clear()


# ==================== Console auth (bearer token) ====================


@pytest.fixture
def auth_headers():
    """A real bearer token, exactly what the console carries after login.

    Since the auth gate was added to ``include_router`` (chat / dashboard /
    roster / profile / knowledge), those endpoints answer 401 without one. Test
    clients that exercise their *behaviour* -- not their access control -- pass
    these headers as a default; tests that assert the 401 itself simply omit
    them (see ``tests/test_gateway_auth.py``).

    Minted through the same ``get_jwt_handler()`` the gateway validates with, so
    this exercises the real signature check rather than stubbing it out.
    """
    from src.security import get_jwt_handler

    token, _payload = get_jwt_handler().create_token(
        subject="test-human", metadata={"kind": "human", "scope": "L0"}
    )
    return {"Authorization": f"Bearer {token}"}


# ==================== Test Path Configuration ====================

# Ensure test output directories exist
import os
from pathlib import Path

OUTPUT_DIRS = [
    "tests/output",
    "tests/output/coverage_html",
    "tests/data",
    "tests/fixtures",
]

for dir_path in OUTPUT_DIRS:
    Path(dir_path).mkdir(parents=True, exist_ok=True)


@pytest.fixture(autouse=True)
def _isolate_model_config_per_test(tmp_path_factory, monkeypatch):
    """隔离模型配置中心（SQLite + 加密密钥）到每个用例独占的临时目录。

    模型管理是新增能力：默认库路径是仓库下的 ``data/model_registry.sqlite3``。
    本机真实添加过模型后，如果测试直接读默认路径，全量测试会被本机配置
    "污染"（例如聊天用例拿到真实云端 provider）。同理，加密 key 也绝不能
    在跑测试时落到仓库 ``data/`` 里 —— 这里显式用固定测试口令，避免生成密钥文件。
    """
    import src.ai.model_config as model_config_module

    base = tmp_path_factory.mktemp("lh_model_config")
    monkeypatch.setenv(
        model_config_module.MODEL_CONFIG_DB_ENV, str(base / "model_registry.sqlite3")
    )
    monkeypatch.setenv(model_config_module.MODEL_CONFIG_KEY_ENV, "pytest-model-config-key")
    monkeypatch.setenv(
        model_config_module.MODEL_CONFIG_KEY_FILE_ENV, str(base / "model_config.key")
    )
    # 旧 .env 迁移是"首次启动"行为：测试默认关闭，避免仓库 .env 里的
    # AI_PROVIDER_* 被搬进每个临时库、污染"空注册表"断言；迁移本身有独立用例。
    monkeypatch.setenv(model_config_module.MODEL_CONFIG_MIGRATE_ENV, "0")
    model_config_module.reset_model_config_store()
    yield
    model_config_module.reset_model_config_store()


@pytest.fixture(autouse=True)
def _reset_rate_limiter_singleton():
    """复位进程级限流单例，隔离「前一个用例把桶打空」的跨用例污染。

    ``RateLimitMiddleware`` 走的是 ``get_rate_limiter()`` 的**模块级单例**
    （src/gateway/rate_limiter.py:333），桶按 token / 来源 IP 共享且容量有限。
    全量跑时，前面的批量请求会把某个 token 的桶打空；随后 ``/v1/dashboard``、
    ``/v1/roster`` 等受保护路径的请求就会拿到 429「请求过于频繁」。

    run 42283620800 的 3.12 腿就是这样红的（5 个 provider 诚实面用例全 429），
    而 3.11 腿只是恰好错开了 60 秒的补桶窗口 —— 同一份代码、两条腿一绿一红，
    根因是用例间共享状态，不是被测代码本身。限流用例在**单个用例内部**连续
    请求，逐用例复位不影响它们真正要验证的行为。
    """
    import src.gateway.rate_limiter as rate_limiter_module

    rate_limiter_module._default_limiter = None
    yield
    rate_limiter_module._default_limiter = None


@pytest.fixture(autouse=True)
def _isolate_knowledge_vector_store(tmp_path, monkeypatch):
    """隔离知识库向量库到每个用例独占的临时文件，并复位进程级单例。"""
    import src.knowledge.documents as knowledge_documents
    import src.knowledge.vector_store as vector_store_module

    monkeypatch.setenv(
        vector_store_module.KNOWLEDGE_DB_ENV,
        str(tmp_path / "knowledge_vectors.sqlite3"),
    )
    vector_store_module.reset_vector_store()
    knowledge_documents.reset_knowledge_service()
    yield
    vector_store_module.reset_vector_store()
    knowledge_documents.reset_knowledge_service()


@pytest.fixture(scope="session", autouse=True)
def _reap_localappdata_test_copies():
    """兜底清理测试期间新造在 ``%LOCALAPPDATA%\\LiuHao`` 下的数据目录副本。

    这些 ``data.after-smoke-*`` / ``data.bak-*`` 目录是整目录复制出来的数据，
    里面可能同时含凭据库、身份库与模型主密钥。测试不应该把这类明文留在
    用户目录里。这里只在**本次会话新出现**的目录上动手（预先存在的残留交给
    ``scripts/cleanup_localappdata_test_leftovers.py``，它要求人工确认），
    并且逐目录登记为临时根，确保与删除护栏的判定一致。
    """
    import os
    import shutil

    base = os.environ.get("LOCALAPPDATA")
    root = Path(base) / "LiuHao" if base else None
    before: set = set()
    if root is not None and root.is_dir():
        before = {p.name for p in root.iterdir() if p.is_dir()}

    yield

    if root is None or not root.is_dir():
        return
    prefixes = ("data.after-smoke-", "data.bak-")
    for candidate in root.iterdir():
        if not candidate.is_dir() or candidate.name in before:
            continue
        if not candidate.name.startswith(prefixes):
            continue
        _register_scratch_root(candidate)
        shutil.rmtree(candidate, ignore_errors=True)


# ==================== 本地推理白名单端口上的假上游 ====================


#: 产品策略（``src/ai/net_guard.py::LOCAL_INFERENCE_PORTS``）：带 Key 的 http
#: 只放行回环 + 这四个端口。假上游若绑随机端口，用例实际证明的是"策略会拒绝
#: 随机端口"，与它声称验证的行为相反——所以这里只在白名单里取端口，
#: 产品策略一字不动。
_LOOPBACK_INFERENCE_PORTS = (11434, 1234, 8000, 8080)
_LOOPBACK_LEASES: dict = {}
_LOOPBACK_LOCK = _guard_threading.Lock()


def _loopback_port_is_listening(port: int) -> bool:
    """端口上已经有进程在监听吗（例如本机真开着 Ollama / LM Studio）。"""
    import socket as _socket

    try:
        with _socket.create_connection(("127.0.0.1", port), timeout=0.3):
            return True
    except OSError:
        return False


def _bind_loopback_inference_port(handler_cls, label: str):
    """在 11434/1234/8000/8080 里挑一个空闲端口起假上游。

    占用处理与 ``scripts/acceptance/local_attack_surface_e2e.py`` 一致：

    * 本进程内已被其它用例占用 → 换下一个（占用表由 ``_LOOPBACK_LOCK`` 保护）；
    * 被本机其它进程监听（真的有 Ollama 在跑）→ 换下一个，绝不抢占；
    * 四个都不可用 → 抛 ``RuntimeError`` 并说明原因，绝不静默退回随机端口
      ——那会把用例外面的策略拒绝误当成被测行为，变成"永远红"或"永远绿"。
    """
    from http.server import ThreadingHTTPServer as _ThreadingHTTPServer

    problems = []
    with _LOOPBACK_LOCK:
        for port in _LOOPBACK_INFERENCE_PORTS:
            holder = _LOOPBACK_LEASES.get(port)
            if holder:
                problems.append(f"{port}：被本进程的 {holder} 占用")
                continue
            if _loopback_port_is_listening(port):
                problems.append(f"{port}：已被本机其它进程监听")
                continue
            try:
                httpd = _ThreadingHTTPServer(("127.0.0.1", port), handler_cls)
            except OSError as exc:
                reason = getattr(exc, "strerror", None) or str(exc)
                problems.append(f"{port}：绑定失败 {reason}")
                continue
            httpd.daemon_threads = True
            _LOOPBACK_LEASES[port] = label
            return httpd, port
    raise RuntimeError(
        "白名单端口 11434/1234/8000/8080 全部不可用，无法在不动产品策略的前提下"
        "验证本机推理端点：" + "；".join(problems) + "。请释放端口后重跑"
        "（`netstat -ano | findstr \":11434\"` 可查 PID）。"
    )


@pytest.fixture
def loopback_inference_server():
    """在本地推理白名单端口上起假上游；退出时自动关停并释放端口。

    用法：``with loopback_inference_server(_Handler, "label") as server:``，
    ``server.server_address[1]`` 就是白名单内的真实端口。
    """
    import contextlib as _contextlib

    @_contextlib.contextmanager
    def _serve(handler_cls, label: str = "stub"):
        httpd, port = _bind_loopback_inference_port(handler_cls, label)
        thread = _guard_threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        try:
            yield httpd
        finally:
            httpd.shutdown()
            httpd.server_close()
            thread.join(timeout=5)
            with _LOOPBACK_LOCK:
                if _LOOPBACK_LEASES.get(port) == label:
                    del _LOOPBACK_LEASES[port]

    return _serve


# ==================== Export ====================

__all__ = [
    "pytest_configure",
    "pytest_collection_modifyitems", 
    "pytest_runtest_setup",
    "pytest_addoption",
    "loopback_inference_server",
]