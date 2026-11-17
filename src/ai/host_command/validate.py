"""防诱导 §2 — 参数校验 + 四件大事分类（纯函数，可单测）。

位置：白名单命中之后、策略评估之前（broker 的第 1d 步）。校验的是
**参数层面**的危险，而不是“这条命令在不在白名单里”：白名单说“可以
用 git”，校验说“git 后面跟的参数不能是危险的组合”。

校验顺序（§2.2，逐条返回，任何一条不通过即 DENY，不继续往下走）：

1. 空命令 / 超长（> 2000 字符）；
2. 注入元字符 —— 用 :class:`src.security.input_sanitizer.CommandInjectionGuard`
   的 ``detect()``（只读调用，security 层不改）；``use_shell=False`` 时
   任何注入元字符都 DENY；``use_shell=True`` 时管道/重定向本身是 shell
   语法，不直接 DENY，改走“必须人工审批”；
3. 万能壳（powershell/cmd）——不在校验里单列：白名单的 program 字段
   已经把程序锁死，powershell/cmd 只有单独列条目并锁死参数模板才能用
   （见 allowlist 文档）；本层只拦第 4 条的编码形态；
4. 编码/拼接命令（-enc / -EncodedCommand / %VAR% / $env: / ^ 转义 /
   base64 载荷）——一律 DENY；
5. 路径参数 —— 规范化（去引号、去 ``\\\\?\\`` 前缀、折叠 ``..``）后
   必须落在允许根目录内；受保护文件名（审计库/主密钥/模式文件等）
   直接 DENY；目标不存在时不在本层判定（见下文注）；
6. 通配符 —— 盘符根（``C:\\*``）或无目录约束的通配符直接 DENY；
   其余通配符必须人工审批（展开结果要写进审批单，见 broker）；
7. 管道/重定向 —— 只在 ``use_shell=True`` 时出现，本层不拦，改走审批；
8. 危险开关（/s /q /f / -rf / --force 等）——改走“必须人工审批”。

返回值是三态：``ok=True``（放行，继续往策略走）、``ok=False``
（DENY）、``needs_approval=True``（必须人工审批；即使策略 ALLOW、
即使全权模式，也要先批后执行——由 broker 的第 2c 步强制 DEFER）。

注：§2.2 第 5 条“目标不存在先 DENY”不在本层做——校验时命令还没
跑，目标是否在磁盘上是执行时才知道的事（且沙箱里 Windows 路径本就
不存在）。审批单上列出展开后的目标，执行器在执行前逐个核对，目标
不存在时执行器报错、命令不跑。行为上等价（不存在的目标不会被执行），
只是拦截点在执行器而不在校验层。
"""

from __future__ import annotations

import base64
import binascii
import os
import re
import shlex
from dataclasses import dataclass, field
from typing import List, Optional

try:  # pragma: no cover - exercised through the public functions
    from src.security.input_sanitizer import CommandInjectionGuard
except Exception:  # pragma: no cover - security layer import guard
    CommandInjectionGuard = None  # type: ignore[assignment]

#: 命令总长度上限（§2.2 第 1 条）。
MAX_COMMAND_LENGTH = 2000

#: 通配符展开后单次允许的命中上限（§2.2 第 6 条，审批单上核对）。
MAX_WILDCARD_MATCHES = 50

#: 必须人工审批的危险开关（§2.2 第 8 条）。
DANGEROUS_FLAGS = frozenset({
    "/s", "/q", "/f", "-f", "-rf", "--force", "--recurse", "-recurse",
    "-force", "/r", "-r",
})

#: 同时带 r 与 f 的组合短开关（如 -rf / -fr / -rfa）。
_COMBINED_RF = re.compile(r"^-[a-z]*r[a-z]*f[a-z]*$|^-[a-z]*f[a-z]*r[a-z]*$", re.IGNORECASE)

#: 编码命令形态（§2.2 第 4 条）。
_ENCODED_FLAG = re.compile(r"(?i)(?:^|\s)-(?:enc|encodedcommand|e)(?:\s|$|=)")
_ENV_SPLICE = re.compile(r"%[A-Za-z_][A-Za-z0-9_]*%|\$env:")
_CARET_SPLICE = re.compile(r"\^[\w&|]")
_BASE64_TOKEN = re.compile(r"^[A-Za-z0-9+/]{64,}={0,2}$")
_BASE64_PAYLOAD_VERB = re.compile(
    r"(?i)\b(del|remove|format|shutdown|invoke|download|iex|start-process)\b"
)

