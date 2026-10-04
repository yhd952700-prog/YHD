"""P3 诚实多智能体协作证明 + P2 内容捕获修正测试。

P3：在无 LLM provider key 的环境下，确定性拆解器会把一个 file-write 目标拆成
>=2 个由**不同真实 agent** 执行的**真实**子任务（写文件 + 派生的 sha256 校验
侧车文件），组合结果（文件 + 侧车）在 ``GET /v1/goals/{id}`` 的 ``artifacts``
与 ``assigned_agent`` 上可观测。无 LLM、无模拟、无装饰性拆分。

P2：修正后的 ``_FILE_WRITE_A`` 正则对未加引号的 "containing X" 目标能捕获完
整多词内容，而不是只抓一个 token。

隔离约定：复用 test_p10 的 module 级 ``_isolate`` 模式，三个环境变量指向全新
临时目录并重置进程级单例，绝不污染真实工作区 / 审计库。
"""

from __future__ import annotations

import hashlib
import os
import tempfile

import pytest
from fastapi.testclient import TestClient

from src.gateway.main import get_app
from src.kernels.execution import Goal, GoalDecomposer

# 真实、可被确定性拆解器识别为 file_write 的目标短语（_FILE_WRITE_B 正则）。
EXPECTED_CONTENT = "LIUHAO multi-agent proof without an LLM provider key"
GOAL = "write '%s' to file ma.txt" % EXPECTED_CONTENT


def _auth_headers() -> dict:
    from src.security import get_jwt_handler

    token, _payload = get_jwt_handler().create_token(subject="p3-human")
    return {"Authorization": "Bearer %s" % token}


# ── 单元测试：拆解层（不依赖网关 / LLM）────────────────────────────


def test_unquoted_file_write_captures_full_content():
    """P2 修正：未加引号的 'containing X' 目标捕获完整多词内容。"""
    extracted = GoalDecomposer._extract_file_write(
        "create a file named report.txt containing hello world"
    )
    assert extracted is not None, "未识别 file-write 目标"
    assert extracted["path"] == "report.txt", extracted
    # 修正前 bare 组是 \S+，只会抓到 "hello"；修正后应抓到 "hello world"。
    assert extracted["content"] == "hello world", extracted


def test_file_write_goal_splits_into_two_distinct_agents():
    """P3：file-write 目标被拆成 >=2 个由不同 agent 执行的真实子任务。"""
    goal = Goal(id="g-ma", natural_language=GOAL)
    tasks = GoalDecomposer().decompose(goal)

    fw = [t for t in tasks if t.capability_id == "file_write"]
    assert len(fw) >= 2, "应至少有一个主写任务和一个校验侧车任务：%s" % tasks

    primary, sidecar = fw[0], fw[1]
    assert primary.name == "WriteFile", primary.name
    assert sidecar.name == "WriteChecksum", sidecar.name

    # 侧车内容是主文件内容的真实 sha256 —— 派生自同一目标，是真实不同的工作。
    assert sidecar.inputs["content"] == hashlib.sha256(
        EXPECTED_CONTENT.encode("utf-8")
    ).hexdigest(), sidecar.inputs
    # 侧车必须在主写之后运行（真实依赖，不是装饰性）。
    assert sidecar.dependencies == [primary.id], sidecar.dependencies

    # 两个任务被指派给**不同**的 agent —— 多智能体可观测。
    agents = {t.assigned_agent for t in tasks}
    assert len(agents) >= 2, "任务未指派给 >=2 个不同 agent：%s" % tasks
    assert primary.assigned_agent != sidecar.assigned_agent, tasks


# ── 网关端到端：真实 HTTP 链路（FastAPI TestClient，等同 uvicorn 服务的代码路径）


@pytest.fixture(scope="module", autouse=True)
def _isolate():
    base = tempfile.mkdtemp(prefix="lh_p3_")
    ws = os.path.join(base, "ws")
    alerts = os.path.join(base, "alerts.json")
    audit = os.path.join(base, "audit.db")
    os.makedirs(ws, exist_ok=True)

    saved = {}
    for key, val in (
        ("LIUHAO_WORKSPACE_ROOT", ws),
        ("LIUHAO_ALERTS_STORE_PATH", alerts),
        ("AUDIT_DB_PATH", audit),
    ):
        saved[key] = os.environ.get(key)
        os.environ[key] = val

    from src.gateway.ai_management import AIStateManager
    import src.kernels.audit as audit_mod

    AIStateManager._instance = None
    audit_mod._audit_store = None

    yield

    for key, val in saved.items():
        if val is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = val


@pytest.fixture(scope="module")
def client(_isolate):
    app = get_app()
    with TestClient(app) as c:
        yield c, _auth_headers()


def test_multi_agent_goal_writes_two_artifacts_and_two_agents(client):
    """P3 端到端：真实目标 -> 两个不同 agent 执行 -> 两个真实 artifact 落盘。"""
    c, headers = client

    r = c.post(
        "/v1/goals",
        json={"natural_language": GOAL, "scope": "L1"},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    body = r.json()

    # 同步执行应在请求内到达终态 completed。
    assert body["state"] == "completed", body

    # >=2 个不同 agent 参与了该目标。
    agents = {t["assigned_agent"] for t in body["tasks"]}
    assert len(agents) >= 2, "assigned_agent 不足两个不同值：%s" % body["tasks"]

    # >=2 个真实 artifact：主文件 + .sha256 侧车。
    arts = body["artifacts"]
    assert len(arts) >= 2, "artifacts 不足两个：%s" % arts
    assert any(p.endswith(".sha256") for p in arts), arts

    # 两个文件确实落在 workspace 下，且内容与预期一致（真实副作用）。
    from src.ai.workspace import workspace_root

    ws_root = workspace_root()
    primary_rel = [p for p in arts if not p.endswith(".sha256")][0]
    sidecar_rel = [p for p in arts if p.endswith(".sha256")][0]

    with open(os.path.join(ws_root, primary_rel), "r", encoding="utf-8") as fh:
        assert fh.read() == EXPECTED_CONTENT, "主文件内容不符"
    with open(os.path.join(ws_root, sidecar_rel), "r", encoding="utf-8") as fh:
        assert fh.read() == hashlib.sha256(
            EXPECTED_CONTENT.encode("utf-8")
        ).hexdigest(), "侧车校验值不符"


def test_multi_agent_goal_requires_auth(client):
    """无令牌 -> 401（真实鉴权闸门）。"""
    c, _headers = client
    r = c.post("/v1/goals", json={"natural_language": GOAL, "scope": "L1"})
    assert r.status_code == 401, r.text
