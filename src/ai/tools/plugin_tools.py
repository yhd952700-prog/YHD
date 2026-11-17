"""聊天可调用的插件安全子集（0.4.6 C 组，5-4 一期）。

只接 ``plugins/`` 里**纯计算、无外部依赖、无写文件**的子集，直接委托给
插件类本身（不是把逻辑重写一份），每件都标明来源插件与权限：

================  ====================  ================================
聊天工具           来源插件             权限
================  ====================  ================================
``calculate``      builtin.CalculatorTool  纯计算（AST 求值，无 eval）
``base64_text``    plugins/base64       纯计算（只编解码文本）
``hash_text``      plugins/hash         纯计算（只算文本摘要）
``convert_units``  plugins/units        纯计算（查表换算）
``timezone_time``  plugins/timezone     纯计算（时区换算/报时）
``generate_uuid``  plugins/uuid         纯计算（生成 UUID，不碰网络/文件）
``roman_numeral``  plugins/roman_numeral 纯计算（阿拉伯数字 ↔ 罗马数字）
``make_slug``      plugins/slug         纯计算（标题转 URL slug）
``knowledge_search`` builtin.KnowledgeSearchTool 读本地知识库向量索引
``web_search``     builtin.WebResearchTool 读公开联网检索源
================  ====================  ================================

刻意**没接**的（审过，留档）：

* ``qrcode``：插件生成二维码要么写文件，要么依赖未装的 ``qrcode`` 库；
  无库时的 ``_generate_basic`` 回退在插件注释里自己写明“不是真二维码，
  只是视觉占位”，接进来就是假数据。等补了 ``qrcode`` 依赖（许可核实后
  登记）+ 图片回传通道再接。
* ``calendar`` / ``todo`` / ``notes`` 等：带状态、要读写文件，本轮不接。
* 碰文件/网络/跑命令的插件（``web_scraper``、``weather``、``translate``、
  ``rss``、``currency`` 等）：下一轮走低完整性进程 + 审批 + 审计再接。

开关：这些工具由 ``make_tools()`` 注册进聊天提示词；网关
``POST /v1/skills/{id}/run`` 是另一条线——那是人手动跑“已安装技能包”
的执行面，见 ``docs/skills-knowledge-memory.md`` 的分工说明。
"""

from __future__ import annotations

import json
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..tool_registry import Tool

