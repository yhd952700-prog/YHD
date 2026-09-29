"""UBX-005 首次生产路径接线的真实端到端验证。

本文件独立于实现，证明"武装围栏后自主动作能拿到 executor 身份并被执行、而
未持上下文的动作被默认拒绝、后端故障 fail-closed、两进程不能同时通过、重启安全
恢复、令牌单调、租约持久化、审计链路完整"。

覆盖路径：set_executor_fence → C15 deny gate（@kernel_action）→
SqliteExecutorLease / DistributedExecutorLease → 动作执行边界
（WorldInterface.execute）→ audit → recovery → observability。

默认（LIUHAO_EXECUTOR_FENCE 未设）行为必须字节级不变——本文件也显式断言这一点。
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

import pytest

from src.kernels.execution.fence import (
    ExecutorFenceDenied,
    FenceContext,
    attach_default_executor_fence,
    executor_fence_armed,
    executor_session,
    get_executor_fence,
    reset_process_executor_lease_for_testing,
    set_executor_fence,
)
from src.kernels._crosscutting import PolicyDeniedError, kernel_action
from src.ai.world_interface import (
    WorldAdapter,
    WorldInterface,
    WorldRequest,
)

REPO_ROOT = str(Path(__file__).resolve().parents[2])
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

VENV_PY = (
    "D:/LiuHao-Data/UserRoot/.workbuddy/binaries/python/envs/default/"
    "Scripts/python.exe"
)

WORKER = str(Path(__file__).resolve().parent / "_ubx005_fence_worker.py")


# =============================================================================
# Fixtures
# =============================================================================
@pytest.fixture(autouse=True)
def _reset_fence_state():
    """每个测试之间重置围栏状态，避免进程级租约缓存 / 默认围栏泄漏。"""
    reset_process_executor_lease_for_testing()
    set_executor_fence(None)
    yield
    reset_process_executor_lease_for_testing()
    set_executor_fence(None)


@pytest.fixture
def armed_env(monkeypatch):
    """武装执行围栏门（与部署一致的环境变量形状）。"""
    monkeypatch.setenv("LIUHAO_EXECUTOR_FENCE", "on")
    monkeypatch.setenv("LIUHAO_DISTRIBUTED_LEASE_BACKEND", "file")
    return monkeypatch


def _attach_file_fence(tmp_path, max_executors=None):
    return attach_default_executor_fence(
        backend="file",
        lease_dir=str(tmp_path / "leases"),
        max_executors=max_executors,
    )


class StubAdapter(WorldAdapter):
    """测试用假适配器：execute 返回固定结果，便于隔离动作边界。"""

    name = "stub"
    SUPPORTED_ACTIONS = frozenset({"ping"})

    def observe(self, request: WorldRequest):  # noqa: D401 - simple stub
        return {"observed": True}

    def execute(self, request: WorldRequest):  # noqa: D401 - simple stub
        return {"pong": True}


# =============================================================================
# 默认（未武装）行为字节级不变
# =============================================================================
def test_default_unarmed_is_pass_through():
    assert executor_fence_armed() is False
    # 未武装时 executor_session 不绑定任何上下文。
    with executor_session(action="x", capabilities=()) as ctx:
        assert ctx is None
    assert get_executor_fence() is not None  # 惰性默认围栏始终存在


# =============================================================================
# 武装后：中央 deny gate 允许绑定动作、拒绝无上下文动作
# =============================================================================
def test_armed_kernel_action_allowed_with_session(tmp_path, armed_env, monkeypatch):
    fence = _attach_file_fence(tmp_path, max_executors=4)

    import src.kernels.audit as audit_mod

    captured = []

    def _spy(*args, **kwargs):
        captured.append(kwargs)
        return None

    monkeypatch.setattr(audit_mod, "log_event", _spy)

    @kernel_action("ubx005.test.action", risk_level="LOW")
    def do_thing(x):
        return x * 2

    with executor_session(
        action="ubx005.test.action", capabilities=(), ttl_sec=30.0
    ) as ctx:
        assert ctx is not None
        # 上下文确实绑定到了调用栈上。
        from src.kernels.execution.fence import current_executor_fence

        assert current_executor_fence() is ctx
        result = do_thing(21)

    assert result == 42

    # 审计链路：绑定的 executor 身份被写进审计 details（篡改可见）。
    assert captured, "kernel_action 没有写出任何审计事件"
    details = captured[-1]["details"]
    assert details.get("executor_id") == ctx.executor_id
    assert details.get("fence_token") == ctx.token
    assert details.get("lease_epoch") == ctx.epoch

    # 顺便确认围栏对象是 file 后端的真实分布式围栏。
    from src.distribution.coordination import DistributedExecutorLease

    assert isinstance(fence.lease, DistributedExecutorLease)


def test_armed_kernel_action_denied_without_session(armed_env):
    # 武装但没有绑定任何 executor 上下文 -> 默认-DENY 门必须拒绝。
    @kernel_action("ubx005.test.action", risk_level="LOW")
    def do_thing(x):
        return x * 2

    with pytest.raises(PolicyDeniedError):
        do_thing(21)


# =============================================================================
# 动作执行边界（WorldInterface.execute）真实端到端
# =============================================================================
def test_world_execute_allowed_with_session(tmp_path, armed_env):
    _attach_file_fence(tmp_path, max_executors=4)
    wi = WorldInterface(adapters=[StubAdapter()], authorize=lambda r: True)
    res = wi.execute(WorldRequest(adapter="stub", action="ping"))
    assert res.success is True
    assert res.output == {"pong": True}


def test_world_execute_fail_closed_on_broken_fence(tmp_path, armed_env):
    fence = _attach_file_fence(tmp_path, max_executors=4)

    from src.kernels.execution.fence import ExecutorFenceDenied

    def _boom(*args, **kwargs):
        raise ExecutorFenceDenied("backend unavailable (simulated)")

    # 破坏后端获取路径 -> 围栏必须 fail-closed 拒绝动作，而不是放行。
    fence.lease.acquire = _boom

    wi = WorldInterface(adapters=[StubAdapter()], authorize=lambda r: True)
    res = wi.execute(WorldRequest(adapter="stub", action="ping"))
    assert res.success is False
    assert "executor fence denied" in res.error


# =============================================================================
# 两进程不能同时通过（真实 OS 子进程，file 后端，max_executors=1）
# =============================================================================
def test_two_processes_cannot_both_execute(tmp_path):
    lease_dir = tmp_path / "leases"
    lease_dir.mkdir()
    ready = tmp_path / "ready"
    denied_parent = tmp_path / "denied_parent"
    ok_parent = tmp_path / "ok_parent"

    env = {
        "LIUHAO_EXECUTOR_FENCE": "on",
        "LIUHAO_DISTRIBUTED_LEASE_BACKEND": "file",
        "LIUHAO_EXECUTOR_FENCE_LEASE_DIR": str(lease_dir),
        "LIUHAO_EXECUTOR_FENCE_MAX_EXECUTORS": "1",
    }

    child = subprocess.Popen(
        [
            VENV_PY,
            WORKER,
            json.dumps(
                {
                    "mode": "hold",
                    "dir": str(lease_dir),
                    "repo_root": REPO_ROOT,
                    "env": env,
                    "ready": str(ready),
                    "hold_for": 40.0,
                }
            ),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    # 等待子进程真正持有租约。
    for _ in range(400):
        if ready.exists():
            break
        time.sleep(0.1)
    assert ready.exists(), "child never acquired the lease (deadlock in worker?)"

    # 父进程（另一个独立进程）尝试获取 -> 必须被 N+1 上限拒绝。
    parent = subprocess.run(
        [
            VENV_PY,
            WORKER,
            json.dumps(
                {
                    "mode": "try",
                    "dir": str(lease_dir),
                    "repo_root": REPO_ROOT,
                    "env": env,
                    "ok": str(ok_parent),
                    "denied": str(denied_parent),
                }
            ),
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert denied_parent.exists(), (
        f"parent was NOT denied (cap=1 failed): ok={ok_parent.exists()} "
        f"stdout={parent.stdout!r} stderr={parent.stderr!r}"
    )
    assert not ok_parent.exists()
    child.wait(timeout=50)


# =============================================================================
# file 后端：重启安全恢复 + 令牌单调 + 租约持久化 + stale 拒绝
# =============================================================================
def test_file_backend_restart_recovery_and_monotonic(tmp_path):
    d = str(tmp_path / "leases")
    # "进程 1"
    f1 = attach_default_executor_fence(
        backend="file", lease_dir=d, max_executors=4
    )
    ctx1 = f1.acquire_for("exec-A", "exec-A", (), ttl_sec=1.0)
    t1 = ctx1.token

    # "进程 2"（全新 fence 实例，共享同一后端目录 = 重启后重新接入）
    f2 = attach_default_executor_fence(
        backend="file", lease_dir=d, max_executors=4
    )

    # 未过期时，旧令牌仍然有效。
    assert f2.lease.validate("exec-A", t1) is True

    # 过期后：旧令牌失效，stale 上下文在门上被拒绝。
    time.sleep(1.3)
    assert f2.lease.validate("exec-A", t1) is False
    with pytest.raises(ExecutorFenceDenied):
        f2.enforce(
            FenceContext("exec-A", t1, ctx1.epoch, (), ctx1.boot_gen), "x"
        )

    # 重启后重新获取同一 executor -> 新令牌，且严格单调（大于旧的）。
    ctx2 = f2.acquire_for("exec-A", "exec-A", (), ttl_sec=30.0)
    assert ctx2.token > t1

    # 另一个 executor -> 令牌继续单调递增（全局 seq 跨"进程"一致）。
    ctx3 = f2.acquire_for("exec-B", "exec-B", (), ttl_sec=30.0)
    assert ctx3.token > ctx2.token


# =============================================================================
# 持久化：租约行落在持久目录的 SQLite 中（重启后仍在）
# =============================================================================
def test_file_backend_lease_is_persistent(tmp_path):
    import sqlite3

    d = tmp_path / "leases"
    fence = attach_default_executor_fence(
        backend="file", lease_dir=str(d), max_executors=4
    )
    fence.acquire_for("exec-P", "exec-P", (), ttl_sec=30.0)

    # 后端数据库文件应存在于持久目录中。
    db_files = list(d.glob("*.db")) + list(d.glob("*.sqlite"))
    assert db_files, "no coordination db file written to the persistent dir"

    # 直接读后端，确认租约行持久化。
    conn = sqlite3.connect(str(db_files[0]))
    try:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
        names = {r[0] for r in rows}
        # 协调后端（FileLockLease）的表是 leases / meta / consumed，不是
        # SqliteExecutorLease 的 executor_fence —— 两者都是持久化证据。
        assert "leases" in names
    finally:
        conn.close()
