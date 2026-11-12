"""技能注入的接口级证明：``POST /v1/goals/assign`` 走完整链路。

要证明的三件事：

1. 绑定了技能、且技能已启用 → 该员工派活时，真正发给模型的请求体里出现技能正文；
2. 仓库里存在同一技能、但员工没绑定 → 请求体里不能有它；
3. 绑定还在、但技能被禁用（``enabled=false``）→ 同样不能注入，避免"关了还在生效"。

执行层用记录 prompt 的 stub，``default_generate`` 仍走真实注入逻辑；不调用
真实模型。
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from src.ai.llm_support import default_generate
import src.ai.providers as providers
from src.gateway.ai_management import AIStateManager
from src.gateway.main import get_app

SKILL_BODY = """# 周报整理

## 步骤
1. 收集本周提交记录与关闭的工单。
2. 按项目归类，标注完成 / 进行中 / 受阻。
3. 风险单列，写清影响与下周动作。
"""

BODY_MARK = "收集本周提交记录与关闭的工单"
TASK = "请根据本周的提交和工单，写一份周报。"
ROOT = "skill-inject-boss"


def _headers() -> dict:
    from src.security import get_jwt_handler

    token, _payload = get_jwt_handler().create_token(
        subject=ROOT, metadata={"kind": "human", "scope": "L0"}
    )
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.setenv("LIUHAO_WORKSPACE_ROOT", str(tmp_path))
    monkeypatch.setenv("LIUHAO_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.delenv("LIUHAO_DEFAULT_ACCOUNT_ID", raising=False)
    AIStateManager._instance = None
    # 目标/员工接口有分钟级限流；这里验的是注入语义，不是限流，重置限流桶。
    import src.gateway.rate_limiter as rate_limiter_module

    rate_limiter_module._default_limiter = None
    from src.kernels.identity import get_identity_manager

    identity = get_identity_manager()
    try:
        identity.register_root_account(ROOT)
    except Exception:  # noqa: BLE001 - 同一进程重复注册不致命
        pass
    yield
    AIStateManager._instance = None


@pytest.fixture
def client():
    with TestClient(get_app()) as test_client:
        yield test_client


class _CaptureProvider:
    """记录 ``generate_with_retry`` 收到的 prompt（= 真正发给模型的请求体）。"""

    name = "capture"

    def __init__(self) -> None:
        self.prompts: list = []

    def generate_with_retry(self, prompt, **kwargs):
        self.prompts.append(prompt)
        return "（模型回复占位）"

    def generate(self, prompt, **kwargs):
        return self.generate_with_retry(prompt)


class _CaptureRuntime:
    """执行层 stub：在 run_goal 里发起一次真实的默认模型调用。"""

    def run_goal(self, **kwargs):
        default_generate(TASK)
        goal_id = kwargs.get("goal_id") or "stub"
        return SimpleNamespace(
            goal_id=goal_id,
            state=SimpleNamespace(value="completed"),
            correlation_id=f"stub-{goal_id}",
            error=None,
            replan_count=0,
            replan_suggested=False,
            memory_keys=[],
            persist_errors=[],
            trace=None,
            context=None,
            evaluation=None,
        )


def _create_employee(client: TestClient, headers: dict, name: str) -> None:
    response = client.post(
        "/v1/employees",
        json={"name": name, "agent_count": 1, "agent_types": ["general"]},
        headers=headers,
    )
    assert response.status_code == 201, response.text


def _assign(client: TestClient, headers: dict, employee: str) -> None:
    response = client.post(
        "/v1/goals/assign",
        json={
            "employee_name": employee,
            "instruction": TASK,
            "background": False,
        },
        headers=headers,
    )
    assert response.status_code == 201, response.text


def test_only_bound_and_enabled_skills_reach_the_request_body(client, monkeypatch):
    headers = _headers()
    _create_employee(client, headers, "周报助理")
    _create_employee(client, headers, "其他员工")

    from src.gateway.skill_bindings import SkillBindingStore
    from src.gateway.skill_store import SkillStore

    record = SkillStore().create(name="周报整理", content=SKILL_BODY, enabled=True)
    SkillBindingStore().bind(skill_id=record["id"], employee="周报助理")

    capture = _CaptureProvider()
    monkeypatch.setattr(providers, "resolve_provider", lambda: capture)
    runtime = _CaptureRuntime()
    AIStateManager()._runtime = runtime

    # 1) 绑定 + 启用 → 请求体里有技能正文与资料分隔标记
    _assign(client, headers, "周报助理")
    bound_body = capture.prompts[-1]
    assert BODY_MARK in bound_body
    assert "<<<技能资料 开始" in bound_body
    assert "非指令" in bound_body

    # 2) 同库同技能，但员工未绑定 → 请求体里没有正文
    _assign(client, headers, "其他员工")
    unbound_body = capture.prompts[-1]
    assert BODY_MARK not in unbound_body
    assert "<<<技能资料 开始" not in unbound_body

    # 3) 绑定仍在但技能被禁用 → 也不能注入（关了就必须真的不生效）
    SkillStore().set_enabled(record["id"], False)
    _assign(client, headers, "周报助理")
    disabled_body = capture.prompts[-1]
    assert BODY_MARK not in disabled_body
    assert "<<<技能资料 开始" not in disabled_body
