"""聊天工具补通路专属测试（完善第二轮 D 精而简审计）。

审计缺口：Boss 面板「建 Bot」只能点界面，聊天工具集没有 Bot/分组
工具；「备份」也没有聊天工具。补 manage_bots / backup_now 两个工具
（复用 workbench store 与 auto_backup 既有实现），并登记 B 档。
本测试直接调用工具函数（无 Key 可测层），验证真建得出、真备得出、
分档登记存在。
"""

from __future__ import annotations

import json

import pytest

from src.ai.tools.kernel_tools import make_tools


@pytest.fixture()
def tools(tmp_path, monkeypatch):
    monkeypatch.setenv("LIUHAO_WORKBENCH_DB", str(tmp_path / "workbench.db"))
    monkeypatch.setenv("LIUHAO_DATA_DIR", str(tmp_path))
    from src.workbench.store import _reset_workbench_store

    _reset_workbench_store()
    built = {t.name: t for t in make_tools("user-alice", include_disabled=True)}
    yield built
    _reset_workbench_store()


def _call(tool, **kwargs):
    return json.loads(tool.fn(**kwargs))


def test_manage_bots_create_group_bot_and_move(tools):
    tool = tools["manage_bots"]
    out = _call(tool, action="create_bot", name="管账 Bot", job="记账", group_name="财务")
    assert out["status"] == "ok"
    bot = out["bot"]
    assert bot["name"] == "管账 Bot" and bot["group_id"]

    out = _call(tool, action="list")
    assert [b["name"] for b in out["bots"]] == ["管账 Bot"]
    assert [g["name"] for g in out["groups"]] == ["财务"]

    out = _call(tool, action="create_group", group_name="学习")
    assert out["status"] == "ok"
    out = _call(tool, action="move_bot", bot_id=bot["id"], group_name="学习")
    assert out["status"] == "ok"
    out = _call(tool, action="list")
    groups = {g["id"]: g["name"] for g in out["groups"]}
    assert groups[out["bots"][0]["group_id"]] == "学习"


def test_manage_bots_rejects_bad_calls(tools):
    tool = tools["manage_bots"]
    assert _call(tool, action="create_bot")["status"] == "failed"
    assert _call(tool, action="nope")["status"] == "failed"


def test_backup_now_produces_backup(tools, tmp_path):
    tool = tools["backup_now"]
    out = _call(tool)
    assert out["status"] == "ok" and out["backup_id"]
    assert (tmp_path / "backups" / "auto").exists()


def test_new_tools_are_graded_b(tmp_path):
    from src.gateway.chat_tool_settings import CHAT_TOOL_GRADES

    for tool_id in ("liuhao.workbench.bots", "liuhao.backup.now"):
        grade = CHAT_TOOL_GRADES[tool_id]
        assert grade["grade"] == "B" and grade["default_enabled"] is False
