"""故障面回归：时间跳变、审计原子写、记忆/模型配置/升级并发。

覆盖用户会真实撞上、但常规功能测试不会走到的失败路径：

* 系统时钟前进/后退（含时区变化）不能把已登录用户踢下线；
* 审计写入失败必须 fail-closed（旧文件完好、内存事件回滚）；
* 记忆落盘失败要有可读提示，且内存态回滚；
* 两个窗口同时改模型配置不能互相覆盖；
* 两个执行体同时点升级，第二个必须得到可读拒绝。
"""
from __future__ import annotations

import errno
import json
import sqlite3
import sys
import threading

import jwt
import pytest

import src._time as time_mod
from src.ai.model_config import ModelConfigStore
from src.audit.models import AuditEvent
from src.audit.store import AuditStore
from src.kernels.memory import MemoryKernel, MemoryTier
from src.kernels.memory.store import MemoryStore, MemoryStoreError
from src.upgrade.errors import ApplyError
from src.upgrade.manager import UpgradeConfig, UpgradeManager
from src.common.instance_lock import InstanceLock


# ---------------------------------------------------------------------- #
# 时间跳变
# ---------------------------------------------------------------------- #
@pytest.fixture
def clock(monkeypatch):
    """把抗跳变时钟的锚点固定在 (1000.0, 0.0)，用例通过 dict 改两个钟。

    用例结束后必须把锚点恢复到真实时间：抗跳变时钟的语义就是"跳变后不再跟随
    墙钟直到进程重启"，不恢复会污染同一进程里后续所有测试。
    """
    real_time, real_mono = time_mod.time.time, time_mod.time.monotonic
    time_mod._reanchor(1000.0, 0.0)
    state = {"wall": 1000.0, "mono": 0.0}
    monkeypatch.setattr(time_mod.time, "time", lambda: state["wall"])
    monkeypatch.setattr(time_mod.time, "monotonic", lambda: state["mono"])
    try:
        yield state
    finally:
        time_mod._reanchor(real_time(), real_mono())


@pytest.fixture
def jwt_handler(tmp_path, monkeypatch):
    monkeypatch.setenv("LIUHAO_JWT_REVOCATION_DB", str(tmp_path / "revocation.db"))
    from src.security.jwt_handler import JWTHandler

    return JWTHandler(algorithm="HS256", secret_key="k" * 32)


def test_stable_clock_ignores_forward_and_backward_jumps(clock):
    clock["mono"] = 10.0
    clock["wall"] = 1000.0 + 10.0 + 3600.0  # 向前跳 1 小时
    assert time_mod.stable_now_epoch() == pytest.approx(1010.0)

    clock["mono"] = 10.0
    clock["wall"] = 1000.0 + 10.0 - 86400.0  # 再向后拨 1 天
    assert time_mod.stable_now_epoch() == pytest.approx(1010.0)

    # 正常走时（±阈值内）仍然跟随墙钟。
    clock["mono"] = 20.0
    clock["wall"] = 1020.0
    assert time_mod.stable_now_epoch() == pytest.approx(1020.0)


def test_jwt_survives_forward_and_backward_clock_jumps(jwt_handler, clock):
    token, _ = jwt_handler.create_token("alice", ttl=900)

    clock["mono"] = 5.0
    clock["wall"] = 1000.0 + 5.0 + 365 * 24 * 3600
    assert jwt_handler.validate_token(token).sub == "alice"

    clock["mono"] = 5.0
    clock["wall"] = 1000.0 + 5.0 - 365 * 24 * 3600
    assert jwt_handler.validate_token(token).sub == "alice"


def test_revocation_still_applies_after_forward_clock_jump(jwt_handler, clock):
    token, _ = jwt_handler.create_token("alice", ttl=900)
    jwt_handler.revoke_token(token)
    clock["mono"] = 5.0
    clock["wall"] = 1000.0 + 5.0 + 10 * 365 * 24 * 3600
    with pytest.raises(jwt.InvalidTokenError, match="revoked"):
        jwt_handler.validate_token(token)


def test_expired_token_is_still_rejected(jwt_handler, clock):
    token, _ = jwt_handler.create_token("bob", ttl=1)
    clock["mono"] = 120.0
    clock["wall"] = 1120.0
    with pytest.raises(jwt.ExpiredSignatureError):
        jwt_handler.validate_token(token)


def test_audit_chain_survives_clock_rollback(tmp_path, clock):
    store = AuditStore(str(tmp_path / "events.json"))
    store.emit(AuditEvent(event_id="evt-1", event_type="test", source="tester", timestamp=1000.0))
    clock["mono"] = 5.0
    clock["wall"] = 1005.0
    store.emit(AuditEvent(event_id="evt-2", event_type="test", source="tester", timestamp=1005.0))
    # 时间被拨回一年：审计链只按 event_id + prev_hash 校验，不依赖墙钟。
    clock["mono"] = 5.0
    clock["wall"] = 1005.0 - 365 * 24 * 3600
    store.emit(AuditEvent(event_id="evt-3", event_type="test", source="tester", timestamp=1006.0))
    assert store.verify_integrity() is True


# ---------------------------------------------------------------------- #
# 审计：原子写 + fail-closed
# ---------------------------------------------------------------------- #
def _event(event_id: str) -> AuditEvent:
    return AuditEvent(event_id=event_id, event_type="test", source="tester", timestamp=1000.0)


