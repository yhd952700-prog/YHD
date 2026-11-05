"""集成建议：只建议，不自动动手。

策略只有三种：``采用`` / ``改造`` / ``仅借鉴设计``。
许可证不可采用或未识别的候选，``adoptable`` 必为 ``False``，不得进入
「建议采用」列表。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from src.intel.licenses import LicenseDecision
from src.intel.models import Candidate, GapStatus, ProposalRecord, ProposalStrategy

DISCLAIMER = "由 AI 生成，未经人工复核"


@dataclass
class GapFindingLike:
    """鸭子类型：只需 ``capability`` 与 ``status`` 两个字段。"""

    capability: str
    status: str


def _effort(findings: Sequence[GapFindingLike]) -> str:
    statuses = {finding.status for finding in findings}
    if GapStatus.MISSING.value in statuses or GapStatus.FAKE.value in statuses:
        return "高"
    if GapStatus.PARTIAL.value in statuses:
        return "中"
    return "低"


def build_proposal(
    candidate: Candidate,
    decision: LicenseDecision,
    findings: Sequence[GapFindingLike],
    created_at: str,
) -> ProposalRecord:
    """根据许可证结论与能力缺口生成一条可持久化的建议。"""
    if not decision.allowed:
        return ProposalRecord(
            repo_url=candidate.repo_url,
            strategy=ProposalStrategy.BORROW.value,
            effort=_effort(findings),
            risk="高：许可证不可采用",
            license_conclusion=decision.reason,
            adoptable=False,
            summary=(
                f"{candidate.name} 不进入建议采用列表；"
                f"许可证结论：{decision.license_id} / {decision.verdict.value}"
            ),
            created_at=created_at,
            disclaimer=DISCLAIMER,
        )

    missing = any(
        finding.status in {GapStatus.MISSING.value, GapStatus.FAKE.value}
        for finding in findings
    )
    partial = any(finding.status == GapStatus.PARTIAL.value for finding in findings)

    if missing:
        strategy = ProposalStrategy.ADOPT
    elif partial:
        strategy = ProposalStrategy.ADAPT
    else:
        strategy = ProposalStrategy.BORROW

    risk = "中：许可证需要注明约束" if decision.requires_note else "低"

    return ProposalRecord(
        repo_url=candidate.repo_url,
        strategy=strategy.value,
        effort=_effort(findings),
        risk=risk,
        license_conclusion=f"{decision.license_id} / {decision.verdict.value} / {decision.category.value}：{decision.reason}",
        adoptable=True,
        summary=(
            f"{candidate.name} 建议策略为「{strategy.value}」；"
            f"对照能力 {len(findings)} 项（缺失/半成品由缺口分析给出）"
        ),
        created_at=created_at,
        disclaimer=DISCLAIMER,
    )


def list_adoptable(proposals: Sequence[ProposalRecord]) -> list[ProposalRecord]:
    return [proposal for proposal in proposals if proposal.adoptable]
