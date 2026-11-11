"""员工补齐四项能力：复制 / 导入导出 / 质量评估 / 预算。

这些测试只使用真实落盘状态与真实路由，不 mock 业务结果：

* 预算：``calls_limit=0`` 首次派活必须 402；``calls_limit=1`` 第一次跑通、
  第二次 402，并核对预算接口里的消耗/剩余。
* 复制：技能绑定、工具能力、提示词随副本带走，副本默认停用，任务/记忆不复制。
* 导入导出：dry-run 先预览，冲突（同名 / employee_id）按策略处理；导出无密钥。
* 质量：只读台账真实行；没有数据时如实返回"算不出来"。
"""

from __future__ import annotations

import json
import os
import tempfile

os.environ.setdefault("LIUHAO_WORKSPACE_ROOT", tempfile.mkdtemp(prefix="liuhao-emp-ext-"))

import pytest
from fastapi.testclient import TestClient

from src.gateway.main import get_app
from src.gateway.ai_management import AIStateManager


def _auth_headers() -> dict:
    from src.security import get_jwt_handler

    token, _payload = get_jwt_handler().create_token(
        subject="test-human", metadata={"kind": "human", "scope": "L0"}
    )
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.setenv("LIUHAO_WORKSPACE_ROOT", str(tmp_path))
    AIStateManager._instance = None
    yield


@pytest.fixture
def client():
    app = get_app()
    with TestClient(app) as c:
        yield c, _auth_headers()


# ─── 预算 ──────────────────────────────────────────────────────


def test_budget_zero_calls_blocks_first_dispatch(client):
    """calls_limit=0：第一次派活就被 402 拦下，且没有任何执行痕迹。"""
    c, headers = client
    put = c.put(
        "/v1/employees/liuhao-default/budget",
        json={"calls_limit": 0, "period": "day"},
        headers=headers,
    )
    assert put.status_code == 200, put.text
    assert put.json()["enforced"] is True
    assert put.json()["remaining"]["calls"] == 0

    r = c.post("/v1/goals", json={"natural_language": "python: result = 1+1"}, headers=headers)
    assert r.status_code == 402, r.text
    detail = r.json()
    assert isinstance(detail, dict), f"unexpected detail: {r.text}"
    assert detail["code"] == "employee_budget_exceeded"
    assert "调用次数" in detail["message"]
    print("BUDGET_BLOCK_LOG zero_limit:", r.status_code, json.dumps(detail, ensure_ascii=False))

    view = c.get("/v1/employees/liuhao-default/budget", headers=headers).json()
    assert view["usage"]["calls"] == 0, "被拦下的派活不能记用量"


def test_budget_one_call_then_second_dispatch_blocked(client):
    """calls_limit=1：第一次完成任务，第二次派活 402（不依赖 provider 上报）。"""
    c, headers = client
    c.put(
        "/v1/employees/liuhao-default/budget",
        json={"calls_limit": 1, "period": "day", "reset_usage": True},
        headers=headers,
    )

    first = c.post("/v1/goals", json={"natural_language": "python: result = 2+2"}, headers=headers)
    assert first.status_code == 200, first.text
    assert first.json()["state"] in ("completed", "failed")

    view = c.get("/v1/employees/liuhao-default/budget", headers=headers).json()
    assert view["usage"]["calls"] >= 1
    assert view["remaining"]["calls"] == 0
    assert view["exceeded"] is True

    second = c.post("/v1/goals", json={"natural_language": "python: result = 3+3"}, headers=headers)
    assert second.status_code == 402, second.text
    print(
        "BUDGET_BLOCK_LOG one_call:",
        second.status_code,
        json.dumps(second.json(), ensure_ascii=False),
    )


def test_budget_weekly_period_and_clear(client):
    """周期可设周/月，展示下次重置时间；删除后回到不限。"""
    c, headers = client
    put = c.put(
        "/v1/employees/liuhao-default/budget",
        json={"calls_limit": 5, "tokens_limit": 1000, "cost_limit": 2.5, "period": "week"},
        headers=headers,
    )
    assert put.status_code == 200, put.text
    body = put.json()
    assert body["period_label"] == "每周"
    assert body["window"].count("W") == 1
    assert body["next_reset_at"]

    removed = c.delete("/v1/employees/liuhao-default/budget", headers=headers)
    assert removed.status_code == 200, removed.text
    assert removed.json()["configured"] is False

    view = c.get("/v1/employees/liuhao-default/budget", headers=headers).json()
    assert view["configured"] is False
    assert view["exceeded"] is False


