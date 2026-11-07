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
import re
from typing import Any, Dict, List, Mapping, Tuple

#: 用户可见的账号文案禁用词：任何 ``detail`` / ``notice`` / 提示里都不应出现
#: 这些层级或角色结构词（主账号自己的管理页除外，见产品约定）。
ACCOUNT_HIERARCHY_WORDS: Tuple[str, ...] = (
    "管理员",
    "主账号",
    "子账号",
    "上级",
    "下级",
    "层级",
    "加入",
    "邀请",
    "越级",
)

#: 机器串特征：``key=value``、``a.b.c`` 点号链、``snake_case``。
#: 只用于检查"用户主界面文案"（``user_message`` / ``display_title`` /
#: ``status_label``），不检查 ``technical_detail``。
_MACHINE_PATTERNS: Tuple[re.Pattern[str], ...] = (
    re.compile(r"\b[a-z][a-z0-9_.-]*\s*=\s*\S+"),
    re.compile(r"\b[a-z][a-z0-9_-]*(?:[.][a-z][a-z0-9_-]*)+\b"),
    re.compile(r"\b[a-z][a-z0-9]*_[a-z0-9_]+\b"),
)

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
    "kernel_policy_enforcement": "策略强制",
}

#: 就绪页子系统名 -> 人话名称。
SUBSYSTEM_LABELS: Dict[str, str] = {
    "identity": "账号身份",
    "memory": "记忆",
    "context": "上下文",
    "capability": "能力目录",
    "policy": "策略",
    "security": "安全",
    "execution": "执行",
    "evaluation": "评估",
    "resource": "资源",
    "event": "事件总线",
    "network": "网络",
    "audit": "审计",
    "trust": "信任",
    "plugin": "插件",
    "retention": "数据保留",
}

#: 指标机器名 -> 人话名称（告警 ``user_message`` 只用人话）。
METRIC_LABELS: Dict[str, str] = {
    "cpu_usage": "CPU 使用率",
    "cpu_percent": "CPU 使用率",
    "memory_usage": "内存使用率",
    "memory_percent": "内存使用率",
    "disk_usage": "磁盘使用率",
    "disk_percent": "磁盘使用率",
    "error_rate": "错误率",
    "latency_p99": "99 分位响应时间",
    "request_rate": "请求速率",
    "queue_depth": "队列积压",
    "active_connections": "活跃连接数",
}

#: 比较方向 -> 人话。
OPERATOR_WORDS: Dict[str, str] = {
    "gt": "超过",
    "gte": "超过",
    ">": "超过",
    ">=": "超过",
    "lt": "低于",
    "<": "低于",
    "lte": "低于",
    "<=": "低于",
    "eq": "等于",
    "=": "等于",
    "ne": "不等于",
}

#: 预检发现代码 -> 人话标题。原始 ``message`` 只进 ``technical_detail``。
CONFIG_FINDING_LABELS: Dict[str, str] = {
    "secret_key_missing": "缺少会话加密密钥",
    "secret_key_placeholder": "会话加密密钥仍是公开占位值",
    "jwt_secret_missing": "缺少持久登录签名密钥",
    "jwt_secret_placeholder": "登录签名密钥仍是公开占位值",
    "sqlite_in_production": "生产环境仍在使用本地单机数据库",
    "debug_logging_in_production": "生产环境开启了调试日志",
    "policy_enforcement_off": "生产环境未开启策略强制",
}

#: 从告警原始文案里抽取 ``指标 / 当前值 / 阈值 / 比较方向``。
_METRIC_VALUE_PATTERNS: Tuple[re.Pattern[str], ...] = (
    re.compile(
        r"(?P<metric>[a-z][a-z0-9_.-]*)\s*=\s*"
        r"(?P<value>\d+(?:\.\d+)?(?:%|ms|s|毫秒|秒)?)\s*"
        r"(?P<op>\>=|\<=|>|<)\s*"
        r"(?P<threshold>\d+(?:\.\d+)?(?:%|ms|s|毫秒|秒)?)",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?P<metric>[a-z][a-z0-9_.-]*)\s*=\s*"
        r"(?P<value>\d+(?:\.\d+)?(?:%|ms|s|毫秒|秒)?)\s*"
        r"(?:exceeds|above|大于|超过)\s*(?:threshold|阈值)?\s*"
        r"(?P<threshold>\d+(?:\.\d+)?(?:%|ms|s|毫秒|秒)?)"
        r"\s*(?:\(\s*(?P<op>[a-z]+)\s*\))?",
        re.IGNORECASE,
    ),
)


