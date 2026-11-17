"""远程访问（Tailscale 组网）—— 网关侧的显式“远程模式”开关。

设计依据：``~/workspace/yhd-046-next-prep/plans/7-2-remote-access.md``。

总原则：远程访问只多开一条“回家的路”，不降低任何一道已有的门。

* **默认关闭**：不设开关时网关只绑 ``127.0.0.1``，与以前完全一致。
  远程模式是 Joseph 在设置里显式打开（或启动时显式指定）才生效；
  关掉之后立刻回到只本机。
* **不暴露端口**：远程模式不做路由器端口转发、不调 UPnP、不装
  Cloudflare Tunnel。手机与电脑各装一个 Tailscale 客户端、登录同一个
  账号，手机在外面就像在家里 Wi-Fi 里一样连网关。
* **复用已有保护**：登录、节流、审批、审计、Host 白名单全部复用，
  不另造一套。Host 白名单里永远不加 ``*``。
* **电脑关机就连不上**：Tailscale 只解决“人在外面连家里开着的电脑”，
  电脑关机、断电、断网时手机照样连不上——这不是故障，界面与说明书
  里都要先说清。

开关的两个层次（不要混淆）：

1. **期望模式**（``configured_mode``）：环境变量 ``LIUHAO_REMOTE_MODE``
   优先；否则读数据目录 ``remote_access.json`` 里的持久化设置；
   都没有就是 ``off``。设置页的开关改的就是持久化设置，改完要重启
   网关才生效——重启前状态接口会如实报 ``restart_required: true``，
   不假装已经生效。
2. **实际生效**（``active``）：期望模式是 ``tailscale`` **并且** 本进程
   真的绑了非回环地址。只改设置不重启，``active`` 仍是 false。

Tailscale 地址的探测（尽力而为，探测不到就老实报“未检测到”，不编）：

* 优先跑 ``tailscale ip``（只读本地状态，不登录、不改配置）；
* 其次枚举网卡，找 ``100.64.0.0/10``（CGNAT 段，Tailscale 虚拟地址所在）
  或名字以 ``tailscale`` 开头的网卡。

本模块不 import 网关其它模块（``__main__`` 与 ``main`` 都要 import 它），
只用标准库 + 可选的 ``psutil``（缺失时优雅降级）。
"""
from __future__ import annotations

import ipaddress
import json
import os
import shutil
import socket
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from .policy import require_admin_principal

#: 环境变量：``off``（默认）或 ``tailscale``。环境变量优先于持久化设置。
REMOTE_MODE_ENV = "LIUHAO_REMOTE_MODE"
#: 持久化设置文件名（数据目录下，与 lan_access.json 同一目录）。
REMOTE_MODE_FILENAME = "remote_access.json"
#: 合法取值。其它值一律按 ``off`` 处理并给出原因，不静默当成 tailscale。
REMOTE_MODES = ("off", "tailscale")
#: Tailscale 虚拟地址所在的 CGNAT 段。
TAILSCALE_CGNAT = ipaddress.ip_network("100.64.0.0/10")

#: 风险提示原文：前端原样展示，不另写一份，避免两处措辞不一致。
RISK_NOTICE = (
    "远程模式打开后，手机在外面也能连到这台电脑上的 Jarvis。"
    "登录、审批与审计不变，但请只在可信任的 Tailscale 账号下使用；"
    "不用时请关掉，恢复成“只本机能用”。"
    "注意：电脑关机、断电或断网时，手机照样连不上。"
)

router = APIRouter(prefix="/v1/remote-access", tags=["remote-access"])


# ==================== Tailscale 地址探测 ====================


def _valid_ipv4(value: str) -> Optional[str]:
    try:
        parsed = ipaddress.ip_address((value or "").strip())
    except ValueError:
        return None
    return str(parsed) if isinstance(parsed, ipaddress.IPv4Address) else None


