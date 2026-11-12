"""主机命令的两条判定：提权（一律拒绝）与危险（一次确认）。

**归属**：这是「命令字符串层」的判定，不是操作系统层的边界。它能拦住直白写法，
但拦不住刻意拼接/编码/由子进程自己生成的命令 —— 报告里必须如实这么写。

为什么不把危险命令直接拒掉：老板要的是「主机命令一并放开」，危险动作保留**一次
确认**（设置里可关，关闭本身进审计 G3）。而**提权**不在此列：全权模式也不提权，
始终以当前 Windows 用户身份运行（不碰 UAC / 服务 / 计划任务 / HKLM）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

#: 提权特征：命中即 DENY，与模式无关（全权也不提权）。
_ESCALATION_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("runas", re.compile(r"(?i)(^|[\s;&|])runas(\.exe)?(\s|$)")),
    ("start-process-runas", re.compile(r"(?i)start-process[^\r\n]*?-verb\s+runas")),
    ("sc-service-change", re.compile(r"(?i)(^|[\s;&|])sc(\.exe)?\s+(create|config|delete)\b")),
    ("new-service", re.compile(r"(?i)\bnew-service\b")),
    ("set-service", re.compile(r"(?i)\bset-service\b")),
    ("reg-hklm", re.compile(r"(?i)(^|[\s;&|])reg(\.exe)?\s+(add|delete|import)\b[^\r\n]*hklm")),
    ("register-scheduled-task", re.compile(r"(?i)\bregister-scheduledtask\b")),
    ("schtasks-create", re.compile(r"(?i)(^|[\s;&|])schtasks(\.exe)?\s+/create\b")),
)

#: 危险特征：全权模式下走「一次确认」（设置可关，关闭进审计）。
_DANGER_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("format", re.compile(r"(?i)(^|[\s;&|])format(\.com)?\s+[a-z]:")),
    ("diskpart", re.compile(r"(?i)\bdiskpart(\.exe)?\b")),
    ("bcdedit", re.compile(r"(?i)\bbcdedit(\.exe)?\b")),
    ("del-recursive", re.compile(r"(?i)(^|[\s;&|])del(\.exe)?\b[^\r\n]*(\s/s\b|\s/f\b|\s/q\b)")),
    ("rmdir-recursive", re.compile(r"(?i)(^|[\s;&|])(rmdir|rd)(\.exe)?\b[^\r\n]*\s/s\b")),
    (
        "remove-item-recursive",
        re.compile(r"(?i)\bremove-item\b[^\r\n]*-(recurse|force)\b"),
    ),
    ("rm-recursive-force", re.compile(r"(?i)(^|[\s;&|])rm\s+-[a-z]*r[a-z]*f|(^|[\s;&|])rm\s+-[a-z]*f[a-z]*r")),
    ("shutdown", re.compile(r"(?i)(^|[\s;&|])shutdown(\.exe)?\s+(/s|/r|-s|-r)\b")),
    ("restart-computer", re.compile(r"(?i)\brestart-computer\b")),
    ("stop-computer", re.compile(r"(?i)\bstop-computer\b")),
)


@dataclass(frozen=True)
class DangerReport:
    """一条命令的判定结果；两项都为空即普通命令。"""

    escalation: str | None = None
    dangerous: str | None = None

    @property
    def is_escalation(self) -> bool:
        return bool(self.escalation)

    @property
    def is_dangerous(self) -> bool:
        return bool(self.dangerous)

    def describe(self) -> dict[str, str | None]:
        return {"escalation": self.escalation, "dangerous": self.dangerous}


def classify(command: str) -> DangerReport:
    """按命令字符串判定。空命令按普通命令处理（由调用方拒绝空命令）。"""
    text = command or ""
    escalation = next(
        (name for name, pattern in _ESCALATION_PATTERNS if pattern.search(text)), None
    )
    dangerous = next(
        (name for name, pattern in _DANGER_PATTERNS if pattern.search(text)), None
    )
    return DangerReport(escalation=escalation, dangerous=dangerous)


def escalation_names() -> list[str]:
    return [name for name, _ in _ESCALATION_PATTERNS]


def danger_names() -> list[str]:
    return [name for name, _ in _DANGER_PATTERNS]
