"""诊断包与日志共用的文本脱敏 + 独立的自检谓词。

为什么自检不复用脱敏规则
------------------------
如果脱敏漏了一类，拿同一套规则去"自检"必然一起漏。所以这里刻意分成两层：

* :func:`redact_text` —— 把已知敏感形态替换成占位符，尽量保留可读性；
* :func:`leak_violations` —— 一份**独立编写**的泄漏特征清单，正向判断"文本里
  是否还有不该出现的东西"。诊断包只在后者为空时才允许落盘。

覆盖的泄漏类别（脱敏与自检各自实现一遍）：

* 路径里的用户名（``C:\\Users\\张三`` / ``/home/zhangsan`` / ``/root``）；
* 环境变量名（``LIUHAO_*`` / ``APP_ENV`` / ``AI_*`` / 全大写三段式常量名）；
* 脚本与安装路径（``scripts/`` / ``packaging/`` / ``installer/`` /
  ``Program Files`` / 仓库名）；
* 层级字段（``parent_id`` / ``root_id`` / ``depth`` / ``ancestor``）；
* 密钥与令牌（PEM、``sk-``、``AIza``、``gsk_``、``ghp_``、``xox?-``、``AKIA``、
  JWT、``Authorization: Basic/Bearer``、URL userinfo、``key=value`` 形式的
  口令 / 恢复码 / 通行短语）。
"""
from __future__ import annotations

import getpass
import re
from typing import List, Tuple

#: 占位符变更或规则扩展时递增；诊断包的 manifest 会记录它。
REDACTION_VERSION = "2"

REDACTED = "***REDACTED***"
USER_PLACEHOLDER = "<用户>"
ENV_PLACEHOLDER = "<环境变量>"
PATH_PLACEHOLDER = "<程序文件>"
APP_PLACEHOLDER = "<程序目录>"
HIERARCHY_PLACEHOLDER = "<层级字段>"

# --------------------------------------------------------------------------- #
# 1) 路径中的用户名
# --------------------------------------------------------------------------- #

_USER_PATH_PATTERNS: Tuple[Tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"(?i)([A-Za-z]:[\\/]+Users[\\/]+)[^\\/\r\n\"'<>|]+"), rf"\1{USER_PLACEHOLDER}"),
    (
        re.compile(r"(?i)([A-Za-z]:[\\/]+Documents and Settings[\\/]+)[^\\/\r\n\"'<>|]+"),
        rf"\1{USER_PLACEHOLDER}",
    ),
    (re.compile(r"(/(?:home|Users)/)(?!<用户>)[^/\s\"'<>|]+"), rf"\1{USER_PLACEHOLDER}"),
    (re.compile(r"(?i)(^|[\s\"'(=/])root(?=/|\s|$)"), rf"\1{USER_PLACEHOLDER}"),
)


def _mask_system_user(text: str) -> str:
    """把当前系统用户名在自由文本里也遮掉（路径之外的位置）。"""
    try:
        user = getpass.getuser()
    except Exception:  # noqa: BLE001 - 取不到用户名时静默跳过
        return text
    if len(user) < 3:
        return text
    pattern = rf"(?<![A-Za-z0-9_.-]){re.escape(user)}(?![A-Za-z0-9_.-])"
    return re.sub(pattern, USER_PLACEHOLDER, text)


def mask_user_path(text: str) -> str:
    """只处理路径里的用户名；供需要单独复用的调用方使用。"""
    if not text:
        return ""
    out = str(text)
    for pattern, replacement in _USER_PATH_PATTERNS:
        out = pattern.sub(replacement, out)
    return _mask_system_user(out)


# --------------------------------------------------------------------------- #
# 2) 环境变量名 / 3) 脚本与安装路径 / 4) 层级字段
# --------------------------------------------------------------------------- #

_ENV_NAME_PATTERNS: Tuple[re.Pattern[str], ...] = (
    # 必须带下划线后缀：裸 ``LIUHAO`` 是产品名（"鎏灏 LIUHAO X"），不是变量名。
    re.compile(r"\bLIUHAO_[A-Z0-9_]+\b"),
    re.compile(r"\bAPP_ENV\b"),
    re.compile(r"\bAI_[A-Z0-9_]+\b"),
    re.compile(
        r"\b(?:OPENAI|ANTHROPIC|MOONSHOT|DEEPSEEK|OLLAMA|GEMINI|GOOGLE|GROQ|MISTRAL)"
        r"_[A-Z0-9_]+\b"
    ),
    # 通用兜底：三段以上的全大写常量名（LIUHAO_AUTH_TTL_SECONDS 之类）。
    re.compile(r"\b[A-Z][A-Z0-9]*(?:_[A-Z0-9]+){2,}\b"),
)

