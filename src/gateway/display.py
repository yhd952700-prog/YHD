"""把机器字段翻成**人类可读字段**，供界面直接展示（只增字段，不改旧字段）。

为什么放在接口侧
----------------
界面此前只能把 ``provider=ollama``、``status=success``、``severity=HIGH`` 这类
机器值原样贴出来。前端硬翻译会随后端枚举漂移而失真，且每加一个字段就要改一次
前端。这里集中做"人话"映射，规则唯一定义在同一处，前端只读 ``display_*`` /
``status_label`` / ``user_message``。

约定
----
* 全部是纯函数，未知值不抛异常；
* 新字段一律与旧字段并存，旧字段语义不变（向后兼容）；
* ``technical_detail`` 保留原始机器信息，供诊断页使用，不进主界面文案。
"""
from __future__ import annotations

import json
from typing import Any, Dict, Mapping, Optional

#: provider 类型 -> 人话名称。新增 provider 时在此补一行即可。
PROVIDER_LABELS: Dict[str, str] = {
    "mock": "模拟服务",
    "openai": "OpenAI",
    "anthropic": "Anthropic",
    "google": "Google Gemini",
    "ollama": "本地 Ollama",
    "moonshot": "月之暗面 Kimi",
    "deepseek": "DeepSeek",
    "local": "本地模型",
}

#: 机器状态 -> 中文状态。界面只读 ``status_label``，诊断页仍可读原值。
STATUS_LABELS: Dict[str, str] = {
    "ok": "正常",
    "healthy": "正常",
    "ready": "已就绪",
    "success": "成功",
    "allowed": "已允许",
    "degraded": "部分可用",
    "unhealthy": "异常",
    "unavailable": "不可用",
    "not_ready": "未就绪",
    "denied": "已拒绝",
    "blocked": "已阻断",
    "failed": "失败",
    "error": "错误",
    "firing": "正在告警",
    "resolved": "已恢复",
    "suppressed": "已抑制",
    "active": "已启用",
    "enabled": "已启用",
    "inactive": "未启用",
    "disabled": "未启用",
    "running": "进行中",
    "pending": "等待中",
    "completed": "已完成",
    "verified": "校验通过",
    "broken": "校验未通过",
    "empty": "暂无数据",
    "unknown": "未知",
}

#: 严重级别 -> 中文级别。
SEVERITY_LABELS: Dict[str, str] = {
    "low": "提示",
    "medium": "注意",
    "high": "严重",
    "critical": "紧急",
    "info": "提示",
    "warning": "警告",
    "error": "错误",
    "blocking": "阻断",
}

#: 事件动作前缀 -> 业务域。
EVENT_DOMAIN_LABELS: Dict[str, str] = {
    "account": "账号",
    "auth": "登录",
    "audit": "审计",
    "chat": "会话",
    "file": "文件",
    "memory": "记忆",
    "model": "模型",
    "models": "模型",
    "execution": "执行",
    "task": "任务",
    "goal": "目标",
    "system": "系统",
    "security": "安全",
    "backup": "备份",
}

#: 事件动作结尾（动词）-> 人话。
ACTION_TAIL_LABELS: Dict[str, str] = {
    "login": "登录",
    "logout": "退出登录",
    "register": "注册",
    "registered": "注册",
    "self_registered": "自助注册",
    "created": "创建",
    "create": "创建",
    "updated": "更新",
    "update": "更新",
    "deleted": "删除",
    "delete": "删除",
    "export": "导出",
    "import": "导入",
    "activate": "激活",
    "activated": "激活",
    "suspend": "停用",
    "suspended": "停用",
    "status": "状态变更",
    "permissions": "权限变更",
    "verify": "校验",
    "verified": "校验",
    "execute": "执行",
    "executed": "执行",
    "completed": "完成",
    "failed": "失败",
    "toggle": "开关变更",
}

#: 健康检查项 -> 人话名称。
HEALTH_CHECK_LABELS: Dict[str, str] = {
    "api_key_manager": "密钥管理",
    "jwt_handler": "登录令牌",
    "rbac_manager": "权限模型",
    "encryption_manager": "加密模块",
    "rate_limiter": "请求限流",
    "audit_store": "审计链",
    "audit_checkpointer": "审计断点",
    "human_identity_registry": "账号身份库",
    "executor_fence": "执行隔离",
    "ai_provider": "模型服务",
    "kernel_policy_enforcement": "内核策略",
}


def status_label(value: Any) -> str:
    """机器状态 -> 中文；未知值回落到"未知状态"（原值留在 technical_detail）。"""
    raw = str(value or "").strip()
    if not raw:
        return "未知状态"
    return STATUS_LABELS.get(raw.lower(), "未知状态")


