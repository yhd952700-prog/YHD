"""显式「完全授权模式」读取层（A 步与 G1–G5 埋点共用）。

设计约束（来自用户决策）：

* 模式是**启动期快照**，不做运行中热切。热切会在一次任务的中途产生
  「半套权限」窗口：同一任务前半段需要确认、后半段又不需要，审计也无法
  把这次任务归到单一模式下。
* 模式**不通过清空** ``LIUHAO_KERNEL_POLICY_ENFORCE`` 实现。清空会让
  ``/v1/ready`` 判 ``UNARMED_PRODUCTION``（``src/gateway/health.py``），
  桌面壳/托盘会一直显示出错。模式是另一根轴：武装保持不变，闸对「用户自己」
  不再生效，但对系统主体、员工、定时任务仍然生效。
* 读不到配置时一律回落到 ``standard`` —— 权限开关的失败方向必须更严。

来源优先级（第一步只有环境变量；第二步接入设置页写入的配置文件）：

1. ``LIUHAO_AUTHORITY_MODE`` 环境变量（``full`` / ``standard``）；
2. 数据目录 ``authority_mode.json``；
3. 缺省 ``standard``。
"""

from __future__ import annotations

import os
import threading
from typing import Any

#: 环境变量名：显式声明运行模式。
ENV_VAR = "LIUHAO_AUTHORITY_MODE"

#: 标准模式（默认）：现有策略闸与确认流程原样生效。
MODE_STANDARD = "standard"

#: 完全授权模式：仅对「用户自己」放开高风险动作，审计不降级。
MODE_FULL = "full"

_VALID_MODES = frozenset({MODE_STANDARD, MODE_FULL})

_lock = threading.RLock()
#: 启动期快照。``None`` 表示尚未读取。
_snapshot: str | None = None


def parse_mode(value: str | None) -> str:
    """把任意输入归一化为合法模式；非法/空值 → ``standard``（fail-safe）。"""
    text = (value or "").strip().lower()
    return text if text in _VALID_MODES else MODE_STANDARD


def current_mode() -> str:
    """当前进程生效的模式。

    只在首次调用时求值并缓存 —— 之后再改环境变量也不会改变本进程的模式，
    这就是「切换需重启」的落点（避免半套权限窗口期）。
    """
    global _snapshot
    with _lock:
        if _snapshot is None:
            _snapshot = parse_mode(os.environ.get(ENV_VAR))
        return _snapshot


def is_full() -> bool:
    """是否处于完全授权模式。"""
    return current_mode() == MODE_FULL


def reload() -> None:
    """丢弃快照并重新求值。

    仅供测试与「进程启动完成后立刻初始化」使用；产品运行期没有任何调用点，
    以保证不出现热切。
    """
    global _snapshot
    with _lock:
        _snapshot = None


def describe() -> dict[str, Any]:
    """给接口/诊断用的快照（不含任何机密）。"""
    return {
        "mode": current_mode(),
        "env_var": ENV_VAR,
        "env_value": os.environ.get(ENV_VAR, ""),
        "restart_required": True,
    }