#: 工具 id → 来源插件（前端技能页与文档共用同一张表）。
PLUGIN_TOOL_CATALOG: List[Dict[str, Any]] = [
    {
        "id": "liuhao.plugin.calculate",
        "name": "calculate",
        "source": "src/ai/tools/builtin.py:CalculatorTool",
        "description": "安全求值数学表达式（AST 求值，无 eval）",
        "permission": "纯计算，无网络、无写文件",
        "available": True,
        "grade": "A",
        "permissions": ["read"],
        "confirm": "none",
        "source_kind": "plugin",
    },
    {
        "id": "liuhao.plugin.base64",
        "name": "base64_text",
        "source": "plugins/base64:Base64Plugin",
        "description": "Base64 / Base64URL 编解码文本",
        "permission": "纯计算，无网络、无写文件",
        "available": True,
        "grade": "A",
        "permissions": ["read"],
        "confirm": "none",
        "source_kind": "plugin",
    },
    {
        "id": "liuhao.plugin.hash",
        "name": "hash_text",
        "source": "plugins/hash:HashPlugin",
        "description": "算文本的 MD5 / SHA-1 / SHA-256 / SHA-512 摘要",
        "permission": "纯计算，无网络、无写文件",
        "available": True,
        "grade": "A",
        "permissions": ["read"],
        "confirm": "none",
        "source_kind": "plugin",
    },
    {
        "id": "liuhao.plugin.units",
        "name": "convert_units",
        "source": "plugins/units:UnitsPlugin",
        "description": "单位换算（长度/重量/温度/体积/面积/速度/时间/数据量）",
        "permission": "纯计算，无网络、无写文件",
        "available": True,
        "grade": "A",
        "permissions": ["read"],
        "confirm": "none",
        "source_kind": "plugin",
    },
    {
        "id": "liuhao.plugin.timezone",
        "name": "timezone_time",
        "source": "plugins/timezone:TimezonePlugin",
        "description": "时区换算、报时（convert / now）",
        "permission": "纯计算，无网络、无写文件",
        "available": True,
        "grade": "A",
        "permissions": ["read"],
        "confirm": "none",
        "source_kind": "plugin",
    },
    {
        "id": "liuhao.plugin.uuid",
        "name": "generate_uuid",
        "source": "plugins/uuid:UuidPlugin",
        "description": "生成 UUID（v1/v3/v4/v5/v7，默认 v4）",
        "permission": "纯计算，无网络、无写文件",
        "available": True,
        "grade": "A",
        "permissions": ["read"],
        "confirm": "none",
        "source_kind": "plugin",
    },
    {
        "id": "liuhao.plugin.roman_numeral",
        "name": "roman_numeral",
        "source": "plugins/roman_numeral:RomanNumeralPlugin",
        "description": "阿拉伯数字 ↔ 罗马数字（1–3999）",
        "permission": "纯计算，无网络、无写文件",
        "available": True,
        "grade": "A",
        "permissions": ["read"],
        "confirm": "none",
        "source_kind": "plugin",
    },
    {
        "id": "liuhao.plugin.slug",
        "name": "make_slug",
        "source": "plugins/slug:SlugPlugin",
        "description": "标题转 URL slug（去停用词、可限长度）",
        "permission": "纯计算，无网络、无写文件",
        "available": True,
        "grade": "A",
        "permissions": ["read"],
        "confirm": "none",
        "source_kind": "plugin",
    },
    {
        "id": "liuhao.plugin.qrcode",
        "name": "qrcode",
        "source": "plugins/qrcode:QRCodePlugin",
        "description": "生成二维码图片",
        "permission": "纯计算，但要出图（需文件/图片通道）",
        "available": False,
        "grade": "B",
        "permissions": ["write"],
        "confirm": "none",
        "source_kind": "plugin",
        "unavailable_reason": (
            "未接线：插件要么写文件出图，要么依赖未装的 qrcode 库；"
            "无库回退是假图案（插件自己注明不是真二维码）。"
            "等补依赖 + 图片回传通道后再接。"
        ),
    },
    {
        "id": "liuhao.knowledge.search",
        "name": "knowledge_search",
        "source": "src/ai/tools/builtin.py:KnowledgeSearchTool",
        "description": "搜本地知识库（向量检索）；没配 embedding 时诚实报错",
        "permission": "只读本地知识库，无网络、无写文件",
        "available": True,
        "grade": "B",
        "permissions": ["read"],
        "confirm": "none",
        "source_kind": "builtin",
    },
    {
        "id": "liuhao.web.search",
        "name": "web_search",
        "source": "src/ai/tools/builtin.py:WebResearchTool",
        "description": "联网查东西（Hacker News / GitHub / arXiv，带来源与时间）",
        "permission": "只读公开检索源；默认关闭，需用户在设置里打开",
        "available": True,
        "grade": "B",
        "permissions": ["network"],
        "confirm": "none",
        "source_kind": "builtin",
    },
]

_PLUGIN_SINGLETONS: Dict[str, Any] = {}

#: 顶层 ``plugins/`` 包在仓库根下。不能直接 ``import plugins``：``src/``
#: 也在 sys.path 上，``src/plugins``（沙箱后端）会先占住 ``plugins`` 这个
#: 顶层名，且一旦被别的模块 import 过就缓存在 ``sys.modules`` 里换不掉。
#: 因此按文件位置把仓库根下的 ``plugins/`` 以别名
#: ``liuhao_chat_plugins`` 单独挂成一个包再导入——插件模块里的相对导入
#: （``from ..plugin_base import ...``）按 ``__package__`` 解析，别名下
#: 一样能找到 ``plugin_base``，与 ``src.plugins`` 互不干扰。
_REPO_ROOT = str(Path(__file__).resolve().parents[3])
_ALIAS_PKG = "liuhao_chat_plugins"


def _ensure_alias_package() -> None:
    import importlib.util
    import sys

    if _ALIAS_PKG in sys.modules:
        return
    plugins_dir = str(Path(_REPO_ROOT) / "plugins")
    spec = importlib.util.spec_from_file_location(
        _ALIAS_PKG,
        str(Path(plugins_dir) / "__init__.py"),
        submodule_search_locations=[plugins_dir],
    )
    if spec is None or spec.loader is None:  # pragma: no cover - 防御性分支
        raise ImportError("cannot load plugins package from " + plugins_dir)
    module = importlib.util.module_from_spec(spec)
    sys.modules[_ALIAS_PKG] = module
    spec.loader.exec_module(module)


