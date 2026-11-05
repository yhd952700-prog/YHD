"""防火墙 / 局域网可达性**只读**检测 + 用户自助指引。

边界（硬约束）：
  * 只执行只读 PowerShell 查询（``Get-NetFirewallProfile`` /
    ``Get-NetConnectionProfile`` / ``Get-NetTCPConnection`` /
    ``Get-NetFirewallRule``），**绝不** ``New-NetFirewallRule`` /
    ``Set-NetFirewallProfile`` / ``netsh advfirewall set``。
  * 不请求管理员权限，不操作任何安全软件。
  * 输出里的命令只是**给用户自己复制执行**的参考，工具本身不会运行它。

手机连不上时，按顺序排查：端口是否只听回环 → 当前网络是否为"公用"
（Windows 默认阻断入站）→ 防火墙是否开启且没有放行规则。
"""

from __future__ import annotations

import json
import os
import subprocess
from typing import Any

#: 明确写死的策略：本工具永不自动修改防火墙。
AUTO_MODIFY_FIREWALL = False

_DISCLAIMER = "本工具只读检测，绝不修改防火墙或安全软件设置，也不请求管理员权限。"


def _no_window_flags() -> int:
    return getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0


def _run_powershell(script: str, timeout: float = 20.0) -> tuple[str | None, str | None]:
    """执行一段只读 PowerShell，返回 ``(stdout, error)``。"""
    if os.name != "nt":
        return None, "非 Windows 平台，无法查询 Netsh/Net 防火墙"
    try:
        completed = subprocess.run(
            [
                "powershell.exe",
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-Command",
                script,
            ],
            capture_output=True,
            text=True,
            timeout=timeout,
            creationflags=_no_window_flags(),
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return None, f"{type(exc).__name__}: {exc}"
    if completed.returncode != 0:
        return None, (completed.stderr or "").strip()[:300] or f"powershell 退出码 {completed.returncode}"
    return completed.stdout.strip(), None


def _parse_list(text: str | None) -> tuple[list[dict[str, Any]], str | None]:
    if not text:
        return [], None
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        return [], f"JSON 解析失败: {exc}"
    if isinstance(data, dict):
        data = [data]
    if not isinstance(data, list):
        return [], "PowerShell 返回结构不是列表"
    return [item for item in data if isinstance(item, dict)], None


def _query_profiles() -> tuple[list[dict[str, Any]], str | None]:
    out, err = _run_powershell(
        "@(Get-NetFirewallProfile -ErrorAction SilentlyContinue | "
        "Select-Object Name,Enabled) | ConvertTo-Json -Compress"
    )
    return _parse_list(out)[0], err


def _query_networks() -> tuple[list[dict[str, Any]], str | None]:
    out, err = _run_powershell(
        "@(Get-NetConnectionProfile -ErrorAction SilentlyContinue | "
        "Select-Object Name,NetworkCategory,InterfaceAlias) | ConvertTo-Json -Compress"
    )
    return _parse_list(out)[0], err


def _query_listeners(port: int) -> tuple[list[dict[str, Any]], str | None]:
    out, err = _run_powershell(
        "@(Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue | "
        f"Where-Object {{ $_.LocalPort -eq {int(port)} }} | "
        "Select-Object LocalAddress,LocalPort,OwningProcess) | ConvertTo-Json -Compress"
    )
    return _parse_list(out)[0], err


def _query_allow_rule(port: int) -> tuple[bool | None, str | None]:
    script = (
        "$found = 'false';"
        "foreach ($r in (Get-NetFirewallRule -Direction Inbound -Action Allow -Enabled True "
        "-ErrorAction SilentlyContinue)) {"
        "  $pf = $r | Get-NetFirewallPortFilter -ErrorAction SilentlyContinue;"
        "  if (-not $pf) { continue };"
        "  if ($pf.Protocol -ne 'TCP') { continue };"
        f"  $lp = @($pf.LocalPort);"
        f"  if (($lp -contains 'Any') -or ($lp -contains '{int(port)}')) {{ $found = 'true'; break }}"
        "}"
        "$found"
    )
    out, err = _run_powershell(script, timeout=30.0)
    if out is None:
        return None, err
    return out.strip().lower() == "true", err


def _guidance(port: int | None) -> list[str]:
    port_text = str(port) if port else "服务实际端口"
    steps = [
        "确认手机和电脑连的是同一个 Wi-Fi（不要一边连公司/学校网络、另一边连手机热点）。",
        "Windows 安全中心 → 防火墙和网络保护 → 专用网络 →「允许应用通过防火墙」→ "
        "勾选 Python（或运行本程序的 pythonw.exe）。",
        "若当前网络被识别为「公用」，切到「专用」：设置 → 网络和 Internet → Wi-Fi → "
        "当前网络的「网络配置文件」改为「专用网络」。",
        "仍不通时，用**你自己**打开的管理员 PowerShell 执行下面这条放行命令"
        "（本工具不会替你执行）：",
        f'  New-NetFirewallRule -DisplayName "LiuHao LAN {port_text}" -Direction Inbound '
        f"-Protocol TCP -LocalPort {port_text} -Action Allow -Profile Private",
        "公司/学校网络可能做了客户端隔离（AP isolation），需联系网管放行同一网段互访。",
    ]
    return steps


def _truthy(value: Any) -> bool:
    """PowerShell 的 ConvertTo-Json 会把布尔值输出为 true/false，也可能给 0/1。"""
    return value is True or value == 1 or str(value).strip().lower() in {"true", "1"}


def _network_category(value: Any) -> str:
    """``NetworkCategory`` 在 JSON 里可能是字符串，也可能是 NLM 枚举整数。"""
    if isinstance(value, str) and value:
        return value
    return {0: "Public", 1: "Private", 2: "DomainAuthenticated"}.get(value, str(value))


def inspect_firewall(port: int | None = None) -> dict[str, Any]:
    """只读检测防火墙与局域网可达性，返回诊断结论 + 用户自助指引。"""
    errors: list[str] = []
    profiles, err = _query_profiles()
    if err:
        errors.append(f"防火墙配置查询失败：{err}")
    networks, err = _query_networks()
    if err:
        errors.append(f"网络配置查询失败：{err}")

    enabled_profiles = [str(p.get("Name")) for p in profiles if _truthy(p.get("Enabled"))]
    public_networks = [
        str(n.get("Name") or n.get("InterfaceAlias") or "?")
        for n in networks
        if _network_category(n.get("NetworkCategory")).lower() == "public"
    ]

    listeners: list[dict[str, Any]] = []
    allow_rule: bool | None = None
    if port:
        listeners, err = _query_listeners(port)
        if err:
            errors.append(f"端口监听查询失败：{err}")
        allow_rule, err = _query_allow_rule(port)
        if err:
            errors.append(f"入站规则查询失败：{err}")

    reasons: list[str] = []
    risk = "low"
    if port and not listeners:
        risk = "high"
        reasons.append(f"端口 {port} 当前没有监听（服务未启动或绑定失败）")
    elif port and listeners:
        addresses = {str(item.get("LocalAddress")) for item in listeners}
        if addresses <= {"127.0.0.1", "::1"}:
            risk = "high"
            reasons.append(f"端口 {port} 只监听回环地址，手机无法访问（应以 0.0.0.0 绑定）")
        else:
            reasons.append(f"端口 {port} 正在监听：{', '.join(sorted(addresses))}")

    if public_networks:
        risk = "high"
        reasons.append(f"当前网络被识别为「公用」：{', '.join(public_networks)}，Windows 默认阻断入站")
    if enabled_profiles and port and allow_rule is False and risk != "high":
        risk = "medium"
        reasons.append(f"防火墙已开启（{', '.join(enabled_profiles)}），未找到端口 {port} 的入站放行规则")
    if enabled_profiles and port and allow_rule is True:
        reasons.append(f"防火墙已开启（{', '.join(enabled_profiles)}），已找到端口 {port} 的 TCP 入站放行规则")

    return {
        "supported": os.name == "nt",
        "port": port,
        "firewall_profiles": profiles,
        "enabled_profiles": enabled_profiles,
        "network_profiles": networks,
        "public_networks": public_networks,
        "listeners": listeners,
        "allow_rule_found": allow_rule,
        "risk": risk,
        "reasons": reasons,
        "guidance": _guidance(port),
        "auto_modify": AUTO_MODIFY_FIREWALL,
        "disclaimer": _DISCLAIMER,
        "errors": errors,
    }


__all__ = ["AUTO_MODIFY_FIREWALL", "inspect_firewall"]
