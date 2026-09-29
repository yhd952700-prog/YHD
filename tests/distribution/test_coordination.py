"""UBX-005 分布式协调层测试 —— 证明行为，而不是证明"代码存在"。

本文件的核心价值是**证伪**：每一条断言都指向旧实现曾经说谎的那一个具体位置。

* 旧 ``src/distribution/lock.py`` 用实例 dict 当锁、``release()`` 不校验
  ``lock_id``、``check()`` 在过期后仍返回 ``True``。
* 旧 ``src/distribution/election.py`` 用 dict 插入顺序裁决 leader、
  ``is_leader()`` 不查 TTL、``renew_lease()`` 恒 ``False``、``_stop_renewal()``
  是 ``pass``。

对应地，这里要求：

* **两个独立 OS 进程**争同一租约 → 有且仅有一个持有者（真 subprocess，不是线程）。
* 持有者被 ``os._exit``（等价 kill -9）杀死 → 新持有者的令牌**严格更大**；
  且在租约到期**之前**不允许提前夺权。
* 陈旧 / 过期 / 被取代的令牌执行动作 → 被拒。
* 用错误令牌 ``release`` → **不会**释放他人租约（lock.py:120 的回归测试）。
* 后端不可达 → 拒绝，绝不 best-effort 成功。
* ``backend="redis"`` 且 redis 不可达 → **绝不静默切换**成 file/memory
  （对齐 test_bus_backends.py:227 的反假纪律）。

断言风格刻意对齐 ``tests/distribution/test_bus_backends.py``。
"""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import textwrap
import time
import uuid
from pathlib import Path
from typing import Any, Dict, Optional

import pytest

from src.distribution.coordination import (
    BACKEND_ENV,
    CoordinationUnavailableError,
    DistributedExecutorLease,
    DistributedLease,
    ExecutorFenceBackendError,
    EtcdLease,
    FileLockLease,
    FencingToken,
    RedisLease,
    get_distributed_lease,
    parse_token,
)
from src.kernels.execution.fence import (
    ExecutorFence,
    ExecutorFenceDenied,
    ExecutorLease,
    ExecutorLimitExceeded,
    ReplayDetectedError,
    StaleExecutorError,
)
from src.distribution.election import LeaderElection
from src.distribution.lock import DistributedLock

REPO_ROOT = str(Path(__file__).resolve().parents[2])

#: 一个几乎必定拒绝连接的地址（端口 6399），用来制造"redis 不可达"。
UNREACHABLE_URL = "redis://127.0.0.1:6399/0"
FAST_FAIL = {"url": UNREACHABLE_URL, "connect_timeout": 0.3}


# =============================================================================
# 子进程工人：让"两个进程"是真的两个进程
# =============================================================================

WORKER_SRC = textwrap.dedent(
    '''
    """独立进程工人。所有参数通过单个 JSON 传入（避免 argv 位置歧义）。"""

    import json, os, sys, time

    payload = json.loads(sys.argv[1])
    mode = payload["mode"]
    out = payload["out"]
    sys.path.insert(0, payload["repo"])

    from src.distribution.coordination import FileLockLease

    lease = FileLockLease(payload["name"], directory=payload["dir"])
    owner = "proc-%d" % os.getpid()
    result = {"pid": os.getpid(), "mode": mode}

    # 争用栅栏：父进程在所有子进程 spawn 完成后才创建 barrier 文件。
    # 工人等到它出现再抢锁，确保"真正同时争用"。否则在满负荷测试下子进程
    # spawn 错峰可能超过 hold 窗口，后 spawn 的进程会在前一个持有者释放后才
    # 抢到锁，被误判为"多个持有者"（这是测试工装的时序缺陷，不是租约缺陷）。
    # 若租约本身存在真实竞态，栅栏反而会让它稳定暴露（测试仍会红），不会掩盖。
    _barrier = payload.get("barrier")
    if _barrier:
        _bdeadline = time.time() + float(payload.get("barrier_timeout", 60.0))
        while not os.path.exists(_barrier):
            if time.time() > _bdeadline:
                break
            time.sleep(0.005)

    def finish(extra=None):
        if extra:
            result.update(extra)
        with open(out, "w") as fh:
            json.dump(result, fh)
            fh.flush()
            os.fsync(fh.fileno())

    if mode == "crash":
        ok, token = lease.acquire(owner, payload.get("ttl", 3.0))
        finish({"ok": bool(ok),
                "token": int(token) if token is not None else None})
        # 等价 kill -9：不跑 finally、不 flush stdio、不释放任何东西。
        os._exit(9)

    if mode == "hold":
        ok, token = lease.acquire(owner, payload.get("ttl", 30.0))
        finish({"ok": bool(ok),
                "token": int(token) if token is not None else None})
        time.sleep(float(payload.get("hold", 2.0)))
        if ok and token is not None:
            lease.release(owner, token)
        sys.exit(0)

    if mode == "reader":
        # 持续读，制造"围栏热路径"：ExecutorFence._enforce 每个动作都读。
        deadline = time.time() + float(payload.get("seconds", 3.0))
        expected = payload.get("expected_holder")
        reads = anomalies = 0
        last_error = None
        while time.time() < deadline:
            try:
                view = lease.current()
                reads += 1
                # 一致性：holder 与 token 必须同在/同缺；state 必须合法；
                # 持有者只能是预期的那个（或空）。
                if (view.token is None) != (view.holder is None):
                    anomalies += 1
                if view.state not in ("held", "expired", "free", "released"):
                    anomalies += 1
                if expected and view.holder is not None and view.holder != expected:
                    anomalies += 1
            except Exception as exc:
                anomalies += 1
                last_error = "%s: %s" % (type(exc).__name__, exc)
        finish({"reads": reads, "anomalies": anomalies, "last_error": last_error})
        sys.exit(0)

    if mode == "writer":
        # 有并发读者在侧的情况下，写者必须能持续推进。
        attempts = int(payload.get("attempts", 50))
        ok_count = 0
        errors = []
        for _ in range(attempts):
            try:
                ok, _tok = lease.acquire(payload.get("writer_owner", owner),
                                         payload.get("ttl", 10.0))
                if ok:
                    ok_count += 1
            except Exception as exc:
                errors.append("%s: %s" % (type(exc).__name__, exc))
        finish({"attempts": attempts, "ok": ok_count,
                "error_count": len(errors), "errors": errors[:5]})
        sys.exit(0)

    if mode == "exec":
        from src.distribution.coordination import DistributedExecutorLease

        directory = payload["dir"]
        bridge = DistributedExecutorLease(
            lease_factory=lambda n: FileLockLease(n, directory=directory),
            # 上限必须由**桥接**强制（原子），fence 侧的 check-then-act 只是快路径。
            max_executors=payload.get("max_executors"),
        )
        exec_id = payload["exec_id"]
        action = payload.get("action", "acquire")
        res = {"exec_id": exec_id, "action": action}
        try:
            if action == "acquire":
                tok = bridge.acquire(exec_id, "owner", float(payload.get("ttl", 30.0)),
                                     list(payload.get("caps", ["read"])))
                res.update({"token": tok,
                            "validate": bool(bridge.validate(exec_id, tok)),
                            "state": bridge.current(exec_id).state})
            elif action == "validate":
                tok = int(payload["token"])
                res.update({"validate": bool(bridge.validate(exec_id, tok)),
                            "state": bridge.current(exec_id).state,
                            "known": bool(bridge.known_executor(exec_id)),
                            "caps": list(bridge.current(exec_id).granted_capabilities)})
            elif action == "enforce":
                from src.kernels.execution.fence import ExecutorFence
                fence = ExecutorFence(bridge, max_executors=payload.get("max_executors"))
                ctx = fence.acquire_for(exec_id, "owner", ["read"], ttl_sec=30.0)
                fence.enforce(ctx, "read", ["read"], correlation_id="c1")
                res.update({"token": ctx.token, "allowed": True})
        except Exception as exc:
            try:
                st = bridge.current(exec_id).state
            except Exception:
                st = None
            res.update({"error": "%s: %s" % (type(exc).__name__, exc),
                        "error_type": type(exc).__name__, "state": st})
        finish(res)
        sys.exit(0)

    finish()
    sys.exit(0)
    '''
)


def _worker_path(tmp_path: Path) -> str:
    path = tmp_path / "_lease_worker.py"
    path.write_text(WORKER_SRC, encoding="utf-8")
    return str(path)


