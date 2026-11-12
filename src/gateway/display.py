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

from fastapi import HTTPException

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
    "allow": "已允许",
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
    "login_success": "登录成功",
    "login_denied": "登录失败",
    "granted": "已授权",
    "revoked": "已撤销",
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
    "employee": "员工",
    "knowledge": "知识库",
    "document": "文档",
    "key": "密钥",
    "ai": "模型",
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
    "hired": "新增",
    "removed": "移除",
    "adopted": "取用",
    "assign_task": "派活",
    "changed": "变更",
    "grant": "授权",
    "granted": "授权",
    "revoke": "撤销",
    "revoked": "撤销",
    "issued": "发放",
    "used": "使用",
    "rotated": "重置",
    "discarded": "作废",
    "imported": "导入",
    "exported": "导出",
    "set_default": "设为默认",
    "manage": "管理",
    "owner": "归属",
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
    # 从后往前找第一个认识的动作词：``account.manage.denied`` 的结尾
    # ``denied`` 只是结果，真正的动作是 ``manage``；只看最后一段会退化成
    # 无信息量的「账号 · 操作」。
    tail = next(
        (label for label in (
            ACTION_TAIL_LABELS.get(part.lower()) for part in reversed(parts)
        ) if label),
        None,
    )
    if domain and tail:
        return f"{domain} · {tail}"
    if domain:
        return f"{domain} · 操作"
    if tail:
        return tail
    return parts[-1].replace("_", " ")


#: 无动作名的内核事件类型 -> 人话（登录等事件只有 event_type）。
EVENT_TYPE_TITLES: Dict[str, str] = {
    "access_allowed": "访问放行",
    "access_denied": "访问拒绝",
    "role_grant": "角色授予",
    "role_revoke": "角色撤销",
    "state_change": "状态变更",
    "policy_eval": "策略评估",
}


def event_display_title(event: Mapping[str, Any]) -> str:
    """事件/任务标题：``登录：张三（登录成功）`` 这种一句人话。

    ``target`` 是"对什么做的"，必须和动作、结果一起出现在同一句里，
    否则普通人只知道"发生了一次更新"，不知道改的是谁/哪个对象。
    """
    action = str(event.get("action") or "").strip()
    event_type = str(event.get("type") or event.get("event_type") or "").strip()
    title = (
        _humanize_action(action)
        if action
        else EVENT_TYPE_TITLES.get(event_type, _humanize_action(event_type))
    )
    target = str(event.get("target") or "").strip()
    if target:
        title = f"{title}：{target}"
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

#: 用户可见文案里不应出现的绝对路径（Windows 盘符 / 常见系统目录）。
#: 只认真正的文件系统绝对路径；``/v1/...`` 这类接口路径不在此列。
#:
#: 路径的终止字符里必须包含收尾标点（``，。；：`` 等全角符号、``）)``、
#: ``」』”’`` 等收尾引号）：否则 ``「…/conversation.db」`` 的右引号会被
#: 当成路径的一部分整段替换掉，用户看到的就成了半截引号
#: （"…「相关位置：磁盘已满…"）。排除这些字符只是**紧贴路径的边界**，
#: 路径本身仍被完整识别与替换，不是放宽敏感片段识别。
_ABSOLUTE_PATH_RE = re.compile(
    r"(?i)(?<![A-Za-z0-9])(?:"
    r"[A-Za-z]:[\\/][^\s，。；;：:'\"）)」』”’]*"
    r"|/(?:Users|home|app|var|tmp|opt|etc|root|usr|mnt|srv)/"
    r"[^\s，。；;：:'\"）)」』”’]*"
    r")"
)

#: 所有 5xx 对外的唯一文案。异常原文、堆栈、路径、表名只进服务端日志与诊断包。
INTERNAL_ERROR_MESSAGE = (
    "服务端处理这个请求时遇到意外问题，本次操作没有完成。"
    "请稍后重试；如果反复出现，请导出诊断包，"
    "并把下面的追踪编号一起提供给技术支持。"
)


def internal_error_body(trace_id: Any = None) -> Dict[str, Any]:
    """5xx 通用错误体：中文兜底 + ``trace_id``，绝不含异常原文。"""
    body: Dict[str, Any] = {
        "error": "internal_server_error",
        "detail": INTERNAL_ERROR_MESSAGE,
    }
    if trace_id:
        body["trace_id"] = str(trace_id)
    return body


