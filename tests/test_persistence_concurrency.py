"""并发回归：凭据库 / API Key 注册表 / 密钥库主密钥 / 任务图状态。

这些文件过去都是「读全量 → 整份覆盖写」且无锁、非原子：

* 每个请求 ``new`` 一个实例，实例锁跨实例无效；
* 固定 ``<path>.tmp`` 在并发时互相覆盖；
* ``open(..., "w")`` / ``O_TRUNC`` 在崩溃时留下半截 JSON。

本文件用「多实例 + 多线程 + 子进程强杀」把四个高风险点逐一钉住：

1. ``SecretStore.set_credential``：8 个账号并发注册，最终 8 条齐全；
2. ``APIKeyManager.create_key``：8 个 manager 并发创建，最终 8 把 key 齐全；
3. ``LocalEncryptedSecretStore`` 首次初始化：多实例并发，密钥不丢、可解密；
4. ``GoalTaskGraph.save_state``：6 个实例并发保存不同任务，任务不互相覆盖；
5. 子进程写入中被强杀：目标文件仍是完整 JSON，不会留半截。

所有路径都落在 ``tmp_path``，不碰仓库/压测的真实数据目录。
"""

import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from src.ai.goal_task_graph import GoalDefinition, GoalTaskGraph, PlannerStateError, TaskNode
from src.gateway.auth import CredentialStoreError, SecretStore
from src.security.api_keys import APIKeyManager, APIKeyStoreError
from src.security.secret_store import CRYPTO_AVAILABLE, LocalEncryptedSecretStore

REPO_ROOT = Path(__file__).resolve().parents[1]


def _run_threads(targets):
    """并发跑一组 callable，收集异常（不在子线程里 pytest.fail）。"""
    barrier = threading.Barrier(len(targets))
    errors = []
    errors_lock = threading.Lock()

    def wrap(fn, label):
        def runner():
            try:
                barrier.wait(timeout=30)
                fn()
            except Exception as exc:  # noqa: BLE001 - 统一断言
                with errors_lock:
                    errors.append(f"{label}: {type(exc).__name__}: {exc}")
        return runner

    threads = [
        threading.Thread(target=wrap(fn, label)) for fn, label in targets
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=120)
    return errors


# ---------------------------------------------------------------------------
# 1. 凭据库（多账号注册）
# ---------------------------------------------------------------------------


def test_credentials_concurrent_registration_keeps_all_accounts(tmp_path):
    path = os.path.join(str(tmp_path), "auth_secrets.json")
    principals = [f"user-{i}" for i in range(8)]

    def register(name):
        def run():
            assert SecretStore(path).set_credential(name, f"pw-{name}", iterations=1000)
        return run

    errors = _run_threads([(register(name), name) for name in principals])
    assert errors == [], f"并发注册出现异常：{errors[:5]}"

    with open(path, "r", encoding="utf-8") as handle:
        document = json.load(handle)
    assert set(document["credentials"]) == set(principals)
    assert SecretStore(path).credential_for("user-3") is not None


def test_credentials_corrupt_store_refuses_write(tmp_path):
    path = os.path.join(str(tmp_path), "auth_secrets.json")
    with open(path, "w", encoding="utf-8") as handle:
        handle.write("{not valid json")

    with pytest.raises(CredentialStoreError):
        SecretStore(path).set_credential("ghost", "pw", iterations=1000)

    with open(path, "r", encoding="utf-8") as handle:
        assert handle.read() == "{not valid json"


def test_credentials_login_read_path_still_fails_closed(tmp_path):
    """损坏文件时读取仍返回空（fail-closed），不能把异常抛到登录路径。"""
    path = os.path.join(str(tmp_path), "auth_secrets.json")
    with open(path, "w", encoding="utf-8") as handle:
        handle.write("{not valid json")
    assert SecretStore(path).load() == {}


# ---------------------------------------------------------------------------
# 2. API Key 注册表
# ---------------------------------------------------------------------------


def test_api_keys_concurrent_create_keeps_all_keys(tmp_path):
    path = os.path.join(str(tmp_path), "api_keys.json")
    names = [f"key-{i}" for i in range(8)]

    def create(name):
        def run():
            manager = APIKeyManager(storage_path=path)
            key, raw = manager.create_key(name=name)
            assert key.id and raw
        return run

    errors = _run_threads([(create(name), name) for name in names])
    assert errors == [], f"并发创建出现异常：{errors[:5]}"

    with open(path, "r", encoding="utf-8") as handle:
        payload = json.load(handle)
    assert len(payload["keys"]) == 8
    assert {item["name"] for item in payload["keys"]} == set(names)

    # 新建 manager 能从磁盘加载全部 key，且仍能校验有效。
    reloaded = APIKeyManager(storage_path=path)
    assert len(reloaded._keys) == 8


def test_api_keys_corrupt_registry_refuses_write(tmp_path):
    path = os.path.join(str(tmp_path), "api_keys.json")
    with open(path, "w", encoding="utf-8") as handle:
        handle.write("{not valid json")

    with pytest.raises(APIKeyStoreError):
        APIKeyManager(storage_path=path).create_key(name="ghost")

    with open(path, "r", encoding="utf-8") as handle:
        assert handle.read() == "{not valid json"


