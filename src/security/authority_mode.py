"""显式「完全授权模式」读取层（A 步与 G1–G5 埋点共用）。

设计约束（来自用户决策）：

* 模式是**启动期快照**，不做运行中热切。热切会在一次任务的中途产生
  「半套权限」窗口：同一任务前半段需要确认、后半段又不需要，审计也无法
  把这次任务归到单一模式下。
* 模式**不通过清空** ``LIUHAO_KERNEL_POLICY_ENFORCE`` 实现。清空会让
  ``/v1/ready`` 判 ``UNARMED_PRODUCTION``（``src/gateway/health.py``），
  桌面壳/托盘会一直显示出错。模式是另一根轴：武装保持不变，闸对「用户自己
  手动派出的任务」不再生效，但对系统主体、定时任务、自主接活的调用仍然生效。
* 读不到配置时一律回落到 ``standard`` —— 权限开关的失败方向必须更严。

来源优先级：

1. ``LIUHAO_AUTHORITY_MODE`` 环境变量（``full`` / ``standard``）；
2. 数据目录 ``authority_mode.json``（设置页写入）；
3. 缺省 ``standard``。

「继承」的边界（B 步，2026-11-11 老板拍板）：全权模式对**所有执行主体**生效
—— 用户手动派活、AI 员工、定时触发、自主接活、多级委派，一律不再逐次等审批。
本模块因此只回答「现在是不是全权模式」，不再区分调用来源；来源与归属由
``src/kernels/_crosscutting`` 的 ``task_attribution`` 记进审计（哪个员工、哪个
任务、什么 correlation_id）。

收回是例外：**收回全权立即生效，不需要重启**；开启全权才需要重启。收紧方向
可以热切，放宽方向不行 —— 这样既不会出现「半套权限」的任务窗口，又保证任何
时候都能一键把权限拿回来。
"""

from __future__ import annotations

import json
import logging
import os
import threading
from pathlib import Path
from typing import Any

from ..common.atomic_write import atomic_write_text, read_text_with_retry

logger = logging.getLogger(__name__)

#: 环境变量名：显式声明运行模式（最高优先级，运维/测试用）。
ENV_VAR = "LIUHAO_AUTHORITY_MODE"

#: 设置页写入的落盘文件的显式覆盖变量。
SETTINGS_FILE_ENV = "LIUHAO_AUTHORITY_MODE_FILE"

#: 标准模式（默认）：现有策略闸与确认流程原样生效。
MODE_STANDARD = "standard"

#: 完全授权模式：对所有执行主体放开高风险动作，审计不降级。
MODE_FULL = "full"

_VALID_MODES = frozenset({MODE_STANDARD, MODE_FULL})

_DEFAULT_SETTINGS_PATH = Path(__file__).resolve().parents[2] / "data" / "authority_mode.json"

_lock = threading.RLock()
#: 启动期快照。``None`` 表示尚未读取。
_snapshot: str | None = None
#: 快照的来源，供诊断说明「这个值是哪儿来的」。
_snapshot_source: str = "default"


def parse_mode(value: str | None) -> str:
    """把任意输入归一化为合法模式；非法/空值 → ``standard``（fail-safe）。"""
    text = (value or "").strip().lower()
    return text if text in _VALID_MODES else MODE_STANDARD


def _settings_path() -> Path:
    from ..common.data_paths import resolve_store_path

    return Path(
        resolve_store_path(
            "authority_mode.json",
            _DEFAULT_SETTINGS_PATH,
            env_var=SETTINGS_FILE_ENV,
        )
    )


def stored_mode() -> str:
    """设置页保存的模式。缺失/损坏一律当作标准模式。"""
    try:
        payload = json.loads(read_text_with_retry(_settings_path()))
    except (FileNotFoundError, OSError, ValueError):
        return MODE_STANDARD
    if not isinstance(payload, dict):
        return MODE_STANDARD
    return parse_mode(str(payload.get("mode") or ""))


def set_stored_mode(mode: str, *, updated_by: str = "") -> dict[str, Any]:
    """持久化设置页的选择；**立即生效的是下一次启动**，本进程不热切。

    写盘只改变「下次启动读到的值」。当前进程的 :func:`current_mode` 仍是启动
    快照 —— 这正是用户要求的「切换后必须重启生效」。
    """
    import time

    target = parse_mode(mode)
    path = _settings_path()
    payload: dict[str, Any] = {}
    try:
        loaded = json.loads(read_text_with_retry(path))
        if isinstance(loaded, dict):
            payload = loaded
    except (FileNotFoundError, OSError, ValueError):
        payload = {}
    payload["mode"] = target
    payload["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    if updated_by:
        payload["updated_by"] = updated_by
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(path, json.dumps(payload, ensure_ascii=False, indent=2))
    return describe()


def _resolve() -> tuple[str, str]:
    """求值一次并说明来源。环境变量优先，其次落盘，最后缺省。"""
    raw_env = os.environ.get(ENV_VAR)
    if raw_env is not None and raw_env.strip():
        return parse_mode(raw_env), "env"
    path = _settings_path()
    try:
        path_exists = path.exists()
    except OSError:  # pragma: no cover - defensive
        path_exists = False
    if path_exists:
        return stored_mode(), "file"
    return MODE_STANDARD, "default"


def current_mode() -> str:
    """当前进程生效的模式。

    只在首次调用时求值并缓存 —— 之后再改环境变量或设置页都不会改变本进程的
    模式，这就是「切换需重启」的落点（避免半套权限窗口期）。
    """
    global _snapshot, _snapshot_source
    with _lock:
        if _snapshot is None:
            _snapshot, _snapshot_source = _resolve()
        return _snapshot


def is_full() -> bool:
    """是否处于完全授权模式（启动快照）。"""
    return current_mode() == MODE_FULL


def reload() -> None:
    """丢弃快照并重新求值。

    仅供测试与「进程启动完成后立刻初始化」使用；产品运行期没有任何调用点，
    以保证不出现热切。
    """
    global _snapshot
    with _lock:
        _snapshot = None


def revoke_now(reason: str = "") -> None:
    """立刻收回全权（**不需要重启**）。

    收紧方向允许热切：即使进程启动时是 ``full``，这里也能当场把本进程压回
    ``standard``。调用方（设置页总闸）应同时把落盘值写成 ``standard``，否则
    下次重启又会读回 ``full``。

    ``reason`` 只用于日志；审计由调用方写 ``AUTHORITY_MODE_CHANGED``。
    """
    global _snapshot, _snapshot_source
    with _lock:
        _snapshot = MODE_STANDARD
        _snapshot_source = "kill-switch"
    logger.warning("full authority revoked at runtime (%s)", reason or "no reason given")


def describe() -> dict[str, Any]:
    """给接口/设置页/诊断用的快照（不含任何机密）。"""
    effective = current_mode()
    # 重启后会生效的值：与启动期同一个求值顺序，但不走快照缓存。
    pending, pending_source = _resolve()
    return {
        "mode": effective,
        "mode_label": "完全授权" if effective == MODE_FULL else "标准",
        "source": _snapshot_source,
        "stored_mode": stored_mode(),
        "next_mode": pending,
        "next_source": pending_source,
        "restart_required": pending != effective,
        "available_modes": sorted(_VALID_MODES),
        "env_var": ENV_VAR,
        "env_value": os.environ.get(ENV_VAR, ""),
    }


# --------------------------------------------------------------------------- #
# 归属信息不在这里：见 src/kernels/_crosscutting.task_attribution
# --------------------------------------------------------------------------- #
