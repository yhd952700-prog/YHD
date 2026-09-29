"""UBX-005 独立对抗性验证 —— 不确认实现者自测通过，而是尝试攻破不变量。

本文件由独立验证者编写，目标是以 fresh eyes 证伪三个 P0 修复以及 fence.py 的
潜在分歧。它**不修改** coordination.py / fence.py / test_coordination.py。

测试内容：
  * P0-1：后端 I/O 故障（裸 OSError / sqlite3.OperationalError）必须在围栏边界
    被翻译成 ``ExecutorFenceBackendError``（``ExecutorFenceDenied`` 子类），且
    动作绝不执行（fail-closed 端到端）。
  * P0-3(c)：两个真实 OS 进程争同一 executor_id —— 同一瞬间不可能都持有效验令牌。
  * P0-2：3 读者 + 1 写者并发，写者不被饿死（SQLite WAL）。
  * fence.py 回归：生产默认 ``SqliteExecutorLease`` 在租约过期未释放后，曾因
    my_last_token 默认 -1 而永久抛 ``FencedExecutorError``（崩溃/重启后永久锁死）。
    已修复——``FencedExecutorError`` 只在租约仍 LIVE 时触发；过期租约允许重新获取。
    同时 ``heartbeat``/``renew``/``release``/``force_new_era`` 现在自提交（与
    ``consume_token`` 一致），不再残留打开事务。
  * P1-2：fakeredis / lupa 在 venv 中是否可导入（如实报告残留）。
"""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import pytest

from src.distribution.coordination import (
    DistributedExecutorLease,
    ExecutorFenceBackendError,
    get_distributed_lease,
)
from src.kernels.execution.fence import (
    ExecutorFence,
    ExecutorFenceDenied,
    SqliteExecutorLease,
    require_executor_lease,
)

REPO_ROOT = str(Path(__file__).resolve().parents[2])
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

VENV_PY = (
    "D:/LiuHao-Data/UserRoot/.workbuddy/binaries/python/envs/default/"
    "Scripts/python.exe"
)


# =============================================================================
# 独立子进程工人（让"两个进程"是真的两个进程，不是线程）
# =============================================================================

WORKER_SRC = r'''
import json, os, sys, time

payload = json.loads(sys.argv[1])
mode = payload["mode"]
d = payload["dir"]
eid = payload["executor_id"]
repo_root = payload["repo_root"]
sys.path.insert(0, repo_root)

from src.distribution.coordination import (
    DistributedExecutorLease, get_distributed_lease, process_holder_id,
)

def factory(nm):
    return get_distributed_lease(nm, config={"directory": d})

def wait_file(p, timeout=90.0):
    t0 = time.time()
    while not os.path.exists(p):
        if time.time() - t0 > timeout:
            raise TimeoutError("wait_file timeout: " + p)
        time.sleep(0.02)

def write_result(name, obj):
    with open(os.path.join(d, name), "w") as f:
        f.write(json.dumps(obj))

try:
    if mode == "era_a":
        del_ = DistributedExecutorLease(lease_factory=factory)
        token = del_.acquire(eid, "ownerA", 30.0, ("cap",))
        write_result("result_A.json", {
            "role": "A", "token": int(token), "holder": del_._holder,
            "validates_initial": bool(del_.validate(eid, token)),
        })
        wait_file(os.path.join(d, "epoch_bumped"))
        validates_after = bool(del_.validate(eid, token))
        write_result("result_A_final.json", {
            "role": "A", "token": int(token), "validates_after": validates_after,
        })
    elif mode == "era_b":
        wait_file(os.path.join(d, "epoch_bumped"))
        del_ = DistributedExecutorLease(lease_factory=factory)
        err = None
        tok = None
        try:
            tok = del_.acquire(eid, "ownerB", 30.0, ("cap",))
        except Exception as ex:
            err = type(ex).__name__ + ": " + str(ex)
        validates = bool(del_.validate(eid, tok)) if tok is not None else False
        write_result("result_B.json", {
            "role": "B", "token": (int(tok) if tok is not None else None),
            "holder": del_._holder, "validates": validates, "error": err,
        })
    elif mode == "expire_a":
        del_ = DistributedExecutorLease(lease_factory=factory)
        token = del_.acquire(eid, "ownerA", 0.6, ("cap",))
        write_result("result_A.json", {
            "role": "A", "token": int(token),
            "validates_initial": bool(del_.validate(eid, token)),
        })
        time.sleep(2.0)
        validates_after = bool(del_.validate(eid, token))
        write_result("result_A_final.json", {
            "role": "A", "token": int(token), "validates_after": validates_after,
        })
    elif mode == "expire_b":
        time.sleep(1.2)
        del_ = DistributedExecutorLease(lease_factory=factory)
        err = None
        tok = None
        try:
            tok = del_.acquire(eid, "ownerB", 30.0, ("cap",))
        except Exception as ex:
            err = type(ex).__name__ + ": " + str(ex)
        validates = bool(del_.validate(eid, tok)) if tok is not None else False
        write_result("result_B.json", {
            "role": "B", "token": (int(tok) if tok is not None else None),
            "holder": del_._holder, "validates": validates, "error": err,
        })
    elif mode == "bench_writer":
        fl = get_distributed_lease("bench:writer", config={"directory": d})
        holder = process_holder_id()
        n = int(payload.get("n", 400))
        success = 0
        for i in range(n):
            try:
                ok, tok = fl.acquire(holder, 30.0)
                if ok:
                    success += 1
            except Exception:
                pass
        write_result("result_writer.json", {"writer_success": success, "n": n})
    elif mode == "bench_reader":
        fl = get_distributed_lease("bench:writer", config={"directory": d})
        n = int(payload.get("n", 400))
        reads = 0
        errors = 0
        for i in range(n):
            try:
                view = fl.current()
                reads += 1
                tok = view.token
                if tok is not None and not isinstance(tok, int):
                    errors += 1
            except Exception:
                errors += 1
        write_result(payload["result_file"], {"reads": reads, "errors": errors})
    else:
        raise SystemExit("unknown mode: " + mode)
except Exception as ex:
    sys.stderr.write("WORKER ERROR: " + repr(ex) + "\n")
    raise
'''