def _spawn(tmp_path: Path, payload: dict):
    """启动**一个独立 OS 进程**；返回 ``(out_path, Popen)``。"""
    worker = _worker_path(tmp_path)
    out = Path(payload["out"])
    return out, subprocess.Popen(
        [sys.executable, worker, json.dumps(payload)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def _collect(pairs, timeout: int = 180):
    results = []
    for out, proc in pairs:
        proc.wait(timeout=timeout)
        results.append(json.loads(out.read_text(encoding="utf-8")))
    return results


def _run_workers(tmp_path: Path, mode: str, count: int, hold: float = 2.0):
    """启动 ``count`` 个独立进程争同一个租约，返回它们各自的结果 dict。"""
    lease_dir = tmp_path / "leases"
    lease_dir.mkdir(exist_ok=True)
    # 争用栅栏文件：仅 "hold" 模式（TestTwoProcessesContend）使用，确保子进程
    # 真正同时争用，消除 spawn 错峰导致的偶发多赢假阳性。
    barrier_path = lease_dir / ".contend_barrier"
    pairs = []
    for i in range(count):
        out = lease_dir / f"result-{mode}-{i}.json"
        payload = {
            "mode": mode,
            "dir": str(lease_dir),
            "name": "contended",
            "out": str(out),
            "repo": REPO_ROOT,
            "hold": hold,
            "ttl": 3.0 if mode == "crash" else 30.0,
        }
        if mode == "hold":
            payload["barrier"] = str(barrier_path)
        pairs.append(_spawn(tmp_path, payload))
    # 所有子进程已 spawn 完成：放开栅栏，让它们真正"同时"抢锁。
    if mode == "hold":
        barrier_path.write_text("go", encoding="utf-8")
    return _collect(pairs)


@pytest.fixture
def lease_dir(tmp_path):
    d = tmp_path / "leases"
    d.mkdir(exist_ok=True)
    return str(d)


@pytest.fixture
def factory(lease_dir):
    def _factory(name: str) -> DistributedLease:
        return FileLockLease(name, directory=lease_dir)
    return _factory


def _wait_until_expiry(expires_at, margin: float = 0.10, timeout: float = 20.0) -> None:
    """睡到 ``expires_at + margin``，**而不是**睡一个固定的秒数。

    固定 ``sleep(0.3)`` 在负载高的机器上会假失败：进程刚启动、磁盘忙、GC 一抖，
    TTL 就已经过去了，于是"过期前必须还有效"这类断言随机翻车。这里按**实际
    记录的到期时间**等待，机器快慢都不改变结论。
    """
    if expires_at is None:
        raise AssertionError("expected the lease to carry an expires_at")
    deadline = time.time() + timeout
    while time.time() <= float(expires_at) + margin:
        if time.time() > deadline:
            raise AssertionError(f"lease did not expire within {timeout}s")
        time.sleep(0.02)


# =============================================================================
# A) FencingToken —— 可比较的整数，绝不是 uuid
# =============================================================================


class TestFencingTokenIsComparable:
    def test_token_is_an_ordered_integer_not_a_uuid(self):
        a, b = FencingToken(1), FencingToken(2)
        assert b > a
        assert a < b
        assert int(b) == 2
        # 随机 UUID 无法比较大小，因而无法实现围栏 —— 这里断言我们没走那条路。
        assert isinstance(a, int)

    def test_next_is_strictly_monotonic(self):
        tok = FencingToken(0)
        seen = [tok]
        for _ in range(25):
            tok = tok.next()
            seen.append(tok)
        assert all(y > x for x, y in zip(seen, seen[1:]))

    def test_parse_token_rejects_garbage_instead_of_guessing(self):
        assert parse_token("42") == FencingToken(42)
        assert parse_token(7) == FencingToken(7)
        for bad in (None, "", "not-a-number", "uuid-1234", True, object()):
            assert parse_token(bad) is None


# =============================================================================
# B) 跨进程互斥 —— 真的两个 OS 进程
# =============================================================================


class TestTwoProcessesContend:
    def test_exactly_one_holder_across_separate_os_processes(self, tmp_path):
        """四个独立进程同时抢一个租约，有且仅有一个拿到。"""
        results = _run_workers(tmp_path, "hold", count=4, hold=2.0)
        winners = [r for r in results if r["ok"]]
        assert len(winners) == 1, (
            f"expected exactly one holder, got {len(winners)}: {results}"
        )
        # 每个进程都是真的不同 pid
        assert len({r["pid"] for r in results}) == 4

    def test_loser_gets_false_not_a_fake_token(self, tmp_path):
        results = _run_workers(tmp_path, "hold", count=3, hold=1.5)
        losers = [r for r in results if not r["ok"]]
        assert losers, "expected at least one loser"
        assert all(r["token"] is None for r in losers)


# =============================================================================
# C) 崩溃接管
# =============================================================================


class TestCrashTakeover:
    def test_dead_holder_is_replaced_with_a_strictly_greater_token(self, tmp_path):
        """持有者被 os._exit(9) 杀死（等价 kill -9）→ 接管者令牌严格更大。"""
        (crash_result,) = _run_workers(tmp_path, "crash", count=1)
        assert crash_result["ok"] is True
        dead_token = crash_result["token"]
        assert dead_token is not None

        lease_dir = str(tmp_path / "leases")
        takeover = FileLockLease("contended", directory=lease_dir)

        # 1) 租约到期之前**不允许**提前夺权 —— 否则就是脑裂。
        #    （崩溃者用的是 3s TTL，所以这里绝不会因为"刚好到期"而侥幸通过。）
        early_ok, early_token = takeover.acquire("survivor", 30.0)
        assert early_ok is False, "premature takeover before TTL is split-brain"
        assert early_token is None

        # 2) 等租约真正超时后接管（按记录的到期时间等，不睡固定秒数）。
        _wait_until_expiry(takeover.current().expires_at)
        late_ok, late_token = takeover.acquire("survivor", 30.0)
        assert late_ok is True
        assert int(late_token) > dead_token, (
            f"takeover token {int(late_token)} must be strictly greater than "
            f"the dead holder's {dead_token}"
        )

    def test_tokens_never_reuse_a_dead_holders_value(self, tmp_path):
        """令牌只增不减：进程崩溃不会让计数器回退。"""
        (first,) = _run_workers(tmp_path, "crash", count=1)
        lease_dir = str(tmp_path / "leases")
        lease = FileLockLease("contended", directory=lease_dir)
        _wait_until_expiry(lease.current().expires_at)
        ok, tok = lease.acquire("survivor", 30.0)
        assert ok is True
        assert int(tok) != first["token"]


# =============================================================================
# D) 陈旧 / 过期 / 被取代的令牌必须被拒
# =============================================================================


class TestStaleTokensAreRejected:
    def test_expired_lease_stops_validating(self, lease_dir):
        lease = FileLockLease("expiring", directory=lease_dir)
        ok, token = lease.acquire("owner-a", 1.0)
        assert ok is True
        assert lease.validate("owner-a", token) is True
        _wait_until_expiry(lease.current().expires_at)
        assert lease.validate("owner-a", token) is False
        assert lease.current().state == "expired"

    def test_superseded_token_is_rejected(self, lease_dir):
        """A 释放 → B 接管拿更大的令牌 → A 的旧令牌必须失效。"""
        lease = FileLockLease("superseded", directory=lease_dir)
        ok_a, token_a = lease.acquire("owner-a", 30.0)
        assert ok_a is True
        assert lease.release("owner-a", token_a) is True

        ok_b, token_b = lease.acquire("owner-b", 30.0)
        assert ok_b is True
        assert int(token_b) > int(token_a)
        # A 的旧令牌现在必须被拒（这正是围栏的全部意义）。
        assert lease.validate("owner-a", token_a) is False
        assert lease.validate("owner-b", token_b) is True

    def test_another_owners_token_is_rejected(self, lease_dir):
        lease = FileLockLease("owners", directory=lease_dir)
        ok, token = lease.acquire("owner-a", 30.0)
        assert ok is True
        assert lease.validate("owner-b", token) is False


# =============================================================================
# E) release 必须校验令牌 —— lock.py:120 的回归测试
# =============================================================================


class TestReleaseValidatesTheToken:
    def test_wrong_token_does_not_free_another_holders_lease(self, lease_dir):
        """核心回归：旧实现任何 lock_id 都能解锁。"""
        holder = FileLockLease("release-guard", directory=lease_dir)
        attacker = FileLockLease("release-guard", directory=lease_dir)

        ok, token = holder.acquire("real-owner", 30.0)
        assert ok is True

        # 攻击者伪造/猜测一个令牌
        assert attacker.release("attacker", FencingToken(int(token) + 1)) is False
        assert attacker.release("attacker", FencingToken(1)) is False
        assert attacker.release("attacker", token) is False  # 令牌对但 owner 不对

        # 真正的持有者毫发无伤
        assert holder.validate("real-owner", token) is True

    def test_release_rejects_garbage_tokens(self, lease_dir):
        lease = FileLockLease("garbage", directory=lease_dir)
        ok, token = lease.acquire("owner", 30.0)
        assert ok is True
        for bad in ("", "not-a-token", None, "0x10"):
            assert lease.release("owner", bad) is False
        assert lease.validate("owner", token) is True

    def test_correct_release_frees_the_lease(self, lease_dir):
        lease = FileLockLease("clean", directory=lease_dir)
        ok, token = lease.acquire("owner", 30.0)
        assert ok is True
        assert lease.release("owner", token) is True
        assert lease.current().state in ("free", "released")
        ok2, token2 = lease.acquire("next-owner", 30.0)
        assert ok2 is True and int(token2) > int(token)


# =============================================================================
# F) 后端不可达 ⇒ 拒绝，绝不 best-effort；绝不静默降级
# =============================================================================


class TestUnreachableBackendDenies:
    def test_redis_unreachable_raises_instead_of_returning_a_lease(self):
        with pytest.raises(CoordinationUnavailableError) as exc:
            get_distributed_lease("x", backend="redis", config=dict(FAST_FAIL))
        assert "redis" in str(exc.value).lower()

    def test_redis_unreachable_does_not_silently_switch_to_file(self, tmp_path):
        """反假核心：mode 说 redis 就必须是 redis，不能偷偷变成本地文件锁。"""
        sentinel_dir = tmp_path / "must-stay-empty"
        sentinel_dir.mkdir()
        with pytest.raises(CoordinationUnavailableError):
            get_distributed_lease(
                "x",
                backend="redis",
                config={"url": UNREACHABLE_URL, "connect_timeout": 0.3},
            )
        # 如果实现偷偷降级到 file，这里会出现 .state.json / .lock 文件。
        assert list(sentinel_dir.iterdir()) == []

    def test_env_selected_redis_backend_also_denies(self, monkeypatch, tmp_path):
        monkeypatch.setenv(BACKEND_ENV, "redis")
        with pytest.raises(CoordinationUnavailableError):
            get_distributed_lease(
                "x", config={"url": UNREACHABLE_URL, "connect_timeout": 0.3}
            )

    def test_unknown_backend_is_a_loud_value_error(self):
        with pytest.raises(ValueError) as exc:
            get_distributed_lease("x", backend="consul")
        assert "consul" in str(exc.value)

    def test_etcd_backend_is_now_supported_and_fails_closed_without_etcd(self):
        """etcd 现在是受支持的后端；但 etcd3 未安装 / 后端不可达时必须 fail-closed。

        绝不能在 etcd3 缺失时崩溃，也不能偷偷降级成 file/memory。
        """
        # etcd3 在此 venv 未安装（任务约束：设计测试不依赖它），所以走到
        # connect() 的 import etcd3 分支 → CoordinationUnavailableError。
        with pytest.raises(CoordinationUnavailableError) as exc:
            get_distributed_lease("x", backend="etcd")
        assert "etcd" in str(exc.value).lower()

    def test_operations_on_a_closed_redis_lease_deny(self):
        lease = RedisLease("x", url=UNREACHABLE_URL, connect_timeout=0.3)
        # 从未 connect() —— 任何操作都必须是失败，而不是"大概成功"。
        with pytest.raises(CoordinationUnavailableError):
            lease.acquire("owner", 30.0)
        with pytest.raises(CoordinationUnavailableError):
            lease.validate("owner", FencingToken(1))


# =============================================================================
# G) RedisLease 的接线（注意：不验证 Lua 语义，见文档字符串）
# =============================================================================


class RecordingRedis:
    """最小 redis-py 替身，只记录 eval/get/pttl 的调用并回放固定返回值。

    **它不执行 Lua。** 因此本组测试只验证「传给 Redis 的 KEYS/ARGV 是否正确、
    返回值如何被解释」，**不**验证 Lua 脚本本身的语义 —— 后者需要真实 Redis
    （无 Lua 解释器、无 redis-server 时无法在此环境验证，见 TestRealRedis）。
    """

    def __init__(self, eval_result=None):
        self.eval_calls = []
        self.eval_result = eval_result
        self.store = {}

    def ping(self):
        return True

    def eval(self, script, numkeys, *args):
        keys = list(args[:numkeys])
        argv = list(args[numkeys:])
        self.eval_calls.append({"script": script, "keys": keys, "argv": argv})
        return self.eval_result

    def get(self, key):
        return self.store.get(key)

    def pttl(self, key):
        return 1234

    def incr(self, key):
        self.store[key] = int(self.store.get(key) or 0) + 1
        return self.store[key]

    def close(self):
        pass


class DeadRedis:
    """连接成功、之后每条命令都失败的 redis 替身 —— 模拟"运行中后端死掉"。"""

    def ping(self):
        return True

    def eval(self, *args, **kwargs):
        raise ConnectionError("redis backend went away")

    def get(self, key):
        raise ConnectionError("redis backend went away")

    def pttl(self, key):
        raise ConnectionError("redis backend went away")

    def incr(self, key):
        raise ConnectionError("redis backend went away")

    def close(self):
        pass


def factory_for_dead_redis():
    """返回一个"已连接但后端已死"的 RedisLease 工厂。"""

    def _factory(name: str) -> DistributedLease:
        lease = RedisLease(name, client=DeadRedis(), prefix="dead:")
        lease.connect()
        return lease

    return _factory


class TestRedisLeaseWiring:
    def test_acquire_passes_lease_and_global_token_keys(self):
        client = RecordingRedis(eval_result=[1, "7", "acquired"])
        lease = RedisLease("res", client=client, prefix="p:")
        lease.connect()
        ok, token = lease.acquire("owner-a", 30.0)
        assert ok is True and int(token) == 7

        call = client.eval_calls[-1]
        # KEYS[1]=租约键, KEYS[2]=**全局**令牌键（不是按 name 分）
        assert call["keys"] == ["p:res", "p:__global_token__"]
        assert call["argv"][0] == "owner-a"
        assert call["argv"][1] == 30000
        # ARGV 还必须带上 era 与 capabilities —— 让别的进程能校验它没获取的令牌
        assert call["argv"][2] == ""
        assert json.loads(call["argv"][3]) == []

    def test_release_is_a_compare_and_delete(self):
        client = RecordingRedis(eval_result=0)
        lease = RedisLease("res", client=client, prefix="p:")
        lease.connect()
        assert lease.release("owner-a", FencingToken(5)) is False
        call = client.eval_calls[-1]
        assert call["keys"] == ["p:res"]
        # owner 与 token 都作为参数参与比较 —— 不能只凭 key 就删。
        assert call["argv"] == ["owner-a", "5"]

    def test_owner_containing_the_separator_is_rejected(self):
        lease = RedisLease("res", client=RecordingRedis())
        with pytest.raises(ValueError):
            lease.acquire("bad|owner", 30.0)

    def test_token_key_is_global_across_lease_names(self):
        """两个不同租约名必须共用同一个计数器，否则令牌不再全局单调。"""
        a = RedisLease("one", client=RecordingRedis(), prefix="p:")
        b = RedisLease("two", client=RecordingRedis(), prefix="p:")
        assert a._token_key == b._token_key

    def test_consume_passes_correct_key_and_ttl(self):
        """consume 必须把 (name, token, correlation_id) 编码进 Redis key，并带 TTL。"""
        client = RecordingRedis(eval_result=1)
        lease = RedisLease("res", client=client, prefix="p:")
        lease.connect()
        lease.consume("exec-1", FencingToken(7), "c1")
        call = client.eval_calls[-1]
        assert call["keys"] == ["p:consumed:res:7:c1"]
        assert call["argv"][0] == "1"
        assert int(call["argv"][1]) == RedisLease._CONSUME_TTL_MS

    def test_consume_returns_0_raises_replay(self):
        """Lua 返回 0（key 已存在）⇒ consume 抛 ValueError（桥接转 ReplayDetectedError）。"""
        client = RecordingRedis(eval_result=0)
        lease = RedisLease("res", client=client, prefix="p:")
        lease.connect()
        with pytest.raises(ValueError):
            lease.consume("exec-1", FencingToken(7), "c1")

    def test_consume_rejects_empty_correlation_id(self):
        client = RecordingRedis(eval_result=1)
        lease = RedisLease("res", client=client, prefix="p:")
        lease.connect()
        with pytest.raises(ValueError):
            lease.consume("exec-1", FencingToken(7), "")


# =============================================================================
# H) ExecutorFence 桥接 —— 不改 fence.py 一行
# =============================================================================


class TestExecutorFenceRunsOnTheCoordinationBackend:
    def test_bridge_implements_the_full_executor_lease_abc(self, factory):
        bridge = DistributedExecutorLease(lease_factory=factory)
        assert isinstance(bridge, ExecutorLease)

    def test_tokens_are_globally_monotonic_across_executors(self, factory):
        bridge = DistributedExecutorLease(lease_factory=factory)
        t1 = bridge.acquire("exec-1", "o1", 30.0, ["read"])
        t2 = bridge.acquire("exec-2", "o2", 30.0, ["read"])
        t3 = bridge.acquire("exec-3", "o3", 30.0, ["read"])
        assert t1 < t2 < t3

    def test_executor_fence_acquire_and_enforce_unchanged(self, factory):
        """ExecutorFence 用协调后端跑通完整的授权链路。"""
        bridge = DistributedExecutorLease(lease_factory=factory)
        fence = ExecutorFence(bridge, max_executors=3)

        ctx = fence.acquire_for("exec-1", "owner", ["read", "write"], ttl_sec=30.0)
        assert ctx.token is not None
        fence.enforce(ctx, "read", ["read"], correlation_id="c1")
        fence.enforce(ctx, "write", ["read", "write"], correlation_id="c2")

        # 能力升级必须被拒（围栏语义不变）
        from src.kernels.execution.fence import ExecutorFenceDenied

        with pytest.raises(ExecutorFenceDenied):
            fence.enforce(ctx, "admin", ["admin"], correlation_id="c3")

        # 重放必须被拒
        with pytest.raises(ExecutorFenceDenied):
            fence.enforce(ctx, "read", ["read"], correlation_id="c1")

        assert fence.release(ctx) is True

    def test_released_executor_is_unknown_again(self, factory):
        bridge = DistributedExecutorLease(lease_factory=factory)
        token = bridge.acquire("exec-9", "o", 30.0, ["read"])
        assert bridge.release("exec-9", token) is True
        assert bridge.current("exec-9").executor_id is None
        assert bridge.validate("exec-9", token) is False

    def test_a_superseded_executor_cannot_act(self, factory):
        """R-G3-09：被取代的持有者拿着旧令牌执行动作必须被拒（不是双写）。"""
        from src.kernels.execution.fence import ExecutorFenceDenied

        bridge = DistributedExecutorLease(lease_factory=factory)
        fence = ExecutorFence(bridge)
        ctx = fence.acquire_for("exec-old", "o", ["read"], ttl_sec=30.0)
        fence.enforce(ctx, "read", ["read"], correlation_id="ok-1")

        # 纪元推进 == 脑裂恢复时的一次性夺权；旧持有者从此失效。
        bridge.force_new_era()
        with pytest.raises(ExecutorFenceDenied):
            fence.enforce(ctx, "read", ["read"], correlation_id="after-era")

    def test_backend_down_denies_execution_instead_of_allowing_it(self):
        """后端不可达 ⇒ 执行 deny（fail-closed），绝不 best-effort 放行。"""
        from src.kernels.execution.fence import ExecutorFenceDenied

        def dead_factory(_name: str) -> DistributedLease:
            return get_distributed_lease(
                "dead", backend="redis", config=dict(FAST_FAIL)
            )

        # 构造本身就必须炸（拿不到协调后端就不许继续）。
        with pytest.raises(CoordinationUnavailableError):
            DistributedExecutorLease(lease_factory=dead_factory)

        # 即使强行构造出一个已连接但后端已死的 lease，动作也必须被拒。
        bridge = DistributedExecutorLease(lease_factory=factory_for_dead_redis())
        fence = ExecutorFence(bridge)
        with pytest.raises((CoordinationUnavailableError, ExecutorFenceDenied)):
            fence.acquire_for("exec-x", "o", ["read"], ttl_sec=30.0)

    def test_capability_escalation_is_refused(self, factory):
        bridge = DistributedExecutorLease(lease_factory=factory)
        token = bridge.acquire("exec-cap", "o", 30.0, ["read"])
        assert bridge.validate("exec-cap", token, ["read"]) is True
        assert bridge.validate("exec-cap", token, ["admin"]) is False

    def test_replay_of_the_same_correlation_id_is_refused(self, factory):
        from src.kernels.execution.fence import ReplayDetectedError

        bridge = DistributedExecutorLease(lease_factory=factory)
        token = bridge.acquire("exec-replay", "o", 30.0, [])
        bridge.consume_token("exec-replay", token, "same-action")
        with pytest.raises(ReplayDetectedError):
            bridge.consume_token("exec-replay", token, "same-action")

    def test_renew_and_heartbeat(self, factory):
        bridge = DistributedExecutorLease(lease_factory=factory)
        token = bridge.acquire("exec-ren", "o", 30.0, [])
        assert bridge.renew("exec-ren", token, 60.0) == token
        bridge.heartbeat("exec-ren", token)
        assert bridge.validate("exec-ren", token) is True

    def test_an_expired_executor_lease_is_stale(self, factory):
        """过期后围栏必须拒绝 —— 这是跨节点接管能否成立的前提。"""
        bridge = DistributedExecutorLease(lease_factory=factory)
        token = bridge.acquire("exec-exp", "o", 1.0, ["read"])
        assert bridge.validate("exec-exp", token) is True
        _wait_until_expiry(bridge.current("exec-exp").expires_at)
        assert bridge.validate("exec-exp", token) is False
        assert bridge.is_stale("exec-exp", token) is True
        assert bridge.current("exec-exp").state == "stale"

    def test_renewing_a_dead_token_raises(self, factory):
        bridge = DistributedExecutorLease(lease_factory=factory)
        token = bridge.acquire("exec-dead", "o", 30.0, [])
        with pytest.raises(StaleExecutorError):
            bridge.renew("exec-dead", FencingToken(int(token) + 99), 30.0)


# =============================================================================
# I) lock.py —— 公共 API 保留，但真的互斥了
# =============================================================================


class TestDistributedLockIsNowReal:
    def test_two_clients_cannot_both_hold_the_same_key(self, lease_dir):
        a = DistributedLock(backend="file", directory=lease_dir)
        b = DistributedLock(backend="file", directory=lease_dir)
        ok_a, id_a = a.acquire("audit:writer")
        ok_b, id_b = b.acquire("audit:writer")
        assert ok_a is True and id_a is not None
        # 旧实现这里也返回 True —— 那正是"假锁"。
        assert ok_b is False and id_b is None

    def test_lock_id_is_a_fencing_token_not_a_uuid(self, lease_dir):
        a = DistributedLock(backend="file", directory=lease_dir)
        ok, lock_id = a.acquire("k")
        assert ok is True
        assert parse_token(lock_id) is not None, (
            f"lock_id must be a comparable fencing token, got {lock_id!r}"
        )

    def test_release_with_a_bogus_lock_id_is_refused(self, lease_dir):
        """lock.py:120 的直接回归：旧实现任何 lock_id 都能解锁。"""
        a = DistributedLock(backend="file", directory=lease_dir)
        b = DistributedLock(backend="file", directory=lease_dir)
        ok, lock_id = a.acquire("k")
        assert ok is True
        assert b.release("TOTALLY-MADE-UP-ID", "k") is False
        assert a.check("k")[0] is True

    def test_check_returns_false_when_expired(self, lease_dir):
        a = DistributedLock(backend="file", directory=lease_dir, lock_timeout=1000)
        ok, _ = a.acquire("k")
        assert ok is True
        held, status = a.check("k")
        assert held is True
        _wait_until_expiry(status["expires_at"])
        # 旧实现在过期后仍返回 True —— 这里必须是 False。
        assert a.check("k") == (False, None)

    def test_check_returns_false_when_free(self, lease_dir):
        a = DistributedLock(backend="file", directory=lease_dir)
        assert a.check("never-acquired") == (False, None)

    def test_release_then_reacquire_yields_a_greater_token(self, lease_dir):
        a = DistributedLock(backend="file", directory=lease_dir)
        ok, first = a.acquire("k")
        assert a.release(first, "k") is True
        ok2, second = a.acquire("k")
        assert ok2 is True and int(second) > int(first)

    def test_renew_requires_the_right_lock_id(self, lease_dir):
        a = DistributedLock(backend="file", directory=lease_dir)
        ok, lock_id = a.acquire("k")
        assert a.renew("wrong-id", "k", 5000) is False
        assert a.renew(lock_id, "k", 5000) is True


# =============================================================================
# J) election.py —— 租约选主
# =============================================================================


class TestLeaderElectionIsLeaseBased:
    def _two(self, lease_dir, **kw):
        a = LeaderElection(lease=FileLockLease("el", directory=lease_dir), **kw)
        b = LeaderElection(lease=FileLockLease("el", directory=lease_dir), **kw)
        return a, b

    def test_only_one_leader_at_a_time(self, lease_dir):
        a, b = self._two(lease_dir)
        assert a.start_election("node-1") is True
        assert b.start_election("node-2") is False
        # 旧实现两者都返回 True（dict 插入顺序裁决）。
        assert a.is_leader("node-1") is True
        assert b.is_leader("node-2") is False
        assert not (a.is_leader("node-1") and b.is_leader("node-2"))

    def test_renew_lease_actually_renews(self, lease_dir):
        """旧实现因为先赋值后比较而恒返回 False。"""
        a, _ = self._two(lease_dir)
        a.start_election("node-1")
        assert a.renew_lease("node-1") is True

    def test_is_leader_demotes_when_the_lease_expires(self, lease_dir):
        a, _ = self._two(lease_dir, lease_duration=2000, renewal_deadline=60000)
        assert a.start_election("solo") is True
        a._stop_renewal()  # 模拟续租线程死掉
        assert a.is_leader("solo") is True
        # lease_duration=2000ms；等到它真正过去（不睡固定秒数）。
        _wait_until_expiry(time.time() + 2.0)
        # 旧实现这里永远 True（不查 TTL）—— 脑裂的教科书成因。
        assert a.is_leader("solo") is False

    def test_demotion_notifies_listeners(self, lease_dir):
        a, _ = self._two(lease_dir, lease_duration=2000, renewal_deadline=60000)
        events = []
        a.add_listener(lambda cid, ok: events.append((cid, ok)))
        a.start_election("solo")
        a._stop_renewal()
        _wait_until_expiry(time.time() + 2.0)
        a.is_leader("solo")
        assert ("solo", True) in events
        assert ("solo", False) in events

    def test_step_down_lets_another_take_over_with_a_greater_token(self, lease_dir):
        a, b = self._two(lease_dir)
        a.start_election("node-1")
        first = a.current_token()
        assert a.step_down("node-1") is True
        assert b.start_election("node-2") is True
        assert int(b.current_token()) > int(first)

    def test_stop_renewal_actually_stops_the_thread(self, lease_dir):
        """旧实现 _stop_renewal() 是 pass —— 线程永远不停。"""
        a, _ = self._two(lease_dir, lease_duration=1000, renewal_deadline=100)
        a.start_election("solo")
        assert a._renewal_task is not None and a._renewal_task.is_alive()
        a._stop_renewal()
        assert a._renewal_task is None

    def test_stats_report_the_real_lease_state(self, lease_dir):
        a, _ = self._two(lease_dir)
        a.start_election("node-1")
        stats = a.get_stats()
        assert stats["is_leader"] is True
        assert stats["lease_holder"] == "node-1"
        assert stats["lease_state"] == "held"


# =============================================================================
# K) "旧实现正是这里说谎" —— 把旧逻辑摆回来，证明新实现在同一位置拒绝
# =============================================================================


class _LegacyDistributedLock:
    """旧 ``src/distribution/lock.py`` 的核心，原样搬来作为对照组。

    它不是被测代码，而是"缺陷样本"：用来证明下面的断言确实打在旧实现说谎的
    那个位置上（同样手法见 test_bus_backends.py:430-470）。
    """

    def __init__(self):
        self._locked_keys = {}

    def acquire(self, lock_key, timeout=30000):
        import uuid

        info = self._locked_keys.get(lock_key)
        if info is not None and time.time() < info["expires_at"]:
            return False, None
        self._locked_keys[lock_key] = {
            "acquired_at": time.time(),
            "expires_at": time.time() + timeout / 1000.0,
        }
        return True, str(uuid.uuid4())

    def release(self, lock_id, lock_key):
        if lock_key in self._locked_keys:
            del self._locked_keys[lock_key]  # <-- 从不看 lock_id
            return True
        return False

    def check(self, lock_key):
        info = self._locked_keys.get(lock_key)
        if info is None:
            return False, None
        return True, {"is_held": time.time() < info["expires_at"]}


class TestLegacyLiedExactlyWhereTheNewOneRefuses:
    def test_legacy_granted_two_holders_the_new_one_does_not(self, lease_dir):
        # 旧：两个实例各自一份 dict，都能拿到锁。
        l1, l2 = _LegacyDistributedLock(), _LegacyDistributedLock()
        assert l1.acquire("k")[0] is True
        assert l2.acquire("k")[0] is True  # 旧实现说谎的位置

        # 新：同一个位置必须拒绝。
        n1 = DistributedLock(backend="file", directory=lease_dir)
        n2 = DistributedLock(backend="file", directory=lease_dir)
        assert n1.acquire("k")[0] is True
        assert n2.acquire("k")[0] is False

    def test_legacy_released_with_any_lock_id_the_new_one_refuses(self, lease_dir):
        legacy = _LegacyDistributedLock()
        _, _lock_id = legacy.acquire("k")
        assert legacy.release("ANY-GARBAGE-AT-ALL", "k") is True  # 旧实现说谎的位置

        new = DistributedLock(backend="file", directory=lease_dir)
        ok, real_id = new.acquire("k")
        assert ok is True
        assert new.release("ANY-GARBAGE-AT-ALL", "k") is False
        assert new.release(real_id, "k") is True

    def test_legacy_check_says_true_after_expiry_the_new_one_says_false(self, lease_dir):
        legacy = _LegacyDistributedLock()
        legacy.acquire("k", timeout=200)
        time.sleep(0.4)
        held, status = legacy.check("k")
        assert held is True  # 旧实现说谎：过期了仍报 True
        assert status["is_held"] is False  # 而且与自身字段自相矛盾

        new = DistributedLock(backend="file", directory=lease_dir, lock_timeout=200)
        new.acquire("k")
        time.sleep(0.4)
        assert new.check("k") == (False, None)


# =============================================================================
# K2) P0-1 —— 失败即闭：后端 I/O 故障必须是围栏拒绝，绝不能是裸 OSError
# =============================================================================


class _ExplodingStore:
    """每次操作都抛指定异常的假 store（模拟磁盘/权限/SQLite 故障）。"""

    def __init__(self, exc: BaseException):
        self.exc = exc

    def read(self, name):
        raise self.exc

    def write(self, *a, **k):
        raise self.exc

    def count_live(self, prefix=None, now=None):
        raise self.exc

    def next_global_token(self):
        raise self.exc

    def bump_epoch(self, name):
        raise self.exc

    def consume(self, *a, **k):
        raise self.exc


class TestFailClosedIO:
    """P0-1：裸 ``PermissionError`` 绝不能逃出围栏。

    旧实现里 ``os.open`` / ``os.replace`` 都在 try 之外，一个 PermissionError
    会一路冒到调用方；调用方若写的是 ``except Exception: pass``，动作就会
    **在未持租约的情况下被执行**。
    """

    def test_permission_error_on_acquire_becomes_a_fence_denial(self, factory):
        bridge = DistributedExecutorLease(lease_factory=factory)
        from src.kernels.execution.fence import ExecutorFence

        broken = FileLockLease("boom", directory=bridge._era_lease._dir)
        broken._store = _ExplodingStore(PermissionError(13, "Permission denied"))
        bridge._leases["exec-x"] = broken
        bridge._era_lease._store = _ExplodingStore(PermissionError(13, "denied"))

        fence = ExecutorFence(bridge)
        with pytest.raises(ExecutorFenceBackendError) as exc:
            fence.acquire_for("exec-x", "o", ["read"], ttl_sec=30.0)
        # 必须是围栏拒绝（ExecutorFenceDenied 的子类），不是 OSError。
        assert isinstance(exc.value, ExecutorFenceDenied)
        assert not isinstance(exc.value, OSError)

    def test_permission_error_is_not_leaked_as_oserror(self, factory):
        """直接断言：抛出的东西不是 OSError 的子类。"""
        bridge = DistributedExecutorLease(lease_factory=factory)
        lease = bridge._lease_for("exec-y")
        lease._store = _ExplodingStore(PermissionError(13, "denied"))
        with pytest.raises(ExecutorFenceBackendError):
            bridge.acquire("exec-y", "o", 30.0, ["read"])
        with pytest.raises(ExecutorFenceBackendError):
            bridge.validate("exec-y", FencingToken(1))

    def test_raw_oserror_from_os_open_is_translated(self, lease_dir):
        """os.open 失败必须变成 CoordinationUnavailableError（不是 PermissionError）。"""
        lease = FileLockLease("perm", directory=lease_dir)
        real_open = os.open

        def boom(path, flags, mode=0o777, **kw):
            raise PermissionError(13, "Permission denied")

        os.open = boom
        try:
            with pytest.raises(CoordinationUnavailableError) as exc:
                lease.acquire("owner", 30.0)
            assert not isinstance(exc.value, PermissionError)
        finally:
            os.open = real_open

    def test_any_backend_exception_surfaces_as_fence_denied(self, factory):
        """不只是 OSError —— 任何后端异常都不能逃逸成非拒绝型异常。"""
        bridge = DistributedExecutorLease(lease_factory=factory)
        for exc in (
            OSError(5, "boom"),
            RuntimeError("boom"),
            sqlite3.DatabaseError("boom"),
        ):
            lease = bridge._lease_for("exec-z")
            lease._store = _ExplodingStore(exc)
            with pytest.raises(ExecutorFenceDenied):
                bridge.acquire("exec-z", "o", 30.0, [])

    def test_every_fence_boundary_call_is_guarded(self, factory):
        """围栏边界上的**每一个**调用（含 consume / current / count）都必须翻译。"""
        bridge = DistributedExecutorLease(lease_factory=factory)
        tok = bridge.acquire("exec-g", "o", 30.0, ["read"])
        lease = bridge._lease_for("exec-g")
        lease._store = _ExplodingStore(OSError(5, "boom"))
        for call in (
            lambda: bridge.acquire("exec-g", "o", 30.0, []),
            lambda: bridge.validate("exec-g", tok),
            lambda: bridge.current("exec-g"),
            lambda: bridge.heartbeat("exec-g", tok),
            lambda: bridge.renew("exec-g", tok, 30.0),
            lambda: bridge.release("exec-g", tok),
            lambda: bridge.consume_token("exec-g", tok, "c1"),
            lambda: bridge.known_executor("exec-g"),
        ):
            with pytest.raises(ExecutorFenceDenied):
                call()


# =============================================================================
# K3) P0-2 —— Windows 上"并发读者 + 写者"必须都能推进
# =============================================================================


class TestConcurrentReadersAndWriter:
    """P0-2：围栏热路径本身就是 reader（``_enforce`` 每动作都读）。

    裸 JSON sidecar + ``os.replace`` 在 Windows 上会在这里失效：读者的句柄不带
    ``FILE_SHARE_DELETE``，写者的替换被拒（实测 3 读者 → 写者 0/400 成功）。
    后端因此换成 SQLite(WAL)。本组测试就是那条回归防线。
    """

    def test_writer_makes_progress_with_three_concurrent_readers(self, tmp_path):
        lease_dir = tmp_path / "leases"
        lease_dir.mkdir(exist_ok=True)
        writer_owner = "the-writer"

        pairs = []
        for i in range(3):  # N >= 3 读者
            out = lease_dir / f"reader-{i}.json"
            pairs.append(
                _spawn(
                    tmp_path,
                    {
                        "mode": "reader",
                        "dir": str(lease_dir),
                        "name": "hot",
                        "out": str(out),
                        "repo": REPO_ROOT,
                        "seconds": 4.0,
                        "expected_holder": writer_owner,
                    },
                )
            )
        out_w = lease_dir / "writer.json"
        pairs.append(
            _spawn(
                tmp_path,
                {
                    "mode": "writer",
                    "dir": str(lease_dir),
                    "name": "hot",
                    "out": str(out_w),
                    "repo": REPO_ROOT,
                    "attempts": 400,
                    "writer_owner": writer_owner,
                    "ttl": 10.0,
                },
            )
        )

        results = _collect(pairs, timeout=180)
        readers = [r for r in results if r["mode"] == "reader"]
        (writer,) = [r for r in results if r["mode"] == "writer"]

        # 1) 写者必须完成**有意义数量**的写入（旧实现在 Windows 上是 0）
        assert writer["attempts"] == 400
        assert writer["ok"] > 0, (
            f"writer completed {writer['ok']}/400 writes under concurrent readers; "
            f"errors: {writer['errors']}"
        )
        assert writer["error_count"] == 0, writer["errors"]

        # 2) 读者必须真的读到了东西，且一次撕裂都没看到
        assert len(readers) == 3
        assert all(r["reads"] > 0 for r in readers)
        assert all(r["anomalies"] == 0 for r in readers), readers
        assert all(r.get("last_error") is None for r in readers)

    def test_state_store_is_wal_so_readers_never_block_the_writer(self, lease_dir):
        """锁住 P0-2 的**机制选择**（行为证据在上面的 3 读者 + 1 写者）。

        诚实说明：单靠行为测试区分不出 WAL 与 rollback journal —— ``_with_retry``
        + ``busy_timeout`` 会把 DELETE 模式下的 "database is locked" 也重试成
        成功（变异 M5 因此**存活**）。这条断言把"我们确实选了 WAL"固定下来，
        但它证明的是设计选择，不是运行时效果。
        """
        store = FileLockLease("wal-probe", directory=lease_dir)._store
        (mode,) = store._execute("PRAGMA journal_mode")[0]
        assert str(mode).lower() == "wal", mode

    def test_readers_never_observe_a_torn_state(self, tmp_path):
        """同一目录内高频读 + 写；每次读到的状态必须自洽。"""
        lease_dir = tmp_path / "leases"
        lease_dir.mkdir(exist_ok=True)
        lease = FileLockLease("hot2", directory=lease_dir)
        writer = FileLockLease("hot2", directory=lease_dir)

        ok, token = writer.acquire("w", 30.0)
        assert ok is True

        anomalies = 0
        deadline = time.time() + 2.0
        reads = 0
        while time.time() < deadline:
            view = lease.current()
            reads += 1
            if (view.token is None) != (view.holder is None):
                anomalies += 1
            if view.state not in ("held", "expired", "free", "released"):
                anomalies += 1
            # 写者在循环里不断续租（写），读者同时读
            writer.renew("w", token, 30.0)
        assert reads > 0
        assert anomalies == 0


# =============================================================================
# K4) P0-3 —— 桥接必须可以安全接线
# =============================================================================


class TestBridgeStateIsBackendGlobal:
    """P0-3(a)：能力授权与纪元必须**持久化在后端**，跨进程可读。"""

    def test_a_process_that_never_acquired_can_see_the_grant(self, tmp_path):
        """旧实现里 bridgeB 看到 granted_capabilities=()，无法区分 unknown/stale。"""
        lease_dir = tmp_path / "leases"
        lease_dir.mkdir(exist_ok=True)
        out_a = lease_dir / "a.json"
        (res_a,) = _collect(
            [
                _spawn(
                    tmp_path,
                    {
                        "mode": "exec",
                        "dir": str(lease_dir),
                        "name": "unused",
                        "out": str(out_a),
                        "repo": REPO_ROOT,
                        "exec_id": "shared-exec",
                        "action": "acquire",
                        "ttl": 30.0,
                        "caps": ["read", "write"],
                    },
                )
            ]
        )
        assert res_a.get("token") is not None, res_a

        # 另一个进程：从未 acquire 过，直接读
        out_b = lease_dir / "b.json"
        (res_b,) = _collect(
            [
                _spawn(
                    tmp_path,
                    {
                        "mode": "exec",
                        "dir": str(lease_dir),
                        "name": "unused",
                        "out": str(out_b),
                        "repo": REPO_ROOT,
                        "exec_id": "shared-exec",
                        "action": "validate",
                        "token": res_a["token"],
                    },
                )
            ]
        )
        assert res_b["known"] is True
        # 授权必须可见（旧实现这里是 ()）
        assert set(res_b["caps"]) == {"read", "write"}
        # 但不能通过 validate —— 同一时刻只允许持有它的那个进程
        assert res_b["validate"] is False
        # 关键：state 能区分"stale"（有租约但不是我）而不是 unknown
        assert res_b["state"] == "stale"

    def test_unknown_executor_is_distinguishable_from_stale(self, factory):
        bridge = DistributedExecutorLease(lease_factory=factory)
        unknown = bridge.current("never-seen")
        assert unknown.executor_id is None
        assert unknown.state == "unknown"
        assert bridge.known_executor("never-seen") is False


class TestGlobalExecutorCap:
    """P0-3(b)：``max_executors`` 的 N+1 上限必须**跨进程**成立。

    旧实现的 ``count_active()`` 是进程内视图：process1 填满 2 个，新起的
    process2 看到 0 又加了 2 个 → 4 个活跃执行者对着上限 2。
    """

    def test_cap_is_enforced_across_separate_processes(self, tmp_path):
        lease_dir = tmp_path / "leases"
        lease_dir.mkdir(exist_ok=True)

        pairs = []
        for i in range(4):  # 上限 2，起 4 个进程各占 1 个 executor
            out = lease_dir / f"cap-{i}.json"
            pairs.append(
                _spawn(
                    tmp_path,
                    {
                        "mode": "exec",
                        "dir": str(lease_dir),
                        "name": "unused",
                        "out": str(out),
                        "repo": REPO_ROOT,
                        "exec_id": f"exec-{i}",
                        "action": "enforce",
                        "max_executors": 2,
                    },
                )
            )
        results = _collect(pairs)

        allowed = [r for r in results if r.get("allowed")]
        denied = [r for r in results if "error" in r]
        # 上限 2 ⇒ 最多 2 个进程能拿到执行租约
        assert len(allowed) <= 2, (
            f"max_executors=2 but {len(allowed)} processes acquired: {results}"
        )
        assert len(denied) >= 1, f"expected some processes to be denied: {results}"

    def test_cap_is_enforced_atomically_in_one_process(self, factory):
        """上限判定必须与 acquire 在同一临界区内（续租不算新增）。"""
        bridge = DistributedExecutorLease(lease_factory=factory, max_executors=2)
        assert bridge.max_executors == 2
        t1 = bridge.acquire("cap-a", "o", 30.0, [])
        t2 = bridge.acquire("cap-b", "o", 30.0, [])
        assert t2 > t1
        with pytest.raises(ExecutorLimitExceeded):
            bridge.acquire("cap-c", "o", 30.0, [])
        # 已持有者续租不占新名额
        assert bridge.acquire("cap-a", "o", 30.0, []) == t1
        # 释放一个后名额回来
        assert bridge.release("cap-b", t2) is True
        assert bridge.acquire("cap-c", "o", 30.0, []) > t2

    def test_count_active_sees_other_processes_leases(self, factory):
        """count_active 必须是后端全局计数，不是本进程见过几个。"""
        bridge = DistributedExecutorLease(lease_factory=factory)
        assert bridge.count_active() == 0
        bridge.acquire("exec-a", "o", 30.0, [])
        bridge.acquire("exec-b", "o", 30.0, [])
        assert bridge.count_active() == 2
        # 一个**全新的** bridge（模拟另一个进程）也必须看到 2
        fresh = DistributedExecutorLease(lease_factory=factory)
        assert fresh.count_active() == 2


class TestOnlyOneProcessPerExecutorId:
    """P0-3(c)：同一 executor_id 在同一时刻最多只有一个进程能通过校验。"""

    def test_two_processes_cannot_both_act_as_the_same_executor(self, tmp_path):
        """旧实现：A 与 B 都拿到 token=2，都 validate True，都被 enforce 放行。"""
        lease_dir = tmp_path / "leases"
        lease_dir.mkdir(exist_ok=True)

        def run(exec_id, action, token=None, ttl=1.0):
            out = lease_dir / f"{action}-{exec_id}-{uuid.uuid4().hex[:8]}.json"
            payload = {
                "mode": "exec",
                "dir": str(lease_dir),
                "name": "unused",
                "out": str(out),
                "repo": REPO_ROOT,
                "exec_id": exec_id,
                "action": action,
                "ttl": ttl,
            }
            if token is not None:
                payload["token"] = token
            (res,) = _collect([_spawn(tmp_path, payload)])
            return res

        # A 获取（长 TTL —— 子进程启动本身要花 ~1s，短 TTL 会让"A 还活着"这个
        # 前提在 B 尝试之前就失效，测试就会假失败）
        res_a = run("shared", "acquire", ttl=60.0)
        assert res_a.get("token") is not None, res_a
        assert res_a["validate"] is True, res_a
        token_a = res_a["token"]
        # 围栏令牌必须是可比较的正整数（绝不是 uuid4 字符串）
        assert isinstance(token_a, int) and token_a >= 1, res_a

        # B 立刻尝试：A 还活着 → 必须被拒（不能拿到同一个令牌）
        res_b = run("shared", "acquire", ttl=60.0)
        assert res_b.get("token") is None, (
            f"B must not acquire while A holds the id; got {res_b}"
        )
        assert "error" in res_b, res_b
        # 且 A 的令牌不能被 B 的尝试动过
        assert res_b.get("state") == "stale", res_b

        # 过期接管：换一个 id，短 TTL，等它真正过期（等到**记录的到期时刻**之后）
        res_c = run("shared-exp", "acquire", ttl=1.0)
        assert res_c.get("token") is not None, res_c
        token_c = res_c["token"]
        time.sleep(1.5)
        res_d = run("shared-exp", "acquire", ttl=60.0)
        assert res_d.get("token") is not None, res_d
        assert res_d["token"] > token_c, (
            f"takeover token {res_d['token']} must be > {token_c}"
        )

        # 此刻旧令牌必须失效（哪怕换了个进程，令牌本身也不该再被接受）
        res_e = run("shared-exp", "validate", token=token_c)
        assert res_e["validate"] is False

    def test_at_most_one_process_validates_at_any_moment(self, tmp_path):
        lease_dir = tmp_path / "leases"
        lease_dir.mkdir(exist_ok=True)
        out = lease_dir / "owner.json"
        (res,) = _collect(
            [
                _spawn(
                    tmp_path,
                    {
                        "mode": "exec",
                        "dir": str(lease_dir),
                        "name": "unused",
                        "out": str(out),
                        "repo": REPO_ROOT,
                        "exec_id": "solo-exec",
                        "action": "acquire",
                        "ttl": 30.0,
                    },
                )
            ]
        )
        token = res["token"]
        assert res["validate"] is True

        # 另起 3 个进程同时校验同一个 (executor_id, token)
        pairs = []
        for i in range(3):
            o = lease_dir / f"v-{i}.json"
            pairs.append(
                _spawn(
                    tmp_path,
                    {
                        "mode": "exec",
                        "dir": str(lease_dir),
                        "name": "unused",
                        "out": str(o),
                        "repo": REPO_ROOT,
                        "exec_id": "solo-exec",
                        "action": "validate",
                        "token": token,
                    },
                )
            )
        results = _collect(pairs)
        # 持有者进程自己 validate=True；其余 3 个必须全是 False
        assert all(r["validate"] is False for r in results), results

    def test_takeover_never_hands_the_incumbent_token_to_another_owner(self, lease_dir):
        a = FileLockLease("tk", directory=lease_dir)
        b = FileLockLease("tk", directory=lease_dir)
        ok1, t1 = a.acquire("owner-A", 30.0)
        assert ok1 is True
        # A 还活着时，B 绝不能拿到 A 的令牌
        ok2, t2 = b.acquire("owner-B", 30.0)
        assert ok2 is False and t2 is None
        # A 释放后 B 接管 → 新令牌严格更大（不是复用 t1）
        assert a.release("owner-A", t1) is True
        ok3, t3 = b.acquire("owner-B", 30.0)
        assert ok3 is True and int(t3) > int(t1)


class TestHeartbeatFailureIsObservable:
    """P0-3(d)：心跳失败必须可见（旧实现成功失败都返回 None 并吞掉异常）。"""

    def test_heartbeat_returns_the_renewed_token(self, factory):
        bridge = DistributedExecutorLease(lease_factory=factory)
        token = bridge.acquire("exec-hb", "o", 30.0, [])
        assert bridge.heartbeat("exec-hb", token) == token
        assert bridge.heartbeat_failures == 0

    def test_heartbeat_on_a_dead_lease_raises_and_counts(self, factory):
        bridge = DistributedExecutorLease(lease_factory=factory)
        token = bridge.acquire("exec-hb2", "o", 30.0, [])
        assert bridge.release("exec-hb2", token) is True
        with pytest.raises(StaleExecutorError):
            bridge.heartbeat("exec-hb2", token)
        assert bridge.heartbeat_failures == 1

    def test_heartbeat_with_a_garbage_token_raises(self, factory):
        bridge = DistributedExecutorLease(lease_factory=factory)
        bridge.acquire("exec-hb3", "o", 30.0, [])
        with pytest.raises(StaleExecutorError):
            bridge.heartbeat("exec-hb3", "not-a-token")
        assert bridge.heartbeat_failures == 1


# =============================================================================
# K5) P1-1 —— 释放时"令牌校验"必须有**文件后端**的杀得死的测试
# =============================================================================


class TestReleaseWithCorrectOwnerButStaleToken:
    """P1-1：变异 M1（删掉 release 的令牌校验）曾经存活。

    原因：所有文件后端的 release 测试都用**错误的 owner**，持有者检查先把它拦下，
    令牌比较根本没被走到；唯一"正确 owner + 错误令牌"的测试在被 skip 的 Redis
    类里。下面两条把这条路径钉死在文件后端上。
    """

    def test_file_release_with_correct_owner_but_stale_token_is_refused(self, lease_dir):
        lease = FileLockLease("k", directory=lease_dir)
        ok, tok = lease.acquire("alice", 30.0)
        assert ok is True
        # 正确 owner + 陈旧/被取代的令牌：绝不能释放
        assert lease.release("alice", FencingToken(int(tok) + 1)) is False
        assert lease.release("alice", FencingToken(int(tok) * 100 + 7)) is False
        assert lease.current().holder == "alice"
        assert lease.validate("alice", tok) is True
        # 真令牌仍然有效
        assert lease.release("alice", tok) is True

    def test_bridge_release_with_a_superseded_token_does_not_free(self, lease_dir):
        d = lease_dir
        bridge = DistributedExecutorLease(lease_factory=lambda n: FileLockLease(n, directory=d))
        tok = bridge.acquire("exec-1", "owner", 30.0, ["read"])
        # 正确的 executor_id + 错误的令牌
        assert bridge.release("exec-1", FencingToken(int(tok) + 1)) is False
        assert bridge.validate("exec-1", tok) is True
        assert bridge.release("exec-1", tok) is True
        assert bridge.validate("exec-1", tok) is False

    def test_release_under_contention_is_not_confused_with_denial(self, lease_dir):
        """P1-1 尾声：竞争（可重试）与拒绝（确定）必须分开。

        持续并发 release 时，正确令牌的 release 必须**全部成功**（旧实现约 17%
        因为锁竞争假失败），而错误令牌的 release 必须**全部失败**。
        """
        lease = FileLockLease("contend", directory=lease_dir)
        ok, tok = lease.acquire("owner", 300.0)
        assert ok is True

        errors = []
        for _ in range(60):
            if not lease.release("owner", tok):
                errors.append("correct token refused")
            # 重新拿回来（续租），保证下一次 release 仍然合法
            ok2, tok2 = lease.acquire("owner", 300.0)
            assert ok2 is True
            tok = tok2
        assert errors == [], f"{len(errors)} correct releases were refused"

        # 错误令牌：无论有没有并发，都必须拒绝
        wrong = [lease.release("owner", FencingToken(int(tok) + 1)) for _ in range(20)]
        assert all(r is False for r in wrong)


# =============================================================================
# K6) 与 SqliteExecutorLease 的行为等价性
# =============================================================================


class TestParityWithSqliteExecutorLease:
    """P0-3(d)：在相同输入下，桥接应与仓库既有的 SqliteExecutorLease 同行为。

    （验证者曾指出：Sqlite 抛 FencedExecutorError 的地方，桥接不抛。）
    """

    def _sqlite(self, tmp_path):
        import sqlite3 as _sq

        from src.kernels.execution.fence import SqliteExecutorLease

        tmp_path.mkdir(parents=True, exist_ok=True)
        conn = _sq.connect(str(tmp_path / "fence.db"))
        return SqliteExecutorLease(conn)

    def _bridge(self, tmp_path):
        d = tmp_path / "leases"
        d.mkdir(parents=True, exist_ok=True)
        return DistributedExecutorLease(lease_factory=lambda n: FileLockLease(n, directory=str(d)))

    def test_both_raise_fenced_when_the_token_was_superseded(self, tmp_path):
        """两者都必须在"我记住的令牌被别人超过"时抛 FencedExecutorError。

        语义：``my_last_token`` = 我上次持有的令牌。后端现存的令牌**大于**它 ⇒
        有人在我之后夺权了 ⇒ 我必须被围栏。（传一个**更大**的值不构成夺权，
        所以这里传 ``t1 - 1``。）
        """
        from src.kernels.execution.fence import FencedExecutorError

        for name, mk in (("sqlite", self._sqlite), ("bridge", self._bridge)):
            lease = mk(tmp_path / name)
            t1 = lease.acquire("e1", "o", 30.0, [])
            assert t1 is not None
            with pytest.raises(FencedExecutorError):
                lease.acquire("e1", "o", 30.0, [], None, t1 - 1)

    def test_both_allow_expired_reacquire_after_fix(self, tmp_path):
        """After the fence.py fix, an expired (non-released) lease can be
        re-acquired by BOTH the production-default SqliteExecutorLease and the
        distributed bridge -- a crashed/restarted executor is no longer locked
        out forever.

        Previously SqliteExecutorLease raised FencedExecutorError on any
        still-present row (my_last_token defaulted to -1); that was a bug and is
        now fixed. The fence signal only fires while the current lease is still
        strictly live, so an expired lease's holder is no longer authoritative.
        """
        for name, mk in (("sqlite", self._sqlite), ("bridge", self._bridge)):
            lease = mk(tmp_path / name)
            t1 = lease.acquire("e-div", "o", 0.3, [])
            assert t1 is not None
            time.sleep(0.6)
            # Re-acquire after expiry must NOT raise FencedExecutorError.
            t2 = lease.acquire("e-div", "o", 30.0, [])
            assert t2 is not None
            assert lease.validate("e-div", t2) is True

        # The bridge additionally mints a strictly greater token and invalidates
        # the old one (the production sqlite lease renews in place instead).
        br = self._bridge(tmp_path / "bridge-extra")
        bt1 = br.acquire("e-div2", "o", 0.3, [])
        time.sleep(0.6)
        bt2 = br.acquire("e-div2", "o", 30.0, [])
        assert bt2 > bt1
        assert br.validate("e-div2", bt1) is False

    def test_both_deny_capability_escalation(self, tmp_path):
        for name, mk in (("sqlite", self._sqlite), ("bridge", self._bridge)):
            lease = mk(tmp_path / name)
            tok = lease.acquire("e2", "o", 30.0, ["read"])
            assert lease.validate("e2", tok, ["read"]) is True
            assert lease.validate("e2", tok, ["admin"]) is False

    def test_both_detect_replay(self, tmp_path):
        from src.kernels.execution.fence import ReplayDetectedError

        for name, mk in (("sqlite", self._sqlite), ("bridge", self._bridge)):
            lease = mk(tmp_path / name)
            tok = lease.acquire("e3", "o", 30.0, [])
            lease.consume_token("e3", tok, "same")
            with pytest.raises(ReplayDetectedError):
                lease.consume_token("e3", tok, "same")

    def test_both_report_held_then_stale_consistently(self, tmp_path):
        for name, mk in (("sqlite", self._sqlite), ("bridge", self._bridge)):
            lease = mk(tmp_path / name)
            tok = lease.acquire("e4", "o", 30.0, ["read"])
            assert lease.current("e4").state == "held"
            assert lease.is_stale("e4", tok) is False
            assert lease.release("e4", tok) is True
            assert lease.is_stale("e4", tok) is True

    def test_both_force_new_era_invalidates(self, tmp_path):
        for name, mk in (("sqlite", self._sqlite), ("bridge", self._bridge)):
            lease = mk(tmp_path / name)
            tok = lease.acquire("e5", "o", 30.0, [])
            assert lease.validate("e5", tok) is True
            lease.force_new_era()
            assert lease.validate("e5", tok) is False


# =============================================================================
# L) 需要真实 Redis 的端到端（无服务时显式跳过并说明原因）
# =============================================================================


def _redis_available() -> bool:
    import socket

    sock = socket.socket()
    sock.settimeout(0.3)
    try:
        sock.connect(("127.0.0.1", 6379))
        return True
    except OSError:
        return False
    finally:
        sock.close()


try:  # pragma: no cover - 取决于本机装没装
    import fakeredis as _fakeredis  # noqa: F401

    _HAS_FAKEREDIS = True
except Exception:  # noqa: BLE001
    _HAS_FAKEREDIS = False


@pytest.mark.skipif(
    not _HAS_FAKEREDIS,
    reason="需要 fakeredis（+lupa 才有 Lua 解释器）才能真跑 Lua 脚本。"
    "⚠️ 跳过即意味着 Redis 路径**仍未被执行验证**。",
)
class TestRedisLuaScriptsActuallyExecute:
    """把 RedisLease 的 4 段 Lua **真的执行一遍**。

    与 :class:`TestRealRedisEndToEnd` 的区别：那组要真 ``redis-server``；这组只需
    ``fakeredis``（共享 ``FakeServer`` 的两个 client = 两个"节点"共享同一份状态），
    配合 ``lupa`` 就是**真 Lua 解释器**，因此 compare-and-delete / INCR 单调令牌 /
    ``PX`` 过期 / ``consume`` 的 SET-NX 这几条运行时语义可以在本机被验证。

    ``fakeredis`` / ``lupa`` 现已声明进 ``pyproject.toml`` 的
    ``[project.optional-dependencies].tests``；CI 用 ``pip install -e .[tests]``
    即可让这组**真正执行**（不再 skip）。本机（venv 已装 lupa）会实跑；缺 lupa 时
    ``nodes`` fixture 的 eval 自检会优雅 skip，不会把收集搞挂。
    """

    @pytest.fixture
    def nodes(self):
        import fakeredis

        server = fakeredis.FakeServer()
        c1 = fakeredis.FakeStrictRedis(server=server)
        c2 = fakeredis.FakeStrictRedis(server=server)
        try:
            assert c1.eval("return 1", 0) == 1
        except Exception as exc:  # noqa: BLE001
            pytest.skip(f"fakeredis 无法执行 Lua（需要 lupa）: {type(exc).__name__}: {exc}")
        return c1, c2

    def test_acquire_is_mutually_exclusive_across_two_clients(self, nodes):
        c1, c2 = nodes
        a = RedisLease("lua-shared", client=c1)
        b = RedisLease("lua-shared", client=c2)
        a.connect()
        b.connect()
        ok_a, tok_a = a.acquire("node-A", 30.0)
        ok_b, tok_b = b.acquire("node-B", 30.0)
        assert ok_a is True and int(tok_a) >= 1
        assert ok_b is False and tok_b is None

    def test_release_with_a_stale_token_does_not_free_the_lease(self, nodes):
        c1, c2 = nodes
        a = RedisLease("lua-rel", client=c1)
        b = RedisLease("lua-rel", client=c2)
        a.connect()
        b.connect()
        ok, tok = a.acquire("node-A", 30.0)
        assert ok is True
        # 错的 owner / 错的令牌，都不能释放别人的租约
        assert a.release("node-B", tok) is False
        assert b.release("node-A", tok.next()) is False
        assert a.validate("node-A", tok) is True

    def test_renew_with_a_stale_token_returns_none(self, nodes):
        c1, _ = nodes
        a = RedisLease("lua-renew", client=c1)
        a.connect()
        ok, tok = a.acquire("node-A", 30.0)
        assert ok is True
        assert a.renew("node-A", tok, 60.0) == tok
        assert a.renew("node-A", FencingToken(int(tok) + 999), 60.0) is None

    def test_expiry_allows_takeover_with_a_strictly_greater_token(self, nodes):
        c1, c2 = nodes
        a = RedisLease("lua-ttl", client=c1)
        b = RedisLease("lua-ttl", client=c2)
        a.connect()
        b.connect()
        ok1, tok1 = a.acquire("node-A", 0.3)
        assert ok1 is True
        # TTL 未到 ⇒ 不许提前夺权
        ok_early, _ = b.acquire("node-B", 30.0)
        assert ok_early is False
        time.sleep(0.6)
        ok2, tok2 = b.acquire("node-B", 30.0)
        assert ok2 is True and int(tok2) > int(tok1)
        assert a.validate("node-A", tok1) is False

    def test_bridge_on_redis_persists_capabilities_and_era(self, nodes):
        """P0-3(a) 在 Redis 后端上同样成立：授权写进后端，别的"节点"读得到。"""
        c1, c2 = nodes
        b1 = DistributedExecutorLease(lease_factory=lambda n: RedisLease(n, client=c1))
        b2 = DistributedExecutorLease(lease_factory=lambda n: RedisLease(n, client=c2))
        b2._holder = "another-node"  # 同进程内 holder 相同，这里显式换个身份
        tok = b1.acquire("exec-r", "o", 30.0, ["read", "write"])
        assert b1.validate("exec-r", tok, ["read"]) is True
        assert b1.validate("exec-r", tok, ["admin"]) is False
        view = b2.current("exec-r")
        assert set(view.granted_capabilities) == {"read", "write"}
        assert view.state == "stale"  # 不是 unknown
        assert b2.validate("exec-r", tok) is False
        b1.force_new_era()
        assert b1.validate("exec-r", tok) is False

    def test_consume_is_cross_node_persistent(self, nodes):
        """P1-2 修复点：Redis 后端的 replay 去重必须**跨节点**成立。

        旧实现里 ``DistributedExecutorLease`` 对 Redis 后端静默退回进程内
        ``set``，两个节点各记各的、跨节点重放检测彻底失效。现在 ``consume`` 走
        Redis 的 SET-NX，节点 A 消费后节点 B 必然也检测到重放。
        """
        from src.kernels.execution.fence import ReplayDetectedError

        c1, c2 = nodes
        b1 = DistributedExecutorLease(lease_factory=lambda n: RedisLease(n, client=c1))
        b2 = DistributedExecutorLease(lease_factory=lambda n: RedisLease(n, client=c2))
        b2._holder = "another-node"  # 同进程内 holder 相同，这里显式换个身份
        tok = b1.acquire("exec-consume", "o", 30.0, [])
        assert tok is not None

        # 节点 1 消费一次 —— 成功
        b1.consume_token("exec-consume", tok, "action-1")
        # 节点 2（共享同一份 Redis 状态）必须也检测到重放 ⇒ 证明去重是跨节点的
        with pytest.raises(ReplayDetectedError):
            b2.consume_token("exec-consume", tok, "action-1")
        # 不同 correlation_id 仍可消费（不是全局锁死）
        b2.consume_token("exec-consume", tok, "action-2")

    def test_consume_denies_when_backend_is_unreachable(self):
        """后端死时 consume 必须 deny（fail-closed），绝不静默放行重放。"""
        from src.kernels.execution.fence import ExecutorFenceDenied

        bridge = DistributedExecutorLease(lease_factory=factory_for_dead_redis())
        with pytest.raises(ExecutorFenceDenied):
            bridge.consume_token("exec-consume-dead", FencingToken(1), "c1")

    def test_redis_lease_consume_raises_when_backend_down(self):
        """``RedisLease.consume`` 在后端不可达时抛 CoordinationUnavailableError。"""
        lease = RedisLease("dead-consume", client=DeadRedis(), prefix="dead:")
        lease.connect()
        with pytest.raises(CoordinationUnavailableError):
            lease.consume("e", FencingToken(1), "c1")


@pytest.mark.skipif(
    not _redis_available(), reason="需要真实 redis-server（127.0.0.1:6379）"
)
class TestRealRedisEndToEnd:
    """只有真实 Redis 才能真正验证 Lua 脚本语义。

    跳过时请明确知道这意味着什么：compare-and-delete / INCR 令牌的**运行时**
    行为在本环境未被执行验证。
    """

    def test_cross_node_mutual_exclusion(self):
        a = get_distributed_lease("e2e-a", backend="redis")
        b = get_distributed_lease("e2e-a", backend="redis")
        ok_a, tok_a = a.acquire("node-a", 30.0)
        ok_b, _ = b.acquire("node-b", 30.0)
        assert ok_a is True and ok_b is False
        assert a.release("node-a", tok_a) is True
        ok_b2, tok_b2 = b.acquire("node-b", 30.0)
        assert ok_b2 is True and int(tok_b2) > int(tok_a)

    def test_release_with_a_stale_token_does_not_free_the_lease(self):
        a = get_distributed_lease("e2e-b", backend="redis")
        ok, tok = a.acquire("node-a", 30.0)
        assert ok is True
        assert a.release("node-a", tok.next()) is False
        assert a.validate("node-a", tok) is True


# =============================================================================
# M) EtcdLease —— 用内存假后端把 etcd 路径真跑一遍（无需 live etcd）
# =============================================================================


class _FakeEtcdClient:
    """最小 etcd KV 假后端：精确复刻 compare-and-put + TTL 语义。

    它**不**执行 etcd 的 Raft / 实际 RPC；它只复刻 EtcdLease 依赖的那几个语义：

    * ``grant_ttl`` + 绑在 key 上的租约 TTL：TTL 一过，key 在 ``get`` /
      ``get_prefix`` / ``cas_*`` 里**自动消失**（等价于 etcd 自动回收过期租约键）。
    * ``cas_version``：按 etcd 的 **key version** 做 compare-and-put（版本 0 =
      不存在），这正是 etcd 事务的语义。
    * ``cas_absent``：version == 0 才写入 ⇒ 首次成功、重放失败。
    * ``down`` 开关：模拟"运行中后端死掉"，每条命令都抛 ``ConnectionError``，
      让 EtcdLease 把它翻译成 :class:`CoordinationUnavailableError`（fail-closed）。

    因此这一组测试验证的是「传给 etcd 的语义是否正确、返回值如何被解释」，
    **不**验证真实 etcd 服务上的行为 —— 后者见 :class:`TestRealEtcdEndToEnd`
    （无 live etcd 时跳过）。
    """

    def __init__(self, down: bool = False) -> None:
        # key -> {"value": bytes, "lease_id": int|None, "version": int}
        self._store: Dict[str, Dict[str, Any]] = {}
        self._leases: Dict[int, float] = {}  # lease_id -> 过期时间戳
        self._next_lease = 1
        self._down = down

    def _check(self) -> None:
        if self._down:
            raise ConnectionError("etcd backend is down")

    def ping(self) -> bool:
        return not self._down

    def _expired(self, lease_id: Optional[int]) -> bool:
        if lease_id is None:
            return False
        exp = self._leases.get(lease_id)
        return exp is not None and exp < time.time()

    def get(self, key: str):
        self._check()
        entry = self._store.get(key)
        if entry is None:
            return None
        if self._expired(entry["lease_id"]):
            del self._store[key]
            return None
        return entry["value"]

    def version(self, key: str) -> int:
        self._check()
        entry = self._store.get(key)
        if entry is None or self._expired(entry["lease_id"]):
            if entry is not None and self._expired(entry["lease_id"]):
                del self._store[key]
            return 0
        return entry["version"]

    def grant_ttl(self, ttl_sec: float) -> int:
        self._check()
        lid = self._next_lease
        self._next_lease += 1
        self._leases[lid] = time.time() + float(ttl_sec)
        return lid

    def put(self, key: str, value: bytes, lease_id: Optional[int] = None) -> None:
        self._check()
        entry = self._store.get(key)
        ver = (entry["version"] + 1) if entry else 1
        self._store[key] = {
            "value": value,
            "lease_id": lease_id,
            "version": ver,
        }

    def delete(self, key: str) -> None:
        self._check()
        self._store.pop(key, None)

    def cas_version(
        self, key: str, expected_version: int, value: bytes,
        lease_id: Optional[int] = None,
    ) -> bool:
        self._check()
        entry = self._store.get(key)
        if entry is not None and self._expired(entry["lease_id"]):
            del self._store[key]
            entry = None
        cur_ver = entry["version"] if entry is not None else 0
        if cur_ver == expected_version:
            self.put(key, value, lease_id)
            return True
        return False

    def cas_absent(self, key: str, value: bytes, lease_id: Optional[int] = None) -> bool:
        self._check()
        entry = self._store.get(key)
        if entry is not None and not self._expired(entry["lease_id"]):
            return False
        if entry is not None and self._expired(entry["lease_id"]):
            del self._store[key]
        self.put(key, value, lease_id)
        return True

    def get_prefix(self, prefix: str) -> Dict[str, bytes]:
        self._check()
        out: Dict[str, bytes] = {}
        for k, entry in list(self._store.items()):
            if self._expired(entry["lease_id"]):
                del self._store[k]
                continue
            if k.startswith(prefix):
                out[k] = entry["value"]
        return out

    def close(self) -> None:
        self._store.clear()
        self._leases.clear()


def _etcd_available() -> bool:
    import socket

    sock = socket.socket()
    sock.settimeout(0.3)
    try:
        # etcd 默认客户端端口
        sock.connect(("127.0.0.1", 2379))
        return True
    except OSError:
        return False
    finally:
        sock.close()


try:  # pragma: no cover - 取决于本机装没装
    import etcd3 as _etcd3_mod  # noqa: F401

    _HAS_ETCD3 = True
except Exception:  # noqa: BLE001
    _HAS_ETCD3 = False


class TestEtcdLeaseWiring:
    """EtcdLease 的接线：验证「传给 etcd 的语义是否正确、返回值如何被解释」。

    与 :class:`TestRedisLeaseWiring` 同构。注意：这里走的是内存假后端，不是真实
    etcd；真实 etcd 上的行为由 :class:`TestRealEtcdEndToEnd` 负责（无 live etcd
    时跳过）。
    """

    def test_acquire_mints_a_globally_monotonic_token(self):
        # 两个租约名共享同一个 etcd 后端 ⇒ 共用同一个全局计数器（与真实 etcd 一致）。
        client = _FakeEtcdClient()
        a = EtcdLease("res-a", client=client)
        b = EtcdLease("res-b", client=client)
        ok_a, tok_a = a.acquire("owner-a", 30.0)
        ok_b, tok_b = b.acquire("owner-b", 30.0)
        assert ok_a is True and ok_b is True
        # 两个不同租约名共用同一个全局计数器（计数器键与 name 无关）
        assert int(tok_b) == int(tok_a) + 1

    def test_acquire_rejects_a_second_live_holder(self):
        # 同一 etcd 后端上的两个 EtcdLease 实例 = 两个"节点"争同一租约。
        client = _FakeEtcdClient()
        a = EtcdLease("contend", client=client)
        b = EtcdLease("contend", client=client)
        ok_a, _ = a.acquire("owner-a", 30.0)
        assert ok_a is True
        ok_b, tok_b = b.acquire("owner-b", 30.0)
        assert ok_b is False and tok_b is None

    def test_renew_with_right_owner_keeps_the_token(self):
        a = EtcdLease("renew", client=_FakeEtcdClient())
        ok, tok = a.acquire("owner-a", 30.0)
        assert ok is True
        assert a.renew("owner-a", tok, 60.0) == tok
        # 错令牌续租必须失败（返回 None）
        assert a.renew("owner-a", FencingToken(int(tok) + 99), 60.0) is None

    def test_release_requires_the_right_owner_and_token(self):
        a = EtcdLease("rel", client=_FakeEtcdClient())
        ok, tok = a.acquire("owner-a", 30.0)
        assert ok is True
        assert a.release("owner-b", tok) is False
        assert a.release("owner-a", FencingToken(int(tok) + 1)) is False
        assert a.validate("owner-a", tok) is True
        assert a.release("owner-a", tok) is True
        assert a.validate("owner-a", tok) is False

    def test_consume_persists_a_key_with_the_correlation_id(self):
        client = _FakeEtcdClient()
        lease = EtcdLease("res", client=client)
        lease.consume("exec-1", FencingToken(7), "c1")
        # key 确实写进了后端，且编码了 (name, token, correlation_id)
        key = "liuhao:lease:consumed:res:7:c1"
        assert client.get(key) == b"1"

    def test_consume_dedups_a_replay_cross_node(self):
        """同一 (token, correlation_id) 的第二次 consume 必须失败 ⇒ 重放被拒。"""
        client = _FakeEtcdClient()
        lease = EtcdLease("res", client=client)
        lease.consume("exec-1", FencingToken(7), "c1")
        with pytest.raises(ValueError):
            lease.consume("exec-1", FencingToken(7), "c1")
        # 不同的 correlation_id 仍可消费（不是全局锁死）
        lease.consume("exec-1", FencingToken(7), "c2")

    def test_consume_rejects_empty_correlation_id(self):
        lease = EtcdLease("res", client=_FakeEtcdClient())
        with pytest.raises(ValueError):
            lease.consume("exec-1", FencingToken(7), "")

    def test_consume_invalid_token_raises_replay_detected(self):
        lease = EtcdLease("res", client=_FakeEtcdClient())
        with pytest.raises(ReplayDetectedError):
            # parse_token 拒绝的东西（None）⇒ 桥接转 ReplayDetectedError
            lease.consume("exec-1", None, "c1")  # type: ignore[arg-type]

    def test_force_new_era_increments_epoch_monotonically(self):
        # 原始租约 API 的 force_new_era 只推进纪元计数；"纪元推进使旧令牌失效"
        # 由桥接层（DistributedExecutorLease）判定，原始 validate 只查 holder/token。
        client = _FakeEtcdClient()
        lease = EtcdLease("era", client=client)
        assert lease.current_epoch() == 0
        e1 = lease.force_new_era()
        e2 = lease.force_new_era()
        assert e1 == 1 and e2 == 2
        assert lease.current_epoch() == 2

    def test_bridge_force_new_era_invalidates_a_held_token(self):
        """穿桥接：纪元推进后，持有旧纪元令牌的 validate 必须变 False（stale）。"""
        client = _FakeEtcdClient()
        bridge = DistributedExecutorLease(lease_factory=lambda n: EtcdLease(n, client=client))
        tok = bridge.acquire("exec-era", "o", 30.0, ["read"])
        assert bridge.validate("exec-era", tok) is True
        bridge.force_new_era()
        # 纪元推进 == 脑裂恢复时的一次性夺权；旧持有者从此失效。
        assert bridge.validate("exec-era", tok) is False
        assert bridge.current("exec-era").state == "stale"

    def test_count_live_excludes_bookkeeping_keys(self):
        client = _FakeEtcdClient()
        lease = EtcdLease("cnt", client=client)
        lease.acquire("o1", 30.0)
        # 额外塞一个"已消费"键和一个 epoch 键，确认 count_live 不计它们
        client.put("liuhao:lease:consumed:cnt:7:c1", b"1")
        client.put("liuhao:lease:cnt:epoch", b"3")
        assert lease.count_live() == 1

    def test_expiry_allows_takeover_with_a_greater_token(self):
        client = _FakeEtcdClient()
        a = EtcdLease("ttl", client=client)
        b = EtcdLease("ttl", client=client)
        # etcd 租约 TTL 最小 1s（与真实 etcd 一致），所以这里用 1s 并等足时长。
        ok1, tok1 = a.acquire("owner-a", 1.0)
        assert ok1 is True
        # TTL 未到 ⇒ 不许提前夺权
        ok_early, _ = b.acquire("owner-b", 30.0)
        assert ok_early is False
        time.sleep(1.3)
        ok2, tok2 = b.acquire("owner-b", 30.0)
        assert ok2 is True and int(tok2) > int(tok1)
        assert a.validate("owner-a", tok1) is False


class TestEtcdLeaseFailClosed:
    """EtcdLease 的 fail-closed：后端死掉时一切操作都必须 deny，绝不静默放行。"""

    def test_acquire_raises_when_backend_is_down(self):
        lease = EtcdLease("dead", client=_FakeEtcdClient(down=True))
        with pytest.raises(CoordinationUnavailableError):
            lease.acquire("owner", 30.0)

    def test_validate_raises_when_backend_is_down(self):
        lease = EtcdLease("dead", client=_FakeEtcdClient(down=True))
        with pytest.raises(CoordinationUnavailableError):
            lease.validate("owner", FencingToken(1))

    def test_release_raises_when_backend_is_down(self):
        lease = EtcdLease("dead", client=_FakeEtcdClient(down=True))
        with pytest.raises(CoordinationUnavailableError):
            lease.release("owner", FencingToken(1))

    def test_consume_raises_when_backend_is_down(self):
        """consume 在后端不可达时抛 CoordinationUnavailableError（fail-closed），
        绝不静默放行重放。"""
        lease = EtcdLease("dead", client=_FakeEtcdClient(down=True))
        with pytest.raises(CoordinationUnavailableError):
            lease.consume("exec-1", FencingToken(1), "c1")

    def test_bridge_on_etcd_detects_replay(self):
        """穿桥接：EtcdLease 的跨节点重放检测在桥接层同样成立。"""
        from src.kernels.execution.fence import ReplayDetectedError

        client = _FakeEtcdClient()
        bridge = DistributedExecutorLease(lease_factory=lambda n: EtcdLease(n, client=client))
        tok = bridge.acquire("exec-r", "o", 30.0, [])
        assert tok is not None
        bridge.consume_token("exec-r", tok, "action-1")
        with pytest.raises(ReplayDetectedError):
            bridge.consume_token("exec-r", tok, "action-1")
        # 不同 correlation_id 仍可消费
        bridge.consume_token("exec-r", tok, "action-2")

    def test_bridge_on_etcd_fails_closed_on_backend_outage(self):
        """后端死时，穿桥接的动作也必须被拒（绝不 best-effort 放行）。"""
        from src.kernels.execution.fence import ExecutorFenceDenied

        client = _FakeEtcdClient(down=True)
        bridge = DistributedExecutorLease(lease_factory=lambda n: EtcdLease(n, client=client))
        fence = ExecutorFence(bridge)
        with pytest.raises((CoordinationUnavailableError, ExecutorFenceDenied)):
            fence.acquire_for("exec-x", "o", ["read"], ttl_sec=30.0)


@pytest.mark.skipif(
    not (_etcd_available() and _HAS_ETCD3),
    reason="需要真实 etcd（127.0.0.1:2379）+ etcd3 库；否则跳过。",
)
class TestRealEtcdEndToEnd:
    """只有真实 etcd 才能真正验证事务语义（compare-and-put / TTL / 全局令牌）。

    跳过时请明确知道这意味着什么：EtcdLease 的**运行时**行为在本环境未被执行验证
    （CI 上若 etcd 服务容器 + etcd3 就位，则会真正执行）。etcd 逻辑本身已用内存假
    后端在 :class:`TestEtcdLeaseWiring` 真跑验证，不依赖 live etcd。
    """

    def test_cross_node_mutual_exclusion(self):
        a = get_distributed_lease("e2e-a", backend="etcd")
        b = get_distributed_lease("e2e-a", backend="etcd")
        ok_a, tok_a = a.acquire("node-a", 30.0)
        ok_b, _ = b.acquire("node-b", 30.0)
        assert ok_a is True and ok_b is False
        assert a.release("node-a", tok_a) is True
        ok_b2, tok_b2 = b.acquire("node-b", 30.0)
        assert ok_b2 is True and int(tok_b2) > int(tok_a)

    def test_consume_dedups_a_replay(self):
        lease = get_distributed_lease("e2e-consume", backend="etcd")
        lease.consume("exec-1", FencingToken(1), "c1")
        with pytest.raises(ValueError):
            lease.consume("exec-1", FencingToken(1), "c1")
