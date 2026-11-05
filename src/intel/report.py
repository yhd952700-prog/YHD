"""只读报告：生成「开源技术雷达周报」到本地文件。

报告只写入 ``data/intel/reports/``（已被 ``.gitignore`` 忽略），不进入代码库。
每条建议都标注「由 AI 生成，未经人工复核」。
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any

from src._time import utc_now
from src.intel.models import (
    AdvisoryRecord,
    Candidate,
    ChangeRecord,
    GapRecord,
    LicenseRecord,
    ProposalRecord,
    ProvenanceRecord,
    ScanBudget,
    Snapshot,
)

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
DISCLAIMER = "由 AI 生成，未经人工复核"

_STATUS_LABELS = {
    "fetched": "已获取",
    "partial": "部分未获取",
    "not_fetched": "未获取",
}

_GAP_LABELS = {
    "existing": "🟢 真接线",
    "partial": "🟡 半成品",
    "fake": "🔴 假实现",
    "missing": "⚫ 完全没有",
}


def default_report_path(now: str | None = None) -> Path:
    """默认报告路径；``data/`` 已被 gitignore，不会污染代码库。"""
    stamp = (now or utc_now().isoformat()).replace(":", "").replace("-", "")[:15]
    return _PROJECT_ROOT / "data" / "intel" / "reports" / f"osi-radar-{stamp}.md"


def write_report(text: str, path: Path | str) -> Path:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(text, encoding="utf-8")
    return destination


def _table(headers: Sequence[str], rows: Iterable[Sequence[Any]]) -> list[str]:
    lines = ["| " + " | ".join(headers) + " |", "|" + "|".join(["---"] * len(headers)) + "|"]
    for row in rows:
        lines.append("| " + " | ".join(str(cell) for cell in row) + " |")
    return lines


def render_report(
    *,
    candidates: Sequence[Candidate],
    snapshots: Sequence[Snapshot],
    licenses: Sequence[LicenseRecord],
    advisories: Sequence[AdvisoryRecord],
    gaps: Sequence[GapRecord],
    proposals: Sequence[ProposalRecord],
    provenance: Sequence[ProvenanceRecord],
    changes: Sequence[ChangeRecord] = (),
    budget: ScanBudget,
    category: str,
    repo_urls: Sequence[str],
    quota_notes: Sequence[str] = (),
    capability_map_source: str | None = None,
    generated_at: str | None = None,
) -> str:
    generated = generated_at or utc_now().isoformat()
    fetched_count = sum(1 for candidate in candidates if candidate.last_status == "fetched")
    partial_count = sum(1 for candidate in candidates if candidate.last_status == "partial")
    unfetched_count = sum(1 for candidate in candidates if candidate.last_status == "not_fetched")
    lines: list[str] = [
        "# 鎏灏 · 开源技术雷达周报",
        "",
        f"- 生成时间：{generated}",
        (
            f"- 本轮范围：GitHub 单源 + OSV 安全公告 · 提交 {len(repo_urls)} 个仓库"
            f" · 许可证类别 `{category}`"
        ),
        "- **每一条建议均由 AI 生成，未经人工复核。**",
        "",
        "## 配额与预算",
        "",
        (
            f"- 本轮仓库：提交 {len(repo_urls)}，完整获取 {fetched_count}，"
            f"部分获取 {partial_count}，未获取 {unfetched_count}"
        ),
        f"- 仓库数量硬顶：{budget.max_repos}，本轮占用 {budget.repos_reserved}",
        f"- API 调用硬顶：{budget.max_api_calls}，本轮已用 {budget.api_calls_used}",
        (
            f"- Token 硬顶：{budget.max_tokens}，本轮实际消耗 {budget.tokens_used}"
            "（本轮未接入 LLM；0 是实际值，不是估算）"
        ),
    ]
    lines.extend(f"- 配额事件：{note}" for note in quota_notes)
    if not quota_notes:
        lines.append("- 配额事件：无")

    lines.extend(
        [
            "",
            "## 候选仓库",
            "",
        ]
    )
    if candidates:
        lines.extend(
            _table(
                ["仓库", "语言", "Star", "最近提交", "本轮状态", "来源"],
                [
                    (
                        candidate.name,
                        candidate.language or "-",
                        candidate.stars if candidate.stars is not None else "-",
                        candidate.last_commit_at or "-",
                        _STATUS_LABELS.get(candidate.last_status, candidate.last_status)
                        + (f"（{candidate.last_error}）" if candidate.last_error else ""),
                        candidate.repo_url,
                    )
                    for candidate in candidates
                ],
            )
        )
    else:
        lines.append("本轮没有候选。")

    lines.extend(["", "## 许可证判定", ""])
    if licenses:
        lines.extend(
            _table(
                ["仓库", "许可证", "判定", "类别", "可采用", "结论"],
                [
                    (
                        record.repo_url,
                        record.license_id,
                        record.verdict,
                        record.category,
                        "可" if record.allowed else "否",
                        record.reason,
                    )
                    for record in licenses
                ],
            )
        )
    else:
        lines.append("本轮没有许可证记录。")

    lines.extend(
        [
            "",
            "## 安全公告",
            "",
            "“无公告记录”只表示本次 OSV 查询返回为空，**不代表该仓库安全**；"
            "查询失败/包名无法解析一律记为“未获取”。",
            "",
        ]
    )
    if advisories:
        lines.extend(
            _table(
                ["仓库", "来源", "生态/包名", "公告编号", "影响区间", "严重级别", "状态", "原因/说明"],
                [
                    (
                        record.repo_url,
                        record.source,
                        (
                            f"{record.ecosystem}/{record.package}"
                            if record.ecosystem and record.package
                            else "-"
                        ),
                        record.advisory_id or "-",
                        record.affected_range or "-",
                        record.severity or "-",
                        (
                            "已查询·无公告记录"
                            if record.fetch_status == "fetched" and not record.advisory_id
                            else _STATUS_LABELS.get(record.fetch_status, record.fetch_status)
                        ),
                        record.error_reason or record.note or "-",
                    )
                    for record in advisories
                ],
            )
        )
    else:
        lines.append("本轮没有安全公告记录。")

    lines.extend(["", "## 版本变更", ""])
    if changes:
        lines.extend(
            _table(
                ["仓库", "从", "到", "发现时间", "来源"],
                [
                    (
                        record.repo_url,
                        record.previous_version or record.previous_commit or "-",
                        record.current_version or record.current_commit or "-",
                        record.detected_at,
                        record.source_url,
                    )
                    for record in changes
                ],
            )
        )
    else:
        lines.append("本轮未发现版本变化（无变化不记条目）。")

    lines.extend(["", "## 能力缺口对照", ""])
    if capability_map_source:
        lines.append(f"唯一对照源：`{capability_map_source}`，按四态判定。")
    else:
        lines.append("唯一对照源：`docs/capability-map.md`，按四态判定。")
    lines.append("未使用候选仓库 README 推断本仓能力；报告只列与本候选主题相关的行。")
    if gaps:
        relevant = [record for record in gaps if record.relevant]
        lines.extend(
            _table(
                ["仓库", "能力", "状态", "证据文件"],
                [
                    (
                        record.repo_url,
                        record.capability,
                        _GAP_LABELS.get(record.status, record.status),
                        ", ".join(record.file_paths) if record.file_paths else "-",
                    )
                    for record in relevant
                ],
            )
        )
        if not relevant:
            lines.append(
                "本轮未能在能力地图中定位到与候选主题对应的能力行；"
                "全量对照已落库，未据此推断缺口。"
            )
    else:
        lines.append("本轮没有缺口记录。")

    lines.extend(
        [
            "",
            "## 建议采用 / 改造（仅限许可证与缺口均通过的候选）",
            "",
        ]
    )
    adoptable = [proposal for proposal in proposals if proposal.adoptable]
    if adoptable:
        for proposal in adoptable:
            lines.append(
                f"- **{proposal.repo_url}** · 策略 {proposal.strategy} · 工作量 {proposal.effort}"
                f" · 风险 {proposal.risk} · 许可证结论：{proposal.license_conclusion}"
                f"（{DISCLAIMER}）"
            )
    else:
        lines.append("本轮没有候选进入建议采用列表。")

    lines.extend(["", "## 不建议采用", ""])
    rejected = [proposal for proposal in proposals if not proposal.adoptable]
    if rejected:
        for proposal in rejected:
            lines.append(
                f"- **{proposal.repo_url}** · 策略 {proposal.strategy} · 工作量 {proposal.effort}"
                f" · 风险 {proposal.risk} · 许可证结论：{proposal.license_conclusion}"
                f"（{DISCLAIMER}）"
            )
    else:
        lines.append("本轮没有被拒绝的候选。")

    lines.extend(["", "## 本轮未获取仓库/资源及原因", ""])
    unfetched: list[str] = []
    seen: set[tuple[str, str, str]] = set()
    for candidate in candidates:
        if candidate.last_status == "not_fetched":
            key = (candidate.repo_url, "repo", candidate.repo_url)
            if key not in seen:
                seen.add(key)
                unfetched.append(
                    f"- {candidate.repo_url} · 仓库 · {candidate.last_error or '未获取'}"
                    f" · {candidate.repo_url} · {candidate.last_assessed_at}"
                )
    for snapshot in snapshots:
        for detail in snapshot.details:
            if detail.get("status") == "fetched":
                continue
            key = (
                snapshot.repo_url,
                str(detail.get("resource")),
                str(detail.get("url")),
            )
            if key in seen:
                continue
            seen.add(key)
            unfetched.append(
                f"- {snapshot.repo_url} · {detail.get('resource')} · "
                f"{detail.get('reason') or '未获取'} · {detail.get('url')} · "
                f"{detail.get('attempted_at')}"
            )
    for advisory in advisories:
        if advisory.fetch_status == "fetched":
            continue
        key = (advisory.repo_url, "advisory", advisory.source_url)
        if key in seen:
            continue
        seen.add(key)
        unfetched.append(
            f"- {advisory.repo_url} · OSV 安全公告 · "
            f"{advisory.error_reason or '未获取'} · {advisory.source_url} · {advisory.fetched_at}"
        )
    if unfetched:
        lines.extend(unfetched)
    else:
        lines.append("本轮必需抓取资源均已获取；安全公告采集状态见上表。")
    lines.append("配额耗尽后本轮不再追加任何候选、公告或建议条目；表中没有的仓库即未评估。")

    lines.extend(["", "## 来源与版本", ""])
    if provenance:
        lines.extend(
            _table(
                ["仓库", "来源", "版本", "许可证", "修改历史", "抓取时间"],
                [
                    (
                        record.repo_url,
                        record.source,
                        record.version or "-",
                        record.license_id or "-",
                        _history_text(record.change_history),
                        record.captured_at,
                    )
                    for record in provenance
                ],
            )
        )
    else:
        lines.append("本轮没有来源记录。")

    lines.extend(
        [
            "",
            "---",
            "",
            "模块边界：只接受显式仓库清单，不扫描全量开源；只发现、只建议，"
            "不自动安装依赖、不修改主分支代码、不合并第三方源码、未经人工确认不进入生产链路。",
        ]
    )
    return "\n".join(lines) + "\n"


def _history_text(history: dict[str, Any]) -> str:
    if not history:
        return "-"
    if history.get("first_capture"):
        return "首次抓取"
    if history.get("changed"):
        previous = history.get("previous_version") or history.get("previous_commit")
        current = history.get("current_version") or history.get("current_commit")
        detected = history.get("detected_at")
        suffix = f"（{detected} 发现）" if detected else ""
        return f"变更：{previous} → {current}{suffix}"
    return "版本未变"
