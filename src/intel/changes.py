"""版本变更检测：有变化才记一条，无变化不报。

比较基线必须是**最近一次可比快照**（排除抓取失败、没有版本也没有提交的行）。
这样一次断网不会把「v1.0.0」误判成「变成 None」，也不会产生假变更。

变更记录必须写清「从哪版到哪版、什么时候发现」；首次抓取不算变更。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from src.intel.models import ChangeRecord


@dataclass(frozen=True)
class ChangeDecision:
    changed: bool
    first_capture: bool
    previous_version: str | None
    current_version: str | None
    previous_commit: str | None
    current_commit: str | None
    reason: str

    def history(self, detected_at: str | None = None) -> dict[str, Any]:
        """给 ``osi_provenance.change_history`` 用的结构化说明。"""
        if self.first_capture:
            return {"first_capture": True}
        payload: dict[str, Any] = {
            "changed": self.changed,
            "previous_version": self.previous_version,
            "current_version": self.current_version,
            "previous_commit": self.previous_commit,
            "current_commit": self.current_commit,
            "reason": self.reason,
        }
        if self.changed and detected_at:
            payload["detected_at"] = detected_at
        return payload

    def to_record(
        self,
        repo_url: str,
        *,
        source_url: str,
        detected_at: str,
        source: str = "github",
    ) -> ChangeRecord | None:
        if not self.changed:
            return None
        return ChangeRecord(
            repo_url=repo_url,
            source=source,
            source_url=source_url,
            previous_version=self.previous_version,
            current_version=self.current_version,
            previous_commit=self.previous_commit,
            current_commit=self.current_commit,
            detected_at=detected_at,
            note=self.reason,
        )


def detect_change(
    previous: dict[str, Any] | None,
    current_version: str | None,
    current_commit: str | None,
    *,
    current_available: bool,
) -> ChangeDecision:
    """比较最近一次快照与本次抓取结果。

    * ``previous is None`` → 首次抓取，不是变更；
    * 本次抓取失败或没有版本/提交 → 不可比较，不产生变更；
    * 版本或提交任一不同 → 有变化，必须记一条。
    """
    previous_version = str(previous.get("version")) if previous and previous.get("version") else None
    previous_commit = (
        str(previous.get("commit_sha")) if previous and previous.get("commit_sha") else None
    )
    previous_tag = previous.get("tag") if previous else None
    if not previous_tag and previous_version and previous_version != previous_commit:
        # 兼容旧快照：没有单独存 tag 时，version 与 commit 不同即可视为 tag。
        previous_tag = previous_version

    if previous is None:
        return ChangeDecision(
            changed=False,
            first_capture=True,
            previous_version=None,
            current_version=current_version,
            previous_commit=None,
            current_commit=current_commit,
            reason="first_capture",
        )

    if not current_available or (current_version is None and current_commit is None):
        return ChangeDecision(
            changed=False,
            first_capture=False,
            previous_version=previous_version,
            current_version=current_version,
            previous_commit=previous_commit,
            current_commit=current_commit,
            reason="current_unavailable",
        )

    # 发布接口失败时 current_version 会是 None（不再拿 commit 冒充 tag）。
    # 此时只比 commit，避免把「v1.0.0 → aaa111」误判成版本变更。
    tag_changed = bool(current_version) and previous_tag != current_version
    commit_changed = current_commit is not None and previous_commit != current_commit
    changed = tag_changed or commit_changed
    previous_display = previous_tag or previous_version or previous_commit
    current_display = current_version or current_commit
    reason = (
        f"{previous_display} -> {current_display}" if changed else "version_unchanged"
    )
    return ChangeDecision(
        changed=changed,
        first_capture=False,
        previous_version=previous_version,
        current_version=current_version,
        previous_commit=previous_commit,
        current_commit=current_commit,
        reason=reason,
    )


__all__ = ["ChangeDecision", "detect_change"]