@pytest.fixture
def worker_path(tmp_path):
    p = tmp_path / "_adv_worker.py"
    p.write_text(WORKER_SRC)
    return str(p)


@pytest.fixture
def restore_fence_global():
    from src.kernels.execution import fence as fence_mod

    saved = fence_mod._DEFAULT_FENCE
    yield
    fence_mod._DEFAULT_FENCE = saved


def _run_worker(worker_path, payload, timeout=150):
    proc = subprocess.run(
        [VENV_PY, worker_path, json.dumps(payload)],
        capture_output=True,
        text=True,
        timeout=timeout,
        cwd=REPO_ROOT,
    )
    if proc.returncode != 0:
        raise AssertionError(
            f"worker {payload.get('mode')} failed (rc={proc.returncode}):\n"
            f"STDERR:\n{proc.stderr}\nSTDOUT:\n{proc.stdout}"
        )
    return proc


def _wait_file(path, timeout=90.0):
    t0 = time.time()
    while not os.path.exists(path):
        if time.time() - t0 > timeout:
            raise TimeoutError(f"timeout waiting for {path}")
        time.sleep(0.02)


def _read_result(d, name):
    _wait_file(os.path.join(d, name))
    with open(os.path.join(d, name)) as f:
        return json.loads(f.read())


# =============================================================================
# P0-1 端到端 fail-closed（最关键）
# =============================================================================

INNER_METHOD = {
    "current": "_current",
    "validate": "_validate",
    "consume_token": "_consume_token",
}