def machine_fragments(text: Any) -> List[str]:
    """返回文案里的机器串片段；空列表表示"看起来是人话"。

    只用来检查用户主界面字段，不检查 ``technical_detail``。
    """
    raw = str(text or "")
    return [match.group(0) for pattern in _MACHINE_PATTERNS for match in pattern.finditer(raw)]


def subsystem_label(name: Any) -> str:
    raw = str(name or "").strip()
    return SUBSYSTEM_LABELS.get(raw.lower(), "子系统")


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
    """形如 ``本地 Ollama · 未启用`` —— 服务商 + 启用状态，一句话可读。

    优先用 ``vendor``（真实厂商）；缺失时回退旧字段 ``provider``。
    """
    provider = provider_label(model.get("vendor") or model.get("provider"))
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
    label = HEALTH_CHECK_LABELS.get(name, "检查项")
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


def _metric_reading(alert: Mapping[str, Any]) -> Tuple[str, str, str, str]:
    """从告警结构化字段/原始文案里抽 ``(指标, 当前值, 阈值, 方向)``。

    原始 ``message`` 只用于抽取，绝不整段进入 ``user_message``。
    """
    metric = str(alert.get("metric_name") or "").strip()
    message = str(alert.get("message") or "")
    for pattern in _METRIC_VALUE_PATTERNS:
        match = pattern.search(message)
        if not match:
            continue
        metric = metric or str(match.group("metric") or "")
        op = str(match.groupdict().get("op") or "")
        return metric, match.group("value") or "", match.group("threshold") or "", op
    return metric, "", "", ""


def alert_user_message(alert: Mapping[str, Any]) -> str:
    """告警主界面文案：只有人话，机器串只留在 ``technical_detail``。"""
    name = str(alert.get("name") or "系统告警").strip()
    severity = severity_label(alert.get("severity"))
    state = status_label(alert.get("state"))
    metric, value, threshold, op = _metric_reading(alert)
    label = METRIC_LABELS.get(metric.lower(), "")
    if label and value and threshold:
        direction = OPERATOR_WORDS.get(op.lower(), "超过")
        body = f"{label} {value}，{direction}阈值 {threshold}"
    elif label:
        body = f"{label}超出正常范围"
    else:
        body = name
    return f"{severity}告警：{body}，当前{state}"


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
    """配置预检结论：补中文级别与人话标题，原始 message 只进技术细节。"""
    if not isinstance(finding, dict):
        return finding
    label = severity_label(finding.get("severity"))
    code = str(finding.get("code") or "").strip().lower()
    title = CONFIG_FINDING_LABELS.get(code, "存在一条需要处理的安全配置问题")
    finding.setdefault("severity_label", label)
    finding.setdefault("display_title", title)
    finding.setdefault("user_message", f"{label}：{title}，请到诊断页查看技术细节")
    if "technical_detail" not in finding:
        raw = {key: value for key, value in finding.items()
               if key not in ("user_message", "technical_detail", "severity_label",
                              "display_title")}
        finding["technical_detail"] = technical_detail(raw)
    return finding


def subsystem_user_message(name: str, status: str) -> str:
    return f"{subsystem_label(name)}{status_label(status)}"


# --------------------------------------------------------------------------- #
# 面向用户的错误文案：内部治理术语不上屏
# --------------------------------------------------------------------------- #
# 身份/策略拒绝的原始异常文本是给开发者看的（"token does not represent a human
# identity"、"sovereignty grant ..."）。它们一旦经 ``detail=str(exc)`` 到达界面，
# 用户既读不懂、也不知道下一步做什么。这里集中做一次边界翻译：
# 原始英文只进日志，``detail`` 只出中性中文。
#
# 只处理"含内部术语/编号/脚本路径"的文案；其它文案原样返回，免得把本来可读的
# 中文说明改坏。整段英文内部提示直接换成按状态码的兜底 —— 混合中英
# （"unknown 账号 for 人工授权"）比整句中文更难读。

