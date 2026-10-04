"""P10 诚实端到端证明：LIUHAO 在无 LLM provider key 下完成真实 Job。

证明 `POST /v1/goals` 走完整真实链路，且没有任何伪造 / mock：

- 确定性（无 LLM）的自然语言拆解器把一句话目标拆成 `file_write` 任务；
- `file_write` 工具在 workspace 内真实写一个文件（WorldInterface/FilesystemAdapter）；
- 目标详情 API 把该文件路径收进 `artifacts`；
- 产品读取面 `GET /v1/files/content` 能读到该真实文件；
- 审计链里存在以该目标 correlation_id 为索引的真实审计行；
- 不带令牌 → 401（真实鉴权闸门）。

隔离约定
--------
三个环境变量经 module 级 fixture 指向全新临时目录，并重置相关的进程级单例
（AIStateManager / audit store），确保 `file_write` 捕获的 workspace 根就是本
测试设定的临时根 —— 绝不污染真实工作区 / 真实审计库。整条链路不依赖任何
LLM provider key（拆解是确定性 keyword/regex，执行是真实本地工具）。
"""

from __future__ import annotations

import os
import tempfile

import pytest
from fastapi.testclient import TestClient

from src.gateway.main import get_app

# 真实、可被确定性拆解器识别为 file_write 的目标短语。
# _FILE_WRITE_B 正则：write '<content>' to file <path>
EXPECTED_CONTENT = "LIUHAO real job proof: executed without an LLM provider key"
GOAL = "write '%s' to file proof.txt" % EXPECTED_CONTENT


def _auth_headers() -> dict:
    """真实 bearer 令牌（网关 validator 同一签发路径）；不是 stub。"""
    from src.security import get_jwt_handler

    token, _payload = get_jwt_handler().create_token(subject="p10-human")
    return {"Authorization": "Bearer %s" % token}


@pytest.fixture(scope="module", autouse=True)
def _isolate():
    """指向全新临时目录并重置进程级单例，保证 file_write 根为本测试设定值。"""
    base = tempfile.mkdtemp(prefix="lh_p10_")
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

    # 重置进程级单例，使它们以新 env 重新初始化：
    # file_write 的 workspace 根只在首次 _ensure_runtime 时捕获一次。
    from src.gateway.ai_management import AIStateManager

    AIStateManager._instance = None
    import src.kernels.audit as audit_mod

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


def test_real_goal_writes_real_file_and_surfaces_artifact(client):
    """真实目标 -> 真实落盘 -> 产物进入 artifacts -> 读取面能读到同一文件。"""
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

    # 必须存在已完成（completed）的 file_write / WriteFile 任务。
    tasks = body["tasks"]
    fw = [t for t in tasks if t["capability_id"] == "file_write"]
    assert fw, "拆解器未产生 file_write 任务：%s" % tasks
    assert fw[0]["status"] == "completed", fw[0]

    # artifacts 非空且含相对路径。
    assert body["artifacts"], "artifacts 为空：%s" % body
    rel = body["artifacts"][0]

    # 文件确实落在 workspace 根下（真实副作用，不是声明）。
    from src.ai.workspace import workspace_root

    ws_root = workspace_root()
    disk_path = os.path.join(ws_root, rel)
    assert os.path.isfile(disk_path), "预期真实文件不存在：%s" % disk_path
    with open(disk_path, "r", encoding="utf-8") as fh:
        on_disk = fh.read()
    assert on_disk == EXPECTED_CONTENT, on_disk

    # 产品读取面读出的是同一真实文件。
    r2 = c.get("/v1/files/content", params={"path": rel}, headers=headers)
    assert r2.status_code == 200, r2.text
    assert r2.json()["content"] == EXPECTED_CONTENT, r2.json()


def test_real_goal_writes_audit_trail(client):
    """目标完成后，审计链里存在以该目标 correlation_id 为索引的真实审计行。"""
    c, headers = client

    r = c.post(
        "/v1/goals",
        json={"natural_language": GOAL, "scope": "L1"},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    cid = body["correlation_id"]
    assert cid, "响应缺少 correlation_id：%s" % body

    from src.kernels.audit import get_audit_store

    events = get_audit_store().query_events(correlation_id=cid)
    assert len(events) >= 1, "无审计行对应 correlation_id=%s" % cid


def test_goal_requires_auth(client):
    """未带令牌 -> 401（真实鉴权闸门，而非装饰性）。"""
    c, _headers = client
    r = c.post("/v1/goals", json={"natural_language": GOAL, "scope": "L1"})
    assert r.status_code == 401, r.text