def test_budget_rollover_clears_usage(tmp_path, monkeypatch):
    """跨周期读取时用量按 0 展示（不是把上期消耗算进本期）。"""
    from src.ai import budget

    path = budget.default_path()
    document = {
        "version": 1,
        "employees": {
            "周期员工": {
                "policy": {
                    "enabled": True,
                    "calls_limit": 1,
                    "tokens_limit": None,
                    "cost_limit": None,
                    "period": "day",
                },
                "window": "2000-01-01",
                "usage": {"calls": 9, "tokens": 100, "cost": 1.0},
            }
        },
    }
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(document, handle, ensure_ascii=False)

    view = budget.get_employee_budget("周期员工")
    assert view["usage"]["calls"] == 0
    assert view["exceeded"] is False


def test_budget_validation_errors(client):
    c, headers = client
    bad_period = c.put(
        "/v1/employees/liuhao-default/budget",
        json={"period": "year"},
        headers=headers,
    )
    assert bad_period.status_code == 400, bad_period.text
    assert bad_period.json()["code"] == "invalid_budget"

    negative = c.put(
        "/v1/employees/liuhao-default/budget",
        json={"calls_limit": -1},
        headers=headers,
    )
    assert negative.status_code == 400, negative.text


# ─── 复制 ──────────────────────────────────────────────────────


def _seed_employee_with_config(manager, name: str, *, prompt: str, skill: str) -> str:
    """准备一个带提示词/能力/技能绑定的真实员工（走 store + 真实技能库 + 绑定表）。

    技能必须真实存在于 ``SkillStore``：导入校验会跳过本机不存在的技能，
    用假 id 测不出真实链路。返回真实 skill_id 供断言使用。
    """
    result = manager.hire_employee(
        name,
        agent_count=2,
        agent_types=["planner", "executor"],
        role_description="负责测试复制",
        default_capabilities=["file_write"],
    )
    assert result["created"] is True
    emp = manager._store().load(name)
    emp.system_prompts = {"planner": prompt}
    manager._store().save(emp)
    from src.gateway.skill_bindings import SkillBindingStore
    from src.gateway.skill_store import SkillStore

    record = SkillStore().create(name=skill, content="测试技能正文", enabled=True)
    skill_id = str(record["id"])
    SkillBindingStore().bind(skill_id=skill_id, employee=name, actor="test-human")
    return skill_id


def test_copy_employee_carries_config_and_stays_disabled(client):
    c, headers = client
    manager = AIStateManager()
    skill_id = _seed_employee_with_config(
        manager, "复制源", prompt="你是复制源", skill="复制技能"
    )

    r = c.post("/v1/employees/复制源/copy", json={}, headers=headers)
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["name"] == "复制源-copy"
    assert body["employment_status"] == "disabled"
    assert body["copied"]["capabilities"] == ["file_write"]
    assert body["copied"]["skills"] == [skill_id]
    assert "planner" in body["copied"]["prompts"]

    copied = manager._store().load("复制源-copy")
    assert copied is not None
    assert copied.employment_status == "disabled"
    assert copied.system_prompts.get("planner") == "你是复制源"
    assert copied.total_tasks_completed == 0 and copied.total_tasks_failed == 0

    from src.gateway.skill_bindings import SkillBindingStore

    assert SkillBindingStore().skills_for_employee("复制源-copy") == [skill_id]

    # 再复制一次：自动加序号，不覆盖已有副本。
    second = c.post("/v1/employees/复制源/copy", json={}, headers=headers)
    assert second.status_code == 201, second.text
    assert second.json()["name"] == "复制源-copy-2"


def test_copy_missing_employee_404(client):
    c, headers = client
    r = c.post("/v1/employees/不存在/copy", json={}, headers=headers)
    assert r.status_code == 404


# ─── 导入 / 导出 ───────────────────────────────────────────────


def test_export_contains_config_without_secrets(client):
    c, headers = client
    manager = AIStateManager()
    skill_id = _seed_employee_with_config(
        manager, "导出员工", prompt="导出提示词", skill="导出技能"
    )

    r = c.get("/v1/employees/导出员工/export", headers=headers)
    assert r.status_code == 200, r.text
    doc = r.json()
    assert doc["format"] == "liuhao.employee-config"
    assert doc["employee"]["name"] == "导出员工"
    assert doc["employee"]["system_prompts"]["planner"] == "导出提示词"
    assert doc["employee"]["skills"] == [skill_id]
    assert doc["employee"]["default_capabilities"] == ["file_write"]
    text = json.dumps(doc, ensure_ascii=False).lower()
    for banned in ("api_key", "apikey", "secret", "password", "bearer", "sk-"):
        assert banned not in text, f"导出里出现了敏感字段：{banned}"


def test_export_strips_secret_like_keys():
    """导出路径的第二道保险：疑似凭据键一律剔除并留下说明。"""
    from src.gateway.employees_ext import _strip_secrets

    removed: list = []
    cleaned = _strip_secrets(
        {"system_prompts": {"planner": "ok"}, "api_key": "sk-xxx", "nested": {"token": "t"}},
        removed,
    )
    assert "api_key" not in cleaned
    assert "token" not in cleaned["nested"]
    assert cleaned["system_prompts"]["planner"] == "ok"
    assert set(removed) == {"api_key", "nested.token"}


