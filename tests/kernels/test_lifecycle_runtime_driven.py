"""生命周期协议「真的在运行时被驱动」的证明测试。

这些测试专门验证任务的核心诉求：**代码写了、测试全过、运行时空转**这类缺陷被
消除——即 ``src.kernels._registry`` 的驱动函数真正被网关 ``lifespan`` 调用，且
``pause_all`` / ``resume_all`` 有驱动方，端点能被主权主体真正驱动。

关键反证（counter-proof）：把 ``initialize_all`` 的效果摘掉（monkeypatch 成不驱动
任何实例的桩，等价于「把 lifespan 里的初始化调用临时摘掉」），内核必须仍停在
UNINITIALIZED——证明护栏不是橡皮图章；恢复后转绿。
"""
from __future__ import annotations

import os

import pytest

from src.kernels._base import KernelLifecycle, KernelStateError
from src.kernels._registry import (
    pause_all,
    resume_all,
    KERNEL_ENTRIES,
)

import src.kernels.security as _security_mod


def _skip_or_fail(reason: str) -> None:
    """本地可 skip 并写明原因；CI 上跳过等于生命周期端点没人验证，必须红。"""
    if os.environ.get("CI"):
        pytest.fail(reason)
    pytest.skip(reason)


@pytest.fixture
def reset_kernel_globals():
    """把所有单例的私有全局变量确定性地重置回 None（前后都清，避免污染别的测试）。"""
    for e in KERNEL_ENTRIES:
        if e.module is not None and e.var_name is not None:
            setattr(e.module, e.var_name, None)
    yield
    for e in KERNEL_ENTRIES:
        if e.module is not None and e.var_name is not None:
            setattr(e.module, e.var_name, None)


# --------------------------------------------------------------------------- #
# 1. pause_all / resume_all 有驱动方，且如实记录非法转换（绝不静默）
# --------------------------------------------------------------------------- #

def test_pause_all_and_resume_all_drive_existing_instance(reset_kernel_globals):
    """pause_all 把已存在实例置 PAUSED，resume_all 再置回 READY。"""
    inst = _security_mod.SecurityEngine()  # 裸造：UNINITIALIZED
    assert inst.lifecycle is KernelLifecycle.UNINITIALIZED
    _security_mod._global_security = inst

    rep = pause_all()
    assert "security" in rep["paused"]
    assert inst.lifecycle is KernelLifecycle.PAUSED

    rep2 = resume_all()
    assert "security" in rep2["resumed"]
    assert inst.lifecycle is KernelLifecycle.READY


def test_pause_all_records_illegal_transition_honestly(reset_kernel_globals):
    """已 PAUSED 再 pause 是非法转换：被如实记入 errors，绝不静默吞掉。"""
    inst = _security_mod.SecurityEngine()
    inst.initialize()  # READY
    inst.pause()       # PAUSED
    _security_mod._global_security = inst  # 让注册表能看到这个实例
    rep = pause_all()  # 对 PAUSED 再 pause -> KernelStateError
    assert any(e["name"] == "security" for e in rep["errors"])
    # 实例状态没被伪造成别的东西，仍是 PAUSED。
    assert inst.lifecycle is KernelLifecycle.PAUSED


def test_resume_illegal_transition_raises(reset_kernel_globals):
    """从 UNINITIALIZED resume 是非法转换：内核直接抛 KernelStateError。"""
    inst = _security_mod.SecurityEngine()
    with pytest.raises(KernelStateError):
        inst.resume()
    _security_mod._global_security = inst  # 让注册表能看到这个实例
    # resume_all 同样如实记录，不静默。
    rep = resume_all()
    assert any(e["name"] == "security" for e in rep["errors"])


# --------------------------------------------------------------------------- #
# 2. 生命周期被接进真实运行时（网关 lifespan）：启动 -> READY，关闭 -> STOPPED
# --------------------------------------------------------------------------- #

