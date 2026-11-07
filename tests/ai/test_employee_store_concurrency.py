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

import os
import threading

import pytest

from src.ai.employee_store import EmployeeStore, EmployeeStoreError

HIRE_NAMES = [f"emp-c{i}" for i in range(10)]
RMW_THREADS = 5
RMW_ROUNDS = 200


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
