"""审计覆盖回归：员工、知识库、模型配置的写操作必须真的进哈希链。

背景
----
登录/权限/备份导出早就有审计，但"加员工 / 删文档 / 改模型设置"三条日常
操作此前**完全没有**审计写入。这个文件锁定修复后的契约：

* 事件真的落进 ``AuditStore``（不是只打日志）；
* 记录里能定位到 ``actor`` / ``target`` / ``action`` / ``outcome``；
* 界面标题是一句人话，不出现 ``snake_case`` 或点号命名空间。
"""
from __future__ import annotations

import sqlite3

from src.api.routes.knowledge import _audit_document_deleted
from src.gateway import audit as audit_module
from src.gateway.ai_management import _audit_employee_action
from src.gateway.display import event_display_title
from src.knowledge.documents import KnowledgeScope


def _events(action: str):
    return audit_module._read_events(action=action, limit=50, reverse=True)


def test_employee_hire_is_audited_with_target():
    _audit_employee_action(
        "employee.hired", None, "小北", details={"agent_count": 3}
    )

    events = _events("employee.hired")
    assert len(events) == 1
    event = events[0]
    assert event["target"] == "小北"
    assert event["outcome"] == "allowed"
    assert event["details"]["agent_count"] == 3


def test_employee_update_and_remove_are_audited():
    _audit_employee_action("employee.updated", None, "小北")
    _audit_employee_action("employee.removed", None, "小北")

    assert [e["target"] for e in _events("employee.updated")] == ["小北"]
    assert [e["target"] for e in _events("employee.removed")] == ["小北"]


def test_knowledge_delete_is_audited():
    scope = KnowledgeScope(
        principal="member1", owners=("member1",), include_unowned=False
    )
    _audit_document_deleted(scope, "doc-abc", 7)

    events = _events("knowledge.document.deleted")
    assert len(events) == 1
    event = events[0]
    assert event["target"] == "doc-abc"
    assert event["principal_id"] == "member1"
    assert event["details"]["chunks_removed"] == 7


def test_row_projection_exposes_target_at_top_level():
    row = (
        1, "e-1", "STATE_CHANGE", "root1", "L0", "2026-01-01T00:00:00Z",
        "corr-1", "allowed",
        '{"action": "employee.hired", "target": "小北"}',
        "hash", "prev", "sha256", "link",
    )
    event = audit_module._row_to_event(row)
    assert event["action"] == "employee.hired"
    assert event["target"] == "小北"


def test_display_titles_are_plain_language_with_target():
    assert (
        event_display_title(
            {"action": "employee.hired", "target": "小北", "outcome": "allowed"}
        )
        == "员工 · 新增：小北（已允许）"
    )
    assert (
        event_display_title({"action": "login", "outcome": "login_success"})
        == "登录（登录成功）"
    )
    assert (
        event_display_title(
            {
                "action": "knowledge.document.deleted",
                "target": "doc-abc",
                "outcome": "allowed",
            }
        )
        == "知识库 · 删除：doc-abc（已允许）"
    )
    # 绝不把内部点号命名空间 / snake_case 直接给用户。
    title = event_display_title(
        {"action": "model.default.changed", "target": "model-1", "outcome": "allowed"}
    )
    assert "." not in title
    assert "_" not in title
    # 结尾只是结果词时，动作必须回退到真正认识的那一段，而不是「账号 · 操作」。
    assert (
        event_display_title(
            {"action": "account.manage.denied", "target": "member1", "outcome": "denied"}
        )
        == "账号 · 管理：member1（已拒绝）"
    )
    assert (
        event_display_title(
            {"action": "key.owner", "target": "root1", "outcome": "allowed"}
        )
        == "密钥 · 归属：root1（已允许）"
    )


def test_manage_denial_is_audited():
    """越级改权限被拒后，审计里必须有"谁试过动谁"。"""
    from types import SimpleNamespace

    from src.gateway.account_context import _audit_manage_denied

    _audit_manage_denied(
        SimpleNamespace(principal="grand1"), "member1", "ACCOUNT_SUBTREE_DENIED"
    )

    event = _events("account.manage.denied")[0]
    assert event["principal_id"] == "grand1"
    assert event["target"] == "member1"
    assert event["outcome"] == "denied"


def test_redact_foreign_targets_only_redacts_accounts(monkeypatch):
    """对象名（员工/文档/模型）不能被当成账号名一起抹掉。"""
    from types import SimpleNamespace

    from src.gateway.account_context import AccountContext
    from src.kernels.identity import _accounts

    node = _accounts.AccountNode(
        principal="member1", parent_id="root1", depth=1, root_id="root1"
    )
    context = AccountContext(
        principal="member1",
        node=node,
        subtree=("member1", "grand1"),
        standalone=False,
    )
    fake_manager = SimpleNamespace(
        account_nodes=lambda: {
            "root1": object(),
            "member1": object(),
            "grand1": object(),
        }
    )
    monkeypatch.setattr(
        "src.kernels.identity.get_identity_manager", lambda: fake_manager
    )

    events = [
        {"target": "root1", "details": {"target": "root1"}},
        {"target": "员工-小北", "details": {"target": "员工-小北"}},
        {"target": "grand1", "details": {"target": "grand1"}},
    ]
    out = audit_module.redact_foreign_targets(events, context, sovereign=False)

    # 子树外的账号名必须抹掉（防枚举）。
    assert out[0]["target"] is None
    assert "target" not in out[0]["details"]
    # 对象名不是账号，必须保留 —— 否则连"对哪个员工做的"都看不到。
    assert out[1]["target"] == "员工-小北"
    # 子树内的账号名照常保留。
    assert out[2]["target"] == "grand1"


def test_full_verification_detects_direct_db_tampering():
    """绕过 API 直接改库后，全量校验必须报 ok=false。"""
    _audit_employee_action("employee.hired", None, "小北")
    from src.kernels.audit import get_audit_store

    store = get_audit_store()
    ok, _checked = store.verify_integrity()
    assert ok is True

    with sqlite3.connect(store._db_path) as conn:
        conn.execute(
            "UPDATE audit_events SET outcome = 'denied' "
            "WHERE seq = (SELECT MAX(seq) FROM audit_events)"
        )
        conn.commit()

    result = audit_module._run_verification(full=True)
    assert result["ok"] is False
    assert result["details"]["verification_mode"] == "full"