def test_lifespan_drives_kernel_ready_on_startup_and_stopped_on_shutdown(
    reset_kernel_globals, monkeypatch,
):
    """绿色护栏：lifespan 启动调用 initialize_all() 把实例驱动到 READY；
    关闭调用 shutdown_all() 把它驱动到 STOPPED。

    诊断已确认：app 启动后只有 identity 被物料化，security 不会被启动流程触碰，
    因此这里放一个裸造的、停在 UNINITIALIZED 的 security 实例，由 lifespan 真正驱动。
    """
    inst = _security_mod.SecurityEngine()
    assert inst.lifecycle is KernelLifecycle.UNINITIALIZED
    _security_mod._global_security = inst

    # monkeypatch (not os.environ.setdefault) so the secret is reverted at
    # teardown and never leaks into the shared process for later tests.
    monkeypatch.setenv("LIUHAO_JWT_SECRET", "test-secret-lifecycle-runtime", prepend=False)
    from fastapi.testclient import TestClient
    from src.gateway.main import get_app

    app = get_app()
    with TestClient(app):
        # lifespan startup 已调用 initialize_all() -> 实例被真实驱动到 READY。
        assert inst.lifecycle is KernelLifecycle.READY
    # TestClient 退出触发 lifespan shutdown -> shutdown_all() -> STOPPED。
    assert inst.lifecycle is KernelLifecycle.STOPPED


def test_lifespan_counterproof_removing_init_call_leaves_kernel_uninitialized(
    reset_kernel_globals,
    monkeypatch,
):
    """反证（红色护栏）：把 lifespan 里的 initialize_all() 效果摘掉，内核必须仍停在
    UNINITIALIZED；恢复后上面那条转绿。证明 lifespan 的初始化调用是真的在驱动，
    而不是「函数存在就当它在跑」的橡皮图章。

    实现：monkeypatch ``src.kernels._registry.initialize_all`` 成不驱动任何实例的桩，
    等价于把 lifespan 里的初始化调用临时摘掉（lifespan 内部 ``from ..kernels._registry
    import initialize_all`` 在每次调用时读取模块属性，因此会拿到被替换后的桩）。
    """
    inst = _security_mod.SecurityEngine()
    assert inst.lifecycle is KernelLifecycle.UNINITIALIZED
    _security_mod._global_security = inst

    noop_report = {
        "initialized": [],
        "skipped_not_present": [],
        "no_canonical_instance": [],
        "errors": [],
    }
    monkeypatch.setattr(
        "src.kernels._registry.initialize_all", lambda: noop_report
    )

    # monkeypatch (not os.environ.setdefault) so the secret is reverted at
    # teardown and never leaks into the shared process for later tests.
    monkeypatch.setenv("LIUHAO_JWT_SECRET", "test-secret-lifecycle-counterproof", prepend=False)
    from fastapi.testclient import TestClient
    from src.gateway.main import get_app

    app = get_app()
    with TestClient(app):
        # 没有 lifespan 的初始化调用，实例应仍停在 UNINITIALIZED（护栏有牙）。
        assert inst.lifecycle is KernelLifecycle.UNINITIALIZED
    # 但 shutdown_all 未被摘掉，仍真实运行 -> STOPPED。
    assert inst.lifecycle is KernelLifecycle.STOPPED


# --------------------------------------------------------------------------- #
# 3. 主权驱动端点：init / shutdown / {name}/pause / {name}/resume + 诚实失败
# --------------------------------------------------------------------------- #