# ---------------------------------------------------------------------------
# 3. 密钥库主密钥（首次初始化）
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not CRYPTO_AVAILABLE, reason="需要 cryptography")
def test_secret_store_concurrent_first_init_keeps_one_usable_master(tmp_path):
    storage_dir = str(tmp_path / "secrets")
    passphrase = "test-passphrase"

    def init_and_set(index):
        def run():
            store = LocalEncryptedSecretStore(storage_dir, passphrase=passphrase)
            store.set(f"slot-{index}", f"value-{index}")
        return run

    errors = _run_threads(
        [(init_and_set(i), f"store-{i}") for i in range(6)]
    )
    assert errors == [], f"并发初始化出现异常：{errors[:5]}"

    master_path = os.path.join(storage_dir, "master.kek")
    with open(master_path, "r", encoding="utf-8") as handle:
        document = json.load(handle)
    assert document.get("wrapped_dek")

    # 新建实例必须能用同一个主密钥解开全部并发写入的密文。
    reader = LocalEncryptedSecretStore(storage_dir, passphrase=passphrase)
    for i in range(6):
        assert reader.get(f"slot-{i}") == f"value-{i}"


# ---------------------------------------------------------------------------
# 4. 任务图状态（目标态合并）
# ---------------------------------------------------------------------------


def _graph(goal_id="goal_shared", tasks=()):
    graph = GoalTaskGraph(enable_observability=False)
    graph.set_goal(GoalDefinition(id=goal_id, description="共享目标"))
    for task in tasks:
        graph.tasks[task.id] = task
    return graph


def test_goal_graph_concurrent_save_merges_tasks(tmp_path):
    path = os.path.join(str(tmp_path), "planner_state.json")
    task_ids = [f"task-{i}" for i in range(6)]

    def save(task_id):
        def run():
            graph = _graph(tasks=[TaskNode(id=task_id, description=task_id)])
            graph.save_state(path)
        return run

    errors = _run_threads([(save(tid), tid) for tid in task_ids])
    assert errors == [], f"并发保存出现异常：{errors[:5]}"

    with open(path, "r", encoding="utf-8") as handle:
        document = json.load(handle)
    assert {item["id"] for item in document["tasks"]} == set(task_ids)
    assert document["goals"]["id"] == "goal_shared"


def test_goal_graph_memory_state_wins_over_stale_disk(tmp_path):
    """同一 task id：内存中的完成态必须覆盖磁盘上的旧 PENDING 快照。"""
    path = os.path.join(str(tmp_path), "planner_state.json")
    stale = TaskNode(id="task-1", description="first")
    _graph(tasks=[stale]).save_state(path)

    completed = TaskNode(id="task-1", description="first")
    completed.status = completed.status.__class__.COMPLETED
    _graph(tasks=[completed]).save_state(path)

    with open(path, "r", encoding="utf-8") as handle:
        document = json.load(handle)
    assert len(document["tasks"]) == 1
    assert document["tasks"][0]["status"] == "completed"


def test_goal_graph_corrupt_state_refuses_write(tmp_path):
    path = os.path.join(str(tmp_path), "planner_state.json")
    with open(path, "w", encoding="utf-8") as handle:
        handle.write("{not valid json")

    with pytest.raises(PlannerStateError):
        _graph(tasks=[TaskNode(id="task-1", description="x")]).save_state(path)

    with open(path, "r", encoding="utf-8") as handle:
        assert handle.read() == "{not valid json"


# ---------------------------------------------------------------------------
# 5. 崩溃：子进程写入中被强杀，目标文件不能是半截
# ---------------------------------------------------------------------------

_CRASH_SCRIPT = r"""
import json, os, sys
from src.ai.goal_task_graph import GoalDefinition, GoalTaskGraph, TaskNode

path = sys.argv[1]
graph = GoalTaskGraph(enable_observability=False)
graph.set_goal(GoalDefinition(id="goal_crash", description="crash test"))
for i in range(40):
    graph.tasks[f"task-{i}"] = TaskNode(
        id=f"task-{i}", description="x" * 20000
    )
while True:
    graph.save_state(path)
"""


def test_sigkill_during_save_leaves_valid_json(tmp_path):
    path = os.path.join(str(tmp_path), "crash_state.json")
    _graph(goal_id="goal_crash").save_state(path)

    env = dict(os.environ)
    env["PYTHONPATH"] = str(REPO_ROOT) + os.pathsep + env.get("PYTHONPATH", "")
    for _ in range(3):
        proc = subprocess.Popen(
            [sys.executable, "-c", _CRASH_SCRIPT, path],
            cwd=str(REPO_ROOT),
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            time.sleep(0.4)
        finally:
            proc.kill()
            proc.wait(timeout=30)

        with open(path, "r", encoding="utf-8") as handle:
            document = json.load(handle)  # 半截 JSON 会在这里直接失败
        assert isinstance(document, dict)
        assert document.get("goals", {}).get("id") == "goal_crash"
