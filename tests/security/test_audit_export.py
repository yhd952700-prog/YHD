"""审计报告导出回归测试（``export_report``）。

背景：``src/security/audit_policy.py`` 的模块 docstring 依据 ``DL:§114``
声明 Audit Kernel 必须支持 "Export audit reports for compliance"，
但该能力长期缺失。本文件锁定新增的 ``export_report(format)`` 行为：

- ``json``：结构化数组，字段完整、event_type 取 ``.value``；
- ``csv``：首行表头 + 每条一行，``None`` 的 permission/reason 落为 ``""``；
- 非法 format：抛 ``ValueError``；
- 空审计：json 为 ``[]``、csv 仅表头。

隔离策略：审计内核是模块级全局单例（``_global_audit_kernel``），
测试用 ``monkeypatch`` 换成全新实例，避免污染其他测试。
"""

from __future__ import annotations

import csv
import io
import json

import pytest

from src.security import audit_policy
from src.security.audit_policy import (
    AuditEventType,
    AuditKernel,
    export_report,
)


@pytest.fixture()
def fresh_kernel(monkeypatch):
    """把模块级全局审计内核换成隔离实例，避免跨测试串扰。"""
    kernel = AuditKernel()
    monkeypatch.setattr(audit_policy, "_global_audit_kernel", kernel)
    return kernel


def _seed(kernel: AuditKernel) -> None:
    kernel.log(
        AuditEventType.ACCESS_DECISION,
        principal_id="alice",
        permission="memory:write",
        scope="L1",
        result="allow",
        reason="rbac-ok",
        metadata={"policy_enforced": False},
    )


def test_export_report_json_shape(fresh_kernel):
    _seed(fresh_kernel)

    payload = json.loads(export_report("json"))

    assert isinstance(payload, list) and len(payload) == 1
    entry = payload[0]
    assert entry["principal_id"] == "alice"
    assert entry["permission"] == "memory:write"
    assert entry["scope"] == "L1"
    assert entry["result"] == "allow"
    assert entry["reason"] == "rbac-ok"
    # event_type 必须序列化为字符串值，而不是枚举 repr
    assert entry["event_type"] == AuditEventType.ACCESS_DECISION.value
    # hash 链字段随条目导出，供合规审计核对
    assert isinstance(entry["hash"], str) and entry["hash"]
    assert isinstance(entry["prev_hash"], str)
    assert entry["metadata"] == {"policy_enforced": False}
    # timestamp 为 ISO 字符串而非对象
    assert isinstance(entry["timestamp"], str)


def test_export_report_csv_header_and_row(fresh_kernel):
    _seed(fresh_kernel)

    text = export_report("csv")
    rows = list(csv.reader(io.StringIO(text)))

    assert rows[0] == [
        "id", "timestamp", "event_type", "principal_id",
        "permission", "scope", "result", "reason",
        "correlation_id", "prev_hash", "hash",
    ]
    assert len(rows) == 2
    data = dict(zip(rows[0], rows[1]))
    assert data["principal_id"] == "alice"
    assert data["event_type"] == AuditEventType.ACCESS_DECISION.value
    assert data["permission"] == "memory:write"


def test_export_report_csv_nullable_permission_becomes_empty(fresh_kernel):
    """permission 为 None 时 CSV 落空串而非字符串 "None"。

    reason 由 ``AuditKernel.log`` 自动补默认文案，故此处只断言 permission。
    """
    fresh_kernel.log(
        AuditEventType.SYSTEM_BOOT,
        principal_id="system",
        scope="L0",
        result="ok",
    )

    rows = list(csv.reader(io.StringIO(export_report("csv"))))
    data = dict(zip(rows[0], rows[1]))
    assert data["permission"] == ""
    assert "None" not in data["permission"]


def test_export_report_empty_kernel(fresh_kernel):
    assert json.loads(export_report("json")) == []
    rows = list(csv.reader(io.StringIO(export_report("csv"))))
    assert len(rows) == 1  # 仅表头


@pytest.mark.parametrize("bad", ["xml", "yaml", "JSON", ""])
def test_export_report_rejects_unsupported_format(fresh_kernel, bad):
    with pytest.raises(ValueError):
        export_report(bad)