@pytest.mark.gate_contract
def test_kernel_management_endpoints_drive_and_fail_honestly(
    reset_kernel_globals, monkeypatch,
):
    """端到端：受人类闸门保护的生命周期端点能真正驱动，并对非法/无实例/factory-only
    如实返回（绝不伪造成功）。
    """
    # monkeypatch (not os.environ.setdefault) so the secret is reverted at
    # teardown and never leaks into the shared process for later tests.
    monkeypatch.setenv("LIUHAO_JWT_SECRET", "test-secret-kernel-endpoints", prepend=False)
    try:
        from fastapi.testclient import TestClient
        from src.gateway.main import get_app
        from src.security.jwt_handler import create_access_token
        from src.kernels.security import get_security_engine

        # 只有导入/构造客户端失败才算环境不足；下面的断言（含 401/403/409/404）
        # 一律真红，不能被 skip 吞掉。
        client_ctx = TestClient(get_app())
    except Exception as exc:  # noqa: BLE001 - 环境/依赖缺失才跳过
        _skip_or_fail(f"无法在当前环境构建完整网关客户端做端点测试: {exc!r}")

    with client_ctx as client:
        # scope L0 + kind=human = 管理员：init/shutdown/pause/resume 都要求它。
        token, _ = create_access_token(
            "test-principal", metadata={"kind": "human", "scope": "L0"}
        )
        headers = {"X-Liuhao-Token": f"Bearer {token}"}

        # 无令牌 -> 401（人类主权闸门）。
        r = client.post("/v1/kernels/init")
        assert r.status_code == 401, (
            f"无令牌应被拒绝(401)，实际 {r.status_code}"
        )

        # init 端点：返回真实报告。
        r = client.post("/v1/kernels/init", headers=headers)
        assert r.status_code == 200
        body = r.json()
        assert "initialized" in body
        assert body["no_canonical_instance"] == ["context", "execution"]

        # factory-only 内核 pause -> 409（诚实，不伪造成 PAUSED）。
        r = client.post("/v1/kernels/context/pause", headers=headers)
        assert r.status_code == 409
        assert r.json()["detail"]["error"] == "factory_only_no_canonical_instance"

        # 未知内核 -> 404。
        r = client.post("/v1/kernels/nope/pause", headers=headers)
        assert r.status_code == 404

        # 实例尚不存在的内核 -> 409 no_instance_present。
        # （reset_kernel_globals 已把 security 全局置 None，且启动流程不触碰它。）
        assert _security_mod._global_security is None
        r = client.post("/v1/kernels/security/pause", headers=headers)
        assert r.status_code == 409
        assert r.json()["detail"]["error"] == "no_instance_present"

        # 构造一个真实 security 实例（READY），pause -> 200；再 pause -> 409 非法转换。
        _security_mod._global_security = get_security_engine()
        assert _security_mod._global_security.lifecycle is KernelLifecycle.READY
        r = client.post("/v1/kernels/security/pause", headers=headers)
        assert r.status_code == 200
        assert r.json()["lifecycle"] == "paused"
        r = client.post("/v1/kernels/security/pause", headers=headers)
        assert r.status_code == 409
        assert r.json()["detail"]["error"] == "illegal_state_transition"
        # resume -> 200，回到 READY。
        r = client.post("/v1/kernels/security/resume", headers=headers)
        assert r.status_code == 200
        assert r.json()["lifecycle"] == "ready"


def test_kernel_pause_and_resume_require_admin(reset_kernel_globals, monkeypatch):
    """成员（L1）调 pause/resume 必须 403 + 中文原因；匿名 401；管理员在别处验证 200。"""
    monkeypatch.setenv("LIUHAO_JWT_SECRET", "test-secret-kernel-admin-only", prepend=False)
    from fastapi.testclient import TestClient
    from src.gateway.main import get_app
    from src.security.jwt_handler import create_access_token

    app = get_app()
    with TestClient(app) as client:
        member, _ = create_access_token(
            "test-member", metadata={"kind": "human", "scope": "L1"}
        )
        headers = {"X-Liuhao-Token": f"Bearer {member}"}

        for action in ("pause", "resume"):
            response = client.post(f"/v1/kernels/security/{action}", headers=headers)
            assert response.status_code == 403, (
                f"成员调用 {action} 应 403，实际 {response.status_code}: {response.text}"
            )
            detail = response.json()["detail"]
            assert isinstance(detail, str), detail
            assert "管理员权限" in detail
            for leak in ("Traceback", "src/gateway", "src\\gateway", ".py", "C:\\", "/Users/"):
                assert leak not in response.text, f"403 响应泄漏了内部信息：{leak!r}"

        # 只读 status/list 不受影响：成员仍可读。
        listing = client.get("/v1/kernels", headers=headers)
        assert listing.status_code == 200, listing.text

        # 匿名仍是 401，不是 403。
        assert client.post("/v1/kernels/security/pause").status_code == 401
