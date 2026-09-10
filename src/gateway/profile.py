"""鎏灏个人画像 HTTP 端点（KAREN Personal Intelligence）。

- ``GET    /v1/profile``          读取画像（默认主体 ``default``）
- ``PUT    /v1/profile``          写入画像项（称呼/偏好/事实/兴趣/专长/关系）
- ``GET    /v1/profile/summary``  画像摘要（即注入 system prompt 的那段文本）
- ``DELETE /v1/profile``          清空画像

主体命名约定（与对话链路一致）：
- 传 ``user_id``  → 主体 ``user-{user_id}``（跨会话共享）
- 不传            → 主体取 ``principal`` 参数（默认 ``default``）
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field

from ..ai.personal_context import get_personal_context

router = APIRouter(prefix="/v1", tags=["profile"])

_DEFAULT_PRINCIPAL = "default"


def _resolve(principal: Optional[str], user_id: Optional[str]) -> str:
    """解析画像主体：user_id 优先，否则 principal，最后回退默认值。"""
    if user_id:
        return f"user-{user_id}"
    return principal or _DEFAULT_PRINCIPAL


class ProfileUpdate(BaseModel):
    """画像写入请求：所有字段可选，只写传了的项。"""

    principal: Optional[str] = Field(None, description="画像主体（不传 user_id 时使用）")
    user_id: Optional[str] = Field(None, description="用户 ID（优先，主体 user-{id}）")
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
def get_profile(
    principal: Optional[str] = Query(None, description="画像主体"),
    user_id: Optional[str] = Query(None, description="用户 ID（主体 user-{id}）"),
) -> Dict[str, Any]:
    """返回完整画像快照 + 摘要。"""
    pid = _resolve(principal, user_id)
    pcm = get_personal_context()
    profile = pcm.get_profile(pid)
    data = profile.to_dict()
    data["has_profile"] = pcm.has_profile(pid)
    # 摘要：空画像时不给兜底串（与对话链路注入行为一致）。
    data["summary"] = pcm.summarize(pid) if pcm.has_profile(pid) else ""
    return data


@router.get("/profile/summary")
def get_profile_summary(
    principal: Optional[str] = Query(None, description="画像主体"),
    user_id: Optional[str] = Query(None, description="用户 ID（主体 user-{id}）"),
    max_items: int = Query(8, ge=1, le=50, description="摘要最大条目数"),
) -> Dict[str, Any]:
    """返回画像摘要文本（即注入 system prompt 的那一段）。"""
    pid = _resolve(principal, user_id)
    pcm = get_personal_context()
    has = pcm.has_profile(pid)
    return {
        "principal": pid,
        "has_profile": has,
        "summary": pcm.summarize(pid, max_items=max_items) if has else "",
    }


@router.put("/profile")
def update_profile(req: ProfileUpdate) -> Dict[str, Any]:
    """写入画像项（真实落盘 + 写穿 Memory Kernel）。返回写入后的画像。"""
    pid = _resolve(req.principal, req.user_id)
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
def clear_profile(
    principal: Optional[str] = Query(None, description="画像主体"),
    user_id: Optional[str] = Query(None, description="用户 ID（主体 user-{id}）"),
) -> Dict[str, Any]:
    """清空某主体的画像（不可恢复）。"""
    pid = _resolve(principal, user_id)
    pcm = get_personal_context()
    pcm.clear(pid)
    return {"principal": pid, "cleared": True, "has_profile": pcm.has_profile(pid)}