GOVERNANCE_TERM_REPLACEMENTS: Tuple[Tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"(?i)\bhuman[\s-]+sovereignty\b"), "人工授权"),
    (re.compile(r"(?i)\bsovereignty\b"), "人工授权"),
    (re.compile(r"(?i)\bhuman[\s-]+identity\b"), "个人账号"),
    (re.compile(r"(?i)\bgrant[\s_-]?window\b"), "授权窗口"),
    (re.compile(r"(?i)\bkernel[\s_-]*actions?\b"), "系统动作"),
    (re.compile(r"(?i)\bkernel\b"), "系统"),
    (re.compile(r"(?i)\bprincipals?\b"), "账号"),
    (re.compile(r"人类主权|主权"), "人工授权"),
    (re.compile(r"内核策略|内核"), "系统策略"),
    (re.compile(r"(?i)\bL0\b"), "最高权限"),
    (re.compile(r"主体"), "账号"),
)

#: 内部政策 / 设计编号与脚本路径：出现在 ``detail`` 里一律不认。
_POLICY_ID_RE = re.compile(
    r"(?<![A-Za-z0-9])(?:LHX-[A-Z]-|OD-|C-)\d+(?:\.\d+)*(?![A-Za-z0-9])", re.I
)
_SECTION_ID_RE = re.compile(r"§\s*\d+(?:\.\d+)*")
_SCRIPT_PATH_RE = re.compile(
    r"(?i)(?:[A-Za-z]:[\\/])?(?:[\w.-]+[\\/])*"
    r"(?:scripts|src|apps|tests)[\\/][\w./\\-]+\.(?:py|ts|tsx|js|sh|ps1)"
)
_CJK_RE = re.compile(r"[\u4e00-\u9fff]")


def _neutral_fallback(status_code: int) -> str:
    """没有可读中文时的按状态码兜底：说清"发生了什么 + 下一步"。"""
    if status_code == 401:
        return "登录状态已失效，本次操作没有完成。请重新登录后再试。"
    if status_code == 403:
        return (
            "当前账号没有执行这个操作的权限，本次操作没有完成。"
            "请换用有权限的账号，或联系管理员授权后再试。"
        )
    if status_code == 404:
        return "要访问的内容不存在，可能已被删除或移动。请返回上一页后重试。"
    if status_code == 409:
        return "该操作与当前状态冲突，本次操作没有完成。请刷新后重试。"
    if status_code in (502, 503, 504):
        return "本地服务暂时不可用，本次操作没有完成。请稍后重试。"
    return "请求没有通过校验，本次操作没有完成。请检查填写内容后重试。"


def contains_internal_copy(text: Any) -> bool:
    """文案里是否含内部治理术语、政策编号或脚本路径。"""
    raw = str(text or "")
    if not raw:
        return False
    if _POLICY_ID_RE.search(raw) or _SECTION_ID_RE.search(raw) or _SCRIPT_PATH_RE.search(raw):
        return True
    return any(pattern.search(raw) for pattern, _ in GOVERNANCE_TERM_REPLACEMENTS)


def sanitize_user_copy(text: Any) -> str:
    """把内部术语替换成人话；不改变句子里的业务含义。"""
    raw = str(text or "")
    if not raw:
        return ""
    out = _SCRIPT_PATH_RE.sub("相关说明", raw)
    out = _SECTION_ID_RE.sub("", out)
    out = _POLICY_ID_RE.sub("", out)
    for pattern, replacement in GOVERNANCE_TERM_REPLACEMENTS:
        out = pattern.sub(replacement, out)
    # 删掉编号后留下的空括号与多余空白。
    out = re.sub(r"[（(]\s*[)）]", "", out)
    out = re.sub(r"[ \t]{2,}", " ", out)
    out = re.sub(r"\s+([，。；：])", r"\1", out)
    return out.strip(" ，,;；")


def _sanitize_string(text: str, status_code: int) -> str:
    if not contains_internal_copy(text):
        return text
    if _CJK_RE.search(text):
        cleaned = sanitize_user_copy(text)
        # 纯英文内部提示替换后仍是"unknown 账号 ..."这种夹生话，用兜底更清楚。
        return cleaned if _CJK_RE.search(cleaned) else _neutral_fallback(status_code)
    return _neutral_fallback(status_code)


def user_facing_detail(detail: Any, status_code: int = 400) -> Any:
    """HTTP ``detail`` 的边界脱敏；结构与机器字段（如 ``code``）保持不变。"""
    if isinstance(detail, str):
        return _sanitize_string(detail, status_code)
    if isinstance(detail, dict):
        out = dict(detail)
        for key in ("message", "detail"):
            value = out.get(key)
            if isinstance(value, str):
                out[key] = _sanitize_string(value, status_code)
        return out
    if isinstance(detail, (list, tuple)):
        return [
            _sanitize_string(item, status_code) if isinstance(item, str) else item
            for item in detail
        ]
    return detail
