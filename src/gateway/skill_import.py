"""技能包解析：把外来格式收敛成 ``SkillStore`` 能存的一份字段。

支持两种输入：

1. **Agent Skills**（``SKILL.md`` + YAML frontmatter）。按规范读取
   ``name`` / ``description`` / ``license`` / ``allowed-tools`` / ``metadata``，
   其余未知键**原样保留**并写进 ``import_report``，绝不假装支持。
2. **本机 JSON 技能清单**（``name`` / ``description`` / ``capabilities`` 等）。

明确**不**支持、并在错误里说清楚的：

* MCP server 配置（``mcpServers``）—— 那是工具源，不是技能，正确入口是工具中心；
* 常驻技能进程（``runtime.mode: service``）—— 0.3.0 只做一次性脚本，常驻
  需要另一套生命周期设计，这里明确拒绝，不偷偷放行。

诚实边界
--------
* 本模块只做"读懂"，不执行脚本；真正的执行在 ``skill_runtime``，首次运行必须
  Boss 授权，且只是"受限执行"，不是沙箱。
* ``execution_allowed`` 表示"声明了可执行入口"，不等于"已授权"；授权状态由
  ``skill_runtime.SkillApprovalStore`` 单独维护。
* zip / 目录包由 ``skill_install`` 负责安全解包与校验，本模块只解析单个
  ``SKILL.md`` / JSON 清单的语义。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

try:  # PyYAML 已是项目依赖；缺失时按"没有 frontmatter"退化，而不是崩掉导入。
    import yaml
except Exception:  # pragma: no cover - 依赖缺失的环境
    yaml = None  # type: ignore[assignment]


class SkillImportError(ValueError):
    """导入内容不可解析。消息面向用户，必须能直接展示。"""


class McpConfigDetected(SkillImportError):
    """导入的是 MCP server 配置（工具源），不是技能。"""


#: 正文里出现这些形态就记为"引用了脚本"（只登记，不执行）。
_SCRIPT_REF_RE = re.compile(
    r"(?i)(?:\./)?(scripts?/[A-Za-z0-9_.\-/]+\.[A-Za-z0-9]+)"
)

#: frontmatter 里**已映射**到内部字段的键；其余键一律进 import_report。
_MAPPED_KEYS = ("name", "description", "license", "allowed-tools", "metadata", "runtime")

#: 脚本运行默认上限。执行时以 ``runtime.limits`` 覆盖，但不会超过上限。
DEFAULT_LIMITS: Dict[str, int] = {
    "memory_mb": 256,
    "job_memory_mb": 512,
    "cpu_seconds": 30,
    "timeout_seconds": 60,
    "max_processes": 4,
    "max_output_bytes": 65536,
}

_LIMIT_BOUNDS: Dict[str, Tuple[int, int]] = {
    "memory_mb": (32, 2048),
    "job_memory_mb": (64, 4096),
    "cpu_seconds": (1, 300),
    "timeout_seconds": (1, 300),
    "max_processes": (1, 8),
    "max_output_bytes": (1024, 256 * 1024),
}

INTERPRETER_SUFFIX: Dict[str, Tuple[str, ...]] = {
    "python": (".py",),
    "node": (".js", ".mjs", ".cjs"),
    "powershell": (".ps1",),
}

_ENTRY_ID_RE = re.compile(r"^[a-z0-9_-]{1,32}$")
_ENV_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")
_FORBIDDEN_ENV = frozenset(
    {"PATH", "PYTHONPATH", "PYTHONHOME", "NODE_OPTIONS", "PSModulePath", "COMSPEC", "PATHEXT"}
)

#: 兜底名称：规范里 name 是必填，但缺了也不能叫 "SKILL"。
_PLACEHOLDER_NAME = "未命名技能（导入时缺少 name）"

#: 文件名主干落在这些值上时不能当名称用（否则会出现叫「SKILL」的技能）。
_FILENAME_STEMS_NOT_NAMES = frozenset({"skill", "index", "readme", "main"})


@dataclass
class ImportedSkill:
    """解析结果：能落盘的一组字段 + 一份如实的映射报告。"""

    name: str
    description: str = ""
    version: str = ""
    capabilities: List[str] = field(default_factory=list)
    permissions: List[str] = field(default_factory=list)
    declared_risk: str = ""
    content: Optional[str] = None
    format: str = "agent-skill"
    origin: str = ""
    license: str = ""
    allowed_tools: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)
    raw_frontmatter: Dict[str, Any] = field(default_factory=dict)
    has_scripts: bool = False
    script_refs: List[str] = field(default_factory=list)
    files: List[str] = field(default_factory=list)
    name_source: str = "frontmatter"
    import_report: List[Dict[str, Any]] = field(default_factory=list)
    runtime: Dict[str, Any] = field(default_factory=dict)

    @property
    def execution_allowed(self) -> bool:
        # 只表示"声明了可执行入口"；是否已授权由运行时授权表决定。
        return bool((self.runtime or {}).get("entrypoints"))


def _runtime_error(message: str) -> SkillImportError:
    return SkillImportError(f"runtime 声明不合法：{message}")


def _normalize_script_path(value: Any) -> str:
    text = _as_text(value)
    if not text:
        raise _runtime_error("entrypoints[].script 不能为空。")
    normalized = text.replace("\\", "/")
    if normalized.startswith("/") or re.match(r"^[A-Za-z]:", normalized):
        raise _runtime_error(f"脚本路径「{text}」必须是包内相对路径，不能是绝对路径。")
    parts = normalized.split("/")
    if any(part in ("", ".", "..") for part in parts):
        raise _runtime_error(f"脚本路径「{text}」不能包含 .. 或空路径段。")
    if len(normalized) > 240:
        raise _runtime_error("脚本路径超过 240 字符。")
    return "/".join(parts)


def normalize_runtime(raw: Any) -> Dict[str, Any]:
    """把 SKILL.md 的 ``runtime`` 块收敛成可执行声明；未声明返回 ``{}``。

    任何不合法都抛 ``SkillImportError``（安装失败），不做"猜一个默认值"的兜底。
    """
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise _runtime_error("必须是键值对象（YAML mapping）。")
    mode = raw.get("mode")
    if mode not in (None, "", "oneshot", "script"):
        if str(mode).strip().lower() == "service":
            raise SkillImportError(
                "本版不支持常驻技能进程（runtime.mode=service）。"
                "常驻需要独立生命周期设计，0.3.0 只支持一次性脚本。"
            )
        raise _runtime_error(f"不认识的 mode「{mode}」。")

    raw_limits = raw.get("limits") or {}
    if not isinstance(raw_limits, dict):
        raise _runtime_error("limits 必须是键值对象。")
    limits = dict(DEFAULT_LIMITS)
    for key, value in raw_limits.items():
        if key not in DEFAULT_LIMITS:
            raise _runtime_error(f"不认识的资源上限「{key}」。")
        if isinstance(value, bool) or not isinstance(value, int):
            raise _runtime_error(f"limits.{key} 必须是整数。")
        low, high = _LIMIT_BOUNDS[key]
        if not (low <= value <= high):
            raise _runtime_error(
                f"limits.{key}={value} 超出允许范围 {low}..{high}。"
            )
        limits[key] = value

    raw_entries = raw.get("entrypoints")
    if not isinstance(raw_entries, list) or not raw_entries:
        raise _runtime_error("entrypoints 必须是非空列表。")
    if len(raw_entries) > 16:
        raise _runtime_error("一个技能最多声明 16 个 entrypoint。")
    entrypoints: List[Dict[str, Any]] = []
    seen: set[str] = set()
    for index, item in enumerate(raw_entries):
        if not isinstance(item, dict):
            raise _runtime_error(f"entrypoints[{index}] 必须是键值对象。")
        entry_id = _as_text(item.get("id"))
        if not _ENTRY_ID_RE.match(entry_id):
            raise _runtime_error(
                f"entrypoints[{index}].id「{entry_id}」只能是小写字母、数字、_、-，长度 1..32。"
            )
        if entry_id in seen:
            raise _runtime_error(f"entrypoint id「{entry_id}」重复。")
        seen.add(entry_id)
        interpreter = _as_text(item.get("interpreter")).lower()
        if interpreter not in INTERPRETER_SUFFIX:
            raise _runtime_error(
                f"entrypoints[{entry_id}].interpreter 必须是 python / node / powershell。"
            )
        script = _normalize_script_path(item.get("script"))
        if not script.lower().endswith(INTERPRETER_SUFFIX[interpreter]):
            allowed = "、".join(INTERPRETER_SUFFIX[interpreter])
            raise _runtime_error(
                f"entrypoints[{entry_id}] 的脚本「{script}」与解释器 {interpreter} 不匹配（允许 {allowed}）。"
            )
        stdin_mode = _as_text(item.get("stdin") or "json").lower()
        if stdin_mode not in ("json", "text", "none"):
            raise _runtime_error(f"entrypoints[{entry_id}].stdin 必须是 json / text / none。")
        result_mode = _as_text(item.get("result") or "json").lower()
        if result_mode not in ("json", "text"):
            raise _runtime_error(f"entrypoints[{entry_id}].result 必须是 json / text。")
        cwd = _as_text(item.get("cwd") or "workspace").lower()
        if cwd not in ("workspace", "skill"):
            raise _runtime_error(f"entrypoints[{entry_id}].cwd 必须是 workspace / skill。")
        args_raw = item.get("args") or []
        if not isinstance(args_raw, list) or len(args_raw) > 16:
            raise _runtime_error(f"entrypoints[{entry_id}].args 必须是最多 16 项的列表。")
        args: List[str] = []
        for arg in args_raw:
            text = _as_text(arg)
            if len(text) > 256:
                raise _runtime_error(f"entrypoints[{entry_id}] 的静态参数过长。")
            args.append(text)
        env_raw = item.get("env") or {}
        if not isinstance(env_raw, dict) or len(env_raw) > 16:
            raise _runtime_error(f"entrypoints[{entry_id}].env 必须是最多 16 项的键值对象。")
        env: Dict[str, str] = {}
        for key, value in env_raw.items():
            key_text = str(key)
            if not _ENV_KEY_RE.match(key_text):
                raise _runtime_error(f"环境变量名「{key_text}」不合法。")
            if key_text.upper() in _FORBIDDEN_ENV:
                raise _runtime_error(f"不允许覆盖环境变量「{key_text}」。")
            value_text = _as_text(value)
            if len(value_text) > 1024:
                raise _runtime_error(f"环境变量「{key_text}」的值超过 1024 字符。")
            env[key_text] = value_text
        entrypoints.append(
            {
                "id": entry_id,
                "label": _as_text(item.get("label")) or entry_id,
                "interpreter": interpreter,
                "script": script,
                "args": args,
                "stdin": stdin_mode,
                "result": result_mode,
                "network": bool(item.get("network")),
                "cwd": cwd,
                "env": env,
            }
        )
    return {"limits": limits, "entrypoints": entrypoints}


def _report(
    target: List[Dict[str, Any]],
    *,
    field_name: str,
    value: Any,
    status: str,
    note: str,
) -> None:
    target.append(
        {"field": field_name, "value": value, "status": status, "note": note}
    )


def split_frontmatter(text: str) -> Tuple[Dict[str, Any], str, bool]:
    """拆出 YAML frontmatter。

    返回 ``(frontmatter, body, found)``。``found=False`` 表示没有 frontmatter
    或 YAML 不可解析 —— 调用方据此走"普通 Markdown"路径，并把这个事实写进
    import_report，而不是把整篇正文当成 YAML 猜。
    """
    stripped = text.lstrip("\ufeff")
    if not stripped.startswith("---"):
        return {}, text, False
    lines = stripped.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}, text, False
    end = None
    for index in range(1, len(lines)):
        if lines[index].strip() in ("---", "..."):
            end = index
            break
    if end is None or yaml is None:
        return {}, text, False
    raw = "\n".join(lines[1:end])
    try:
        data = yaml.safe_load(raw)
    except Exception:
        return {}, text, False
    if not isinstance(data, dict):
        return {}, text, False
    body = "\n".join(lines[end + 1:])
    return {str(k): v for k, v in data.items()}, body, True


def _as_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    return str(value).strip()


def _as_str_list(value: Any) -> List[str]:
    """``allowed-tools`` 规范里是空格分隔字符串，也兼容 YAML 列表。"""
    if value is None:
        return []
    if isinstance(value, str):
        return [part for part in re.split(r"[\s,]+", value) if part]
    if isinstance(value, (list, tuple, set)):
        out: List[str] = []
        for item in value:
            text = _as_text(item)
            if text and text not in out:
                out.append(text)
        return out
    return []


def _directory_of(filename: str) -> str:
    normalized = (filename or "").replace("\\", "/").strip("/")
    if "/" not in normalized:
        return ""
    return normalized.rsplit("/", 1)[0].strip("/")


def _filename_stem(filename: str) -> str:
    base = (filename or "").replace("\\", "/").rsplit("/", 1)[-1]
    if "." in base:
        base = base.rsplit(".", 1)[0]
    return base.strip()


def _heading_of(body: str) -> str:
    for line in body.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            candidate = stripped.lstrip("#").strip()
            if candidate:
                return candidate
    return ""


def _first_paragraph(body: str) -> str:
    paragraph: List[str] = []
    for line in body.splitlines():
        stripped = line.strip()
        if not stripped:
            if paragraph:
                break
            continue
        if stripped.startswith("#"):
            if paragraph:
                break
            continue
        paragraph.append(stripped)
    return " ".join(paragraph).strip()


def _detect_scripts(text: str) -> Tuple[bool, List[str]]:
    refs: List[str] = []
    for match in _SCRIPT_REF_RE.finditer(text or ""):
        ref = match.group(1).strip()
        if ref and ref not in refs:
            refs.append(ref)
    return bool(refs), refs


def is_mcp_config(document: Any) -> bool:
    if not isinstance(document, dict):
        return False
    for key in ("mcpServers", "mcp_servers", "mcp"):
        if key in document:
            value = document.get(key)
            if isinstance(value, (dict, list)) and value:
                return True
    return False


def parse_upload(filename: str, text: str) -> ImportedSkill:
    """按扩展名分派；不认识的格式直接报错，不做"猜一个格式"的兜底。"""
    suffix = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if suffix == "json":
        return parse_json_manifest(filename, text)
    if suffix == "md":
        return parse_agent_skill(filename, text)
    raise SkillImportError(
        f"本版只支持 .md（Agent Skills）与 .json（本机技能清单）"
        f"（收到「{filename or '未命名文件'}」）。"
    )


def _resolve_name(
    *,
    frontmatter_name: str,
    filename: str,
    body: str,
    report: List[Dict[str, Any]],
) -> Tuple[str, str]:
    """名称优先级：frontmatter.name → 目录名 → 首个标题 → 文件名主干 → 占位。

    绝不让 ``SKILL.md`` 的文件名主干变成技能名（规范里 name 必填；缺了要如实
    说明，而不是造一个叫「SKILL」的技能）。
    """
    if frontmatter_name:
        return frontmatter_name, "frontmatter"
    directory = _directory_of(filename)
    if directory:
        _report(
            report,
            field_name="name",
            value=None,
            status="defaulted",
            note=f"frontmatter 未提供 name，已按目录名「{directory}」兜底。",
        )
        return directory, "directory"
    heading = _heading_of(body)
    if heading:
        _report(
            report,
            field_name="name",
            value=None,
            status="defaulted",
            note=f"frontmatter 未提供 name，已按正文首个标题「{heading}」兜底。",
        )
        return heading, "heading"
    stem = _filename_stem(filename)
    if stem and stem.casefold() not in _FILENAME_STEMS_NOT_NAMES:
        _report(
            report,
            field_name="name",
            value=None,
            status="defaulted",
            note=f"frontmatter 未提供 name，已按文件名「{stem}」兜底。",
        )
        return stem, "filename"
    _report(
        report,
        field_name="name",
        value=None,
        status="defaulted",
        note=(
            "frontmatter 未提供 name，文件名（如 SKILL.md）不能当技能名，"
            "已使用占位名；建议补充 name 后重新导入。"
        ),
    )
    return _PLACEHOLDER_NAME, "placeholder"


def parse_agent_skill(filename: str, text: str) -> ImportedSkill:
    """解析 Agent Skills 的 ``SKILL.md``（有 frontmatter）或普通 Markdown。"""
    report: List[Dict[str, Any]] = []
    frontmatter, body, found = split_frontmatter(text)
    if not found:
        _report(
            report,
            field_name="frontmatter",
            value=None,
            status="absent",
            note="未检测到可解析的 YAML frontmatter，已按普通 Markdown 读取标题与首段。",
        )
    raw_name = _as_text(frontmatter.get("name"))
    name, name_source = _resolve_name(
        frontmatter_name=raw_name, filename=filename, body=body, report=report
    )
    description = _as_text(frontmatter.get("description")) or _first_paragraph(body)
    if not description:
        _report(
            report,
            field_name="description",
            value=None,
            status="empty",
            note="frontmatter 与正文都没有说明；技能列表里会显示为空。",
        )

    license_text = _as_text(frontmatter.get("license"))
    allowed_tools = _as_str_list(frontmatter.get("allowed-tools"))
    metadata_raw = frontmatter.get("metadata")
    if metadata_raw is not None and not isinstance(metadata_raw, dict):
        _report(
            report,
            field_name="metadata",
            value=metadata_raw,
            status="stored_raw",
            note="metadata 不是键值对象，已按原值保存，未参与任何判定。",
        )
        metadata_raw = {"_raw": metadata_raw}
    metadata = dict(metadata_raw or {})

    if license_text:
        _report(
            report,
            field_name="license",
            value=license_text,
            status="stored_raw",
            note="已保存展示；本机不做许可证合规校验。",
        )
    if allowed_tools:
        _report(
            report,
            field_name="allowed-tools",
            value=allowed_tools,
            status="stored_raw",
            note=(
                "已保存展示；本版不执行脚本，allowed-tools 不产生任何实际权限，"
                "也不会被当成所需能力。"
            ),
        )
    if metadata:
        _report(
            report,
            field_name="metadata",
            value=metadata,
            status="stored_raw",
            note="原样保存，未参与风险/能力判定。",
        )

    for key, value in frontmatter.items():
        if key in _MAPPED_KEYS:
            continue
        _report(
            report,
            field_name=key,
            value=value,
            status="unsupported",
            note="Agent Skills 扩展字段：本机只存原文，不理解其语义。",
        )

    runtime = normalize_runtime(frontmatter.get("runtime"))
    if runtime:
        entrypoints = runtime.get("entrypoints") or []
        _report(
            report,
            field_name="runtime",
            value=[entry["id"] for entry in entrypoints],
            status="mapped",
            note=(
                f"已识别 {len(entrypoints)} 个可执行入口；"
                "首次运行需要 Boss 确认，之后可记住该入口。"
            ),
        )

    has_scripts, script_refs = _detect_scripts(text)
    if runtime:
        has_scripts = True
        for entry in runtime.get("entrypoints") or []:
            if entry["script"] not in script_refs:
                script_refs.append(entry["script"])
    if has_scripts and not runtime:
        _report(
            report,
            field_name="scripts",
            value=script_refs,
            status="stored_raw",
            note=(
                "正文提到脚本，但 frontmatter 没有 runtime.entrypoints，"
                "本机不会执行这些脚本；只能当作资料。"
            ),
        )
    if any(tool.casefold() in ("bash", "shell", "python") for tool in allowed_tools):
        _report(
            report,
            field_name="allowed-tools.execution",
            value=allowed_tools,
            status="stored_raw",
            note=(
                "allowed-tools 只存原文，不产生执行权限；"
                "真正能跑的脚本必须在 runtime.entrypoints 里逐个声明。"
            ),
        )

    return ImportedSkill(
        name=name,
        description=description,
        version=_as_text(frontmatter.get("version")),
        content=text,
        format="agent-skill",
        origin=filename,
        license=license_text,
        allowed_tools=allowed_tools,
        metadata=metadata,
        raw_frontmatter=dict(frontmatter),
        has_scripts=has_scripts,
        script_refs=script_refs,
        files=[filename],
        name_source=name_source,
        import_report=report,
        runtime=runtime,
    )


def parse_json_manifest(filename: str, text: str) -> ImportedSkill:
    """解析本机 JSON 技能清单；MCP 配置在这里被识别并拒绝。"""
    try:
        document = json.loads(text)
    except json.JSONDecodeError as exc:
        raise SkillImportError(
            f"技能 JSON 解析失败：{exc.msg}（第 {exc.lineno} 行）。"
        ) from exc
    if not isinstance(document, dict):
        raise SkillImportError("技能 JSON 顶层必须是对象。")
    if is_mcp_config(document):
        raise McpConfigDetected(
            "这是 MCP server 配置，不是技能。MCP 请到工具中心配置；"
            "技能中心只接收 .md（Agent Skills）或 .json 技能清单。"
        )

    report: List[Dict[str, Any]] = []
    name = _as_text(document.get("name"))
    if not name:
        stem = _filename_stem(filename)
        directory = _directory_of(filename)
        if directory:
            name = directory
            source = "directory"
        elif stem and stem.casefold() not in _FILENAME_STEMS_NOT_NAMES:
            name = stem
            source = "filename"
        else:
            name = _PLACEHOLDER_NAME
            source = "placeholder"
        _report(
            report,
            field_name="name",
            value=None,
            status="defaulted",
            note=f"JSON 缺少 name，已按 {source} 兜底。",
        )
    else:
        source = "frontmatter"

    description = document.get("description") or ""
    if not isinstance(description, str):
        raise SkillImportError("技能 JSON 的 description 必须是字符串。")
    version = document.get("version") or ""
    if not isinstance(version, (str, int, float)):
        raise SkillImportError("技能 JSON 的 version 必须是字符串。")
    capabilities = document.get("capabilities") or []
    permissions = document.get("permissions") or []
    declared_risk = document.get("risk") or ""
    if not isinstance(declared_risk, str):
        raise SkillImportError("技能 JSON 的 risk 必须是字符串。")

    known = {
        "name", "description", "version", "capabilities", "permissions", "risk",
        "license", "allowed-tools", "allowed_tools", "metadata", "runtime",
        "scripts", "mcpServers",
    }
    for key, value in document.items():
        if key in known:
            continue
        _report(
            report,
            field_name=key,
            value=value,
            status="unsupported",
            note="本机 JSON 技能清单不识别该字段，只存原文。",
        )

    license_text = _as_text(document.get("license"))
    allowed_tools = _as_str_list(document.get("allowed-tools") or document.get("allowed_tools"))
    metadata_raw = document.get("metadata")
    metadata = dict(metadata_raw) if isinstance(metadata_raw, dict) else {}
    if license_text:
        _report(
            report,
            field_name="license",
            value=license_text,
            status="stored_raw",
            note="已保存展示；本机不做许可证合规校验。",
        )
    if allowed_tools:
        _report(
            report,
            field_name="allowed-tools",
            value=allowed_tools,
            status="stored_raw",
            note="已保存展示；allowed-tools 不影响执行权限，能跑的脚本见 runtime.entrypoints。",
        )
    if metadata:
        _report(
            report,
            field_name="metadata",
            value=metadata,
            status="stored_raw",
            note="原样保存，未参与风险/能力判定。",
        )

    runtime = normalize_runtime(document.get("runtime"))
    if runtime:
        entrypoints = runtime.get("entrypoints") or []
        _report(
            report,
            field_name="runtime",
            value=[entry["id"] for entry in entrypoints],
            status="mapped",
            note=(
                f"已识别 {len(entrypoints)} 个可执行入口；"
                "首次运行需要 Boss 确认，之后可记住该入口。"
            ),
        )

    return ImportedSkill(
        name=name,
        description=description,
        version=str(version),
        capabilities=list(capabilities) if isinstance(capabilities, (list, tuple)) else [],
        permissions=list(permissions) if isinstance(permissions, (list, tuple)) else [],
        declared_risk=declared_risk,
        content=None,
        format="liuhao-json",
        origin=filename,
        license=license_text,
        allowed_tools=allowed_tools,
        metadata=metadata,
        raw_frontmatter=dict(document),
        has_scripts=bool(runtime),
        script_refs=[entry["script"] for entry in (runtime.get("entrypoints") or [])],
        files=[filename],
        name_source=source,
        import_report=report,
        runtime=runtime,
    )
