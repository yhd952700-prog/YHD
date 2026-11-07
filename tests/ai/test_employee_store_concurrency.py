"""并发回归：员工库不能再静默丢员工 / 丢更新。

覆盖三个真实根因：

1. 每个 HTTP 请求都 ``new EmployeeStore()``，实例级 ``threading.RLock`` 跨实例
   无效 —— 用多个 store 实例并发写同一文件验证；
2. ``save`` 是「读全量 → 改自己 → os.replace 覆盖」，后写者会整份覆盖先写者，
   Windows 上目标文件被打开时 rename 还会直接失败 —— 用 10 线程并发雇佣 +
   5×200 次读-改-写验证；
3. 失败必须显式抛出，不能"接口返回成功但数据没了" —— 用损坏文件验证。

路径全部落在 ``tmp_path``，不碰仓库里的真实员工库。
"""

import json
import os
import subprocess
import sys
import threading

import pytest

from src.ai.employee_store import EmployeeStore, EmployeeStoreError

HIRE_NAMES = [f"emp-c{i}" for i in range(10)]
RMW_THREADS = 5
RMW_ROUNDS = 200
MIXED_WORKERS = 20
MIXED_ROUNDS = 10


def _store(tmp_path, name="liuhao_employees.json") -> EmployeeStore:
    return EmployeeStore(path=os.path.join(str(tmp_path), name))


def test_concurrent_hire_persists_all_ten(tmp_path):
    """10 线程各自 new store 并发雇佣 10 个不同员工，必须全部落盘。"""
    _store(tmp_path).create_employee(name="seed", agent_count=1)

    barrier = threading.Barrier(len(HIRE_NAMES))
    errors = []
    errors_lock = threading.Lock()

    def hire(name: str) -> None:
        try:
            barrier.wait(timeout=30)
            store = _store(tmp_path)
            store.create_employee(name=name, agent_count=2)
        except Exception as exc:  # noqa: BLE001 - 收集后统一断言
            with errors_lock:
                errors.append(f"{name}: {type(exc).__name__}: {exc}")

    threads = [threading.Thread(target=hire, args=(n,)) for n in HIRE_NAMES]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)

    assert errors == [], f"并发雇佣出现异常：{errors[:5]}"
    names = _store(tmp_path).list_employees()
    assert set(HIRE_NAMES) <= set(names)
    assert "seed" in names
    for name in HIRE_NAMES:
        assert _store(tmp_path).load(name) is not None


def test_concurrent_read_modify_write_counts_are_exact(tmp_path):
    """5 线程 × 200 次读-改-写：agent 计数必须等于 1000（一次都不丢）。"""
    _store(tmp_path).create_employee(name="liuhao-default", agent_count=3)
    path = os.path.join(str(tmp_path), "liuhao_employees.json")

    barrier = threading.Barrier(RMW_THREADS)
    errors = []
    errors_lock = threading.Lock()

    def worker(tid: int) -> None:
        try:
            barrier.wait(timeout=30)
            for _ in range(RMW_ROUNDS):
                store = EmployeeStore(path=path)  # 每个"请求"一个新实例
                emp = store.create_employee(name="liuhao-default")
                first = next(iter(emp.agents.values()))
                first.completed_tasks += 1
                store.save(emp)
        except Exception as exc:  # noqa: BLE001 - 收集后统一断言
            with errors_lock:
                errors.append(f"tid{tid}: {type(exc).__name__}: {exc}")

    threads = [
        threading.Thread(target=worker, args=(i,)) for i in range(RMW_THREADS)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=180)

    assert errors == [], f"并发写入出现异常：{errors[:5]}"
    final = EmployeeStore(path=path).load("liuhao-default")
    assert final is not None
    actual = next(iter(final.agents.values())).completed_tasks
    assert actual == RMW_THREADS * RMW_ROUNDS


def test_multiple_store_instances_alternate_writes(tmp_path):
    """实例级锁失效的根因：多个 store 实例交替写同一文件也不能互相覆盖。"""
    path = os.path.join(str(tmp_path), "liuhao_employees.json")
    stores = [EmployeeStore(path=path) for _ in range(6)]
    stores[0].create_employee(name="shared", agent_count=2)

    barrier = threading.Barrier(len(stores))
    errors = []
    errors_lock = threading.Lock()

    def write_distinct(index: int) -> None:
        try:
            barrier.wait(timeout=30)
            stores[index].create_employee(name=f"inst-{index}", agent_count=1)
        except Exception as exc:  # noqa: BLE001
            with errors_lock:
                errors.append(f"inst-{index}: {type(exc).__name__}: {exc}")

    threads = [
        threading.Thread(target=write_distinct, args=(i,)) for i in range(len(stores))
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)

    assert errors == [], f"多实例并发写入出现异常：{errors[:5]}"
    names = EmployeeStore(path=path).list_employees()
    assert {"shared"} | {f"inst-{i}" for i in range(len(stores))} <= set(names)

    # 再让这 6 个实例交替对同一员工做读-改-写：计数同样一次不丢。
    rounds = 20
    for _ in range(rounds):
        for store in stores:
            emp = store.create_employee(name="shared")
            next(iter(emp.agents.values())).completed_tasks += 1
            store.save(emp)
    final = EmployeeStore(path=path).load("shared")
    assert final is not None
    assert next(iter(final.agents.values())).completed_tasks == rounds * len(stores)


