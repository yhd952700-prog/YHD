"""明文凭据的正向扫描器（诊断包导出闸 / 发布闸 / 验收脚本共用）。

为什么单独一层、且**不复用** ``redaction.leak_violations``
---------------------------------------------------------
2026-11-08 的泄漏审计里出现过"自检与脱敏共漏"：脱敏正则漏了 ``key``，自检因为
复用同一套特征也跟着漏。这里的规则表是一份独立实现，专门给脱敏链路提供第二意见：

* 诊断包在**写 zip 之前**用它再扫一遍，命中即拒绝导出；
* 发布闸与 ``scripts/acceptance/log_leak_scan.py`` 用同一份规则扫落盘证据；
* 已经脱敏成 ``***REDACTED***`` 的占位符不算命中，但**占位符后面残留的尾巴**
  （``Bearer sk-***REDACTED***abc123``）必须算命中——否则"脱敏不彻底"会被
  占位符骗过去。

纯标准库、无项目内依赖，便于打包进安装目录后单独运行。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

#: 这些取值是"状态词"而不是凭据（``key=configured`` 在日志里大量出现）。
#: 只放明确的非秘密取值；像 ``ABCDEF`` 这种看起来像 Key 的一律保留为命中。
BENIGN_VALUES = frozenset(
    {
        "configured",
        "unconfigured",
        "none",
        "unset",
        "missing",
        "present",
        "required",
        "optional",
        "redacted",
        "masked",
        "hidden",
        "true",
        "false",
        "null",
        "none",
        "mock",
        "test",
        "demo",
        "default",
        "env",
        "file",
        "auto",
        "unknown",
        "not_set",
        "notset",
        "set",
        "yes",
        "no",
        "n/a",
        "disabled",
        "enabled",
        "absent",
        "available",
        "unavailable",
        "已配置",
        "未配置",
        "无",
        "未知",
    }
)


@dataclass(frozen=True)
class Rule:
    """一条正向特征；``value_group`` 非空时，捕获值要再过一遍良性词过滤。"""

    label: str
    pattern: re.Pattern[str]
    value_group: int | None = None


#: 规则顺序即输出顺序。注释里的"为什么"是给后来人看的，别随手删。
RULES: tuple[Rule, ...] = (
    # 脱敏残留：占位符后面还跟着 4 个以上凭据字符，说明只遮了一半。
    Rule(
        "脱敏残留",
        re.compile(r"(?i)\*\*\*REDACTED\*\*\*[A-Za-z0-9._~+/=-]{4,}"),
    ),
    # OpenAI / DeepSeek 风格。``sk-***`` 交给"脱敏残留"判断，这里不重复。
    Rule("sk 明文", re.compile(r"(?i)(?<![A-Za-z0-9_])sk-(?!\*\*\*)[A-Za-z0-9_-]{8,}")),
    # 裸 Bearer（错误体回显、SDK 异常原文最常见）。
    Rule(
        "Bearer 明文",
        re.compile(r"(?i)\bbearer\s+(?!\*\*\*)[A-Za-z0-9._~+/=-]{8,}"),
    ),
    # 带 Authorization 头的完整凭据。
    Rule(
        "Authorization 头",
        re.compile(
            r"(?i)\b(?:authorization|proxy-authorization)\s*:\s*"
            r"(?:basic|bearer|digest|token)\s+(?!\*\*\*)[A-Za-z0-9._~+/=:-]{6,}"
        ),
    ),
    # URL query 里的裸 key（审计 F2 的核心漏点）。
    Rule(
        "?key= 明文",
        re.compile(r"(?i)[?&]key=(?!\*\*\*)([^\s&\"']{4,})"),
        value_group=1,
    ),
    # JSON 字段里的裸 key。
    Rule(
        '"key": 明文',
        re.compile(r'(?i)"key"\s*:\s*"(?!\*\*\*)([^"]{4,})"'),
        value_group=1,
    ),
    # 形如 key=... 的 KV（日志 / 配置文件）。``?key=``/``&key=`` 由上面的规则接管。
    Rule(
        "key= 明文",
        re.compile(
            r"(?i)(?<![A-Za-z0-9_?&])key\s*=\s*(?!\*\*\*)([^\s&,;\"'）)]{4,})"
        ),
        value_group=1,
    ),
    # 带前缀的 key 字段（api_key / access_key / azure_key ...）。
    Rule(
        "带前缀 key 明文",
        re.compile(
            r"(?i)\b(?:api[_-]?key|access[_-]?key|secret[_-]?key|azure[_-]?key|"
            r"subscription[_-]?key)\b[\"']?\s*[=:]\s*[\"']?"
            r"(?!\*\*\*)([^\s,;\"'）)}]{4,})"
        ),
        value_group=1,
    ),
    # URL 里内嵌的用户名:口令。
    Rule(
        "URL userinfo",
        re.compile(r"(?i)\b[a-z][a-z0-9+.-]*://[^:/\s@]+:(?!\*\*\*)[^@/\s]+@"),
    ),
    # 常见托管平台的令牌前缀。
    Rule(
        "厂商前缀令牌",
        re.compile(
            r"\b(?:(?:sk|rk)_(?:live|test)_|hf_|glpat-|gh[pousr]_|xox[baprs]-|"
            r"AIza|npm_|pypi-|github_pat_|ATATT|whsec_)[A-Za-z0-9._-]{8,}"
        ),
    ),
    Rule("AWS 访问键", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{12,}")),
    Rule(
        "JWT",
        re.compile(r"\beyJ[A-Za-z0-9_-]{6,}\.[A-Za-z0-9_-]{6,}\.[A-Za-z0-9_-]{4,}"),
    ),
    # Cookie 头里的多段凭据（单段可能是普通会话 ID，多段才是"值里还有 KV"）。
    Rule(
        "Cookie 多段",
        re.compile(
            r"(?i)\b(?:set-)?cookie\s*:\s*(?!\s*\*)[^\r\n=]+=[^;\r\n]+;\s*[^=\r\n]+="
        ),
    ),
    Rule("私钥块", re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----")),
)


def _is_benign(value: str) -> bool:
    """判断 KV 的取值是不是"状态词 / 已脱敏占位符"，而不是真凭据。"""
    text = value.strip().strip("\"'").strip().lower()
    if not text:
        return True
    if text in BENIGN_VALUES:
        return True
    if text.startswith(("***", "<")):
        return True
    if set(text) <= set("*<>-_"):
        return True
    return False


def scan_text(text: str) -> dict[str, int]:
    """扫描一段文本，返回 ``{特征标签: 命中条数}``（无命中返回空 dict）。"""
    hits: dict[str, int] = {}
    if not text:
        return hits
    for rule in RULES:
        count = 0
        for match in rule.pattern.finditer(text):
            if rule.value_group is not None:
                value = match.group(rule.value_group) or ""
                if _is_benign(value):
                    continue
            count += 1
        if count:
            hits[rule.label] = count
    return hits


def total_hits(hits: dict[str, int]) -> int:
    return sum(hits.values())


def format_hits(hits: dict[str, int]) -> str:
    """把命中字典压成一行可写进日志/文案的文本（不含任何明文值）。"""
    return "、".join(f"{label}={count}" for label, count in hits.items())


#: 降级导出时写进包内的剔除清单文件名（网关与桌面共用同一名字）。
OMITTED_MEMBERS_NAME = "REDACTED-MEMBERS.txt"

#: 包内日志成员的重命名表：避免把 "error" 这类词直接摆到用户面前，
#: 让人误以为软件坏了。本机源文件名不变（仍是 ``error.log``），只改压缩包里的成员名。
LOG_MEMBER_ALIASES = {"error.log": "issue.log"}


def log_member_name(name: str) -> str:
    """返回压缩包内使用的日志基名（``error.log`` → ``issue.log``）。"""
    return LOG_MEMBER_ALIASES.get(name.lower(), name)


def omitted_note(omitted, *, log_prefix: str = "logs/") -> str:
    """包首页那句"少了几个文件"的说明（网关 README / 桌面 README 共用）。

    ``omitted`` 的每一项至少要有 ``name``；有 ``total`` 时只用于清单，不进这句话。
    """
    items = list(omitted)
    log_count = sum(1 for item in items if str(item["name"]).startswith(log_prefix))
    if log_count == len(items):
        return (
            f"为保护你的密钥，{len(items)} 个日志文件未包含"
            "（仍含明文凭据，已在导出前剔除）。"
        )
    return (
        f"为保护你的密钥，{len(items)} 个文件未包含"
        f"（其中日志 {log_count} 个；其余仍含明文凭据，已在导出前剔除）。"
    )


def omitted_members_text(omitted) -> str:
    """被剔除成员清单：只有文件名与命中条数，**不写任何命中内容**。"""
    items = list(omitted)
    lines = [
        "REDACTED-MEMBERS — 因含明文凭据而未包含的文件",
        "================================================",
        "",
        "导出前扫描发现下列成员仍含明文凭据（例如脱敏只遮了一半的 Key）。",
        "为了保护你的密钥，它们没有进入本压缩包；原始文件仍留在你的电脑上。",
        "",
    ]
    for item in items:
        name = str(item["name"])
        total = int(item.get("total") or 0)
        detail = format_hits(dict(item.get("hits") or {}))
        lines.append(f"- {name}：命中 {total} 处（{detail or '疑似密钥'}）")
    lines.append("")
    lines.append(
        "下一步：先退出鎏灏，把数据目录 logs 下的日志改名备份——"
        "liuhao.log、error.log、desktop-gateway.log 都改，"
        "例如把 .log 改成 .log.bak-日期（liuhao.log 变成 liuhao.log.bak-20261108）；"
        "不要直接删除，这些日志是排查证据。"
        "轮转文件（.log.1）建议一起改名：网关导出不收它，桌面导出会收。"
    )
    return "\n".join(lines) + "\n"


def omitted_readme(
    readme: str,
    omitted,
    *,
    marker: str,
    log_prefix: str = "logs/",
    member_name: str = OMITTED_MEMBERS_NAME,
) -> str:
    """把剔除说明插到 README 的 ``marker`` 之前；找不到 marker 时追加到末尾。"""
    items = list(omitted)
    if not items:
        return readme
    note = (
        omitted_note(items, log_prefix=log_prefix)
        + f"清单见 {member_name}。"
        + f"\n{member_name} 只是一份文本清单（不是病毒或损坏文件），用记事本打开即可。"
    )
    if marker and marker in readme:
        return readme.replace(marker, f"{note}\n\n{marker}", 1)
    return f"{readme}\n\n{note}\n"


__all__ = [
    "BENIGN_VALUES",
    "LOG_MEMBER_ALIASES",
    "OMITTED_MEMBERS_NAME",
    "RULES",
    "Rule",
    "format_hits",
    "log_member_name",
    "omitted_members_text",
    "omitted_note",
    "omitted_readme",
    "scan_text",
    "total_hits",
]
