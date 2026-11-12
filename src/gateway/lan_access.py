"""「手机访问（同一 Wi-Fi）」开关 —— 后端唯一入口。

设计底线
--------
* **默认关闭**：安装、升级、进程重启之后一律关闭；只有用户在设置页显式打开、
  并确认风险提示才生效。网关自己的监听地址一个字节都不改，仍然只有
  ``127.0.0.1``。
* **只绑一个网卡地址**：中继绑 ``LAN_IP:端口``，不是 ``0.0.0.0``。换了 Wi-Fi
  或拔了网线，这个地址不存在，手机就进不来；不会顺带监听新网络的地址。
* **持久化 + 网段保护**：记住"上次开启时的网段"，重启后网段不一致就自动关闭
  并提示，避免笔记本带到咖啡馆后对陌生 Wi-Fi 自动敞开。
* **关闭是同步的**：先掐断已建立的连接，再关监听端口，不需要重启进程。

这里只做"开关与设备管理"；配对、设备 Cookie、限流、转发都在
``scripts/ops/lan_relay.py``（以模块方式在本进程内运行）。
"""
from __future__ import annotations

import importlib.util
import ipaddress
import json
import os
import socket
import threading
import time
from pathlib import Path
from types import ModuleType
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..common.fs_permissions import restrict_to_current_user
from ..common.request_context import current_origin
from .policy import require_admin_principal

router = APIRouter(prefix="/v1/lan-access", tags=["lan-access"])

RELAY_DIR_REL = Path("mobile_access")
SETTINGS_FILE = "lan_access.json"

#: 风险提示原文：前端原样展示，不另写一份，避免两处措辞不一致。
RISK_NOTICE = (
    "同一 Wi-Fi 下的其他设备可能嗅探到本页面的流量（当前是明文 HTTP）。"
    "仅在可信任的家庭网络开启；不要在咖啡馆、酒店、公司访客网络打开。"
)

_relay_module: Optional[ModuleType] = None
_module_lock = threading.Lock()


def _load_relay() -> ModuleType:
    """按文件路径加载中继脚本：它既能独立运行，也能被网关进程内复用。"""
    global _relay_module
    with _module_lock:
        if _relay_module is not None:
            return _relay_module
        path = Path(__file__).resolve().parents[2] / "scripts" / "ops" / "lan_relay.py"
        if not path.is_file():
            raise RuntimeError(f"找不到局域网中继脚本：{path}")
        spec = importlib.util.spec_from_file_location("liuhao_lan_relay", path)
        if spec is None or spec.loader is None:
            raise RuntimeError("局域网中继脚本无法加载。")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        _relay_module = module
        return module


def _relay_dir() -> Path:
    path = _relay_module_data_dir() / RELAY_DIR_REL
    path.mkdir(parents=True, exist_ok=True)
    return path


def _relay_module_data_dir() -> Path:
    """与中继/设备会话完全一致的解析顺序。"""
    raw = (os.environ.get("LIUHAO_DATA_DIR") or "").strip()
    if raw:
        return Path(raw).expanduser()
    if os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA") or Path.home())
        return base / "LiuHao" / "data"
    base = Path(os.environ.get("XDG_DATA_HOME") or (Path.home() / ".local" / "share"))
    return base / "liuhao"