def severity_label(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw:
        return "提示"
    return SEVERITY_LABELS.get(raw.lower(), "提示")


def provider_label(provider: Any) -> str:
    raw = str(provider or "").strip()
    if not raw:
        return "未指定服务商"
    return PROVIDER_LABELS.get(raw.lower(), f"服务商 {raw}")


def model_display_name(model: Mapping[str, Any]) -> str:
    for key in ("name", "model", "id"):
        value = str(model.get(key) or "").strip()
        if value:
            return value
    return "未命名模型"


def model_display_status(model: Mapping[str, Any]) -> str:
    """形如 ``本地 Ollama · 未启用`` —— 服务商 + 启用状态，一句话可读。"""
    provider = provider_label(model.get("provider"))
    enabled = model.get("enabled")
    if enabled is None:
        state = "可用"
    else:
        state = "已启用" if bool(enabled) else "未启用"
    return f"{provider} · {state}"


def _humanize_action(action: str) -> str:
    parts = [part for part in action.replace("/", ".").split(".") if part]
    if not parts:
        return "系统事件"
    domain = EVENT_DOMAIN_LABELS.get(parts[0].lower())
    tail = ACTION_TAIL_LABELS.get(parts[-1].lower())
    if domain and tail:
        return f"{domain} · {tail}"
    if domain:
        return f"{domain} · 操作"
    if tail:
        return tail
    return parts[-1].replace("_", " ")


def event_display_title(event: Mapping[str, Any]) -> str:
    """事件/任务标题：``账号 · 自助注册（成功）`` 这种一句人话。"""
    action = str(
        event.get("action")
        or event.get("type")
        or event.get("event_type")
        or ""
    ).strip()
    title = _humanize_action(action)
    outcome = str(event.get("outcome") or "").strip()
    return f"{title}（{status_label(outcome)}）" if outcome else title


def event_status_label(event: Mapping[str, Any]) -> str:
    return status_label(event.get("outcome") or event.get("status") or "unknown")


def health_check_user_message(name: str, check: Mapping[str, Any]) -> str:
    label = HEALTH_CHECK_LABELS.get(name, name or "检查项")
    state = str(check.get("status") or "").strip()
    readable = status_label(state)
    if state == "healthy":
        return f"{label}{readable}"
    return f"{label}{readable}，请到诊断页查看技术细节"


def technical_detail(payload: Any) -> str:
    """原始机器字段的 JSON 文本；供诊断页，不进主界面。"""
    try:
        return json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    except Exception:  # noqa: BLE001 - 诊断信息不能反过来打断接口
        return str(payload)


def decorate_health_check(name: str, check: Dict[str, Any]) -> Dict[str, Any]:
    """就地补 ``user_message`` / ``technical_detail`` / ``status_label``。"""
    if not isinstance(check, dict):
        return check
    check.setdefault("user_message", health_check_user_message(name, check))
    check.setdefault("status_label", status_label(check.get("status")))
    if "technical_detail" not in check:
        raw = {key: value for key, value in check.items()
               if key not in ("user_message", "technical_detail", "status_label")}
        check["technical_detail"] = technical_detail(raw)
    return check


def alert_user_message(alert: Mapping[str, Any]) -> str:
    name = str(alert.get("name") or "系统告警").strip()
    severity = severity_label(alert.get("severity"))
    state = status_label(alert.get("state"))
    message = str(alert.get("message") or "").strip()
    head = f"{name}：{severity}告警，当前{state}"
    return f"{head}（{message}）" if message else head


def decorate_alert(alert: Dict[str, Any]) -> Dict[str, Any]:
    """就地补告警的展示字段；旧字段完全保留。"""
    if not isinstance(alert, dict):
        return alert
    alert.setdefault("severity_label", severity_label(alert.get("severity")))
    alert.setdefault("status_label", status_label(alert.get("state") or alert.get("status")))
    alert.setdefault("user_message", alert_user_message(alert))
    if "technical_detail" not in alert:
        raw = {key: value for key, value in alert.items()
               if key not in ("user_message", "technical_detail", "severity_label",
                              "status_label")}
        alert["technical_detail"] = technical_detail(raw)
    return alert


def decorate_event(event: Dict[str, Any]) -> Dict[str, Any]:
    """就地补事件/任务标题与状态；旧字段完全保留。"""
    if not isinstance(event, dict):
        return event
    event.setdefault("display_title", event_display_title(event))
    event.setdefault("status_label", event_status_label(event))
    return event


def decorate_finding(finding: Dict[str, Any]) -> Dict[str, Any]:
    """配置预检结论：补中文级别与人话标题，原始 message 进技术细节。"""
    if not isinstance(finding, dict):
        return finding
    label = severity_label(finding.get("severity"))
    message = str(finding.get("message") or "").strip()
    finding.setdefault("severity_label", label)
    finding.setdefault("display_title", f"{label} · {message}" if message else label)
    finding.setdefault("user_message", finding["display_title"])
    if "technical_detail" not in finding:
        raw = {key: value for key, value in finding.items()
               if key not in ("user_message", "technical_detail", "severity_label",
                              "display_title")}
        finding["technical_detail"] = technical_detail(raw)
    return finding


def subsystem_user_message(name: str, status: str) -> str:
    label = HEALTH_CHECK_LABELS.get(name, name or "子系统")
    return f"{label}{status_label(status)}"
