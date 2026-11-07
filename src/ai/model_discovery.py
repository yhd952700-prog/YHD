"""从上游端点拉取可用模型名 —— "填 Key 后不用手打模型字符串"。

为什么要这个能力：产品承诺是"任意模型，填 Key 即可用"，但让用户凭记忆
输入模型名（``gpt-4o-mini`` / ``qwen2.5:3b`` / ``gemini-2.0-flash``）几乎
必然出错。这里直接问上游"你有哪些模型"，把名字拉回来让用户挑。

安全约束（与探测/生成路径一致）
--------------------------------
* 只发一次只读 GET，``allow_redirects=False``：302 不能把请求带去内网；
* 每次请求前重新校验目标（``assert_safe_runtime_target``），回环放行、
  私网/链路本地/元数据拒绝；
* 响应体只用于解析模型名；错误只按 ``reason`` 分类返回，原文不进用户文案、
  也不写日志；
* Key 只放在请求头里（Gemini 用 ``x-goog-api-key``），不拼进 URL。

失败时返回 ``manual=True``：界面据此提示"可以直接手填模型名"，绝不让流程
走进死路。
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

import requests

from .net_guard import RuntimeTargetRejected, assert_safe_runtime_target
from .providers import classify_upstream_error

logger = logging.getLogger("liuhao.ai.model_discovery")

#: provider 未填 base_url 时使用的官方端点。
DEFAULT_BASE_URLS: Dict[str, str] = {
    "openai": "https://api.openai.com/v1",
    "deepseek": "https://api.deepseek.com/v1",
    "moonshot": "https://api.moonshot.cn/v1",
    "anthropic": "https://api.anthropic.com",
    "google": "https://generativelanguage.googleapis.com",
    "ollama": "http://127.0.0.1:11434",
}

ANTHROPIC_VERSION = "2023-06-01"

#: 失败原因 → 给用户看的中文、可行动提示。原文（英文/内网地址）不进这里。
FAILURE_MESSAGES: Dict[str, str] = {
    "invalid_key": "这个 Key 没通过端点校验（可能输错或已失效）。请核对后重试，也可以直接手填模型名。",
    "key_revoked": "这个 Key 已被停用或吊销，请换一个有效的 Key；也可以先手填模型名。",
    "quota_exhausted": "这个 Key 的额度已用完，充值或换 Key 后才能拉取列表；也可以先手填模型名。",
    "rate_limited": "端点正在限流，请稍后重试；也可以先手填模型名。",
    "unreachable": "连不上这个地址，请检查 base_url 和网络；也可以直接手填模型名。",
    "timeout": "端点响应超时，请稍后重试；也可以直接手填模型名。",
    "unsupported": "这个端点没有提供模型列表接口，请直接手填模型名（例如 gpt-4o-mini）。",
    "redirect": "端点返回了重定向，已按安全策略拒绝跟随。",
    "rejected_target": "这个地址被安全策略拒绝（内网/链路本地地址不允许）。请换一个地址，或直接手填模型名。",
    "upstream_error": "端点返回了错误，暂时拿不到模型列表；可以直接手填模型名。",
}


def _failure(reason: str, *, detail: Optional[str] = None) -> Dict[str, Any]:
    return {
        "ok": False,
        "reason": reason,
        "detail": detail or FAILURE_MESSAGES.get(reason, FAILURE_MESSAGES["upstream_error"]),
        "models": [],
        # 永远允许手填：列表只是"帮用户少打错字"，不是必经步骤。
        "manual": True,
    }


def _join(base: str, path: str) -> str:
    return base.rstrip("/") + path


def _request_target(provider: str, base_url: str) -> Tuple[str, Dict[str, str], bool]:
    """返回 ``(url, headers, is_ollama)``；Key 只走请求头。"""
    base = base_url or DEFAULT_BASE_URLS.get(provider, "")
    trimmed = base.rstrip("/")
    if provider == "ollama":
        return _join(base, "/api/tags"), {}, True
    if provider == "anthropic":
        # 预设端点本身带 ``/v1``，不能再拼出一个 ``/v1/v1/models``。
        path = "/models" if trimmed.endswith("/v1") else "/v1/models"
        return _join(base, path), {"anthropic-version": ANTHROPIC_VERSION}, False
    if provider == "google":
        path = "/models" if trimmed.endswith("/v1beta") else "/v1beta/models"
        return _join(base, path), {}, False
    # OpenAI 兼容：base_url 可能已经带 /v1（自建网关常见），也可能不带。
    if trimmed.endswith("/v1"):
        return _join(base, "/models"), {}, False
    return _join(base, "/v1/models"), {}, False


def _auth_headers(provider: str, api_key: str) -> Dict[str, str]:
    if not api_key:
        return {}
    if provider == "anthropic":
        return {"x-api-key": api_key}
    if provider == "google":
        return {"x-goog-api-key": api_key}
    return {"Authorization": f"Bearer {api_key}"}


def _parse_models(provider: str, data: Any) -> Optional[List[str]]:
    """把各家响应收敛成模型名列表；结构不认识时返回 ``None``（→ unsupported）。"""
    if not isinstance(data, dict):
        return None
    if provider == "google":
        raw = data.get("models")
        if not isinstance(raw, list):
            return None
        names: List[str] = []
        for item in raw:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "").strip()
            if not name:
                continue
            methods = item.get("supportedGenerationMethods")
            if isinstance(methods, list) and methods and "generateContent" not in methods:
                continue
            names.append(name[len("models/"):] if name.startswith("models/") else name)
        return names
    if provider == "ollama":
        raw = data.get("models")
        if not isinstance(raw, list):
            return None
        return [
            str(item.get("name") or item.get("model") or "").strip()
            for item in raw
            if isinstance(item, dict) and (item.get("name") or item.get("model"))
        ]
    raw = data.get("data")
    if not isinstance(raw, list):
        return None
    return [
        str(item.get("id") or "").strip()
        for item in raw
        if isinstance(item, dict) and item.get("id")
    ]


def fetch_model_list(
    provider: str,
    base_url: Optional[str] = None,
    api_key: Optional[str] = None,
    *,
    timeout: float = 10.0,
) -> Dict[str, Any]:
    """拉取上游可用模型名。

    返回 ``{"ok": True, "models": [...], "endpoint": "..."}`` 或
    ``{"ok": False, "reason": "...", "detail": "中文提示", "manual": True}``。
    """
    provider = (provider or "").strip().lower()
    url, headers, is_ollama = _request_target(provider, base_url or "")
    if not url:
        return _failure("unsupported")
    headers.update(_auth_headers(provider, (api_key or "").strip()))

    try:
        assert_safe_runtime_target(
            url, context="模型服务", allow_private_service=is_ollama
        )
    except RuntimeTargetRejected:
        return _failure("rejected_target")

    try:
        response = requests.get(
            url,
            headers=headers,
            timeout=min(float(timeout), 20.0),
            allow_redirects=False,
        )
    except requests.exceptions.Timeout:
        return _failure("timeout")
    except requests.exceptions.RequestException:
        logger.info("model discovery unreachable provider=%s", provider)
        return _failure("unreachable")

    status = int(getattr(response, "status_code", 0) or 0)
    if 300 <= status < 400:
        return _failure("redirect")
    if status in (401, 403) or status == 402 or status == 429 or status >= 500:
        kind = classify_upstream_error(status=status, text=getattr(response, "text", ""))
        logger.info(
            "model discovery upstream failure provider=%s status=%s kind=%s",
            provider,
            status,
            kind,
        )
        return _failure(kind)
    if status in (404, 405, 501):
        # 404 可能是"模型不存在"，但列出接口本身不存在时也返回 404；
        # 对"拉列表"这个动作来说，正确提示是"该端点不支持列表接口"。
        return _failure("unsupported")
    if status != 200:
        return _failure("upstream_error")

    try:
        data = response.json()
    except ValueError:
        return _failure("unsupported")
    models = _parse_models(provider, data)
    if models is None:
        return _failure("unsupported")

    unique: List[str] = []
    seen = set()
    for name in models:
        if name and name not in seen:
            seen.add(name)
            unique.append(name)
    if not unique:
        # 结构对但没有模型：仍然让用户手填，别把"空列表"当成可用结果。
        return _failure("unsupported", detail="端点没有返回可用的模型名，请直接手填模型名。")
    return {"ok": True, "reason": "", "detail": "", "models": unique, "endpoint": url}