#: 四件大事里的“改代码”：写文件时这些后缀才算改代码/改配置。
_CODE_EXTENSIONS = frozenset({
    ".py", ".pyw", ".js", ".ts", ".tsx", ".jsx", ".json", ".yaml", ".yml",
    ".toml", ".ini", ".cfg", ".cs", ".java", ".go", ".rs", ".cpp", ".h",
    ".html", ".css", ".sql",
})

#: 写文件类动词（与“改代码”分类配合）。
_WRITE_VERBS = frozenset({
    "copy", "move", "xcopy", "robocopy", "out-file", "set-content",
    "add-content", "echo",
})

#: 受保护文件名（与 src/security/protected_paths.py 的保护清单同口径；
#: 该层是禁区，本层只做同口径的只读复核，不改它）。
_PROTECTED_BASENAMES = frozenset({
    "audit_store.db", "master.key", "authority_mode.json",
    "host_command_confirm.json", "host_command_allowlist.json",
    "login_throttle.sqlite3",
})


@dataclass(frozen=True)
class ValidationReport:
    """一次参数校验的结果。"""

    ok: bool
    needs_approval: bool = False
    rule_code: str = ""
    reason: str = ""
    expanded_targets: tuple = field(default_factory=tuple)

    def as_dict(self) -> dict:
        return {
            "ok": self.ok,
            "needs_approval": self.needs_approval,
            "rule_code": self.rule_code,
            "reason": self.reason,
            "expanded_targets": list(self.expanded_targets),
        }


def _normalize_path(token: str) -> str:
    """把一个路径类 token 规范化（去引号/去 \\\\?\\ 前缀/折叠 ..）。"""
    text = token.strip().strip("\"'")
    for prefix in ("\\\\?\\", "\\\\.\\"):
        if text.lower().startswith(prefix):
            text = text[len(prefix):]
    text = text.replace("/", "\\")
    absolute = text.startswith("\\")
    parts: List[str] = []
    for part in text.split("\\"):
        if part in ("", "."):
            continue
        if part == "..":
            if parts and parts[-1] not in ("", ".."):
                parts.pop()
            else:
                parts.append("..")
            continue
        parts.append(part)
    normalized = "\\".join(parts)
    # 绝对路径（/tmp/... 或 \Windows\...）保留开头的分隔符，否则与
    # _under_roots 里的根目录比对不上（根保留开头，路径丢了开头）。
    if absolute and normalized and not normalized.startswith("\\"):
        normalized = "\\" + normalized
    return normalized


def _looks_like_path(token: str) -> bool:
    return bool(re.search(r"[\\/]", token) or re.match(r"^[A-Za-z]:", token))


def _is_flag_token(token: str) -> bool:
    """开关类 token（/q、-rf、--force）：以 / 或 - 开头且不再含分隔符。

    这类 token 不是路径参数——``format c: /q`` 里的 ``/q`` 若当成路径，
    会因为不在允许根目录内被误拦。
    """
    return bool(re.match(r"^[/-][^/\\]*$", token.strip().strip("\"'")))


def _under_roots(path: str, roots: List[str]) -> bool:
    lowered = path.lower()
    for root in roots:
        root_norm = root.replace("/", "\\").rstrip("\\").lower()
        if lowered == root_norm or lowered.startswith(root_norm + "\\"):
            return True
    return False


def _tokenize(command: str) -> Optional[List[str]]:
    try:
        return shlex.split(command, posix=False)
    except ValueError:
        return None


