"""
pytest configuration for LiuHao AI OS

Configures pytest with custom options, markers, and fixtures.
"""

import sys
from pathlib import Path

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
    import src.kernels.memory as memory_module

    mem_dir = tmp_path_factory.mktemp("lh_memory_test")
    monkeypatch.setenv("MEMORY_DB_PATH", str(mem_dir / "memory_store.db"))
    monkeypatch.setattr(memory_module, "_global_kernel", None)
    yield
    monkeypatch.setattr(memory_module, "_global_kernel", None)


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
    yield
    monkeypatch.setattr(audit_module, "_audit_store", None)
    monkeypatch.setattr(fence_module, "_DEFAULT_FENCE", None)


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
# 判据
# ----
#   放行：位于 ``tempfile.gettempdir()``（本机 D:\\cache\\temp）之下——
#         pytest 的 tmp_path / tmp_path_factory / basetemp 都在这里；
#         以及完全在仓库工作树之外的路径（不属于本护栏管辖范围）。
#   拒绝：位于仓库工作树之内（仓库根 = 本文件上溯一级）的任何路径，
#         除非该路径已由测试通过 ``allow_repo_deletion`` 显式登记。
#
# 顺带收益：仓库根的 ``audit_store.db``（冻结的 HC-01 证据）也落在拒绝面内。
# 任何测试试图删除/覆盖它都会当场变红，而不是悄悄毁掉证据。

# 这些 import 故意留在文件中段（E402）：整个 guard 代码块必须能**原样切出来**
# 给子进程 conftest 用（见 tests/test_cwd_delete_guard.py 的 _guard_source），
# 块内自带import 才能独立成立。挪到文件头会切不出可用的子进程 conftest。
import os as _guard_os  # noqa: E402
import shutil as _guard_shutil  # noqa: E402
import tempfile as _guard_tempfile  # noqa: E402
import traceback as _guard_traceback  # noqa: E402
from pathlib import Path as _GuardPath  # noqa: E402

# ``pytest`` 在下方 "Async Fixtures" 段以模块级名字导入；本 guard 代码块位于那段
# 之后，因此运行时引用同一个名字是安全的（定义期不求值）。这样避免重复 import
# 触发 E402，也避免同一模块出现两个 pytest 名字。

#: 仓库工作树根：``tests/conftest.py`` 上溯一级。
REPO_ROOT = _GuardPath(__file__).resolve().parent.parent

#: pytest 临时根。本机为 D:\\cache\\temp，恰好也是宿主 safe-delete shim 的 bypass 目录。
_TEMP_ROOT = _GuardPath(_guard_tempfile.gettempdir()).resolve()

#: 额外放行的临时根。若 pytest 启动时带了 ``--basetemp``（不在 gettempdir 之下），
#: 该目录由 ``_forbid_deleting_repository_files`` 在会话开始时登记进来。
_EXTRA_TEMP_ROOTS: set = set()

#: 当前正在运行的用例 nodeid。由 ``_record_current_test`` 逐用例更新，
#: 仅为让报错信息能直接点名是哪个测试干的（调用栈之外的第二重定位）。
_CURRENT_NODEID = {"nodeid": "<session setup>"}


def _norm_target(path) -> _GuardPath:
    """把删除目标解析成绝对路径。

    相对路径按**进程 CWD** 解析——这正是事故的成因：CWD 相对路径会静默地指向
    仓库内的真实文件，所以这里必须用 ``abspath`` 而不是要求调用方传绝对路径。
    """
    return _GuardPath(_guard_os.path.abspath(_guard_os.fspath(path))).resolve()


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