# --------------------------------------------------------------------------- #
# 5xx 文案的显式透传标记
# --------------------------------------------------------------------------- #
# 统一掩码把 5xx 的字符串 ``detail`` 一律换成通用文案。这对内部错误是必要的，
# 但也会吃掉**本来就能让用户照做**的运维文案（例如"知识库未启用：请执行
# ollama pull …"）。这里用**显式类型标记**而不是猜字符串：
#
# * 只有主动继承 :class:`UserReadableHTTPException` 的端点才算"可透传"；
# * 透传前仍过一次安全闸 :func:`is_public_5xx_detail_safe`：含绝对路径、
#   脚本路径、堆栈、SQL、数据库名的一律回落掩码；
# * 透传与掩码两条路径都把异常原文写进日志（掩码用 error，透传用 warning）。


class UserReadableHTTPException(HTTPException):
    """显式声明："这条 5xx 的 detail 是给用户看、且可照做的中文文案"。

    继承本类型的端点必须保证 ``detail`` 不含内部路径 / 堆栈 / SQL / 主机名 /
    密钥；安全闸会再检查一次，命中内部特征时仍按通用掩码返回。
    """


#: 可透传文案里绝不能出现的内部分子：堆栈、脚本/绝对路径、SQL、数据库名。
_FORBIDDEN_PUBLIC_5XX_RE: Tuple[re.Pattern[str], ...] = (
    re.compile(r"(?i)\btraceback\b"),
    re.compile(r"(?i)\bfile\s+\"[^\"]+\""),
    re.compile(r"(?i)\bselect\b[\s\S]{0,200}\bfrom\b"),
    re.compile(r"(?i)\b(?:sqlite3?|postgres(?:ql)?|mysql|redis)\b"),
    _ABSOLUTE_PATH_RE,
    _SCRIPT_PATH_RE,
)


def is_public_5xx_detail_safe(text: Any) -> bool:
    """透传前的安全闸：文案里是否有内部路径 / 堆栈 / SQL / 数据库名。"""
    raw = str(text or "")
    if not raw:
        return False
    return not any(pattern.search(raw) for pattern in _FORBIDDEN_PUBLIC_5XX_RE)


#: 每个分类的"发生了什么 + 下一步"。英文原文（``Max retries`` /
#: ``Execution timeout`` / ``Blocked:``）只作为 ``technical_detail`` 折叠展示。
_TASK_ERROR_COPY: Tuple[Tuple[Tuple[str, ...], str], ...] = (
    (
        ("max retries", "max retry", "重试次数"),
        "任务多次重试后仍未完成，已停止。请检查任务输入后重试；"
        "如果反复出现，请导出诊断包。",
    ),
    (
        ("execution timeout", "timed out", "timeout", "超时"),
        "任务执行超时，已停止。请稍后重试；如果反复出现，请导出诊断包。",
    ),
    (
        ("blocked", "dependenc", "dependency", "依赖"),
        "依赖的任务没有完成，本任务已跳过。请先完成依赖任务后重试。",
    ),
    (
        ("task not found", "goal not found", "not found", "不存在"),
        "任务不存在，可能已被取消或删除。请刷新后重新提交。",
    ),
    (
        ("cancelled", "canceled", "取消"),
        "任务已取消，没有继续执行。需要时可重新提交。",
    ),
)

_DEFAULT_TASK_ERROR_COPY = (
    "任务执行失败，本次没有完成。请稍后重试；如果反复出现，请导出诊断包。"
)

_TIMEOUT_SECONDS_RE = re.compile(r"(\d+(?:\.\d+)?)\s*s(?:ec(?:onds?)?)?\b", re.IGNORECASE)


def _task_technical_copy(raw: str) -> str:
    """技术详情：英文原文照留；超时类补一行中文说明，让支持人员一眼看懂。"""
    if "timeout" in raw.lower() or "超时" in raw:
        match = _TIMEOUT_SECONDS_RE.search(raw)
        if match:
            return (
                f"中文说明：执行超时，任务在 {match.group(1)} 秒内没有完成。\n"
                f"原始错误：{raw}"
            )
        return f"中文说明：执行超时，任务在限定时间内没有完成。\n原始错误：{raw}"
    return raw


def task_error_copy(error: Any) -> Dict[str, str]:
    """任务失败文案：``message`` 上屏，``technical_detail`` 折叠展示。"""
    raw = str(error or "").strip()
    if not raw:
        return {"message": "", "technical_detail": ""}
    lowered = raw.lower()
    for tokens, message in _TASK_ERROR_COPY:
        if any(token in lowered for token in tokens):
            return {"message": message, "technical_detail": _task_technical_copy(raw)}
    return {"message": _DEFAULT_TASK_ERROR_COPY, "technical_detail": _task_technical_copy(raw)}


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
    out = _ABSOLUTE_PATH_RE.sub("相关位置", out)
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
    # 绝对路径与内部术语一样，是"用户不该看到"的东西：即便句子里有中文，
    # 也不能因为它读起来通顺就把 ``C:\\Users\\...`` 原样带上屏。
    if not contains_internal_copy(text) and not _ABSOLUTE_PATH_RE.search(text):
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