def test_corrupt_store_refuses_write_instead_of_wiping(tmp_path):
    """读回损坏时写入必须显式失败，绝不能用空快照覆盖唯一副本。"""
    path = os.path.join(str(tmp_path), "liuhao_employees.json")
    with open(path, "w", encoding="utf-8") as handle:
        handle.write("{not valid json")

    store = EmployeeStore(path=path)
    with pytest.raises(EmployeeStoreError):
        store.create_employee(name="ghost", agent_count=1)

    # 损坏文件保持原样，没有被"成功写入"的空快照替换。
    with open(path, "r", encoding="utf-8") as handle:
        assert handle.read() == "{not valid json"


def test_twenty_workers_mixed_ops_ten_rounds_no_loss(tmp_path):
    """20 并发 × 混合「创建/改名/指派/删除」× 10 轮，0 丢写、0 报错。

    每个操作都 new 一个 store 实例（与"每个请求 new 一个 store"的真实路径一致），
    每轮结束后用全新实例从磁盘读回，逐条核对。
    """
    path = os.path.join(str(tmp_path), "liuhao_employees.json")
    _store(tmp_path).create_employee(name="seed", agent_count=1)
    errors = []
    errors_lock = threading.Lock()

    def worker(round_no: int, index: int) -> None:
        name = f"r{round_no}-w{index}"
        renamed = f"{name}-renamed"
        try:
            barrier.wait(timeout=60)
            EmployeeStore(path=path).create_employee(name=name, agent_count=1)
            # 改名（读写改写的另一种形态）。
            renamed_employee = EmployeeStore(path=path).rename(name, renamed)
            assert renamed_employee is not None
            # 指派：给同一个员工加一个真实 task 并落盘。
            store = EmployeeStore(path=path)
            emp = store.load(renamed)
            assert emp is not None
            task = emp.add_task(f"第 {round_no}/{index} 轮的任务")
            store.save(emp)
            assert task is not None
            # 一半员工删除，一半保留，验证删除不会误删别人。
            if index % 2 == 0:
                EmployeeStore(path=path).remove(renamed)
        except Exception as exc:  # noqa: BLE001 - 收集后统一断言
            with errors_lock:
                errors.append(f"r{round_no}-w{index}: {type(exc).__name__}: {exc}")

    for round_no in range(MIXED_ROUNDS):
        barrier = threading.Barrier(MIXED_WORKERS)
        threads = [
            threading.Thread(target=worker, args=(round_no, index))
            for index in range(MIXED_WORKERS)
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=120)

        assert errors == [], f"round {round_no} 并发写入出错：{errors[:5]}"
        names = set(EmployeeStore(path=path).list_employees())
        expected_kept = {f"r{round_no}-w{i}-renamed" for i in range(1, MIXED_WORKERS, 2)}
        expected_deleted = {f"r{round_no}-w{i}-renamed" for i in range(0, MIXED_WORKERS, 2)}
        assert expected_kept <= names, f"round {round_no} 丢了员工：{expected_kept - names}"
        assert not (expected_deleted & names), f"round {round_no} 删除被覆盖回来了"
        assert "seed" in names, f"round {round_no} 把无关员工覆盖掉了"
        for kept in expected_kept:
            loaded = EmployeeStore(path=path).load(kept)
            assert loaded is not None
            assert len(loaded.tasks) == 1, f"{kept} 的指派任务丢了"


def test_killed_mid_write_leaves_complete_json(tmp_path):
    """写一半进程被杀：磁盘上只能是旧的完整版或新的完整版，不能是半个 JSON。"""
    path = os.path.join(str(tmp_path), "liuhao_employees.json")
    repo_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    script = os.path.join(str(tmp_path), "writer.py")
    with open(script, "w", encoding="utf-8") as handle:
        handle.write(
            "import os, sys\n"
            f"sys.path.insert(0, {repo_root!r})\n"
            "from src.ai.employee_store import EmployeeStore\n"
            "store = EmployeeStore(path=sys.argv[1])\n"
            "i = 0\n"
            "while True:\n"
            "    i += 1\n"
            "    emp = store.create_employee(name='crash', agent_count=1)\n"
            "    emp.role_description = 'v%d-' % i + ('x' * 20000)\n"
            "    emp.add_task('task-%d' % i)\n"
            "    store.save(emp)\n"
        )

    seed = _store(tmp_path).create_employee(name="crash", agent_count=1)
    seed.role_description = "seed-" + ("x" * 20000)
    _store(tmp_path).save(seed)

    proc = subprocess.Popen([sys.executable, script, path])
    try:
        # 等它至少写完几次再中止（写到一半的概率最高）。
        proc.wait(timeout=1.5)
    except subprocess.TimeoutExpired:
        pass
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:  # pragma: no cover
            proc.kill()

    with open(path, "r", encoding="utf-8") as handle:
        raw = handle.read()
    document = json.loads(raw)  # 半个 JSON 会在这里直接失败
    assert isinstance(document.get("employees"), dict)
    crash = document["employees"].get("crash")
    assert crash is not None
    role = crash.get("role_description") or ""
    assert role.startswith("seed-") or role.startswith("v"), role[:20]
    assert role.endswith("x" * 20000), "role_description 不是完整的一份（疑似写了一半）"

