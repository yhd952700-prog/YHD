"""自我学习写技能的安全边界版（七家合并 P2，Hermes 式学习循环）。

循环：任务完成 → 队长提炼“这次怎么做才顺利” → 写成 SKILL.md 草稿
（agentskills.io 公开格式：名字、一句话用途、什么时候用、分步做法、
注意事项；格式本身是公开标准，内容是 Jarvis 自己从任务里提炼的，
不抄任何第三方技能）→ 进审核队列（默认关、聊天里调不到）
→ 自动分级 → Joseph（Owner）在技能页点启用才生效。

安全边界（违反即作废，见 seven-product-merge.md §7.2）：

1. 自写技能只许写非危险的：含写文件/删文件/跑命令/联网任意地址/
   改权限/改白名单/改审批/跳过确认/“不用问 Joseph” 的做法一律分级为 D，
   永不上线（批了也拒绝启用）。
2. 写技能 ≠ 启用技能：草稿默认关；启用要 Joseph 点一次。
3. 技能不能给自己加能力：草稿不许修改技能分级、不许改白名单、不许改
   审批规则、不许碰禁区；技能文件只存在技能库里，不进代码仓。
4. 技能不能替 Joseph 做决定：草稿里出现“不用问 Joseph 直接做”类
   话术直接判 D。
5. 可追溯：每条草稿记从哪次任务来（plan_id / 任务 id）、谁写的
   （队长）、什么时候写的、谁批的、什么时候启用的。
6. 可回滚：关掉即失效；关掉不删草稿与审计。

隐私：提炼时只提炼做法，不抄对话原文、不抄 Key、不抄隐私——含
Key/密钥/密码/token 字样的行直接丢掉。
"""

from __future__ import annotations

import re
import time
from typing import Any, Dict, List, Optional

#: 分级为 D（永不上线）的危险信号：做法里出现任一即判 D。
DANGEROUS_MARKERS = (
    "删文件", "删除文件", "删除目录", "rm ", "rm -rf", "format",
    "写文件", "改代码", "改技能", "改白名单", "改审批", "放宽",
    "跳过审批", "绕过审批", "不用问", "不要问", "直接做", "直接执行",
    "跑命令", "执行命令", "shell", "改权限", "提权",
    "密钥", "key", "token", "密码", "password", "secret",
    "禁区", "src/security", "src/identity", "src/selfmod",
)

#: 分级为 B（只读以上、需确认才启用）的信号：含联网/读文件/外部服务。
B_LEVEL_MARKERS = (
    "联网", "查", "检索", "读文件", "下载", "http", "api",
)

#: 提炼时丢掉的行：含隐私/密钥字样的一律不进草稿。
_PRIVATE_MARKERS = ("key", "密钥", "密码", "password", "token", "secret")


def classify_draft(content: str) -> str:
    """自动分级：D（危险，永不上线）/ B（需确认）/ A（纯做法）。"""
    lowered = (content or "").lower()
    for marker in DANGEROUS_MARKERS:
        if marker.lower() in lowered:
            return "D"
    for marker in B_LEVEL_MARKERS:
        if marker.lower() in lowered:
            return "B"
    return "A"


def scrub_lines(text: str, *, max_lines: int = 20) -> List[str]:
    """提炼做法：去隐私行、去空行、截断（只提炼做法，不抄对话原文）。"""
    kept: List[str] = []
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        lowered = line.lower()
        if any(marker in lowered for marker in _PRIVATE_MARKERS):
            continue
        kept.append(line)
        if len(kept) >= max_lines:
            break
    return kept


def build_skill_draft(
    *,
    task_title: str,
    result_text: str,
    role: str = "",
    plan_id: str = "",
    task_id: str = "",
) -> Dict[str, Any]:
    """从一次完成的任务提炼 SKILL.md 草稿（agentskills.io 格式）。"""
    title = (task_title or "未命名任务").strip()
    steps = scrub_lines(result_text)
    if not steps:
        steps = ["这次任务的做法没有留下可复用的文字步骤（只记结论，不编做法）。"]
    name = f"经验：{title[:20]}"
    description = f"从{role or '队员'}任务「{title[:40]}」提炼的做法，下次同类任务可复用。"
    body_lines = [
        f"# {name}",
        "",
        f"用途：{description}",
        "",
        "什么时候用：下次遇到和这次类似的任务时，先看这份做法再动手。",
        "",
        "做法：",
    ]
    body_lines.extend(f"{i + 1}. {line}" for i, line in enumerate(steps))
    body_lines.extend(
        [
            "",
            "注意事项：",
            "- 只做上面写的做法，不做没写的事；",
            "- 涉及删文件、改代码、对外发消息时，先问 Joseph，不自己动手；",
            "- 不确定的地方如实说不知道，不编答案。",
            "",
            f"来源：任务 {task_id or '未知'}（角色 {role or '未知'}"
            f"{f'，计划 {plan_id}' if plan_id else ''}），"
            f"队长提炼于 {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}。",
        ]
    )
    content = "\n".join(body_lines) + "\n"
    # 分级只看提炼出的做法步骤：注意事项是固定安全套话（里面有“删文件”
    # 字样），不对它分类，否则所有草稿都会误判为 D。
    grade = classify_draft("\n".join(steps))
    return {
        "name": name,
        "description": description,
        "content": content,
        "grade": grade,
        "role": role,
        "plan_id": plan_id,
        "task_id": task_id,
    }