def _repo_deletion_violation(kind: str, target: _GuardPath, registered):
    """就地失败：路径 + 是谁 + 调用栈 + 怎么豁免。``pytest.fail`` 不返回。"""
    rel = os.path.relpath(str(target), str(REPO_ROOT))
    lines = [
        "",
        "=" * 78,
        "护栏拦截：测试试图删除仓库工作树内的文件/目录。",
        "=" * 78,
        "  删除接口  : %s" % kind,
        "  完整路径  : %s" % target,
        "  仓库内位置: %s" % rel,
        "  当前用例  : %s" % _CURRENT_NODEID["nodeid"],
        "",
        "判据：允许删除 tempfile.gettempdir() (%s) 之下的一切" % _TEMP_ROOT,
        "      （tmp_path / tmp_path_factory / basetemp 都在那里）；",
        "      仓库工作树 (%s) 内的一切默认拒绝。" % REPO_ROOT,
        "",
        "如果这个路径确实是**本次测试自己创建**的产物，",
        "请用 allow_repo_deletion 显式登记并写明原因，例如：",
        "",
        "    def test_x(self, allow_repo_deletion):",
        "        allow_repo_deletion.register(CORPUS / '_probe.yaml',",
        "            reason='注入探针，验证门禁会变红')",
        "        (CORPUS / '_probe.yaml').unlink()",
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
        target = _norm_target(path)
        if not _is_under(target, REPO_ROOT):
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
    """拦截 os.remove / os.unlink / pathlib.Path.unlink/rmdir / shutil.rmtree
    对仓库工作树内路径的删除。

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
        "pathlib.Path.unlink": _guard_pathlib.Path.unlink,
        "pathlib.Path.rmdir": _guard_pathlib.Path.rmdir,
        "shutil.rmtree": _guard_shutil.rmtree,
    }

    def _check(kind, path):
        target = _norm_target(path)
        if _is_under(target, _TEMP_ROOT):
            return
        for root in _EXTRA_TEMP_ROOTS:
            if _is_under(target, root):
                return
        if not _is_under(target, REPO_ROOT):
            return  # 仓库之外：不属本护栏管辖
        allowlist = _ACTIVE_ALLOWLIST[0]
        entry = allowlist.lookup(target)
        if entry is not None and not entry["consumed"]:
            allowlist.consume(target)
            return
        _repo_deletion_violation(kind, target, entry)

    def guarded_remove(path, *args, **kwargs):
        if kwargs.get("dir_fd") is None and not args:
            _check("os.remove", path)
        return originals["os.remove"](path, *args, **kwargs)

    def guarded_unlink(path, *args, **kwargs):
        if kwargs.get("dir_fd") is None and not args:
            _check("os.unlink", path)
        return originals["os.unlink"](path, *args, **kwargs)

    def guarded_path_unlink(self, missing_ok=False):
        _check("pathlib.Path.unlink", self)
        return originals["pathlib.Path.unlink"](self, missing_ok=missing_ok)

    def guarded_path_rmdir(self):
        _check("pathlib.Path.rmdir", self)
        return originals["pathlib.Path.rmdir"](self)

    def guarded_rmtree(path, *args, **kwargs):
        # rmtree 只接受路径参数（无 dir_fd），无条件校验。
        _check("shutil.rmtree", path)
        return originals["shutil.rmtree"](path, *args, **kwargs)

    _guard_os.remove = guarded_remove
    _guard_os.unlink = guarded_unlink
    _guard_shutil.rmtree = guarded_rmtree
    _guard_pathlib.Path.unlink = guarded_path_unlink
    _guard_pathlib.Path.rmdir = guarded_path_rmdir
    # pytest 若被指定了 --basetemp（在 gettempdir 之外），把它的根也登记为放行区。
    _EXTRA_TEMP_ROOTS.clear()
    basetemp = getattr(tmp_path_factory, "basetemp", None)
    if basetemp is not None:
        resolved = _GuardPath(str(basetemp)).resolve()
        if not _is_under(resolved, _TEMP_ROOT):
            _EXTRA_TEMP_ROOTS.add(resolved)
    try:
        yield
    finally:
        _guard_os.remove = originals["os.remove"]
        _guard_os.unlink = originals["os.unlink"]
        _guard_shutil.rmtree = originals["shutil.rmtree"]
        _guard_pathlib.Path.unlink = originals["pathlib.Path.unlink"]
        _guard_pathlib.Path.rmdir = originals["pathlib.Path.rmdir"]
        _EXTRA_TEMP_ROOTS.clear()


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

    token, _payload = get_jwt_handler().create_token(subject="test-human")
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


# ==================== Export ====================

__all__ = [
    "pytest_configure",
    "pytest_collection_modifyitems", 
    "pytest_runtest_setup",
    "pytest_addoption",
]