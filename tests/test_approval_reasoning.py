"""approval / reasoning 包真实实现测试（十源 packages 层深化）。"""

import pytest

from src.ai.approval import ApprovalWorkflow, ApprovalRequest, ApprovalStatus
from src.ai.reasoning import Reasoner, ReasoningStep


class _RecordingAudit:
    """测试 stub：记录 log_event 调用，避免真实 SQLite 审计落盘副作用。"""

    def __init__(self):
        self.events = []

    def log_event(self, **kwargs):
        self.events.append(kwargs)
        return None


class TestApprovalWorkflow:
    def test_submit_is_pending(self):
        wf = ApprovalWorkflow(audit_store=_RecordingAudit())
        req = wf.submit("deploy", "deploy:prod", "alice")
        assert isinstance(req, ApprovalRequest)
        assert req.status == ApprovalStatus.PENDING
        assert wf.status(req.id) == "pending"

    def test_approve_sets_status_and_records_audit(self):
        audit = _RecordingAudit()
        wf = ApprovalWorkflow(audit_store=audit)
        req = wf.submit("deploy", "deploy:prod", "alice", risk_level="HIGH")

        decided = wf.approve(req.id, "bob", "reviewed ok")
        assert decided is not None
        assert decided.status == ApprovalStatus.APPROVED
        assert decided.decided_by == "bob"
        assert decided.reason == "reviewed ok"
        # 审计留痕：审批决策被记录，principal=approver，outcome=allow
        assert len(audit.events) == 1
        assert audit.events[0]["principal_id"] == "bob"
        assert audit.events[0]["outcome"] == "allow"

    def test_reject_sets_status(self):
        wf = ApprovalWorkflow(audit_store=_RecordingAudit())
        req = wf.submit("delete", "delete:data", "alice")
        decided = wf.reject(req.id, "bob", "too risky")
        assert decided.status == ApprovalStatus.REJECTED
        assert wf.status(req.id) == "rejected"

    def test_duplicate_decision_rejected(self):
        wf = ApprovalWorkflow(audit_store=_RecordingAudit())
        req = wf.submit("x", "do:x", "alice")
        assert wf.approve(req.id, "bob") is not None
        # 已决策：拒绝重复审批，诚实返回 None
        assert wf.reject(req.id, "carol") is None
        assert wf.approve(req.id, "carol") is None

    def test_unknown_request_returns_none(self):
        wf = ApprovalWorkflow(audit_store=_RecordingAudit())
        assert wf.approve("does-not-exist", "bob") is None
        assert wf.status("does-not-exist") is None

    def test_pending_lists_only_pending(self):
        wf = ApprovalWorkflow(audit_store=_RecordingAudit())
        a = wf.submit("a", "do:a", "x")
        b = wf.submit("b", "do:b", "x")
        wf.approve(a.id, "x")
        pending = wf.pending()
        assert [p.id for p in pending] == [b.id]


class TestReasoner:
    def test_deduce_forward_chain(self):
        r = Reasoner()
        rules = [
            (["a"], "b"),
            (["b"], "c"),
            (["a", "c"], "d"),
        ]
        steps = r.deduce(["a"], rules)
        conclusions = [s.conclusion for s in steps]
        assert conclusions == ["b", "c", "d"]  # 确定性、有序、闭包
        assert all(s.confidence == 1.0 for s in steps)
        assert all(s.justification == "deduction" for s in steps)

    def test_deduce_no_rules_fired(self):
        r = Reasoner()
        steps = r.deduce(["a"], [(["z"], "y")])
        assert steps == []

    def test_reason_without_provider_is_honest_fallback(self):
        r = Reasoner()
        step = r.reason("what?", context=["ctx1"])
        assert isinstance(step, ReasoningStep)
        assert step.confidence == 0.0
        assert step.justification == "no-provider-fallback"
        assert step.conclusion == "<no-provider> what?"

    def test_reason_with_provider_uses_generate(self):
        class _P:
            def generate(self, prompt, **kw):
                return "the-answer"

        r = Reasoner()
        step = r.reason("q", context=["c"], provider=_P())
        assert step.conclusion == "the-answer"
        assert step.justification == "generative"