_PATH_PATTERNS: Tuple[Tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(
            r"(?i)[A-Za-z]:[\\/][^\s\"'<>|]*[\\/](?:scripts|packaging|installer)[\\/]"
            r"[^\s\"'<>|]*"
        ),
        PATH_PLACEHOLDER,
    ),
    (
        re.compile(r"(?i)\b(?:scripts|packaging|installer)[\\/][A-Za-z0-9_.\-/\\]+"),
        PATH_PLACEHOLDER,
    ),
    (
        re.compile(r"(?i)[A-Za-z]:[\\/]+Program Files(?: \(x86\))?[\\/][^\s\"'<>|]*"),
        PATH_PLACEHOLDER,
    ),
    (re.compile(r"(?i)[A-Za-z]:[\\/][^\s\"'<>|]*LiuHao-AI-OS[^\s\"'<>|]*"), APP_PLACEHOLDER),
)

#: 任意本机绝对路径。``_PATH_PATTERNS`` 只认安装/脚本目录，日志里的 traceback
#: 还会带出数据目录、虚拟环境等其它绝对路径；这里统一折叠成"<本地路径>/文件名"，
#: 既去掉本机目录结构，又保留"是哪个文件出问题"这个技术线索。
_ABS_PATH_PATTERNS: Tuple[Tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(r"(?<![A-Za-z0-9_])([A-Za-z]:[\\/])(?:[^\\/\s\"'<>|]+[\\/])*([^\\/\s\"'<>|]+)"),
        r"<本地路径>\\\2",
    ),
    (
        re.compile(
            r"(?<![\w/])/(?:home|Users|root|var|opt|tmp|mnt|srv|usr|app|workspace)"
            r"(?:/[^\s\"'<>|]+)*/([^/\s\"'<>|]+)"
        ),
        r"<本地路径>/\1",
    ),
)

#: 内部政策 / 设计编号（OD-010、LHX-C-010、C-2、§10.8.3-1）。日志允许技术细节，
#: 但不允许把内部编号带出去。
_POLICY_ID_PATTERNS: Tuple[re.Pattern[str], ...] = (
    re.compile(r"§\s*\d+(?:\.\d+)*"),
    re.compile(
        r"(?<![A-Za-z0-9])(?:LHX-[A-Z]-|OD-|C-)\d+(?:\.\d+)*(?![A-Za-z0-9])",
        re.I,
    ),
)
POLICY_ID_PLACEHOLDER = "内部编号"

_HIERARCHY_KV = re.compile(
    r"(?i)\b(parent[_-]?id|root[_-]?id|depth|ancestors?|ancestor[_-]?ids?)\b"
    r"\s*[:=]\s*(\"[^\"]*\"|'[^']*'|[^\s,;\"'）)]+)"
)
_HIERARCHY_WORD = re.compile(
    r"(?i)\b(parent[_-]?id|root[_-]?id|depth|ancestors?|ancestor[_-]?ids?)\b"
)

# --------------------------------------------------------------------------- #
# 5) 密钥与令牌
# --------------------------------------------------------------------------- #

_PRIVATE_KEY_BLOCK = re.compile(
    r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----.*?-----END [A-Z0-9 ]*PRIVATE KEY-----", re.S
)

_KV_SECRET = re.compile(
    r"(?i)((?:api[_-]?key|access[_-]?key|secret[_-]?key|integrity[_-]?key|"
    r"signing[_-]?key|private[_-]?key|client[_-]?secret|refresh[_-]?token|"
    r"access[_-]?token|session[_-]?token|id[_-]?token|recovery[_-]?code|"
    r"passphrase|pass[_-]?phrase|token|password|passwd|pwd|cookie|authorization|"
    r"credential|secret)\s*[\"']?\s*[=:]\s*)"
    r"(\"[^\"]*\"|'[^']*'|[^\s,;\"'）)]+)"
)