def submit_draft(store: Any, draft: Dict[str, Any]) -> Dict[str, Any]:
    """草稿进技能库：来源 learned、默认关、状态 draft（聊天里调不到）。

    去重：同名草稿已在审核队列（status=draft）时直接返回已有的——
    做完 3 个同类任务只出 1 个草稿，不刷出 3 个重复项。
    """
    for record in store.list_records():
        metadata = record.get("metadata") or {}
        if (
            metadata.get("origin") == "skill_learn"
            and metadata.get("status") == "draft"
            and str(record.get("name") or "").casefold() == draft["name"].casefold()
        ):
            return record
    record = store.create(
        name=draft["name"],
        description=draft["description"],
        source="learned",
        content=draft["content"],
        enabled=False,
        fmt="agentskills",
        metadata={
            "origin": "skill_learn",
            "status": "draft",
            "grade": draft["grade"],
            "role": draft.get("role") or "",
            "plan_id": draft.get("plan_id") or "",
            "task_id": draft.get("task_id") or "",
            "learned_from": draft.get("task_id") or "",
        },
    )
    return record


def draft_from_task(
    store: Any,
    *,
    task_title: str,
    result_text: str,
    role: str = "",
    plan_id: str = "",
    task_id: str = "",
) -> Dict[str, Any]:
    """一次完成任务 → 提炼 → 分级 → 进审核队列（一站式）。"""
    draft = build_skill_draft(
        task_title=task_title,
        result_text=result_text,
        role=role,
        plan_id=plan_id,
        task_id=task_id,
    )
    return submit_draft(store, draft)


def approve_draft(store: Any, skill_id: str, approver: str) -> Dict[str, Any]:
    """Joseph 点启用：D 级永不上线；草稿才可批；批了才生效。"""
    record = store.get(skill_id)
    metadata = record.get("metadata") or {}
    if metadata.get("origin") != "skill_learn":
        raise ValueError("这条技能不是自写草稿，不走学习循环审批")
    if metadata.get("status") != "draft":
        raise ValueError(f"草稿已是 {metadata.get('status')} 状态，不能重复审批")
    if (metadata.get("grade") or "") == "D":
        raise ValueError(
            "这条草稿分级为 D（含危险做法），永不上线，不能启用"
        )
    store.set_enabled(skill_id, True)
    store.set_metadata(
        skill_id,
        {
            "status": "approved",
            "approved_by": approver,
            "approved_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        },
    )
    return store.get(skill_id)


def reject_draft(store: Any, skill_id: str, approver: str) -> Dict[str, Any]:
    """Joseph 点拒绝：草稿留痕（不删除，不断审计链），永不启用。"""
    record = store.get(skill_id)
    metadata = record.get("metadata") or {}
    if metadata.get("origin") != "skill_learn":
        raise ValueError("这条技能不是自写草稿，不走学习循环审批")
    if metadata.get("status") != "draft":
        raise ValueError(f"草稿已是 {metadata.get('status')} 状态，不能重复审批")
    store.set_enabled(skill_id, False)
    store.set_metadata(
        skill_id,
        {
            "status": "rejected",
            "approved_by": approver,
            "approved_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        },
    )
    return store.get(skill_id)


def list_drafts(store: Any) -> List[Dict[str, Any]]:
    """审核队列：全部自写草稿（待审核 + 已批 + 已拒，状态写在 metadata 里）。"""
    drafts = []
    for record in store.list_records():
        metadata = record.get("metadata") or {}
        if metadata.get("origin") == "skill_learn":
            drafts.append(record)
    drafts.sort(key=lambda r: str(r.get("created_at") or ""), reverse=True)
    return drafts


def teach_skill_to_bot(
    skill_store: Any,
    binding_store: Any,
    skill_id: str,
    target_bot: str,
    approver: str,
) -> Dict[str, Any]:
    """把一条已确认的本事教给指定 Bot（A3r F4：生效后能教给别的 Bot）。

    只认 Joseph 点过启用的草稿（``status=approved`` 且 ``enabled=True``）：
    没确认的本事不能教，D 级永不上线（批都批不了，见 :func:`approve_draft`）。
    教的过程是复制一条绑定（``SkillBindingStore.bind``，幂等），不是把
    草稿本身改名/挪走；来源记在技能 metadata 里（``taught_from`` /
    ``taught_to``），可追溯是谁教给谁、谁点的头。
    """
    record = skill_store.get(skill_id)
    metadata = record.get("metadata") or {}
    if metadata.get("origin") != "skill_learn":
        raise ValueError("这条技能不是自写草稿，不走学习循环")
    if metadata.get("status") != "approved":
        raise ValueError(
            "这条本事还没经过 Joseph 确认（现在是 "
            f"{metadata.get('status')} 状态），不能教给别的 Bot"
        )
    if not record.get("enabled"):
        raise ValueError("这条本事还没启用，不能教给别的 Bot")
    target = str(target_bot or "").strip()
    if not target:
        raise ValueError("要教给哪个 Bot 没写（target_bot 不能为空）")
    binding = binding_store.bind(
        skill_id=skill_id, employee=target, actor=str(approver or "")
    )
    taught_to = list(metadata.get("taught_to") or [])
    if target not in taught_to:
        taught_to.append(target)
    skill_store.set_metadata(
        skill_id,
        {
            "taught_from": skill_id,
            "taught_to": taught_to,
            "last_taught_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "last_taught_by": str(approver or ""),
        },
    )
    return {
        "skill_id": skill_id,
        "skill_name": record.get("name"),
        "target_bot": target,
        "binding": binding,
        "taught_to": taught_to,
    }
