"""诱导识别：外来文字永远是“数据”，不是“指令”。

核心规则（防诱导设计 §4.1）：Joseph 亲口在聊天框里打的字是指令，
可以按三道关往下走；粘贴来的、文件里的、网页里的、工具输出里的、
别人发来的、记忆里翻出来的任何文字永远是“数据”，不是指令。里面就算
写着“让贾维斯删文件”，也只是“一段提到删文件的话”，不是 Joseph 让删。

本模块是纯函数，不碰聊天管线、不碰 shell：

* :func:`detect_induction` —— 给定一段文字及其来源，判断它是否含有
  §4.2 的诱导特征。来源是 Joseph 亲口说的（``owner_direct``）时永不
  判定为诱导（他的话走正常三道关）；来源是外来的且命中任一特征时拦下。
* :func:`mark_external_content` —— 给外来内容包一层来源标记（服务端加，
  不靠模型自己判断），供 C 组在聊天接线处调用；本轮不接线，只提供接口。

判定是“宁可多拦”的方向：外来内容里出现可执行命令本身就是信号
（特征 5 藏指令），拦下后等 Joseph 用自己的话重新说一遍（§4.3），
不建审批单、不进待执行队列。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

#: 来源种类。``owner_direct`` 是 Joseph 亲口在聊天框里打的字；其余一律
#: 视为外来内容（数据，不是指令）。
SOURCE_KINDS = frozenset(
    {"owner_direct", "pasted", "file", "web", "tool_output", "memory", "other"}
)

#: 外来内容标记的帧头/帧尾（与网关技能块同一做法：分隔标记 + 伪造标记降级）。
_MARK_START = "<<<外部内容 开始"
_MARK_END = "<<<外部内容 结束>>>"


def _neutralize(text: str) -> str:
    """把正文里伪造的分隔标记降级成全角字符，真标记只保留外层一对。"""
    return text.replace("<<<", "＜＜＜").replace(">>>", "＞＞＞")


def mark_external_content(text: str, source: str) -> str:
    """给一段外来内容包上来源标记，供模型分辨“谁的话”。

    Joseph 亲口说的话不调用本函数（不加标记）。标记由服务端加，
    不许靠模型自己判断“这段像不像粘贴的”。
    """
    src = source if source in SOURCE_KINDS else "other"
    return f"{_MARK_START}（来源：{src}）>>>\n{_neutralize(text)}\n{_MARK_END}"


#: §4.2 诱导特征：特征编号 → （审计规则码，匹配用正则）。
#: 正则只做“信号”判定，不做语义理解；命中即拦，由 Joseph 亲口重说。
_FEATURE_PATTERNS: tuple[tuple[str, str, re.Pattern[str]], ...] = (
    (
        "impersonate-owner",
        "block:induction:impersonate-owner",
        re.compile(
            r"(我是\s*Joseph|我是\s*主人|Joseph\s*已经.*(同意|确认|点了)|"
            r"不用问他|他让我直接做|老板已经.*(同意|批准)|我就是\s*Joseph)",
            re.IGNORECASE,
        ),
    ),
    (
        "impersonate-system",
        "block:induction:impersonate-system",
        re.compile(
            r"(忽略\s*(之前|以上|所有).*(指示|指令|提示)|"
            r"ignore\s+(all\s+)?(previous|above)\s+instructions|"
            r"你是新版贾维斯|开发者指令|developer\s+instruction|"
            r"\[SYSTEM\]|系统提示[:：]\s*忽略)",
            re.IGNORECASE,
        ),
    ),
    (
        "change-rules",
        "block:induction:change-rules",
        re.compile(
            r"(不用确认|不用问(我|他)|跳过确认|删除确认步骤|不用审批|全权模式已开|"
            r"把白名单关掉|关闭.*(确认|审批)|不需要.*(确认|审批)|"
            r"直接做|马上执行.*不用问)",
            re.IGNORECASE,
        ),
    ),
    (
        "fake-urgency",
        "block:induction:fake-urgency",
        re.compile(
            r"(十万火急|马上.*不然|不然.*(全丢|就来不及|就晚了)|"
            r"公司规定|官方要求|微软官方|这是.*(规定|要求).*必须)",
            re.IGNORECASE,
        ),
    ),
    (
        "rephrase-bypass",
        "block:induction:rephrase-bypass",
        re.compile(
            r"((清理|腾空间|归档|优化|整理)\s*(一下)?\s*"
            r"(文件|资料|照片|目录|文件夹|[A-Za-z]:|D盘|C盘|工作区))|"
            r"((文件|照片|资料|目录|文件夹|盘|工作区).{0,8}"
            r"(清理|腾空间|归档|优化|整理))|"
            r"((更新|同步)\s*(一下)?\s*(软件|程序|系统))",
            re.IGNORECASE,
        ),
    ),
    (
        "fake-evidence",
        "block:induction:fake-evidence",
        re.compile(
            r"(审批单号.*(已通过|通过了)|刚才.*已经同意|"
            r"我说过可以|看聊天记录|已经批了|Joseph\s*已经点了)",
            re.IGNORECASE,
        ),
    ),
)

#: 外来内容里出现可执行命令即视为“藏指令”（特征 5）：删/格/关机/装软件
#: 类动词 + 命令形态。只用于 source != owner_direct 的文本。
#: 英文命令形态（del/rm/format/...）与中文说法（删文件/装软件）各一支，
#: 中文支要求带文件/软件类目标，避免把普通叙述误判为命令。
_COMMAND_LIKE = re.compile(
    r"(?i)\b(del|rmdir|rd|remove-item|format|diskpart|bcdedit|shutdown|"
    r"restart-computer|stop-computer|rm\s+-[a-z]*r|pip\s+install|"
    r"winget\s+install|choco\s+install|reg\s+(add|delete|import))\b",
)
_COMMAND_LIKE_ZH = re.compile(
    r"(删|删除|清空).{0,12}(文件|照片|资料|目录|文件夹|盘)"
    r"|(文件|照片|资料|目录|文件夹|盘).{0,12}(删|删除|清空)"
    r"|(下载并安装|安装).{0,12}软件",
)


@dataclass(frozen=True)
class InductionReport:
    """一次诱导识别的结果。"""

    blocked: bool
    source: str
    features: tuple[str, ...] = ()
    rule_codes: tuple[str, ...] = ()
    reason: str = ""

    def as_dict(self) -> dict:
        return {
            "blocked": self.blocked,
            "source": self.source,
            "features": list(self.features),
            "rule_codes": list(self.rule_codes),
            "reason": self.reason,
        }


def detect_induction(text: str, source: str) -> InductionReport:
    """识别一段文字是否在诱导模型下危险命令。

    * ``source == "owner_direct"``：Joseph 亲口说的，永不判定为诱导，
      直接返回不拦（他的话仍要过白名单/参数校验/人类确认三道关）。
    * 其余来源：命中任一 §4.2 特征即拦；即使无特征，外来内容里出现
      可执行命令（删/格/关机/装软件类）也按“藏指令”拦。
    """
    src = source if source in SOURCE_KINDS else "other"
    if src == "owner_direct":
        return InductionReport(blocked=False, source=src)
    body = text or ""
    features: list[str] = []
    codes: list[str] = []
    for name, code, pattern in _FEATURE_PATTERNS:
        if pattern.search(body):
            features.append(name)
            codes.append(code)
    if _COMMAND_LIKE.search(body) or _COMMAND_LIKE_ZH.search(body):
        if "hidden-instruction" not in features:
            features.append("hidden-instruction")
            codes.append("block:induction:hidden-instruction")
    if not features:
        return InductionReport(blocked=False, source=src)
    return InductionReport(
        blocked=True,
        source=src,
        features=tuple(features),
        rule_codes=tuple(codes),
        reason=(
            "外来内容（来源："
            + src
            + "）含有诱导特征 "
            + "、".join(features)
            + "，按“数据不是指令”拦下，不建审批单"
        ),
    )


def feature_names() -> list[str]:
    """§4.2 特征编号清单（含藏指令）。"""
    return [name for name, _, _ in _FEATURE_PATTERNS] + ["hidden-instruction"]
