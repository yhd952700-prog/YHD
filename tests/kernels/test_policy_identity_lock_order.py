"""锁序回归：identity -> policy 与 policy -> identity 不能互等。

背景（实测，不是推测）
----------------------
``IdentityManager.create_member_account`` 在持有 ``self._lock`` 时调用
``create_member_identity`` -> ``create_identity``（``@kernel_action``），
装饰器会先做策略判定 -> ``PolicyEngine.evaluate_simple`` -> ``evaluate``，
即 ``identity -> policy``。

而 ``PolicyEngine._evaluate_decision`` 原先在持有 ``policy._lock`` 时调用
``_compute_verified`` -> ``IdentityManager.get_identity``，即
``policy -> identity``。

两把锁都是 ``RLock``，但可重入只对**同一线程**成立；两条线程同时走这两条
路径就永久互等。改前用两线程探针复现：创建 1 个成员 + 评估 1 次策略后即
冻结（8 秒看门狗打印的栈里，一条线程停在 ``policy/__init__.py`` 的
``with self._lock``，另一条停在 ``identity/__init__.py`` 的 ``get_identity``）。

修法是把纯输入准备（``_compute_verified``）移到取 policy 锁之前，使
policy 侧不再"持锁问 identity"。本测试就是那条回归线：冻结时断言失败，
而不是让整个 pytest 挂住。
"""
from __future__ import annotations

import threading

import pytest

_JOIN_TIMEOUT = 20.0


@pytest.fixture
def isolated_kernels(tmp_path, monkeypatch):
    monkeypatch.setenv("LIUHAO_WORKSPACE_ROOT", str(tmp_path))
    monkeypatch.setenv("LIUHAO_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("AUDIT_DB_PATH", str(tmp_path / "audit.db"))
    monkeypatch.setenv("LIUHAO_HUMAN_IDENTITIES_BACKEND", "file")
    monkeypatch.setenv("LIUHAO_HUMAN_IDENTITIES_FILE", str(tmp_path / "humans.json"))
    monkeypatch.setenv("LIUHAO_HUMAN_IDENTITIES_INTEGRITY_KEY", "lock-order-test-key")
    monkeypatch.setenv("LIUHAO_ENV", "development")

    import src.kernels.audit as audit_module
    import src.kernels.identity as identity_module
    import src.kernels.policy as policy_module

    identity_module._global_manager = None
    policy_module._global_engine = None
    audit_module._audit_store = None
    try:
        yield
    finally:
        identity_module._global_manager = None
        policy_module._global_engine = None
        audit_module._audit_store = None


def test_member_creation_and_policy_evaluation_do_not_deadlock(isolated_kernels):
    from src.kernels.identity import get_identity_manager
    from src.kernels.policy import evaluate_policy_simple

    manager = get_identity_manager()
    manager.register_root_account("root")

    barrier = threading.Barrier(2)
    progress = {"create": 0, "eval": 0}
    errors = []

    def create_members() -> None:
        barrier.wait(_JOIN_TIMEOUT)
        try:
            for index in range(3):
                manager.create_member_account(actor="root", principal=f"member{index}")
                progress["create"] += 1
        except BaseException as exc:  # noqa: BLE001 - 线程内异常要带回主线程
            errors.append(exc)

    def evaluate_policy() -> None:
        barrier.wait(_JOIN_TIMEOUT)
        try:
            for _ in range(30):
                evaluate_policy_simple(
                    actor={"type": "service", "principal": "liuhao-internal-service"},
                    action={"name": "policy.evaluate", "risk_level": "LOW"},
                )
                progress["eval"] += 1
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [
        # daemon：改前冻结时让 pytest 能报失败并退出，而不是整个进程挂住。
        threading.Thread(target=create_members, name="create-members", daemon=True),
        threading.Thread(target=evaluate_policy, name="evaluate-policy", daemon=True),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(_JOIN_TIMEOUT)

    alive = [thread.name for thread in threads if thread.is_alive()]
    assert not alive, (
        f"锁序反转：线程 {alive} 在 {_JOIN_TIMEOUT}s 后仍未结束；"
        f"进度 {progress}。检查 policy._evaluate_decision 是否又持锁调用 "
        "IdentityManager（policy -> identity），而 create_member_account "
        "持 identity 锁调用 @kernel_action（identity -> policy）。"
    )
    assert not errors, f"线程内异常：{errors!r}"
    assert progress == {"create": 3, "eval": 30}, progress
