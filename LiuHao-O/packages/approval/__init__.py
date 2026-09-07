"""approval — 十源 packages 层 facade.

人类审批工作流：submit → pending → approve/reject → audit 留痕。

复用 src/ai/approval.py 的真实实现（不重写，NO-FAKE）。
"""

from src.ai.approval import ApprovalWorkflow, ApprovalRequest, ApprovalStatus

__all__ = ["ApprovalWorkflow", "ApprovalRequest", "ApprovalStatus"]
