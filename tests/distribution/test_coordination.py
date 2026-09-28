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
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

from src.distribution.coordination import (
    BACKEND_ENV,
    CoordinationUnavailableError,
    DistributedExecutorLease,
    DistributedLease,
    FileLockLease,
    FencingToken,
    RedisLease,
    get_distributed_lease,
    parse_token,
)
from src.distribution.election import LeaderElection
from src.distribution.lock import DistributedLock
from src.kernels.execution.fence import (
    ExecutorFence,
    ExecutorLease,
    StaleExecutorError,
)

REPO_ROOT = str(Path(__file__).resolve().parents[2])

#: 一个几乎必定拒绝连接的地址（端口 6399），用来制造"redis 不可达"。
UNREACHABLE_URL = "redis://127.0.0.1:6399/0"
FAST_FAIL = {"url": UNREACHABLE_URL, "connect_timeout": 0.3}


# =============================================================================
# 子进程工人：让"两个进程"是真的两个进程
# =============================================================================

WORKER_SRC = textwrap.dedent(
    '''
    import json, os, sys, time

    mode, directory, name, out, repo = sys.argv[1:6]
    sys.path.insert(0, repo)

    from src.distribution.coordination import FileLockLease

    lease = FileLockLease(name, directory=directory)
    owner = "proc-%d" % os.getpid()
    ttl = 3.0 if mode == "crash" else 30.0
    ok, token = lease.acquire(owner, ttl)
    with open(out, "w") as fh:
        json.dump(
            {"pid": os.getpid(), "ok": bool(ok),
             "token": int(token) if token is not None else None},
            fh,
        )
        fh.flush()
        os.fsync(fh.fileno())

    if mode == "crash":
        # 等价 kill -9：不跑 finally、不 flush stdio、不释放任何东西。
        os._exit(9)

    time.sleep(float(sys.argv[6]) if len(sys.argv) > 6 else 2.0)
    if ok and token is not None:
        lease.release(owner, token)
    '''
)


def _worker_path(tmp_path: Path) -> str:
    path = tmp_path / "_lease_worker.py"
    path.write_text(WORKER_SRC, encoding="utf-8")
    return str(path)


def _run_workers(tmp_path: Path, mode: str, count: int, hold: float = 2.0):
    """启动 ``count`` 个独立进程争同一个租约，返回它们各自的结果 dict。"""
    worker = _worker_path(tmp_path)
    lease_dir = tmp_path / "leases"
    lease_dir.mkdir(exist_ok=True)

    procs = []
    for i in range(count):
        out = lease_dir / f"result-{mode}-{i}.json"
        procs.append(
            (
                out,
                subprocess.Popen(
                    [
                        sys.executable,
                        worker,
                        mode,
                        str(lease_dir),
                        "contended",
                        str(out),
                        REPO_ROOT,
                        str(hold),
                    ],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                ),
            )
        )

    results = []
    for out, proc in procs:
        proc.wait(timeout=60)
        results.append(json.loads(out.read_text(encoding="utf-8")))
    return results


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
            get_distributed_lease("x", backend="etcd")
        assert "etcd" in str(exc.value)

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