@pytest.mark.parametrize("inj", ["current", "validate", "consume_token"])
@pytest.mark.parametrize("exc_type", [OSError, sqlite3.OperationalError])
def test_p0_1_backend_fault_is_fail_closed(
    inj, exc_type, tmp_path, restore_fence_global, worker_path, monkeypatch
):
    """后端在 _enforce 调用路径上抛出裸异常 -> 必须翻译成
    ExecutorFenceBackendError -> 动作绝不执行。

    注意：public 的 ``current``/``validate``/``consume_token`` 本身已是
    ``_guarded`` 包裹层；若直接替换它们会绕过正被测试的护栏。真正贴近任务的
    "后端 I/O 故障" 注入点是这些 public 方法内部、由 ``_guarded`` 包裹的函数
    （``_current``/``_validate``/``_consume_token``）—— 这也是 _enforce 实际
    触达的后端调用。注入裸异常后，验证 ``_guarded`` 把它转换成
    ``ExecutorFenceBackendError``（fail-closed）。
    """
    directory = str(tmp_path / "store")
    os.makedirs(directory, exist_ok=True)

    def factory(nm):
        return get_distributed_lease(nm, config={"directory": directory})

    del_ = DistributedExecutorLease(lease_factory=factory)
    fence = ExecutorFence(del_)
    # 按任务要求通过 set_executor_fence 接入全局围栏。
    from src.kernels.execution import fence as fence_mod

    fence_mod.set_executor_fence(fence)

    # 先真实地获取一个上下文，保证未被注入的方法能正常返回。
    executor_id = f"p01-{inj}-{exc_type.__name__}"
    ctx = fence.acquire_for(executor_id, "owner", ("act",), ttl_sec=30.0)

    inner = INNER_METHOD[inj]

    def boom(self, *args, **kwargs):
        raise exc_type("injected")

    monkeypatch.setattr(DistributedExecutorLease, inner, boom)

    # (a) + (b): enforce 必须抛 ExecutorFenceDenied 子类，且动作标志永不被设置。
    ran = []
    with pytest.raises(ExecutorFenceDenied) as excinfo:
        fence.enforce(ctx, "do_thing")
        ran.append(1)  # 若 enforce 放行，这里会被执行 -> 暴露 bypass
    assert not ran, "动作在围栏拒绝后仍被执行（围栏被绕过）"
    assert isinstance(excinfo.value, ExecutorFenceBackendError), (
        f"后端异常未被翻译成 ExecutorFenceBackendError，而是 {type(excinfo.value)}"
    )

    # (c): require_executor_lease 同理。
    ran2 = []
    with pytest.raises(ExecutorFenceDenied):
        require_executor_lease(fence, ctx, "do_thing")
        ran2.append(1)
    assert not ran2


# =============================================================================
# P0-3(c) 两个真实进程争同一 executor_id
# =============================================================================

def test_p0_3c_era_supersede_no_dual_validation(tmp_path, worker_path):
    d = str(tmp_path / "store")
    os.makedirs(d, exist_ok=True)
    eid = "X-shared"

    # A 获取 T1 并保持租约存活；B 在 force_new_era 之后尝试获取。
    pa = _run_worker_async(worker_path, {
        "mode": "era_a", "dir": d, "executor_id": eid, "repo_root": REPO_ROOT,
    })
    a_init = _read_result(d, "result_A.json")  # A 拿到 T1 且初始可验
    assert a_init["validates_initial"] is True, "A 初始应能通过校验"

    # 测试进程触发 force_new_era（跨进程共享后端）。
    def factory(nm):
        return get_distributed_lease(nm, config={"directory": d})

    ctrl = DistributedExecutorLease(lease_factory=factory)
    new_epoch = ctrl.force_new_era()
    with open(os.path.join(d, "epoch_bumped"), "w") as f:
        f.write(str(new_epoch))

    pb = _run_worker_async(worker_path, {
        "mode": "era_b", "dir": d, "executor_id": eid, "repo_root": REPO_ROOT,
    })

    a_final = _read_result(d, "result_A_final.json")
    b_res = _read_result(d, "result_B.json")
    pa.wait()
    pb.wait()

    print("\n[P0-3c era] A:", a_final, "B:", b_res)

    # 核心安全不变量：同一瞬间 A 与 B 不可能都持有效验令牌。
    a_ok = bool(a_final["validates_after"])
    b_ok = bool(b_res["validates"])
    assert not (a_ok and b_ok), (
        f"双验证发生（绕过围栏）：A.validates={a_ok} B.validates={b_ok}"
    )
    assert a_ok is False, "force_new_era 之后 A 的旧令牌仍可用（围栏失效）"
    # B 在 A 仍持活租约时无法夺取（last=-1 默认拒绝）—— 这比任务设想更严，
    # 但以"零双验证"为安全目标它是成立的；如实记录 B 的实际结果。
    print(f"[P0-3c era] B 实际结果: token={b_res['token']} validates={b_ok} "
          f"error={b_res['error']}")


def test_p0_3c_expiry_no_dual_validation(tmp_path, worker_path):
    d = str(tmp_path / "store")
    os.makedirs(d, exist_ok=True)
    eid = "X-expire"

    pa = _run_worker_async(worker_path, {
        "mode": "expire_a", "dir": d, "executor_id": eid, "repo_root": REPO_ROOT,
    })
    pb = _run_worker_async(worker_path, {
        "mode": "expire_b", "dir": d, "executor_id": eid, "repo_root": REPO_ROOT,
    })
    a_final = _read_result(d, "result_A_final.json")
    b_res = _read_result(d, "result_B.json")
    pa.wait()
    pb.wait()

    print("\n[P0-3c expiry] A:", a_final, "B:", b_res)

    a_ok = bool(a_final["validates_after"])
    b_ok = bool(b_res["validates"])
    assert not (a_ok and b_ok), (
        f"双验证发生（绕过围栏）：A.validates={a_ok} B.validates={b_ok}"
    )
    assert a_ok is False, "A 过期后旧令牌仍可用（围栏失效）"
    assert b_ok is True and b_res["token"] is not None, (
        "B 未能在 A 过期后夺取租约"
    )