def _plugin(name: str, cls_path: str):
    """懒加载并激活一个插件实例（失败时抛错，不静默降级）。

    ``cls_path`` 形如 ``"base64.base64_plugin.Base64Plugin"``，即
    ``plugins/<name>/<name>_plugin.py`` 里的类；实际按别名包导入。
    """
    if name not in _PLUGIN_SINGLETONS:
        import importlib

        _ensure_alias_package()
        module_name, class_name = cls_path.rsplit(".", 1)
        module = importlib.import_module(f"{_ALIAS_PKG}.{module_name}")
        plugin = getattr(module, class_name)()
        plugin.initialize()
        plugin.activate()
        _PLUGIN_SINGLETONS[name] = plugin
    return _PLUGIN_SINGLETONS[name]


def _jsonable(value: Any) -> Any:
    """插件返回的 dataclass 转 dict；bytes 字段去掉（摘要已另有 hex 字段）。"""
    if is_dataclass(value):
        data = asdict(value)
        return {k: v for k, v in data.items() if not isinstance(v, (bytes, bytearray))}
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    return value


def _dump(value: Any) -> str:
    return json.dumps(_jsonable(value), ensure_ascii=False, default=str)


def calculate(expression: str = "", **kwargs: Any) -> str:
    """安全求值数学表达式。"""
    from .builtin import CalculatorTool

    expr = expression or kwargs.get("expr") or ""
    if not str(expr).strip():
        return "请提供 expression 参数（数学表达式，如 '2 + 3 * 4'）。"
    result = CalculatorTool().execute(expression=str(expr))
    if not result.ok:
        return f"计算失败：{result.error}"
    return _dump(result.output)


def base64_text(text: str = "", operation: str = "encode", variant: str = "standard", **kwargs: Any) -> str:
    """Base64 / Base64URL 编解码文本（只处理文本，不碰文件）。"""
    plugin = _plugin("base64", "base64.base64_plugin.Base64Plugin")
    body = text or kwargs.get("data") or ""
    op = (operation or "encode").strip().lower()
    if op == "encode":
        return _dump(plugin.encode(body, variant=variant))
    if op == "decode":
        return _dump(plugin.decode(body, variant=variant))
    return "operation 只能是 encode 或 decode。"


def hash_text(text: str = "", algorithm: str = "sha256", **kwargs: Any) -> str:
    """算一段文本的哈希摘要（不读文件）。"""
    plugin = _plugin("hash", "hash.hash_plugin.HashPlugin")
    body = text or kwargs.get("data") or ""
    algo = (algorithm or "sha256").strip().lower()
    if algo not in ("md5", "sha1", "sha256", "sha512"):
        return "algorithm 只能是 md5 / sha1 / sha256 / sha512。"
    return _dump(plugin.hash(body, algorithm=algo))


def convert_units(value: float = 0, from_unit: str = "", to_unit: str = "", **kwargs: Any) -> str:
    """单位换算（查表，无网络）。"""
    plugin = _plugin("units", "units.units_plugin.UnitsPlugin")
    src = from_unit or kwargs.get("from") or ""
    dst = to_unit or kwargs.get("to") or ""
    if not src or not dst:
        return "请提供 from_unit 与 to_unit 参数（如 km → mi）。"
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "value 必须是数字。"
    return _dump(plugin.convert(number, src, dst))


def timezone_time(
    time_str: str = "",
    from_tz: str = "UTC",
    to_tz: str = "Asia/Shanghai",
    operation: str = "convert",
    **kwargs: Any,
) -> str:
    """时区换算（convert）或报时（now）。"""
    plugin = _plugin("timezone", "timezone.timezone_plugin.TimezonePlugin")
    op = (operation or "convert").strip().lower()
    if op == "now":
        tz = to_tz or kwargs.get("timezone") or "Asia/Shanghai"
        return _dump(plugin.get_current_time(tz))
    body = time_str or kwargs.get("time") or ""
    if not body:
        return "请提供 time_str 参数（如 '2026-11-12 12:00:00'）。"
    return _dump(plugin.convert(body, from_tz or "UTC", to_tz or "Asia/Shanghai"))


