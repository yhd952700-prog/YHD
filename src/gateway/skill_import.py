"""技能包解析：把外来格式收敛成 ``SkillStore`` 能存的一份字段。

支持两种输入：

1. **Agent Skills**（``SKILL.md`` + YAML frontmatter）。按规范读取
   ``name`` / ``description`` / ``license`` / ``allowed-tools`` / ``metadata``，
   其余未知键**原样保留**并写进 ``import_report``，绝不假装支持。
2. **本机 JSON 技能清单**（``name`` / ``description`` / ``capabilities`` 等）。

明确**不**支持、并在错误里说清楚的：

* MCP server 配置（``mcpServers``）—— 那是工具源，不是技能，正确入口是工具中心；
* zip / 目录包 —— 解包与多文件导入本版没做，导入端会返回 415。

诚实边界
--------
* ``execution_allowed`` 恒为 ``False``：本模块只做"读懂并登记"，不执行任何
  脚本。``allowed-tools`` / ``scripts/`` 引用只被**记录**，不产生任何权限。
* 单文件导入时无法确认目录里还有什么，因此 ``has_scripts`` 只表示"正文里
  引用了脚本路径"，不代表已校验脚本内容。
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
_MAPPED_KEYS = ("name", "description", "license", "allowed-tools", "metadata")

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

    @property
    def execution_allowed(self) -> bool:
        # 本轮拍板：任何导入的技能都不执行脚本。
        return False


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

    has_scripts, script_refs = _detect_scripts(text)
    if has_scripts:
        _report(
            report,
            field_name="scripts",
            value=script_refs,
            status="stored_raw",
            note="检测到脚本引用；技能可以登记，但本机未启用脚本执行。",
        )
    if any(tool.casefold() in ("bash", "shell", "python") for tool in allowed_tools):
        _report(
            report,
            field_name="allowed-tools.execution",
            value=allowed_tools,
            status="unsupported",
            note="声明了命令类工具，但本机不执行；实际执行留到下一阶段。",
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
            note="已保存展示；本版不执行脚本，不产生实际权限。",
        )
    if metadata:
        _report(
            report,
            field_name="metadata",
            value=metadata,
            status="stored_raw",
            note="原样保存，未参与风险/能力判定。",
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
        has_scripts=False,
        files=[filename],
        name_source=source,
        import_report=report,
    )