def test_audit_write_failure_keeps_previous_file_and_rolls_back(tmp_path, monkeypatch):
    path = tmp_path / "events.json"
    store = AuditStore(str(path))
    store.emit(_event("evt-1"))
    before = path.read_text(encoding="utf-8")

    def boom(*_args, **_kwargs):
        raise OSError(errno.ENOSPC, "No space left on device (simulated)")

    with monkeypatch.context() as m:
        # 打补丁到 AuditStore 方法实际持有的 globals 字典。tests/test_audit_single_source_of_truth.py
        # 会 pop 该模块再重新 import，之后 sys.modules 里是新模块对象，而旧类的方法
        # 仍解析旧字典 —— 按模块名打补丁会静默失效（全量套件里 DID NOT RAISE，
        # 单跑却是绿的）。
        m.setitem(type(store)._save.__globals__, "atomic_write_text", boom)
        with pytest.raises(OSError):
            store.emit(_event("evt-2"))

    assert path.read_text(encoding="utf-8") == before
    assert "evt-2" not in store._events

    # 恢复磁盘后继续写：回滚没有污染链，下一次写入仍然完整可用。
    store.emit(_event("evt-3"))
    assert "evt-3" in json.loads(path.read_text(encoding="utf-8"))["events"]
    assert store.verify_integrity() is True


# ---------------------------------------------------------------------- #
# 记忆：可读失败 + 内存回滚
# ---------------------------------------------------------------------- #
def _row(key_hash: str = "persistent:k", value: str = '"v"') -> dict:
    return {
        "key_hash": key_hash,
        "entry_id": "e1",
        "key": "k",
        "value": value,
        "tier": "persistent",
        "scope": "l1",
        "created_at": "2026-01-01T00:00:00",
        "expires_at": None,
        "tags": "[]",
        "correlation_id": "c1",
        "access_count": 0,
        "last_accessed": None,
        "provenance": None,
    }


def test_memory_store_write_failure_is_readable_and_keeps_old_rows(tmp_path):
    db = tmp_path / "memory.db"
    store = MemoryStore(str(db))
    store.persist(_row("persistent:k", '"old"'))
    store._conn.close()  # 模拟底层 I/O 不可用（磁盘满/只读/文件被占用）
    with pytest.raises(MemoryStoreError) as info:
        store.persist(_row("persistent:k2", '"new"'))
    assert "无法写入记忆库" in str(info.value)

    conn = sqlite3.connect(db)
    try:
        rows = conn.execute("SELECT key_hash, value FROM memory_entries").fetchall()
    finally:
        conn.close()
    assert rows == [("persistent:k", '"old"')]


def test_memory_kernel_rolls_back_memory_when_persist_fails(tmp_path, monkeypatch):
    kernel = MemoryKernel(db_path=str(tmp_path / "kernel.db"))

    def boom(_entry):
        raise MemoryStoreError(str(tmp_path / "kernel.db"), RuntimeError("disk full"))

    monkeypatch.setattr(kernel._store, "persist", boom)
    with pytest.raises(MemoryStoreError):
        kernel.store("k", {"a": 1}, tier=MemoryTier.PERSISTENT)

    # fail-closed：调用方收到失败，内存里也不能留下"已记住"的假象。
    assert kernel.recall("k") is None


# ---------------------------------------------------------------------- #
# 并发：模型配置 / 升级
# ---------------------------------------------------------------------- #
def test_concurrent_model_config_updates_do_not_lose_each_other(tmp_path):
    store = ModelConfigStore(
        db_path=str(tmp_path / "models.sqlite3"), key_path=str(tmp_path / "models.key")
    )
    created = store.create(
        {"name": "本地", "provider": "mock", "model": "m", "timeout": 30.0}, "admin"
    )
    barrier = threading.Barrier(2)
    failures = []

    def update(payload):
        try:
            barrier.wait(timeout=5)
            store.update(created["id"], payload, "admin")
        except Exception as exc:  # noqa: BLE001
            failures.append(exc)

    threads = [
        threading.Thread(target=update, args=({"timeout": 45.0},)),
        threading.Thread(target=update, args=({"base_url": "http://127.0.0.1:9"},)),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)

    assert failures == []
    final = store.get_public(created["id"])
    assert final["timeout"] == 45.0
    assert final["base_url"] == "http://127.0.0.1:9"


def _upgrade_manager(tmp_path) -> UpgradeManager:
    cfg = UpgradeConfig(
        app_root=tmp_path / "app",
        data_dir=tmp_path / "data",
        backup_root=tmp_path / "backups",
        state_dir=tmp_path / "state",
    )
    return UpgradeManager(cfg)


def test_upgrade_is_exclusive_across_threads(tmp_path):
    manager = _upgrade_manager(tmp_path)
    entered = threading.Event()
    release = threading.Event()

    def holder():
        with manager._exclusive("升级操作", ApplyError):
            entered.set()
            release.wait(timeout=5)

    thread = threading.Thread(target=holder)
    thread.start()
    assert entered.wait(timeout=5)
    try:
        with pytest.raises(ApplyError, match="另一个升级操作正在进行中"):
            with manager._exclusive("升级操作", ApplyError):
                pass
    finally:
        release.set()
        thread.join(timeout=10)


def test_upgrade_lock_is_visible_to_another_process(tmp_path):
    manager = _upgrade_manager(tmp_path)
    external = InstanceLock(manager.config.state_dir, subdir="", name="upgrade-op")
    external.acquire()
    try:
        with pytest.raises(ApplyError, match="另一个窗口或命令行进程"):
            with manager._exclusive("升级操作", ApplyError):
                pass
    finally:
        external.release()