def generate_uuid(version: int = 4, **kwargs: Any) -> str:
    """生成一个 UUID（默认 v4 随机；只算不记，不碰网络/文件）。"""
    plugin = _plugin("uuid", "uuid.uuid_plugin.UuidPlugin")
    try:
        number = int(version)
    except (TypeError, ValueError):
        return "version 只能是 1 / 3 / 4 / 5 / 7。"
    if number not in (1, 3, 4, 5, 7):
        return "version 只能是 1 / 3 / 4 / 5 / 7。"
    try:
        return str(plugin.generate(version=number))
    except Exception as exc:  # noqa: BLE001 - 插件异常转人话，不抛裸错
        return f"生成 UUID 失败：{exc}"


def roman_numeral(
    value: Any = None, operation: str = "to_roman", **kwargs: Any
) -> str:
    """阿拉伯数字 ↔ 罗马数字（1–3999；超范围诚实报错，不编）。"""
    plugin = _plugin(
        "roman_numeral", "roman_numeral.roman_numeral_plugin.RomanNumeralPlugin"
    )
    op = (operation or "to_roman").strip().lower()
    if op == "from_roman":
        body = str(value or kwargs.get("roman") or "").strip()
        if not body:
            return "请提供 value 参数（罗马数字，如 'MMXXIV'）。"
        return _dump(plugin.from_roman(body))
    raw = value if value is not None else kwargs.get("number")
    try:
        number = int(raw)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return "value 必须是 1–3999 的整数。"
    return _dump(plugin.to_roman(number))


def make_slug(text: str = "", **kwargs: Any) -> str:
    """标题转 URL slug（纯文本处理，不碰文件）。"""
    plugin = _plugin("slug", "slug.slug_plugin.SlugPlugin")
    body = str(text or kwargs.get("title") or "").strip()
    if not body:
        return "请提供 text 参数（要转 slug 的标题）。"
    max_length = kwargs.get("max_length")
    try:
        limit = int(max_length) if max_length is not None else None
    except (TypeError, ValueError):
        return "max_length 必须是整数。"
    return _dump(
        plugin.generate(
            body,
            separator=kwargs.get("separator"),
            case=kwargs.get("case"),
            max_length=limit,
        )
    )


def knowledge_search(query: str = "", top_k: int = 4, **kwargs: Any) -> str:
    """搜本地知识库；没配 embedding 时诚实报错，不编结果。"""
    from .builtin import KnowledgeSearchTool

    q = query or kwargs.get("q") or ""
    if not str(q).strip():
        return "请提供 query 参数（要搜的问题）。"
    result = KnowledgeSearchTool().execute(query=str(q), top_k=int(top_k))
    if not result.ok:
        return f"知识库检索不可用：{result.error}"
    return _dump(result.output)


def web_search(query: str = "", max_results: int = 6, **kwargs: Any) -> str:
    """联网查东西；未开启或检索失败时诚实说明，不编来源。"""
    from .builtin import WebResearchTool

    q = query or kwargs.get("q") or ""
    if not str(q).strip():
        return "请提供 query 参数（要查的问题）。"
    result = WebResearchTool().execute(query=str(q), max_results=int(max_results))
    if not result.ok:
        return f"联网检索不可用：{result.error}"
    return _dump(result.output)


