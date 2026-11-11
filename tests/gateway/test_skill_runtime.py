"""技能运行时：真的把脚本跑起来，并把输出交回模型上下文。

要证明的几件事（对应 Boss 的验收口径）：

- 界面上"安装 → 启用 → 运行"这条链路真的能跑通，脚本真的被执行；
- 第一次运行必须经过 Boss 授权，授权前**不启动任何进程**；
- 授权可以"只这次"或"记住"，记住的授权能被清除；
- 超时能杀掉；子进程数量能限住；越界写能被拦；
- 运行结果写进审计，并且只有绑定该技能的员工能在提示词里看到。

所有落盘都在 ``tmp_path``，不碰真实工作区。
"""

from __future__ import annotations

import io
import json
import os
import zipfile

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.gateway.skills import router as skills_router


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    workspace = tmp_path / "ws"
    workspace.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("LIUHAO_WORKSPACE_ROOT", str(workspace))
    monkeypatch.setenv("LIUHAO_DATA_DIR", str(tmp_path / "data"))
    yield workspace


def _token(scope: str = "L0") -> str:
    from src.security import get_jwt_handler

    token, _payload = get_jwt_handler().create_token(
        subject="test-boss", metadata={"kind": "human", "scope": scope}
    )
    return token


def _headers() -> dict:
    return {"Authorization": f"Bearer {_token()}"}


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(skills_router)
    with TestClient(app) as test_client:
        yield test_client


def _zip(entries: dict) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, payload in entries.items():
            archive.writestr(name, payload)
    return buffer.getvalue()


def _install(client, name: str, script: str, *, limits: dict | None = None) -> dict:
    lines = [
        "---",
        f"name: {name}",
        "description: 运行时测试技能",
        "runtime:",
        "  limits:",
    ]
    for key, value in (limits or {}).items():
        lines.append(f"    {key}: {value}")
    lines += [
        "  entrypoints:",
        "    - id: main",
        "      interpreter: python",
        "      script: scripts/run.py",
        "---",
        "",
        "正文。",
        "",
    ]
    payload = _zip(
        {
            f"{name}/SKILL.md": "\n".join(lines),
            f"{name}/scripts/run.py": script,
        }
    )
    response = client.post(
        "/v1/skills/import",
        headers=_headers(),
        files={"file": (f"{name}.zip", payload, "application/zip")},
    )
    assert response.status_code == 201, response.text
    return response.json()


def _enable(client, skill_id: str) -> None:
    response = client.post(f"/v1/skills/{skill_id}/enable", headers=_headers())
    assert response.status_code == 200, response.text


def _run(client, skill_id: str, params: dict | None = None):
    return client.post(
        f"/v1/skills/{skill_id}/run",
        headers=_headers(),
        json={"entrypoint": "main", "params": params or {}},
    )


HAPPY_SCRIPT = """
import json, os

params = json.loads(os.environ.get("LIUHAO_SKILL_PARAMS") or "{}")
name = params.get("name", "anonymous")
with open("note.txt", "w", encoding="utf-8") as handle:
    handle.write("written-by-skill")
print(json.dumps({"summary": "你好 " + name, "ok": True}, ensure_ascii=False))
"""