_SECRET_PATTERNS: Tuple[Tuple[re.Pattern[str], str], ...] = (
    (_PRIVATE_KEY_BLOCK, "***PRIVATE-KEY-REDACTED***"),
    (re.compile(r"(?i)\b(sk-[A-Za-z0-9_-]{8,})"), "sk-***REDACTED***"),
    (re.compile(r"\b(AIza[0-9A-Za-z_\-]{20,})"), "AIza***REDACTED***"),
    (re.compile(r"\b(gsk_[A-Za-z0-9]{16,})"), "gsk_***REDACTED***"),
    (re.compile(r"\b(gh[pousr]_[A-Za-z0-9]{16,})"), "gh***REDACTED***"),
    (re.compile(r"\b(xox[baprs]-[A-Za-z0-9-]{8,})"), "xox-***REDACTED***"),
    (re.compile(r"\b((?:AKIA|ASIA)[0-9A-Z]{12,})"), REDACTED),
    (re.compile(r"(?i)\b(Bearer\s+)[A-Za-z0-9._~+/=-]{8,}"), rf"\1{REDACTED}"),
    (
        re.compile(
            r"(?i)\b((?:authorization|proxy-authorization)\s*:\s*)"
            r"(?:basic|bearer|digest|token)?\s*[A-Za-z0-9._~+/=:-]{6,}"
        ),
        rf"\1{REDACTED}",
    ),
    (
        re.compile(r"\b(eyJ[A-Za-z0-9_-]{6,}\.[A-Za-z0-9_-]{6,}\.[A-Za-z0-9_-]{4,})"),
        "***JWT-REDACTED***",
    ),
    (re.compile(r"(\b[a-z][a-z0-9+.-]*://[^:/\s@]+:)([^@/\s]+)(@)", re.I), rf"\1{REDACTED}\3"),
    (_KV_SECRET, rf"\1{REDACTED}"),
    (re.compile(r"(?i)\b(AI_PROVIDER_KEY\s*=\s*)([^\s\"']+)"), rf"\1{REDACTED}"),
)


def redact_text(text: str) -> str:
    """把常见敏感形态替换为固定占位串（幂等）。"""
    if not text:
        return ""
    out = str(text)
    for pattern, replacement in _SECRET_PATTERNS:
        out = pattern.sub(replacement, out)
    for pattern, replacement in _PATH_PATTERNS:
        out = pattern.sub(replacement, out)
    out = mask_user_path(out)
    for pattern in _POLICY_ID_PATTERNS:
        out = pattern.sub(POLICY_ID_PLACEHOLDER, out)
    for pattern in _ENV_NAME_PATTERNS:
        out = pattern.sub(ENV_PLACEHOLDER, out)
    out = _HIERARCHY_KV.sub(rf"\1={HIERARCHY_PLACEHOLDER}", out)
    out = _HIERARCHY_WORD.sub(HIERARCHY_PLACEHOLDER, out)
    return out


def sanitize_text(text: str) -> str:
    """语义化别名：所有要进诊断包的文本都走这里。"""
    return redact_text(text)


def redact_log_text(text: str) -> str:
    """``error.log`` 落盘前的脱敏：比诊断包更严，任意本机绝对路径都折叠。

    诊断包需要保留 ``C:\\Users\\<用户>\\...`` 这类可读路径用于排障；日志不需要，
    只要保留"是哪个文件出的问题"。所以这里先把整个绝对路径折叠成
    ``<本地路径>/文件名``，再走通用脱敏（密钥、环境变量、层级字段、政策编号）。
    """
    if not text:
        return ""
    out = str(text)
    for pattern, replacement in _ABS_PATH_PATTERNS:
        out = pattern.sub(replacement, out)
    return redact_text(out)


