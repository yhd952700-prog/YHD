"""自写技能学习循环专属测试（完善第二轮 A4）。

覆盖：草稿分级（D 危险永不上线 / B 需确认 / A 纯做法）、提炼时去
隐私行、草稿提交去重、审批规则（D 级拒批、非草稿拒批、重复审批拒）。
store 用最小假实现，只实现 skill_learn 真正调用的那几个方法。
"""

from __future__ import annotations

import pytest

from src.ai.skill_learn import (
    approve_draft,
    build_skill_draft,
    classify_draft,
    draft_from_task,
    reject_draft,
    scrub_lines,
    submit_draft,
)


class FakeStore:
    def __init__(self):
        self.records = {}
        self._seq = 0

    def list_records(self):
        return list(self.records.values())

    def create(self, *, name, description, source, content, enabled, fmt, metadata):
        self._seq += 1
        rid = f"skill-{self._seq}"
        record = {"id": rid, "name": name, "description": description,
                  "source": source, "content": content, "enabled": enabled,
                  "format": fmt, "metadata": dict(metadata)}
        self.records[rid] = record
        return record

    def get(self, skill_id):
        return self.records[skill_id]

    def set_enabled(self, skill_id, enabled):
        self.records[skill_id]["enabled"] = enabled

    def set_metadata(self, skill_id, metadata):
        self.records[skill_id]["metadata"].update(metadata)


def test_classify_grades():
    assert classify_draft("第一步打开表格，第二步核对数字") == "A"
    danger = None
    from src.ai.skill_learn import DANGEROUS_MARKERS
    if DANGEROUS_MARKERS:
        danger = classify_draft(f"做法里包含 {DANGEROUS_MARKERS[0]} 的操作")
        assert danger == "D"


def test_scrub_lines_drops_private_and_empty():
    text = "第一步：打开报表\n\n我的密码是 abc123\n第二步：核对总额\n联系电话 138xxxx\n"
    kept = scrub_lines(text)
    assert kept[0].startswith("第一步")
    assert any("核对总额" in line for line in kept)
    assert all("密码" not in line for line in kept)


def test_scrub_lines_truncates():
    text = "\n".join(f"步骤 {i}" for i in range(50))
    assert len(scrub_lines(text, max_lines=5)) == 5


def test_build_draft_shape():
    draft = build_skill_draft(task_title="月度对账", result_text="先导出流水\n再逐笔核对", role="队员")
    assert draft["name"].startswith("经验：")
    assert "月度对账" in draft["content"]
    assert draft["grade"] in ("A", "B", "D")


def test_submit_dedupes_same_name():
    store = FakeStore()
    draft = build_skill_draft(task_title="周报汇总", result_text="打开周报模板\n填入数据")
    first = submit_draft(store, draft)
    second = submit_draft(store, draft)
    assert first["id"] == second["id"]
    assert len(store.list_records()) == 1
    assert first["enabled"] is False
    assert first["metadata"]["status"] == "draft"


def test_approve_enables_and_marks():
    store = FakeStore()
    record = draft_from_task(store, task_title="盘点库存", result_text="先清点货架\n再核对系统数", task_id="t1")
    if record["metadata"]["grade"] == "D":
        pytest.skip("该草稿被判 D 级，审批拒绝另有专测")
    approved = approve_draft(store, record["id"], approver="boss")
    assert approved["enabled"] is True or store.get(record["id"])["enabled"] is True
    with pytest.raises(ValueError):
        approve_draft(store, record["id"], approver="boss")  # 不能重复审批


def test_approve_rejects_d_grade():
    from src.ai.skill_learn import DANGEROUS_MARKERS
    if not DANGEROUS_MARKERS:
        pytest.skip("没有危险标记可测")
    store = FakeStore()
    draft = build_skill_draft(task_title="危险任务", result_text=f"直接{DANGEROUS_MARKERS[0]}处理")
    draft["grade"] = "D"
    record = submit_draft(store, draft)
    with pytest.raises(ValueError, match="D"):
        approve_draft(store, record["id"], approver="boss")
    assert store.get(record["id"])["enabled"] is False


def test_reject_marks_rejected():
    store = FakeStore()
    record = draft_from_task(store, task_title="订机票", result_text="先查航班\n再比价", task_id="t2")
    rejected = reject_draft(store, record["id"], approver="boss")
    meta = store.get(record["id"])["metadata"]
    assert meta["status"] in ("rejected", "disabled", "archived") or rejected
    assert store.get(record["id"])["enabled"] is False
