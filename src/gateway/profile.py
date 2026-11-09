"""鎏灏个人画像 HTTP 端点（KAREN Personal Intelligence）。

- ``GET    /v1/profile``          读取画像（默认主体 ``default``）
- ``PUT    /v1/profile``          写入画像项（称呼/偏好/事实/兴趣/专长/关系）
- ``GET    /v1/profile/summary``  画像摘要（即注入 system prompt 的那段文本）
- ``DELETE /v1/profile``          清空画像

主体命名约定（与对话链路一致，**只来自身份令牌**）：
- 令牌主体 ``sub`` → 画像主体 ``user-{sub}``（跨会话共享）
- 客户端提交的 ``principal`` / ``user_id`` / 查询参数一律不采信 ——
  否则任何已登录用户都能读写、甚至清空别人的画像。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from ..ai.personal_context import get_personal_context
from .policy import require_human_principal

router = APIRouter(prefix="/v1", tags=["profile"])


def _resolve(principal: str) -> str:
    """把令牌主体解析为画像主体（``user-{sub}``，与 chat 链路的绑定保持一致）。"""
    return f"user-{principal}"


class ProfileUpdate(BaseModel):
    """画像写入请求：所有字段可选，只写传了的项。

    **没有** ``principal`` / ``user_id`` 字段：主体只能来自令牌。
    """

    display_name: Optional[str] = Field(None, description="称呼")
    preference: Optional[Dict[str, Any]] = Field(
        None, description="单条偏好 {key, value, confidence?}"
    )
    fact: Optional[Dict[str, Any]] = Field(None, description="单条事实 {key, value}")
    interest: Optional[str] = None
    expertise: Optional[str] = None
    relationship: Optional[Dict[str, str]] = Field(
        None, description="关系 {who, relation}"
    )
    behavior_pattern: Optional[str] = None


@router.get("/profile")
def get_profile(principal: str = Depends(require_human_principal)) -> Dict[str, Any]:
    """返回**当前主体**的完整画像快照 + 摘要。"""
    pid = _resolve(principal)
    pcm = get_personal_context()
    profile = pcm.get_profile(pid)
    data = profile.to_dict()
    data["has_profile"] = pcm.has_profile(pid)
    data["pending_facts"] = pcm.list_pending_facts(pid)
    # 摘要：空画像时不给兜底串（与对话链路注入行为一致）。
    data["summary"] = pcm.summarize(pid) if pcm.has_profile(pid) else ""
    return data


@router.get("/profile/summary")
def get_profile_summary(
    principal: str = Depends(require_human_principal),
    max_items: int = Query(8, ge=1, le=50, description="摘要最大条目数"),
) -> Dict[str, Any]:
    """返回**当前主体**的画像摘要文本（即注入 system prompt 的那一段）。"""
    pid = _resolve(principal)
    pcm = get_personal_context()
    has = pcm.has_profile(pid)
    return {
        "principal": pid,
        "has_profile": has,
        "summary": pcm.summarize(pid, max_items=max_items) if has else "",
    }


@router.put("/profile")
def update_profile(
    req: ProfileUpdate, principal: str = Depends(require_human_principal)
) -> Dict[str, Any]:
    """写入**当前主体**的画像项（真实落盘 + 写穿 Memory Kernel）。"""
    pid = _resolve(principal)
    pcm = get_personal_context()
    written: List[str] = []

    if req.display_name:
        pcm.set_display_name(pid, req.display_name)
        written.append("display_name")

    if req.preference:
        key = str(req.preference.get("key", "")).strip()
        if key:
            pcm.set_preference(
                pid,
                key,
                req.preference.get("value"),
                confidence=float(req.preference.get("confidence", 0.8)),
            )
            written.append(f"preference:{key}")

    if req.fact:
        key = str(req.fact.get("key", "")).strip()
        if key:
            pcm.record_fact(pid, key, req.fact.get("value"))
            written.append(f"fact:{key}")

    if req.interest:
        pcm.add_interest(pid, req.interest)
        written.append(f"interest:{req.interest}")

    if req.expertise:
        pcm.add_expertise(pid, req.expertise)
        written.append(f"expertise:{req.expertise}")

    if req.relationship:
        who = str(req.relationship.get("who", "")).strip()
        if who:
            pcm.add_relationship(pid, who, req.relationship.get("relation", ""))
            written.append(f"relationship:{who}")

    if req.behavior_pattern:
        pcm.add_behavior_pattern(pid, req.behavior_pattern)
        written.append(f"behavior_pattern:{req.behavior_pattern}")

    profile = pcm.get_profile(pid)
    return {
        "principal": pid,
        "written": written,
        "profile": profile.to_dict(),
        "summary": pcm.summarize(pid) if pcm.has_profile(pid) else "",
    }


@router.delete("/profile")
def clear_profile(principal: str = Depends(require_human_principal)) -> Dict[str, Any]:
    """清空**当前主体**的画像（不可恢复），并同步删穿内核 PERSISTENT 副本。

    主体只来自令牌：``DELETE /v1/profile?principal=someone`` 不会删掉别人，
    查询参数被忽略。
    """
    pid = _resolve(principal)
    pcm = get_personal_context()
    kernel_deleted = pcm.clear(pid)
    return {
        "principal": pid,
        "cleared": True,
        "kernel_deleted": kernel_deleted,
        "has_profile": pcm.has_profile(pid),
    }


@router.delete("/profile/facts/{key}")
def delete_profile_fact(
    key: str, principal: str = Depends(require_human_principal)
) -> Dict[str, Any]:
    """删除单条事实（纠正错误记忆），同时删穿内核副本。"""
    pid = _resolve(principal)
    pcm = get_personal_context()
    deleted = pcm.delete_fact(pid, key)
    return {"principal": pid, "key": key, "deleted": deleted}


@router.delete("/profile/preferences/{key}")
def delete_profile_preference(
    key: str, principal: str = Depends(require_human_principal)
) -> Dict[str, Any]:
    """删除单条偏好（纠正错误记忆），同时删穿内核副本。"""
    pid = _resolve(principal)
    pcm = get_personal_context()
    deleted = pcm.delete_preference(pid, key)
    return {"principal": pid, "key": key, "deleted": deleted}


@router.get("/profile/pending")
def list_pending_facts(
    principal: str = Depends(require_human_principal),
) -> Dict[str, Any]:
    """模型提议、等待用户确认的长期事实（记忆投毒防线）。"""
    pid = _resolve(principal)
    return {
        "principal": pid,
        "pending": get_personal_context().list_pending_facts(pid),
    }


@router.post("/profile/pending/{pending_id}/approve")
def approve_pending_fact(
    pending_id: str, principal: str = Depends(require_human_principal)
) -> Dict[str, Any]:
    """用户确认后才真正写入长期事实。"""
    pid = _resolve(principal)
    item = get_personal_context().approve_pending_fact(pid, pending_id)
    if item is None:
        return {"principal": pid, "approved": False, "reason": "不存在或已处理"}
    return {"principal": pid, "approved": True, "item": item}


@router.post("/profile/pending/{pending_id}/reject")
def reject_pending_fact(
    pending_id: str, principal: str = Depends(require_human_principal)
) -> Dict[str, Any]:
    pid = _resolve(principal)
    item = get_personal_context().reject_pending_fact(pid, pending_id)
    if item is None:
        return {"principal": pid, "rejected": False, "reason": "不存在或已处理"}
    return {"principal": pid, "rejected": True, "item": item}
