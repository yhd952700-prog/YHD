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