def _tailscale_ip_from_cli(timeout: float = 5.0) -> List[str]:
    """跑 ``tailscale ip``（只读），返回其中的 IPv4 地址列表。

    未安装、未登录、超时、非零退出一律返回空列表——调用方按“未检测到”
    处理，不把失败伪装成地址。
    """
    exe = shutil.which("tailscale")
    if not exe:
        return []
    try:
        proc = subprocess.run(
            [exe, "ip"],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    if proc.returncode != 0:
        return []
    found: List[str] = []
    for line in (proc.stdout or "").splitlines():
        ip = _valid_ipv4(line)
        if ip and ip not in found:
            found.append(ip)
    return found


def _tailscale_ip_from_interfaces() -> List[str]:
    """枚举网卡：找 100.64/10 段地址或 tailscale 网卡上的 IPv4。"""
    found: List[str] = []
    try:
        import psutil
    except ImportError:
        return found
    try:
        interfaces = psutil.net_if_addrs()
    except Exception:  # noqa: BLE001 - 枚举是尽力而为
        return found
    for name, addrs in interfaces.items():
        is_tailscale_iface = (name or "").lower().startswith("tailscale")
        for addr in addrs:
            if getattr(addr, "family", None) != socket.AF_INET:
                continue
            ip = _valid_ipv4(getattr(addr, "address", "") or "")
            if not ip:
                continue
            try:
                in_cgnat = ipaddress.ip_address(ip) in TAILSCALE_CGNAT
            except ValueError:
                in_cgnat = False
            if (is_tailscale_iface or in_cgnat) and ip not in found:
                found.append(ip)
    return found


def tailscale_ipv4_addresses() -> List[str]:
    """本机 Tailscale 虚拟 IPv4 地址（去重；没有就返回空列表，不编）。"""
    found = _tailscale_ip_from_cli()
    for ip in _tailscale_ip_from_interfaces():
        if ip not in found:
            found.append(ip)
    return found


def tailscale_ipv4() -> Optional[str]:
    """本机第一个 Tailscale 虚拟 IPv4 地址；未检测到返回 None。"""
    addresses = tailscale_ipv4_addresses()
    return addresses[0] if addresses else None


# ==================== 开关：期望模式 / 持久化 ====================


def _data_dir() -> Path:
    """持久化设置所在的数据目录（与 lan_access.json 同一解析顺序）。"""
    raw = (os.environ.get("LIUHAO_DATA_DIR") or "").strip()
    if raw:
        return Path(raw).expanduser()
    try:
        from ..logging_config import resolve_data_dir

        return resolve_data_dir()
    except Exception:  # noqa: BLE001 - 解析失败时回落到程序目录 data
        return Path(__file__).resolve().parents[2] / "data"


def _settings_path() -> Path:
    return _data_dir() / REMOTE_MODE_FILENAME


def load_persisted_remote_mode() -> Tuple[str, str]:
    """读持久化设置。返回 ``(mode, source)``；文件缺失/损坏按 off 处理。"""
    path = _settings_path()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return "off", "default"
    mode = (raw.get("mode") if isinstance(raw, dict) else None) or "off"
    mode = str(mode).strip().lower()
    if mode not in REMOTE_MODES:
        return "off", "default"
    return mode, "persisted"


def persist_remote_mode(mode: str) -> Dict[str, Any]:
    """把期望模式写进数据目录。返回写盘后的记录；写失败时抛错（调用方转 500）。"""
    mode = (mode or "").strip().lower()
    if mode not in REMOTE_MODES:
        raise ValueError(f"远程模式只能是 off 或 tailscale，收到：{mode!r}")
    path = _settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "mode": mode,
        "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass  # Windows 上 chmod 语义有限，忽略
    return payload


def resolve_remote_mode() -> Tuple[str, str, str]:
    """解析本次启动的期望模式。返回 ``(mode, source, note)``。

    优先级：环境变量 > 持久化设置 > 默认 off。环境变量写错时按 off 处理，
    并在 note 里写清原因（不静默、不猜）。
    """
    raw_env = (os.environ.get(REMOTE_MODE_ENV) or "").strip().lower()
    if raw_env:
        if raw_env in REMOTE_MODES:
            return raw_env, "env", ""
        return (
            "off",
            "env",
            f"环境变量 {REMOTE_MODE_ENV}={raw_env!r} 不是合法取值（off/tailscale），已按 off 处理。",
        )
    mode, source = load_persisted_remote_mode()
    return mode, source, ""


def _is_loopback_host(host: str) -> bool:
    """本模块自用的回环判定（不 import ``__main__``，避免循环 import）。"""
    value = (host or "").strip().lower()
    if value in ("127.0.0.1", "localhost", "::1", "[::1]"):
        return True
    try:
        return ipaddress.ip_address(value.strip("[]")).is_loopback
    except ValueError:
        return False


def effective_bind_host(requested_host: str, mode: str) -> str:
    """远程模式下的实际绑定地址。

    模式 tailscale 且请求的是回环地址时，改绑 ``0.0.0.0``（网关只监听
    本机所有网卡；Tailscale 虚拟网卡是其中之一，路由器公网端口不暴露，
    因为没有任何端口转发）。其它情况原样返回——显式指定的地址优先，
    默认回环在 off 模式下绝不改动。
    """
    if (mode or "").strip().lower() == "tailscale" and _is_loopback_host(requested_host):
        return "0.0.0.0"
    return requested_host


def _listen_host() -> str:
    return (
        os.environ.get("LIUHAO_LISTEN_HOST") or os.environ.get("HOST") or "127.0.0.1"
    ).strip().lower()


def _listen_port() -> Optional[int]:
    raw = (os.environ.get("LIUHAO_LISTEN_PORT") or "").strip()
    if raw:
        try:
            return int(raw)
        except ValueError:
            return None
    try:
        payload = json.loads((_data_dir() / "gateway.port").read_text(encoding="utf-8"))
        port = payload.get("port") if isinstance(payload, dict) else None
        return int(port) if port is not None else None
    except (OSError, ValueError, TypeError):
        return None


def _external_bind_active() -> bool:
    """本进程是否真的绑了非回环地址（与 main.external_bind_enabled 同义，
    这里不 import main，避免启动期循环 import）。"""
    listen = _listen_host()
    if listen in ("0.0.0.0", "::", "*"):
        return True
    return not _is_loopback_host(listen)


def remote_access_status() -> Dict[str, Any]:
    """远程访问当前状态（设置页展示用；全部是实测值，不编）。

    * ``mode``：期望模式（环境变量 > 持久化 > off）。
    * ``startup_mode``：本进程启动时实际应用的模式（入口在启动时写入
      ``LIUHAO_REMOTE_MODE_ACTIVE``）；未经入口启动的进程（如测试客户
      端）为 null，``startup_mode_known`` 为 false，这时按当前实际绑定
      推断是否生效，不假装知道启动模式。
    * ``active``：启动模式是 tailscale 且本进程已绑非回环地址。
    * ``restart_required``：期望模式与启动模式不一致——改了开关还没重启，
      本进程的绑定还没变；界面必须如实显示，不能假装已生效。
    """
    mode, source, note = resolve_remote_mode()
    raw_startup = (os.environ.get("LIUHAO_REMOTE_MODE_ACTIVE") or "").strip().lower()
    startup_known = raw_startup in REMOTE_MODES
    # 启动模式已知（经 __main__ / _runner 启动）时以它为准；未知时（如测试
    # 客户端、旧启动器）以当前实际绑定推断，不假装知道。
    startup_mode = raw_startup if startup_known else None
    effective_startup = startup_mode if startup_mode is not None else mode
    listen_host = _listen_host()
    listen_port = _listen_port()
    externally_bound = _external_bind_active()
    active = effective_startup == "tailscale" and externally_bound
    if startup_known:
        restart_required = mode != startup_mode
    else:
        restart_required = mode == "tailscale" and not externally_bound
    tailscale_ip = tailscale_ipv4()
    remote_url = (
        f"http://{tailscale_ip}:{listen_port}/"
        if active and tailscale_ip and listen_port
        else None
    )
    return {
        "mode": mode,
        "mode_source": source,
        "mode_note": note,
        "startup_mode": startup_mode,
        "startup_mode_known": startup_known,
        "active": active,
        "restart_required": restart_required,
        "tailscale_detected": tailscale_ip is not None,
        "tailscale_ip": tailscale_ip,
        "listen_host": listen_host,
        "listen_port": listen_port,
        "remote_url": remote_url,
        "risk_notice": RISK_NOTICE,
    }


# ==================== HTTP 接口（设置页用；仅管理员） ====================


@router.get("/status", response_model=Dict)
def remote_access_status_endpoint(_: str = Depends(require_admin_principal)) -> Dict:
    return remote_access_status()


class RemoteModeRequest(BaseModel):
    mode: str


@router.post("/mode", response_model=Dict)
def remote_access_set_mode(
    body: RemoteModeRequest, _: str = Depends(require_admin_principal)
) -> Dict:
    mode = (body.mode or "").strip().lower()
    if mode not in REMOTE_MODES:
        raise HTTPException(status_code=400, detail="远程模式只能是 off 或 tailscale。")
    try:
        persist_remote_mode(mode)
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"保存远程模式失败：{exc}") from exc
    status = remote_access_status()
    status["saved"] = True
    return status