def build_plugin_tools() -> List[Tool]:
    """插件安全子集 + 知识库/联网检索的聊天工具（供 ``make_tools`` 注册）。"""
    specs = [
        (
            "liuhao.plugin.calculate", "calculate", "1.0.0",
            "安全求值数学表达式（AST 求值，无 eval）：如 2 + 3 * 4",
            "plugin.calculate",
            {"type": "object", "properties": {"expression": {"type": "string"}}, "required": ["expression"]},
            calculate,
        ),
        (
            "liuhao.plugin.base64", "base64_text", "1.0.0",
            "Base64 / Base64URL 编解码文本（来源插件 plugins/base64，只处理文本）",
            "plugin.base64",
            {"type": "object", "properties": {
                "text": {"type": "string"},
                "operation": {"type": "string", "enum": ["encode", "decode"], "default": "encode"},
                "variant": {"type": "string", "enum": ["standard", "url_safe"], "default": "standard"},
            }, "required": ["text"]},
            base64_text,
        ),
        (
            "liuhao.plugin.hash", "hash_text", "1.0.0",
            "算文本的哈希摘要 md5/sha1/sha256/sha512（来源插件 plugins/hash）",
            "plugin.hash",
            {"type": "object", "properties": {
                "text": {"type": "string"},
                "algorithm": {"type": "string", "enum": ["md5", "sha1", "sha256", "sha512"], "default": "sha256"},
            }, "required": ["text"]},
            hash_text,
        ),
        (
            "liuhao.plugin.units", "convert_units", "1.0.0",
            "单位换算（来源插件 plugins/units）：如 10 km → mi",
            "plugin.units",
            {"type": "object", "properties": {
                "value": {"type": "number"},
                "from_unit": {"type": "string"},
                "to_unit": {"type": "string"},
            }, "required": ["value", "from_unit", "to_unit"]},
            convert_units,
        ),
        (
            "liuhao.plugin.timezone", "timezone_time", "1.0.0",
            "时区换算或报时（来源插件 plugins/timezone）：operation=convert/now",
            "plugin.timezone",
            {"type": "object", "properties": {
                "time_str": {"type": "string"},
                "from_tz": {"type": "string", "default": "UTC"},
                "to_tz": {"type": "string", "default": "Asia/Shanghai"},
                "operation": {"type": "string", "enum": ["convert", "now"], "default": "convert"},
            }},
            timezone_time,
        ),
        (
            "liuhao.plugin.uuid", "generate_uuid", "1.0.0",
            "生成一个 UUID（来源插件 plugins/uuid；默认 v4）",
            "plugin.uuid",
            {"type": "object", "properties": {
                "version": {"type": "integer", "enum": [1, 3, 4, 5, 7], "default": 4},
            }},
            generate_uuid,
        ),
        (
            "liuhao.plugin.roman_numeral", "roman_numeral", "1.0.0",
            "阿拉伯数字 ↔ 罗马数字（来源插件 plugins/roman_numeral；1–3999）",
            "plugin.roman_numeral",
            {"type": "object", "properties": {
                "value": {"type": ["integer", "string"]},
                "operation": {"type": "string", "enum": ["to_roman", "from_roman"], "default": "to_roman"},
            }, "required": ["value"]},
            roman_numeral,
        ),
        (
            "liuhao.plugin.slug", "make_slug", "1.0.0",
            "标题转 URL slug（来源插件 plugins/slug；纯文本处理）",
            "plugin.slug",
            {"type": "object", "properties": {
                "text": {"type": "string"},
                "separator": {"type": "string", "default": "-"},
                "case": {"type": "string", "enum": ["lower", "upper", "title"]},
                "max_length": {"type": "integer"},
            }, "required": ["text"]},
            make_slug,
        ),
        (
            "liuhao.knowledge.search", "knowledge_search", "1.0.0",
            "搜本地知识库（向量检索）；没配 embedding 时诚实报错，不编结果",
            "knowledge.search",
            {"type": "object", "properties": {
                "query": {"type": "string"},
                "top_k": {"type": "integer", "default": 4},
            }, "required": ["query"]},
            knowledge_search,
        ),
        (
            "liuhao.web.search", "web_search", "1.0.0",
            "联网查东西（Hacker News / GitHub / arXiv，返回带来源与时间的真实结果）；未开启时诚实报错",
            "web.search",
            {"type": "object", "properties": {
                "query": {"type": "string"},
                "max_results": {"type": "integer", "default": 6},
            }, "required": ["query"]},
            web_search,
        ),
    ]
    return [
        Tool(
            tool_id=tool_id,
            name=name,
            version=version,
            description=description,
            capability=capability,
            schema=schema,
            fn=fn,
            risk="LOW",
        )
        for tool_id, name, version, description, capability, schema, fn in specs
    ]


__all__ = [
    "PLUGIN_TOOL_CATALOG",
    "build_plugin_tools",
    "calculate",
    "base64_text",
    "generate_uuid",
    "hash_text",
    "convert_units",
    "make_slug",
    "roman_numeral",
    "timezone_time",
    "knowledge_search",
    "web_search",
]