def _load_settings() -> Dict[str, Any]:
    try:
        raw = json.loads((_relay_dir() / SETTINGS_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return raw if isinstance(raw, dict) else {}


def _save_settings(values: Dict[str, Any]) -> None:
    path = _relay_dir() / SETTINGS_FILE
    path.write_text(json.dumps(values, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    restrict_to_current_user(path)


def _private_ipv4(candidate: str) -> Optional[str]:
    try:
        address = ipaddress.ip_address(candidate)
    except ValueError:
        return None
    if address.version != 4 or address.is_loopback or address.is_link_local:
        return None
    if not address.is_private:
        return None
    return str(address)


def _default_lan_ip() -> Optional[str]:
    """默认路由所在网卡的私有 IPv4；没有（没连网/只有公网地址）时返回 ``None``。"""
    try:
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        probe.connect(("8.8.8.8", 80))
        candidate = probe.getsockname()[0]
        probe.close()
    except OSError:
        candidate = ""
    found = _private_ipv4(candidate)
    if found:
        return found
    try:
        for item in socket.gethostbyname_ex(socket.gethostname())[2]:
            found = _private_ipv4(item)
            if found:
                return found
    except OSError:
        pass
    return None


def _subnet_of(ip: str) -> str:
    return str(ipaddress.ip_network(f"{ip}/24", strict=False))


def _listen_port() -> int:
    """网关实际监听端口。延迟导入 main，避免 create_app 阶段的循环导入。"""
    try:
        from .main import resolve_listen_port

        return int(resolve_listen_port() or 8080)
    except Exception:  # noqa: BLE001 - 端口探测失败按默认值处理，绑定失败会再说
        return 8080


def _require_local() -> None:
    """开关、重新配对、吊销设备都只能在电脑上做（手机路由层已拦，这里兜底）。"""
    if current_origin().is_lan:
        raise HTTPException(
            status_code=403,
            detail="手机访问的开关与设备管理只能在电脑上操作。手机上可以看进度、审批和下达指令。",
        )


class _Runtime:
    """进程内中继的持有者；所有状态变更都在锁内完成。"""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._relay = None
        self._notice: Optional[str] = None
        self._resumed = False

    # ---- 读 ---------------------------------------------------------------
    @property
    def running(self) -> bool:
        return self._relay is not None

    def _current(self):
        return self._relay

    def _status_locked(self) -> Dict[str, Any]:
        relay = self._relay
        if relay is None:
            settings = _load_settings()
            return {
                "enabled": False,
                "lan_ip": None,
                "port": int(settings.get("port") or _listen_port()),
                "urls": [],
                "pair_code": None,
                "pair_expires_in": 0,
                "pair_url": None,
                "devices": [],
                "online_count": 0,
                "subnet": settings.get("subnet"),
                "notice": self._notice,
                "risk_notice": RISK_NOTICE,
            }
        snapshot = relay.status()
        return {
            "enabled": True,
            "lan_ip": relay.lan_host,
            "port": relay.lan_port,
            "urls": snapshot.get("lan_urls") or [],
            "pair_code": snapshot.get("pair_code"),
            "pair_expires_in": snapshot.get("pair_expires_in"),
            "pair_url": (snapshot.get("pair_urls") or [None])[0],
            "devices": snapshot.get("devices") or [],
            "online_count": snapshot.get("online_count") or 0,
            "subnet": _subnet_of(relay.lan_host),
            "notice": self._notice,
            "risk_notice": RISK_NOTICE,
        }

    # ---- 开 ---------------------------------------------------------------
    def enable(self, principal: str = "") -> Dict[str, Any]:
        with self._lock:
            if self._relay is not None:
                return self._status_locked()
            ip = _default_lan_ip()
            if not ip:
                raise HTTPException(
                    status_code=409,
                    detail="这台电脑现在没有局域网地址（没连 Wi-Fi 或网线）。连上家庭网络后再开启。",
                )
            port = _listen_port()
            module = _load_relay()
            try:
                relay = module.start_inline(
                    backend_host="127.0.0.1",
                    backend_port=port,
                    lan_host=ip,
                    lan_port=port,
                )
            except OSError as exc:
                raise HTTPException(
                    status_code=409,
                    detail=(
                        f"局域网地址 {ip}:{port} 无法监听：{exc}。"
                        "可能是端口被占用，或 Windows 防火墙拦截了本次绑定。"
                    ),
                ) from exc
            self._relay = relay
            self._notice = None
            _save_settings(
                {
                    "enabled": True,
                    "lan_ip": ip,
                    "subnet": _subnet_of(ip),
                    "port": port,
                    # 谁开的开关：手机换到的令牌就以这个账号的身份签发。
                    "principal": principal,
                    "updated_at": time.time(),
                }
            )
            return self._status_locked()

    # ---- 关 ---------------------------------------------------------------
    def disable(self, *, notice: Optional[str] = None, persist: bool = True) -> Dict[str, Any]:
        with self._lock:
            relay = self._relay
            self._relay = None
            if relay is not None:
                try:
                    relay.stop()
                except Exception:  # noqa: BLE001 - 关闭失败也必须让开关归零
                    pass
            self._notice = notice
            if persist:
                settings = _load_settings()
                settings["enabled"] = False
                settings["updated_at"] = time.time()
                try:
                    _save_settings(settings)
                except OSError:
                    pass
            return self._status_locked()

    # ---- 网络变化保护 -----------------------------------------------------
    def guard_network(self) -> None:
        """当前网段与记录不一致时自动关闭（重启后、或拔插网线后都会走到）。"""
        with self._lock:
            relay = self._relay
            if relay is None:
                return
            current = _default_lan_ip()
            if not current:
                self.disable(notice="网络已变化，局域网访问已关闭：这台电脑现在没有局域网地址。")
                return
            if _subnet_of(current) != _subnet_of(relay.lan_host):
                self.disable(notice="网络已变化，局域网访问已关闭。需要时请在设置里重新开启。")

    def resume(self) -> None:
        with self._lock:
            if self._resumed:
                return
            self._resumed = True
            settings = _load_settings()
            if not settings.get("enabled"):
                return
            current = _default_lan_ip()
            if not current:
                self._notice = "上次开启过手机访问，但这台电脑现在没有局域网地址，已保持关闭。"
                _save_settings({**settings, "enabled": False})
                return
            if settings.get("subnet") and _subnet_of(current) != str(settings.get("subnet")):
                self._notice = "网络已变化，局域网访问已关闭。需要时请在设置里重新开启。"
                _save_settings({**settings, "enabled": False, "updated_at": time.time()})
                return
        # 锁外启动：绑定端口可能阻塞几百毫秒，不要卡住状态查询。
        try:
            self.enable(str(settings.get("principal") or ""))
        except HTTPException as exc:
            self._notice = f"上次开启过手机访问，但这次没能自动恢复：{exc.detail}"

    # ---- 设备 -------------------------------------------------------------
    def rotate_pair(self) -> Dict[str, Any]:
        with self._lock:
            if self._relay is None:
                raise HTTPException(status_code=409, detail="手机访问没有开启。")
            snapshot = self._relay.relay.rotate_pair_code()
            self._relay.relay.pair_guard.reset()
            return snapshot

    def devices(self) -> Dict[str, Any]:
        with self._lock:
            if self._relay is None:
                return {"devices": [], "enabled": False}
            return {"devices": self._relay.relay.devices_snapshot(), "enabled": True}

    def revoke(self, device_id: str) -> Dict[str, Any]:
        with self._lock:
            if self._relay is None:
                raise HTTPException(status_code=409, detail="手机访问没有开启。")
            relay_state = self._relay.relay
            record = relay_state.devices.pop(device_id)
            if record is None:
                raise HTTPException(status_code=404, detail="设备不存在（可能已经被移除）。")
            module = _load_relay()
            module._revoke_tokens(relay_state, record)
            relay_state.close_device_connections(device_id)
            return {"revoked": 1, "id": device_id}

    def revoke_all(self) -> Dict[str, Any]:
        with self._lock:
            if self._relay is None:
                raise HTTPException(status_code=409, detail="手机访问没有开启。")
            relay_state = self._relay.relay
            records = relay_state.devices.clear()
            module = _load_relay()
            for record in records:
                module._revoke_tokens(relay_state, record)
            relay_state.close_all_connections()
            relay_state.rotate_pair_code()
            relay_state.pair_guard.reset()
            return {"revoked": len(records)}


_state = _Runtime()


def resume_on_startup() -> None:
    """网关启动时调用：按记录恢复，或按网段变化自动关闭。"""
    try:
        _state.resume()
    except Exception:  # noqa: BLE001 - 启动恢复失败不能拖垮网关
        pass


def shutdown_lan_access() -> None:
    """进程退出时停掉中继，但**保留**"上次是开着的"这条记录。

    下次启动会走 ``resume_on_startup``：网段没变就自动恢复，变了就自动关闭并提示。
    """
    _state.disable(persist=False, notice=None)


@router.get("/status", response_model=Dict)
def lan_access_status(_: str = Depends(require_admin_principal)) -> Dict:
    _require_local()
    _state.guard_network()
    with _state._lock:
        return _state._status_locked()


class EnableRequest(BaseModel):
    #: 用户必须显式确认风险提示；缺省或 false 一律拒绝，避免"顺手打开"。
    confirm_risk: bool = False


@router.post("/enable", response_model=Dict)
def lan_access_enable(
    req: EnableRequest,
    principal: str = Depends(require_admin_principal),
) -> Dict:
    _require_local()
    if not req.confirm_risk:
        raise HTTPException(
            status_code=400,
            detail=f"开启前需要确认风险：{RISK_NOTICE}",
        )
    with _state._lock:
        return _state.enable(principal)


@router.post("/disable", response_model=Dict)
def lan_access_disable(_: str = Depends(require_admin_principal)) -> Dict:
    _require_local()
    with _state._lock:
        return _state.disable(notice="已关闭：所有手机连接已断开，局域网端口不再监听。")


@router.post("/pair/rotate", response_model=Dict)
def lan_access_rotate(_: str = Depends(require_admin_principal)) -> Dict:
    _require_local()
    return _state.rotate_pair()


@router.get("/devices", response_model=Dict)
def lan_access_devices(_: str = Depends(require_admin_principal)) -> Dict:
    _require_local()
    return _state.devices()


@router.post("/devices/{device_id}/revoke", response_model=Dict)
def lan_access_revoke(device_id: str, _: str = Depends(require_admin_principal)) -> Dict:
    _require_local()
    return _state.revoke(device_id)


@router.post("/devices/revoke-all", response_model=Dict)
def lan_access_revoke_all(_: str = Depends(require_admin_principal)) -> Dict:
    _require_local()
    return _state.revoke_all()
