"""聊天工具（技能页 B 栏）开关的持久化存储与分档表。

技能页透明化（``plans/skill-transparency.md`` §4/§5.4/§7.2）的一半是
“每个聊天工具都能开能关”：本模块只管**开关落盘 + 分档**，HTTP 层在
``src/gateway/skills.py``，注册闸在 ``src/ai/tools/kernel_tools.py``，
执行闸在 ``src/ai/tool_registry.py``。

分档（四档权限 + 三档确认）：

* A 档：只读 / 纯计算。默认开，开关不问（``confirm="none"``）。
* B 档：写文件 / 联网 / 查知识库 / 跑命令。默认关，开关不问。
* C 档：操控电脑 / 看屏幕。默认关，开关要正式确认（``confirm="formal"``，
  须 ``confirm=true`` + ``acknowledge_risk`` 复述档位，否则 409）。
* 未知工具（表里没有的 id）：一律按 C 档处理——默认关、要正式确认，
  绝不默认当安全；前端显示“来源未知 / 权限未知”。

例外 —— ``liuhao.web.search``（联网检索）：

开关本体**不在**本文件，在 ``src/ai/web_research.py`` 的
``data/privacy_settings.json``（``web_research_enabled``），与设置页是
同一个开关，不许做两套。本模块只登记它的分档（B 档 / 联网 / 默认关），
``is_chat_tool_enabled`` / ``set_chat_tool_enabled`` 遇到它时转调
``web_research``（函数内懒导入，避免 gateway → ai 的模块级循环）。

存储约定与 ``SkillStore`` 一致：路径经 ``resolve_store_path`` 解析
（优先 ``LIUHAO_WORKSPACE_ROOT``，其次 ``LIUHAO_DATA_DIR``，最后仓库根），
写入先写同目录临时文件再 ``os.replace``。文件损坏 / 读不懂时按“没有改过”
处理（只用默认档），不编一个空开关表假装全关——B/C 档默认就是关的，
A 档默认开是写明的档位，不是猜的。
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.common.data_paths import resolve_store_path

logger = logging.getLogger(__name__)

#: 开关文件的文件名（落在数据根 / 工作区根下）。
SETTINGS_FILENAME = "liuhao_chat_tool_settings.json"

#: 聊天工具 id（``Tool.tool_id``）→ 分档。``permissions`` 只取四档：
#: ``read`` 只读、``write`` 写文件/写数据、``network`` 联网、``exec`` 跑命令。
CHAT_TOOL_GRADES: Dict[str, Dict[str, Any]] = {
    # ---- A 档：只读 / 纯计算，默认开，开关不问 ----
    "liuhao.memory.search": {
        "grade": "A",
        "permissions": ["read"],
        "confirm": "none",
        "source_kind": "builtin",
        "default_enabled": True,
    },
    "liuhao.audit.query": {
        "grade": "A",
        "permissions": ["read"],
        "confirm": "none",
        "source_kind": "builtin",
        "default_enabled": True,
    },
    "liuhao.system.status": {
        "grade": "A",
        "permissions": ["read"],
        "confirm": "none",
        "source_kind": "builtin",
        "default_enabled": True,
    },
    "liuhao.plugin.calculate": {
        "grade": "A",
        "permissions": ["read"],
        "confirm": "none",
        "source_kind": "plugin",
        "default_enabled": True,
    },
    "liuhao.plugin.base64": {
        "grade": "A",
        "permissions": ["read"],
        "confirm": "none",
        "source_kind": "plugin",
        "default_enabled": True,
    },
    "liuhao.plugin.hash": {
        "grade": "A",
        "permissions": ["read"],
        "confirm": "none",
        "source_kind": "plugin",
        "default_enabled": True,
    },
    "liuhao.plugin.units": {
        "grade": "A",
        "permissions": ["read"],
        "confirm": "none",
        "source_kind": "plugin",
        "default_enabled": True,
    },
    "liuhao.plugin.timezone": {
        "grade": "A",
        "permissions": ["read"],
        "confirm": "none",
        "source_kind": "plugin",
        "default_enabled": True,
    },
    # A3r §7 能力自查打开的三个纯计算插件：无网络、无写文件，A 档默认开。
    "liuhao.plugin.uuid": {
        "grade": "A",
        "permissions": ["read"],
        "confirm": "none",
        "source_kind": "plugin",
        "default_enabled": True,
    },
    "liuhao.plugin.roman_numeral": {
        "grade": "A",
        "permissions": ["read"],
        "confirm": "none",
        "source_kind": "plugin",
        "default_enabled": True,
    },
    "liuhao.plugin.slug": {
        "grade": "A",
        "permissions": ["read"],
        "confirm": "none",
        "source_kind": "plugin",
        "default_enabled": True,
    },
    # ---- B 档：写文件 / 联网 / 查知识库 / 跑命令，默认关，开关不问 ----
    "liuhao.knowledge.search": {
        "grade": "B",
        "permissions": ["read"],
        "confirm": "none",
        "source_kind": "builtin",
        "default_enabled": False,
    },
    "liuhao.file.read": {
        "grade": "B",
        "permissions": ["read"],
        "confirm": "none",
        "source_kind": "builtin",
        "default_enabled": False,
    },
    "liuhao.file.list": {
        "grade": "B",
        "permissions": ["read"],
        "confirm": "none",
        "source_kind": "builtin",
        "default_enabled": False,
    },
    "liuhao.python.compute": {
        "grade": "B",
        "permissions": ["exec"],
        "confirm": "none",
        "source_kind": "builtin",
        "default_enabled": False,
    },
    "liuhao.personal.set_preference": {
        "grade": "B",
        "permissions": ["write"],
        "confirm": "none",
        "source_kind": "builtin",
        "default_enabled": False,
    },
    "liuhao.personal.add_fact": {
        "grade": "B",
        "permissions": ["write"],
        "confirm": "none",
        "source_kind": "builtin",
        "default_enabled": False,
    },
    "liuhao.personal.set_display_name": {
        "grade": "B",
        "permissions": ["write"],
        "confirm": "none",
        "source_kind": "builtin",
        "default_enabled": False,
    },
    "liuhao.reminder.manage": {
        "grade": "B",
        "permissions": ["write"],
        "confirm": "none",
        "source_kind": "builtin",
        "default_enabled": False,
    },
    # 联网检索：分档登记在这里，开关本体在 web_research（见模块 docstring）。
    "liuhao.web.search": {
        "grade": "B",
        "permissions": ["network"],
        "confirm": "none",
        "source_kind": "builtin",
        "default_enabled": False,
    },
    # 派给一队（7-3 Step 2）：触发队员模型调用与下游动作，B 档默认关，
    # Owner 在技能页开了才进聊天工具集。
    "liuhao.team.delegate": {
        "grade": "B",
        "permissions": ["exec"],
        "confirm": "none",
        "source_kind": "builtin",
        "default_enabled": False,
    },
    # 运行已安装技能（Q1 聊天接线）：触发技能脚本执行，B 档默认关，
    # Owner 在技能页开了才进聊天工具集。技能本身还要在技能中心启用，
    # 且首次运行须 Joseph 确认（SkillRuntime.run 的审批语义）。
    "liuhao.skill.run": {
        "grade": "B",
        "permissions": ["exec"],
        "confirm": "none",
        "source_kind": "builtin",
        "default_enabled": False,
    },
    # ---- C 档：操控电脑 / 看屏幕，默认关，开关要正式确认 ----
    "liuhao.automation.session.open": {
        "grade": "C",
        "permissions": ["exec"],
        "confirm": "formal",
        "source_kind": "builtin",
        "default_enabled": False,
    },
    "liuhao.automation.session.close": {
        "grade": "C",
        "permissions": ["exec"],
        "confirm": "formal",
        "source_kind": "builtin",
        "default_enabled": False,
    },
    "liuhao.automation.session.status": {
        "grade": "C",
        "permissions": ["read"],
        "confirm": "formal",
        "source_kind": "builtin",
        "default_enabled": False,
    },
    "liuhao.automation.emergency_stop": {
        "grade": "C",
        "permissions": ["exec"],
        "confirm": "formal",
        "source_kind": "builtin",
        "default_enabled": False,
    },
    "liuhao.automation.observation.capture": {
        "grade": "C",
        "permissions": ["read"],
        "confirm": "formal",
        "source_kind": "builtin",
        "default_enabled": False,
    },
    "liuhao.automation.observation.disable": {
        "grade": "C",
        "permissions": ["exec"],
        "confirm": "formal",
        "source_kind": "builtin",
        "default_enabled": False,
    },
    "liuhao.automation.observation.enable": {
        "grade": "C",
        "permissions": ["exec"],
        "confirm": "formal",
        "source_kind": "builtin",
        "default_enabled": False,
    },
    "liuhao.automation.observation.status": {
        "grade": "C",
        "permissions": ["read"],
        "confirm": "formal",
        "source_kind": "builtin",
        "default_enabled": False,
    },
}

#: 未知工具的分档：C 档、权限未知、要正式确认、默认关。
UNKNOWN_GRADE: Dict[str, Any] = {
    "grade": "C",
    "permissions": [],
    "confirm": "formal",
    "source_kind": "unknown",
    "default_enabled": False,
}

#: 联网检索的工具 id——开关本体在 ``web_research``，不在本文件。
WEB_SEARCH_TOOL_ID = "liuhao.web.search"

_WRITE_LOCK = threading.RLock()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def settings_path() -> str:
    """开关文件的绝对路径（与 SkillStore 同一套解析规则）。"""
    repo_root = os.path.dirname(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    )
    return resolve_store_path(
        SETTINGS_FILENAME,
        os.path.join(repo_root, f".{SETTINGS_FILENAME}"),
        workspace=True,
    )


def grade_for(tool_id: str) -> Dict[str, Any]:
    """返回某工具的分档；表里没有的一律按未知（C 档）处理。"""
    entry = CHAT_TOOL_GRADES.get(str(tool_id))
    if entry is None:
        return dict(UNKNOWN_GRADE)
    return dict(entry)


def _read_overrides(path: Optional[str] = None) -> Dict[str, bool]:
    """读取开关文件里的显式覆盖（tool_id → enabled）。

    文件缺失 / 损坏 / 不是对象时返回空 dict（只用默认档），并记一条警告；
    损坏的文件绝不静默改写——下一次显式开关时才会整体重写。
    """
    target = path or settings_path()
    try:
        with open(target, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as exc:
        logger.warning("chat tool settings unreadable (%s): %s", target, exc)
        return {}
    if not isinstance(payload, dict):
        return {}
    overrides = payload.get("tools")
    if not isinstance(overrides, dict):
        return {}
    return {
        str(tool_id): bool(value)
        for tool_id, value in overrides.items()
        if isinstance(value, bool)
    }


def _write_overrides(overrides: Dict[str, bool], path: Optional[str] = None) -> None:
    target = path or settings_path()
    directory = os.path.dirname(os.path.abspath(target))
    os.makedirs(directory, exist_ok=True)
    document = {"version": 1, "updated_at": _now(), "tools": dict(overrides)}
    payload = json.dumps(document, ensure_ascii=False, indent=2)
    handle = None
    tmp_path = ""
    try:
        fd, tmp_path = tempfile.mkstemp(
            prefix=".chat-tools-", suffix=".tmp", dir=directory
        )
        handle = os.fdopen(fd, "w", encoding="utf-8")
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
        handle.close()
        handle = None
        os.replace(tmp_path, target)
        tmp_path = ""
    except OSError as exc:
        raise OSError(f"聊天工具开关写入失败：{target}（{exc}）。本次修改没有落盘。") from exc
    finally:
        if handle is not None:
            try:
                handle.close()
            except OSError:  # pragma: no cover - 关闭失败无处可退
                pass
        if tmp_path:
            try:
                os.unlink(tmp_path)
            except OSError:  # pragma: no cover - 临时文件残留无处可退
                pass


def is_chat_tool_enabled(tool_id: str, *, path: Optional[str] = None) -> bool:
    """某聊天工具当前是否启用（显式覆盖优先，其次分档默认）。

    ``liuhao.web.search`` 走 ``web_research.enabled()``（与设置页同一开关，
    环境变量可强制覆盖）；未知工具一律返回 False（默认关）。
    """
    tool_id = str(tool_id)
    if tool_id == WEB_SEARCH_TOOL_ID:
        from src.ai import web_research

        return bool(web_research.enabled())
    grade = grade_for(tool_id)
    overrides = _read_overrides(path)
    if tool_id in overrides:
        return overrides[tool_id]
    return bool(grade["default_enabled"])


def set_chat_tool_enabled(
    tool_id: str, enabled: bool, *, path: Optional[str] = None
) -> bool:
    """持久化某聊天工具的开关；返回改动前的有效值。

    未知工具拒绝写入（``ValueError``）——开关只能对登记在案的工具操作，
    前端/网关层对未知 id 应返回 404 而不是悄悄建一条。
    ``liuhao.web.search`` 转调 ``web_research.set_research_enabled``。
    """
    tool_id = str(tool_id)
    if tool_id not in CHAT_TOOL_GRADES:
        raise ValueError(f"未知聊天工具：{tool_id}，没有可开关的登记。")
    previous = is_chat_tool_enabled(tool_id, path=path)
    if tool_id == WEB_SEARCH_TOOL_ID:
        from src.ai import web_research

        web_research.set_research_enabled(bool(enabled))
        return previous
    with _WRITE_LOCK:
        overrides = _read_overrides(path)
        overrides[tool_id] = bool(enabled)
        _write_overrides(overrides, path)
    return previous


def effective_settings(path: Optional[str] = None) -> Dict[str, bool]:
    """全部登记工具的有效开关（显式覆盖 + 默认档），供列表端点与测试用。"""
    overrides = _read_overrides(path)
    result: Dict[str, bool] = {}
    for tool_id, grade in CHAT_TOOL_GRADES.items():
        if tool_id == WEB_SEARCH_TOOL_ID:
            from src.ai import web_research

            result[tool_id] = bool(web_research.enabled())
        elif tool_id in overrides:
            result[tool_id] = overrides[tool_id]
        else:
            result[tool_id] = bool(grade["default_enabled"])
    return result


def known_tool_ids() -> List[str]:
    """已登记的聊天工具 id（分档表里的全部）。"""
    return list(CHAT_TOOL_GRADES.keys())


__all__ = [
    "SETTINGS_FILENAME",
    "CHAT_TOOL_GRADES",
    "UNKNOWN_GRADE",
    "WEB_SEARCH_TOOL_ID",
    "settings_path",
    "grade_for",
    "is_chat_tool_enabled",
    "set_chat_tool_enabled",
    "effective_settings",
    "known_tool_ids",
]