# --------------------------------------------------------------------------- #
# 独立自检：正向的"泄漏特征清单"
# --------------------------------------------------------------------------- #
# 这个元组刻意不复用上面的正则以保证独立性；修改脱敏规则时，这里必须单独
# 复核。每个条目是 (类别标签, 正则)。命中即视为仍有泄漏。
_LEAK_FEATURES: Tuple[Tuple[str, re.Pattern[str]], ...] = (
    # 用户名路径
    (
        "用户名路径",
        re.compile(r"(?i)[A-Za-z]:[\\/]+Users[\\/]+(?!<用户>)[^\\/\r\n\"'<>|]+"),
    ),
    (
        "用户名路径",
        re.compile(r"(?i)[A-Za-z]:[\\/]+Documents and Settings[\\/]+(?!<用户>)[^\\/\r\n\"'<>|]+"),
    ),
    ("用户名路径", re.compile(r"/(?:home|Users)/(?!<用户>)[^/\s\"'<>|]+")),
    # 环境变量名
    ("环境变量名", re.compile(r"\bLIUHAO_[A-Z0-9_]+\b")),
    ("环境变量名", re.compile(r"\bAPP_ENV\b")),
    ("环境变量名", re.compile(r"\bAI_[A-Z0-9_]+\b")),
    ("环境变量名", re.compile(r"\b[A-Z][A-Z0-9]*(?:_[A-Z0-9]+){2,}\b")),
    # 脚本与安装路径
    (
        "脚本或安装路径",
        re.compile(r"(?i)\b(?:scripts|packaging|installer)[\\/][^\s\"'<>|]+"),
    ),
    (
        "脚本或安装路径",
        re.compile(r"(?i)\b[A-Za-z]:[\\/][^\s\"'<>|]*(?:scripts|packaging|installer)[\\/]"),
    ),
    (
        "脚本或安装路径",
        re.compile(r"(?i)\b[A-Za-z]:[\\/]+Program Files(?: \(x86\))?[\\/]"),
    ),
    ("脚本或安装路径", re.compile(r"(?i)LiuHao-AI-OS[\\/][^\s\"'<>|]+")),
    # 层级字段
    (
        "层级字段",
        re.compile(r"(?i)\b(parent[_-]?id|root[_-]?id|depth|ancestors?|ancestor[_-]?ids?)\b"),
    ),
    # 密钥与令牌（各家独立模式，避免与脱敏共用同一条正则）
    ("密钥或令牌", re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----")),
    ("密钥或令牌", re.compile(r"(?i)\bsk-[A-Za-z0-9_-]{8,}")),
    ("密钥或令牌", re.compile(r"\bAIza[0-9A-Za-z_\-]{20,}")),
    ("密钥或令牌", re.compile(r"\bgsk_[A-Za-z0-9]{16,}")),
    ("密钥或令牌", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{16,}")),
    ("密钥或令牌", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{12,}")),
    ("密钥或令牌", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{8,}")),
    ("密钥或令牌", re.compile(r"\beyJ[A-Za-z0-9_-]{6,}\.[A-Za-z0-9_-]{6,}\.[A-Za-z0-9_-]{4,}")),
    (
        "密钥或令牌",
        re.compile(r"(?i)\b(?:Bearer|Basic)\s+[A-Za-z0-9._~+/=:-]{8,}"),
    ),
    (
        "密钥或令牌",
        re.compile(
            r"(?i)\b(?:recovery[_-]?code|passphrase|pass[_-]?phrase)\s*"
            r"[\"']?\s*[=:]\s*(?![\*<])"
        ),
    ),
    (
        "密钥或令牌",
        re.compile(
            r"(?i)\b(?:api[_-]?key|access[_-]?key|secret[_-]?key|integrity[_-]?key|"
            r"signing[_-]?key|private[_-]?key|token|password|passwd|pwd|"
            r"client[_-]?secret|refresh[_-]?token|session[_-]?token|cookie)\s*"
            r"[\"']?\s*[=:]\s*(?![\*<])\S{4,}"
        ),
    ),
    (
        "密钥或令牌",
        re.compile(r"(?i)\b[a-z][a-z0-9+.-]*://[^:/\s@]+:(?![\*<])[^@/\s]+@"),
    ),
)


def leak_violations(text: str) -> List[str]:
    """返回命中的泄漏类别（去重、稳定顺序）；空列表表示通过。"""
    if not text:
        return []
    found: List[str] = []
    for label, pattern in _LEAK_FEATURES:
        if pattern.search(text) and label not in found:
            found.append(label)
    return found


def contains_secret(text: str) -> bool:
    """兼容旧接口：文本里是否仍有疑似密钥形态。"""
    return any(label == "密钥或令牌" for label in leak_violations(text))


__all__ = [
    "POLICY_ID_PLACEHOLDER",
    "REDACTION_VERSION",
    "contains_secret",
    "leak_violations",
    "mask_user_path",
    "redact_log_text",
    "redact_text",
    "sanitize_text",
]