def _run_worker_async(worker_path, payload):
    return subprocess.Popen(
        [VENV_PY, worker_path, json.dumps(payload)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        cwd=REPO_ROOT,
    )


# =============================================================================
# P0-2 写者在并发读者下不被饿死
# =============================================================================

def test_p0_2_writer_progress_under_readers(tmp_path, worker_path):
    d = str(tmp_path / "store")
    os.makedirs(d, exist_ok=True)
    n = 400

    writer = _run_worker_async(worker_path, {
        "mode": "bench_writer", "dir": d, "executor_id": "w",
        "repo_root": REPO_ROOT, "n": n,
    })
    readers = []
    for i in range(3):
        readers.append(_run_worker_async(worker_path, {
            "mode": "bench_reader", "dir": d, "executor_id": "w",
            "repo_root": REPO_ROOT, "n": n, "result_file": f"result_reader_{i}.json",
        }))

    writer.wait(timeout=150)
    for r in readers:
        r.wait(timeout=150)

    wres = _read_result(d, "result_writer.json")
    rres = [_read_result(d, f"result_reader_{i}.json") for i in range(3)]

    print("\n[P0-2] writer:", wres)
    for i, rr in enumerate(rres):
        print(f"[P0-2] reader[{i}]:", rr)

    writer_success = wres["writer_success"]
    assert writer_success >= 300, (
        f"写者被饿死：仅 {writer_success}/{n} 次写入成功 "
        f"（原始 bug 为 0/400）"
    )
    for i, rr in enumerate(rres):
        assert rr["errors"] == 0, f"reader[{i}] 观察到结构不一致或抛错：{rr}"


# =============================================================================
# fence.py 分歧确认（生产默认 SqliteExecutorLease）
# =============================================================================

def test_fence_expiry_reacquire_not_permanent(tmp_path):
    """REGRESSION: an expired-but-unreleased lease must allow re-acquire.

    Before the fix, ``acquire_for`` defaulted ``my_last_token`` to -1 and raised
    ``FencedExecutorError`` for ANY still-present row (even an expired one), so a
    crashed/restarted executor whose row was never released was locked out
    forever. After the fix the fence signal only fires while the lease is LIVE.
    Also asserts heartbeat/renew/release/force_new_era now self-commit (no
    dangling open transaction).
    """
    db = str(tmp_path / "fence_div.db")
    conn = sqlite3.connect(db)
    lease = SqliteExecutorLease(conn)
    fence = ExecutorFence(lease)

    ctx = fence.acquire_for("Z", "ownerZ", ("cap",), ttl_sec=0.5)
    old_token = ctx.token
    assert old_token is not None
    time.sleep(1.0)  # expire, but do NOT release

    # Before the fix this raised FencedExecutorError forever. After the fix an
    # expired (non-live) lease is no longer authoritative, so re-acquire must
    # succeed and return a usable, live grant.
    re_ctx = fence.acquire_for("Z", "ownerZ2", ("cap",), ttl_sec=30.0)
    assert re_ctx is not None and re_ctx.token is not None
    assert lease.validate("Z", re_ctx.token) is True

    # force_new_era must self-commit (no dangling open transaction) and not raise.
    new_epoch = fence.force_new_era()
    assert new_epoch >= 1

    # release must self-commit; after release the executor is "released" and its
    # token no longer validates.
    assert lease.release("Z", re_ctx.token) is True
    assert lease.current("Z").state == "released"
    assert lease.validate("Z", re_ctx.token) is False

    print("\n[fence.py fix] expired-but-unreleased lease is re-acquirable; "
          "force_new_era/release self-commit; no permanent lockout.")
    conn.close()


# =============================================================================
# P1-2 残留：fakeredis / lupa 可导入性
# =============================================================================

def test_p1_2_optional_deps_importable():
    availability = {}
    for mod in ("fakeredis", "lupa"):
        try:
            __import__(mod)
            availability[mod] = True
        except ImportError:
            availability[mod] = False
    print("\n[P1-2] 可选依赖可导入性:", availability)
    # 仅如实报告；不安装、不编辑 pyproject。
    # 已知：5 个 Redis-Lua 测试依赖 fakeredis(+lupa)，本地若已装则可运行，
    # 但 pyproject.toml 未声明这两个依赖 -> CI 上会 skip。