def validate_command(
    command: str,
    *,
    use_shell: bool = False,
    allowed_roots: Optional[List[str]] = None,
) -> ValidationReport:
    """按 §2.2 逐条校验一条命令的参数层面。"""
    roots = list(allowed_roots or [])
    text = command or ""

    if not text.strip():
        return ValidationReport(
            ok=False, rule_code="deny:param:empty", reason="命令为空")
    if len(text) > MAX_COMMAND_LENGTH:
        return ValidationReport(
            ok=False, rule_code="deny:param:too-long",
            reason=f"命令超过 {MAX_COMMAND_LENGTH} 字符")

    # 第 4 条：编码/拼接命令（先于注入判定——编码命令本身就是绕过手段）。
    if _ENCODED_FLAG.search(text):
        return ValidationReport(
            ok=False, rule_code="deny:param:encoded-command",
            reason="命令含编码形态（-enc/-EncodedCommand），拒绝执行")
    if _ENV_SPLICE.search(text):
        return ValidationReport(
            ok=False, rule_code="deny:param:env-splice",
            reason="命令含环境变量拼接（%VAR%/$env:），拒绝执行")
    if _CARET_SPLICE.search(text):
        return ValidationReport(
            ok=False, rule_code="deny:param:caret-splice",
            reason="命令含 ^ 转义拼接，拒绝执行")
    argv_for_b64 = text.split()
    for token in argv_for_b64:
        if _BASE64_TOKEN.match(token):
            try:
                decoded = base64.b64decode(token).decode("utf-8", "ignore")
            except binascii.Error:
                continue
            if _BASE64_PAYLOAD_VERB.search(decoded):
                return ValidationReport(
                    ok=False, rule_code="deny:param:base64-payload",
                    reason="命令含 base64 编码的命令载荷，拒绝执行")

    # 第 2 条：注入元字符（detect() 返回命中列表；is_suspicious() 是布尔判定）。
    if CommandInjectionGuard is not None:
        guard = CommandInjectionGuard()
        if guard.is_suspicious(text):
            if use_shell:
                # shell 语法本身就含管道/重定向：不直接 DENY，改走审批。
                return ValidationReport(
                    ok=True, needs_approval=True,
                    rule_code="escalate:param:shell-metachars",
                    reason="命令含 shell 元字符（管道/重定向），须人工审批")
            return ValidationReport(
                ok=False, rule_code="deny:param:injection",
                reason="命令含注入元字符（; | & ` $() 等），拒绝执行")

    argv = _tokenize(text)
    if argv is None:
        return ValidationReport(
            ok=False, rule_code="deny:param:unparseable",
            reason="命令无法分词，拒绝执行")

    # 第 6 条：通配符（先于路径检查——通配符 token 本身不是真实路径）。
    # 静态前缀在根目录外时直接 DENY；盘符根或无目录约束时直接 DENY；
    # 其余通配符记下来，跑完其余检查后再统一要求人工审批（展开结果须
    # 写进审批单）——不能在这里直接 return，否则同命令里其他越界路径
    # 会被漏掉。
    wildcard_needs_approval = False
    for token in argv[1:]:
        if "*" not in token and "?" not in token:
            continue
        head = token.split("*")[0].split("?")[0]
        head = head.strip().strip("\"'")
        if re.match(r"^[A-Za-z]:\\?$", head.strip()) or head in ("", "\\"):
            return ValidationReport(
                ok=False, rule_code="deny:param:wildcard-too-broad",
                reason="通配符覆盖整个盘符或无目录约束，拒绝执行")
        if _looks_like_path(head) and not _under_roots(
            _normalize_path(head), roots
        ):
            return ValidationReport(
                ok=False, rule_code="deny:param:path-outside-roots",
                reason=f"通配符的目录不在允许目录内（{head}），拒绝执行")
        wildcard_needs_approval = True

    # 第 5 条：路径参数（规范化后必须在允许根目录内；受保护文件名拒绝）。
    # argv[0] 是程序本身（身份由白名单的 program 字段管），不在此检查；
    # 单独一个盘符（format c: 里的 c:）也不是文件路径参数。
    for token in argv[1:]:
        if "*" in token or "?" in token:
            continue
        if re.match(r"^[A-Za-z]:$", token.strip().strip("\"'")):
            continue
        if _is_flag_token(token):
            continue
        if not _looks_like_path(token):
            continue
        normalized = _normalize_path(token)
        basename = normalized.split("\\")[-1].lower()
        if basename in _PROTECTED_BASENAMES:
            return ValidationReport(
                ok=False, rule_code="deny:param:protected-path",
                reason=f"路径指向受保护文件（{basename}），拒绝执行")
        if normalized.startswith(".."):
            return ValidationReport(
                ok=False, rule_code="deny:param:path-escape",
                reason="路径试图越界（..），拒绝执行")
        if not _under_roots(normalized, roots):
            return ValidationReport(
                ok=False, rule_code="deny:param:path-outside-roots",
                reason=f"路径不在允许目录内（{normalized}），拒绝执行")

    # 第 7 条：管道/重定向（use_shell 模式下才出现）。
    if use_shell and re.search(r"[|<>]", text):
        return ValidationReport(
            ok=True, needs_approval=True,
            rule_code="escalate:param:shell-redirect",
            reason="命令含管道/重定向，须人工审批")

    # 第 8 条：危险开关。
    for token in argv:
        lowered = token.lower()
        if lowered in DANGEROUS_FLAGS or _COMBINED_RF.match(token):
            return ValidationReport(
                ok=True, needs_approval=True,
                rule_code="escalate:param:dangerous-flag",
                reason=f"命令含危险开关（{token}），须人工审批")

    # 通配符（第 6 条的后半段）：展开结果须写进审批单、人工审批。
    if wildcard_needs_approval:
        return ValidationReport(
            ok=True, needs_approval=True,
            rule_code="escalate:param:wildcard",
            reason="命令含通配符，展开结果须写进审批单、人工审批")

    # use_shell=True 的请求一律人工审批（不能只靠白名单放行；§2.2 第 7 条）。
    if use_shell:
        return ValidationReport(
            ok=True, needs_approval=True,
            rule_code="escalate:param:use-shell",
            reason="use_shell=True 的请求一律人工审批")

    return ValidationReport(ok=True)