def test_import_requires_preview_then_creates_disabled(client):
    c, headers = client
    manager = AIStateManager()
    skill_id = _seed_employee_with_config(
        manager, "导入源", prompt="导入提示词", skill="导入技能"
    )
    doc = c.get("/v1/employees/导入源/export", headers=headers).json()

    # 模拟"在另一台机器上导入"：本机没有这个 employee_id，先移除源员工。
    assert c.delete("/v1/employees/导入源", headers=headers).status_code == 200

    preview = c.post(
        "/v1/employees/import/preview",
        json={"config": doc, "as_name": "导入副本"},
        headers=headers,
    )
    assert preview.status_code == 200, preview.text
    plan = preview.json()
    assert plan["dry_run"] is True
    assert plan["summary"]["create"] == 1
    assert plan["plan"][0]["final_name"] == "导入副本"
    assert plan["plan"][0]["will_be_disabled"] is True

    # 未确认 → 400，不落盘。
    blocked = c.post(
        "/v1/employees/import",
        json={"config": doc, "as_name": "导入副本"},
        headers=headers,
    )
    assert blocked.status_code == 400, blocked.text
    assert blocked.json()["code"] == "import_confirmation_required"
    assert manager._store().load("导入副本") is None

    applied = c.post(
        "/v1/employees/import",
        json={"config": doc, "as_name": "导入副本", "confirm": True},
        headers=headers,
    )
    assert applied.status_code == 200, applied.text
    body = applied.json()
    assert body["created_count"] == 1
    created = manager._store().load("导入副本")
    assert created is not None and created.employment_status == "disabled"
    assert created.system_prompts.get("planner") == "导入提示词"

    from src.gateway.skill_bindings import SkillBindingStore

    assert SkillBindingStore().skills_for_employee("导入副本") == [skill_id]


def test_import_conflicts_skip_and_id_conflict(client):
    c, headers = client
    manager = AIStateManager()
    _seed_employee_with_config(manager, "冲突源", prompt="p", skill="冲突技能")
    doc = c.get("/v1/employees/冲突源/export", headers=headers).json()

    # 同名冲突：默认 skip，不覆盖现有员工。
    same = c.post("/v1/employees/import/preview", json={"config": doc}, headers=headers)
    assert same.status_code == 200, same.text
    assert same.json()["plan"][0]["action"] == "skip"
    assert same.json()["summary"]["skip"] == 1

    # 改名策略：换掉 employee_id，模拟"从另一台机器导入同名员工"，自动加后缀。
    foreign = json.loads(json.dumps(doc))
    foreign["employee"]["employee_id"] = "foreign-machine-employee-id"
    renamed = c.post(
        "/v1/employees/import/preview",
        json={"config": foreign, "on_conflict": "rename"},
        headers=headers,
    )
    assert renamed.status_code == 200, renamed.text
    assert renamed.json()["plan"][0]["action"] == "rename"
    assert renamed.json()["plan"][0]["final_name"].startswith("冲突源-import")

    # employee_id 冲突：即使改名也必须跳过（稳定 id 不能重复）。
    id_conflict = c.post(
        "/v1/employees/import/preview",
        json={"config": doc, "as_name": "另一个名字", "on_conflict": "rename"},
        headers=headers,
    )
    assert id_conflict.status_code == 200, id_conflict.text
    row = id_conflict.json()["plan"][0]
    assert row["action"] == "skip"
    assert "employee_id" in row["reason"]


def test_import_invalid_json_400(client):
    c, headers = client
    r = c.post("/v1/employees/import/preview", json={"config": "{not json"}, headers=headers)
    assert r.status_code == 400, r.text
    assert r.json()["code"] == "invalid_import_payload"


# ─── 质量 ──────────────────────────────────────────────────────


def test_quality_without_history_says_unavailable(client):
    c, headers = client
    r = c.get("/v1/employees/liuhao-default/quality", headers=headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["source"] == "liuhao_tasks.sqlite"
    assert body["available"] is False
    assert body["completion_rate"] is None
    assert any("没有" in note or "无法" in note for note in body["notes"])


def test_quality_uses_real_ledger_rows(client):
    c, headers = client
    goal = c.post("/v1/goals", json={"natural_language": "python: result = 7*6"}, headers=headers)
    assert goal.status_code == 200, goal.text

    r = c.get("/v1/employees/liuhao-default/quality", headers=headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["sample"] >= 1, "真实跑过目标后台账应该至少有一行"
    assert sum(body["status_counts"].values()) == body["sample"]
    assert body["manual_rework_count"] == 0
    assert body["source"] == "liuhao_tasks.sqlite"
    assert "completion_rate" in body["definitions"]
    # 有终态行时必须有可核对的比率；否则明确 null。
    if body["terminal"]:
        assert body["completion_rate"] is not None
        assert 0.0 <= body["completion_rate"] <= 1.0