def test_install_enable_run_requires_approval_then_executes(client):
    skill = _install(client, "hello-runner", HAPPY_SCRIPT)
    assert skill["runtime"]["installed"] is True
    assert skill["execution_allowed"] is False
    _enable(client, skill["id"])

    pending = _run(client, skill["id"], {"name": "世界"})
    assert pending.status_code == 200, pending.text
    body = pending.json()
    assert body["state"] == "pending_approval"
    assert body["approval"]["approval_id"]
    assert "首次运行需要你确认" in body["notice"]

    approved = client.post(
        f"/v1/skills/approvals/{body['approval']['approval_id']}/approve",
        headers=_headers(),
        json={"mode": "once"},
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["approved"] is True

    finished = _run(client, skill["id"], {"name": "世界"})
    assert finished.status_code == 200, finished.text
    run = finished.json()
    assert run["state"] == "finished", run
    result = run["run"]
    assert result["status"] == "ok", result
    assert result["exit_code"] == 0
    assert "你好 世界" in result["summary"]
    assert result["isolation"] in ("low_integrity+job", "job_only")

    # 脚本确实在运行目录里写了文件（不是"假装跑了"）。
    from src.gateway.skill_runtime import data_root

    workspace = os.path.join(data_root(), "skill-runs", result["run_id"], "workspace")
    assert os.path.isfile(os.path.join(workspace, "note.txt"))

    # 审计：开始与结束成对出现。
    audit = client.get(f"/v1/skills/{skill['id']}/audit", headers=_headers())
    assert audit.status_code == 200, audit.text
    events = [row["event"] for row in audit.json()["events"]]
    assert "start" in events and "finish" in events

    # 运行结果能被绑定该技能的员工看到；未绑定的员工一个字都拿不到。
    from src.ai.skill_prompt import render_skill_prompt
    from src.gateway.skill_store import SkillStore

    record = SkillStore().get(skill["id"])
    rendered = render_skill_prompt(
        "运行员",
        records=[record],
        bindings=[
            {
                "id": f"{skill['id']}:运行员",
                "skill_id": skill["id"],
                "employee": "运行员",
                "created_at": "2026-01-01T00:00:00Z",
            }
        ],
    )
    assert "你好 世界" in rendered.text
    assert "不可信数据" in rendered.text
    assert rendered.run_chars > 0

    other = render_skill_prompt("别的员工", records=[record], bindings=[])
    assert "你好 世界" not in other.text


def test_remembered_approval_can_be_revoked(client):
    skill = _install(client, "revoke-demo", HAPPY_SCRIPT)
    _enable(client, skill["id"])

    pending = _run(client, skill["id"]).json()
    approval_id = pending["approval"]["approval_id"]
    client.post(
        f"/v1/skills/approvals/{approval_id}/approve",
        headers=_headers(),
        json={"mode": "always"},
    )

    remembered = client.get(
        f"/v1/skills/{skill['id']}/approvals", headers=_headers()
    ).json()["remembered"]
    assert len(remembered) == 1

    # 记住后不再弹确认，直接跑。
    again = _run(client, skill["id"]).json()
    assert again["state"] == "finished", again

    cleared = client.delete(
        f"/v1/skills/{skill['id']}/approvals", headers=_headers()
    )
    assert cleared.status_code == 200
    assert cleared.json()["revoked"] == 1

    # 清掉之后必须重新确认，不能"永久记住"。
    after = _run(client, skill["id"]).json()
    assert after["state"] == "pending_approval", after


def test_timeout_kills_the_process(client):
    skill = _install(
        client,
        "slow-runner",
        "import time\nprint('started', flush=True)\ntime.sleep(60)\n",
        limits={"timeout_seconds": 2},
    )
    _enable(client, skill["id"])
    approval = _run(client, skill["id"]).json()["approval"]["approval_id"]
    client.post(
        f"/v1/skills/approvals/{approval}/approve",
        headers=_headers(),
        json={"mode": "once"},
    )

    body = _run(client, skill["id"]).json()
    run = body["run"]
    assert run["status"] == "timeout", run
    assert run["timed_out"] is True
    assert run["duration_ms"] < 20000


def test_child_process_is_blocked_by_job_limit(client):
    script = """
import json, subprocess, sys
try:
    subprocess.run([sys.executable, "-c", "print(1)"], check=True)
    outcome = "spawned"
except Exception as exc:
    outcome = "blocked: " + type(exc).__name__ + ": " + str(exc)
print(json.dumps({"summary": outcome}, ensure_ascii=False))
"""
    # 2 而不是 1：本项目的 .venv/python.exe 是 uv 的 trampoline，解释器启动
    # 本身要占两个进程槽；再 spawn 第三个就会被 Job 拒掉（WinError 1816）。
    skill = _install(client, "no-child", script, limits={"max_processes": 2})
    _enable(client, skill["id"])
    approval = _run(client, skill["id"]).json()["approval"]["approval_id"]
    client.post(
        f"/v1/skills/approvals/{approval}/approve",
        headers=_headers(),
        json={"mode": "once"},
    )

    run = _run(client, skill["id"]).json()["run"]
    assert "blocked" in run["summary"], run


def test_write_outside_workspace_is_blocked(client):
    script = """
import json, os
target = os.path.join(os.environ.get("SystemRoot", "C:\\\\Windows"), "liuhao-should-not-write.txt")
try:
    with open(target, "w", encoding="utf-8") as handle:
        handle.write("x")
    outcome = "wrote"
except Exception as exc:
    outcome = "blocked: " + type(exc).__name__
print(json.dumps({"summary": outcome}, ensure_ascii=False))
"""
    skill = _install(client, "no-escape", script)
    _enable(client, skill["id"])
    approval = _run(client, skill["id"]).json()["approval"]["approval_id"]
    client.post(
        f"/v1/skills/approvals/{approval}/approve",
        headers=_headers(),
        json={"mode": "once"},
    )

    run = _run(client, skill["id"]).json()["run"]
    assert "blocked" in run["summary"], run
    assert not os.path.exists(
        os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "liuhao-should-not-write.txt")
    )