# ---------------------------------------------------------------------------
# 四件大事分类（§3.1）：删文件 / 改代码 / 装软件 / 动系统设置。
# 返回其中之一，或 None（不是四件大事）。
# ---------------------------------------------------------------------------

_DELETE_PROGRAMS = frozenset({
    "del", "erase", "rmdir", "rd", "remove-item", "rm",
})

_INSTALL_PROGRAMS = frozenset({
    "pip", "pip3", "uv", "winget", "choco", "chocolatey", "msiexec",
    "npm", "yarn", "pnpm",
})

_SYSTEM_PROGRAMS = frozenset({
    "reg", "sc", "schtasks", "netsh", "powercfg", "bcdedit", "diskpart",
    "shutdown", "restart-computer", "stop-computer", "set-service",
    "new-service", "register-scheduledtask", "format",
})


def classify_big_action(command: str) -> Optional[str]:
    """把一条命令归类到四件大事之一；不是则返回 None。"""
    argv = _tokenize(command or "")
    if not argv:
        return None
    program = argv[0].strip().lower()
    for suffix in (".exe", ".com", ".bat", ".cmd"):
        if program.endswith(suffix):
            program = program[: -len(suffix)]
            break

    # 删文件：删除类程序本身就是大事（无论带不带 -rf）。
    if program in _DELETE_PROGRAMS:
        return "delete"
    # 动系统设置：系统类程序（关机/重启/格式化/改服务/改计划任务/改注册表）。
    if program in _SYSTEM_PROGRAMS:
        return "system_settings"
    # 装软件：包管理器装软件，或直接执行 .msi 安装包。
    if program in _INSTALL_PROGRAMS:
        if len(argv) > 1 and argv[1].lower() in ("install", "add", "i"):
            return "install"
        if program == "msiexec":
            return "install"
    if program.endswith(".msi"):
        return "install"
    # 改代码：写文件类操作的目标是代码/配置后缀。
    if program in _WRITE_VERBS or ">" in (command or ""):
        for token in argv[1:]:
            lowered = token.lower()
            if any(lowered.endswith(ext) for ext in _CODE_EXTENSIONS):
                return "modify_code"
    return None


def default_allowed_roots() -> List[str]:
    """校验默认允许的根目录：数据目录 + 工作区目录（环境变量配置）。"""
    roots: List[str] = []
    try:
        from src.common.data_paths import data_root, WORKSPACE_ROOT_ENV

        data = data_root()
        if data is not None:
            roots.append(str(data))
        workspace = os.environ.get(WORKSPACE_ROOT_ENV, "").strip()
        if workspace:
            roots.append(workspace)
    except Exception:  # pragma: no cover - defensive; fail closed downstream
        pass
    return roots
