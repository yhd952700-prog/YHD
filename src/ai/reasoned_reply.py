"""推理要强 + 反驳机制 + 能力上限自查 + 地基核对（A3r，后端）。

本模块是 A3r 组的集中实现，规则全部写在代码与
``docs/a3r-reasoning-rebuttal.md`` 里，不依赖任何“打分模型”——
难度判断、反驳触发、核实检查点都是确定性规则，可测、可复核。

内容：

1. **难度路由**（§1）：难问题走 Muse Wonder（预设已在 A2a 做，只做
   预设、不填 Key、不调真实接口），日常问题走 DeepSeek；没配好就走
   现有默认模型 + fallback（A2a），不编一个能用的模型。每次路由决定
   都记日志（走了哪个模型、为什么）。
2. **回答格式**（§2）：聊天回复的结构化字段（结论/理由/依据列表），
   没依据写“没找到依据”，不许编。前端（B 组）按字段渲染，见
   :func:`structure_reply` 与 ``docs/a3r-reasoning-rebuttal.md``。
3. **查资料先核实**（§3）：核实检查点在 ``src/ai/web_research.py`` 的
   :func:`answer_with_verification` 里（无来源→不说/标明），本模块只
   做结构化包装；``src/ai/collaboration.py`` 的 researcher 执行链路
   （``MultiAgentTeam.research``）走的就是它。
4. **反驳机制**（§4–§6）：触发条件可测（事实冲突 / 明显损失风险），
   触发时直说、给替代方案、等 Joseph 定（走现有审批/确认机制）；
   反驳记审计（``a3r.rebuttal``），群聊互驳只认真实冲突、不演假辩论。
5. **能力上限自查**（§7）：:func:`audit_capability_gaps` 扫技能/工具/
   模型/渠道，列出“其实能做却被关掉”的地方与没打开的原因。
6. **地基核对**（§8–§11）：F1 见 ``src/ai/vendor_presets.py``；
   F2 :func:`check_memory_export_portable`；F4 见
   ``src/ai/skill_learn.py::teach_skill_to_bot``；F6
   :func:`encryption_posture_report`。
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

logger = logging.getLogger("liuhao.ai.reasoned_reply")

#: 没找到依据时写进 evidence 的固定文案（前端 B 组按它渲染“没依据”态）。
NO_EVIDENCE_TEXT = "没找到依据"

#: 查不到时写进结论的固定前缀（老实说查不到，不编答案）。
NOT_FOUND_PREFIX = "查不到"

# --------------------------------------------------------------------- §1 难度
#: 明确要求深度思考的说法：出现即判“难”（不用看长度与其它词）。
_HARD_EXPLICIT = (
    "深度分析", "深入分析", "仔细分析", "认真分析", "详细分析",
    "好好想想", "认真想想", "深度思考", "深入思考",
    "详细推导", "仔细推导", "慢慢想", "想清楚再说",
)

#: 多步推理 / 对比 / 分析类关键词（规则判断，不假装有个打分模型）。
_HARD_KEYWORDS = (
    "多步", "分几步", "分步骤", "步骤", "先", "再",
    "对比", "比较", "分析", "为什么", "权衡", "利弊",
    "优缺点", "证明", "推导", "论证", "调研", "评估",
    "决策", "多方案", "几种方案", "哪个更好", "该选哪个",
)

#: 判“难”的长度线：长问题 + 一个推理词 = 难（短问题要两个推理词才算）。
_HARD_MIN_CHARS = 400


def classify_difficulty(text: str) -> Dict[str, Any]:
    """难度判断（确定性规则，不假装有个打分模型）。

    判“难”（``hard``）当且仅当满足其一：

    * 明确要求深度分析（见 ``_HARD_EXPLICIT``）；
    * 出现 ≥2 个不同的推理关键词（见 ``_HARD_KEYWORDS``）；
    * 长度 ≥400 字且出现 ≥1 个推理关键词。

    其余一律判“日常”（``daily``）。返回决定 + 命中的规则（可测、可复核）。
    """
    body = str(text or "")
    matched_explicit = [word for word in _HARD_EXPLICIT if word in body]
    matched_keywords = [word for word in _HARD_KEYWORDS if word in body]
    reasons: List[str] = []
    if matched_explicit:
        reasons.append(f"明确要求深度思考：{','.join(matched_explicit)}")
    if len(matched_keywords) >= 2:
        reasons.append(f"含多个推理关键词：{','.join(matched_keywords)}")
    if len(body) >= _HARD_MIN_CHARS and matched_keywords:
        reasons.append(
            f"问题较长（{len(body)} 字）且含推理关键词：{','.join(matched_keywords)}"
        )
    level = "hard" if reasons else "daily"
    if level == "daily":
        reasons.append("没命中难度规则，走日常模型")
    return {
        "level": level,
        "reasons": reasons,
        "matched_explicit": matched_explicit,
        "matched_keywords": matched_keywords,
        "length": len(body),
    }


#: 难度 → 目标模型（预设 id 与展示名；Key 由 Joseph 自己填，不在这里出现）。
_DIFFICULTY_TARGETS = {
    "hard": {
        "preset_id": "muse_wonder",
        "model_name": "muse-wonder",
        "label": "Muse Wonder（Meta Model API）",
        "why": "难问题走更强的模型",
    },
    "daily": {
        "preset_id": "deepseek",
        "model_name": "deepseek-chat",
        "label": "DeepSeek",
        "why": "日常问题走 DeepSeek",
    },
}


def _find_saved_model(store: Any, target: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """在模型管理已保存的记录里找目标模型；找不到返回 None（不编一条）。"""
    if store is None:
        return None
    try:
        records = store.list_models()
    except Exception as exc:  # noqa: BLE001 - 读不到配置时按“没配”处理
        logger.warning("difficulty routing: model store unreadable (%s)", type(exc).__name__)
        return None
    preset_id = str(target["preset_id"])
    model_name = str(target["model_name"])
    for record in records or []:
        if not isinstance(record, dict) or not record.get("enabled", True):
            continue
        name = str(record.get("name") or "")
        model = str(record.get("model") or "")
        record_id = str(record.get("id") or "")
        if preset_id == "muse_wonder":
            if (
                model == model_name
                or record_id == preset_id
                or "Muse Wonder" in name
                or "muse-wonder" in model.lower()
            ):
                return record
        else:
            from .model_config import vendor_for

            if vendor_for(record.get("provider"), record.get("base_url")) == "deepseek":
                return record
    return None


def route_difficulty(text: str, store: Any = None) -> Dict[str, Any]:
    """难度路由决定（只做决定、不调真实模型）。

    难的 → Muse Wonder 预设；日常的 → DeepSeek；目标模型在模型管理里
    没保存（没配 Key/没建记录）时 ``model_id`` 为 None，调用方走现有
    默认模型 + fallback（A2a），绝不编一个能用的模型。每次决定都记日志。
    """
    decision = classify_difficulty(text)
    target = _DIFFICULTY_TARGETS[decision["level"]]
    record = _find_saved_model(store, target)
    if record is not None:
        model_id = str(record.get("id") or "")
        note = f"已按难度路由到{target['label']}（模型与 Key 里已保存）"
    else:
        model_id = None
        note = (
            f"{target['label']}还没配好（模型与 Key 里没有这条记录），"
            "走默认模型与现有 fallback，不编一个能用的模型"
        )
    result = {
        "level": decision["level"],
        "reasons": decision["reasons"],
        "target_preset": target["preset_id"],
        "target_model": target["model_name"],
        "target_label": target["label"],
        "model_id": model_id,
        "note": note,
    }
    logger.info(
        "difficulty routing: level=%s target=%s model_id=%s reasons=%s",
        result["level"],
        target["label"],
        model_id or "-",
        "；".join(decision["reasons"]),
    )
    return result


def provider_for_difficulty(text: str, *, store: Any = None, **kwargs: Any):
    """按难度取 provider（路由失败时回落到默认聊天链，不抛错、不编模型）。

    ``store`` 不传时用默认模型配置中心。目标模型解析失败（如 Key 归属
    不对）时记日志并走 ``provider_for_task("chat")``（A2a 的 fallback
    链仍在里面），绝不因为“难”就绕过 Key 归属与审批。
    """
    from .model_router import provider_for_task

    decision = route_difficulty(text, store=store)
    if decision["model_id"]:
        try:
            return provider_for_task("chat", model_id=decision["model_id"], **kwargs)
        except Exception as exc:  # noqa: BLE001 - 路由失败走默认链
            logger.warning(
                "difficulty routing: target model %s unusable (%s); "
                "falling back to default chat chain",
                decision["model_id"],
                type(exc).__name__,
            )
    return provider_for_task("chat", **kwargs)


# ------------------------------------------------------------------- §2 回答格式
@dataclass
class StructuredReply:
    """聊天回复的结构化字段（B 组前端按它渲染）。

    * ``conclusion``：结论（一句话先说结果）；
    * ``reasons``：理由（为什么是这个结论，可为空）；
    * ``evidence``：依据列表（来源 URL/出处）；**没依据时只写
      ``["没找到依据"]``，不许编一条来源**。
    """

    conclusion: str
    reasons: List[str] = field(default_factory=list)
    evidence: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "conclusion": self.conclusion,
            "reasons": list(self.reasons),
            "evidence": list(self.evidence) if self.evidence else [NO_EVIDENCE_TEXT],
        }


_SECTION_HEADS = ("结论", "理由", "依据", "来源", "证据")


def _clean_section_line(line: str) -> str:
    text = re.sub(r"^\s*(?:\d+[.、)]|[-*•])\s*", "", line.strip())
    return text.strip()


def structure_reply(text: str, sources: Optional[Sequence[Any]] = None) -> StructuredReply:
    """把一段聊天回复拆成结构化字段（结论/理由/依据）。

    回复里已经写了“结论/理由/依据”小节时按小节拆；没写时整段当结论，
    理由为空，依据取 ``sources``（知识库/检索来源）——没有来源就写
    “没找到依据”，绝不从正文里猜一条来源出来。
    """
    body = str(text or "").strip()
    if not body:
        return StructuredReply(conclusion="没得到回复", evidence=[NO_EVIDENCE_TEXT])
    sections: Dict[str, List[str]] = {name: [] for name in _SECTION_HEADS}
    current: Optional[str] = None
    plain: List[str] = []
    for raw in body.splitlines():
        line = raw.strip()
        if not line:
            continue
        head = re.match(r"^(结论|理由|依据|来源|证据)\s*[：:]\s*(.*)$", line)
        if head:
            current = head.group(1)
            rest = head.group(2).strip()
            if rest:
                sections[current].append(_clean_section_line(rest))
            continue
        if current:
            sections[current].append(_clean_section_line(line))
        else:
            plain.append(line)
    evidence: List[str] = []
    for key in ("依据", "来源", "证据"):
        evidence.extend(sections[key])
    if sources:
        for item in sources:
            if isinstance(item, dict):
                url = str(item.get("url") or item.get("title") or "").strip()
            else:
                url = str(item or "").strip()
            if url and url not in evidence:
                evidence.append(url)
    conclusion_lines = sections["结论"] or plain
    conclusion = " ".join(conclusion_lines).strip() or body.splitlines()[0].strip()
    reasons = [_clean_section_line(line) for line in sections["理由"] if line]
    return StructuredReply(
        conclusion=conclusion,
        reasons=reasons,
        evidence=evidence or [NO_EVIDENCE_TEXT],
    )


# ----------------------------------------------------------------- §4–§5 反驳
#: 已核实的事实：subject（说的是什么）+ value（核实的值）+ source（出处）。
#: 只认这种结构化事实——自由文本的深层语义冲突需要模型判断，本函数
#: 不假装能读懂一切（见 docs/a3r-reasoning-rebuttal.md）。
Fact = Dict[str, Any]

#: 删重要东西的说法：删/清空/格式化 + 重要/全部/所有/数据库/代码/备份。
_DELETE_RE = re.compile(
    r"(删|删除|清空|格式化|format)\s*(掉|除|了)?\s*"
    r"(重要|全部|所有|整个|数据库|代码|备份|.*文件|.*目录|.*文件夹)?"
)
_DELETE_SCOPE_RE = re.compile(r"(删|删除|清空|格式化).{0,12}(重要|全部|所有|数据库|代码库|备份)")

#: 金额：数字 + 元/块/万（只认阿拉伯数字，不猜中文大写金额）。
_MONEY_RE = re.compile(r"(\d[\d,]*(?:\.\d+)?)\s*(元|块|万元|万)")


def assess_risk(
    user_text: str,
    *,
    budget: Optional[float] = None,
    known_bad_paths: Optional[Sequence[Dict[str, Any]]] = None,
) -> List[Dict[str, Any]]:
    """风险评估（规则级，只认三类显式信号，不猜）：

    * ``delete_important``：要删重要/全部的东西；
    * ``overspend``：提到的金额超过给定的预算（``budget`` 不传时不判）；
    * ``known_wrong_path``：撞上调用方给定的已知错路清单（清单由调用方
      维护，本函数不自己编“错路”）。
    """
    text = str(user_text or "")
    flags: List[Dict[str, Any]] = []
    if _DELETE_SCOPE_RE.search(text) or (
        _DELETE_RE.search(text) and re.search(r"(重要|全部|所有|数据库|备份)", text)
    ):
        flags.append(
            {
                "kind": "delete_important",
                "detail": "要删重要/全部的东西（删文件、删数据库、清空都是大事）",
                "alternative": "先备份再删，或者先删一份不重要的试试，确认没问题再继续",
            }
        )
    if budget is not None:
        for match in _MONEY_RE.finditer(text):
            amount = float(match.group(1).replace(",", ""))
            if match.group(2) in ("万元", "万"):
                amount *= 10000
            if amount > float(budget):
                flags.append(
                    {
                        "kind": "overspend",
                        "detail": f"提到的 {match.group(0)} 超过预算 {budget:g} 元",
                        "alternative": f"先控制在预算 {budget:g} 元以内，超出的部分另外再定",
                    }
                )
                break
    for entry in known_bad_paths or []:
        pattern = str((entry or {}).get("pattern") or "")
        if pattern and pattern in text:
            flags.append(
                {
                    "kind": "known_wrong_path",
                    "detail": str(entry.get("why") or f"“{pattern}”是已知的错路"),
                    "alternative": str(entry.get("alternative") or "换一条路走"),
                }
            )
    return flags


def check_fact_conflict(user_text: str, facts: Sequence[Fact]) -> Optional[Dict[str, Any]]:
    """事实冲突检查（只认结构化事实 ``subject + value + source``）：

    * 数字对不上：事实里有数字、用户说法里对同一 subject 说了另一个数字；
    * 否定对不上：事实肯定某事存在/是某值，用户说“不是/没有/不对”。

    没命中返回 None（不触发）。自由文本的深层语义冲突不在此列——
    需要模型判断时本函数不判、不假装判了。
    """
    text = str(user_text or "")
    for fact in facts or []:
        subject = str((fact or {}).get("subject") or "").strip()
        value = str((fact or {}).get("value") or "").strip()
        source = str((fact or {}).get("source") or "").strip()
        if not subject or subject not in text:
            continue
        numbers_in_value = re.findall(r"\d[\d,]*(?:\.\d+)?", value)
        numbers_in_text = re.findall(r"\d[\d,]*(?:\.\d+)?", text)
        if numbers_in_value and numbers_in_text:
            if set(numbers_in_value).isdisjoint(set(numbers_in_text)):
                return {
                    "subject": subject,
                    "fact_value": value,
                    "user_value": numbers_in_text[0],
                    "source": source or "（没写出处）",
                    "detail": f"关于“{subject}”，已核实的是 {value}，你说的数字是 {numbers_in_text[0]}，对不上",
                }
        if re.search(rf"(不是|没有|不对|不该|没)\s*.{{0,6}}{re.escape(subject)}", text) or re.search(
            rf"{re.escape(subject)}.{{0,6}}(不是|没有|不对|不存在)", text
        ):
            return {
                "subject": subject,
                "fact_value": value,
                "user_value": "否定",
                "source": source or "（没写出处）",
                "detail": f"关于“{subject}”，已核实的是 {value}，你说的跟它反着",
            }
    return None


@dataclass
class RebuttalDecision:
    """一次反驳判定：触发与否、触发条件、怎么说、替代方案。"""

    triggered: bool
    condition: str = ""  # "fact_conflict" / "risk" / ""
    explanation: str = ""
    reasons: List[str] = field(default_factory=list)
    evidence: List[str] = field(default_factory=list)
    alternative: str = ""
    risk_flags: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "triggered": self.triggered,
            "condition": self.condition,
            "explanation": self.explanation,
            "reasons": list(self.reasons),
            "evidence": list(self.evidence),
            "alternative": self.alternative,
            "risk_flags": list(self.risk_flags),
        }


def evaluate_rebuttal(
    user_text: str,
    *,
    facts: Optional[Sequence[Fact]] = None,
    budget: Optional[float] = None,
    known_bad_paths: Optional[Sequence[Dict[str, Any]]] = None,
) -> RebuttalDecision:
    """反驳判定：两个条件满足其一就触发，都不满足不触发、不没事反驳。

    (a) Joseph 的说法和已核实的事实/数据冲突；
    (b) 他的决定按现有信息推断会带来明显损失、风险或走弯路
        （删重要文件、花超预算、走已知的错路）。
    """
    text = str(user_text or "")
    conflict = check_fact_conflict(text, facts or [])
    if conflict is not None:
        return RebuttalDecision(
            triggered=True,
            condition="fact_conflict",
            explanation=conflict["detail"],
            reasons=[f"已核实的事实是：{conflict['subject']}是 {conflict['fact_value']}"],
            evidence=[conflict["source"]] if conflict["source"] != "（没写出处）" else [NO_EVIDENCE_TEXT],
            alternative=f"按已核实的数据来：{conflict['subject']}是 {conflict['fact_value']}，你看这样行不行",
        )
    flags = assess_risk(text, budget=budget, known_bad_paths=known_bad_paths)
    if flags:
        first = flags[0]
        return RebuttalDecision(
            triggered=True,
            condition="risk",
            explanation=first["detail"],
            reasons=[first["detail"]],
            evidence=[NO_EVIDENCE_TEXT],
            alternative=first["alternative"],
            risk_flags=flags,
        )
    return RebuttalDecision(triggered=False)


def format_rebuttal(decision: RebuttalDecision) -> str:
    """反驳时怎么说：直说“这不对，因为……”，再给替代方案，等他定。

    语气直接但客气，就事论事，不抬杠、不说教，只针对事不针对人。
    没触发时返回空串（调用方不要对着没触发的判定硬凑一句反驳）。
    """
    if not decision.triggered:
        return ""
    lines = [f"这不对，因为{decision.explanation}。"]
    if decision.reasons:
        lines.append("理由：" + "；".join(decision.reasons) + "。")
    if decision.evidence and decision.evidence != [NO_EVIDENCE_TEXT]:
        lines.append("依据：" + "；".join(decision.evidence) + "。")
    else:
        lines.append("依据：没找到依据。")
    if decision.alternative:
        lines.append(f"替代方案：{decision.alternative}。")
    lines.append("你定：这样改行不行？等你点头我再继续。")
    return "\n".join(lines)


def record_rebuttal(
    decision: RebuttalDecision,
    *,
    actor: str = "jarvis",
    session_id: str = "",
    user_text: str = "",
) -> Dict[str, Any]:
    """反驳记录可查：写进能力层审计（``a3r.rebuttal``），失败也不阻断对话。

    返回记录本身（含 ``audited``：审计写没写进去）。
    """
    record = {
        **decision.to_dict(),
        "actor": actor,
        "session_id": session_id,
        "user_text": str(user_text or "")[:300],
        "recorded_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    audited = False
    try:
        from .audit import emit

        audited = bool(
            emit(
                "a3r.rebuttal",
                module="src.ai.reasoned_reply",
                outcome="success",
                reason=decision.explanation[:200],
                actor=actor,
                condition=decision.condition,
                triggered=decision.triggered,
                session_id=session_id,
            )
        )
    except Exception as exc:  # noqa: BLE001 - 审计失败只记日志，不阻断
        logger.warning("rebuttal audit failed: %s", type(exc).__name__)
    record["audited"] = audited
    return record


# ------------------------------------------------------------------- §6 群聊互驳
def summarize_group_discussion(statements: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """群聊讨论汇总：Bot 们能互相反驳，最后贾维斯汇总。

    ``statements``：每条 ``{bot, claim, subject?, value?, sources?}``。
    只认真实冲突，不演假辩论：

    * 同一 subject、值不同，且只有一方有来源 → 有来源的一方反驳没来源的；
    * 同一 subject、值不同，双方都有来源或都没来源 → 记为“未定论的冲突”，
      不判谁赢，等 Joseph 定；
    * 没有冲突 → ``rebuttals`` 为空，结论写明“没有真实冲突，没有互相反驳”。
    """
    items = [dict(item) for item in (statements or []) if isinstance(item, dict)]
    rebuttals: List[Dict[str, Any]] = []
    unresolved: List[Dict[str, Any]] = []
    for i, first in enumerate(items):
        subject = str(first.get("subject") or "").strip()
        if not subject:
            continue
        for second in items[i + 1:]:
            if str(second.get("subject") or "").strip() != subject:
                continue
            value_a = str(first.get("value") or first.get("claim") or "").strip()
            value_b = str(second.get("value") or second.get("claim") or "").strip()
            if not value_a or not value_b or value_a == value_b:
                continue
            sources_a = list(first.get("sources") or [])
            sources_b = list(second.get("sources") or [])
            detail = (
                f"关于“{subject}”，{first.get('bot')} 说是 {value_a}，"
                f"{second.get('bot')} 说是 {value_b}，对不上"
            )
            if bool(sources_a) != bool(sources_b):
                winner = first if sources_a else second
                loser = second if sources_a else first
                rebuttals.append(
                    {
                        "from_bot": str(winner.get("bot")),
                        "to_bot": str(loser.get("bot")),
                        "subject": subject,
                        "reason": detail + f"；{winner.get('bot')} 有来源，{loser.get('bot')} 没来源",
                        "winner_value": str(winner.get("value") or winner.get("claim")),
                        "sources": list(winner.get("sources") or []),
                    }
                )
            else:
                unresolved.append(
                    {
                        "bots": [str(first.get("bot")), str(second.get("bot"))],
                        "subject": subject,
                        "reason": detail + "；双方都有来源或都没来源，无法判定谁对",
                    }
                )
    if rebuttals:
        conclusion = "；".join(
            f"{item['from_bot']} 反驳了 {item['to_bot']}（{item['subject']}：以{item['winner_value']}为准）"
            for item in rebuttals
        )
    elif unresolved:
        conclusion = "有冲突但没法定论，等 Joseph 定：" + "；".join(
            f"{'和'.join(item['bots'])}在{item['subject']}上说法不同"
            for item in unresolved
        )
    else:
        conclusion = "没有真实冲突，没有互相反驳。"
    return {
        "rebuttals": rebuttals,
        "unresolved": unresolved,
        "conclusion": conclusion,
        "summary_text": (
            "群聊汇总："
            + conclusion
            + (f"（未定论 {len(unresolved)} 处）" if unresolved else "")
        ),
    }


# ------------------------------------------------------------- §7 能力上限自查
#: 静态扫描里判定“碰危险面”的信号（只做初步分类，不是安全证明）。
_DANGEROUS_TOKENS = (
    "socket", "requests", "urllib", "subprocess", "os.system",
    "eval(", "exec(", "open(", "shutil", "__import__",
)


def _scan_plugin_dir(plugins_dir: Path) -> List[Dict[str, Any]]:
    """扫 ``plugins/`` 下每个插件目录：纯计算候选 vs 因故没接线。"""
    found: List[Dict[str, Any]] = []
    if not plugins_dir.is_dir():
        return found
    for entry in sorted(plugins_dir.iterdir()):
        if not entry.is_dir() or entry.name.startswith((".", "_")):
            continue
        plugin_files = sorted(entry.glob("*_plugin.py")) + sorted(entry.glob("*_tool_plugin.py"))
        if not plugin_files:
            continue
        hits: List[str] = []
        for plugin_file in plugin_files:
            try:
                source = plugin_file.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            for token in _DANGEROUS_TOKENS:
                if token in source:
                    hits.append(token)
        found.append(
            {
                "id": entry.name,
                "danger_signals": sorted(set(hits)),
                "candidate": not hits,
            }
        )
    return found


def audit_capability_gaps(*, plugins_dir: Optional[str] = None) -> Dict[str, Any]:
    """扫一遍技能/工具/模型/渠道，有没有“其实能做却被关掉”的地方。

    扫四处（只读，不改任何开关）：

    a. 插件聊天工具目录 vs 开关分档表：已接线的工具有没有没登记分档的；
    b. 角色工具 allowlist vs 真能构造出的工具 capability：角色表里写了、
       实际造不出来的（接线断了）；
    c. 厂商预设表 vs 协议适配器：预设的 provider 有没有对应的适配器；
    d. ``plugins/`` 全量静态扫描：纯计算候选 vs 带危险信号（联网/写文件/
       跑命令）所以没接线的。

    返回扫描结果。**本轮打开了的**（见模块 docstring 与交卷）：
    ``uuid`` / ``roman_numeral`` / ``slug`` 三个纯计算插件已接进聊天工具
    （A 档默认开）；其余没打开的原因见 ``not_opened``。
    """
    from ..gateway.chat_tool_settings import CHAT_TOOL_GRADES
    from .tools.plugin_tools import PLUGIN_TOOL_CATALOG, build_plugin_tools
    from .vendor_presets import validate_vendor_presets
    from .collaboration import ROLE_TOOL_ALLOWLIST

    catalog_ids = [str(entry.get("id") or "") for entry in PLUGIN_TOOL_CATALOG]
    available = [entry for entry in PLUGIN_TOOL_CATALOG if entry.get("available")]
    available_ids = [str(entry.get("id") or "") for entry in available]
    # 分档表只管“能造出来的工具”的开关：没接线的条目（如 qrcode，
    # available=False、没有 Tool 对象）本来就没有开关可分档，单列出来，
    # 不算缺失。
    missing_grades = [tool_id for tool_id in available_ids if tool_id not in CHAT_TOOL_GRADES]
    ungraded_unavailable = [
        str(entry.get("id"))
        for entry in PLUGIN_TOOL_CATALOG
        if not entry.get("available") and str(entry.get("id") or "") not in CHAT_TOOL_GRADES
    ]
    default_off = [
        str(entry.get("id"))
        for entry in available
        if not CHAT_TOOL_GRADES.get(str(entry.get("id")), {}).get("default_enabled", False)
    ]

    constructible: set = set()
    try:
        for tool in build_plugin_tools():
            constructible.add(str(getattr(tool, "capability", "") or ""))
        from .tools.kernel_tools import make_tools

        # 真实聊天路径（src/ai/liuhao.py）传 status_fn，system.status 只有
        # 那时才造得出来；自查必须镜像真实路径，不能按无 status_fn 的
        # 列表路径判它“断裂”。
        for tool in make_tools(
            principal="user-capability-audit",
            status_fn=lambda: {},
            include_disabled=True,
        ):
            constructible.add(str(getattr(tool, "capability", "") or ""))
    except Exception as exc:  # noqa: BLE001 - 构造失败按“扫不到”如实记
        logger.warning("capability audit: tool construction failed (%s)", type(exc).__name__)
    allowlist_broken: List[Dict[str, Any]] = []
    for role, allowed in ROLE_TOOL_ALLOWLIST.items():
        if allowed is None:
            continue
        for capability in sorted(allowed):
            if capability not in constructible:
                allowlist_broken.append({"role": role, "capability": capability})

    preset_problems = validate_vendor_presets()

    repo_root = Path(__file__).resolve().parents[2]
    plugins = _scan_plugin_dir(Path(plugins_dir) if plugins_dir else repo_root / "plugins")
    wired_ids = {str(entry.get("id") or "") for entry in PLUGIN_TOOL_CATALOG if entry.get("available")}
    not_opened: List[Dict[str, Any]] = []
    for plugin in plugins:
        tool_id = f"liuhao.plugin.{plugin['id']}"
        if tool_id in wired_ids:
            continue
        if plugin["candidate"]:
            reason = "纯计算候选，本轮没接线（没逐个核过方法签名与输出形态，不硬接）"
        else:
            reason = "带危险信号（联网/写文件/跑命令/读文件），按既定口径不接进聊天工具"
        not_opened.append(
            {"id": plugin["id"], "reason": reason, "danger_signals": plugin["danger_signals"]}
        )

    return {
        "checked": {
            "plugin_catalog_entries": len(catalog_ids),
            "plugin_catalog_available": len(available),
            "plugins_scanned": len(plugins),
            "plugins_pure_candidates": sum(1 for item in plugins if item["candidate"]),
        },
        "missing_grades": missing_grades,
        "ungraded_unavailable": ungraded_unavailable,
        "default_off_by_design": default_off,
        "allowlist_broken": allowlist_broken,
        "preset_problems": preset_problems,
        "opened_this_round": ["liuhao.plugin.uuid", "liuhao.plugin.roman_numeral", "liuhao.plugin.slug"],
        "not_opened": not_opened,
        "note": (
            "B/C 档默认关是既定安全分档（Owner 在技能页开了才进聊天），"
            "不是“被关掉的本事”，本轮不改默认档；底线（花钱、动账号和 Key、"
            "删文件、动系统大事先问；禁区不动；账号安全红线）一律不动。"
        ),
    }


# ----------------------------------------------------------------- §8 F2 记忆不断
def check_memory_export_portable(sample_entries: Optional[Sequence[Dict[str, Any]]] = None) -> Dict[str, Any]:
    """核对记忆独立导出入口：导出是不是通用格式（不依赖贾维斯自有格式）。

    查两处真实存在的导出格式，并用 ``parse_memory_import`` 做回导验证：

    * 搬家包 ``memories.json``：``{"format": "liuhao.memory.export.v1",
      "entries": [{"text", "source", "tags"}]}``（``src/gateway/data_portability.py``）；
    * CLI ``memory export``：``{"format": "liuhao-memory-v1",
      "memories": [{"content", "type", ...}]}``（``src/cli/memory_commands.py``）。

    两者都是明文 JSON、字段名通用（text/content、source、tags），
    不依赖贾维斯自有二进制格式；回导用同一套解析器，丢了字段会报数。
    """
    from ..gateway.a2a import parse_memory_import

    entries = list(sample_entries) if sample_entries else [
        {"text": "Joseph 喜欢简约的操作台", "source": "test", "tags": ["偏好"]},
        {"text": "贾维斯是 Joseph 的工作台", "source": "test", "tags": []},
    ]
    import json as _json

    results: Dict[str, Any] = {"formats": {}, "generic": True, "notes": []}
    # 格式一：搬家包 memories.json。
    payload_v1 = _json.dumps(
        {"format": "liuhao.memory.export.v1", "entries": entries}, ensure_ascii=False
    ).encode("utf-8")
    parsed, fmt, errors = parse_memory_import(payload_v1, "memories.json")
    results["formats"]["liuhao.memory.export.v1"] = {
        "round_trips": len(parsed),
        "expected": len(entries),
        "format_detected": fmt,
        "errors": errors,
        "ok": len(parsed) == len(entries) and not errors,
    }
    # 格式二：CLI memory export（content 别名）。
    cli_entries = [
        {"content": item["text"], "type": "fact", "category": "general",
         "tags": item.get("tags") or []}
        for item in entries
    ]
    payload_cli = _json.dumps(
        {"format": "liuhao-memory-v1", "memories": cli_entries}, ensure_ascii=False
    ).encode("utf-8")
    parsed_cli, fmt_cli, errors_cli = parse_memory_import(payload_cli, "export.json")
    results["formats"]["liuhao-memory-v1"] = {
        "round_trips": len(parsed_cli),
        "expected": len(entries),
        "format_detected": fmt_cli,
        "errors": errors_cli,
        "ok": len(parsed_cli) == len(entries) and not errors_cli,
    }
    results["generic"] = all(item["ok"] for item in results["formats"].values())
    if not results["generic"]:
        results["notes"].append("有格式回导失败，见各格式的 errors（不静默跳过）")
    else:
        results["notes"].append(
            "两种导出格式都是明文 JSON 通用字段，可被其它程序读取与回导；"
            "不依赖贾维斯自有格式。另有 Markdown 导入（“- 内容”每行一条）。"
        )
    return results


# ------------------------------------------------------------------ §11 F6 数据是你的
#: 存量数据的加密核对：逐个文件数有没有调加密（只数，不猜）。
_ENCRYPTION_CHECK_FILES = (
    ("src/ai/conversation_store.py", "聊天记录"),
    ("src/kernels/memory/store.py", "记忆内核"),
    ("src/gateway/account_memory.py", "账号记忆"),
    ("src/ai/knowledge/memory.py", "知识库记忆"),
    ("src/backup/backup_manager.py", "备份"),
    ("src/backup/auto_backup.py", "自动备份"),
)


def encryption_posture_report(*, repo_root: Optional[str] = None) -> Dict[str, Any]:
    """本地数据加密现状核对（只报查到的，不编）：

    * 模型 Key：``ModelConfigStore.encryption_posture()`` 实测（AES-256-GCM）；
    * 备份：源码里数 ``AesGcmCipher`` / ``AES-256-GCM`` 的调用；
    * 其余存量（聊天记录、记忆、账号数据）：源码里数有没有调加密，
      没调就如实写“明文落盘”，不硬说已加密。
    """
    root = Path(repo_root) if repo_root else Path(__file__).resolve().parents[2]
    items: List[Dict[str, Any]] = []
    try:
        from .model_config import ModelConfigStore

        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            posture = ModelConfigStore(
                db_path=str(Path(tmp) / "model_registry.sqlite3"),
                key_path=str(Path(tmp) / "model_config.key"),
            ).encryption_posture()
        items.append(
            {
                "area": "模型 Key",
                "encrypted": True,
                "algorithm": str(posture.get("algorithm") or ""),
                "evidence": f"ModelConfigStore.encryption_posture() 实测：{posture}",
                "note": "AES-256-GCM，AAD 绑定配置 id；密钥文件本机绑定。",
            }
        )
    except Exception as exc:  # noqa: BLE001 - 测不到就如实写测不到
        items.append(
            {"area": "模型 Key", "encrypted": None,
             "evidence": f"实测失败：{type(exc).__name__}", "note": "没测到，不宣称已加密。"}
        )
    for relative, area in _ENCRYPTION_CHECK_FILES:
        path = root / relative
        try:
            source = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            items.append(
                {"area": area, "encrypted": None,
                 "evidence": f"读不到 {relative}", "note": "没读到，不宣称。"}
            )
            continue
        uses_cipher = ("AesGcmCipher" in source) or ("AES-256-GCM" in source)
        calls_encrypt = bool(re.search(r"\bencrypt\s*\(", source))
        if area.startswith("备份"):
            items.append(
                {
                    "area": area,
                    "encrypted": bool(uses_cipher),
                    "evidence": (
                        f"{relative} 含 AesGcmCipher/AES-256-GCM：{uses_cipher}"
                    ),
                    "note": (
                        "备份包用 AES-256-GCM 加密（已有）。"
                        if uses_cipher else
                        "备份文件里没数到加密调用，如实写没数到。"
                    ),
                }
            )
        else:
            items.append(
                {
                    "area": area,
                    "encrypted": bool(uses_cipher or calls_encrypt),
                    "evidence": (
                        f"{relative} 含加密调用：{uses_cipher or calls_encrypt}"
                        "（AesGcmCipher/AES-256-GCM 或 encrypt()）"
                    ),
                    "note": (
                        "已加密。"
                        if (uses_cipher or calls_encrypt) else
                        "缺口：明文落盘（SQLite/JSON），没接加密。"
                        "本轮没补——给存量聊天/记忆加静态加密要动密钥管理"
                        "与迁移，不是只接一行加密调用的事，硬做会丢数据；"
                        "只用现有机制接线也接不上，写进报告，不硬做。"
                    ),
                }
            )
    return {
        "items": items,
        "summary": (
            "已加密：模型 Key、备份包（AES-256-GCM）；"
            "缺口：聊天记录、记忆、账号数据明文落盘，未补（原因见各条 note）。"
        ),
    }


__all__ = [
    "NO_EVIDENCE_TEXT",
    "NOT_FOUND_PREFIX",
    "RebuttalDecision",
    "StructuredReply",
    "assess_risk",
    "audit_capability_gaps",
    "check_fact_conflict",
    "check_memory_export_portable",
    "classify_difficulty",
    "encryption_posture_report",
    "evaluate_rebuttal",
    "format_rebuttal",
    "provider_for_difficulty",
    "record_rebuttal",
    "route_difficulty",
    "structure_reply",
    "summarize_group_discussion",
]
