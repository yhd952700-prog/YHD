"""模型管理 HTTP 面：界面上增删改模型，保存即生效（无需重启）。

设计约束
--------
* **写权限只给管理员**：读（列表/查看）对所有已登录人类开放；新增/更新/删除/
  设为默认/测试连通性要求 ``admin`` 权限或 ``L0`` 人类主权范围。
* **密钥永不出现在响应里**：列表/详情只返回 ``api_key_masked``（首尾各 4 位）
  与 ``api_key_set``；密文只在数据库里，从不出现在 JSON、日志或错误信息中。
* **测试连通性是真调用**：用待保存/已保存的配置真实发一次最小请求；错误按
  ``invalid_key`` / ``model_not_found`` / ``unreachable`` / ``timeout`` /
  ``upstream_error`` 分类，前端据此给出不同提示。
"""
from __future__ import annotations

import ipaddress
import json
import logging
import os
import re
import socket
import time
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.parse import urljoin, urlparse

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from .display import model_display_name, model_display_status, provider_label
from .policy import optional_bearer_payload, require_bearer_payload
from .account_context import (
    NEUTRAL_DENIED,
    deny,
    key_scope_for,
)
from ..kernels.identity import _accounts
from ..ai.model_config import (
    ModelConfigError,
    SUPPORTED_PROVIDERS,
    get_model_config_store,
    is_masked_placeholder,
    vendor_for,
)
from ..ai.providers import ProviderFactory, provider_from_config

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1", tags=["models"])

#: Same override the model registry uses, so the UI and the cost guard agree.
PRICING_FILE_ENV = "LIUHAO_MODEL_PRICING_FILE"
_DEFAULT_PRICING_FILE = Path(__file__).resolve().parents[2] / "config" / "model_pricing.json"

_ADMIN_ROLES = {"admin", "administrator", "owner"}
_ADMIN_SCOPES = {"L0"}


def _principal(payload) -> str:
    return str(getattr(payload, "sub", "") or "admin")


def _claims(payload) -> Dict[str, Any]:
    metadata = getattr(payload, "metadata", None) or {}
    if not isinstance(metadata, dict):
        metadata = {}
    return {
        "roles": {str(r).lower() for r in (getattr(payload, "roles", None) or [])},
        "permissions": {
            str(p).lower() for p in (getattr(payload, "permissions", None) or [])
        },
        "scope": str(metadata.get("scope") or ""),
    }


def require_model_admin(payload=Depends(require_bearer_payload)):
    """写操作闸门：``admin`` 角色/权限，或 L0 人类主权范围。

    为什么接受 L0：L0 是身份内核里的最高人类主权范围（本地唯一人类
    ``liuhao`` 的种子记录就是 ``permissions=[]`` + ``scope=L0``）。只看
    "admin 角色"会把本机唯一合法管理员挡在门外，而 L0 本身就是"可以管理
    整个系统"的语义。
    """
    claims = _claims(payload)
    if claims["roles"] & _ADMIN_ROLES or claims["permissions"] & _ADMIN_ROLES:
        return payload
    if claims["scope"] in _ADMIN_SCOPES:
        return payload
    raise HTTPException(
        status_code=403,
        detail="只有管理员（admin 权限或 L0 人类主权）可以修改模型配置",
    )


class ModelPayload(BaseModel):
    name: str = Field(..., min_length=1, max_length=64, description="显示名称（唯一）")
    provider: str = Field(..., description=f"provider 类型：{', '.join(SUPPORTED_PROVIDERS)}")
    model: str = Field(..., min_length=1, max_length=128, description="模型名，如 qwen2.5:3b")
    base_url: Optional[str] = Field(None, max_length=512, description="可选自定义端点")
    api_key: Optional[str] = Field(
        None, max_length=512, description="API Key（加密落盘；mock/ollama 可留空）"
    )
    timeout: float = Field(30.0, ge=1.0, le=600.0, description="请求超时（秒）")
    enabled: bool = Field(True, description="是否启用")
    is_default: bool = Field(False, description="是否设为默认（首个模型自动成为默认）")
    owner_principal: Optional[str] = Field(
        None, max_length=64, description="归属账号；默认是创建者自己，根账号可指定自己管辖范围内的账号"
    )


class ModelUpdatePayload(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=64)
    provider: Optional[str] = None
    model: Optional[str] = Field(None, min_length=1, max_length=128)
    base_url: Optional[str] = Field(None, max_length=512)
    api_key: Optional[str] = Field(None, max_length=512, description="留空表示不修改")
    timeout: Optional[float] = Field(None, ge=1.0, le=600.0)
    enabled: Optional[bool] = None
    is_default: Optional[bool] = None
    owner_principal: Optional[str] = Field(None, max_length=64, description="转移 Key 归属")


class TestPayload(BaseModel):
    provider: str = Field(..., description="provider 类型")
    model: str = Field(..., min_length=1, description="模型名")
    base_url: Optional[str] = Field(None, max_length=512)
    api_key: Optional[str] = Field(None, max_length=512)
    timeout: float = Field(30.0, ge=1.0, le=600.0)
    model_id: Optional[str] = Field(
        None, description="已保存配置的 id：用它填 base_url/密钥（表单可只改模型名）"
    )


def _store():
    return get_model_config_store()


#: Built-in provider presets. Only defaults for base_url/model -- never keys.
#: The console fills the form from these; a preset is not a credential source.
_PROVIDER_PRESETS: Dict[str, Dict[str, Optional[str]]] = {
    "openai": {
        "label": "OpenAI",
        "base_url": "https://api.openai.com/v1",
        "model": "gpt-4o-mini",
    },
    "deepseek": {
        "label": "DeepSeek",
        "base_url": "https://api.deepseek.com/v1",
        "model": "deepseek-chat",
    },
    "moonshot": {
        "label": "Moonshot",
        "base_url": "https://api.moonshot.cn/v1",
        "model": "moonshot-v1-8k",
    },
    "anthropic": {
        "label": "Anthropic",
        "base_url": "https://api.anthropic.com/v1",
        "model": "claude-3-5-sonnet-latest",
    },
    "google": {
        "label": "Google",
        "base_url": None,
        # 1.5 系列已于 2025-09 下线；沿用旧默认会让 Gemini 选项填完 Key 也只会拿到 404。
        "model": "gemini-2.5-flash",
    },
    "ollama": {
        "label": "Ollama（本地）",
        "base_url": "http://127.0.0.1:11434",
        "model": "llama3.1:8b",
    },
}


def _normalize_url(value: Optional[str]) -> str:
    return (value or "").strip().rstrip("/")


def _local_ollama_urls() -> set:
    """URLs that are unambiguously the local Ollama service."""
    configured = (
        os.environ.get("OLLAMA_BASE_URL")
        or os.environ.get("OLLAMA_HOST")
        or ""
    ).strip()
    if configured and not configured.startswith(("http://", "https://")):
        configured = f"http://{configured}"
    urls = {
        "http://127.0.0.1:11434",
        "http://localhost:11434",
    }
    if configured:
        urls.add(configured)
    return {_normalize_url(u) for u in urls}


def _is_local_ollama(raw: Optional[str]) -> bool:
    """是否是本机 Ollama（含它的 OpenAI 兼容入口 ``/v1``）。

    Ollama 同时提供原生 ``/api/*`` 与 OpenAI 兼容的 ``/v1/*``。两种入口指向
    同一台本机服务，因此都被视为"本地 Ollama"这一个已知安全目标；这不是把
    任意私网地址放行。
    """
    value = _normalize_url(raw)
    if value in _local_ollama_urls():
        return True
    return value.endswith("/v1") and value[: -len("/v1")] in _local_ollama_urls()


def _validate_base_url(
    raw: Optional[str],
    *,
    provider: Optional[str] = None,
    stored_url: Optional[str] = None,
) -> str:
    """Single validation entry point used by save/update/test/use paths.

    * only ``http``/``https``;
    * freshly supplied public targets must resolve to public addresses;
    * the one narrow exception is the **Ollama provider** pointed at the local
      Ollama service (including its OpenAI-compatible ``/v1`` endpoint): users
      may wire a model to their own machine without weakening SSRF protection
      for any other private address.

    私网放行必须同时满足 ``provider == "ollama"``：``OLLAMA_BASE_URL`` 是给
    Ollama 服务本身的配置，不能让 ``provider=openai/deepseek`` 借它把
    ``base_url`` 指向内网（那等于给所有 provider 开了私网白名单）。
    """
    value = (raw or "").strip()
    if not value:
        return ""
    is_ollama = (provider or "").strip().lower() == "ollama"
    return _assert_url_allowed(
        value, allow_private=is_ollama and _is_local_ollama(value)
    )


# --------------------------------------------------------------------------- #
# 价目（config/model_pricing.json）
# --------------------------------------------------------------------------- #
# 为什么模型管理必须谈价格：配额守卫算成本时会读这份文件；文件里没有的模型
# 会被记为"未解析成本"，但那个信息只在决策里可见，管理者在**新增模型**时看不到。
# 这里把同一份文件的状态直接标在每个模型响应上：定价了就说价格，没定价就明说
# "未定价"——不允许"看起来能用但成本算不出来"这种沉默状态。

def _pricing_path() -> Path:
    override = (os.environ.get(PRICING_FILE_ENV) or "").strip()
    return Path(override) if override else _DEFAULT_PRICING_FILE


def _load_prices() -> Dict[str, float]:
    """读取价目文件；任何读/解析问题都返回空表并记 warning（绝不让 UI 500）。"""
    path = _pricing_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        logger.info("model pricing file %s not found; all models are unpriced", path)
        return {}
    except Exception:  # noqa: BLE001 - 配置是不可信输入
        logger.warning("model pricing file %s unreadable; treating all as unpriced", path,
                       exc_info=True)
        return {}
    if not isinstance(data, dict):
        logger.warning("model pricing file %s is not an object; treating all as unpriced", path)
        return {}
    prices: Dict[str, float] = {}
    for model_id, price in data.items():
        if model_id.startswith("_"):
            continue
        if not isinstance(price, (int, float)) or isinstance(price, bool) or price < 0:
            logger.warning("model pricing: ignoring invalid price for %r (%r)", model_id, price)
            continue
        prices[str(model_id)] = float(price)
    return prices


def pricing_status(model_id: str) -> Dict[str, Any]:
    """返回某模型的定价状态。``priced=False`` 就是"未定价"，不是"免费"。"""
    price = _load_prices().get(model_id)
    if price is None:
        return {
            "priced": False,
            "price_per_1k_tokens": None,
            "source": str(_pricing_path()),
            "note": (
                "未定价：config/model_pricing.json 中没有该模型；"
                "配额守卫会把它记为未解析成本（不是 0，也不是免费）"
            ),
        }
    return {
        "priced": True,
        "price_per_1k_tokens": price,
        "source": str(_pricing_path()),
        "note": "",
    }


def _with_pricing(record: Dict[str, Any]) -> Dict[str, Any]:
    """给模型响应补 ``pricing`` 字段（不改存储、不改请求语义）。"""
    if not isinstance(record, dict):
        return record
    model_id = str(record.get("model") or record.get("name") or "")
    stamped = dict(record)
    stamped["pricing"] = pricing_status(model_id)
    # 界面直接展示这三项，不再原样显示 ``provider=ollama`` 这类机器值。
    stamped["display_name"] = model_display_name(stamped)
    # vendor 是真实厂商（provider 只是协议适配器）；旧字段 provider 仍保留。
    stamped["provider_display_name"] = provider_label(
        stamped.get("vendor") or stamped.get("provider")
    )
    stamped["display_status"] = model_display_status(stamped)
    enabled = stamped.get("enabled")
    stamped["status_label"] = "可用" if enabled is None else ("已启用" if enabled else "未启用")
    return stamped


def _run_guarded(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except ModelConfigError as exc:
        detail: object = (
            {"code": exc.code, "message": exc.detail} if exc.code else exc.detail
        )
        raise HTTPException(status_code=exc.status_code, detail=detail)


def require_model_manager(payload=Depends(require_bearer_payload)):
    """写闸门：管理员，或持有 ``model:manage`` 的账号（只能维护自己的 Key）。"""
    claims = _claims(payload)
    if claims["roles"] & _ADMIN_ROLES or claims["permissions"] & _ADMIN_ROLES:
        return payload
    if claims["scope"] in _ADMIN_SCOPES:
        return payload
    ctx = _context(payload)
    # 仅真实账号节点享有账号作用域；合成节点（老工具令牌）不得因此提权。
    if ctx.node.source != "standalone" and ctx.has_scope(_accounts.SCOPE_MODEL_MANAGE):
        return payload
    raise HTTPException(status_code=403, detail="无权执行该操作")


def _context(payload):
    """由令牌构造账号上下文；无账号节点的老令牌视为"只有自己"的根。"""
    from .account_context import AccountContext
    from ..kernels.identity import get_identity_manager

    principal = _principal(payload)
    manager = get_identity_manager()
    node = manager.get_account(principal)
    if node is None:
        from .account_context import _standalone_node

        node = _standalone_node(principal)
        subtree = (principal,)
    else:
        subtree = tuple(manager.subtree_principals(principal)) or (principal,)
    return AccountContext(
        principal=principal,
        node=node,
        subtree=subtree,
        standalone=node.source == "standalone",
    )


def _guard_owner_assign(ctx, owner: Optional[str]) -> None:
    """归属分配：根可指定子树内任意账号；其他账号只能指向自己。"""
    if not owner or owner == ctx.principal:
        return
    if ctx.is_root and owner in ctx.subtree:
        return
    raise deny(403, _accounts.CODE_KEY_NOT_OWNED, NEUTRAL_DENIED)


def _guard_visible(scope, owner: Optional[str]) -> None:
    if scope.can_use(owner or ""):
        return
    raise deny(403, _accounts.CODE_KEY_NOT_OWNED, NEUTRAL_DENIED)


def _audit_owner_change(actor: str, model_id: str, owner: str, previous: str) -> None:
    if owner == previous:
        return
    try:
        from ..kernels.identity import get_identity_manager

        get_identity_manager().record_event(
            action="key.owner",
            actor=actor,
            target=owner or actor,
            outcome="allowed",
            details={
                "key_owner": owner,
                "previous_owner": previous,
                "model_config_id": model_id,
            },
        )
    except Exception:  # noqa: BLE001
        logger.warning("key ownership audit failed for model %s", model_id, exc_info=True)


@router.get("/models")
def list_models(payload=Depends(optional_bearer_payload)) -> Dict[str, Any]:
    store = _store()
    if payload is None:
        # 未认证请求由上游鉴权中间件拦截；这里保持既有语义（空列表）。
        return {
            "models": [],
            "default_id": None,
            "count": 0,
            "supported_providers": list(SUPPORTED_PROVIDERS),
            "encryption": store.encryption_posture(),
        }
    scope = key_scope_for(principal=_principal(payload))
    models = store.list_models(
        visible_to=scope.visible_to, allow_unowned=scope.allow_unowned
    )
    return {
        "models": [_with_pricing(m) for m in models],
        "default_id": store.default_id(
            visible_to=scope.visible_to, allow_unowned=scope.allow_unowned
        ),
        "count": len(models),
        "supported_providers": list(SUPPORTED_PROVIDERS),
        "encryption": store.encryption_posture(),
    }


@router.get("/models/providers")
def list_providers() -> Dict[str, Any]:
    from ..ai.model_config import PROVIDERS_WITHOUT_KEY

    return {
        "providers": [
            {
                "type": name,
                "display_name": provider_label(name),
                "display_status": (
                    "需要 API Key" if name not in PROVIDERS_WITHOUT_KEY else "无需 API Key"
                ),
                "requires_api_key": name not in PROVIDERS_WITHOUT_KEY,
                "supports_base_url": name not in ("mock", "google"),
            }
            for name in SUPPORTED_PROVIDERS
        ]
    }


@router.get("/models/presets")
def list_provider_presets() -> Dict[str, Any]:
    """提供方预设：只填默认 ``base_url`` 与模型名，**不预填任何密钥**。

    刻意放在 ``/models/{model_id}`` 之前注册，否则会被动态路由吞掉。
    """
    from ..ai.model_config import PROVIDERS_WITHOUT_KEY

    presets = [
        {
            "id": name,
            "provider": name,
            **preset,
            "requires_api_key": name not in PROVIDERS_WITHOUT_KEY,
            "api_key": None,
        }
        for name, preset in _PROVIDER_PRESETS.items()
    ]
    # "任意模型"的通用入口：走 OpenAI 兼容协议，端点与模型名都由用户决定。
    # 它不是新的协议适配器，只是把 provider=openai + 自定义 base_url 这条路径
    # 在界面上显式化；内置 6 家同样只是预设，保存后都是可编辑/可删除的记录。
    presets.append(
        {
            "id": "openai_compatible",
            "provider": "openai",
            "label": "OpenAI 兼容（自定义端点）",
            "base_url": None,
            "model": "",
            "requires_api_key": True,
            "api_key": None,
            "custom": True,
        }
    )
    return {
        "presets": presets,
        "note": (
            "预设仅用于填写端点与模型名，不是白名单；保存后均可编辑或删除。"
            "API Key 必须由用户自行输入。"
        ),
    }


@router.get("/models/{model_id}")
def get_model(model_id: str, payload=Depends(require_bearer_payload)) -> Dict[str, Any]:
    """详情必须登录：匿名可读会泄露 base_url / owner / api_key_masked。

    已登录路径的可见性规则与列表一致（``_guard_visible`` 按归属判定），
    所以这里只是把"匿名"从可读集合里去掉，登录后的行为不变。
    """
    record = _run_guarded(_store().get_public, model_id)
    _guard_visible(
        key_scope_for(principal=_principal(payload)),
        record.get("owner_principal"),
    )
    return _with_pricing(record)


@router.post("/models")
def create_model(
    payload: ModelPayload, manager=Depends(require_model_manager)
) -> Dict[str, Any]:
    # 新增时就地标注定价状态（见 pricing_status）：管理者必须当场看到
    # "这个模型在价目表里没有条目"，而不是等到成本算不出来才发现。
    ctx = _context(manager)
    owner = payload.owner_principal or ctx.principal
    _guard_owner_assign(ctx, owner)
    payload.base_url = _validate_base_url(payload.base_url, provider=payload.provider) or None
    actor = _principal(manager)
    created = _run_guarded(
        _store().create,
        payload.model_dump(),
        actor,
        owner_principal=owner,
    )
    _audit_owner_change(actor, created["id"], owner, "")
    return _with_pricing(created)


@router.put("/models/{model_id}")
def update_model(
    model_id: str,
    payload: ModelUpdatePayload,
    manager=Depends(require_model_manager),
) -> Dict[str, Any]:
    stored = _run_guarded(_store().get_public, model_id)
    ctx = _context(manager)
    scope = key_scope_for(principal=ctx.principal)
    _guard_visible(scope, stored.get("owner_principal"))
    data = payload.model_dump(exclude_unset=True)
    if "base_url" in data:
        provider = data.get("provider") or stored.get("provider")
        data["base_url"] = _validate_base_url(
            data["base_url"], provider=provider, stored_url=stored.get("base_url")
        ) or None
    if "owner_principal" in data:
        _guard_owner_assign(ctx, data["owner_principal"])
    updated = _run_guarded(
        _store().update, model_id, data, _principal(manager)
    )
    _audit_owner_change(
        _principal(manager),
        model_id,
        str(updated.get("owner_principal") or ""),
        str(stored.get("owner_principal") or ""),
    )
    return _with_pricing(updated)


@router.delete("/models/{model_id}")
def delete_model(model_id: str, manager=Depends(require_model_manager)) -> Dict[str, Any]:
    stored = _run_guarded(_store().get_public, model_id)
    _guard_visible(
        key_scope_for(principal=_principal(manager)), stored.get("owner_principal")
    )
    return _run_guarded(_store().delete, model_id, _principal(manager))


@router.post("/models/{model_id}/default")
def set_default(model_id: str, manager=Depends(require_model_manager)) -> Dict[str, Any]:
    stored = _run_guarded(_store().get_public, model_id)
    scope = key_scope_for(principal=_principal(manager))
    _guard_visible(scope, stored.get("owner_principal"))
    return _run_guarded(
        _store().set_default,
        model_id,
        _principal(manager),
        visible_to=scope.visible_to,
        allow_unowned=scope.allow_unowned,
    )


# --------------------------------------------------------------------------- #
# 连通性测试
# --------------------------------------------------------------------------- #
_STATUS_RE = re.compile(r"\b(400|401|402|403|404|429|5\d\d)\b")

#: 配额耗尽与“频率限制”是两件事：前者充值才能恢复，后者等一会儿就好。
_QUOTA_TOKENS = (
    "insufficient balance",
    "insufficient_quota",
    "quota exceeded",
    "exceeded your current quota",
    "out of credits",
    "credit balance",
    "billing",
    "余额不足",
    "配额",
)

#: 密钥被吊销/停用：换一把 Key 才能恢复，重试没有意义。
_REVOKED_TOKENS = (
    "revoked",
    "deactivated",
    "suspended",
    "api key disabled",
    "key disabled",
    "key expired",
    "已吊销",
    "已停用",
    "已被禁用",
)

#: 给用户下一步该怎么办；界面回落到 ``detail`` 时也能直接读到中文结论。
_PROBE_HINTS = {
    "invalid_key": "API Key 无效：请检查是否填错或复制不完整",
    "key_revoked": "API Key 已被吊销/停用：请到提供方控制台重新生成",
    "quota_exhausted": "账户额度已耗尽：请到提供方控制台充值或提升额度",
    "rate_limited": "请求被上游限流：请稍后重试",
    "model_not_found": "模型名不存在：请核对提供方支持的模型 ID",
    "unreachable": "端点不可达：请检查 base_url、端口与网络",
    "timeout": "连接超时：请检查网络或调大超时时间",
    "upstream_error": "提供方返回服务端错误：请稍后重试；持续出现请联系提供方",
}


def _classify_error(exc: BaseException) -> str:
    """把上游异常归类成前端可区分的原因。

    分类只看**异常类型与状态码**，不依赖具体厂商文案；文案本身会变化。
    但“额度耗尽”和“密钥吊销”在多数厂商都复用 401/402/429，必须读文案才能
    区分——这两类处理方式完全不同（充值与换 Key），不能都叫“调用失败”。
    """
    name = type(exc).__name__.lower()
    message = str(exc).lower()
    if "timeout" in name or "timed out" in message or "timeout" in message:
        return "timeout"
    # 各 SDK 的连接失败异常名并不统一：``openai`` 抛 ``APIConnectionError``
    # （文案只有一句 ``Connection error.``），标准库与 httpx 则各自不同。
    # 之前只认 ``ConnectionError``，真实"地址填错/端口没开/断网"会被归成
    # ``upstream_error``，界面就说不出"端点不可达"这一步。
    if name in {
        "connectionerror",
        "connecterror",
        "newconnectionerror",
        "apiconnectionerror",
    } or any(
        token in message
        for token in ("connection refused", "failed to connect", "name or service not known",
                      "temporary failure in name resolution", "max retries exceeded",
                      "unreachable", "no route to host", "connection error",
                      "connection aborted", "connection reset", "getaddrinfo failed",
                      "no connection could be made", "actively refused",
                      "errno 11001", "errno 10061")
    ):
        return "unreachable"
    if any(token in message for token in _QUOTA_TOKENS):
        return "quota_exhausted"
    status = getattr(exc, "status_code", None) or getattr(exc, "status", None)
    code = status if isinstance(status, int) else None
    if code is None:
        match = _STATUS_RE.search(message)
        code = int(match.group(1)) if match else None
    if code == 402:
        return "quota_exhausted"
    if code in (401, 403):
        if any(token in message for token in _REVOKED_TOKENS):
            return "key_revoked"
        return "invalid_key"
    if code == 404:
        return "model_not_found"
    if code == 429:
        return "rate_limited"
    if "model not found" in message or "unknown model" in message or "no such model" in message:
        return "model_not_found"
    if "invalid api key" in message or "incorrect api key" in message or "unauthorized" in message:
        return "invalid_key"
    return "upstream_error"


def _sanitize(message: str, api_key: Optional[str]) -> str:
    """错误详情脱敏：移除密钥子串并截断，避免把上游回显的 key 带出去。"""
    if api_key:
        message = message.replace(api_key, "[REDACTED]")
    message = re.sub(r"(sk-[A-Za-z0-9_-]{8,})", "[REDACTED]", message)
    return message[:200]


def _resolve_addresses(raw: str) -> frozenset:
    """Resolve a probe target to a set of literal IP strings."""
    parsed = urlparse((raw or "").strip())
    if not parsed.hostname:
        return frozenset()
    try:
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        infos = socket.getaddrinfo(parsed.hostname, port, proto=socket.IPPROTO_TCP)
    except (socket.gaierror, UnicodeError, ValueError):
        return frozenset()
    return frozenset(str(info[4][0]) for info in infos)


def _guard_probe_target(cfg: Dict[str, Any]) -> None:
    """Reject cross-origin redirects and DNS changes before the real probe.

    Two TOCTOU gaps are narrowed here:

    1. ``_assert_url_allowed`` resolved the host earlier; if it resolves
       differently now, a DNS-rebinding target is in play. Re-resolve and
       refuse on mismatch.
    2. Native SDKs may follow redirects. We issue a strict ``follow_redirects=
       False`` preflight; a cross-origin 3xx is refused instead of being
       followed into a private network.

    Limitation (documented, not silently ignored): we do not pin the socket
    used by the provider SDK itself, so a server that only redirects *after*
    a specific request is not detectable here. Fully pinning would require
    changing the provider HTTP clients (outside this module's ownership).
    """
    url = str(cfg.get("base_url") or "").strip()
    if not url:
        return
    before = _resolve_addresses(url)
    try:
        import httpx
    except ImportError:  # pragma: no cover - httpx is an openai dependency
        logger.warning("httpx unavailable; redirect/DNS preflight skipped for %s", url)
        return
    try:
        with httpx.Client(
            timeout=httpx.Timeout(5.0),
            follow_redirects=False,
            trust_env=False,
        ) as client:
            response = client.get(url)
    except Exception as exc:  # noqa: BLE001 - connectivity is the probe's job
        logger.debug("redirect preflight for %s failed: %s", url, exc)
    else:
        status = int(response.status_code)
        if 300 <= status < 400:
            location = response.headers.get("location") or ""
            target = urljoin(url, location) if location else ""
            origin = urlparse(url)
            redirected = urlparse(target)
            if (
                not location
                or redirected.scheme != origin.scheme
                or redirected.netloc != origin.netloc
            ):
                raise HTTPException(
                    status_code=400,
                    detail=(
                        "base_url 返回跨域重定向，已拒绝（防 SSRF 绕过）："
                        f"{status} -> {target or '(no location)'}"
                    ),
                )
    after = _resolve_addresses(url)
    if before and after and before != after:
        raise HTTPException(
            status_code=400,
            detail="base_url 解析结果在测试期间发生变化，疑似 DNS rebinding，已拒绝",
        )


def _probe(cfg: Dict[str, Any]) -> Dict[str, Any]:
    _guard_probe_target(cfg)
    api_key = cfg.get("api_key")
    provider = provider_from_config(cfg)
    started = time.monotonic()
    try:
        reply = provider.generate("ping", max_tokens=8, temperature=0.0)
    except Exception as exc:
        elapsed = round((time.monotonic() - started) * 1000.0, 1)
        reason = _classify_error(exc)
        raw_detail = _sanitize(f"{type(exc).__name__}: {exc}", api_key)
        # 上游原文只留在服务端日志（排障/诊断包），响应里只给用户"下一步改什么"。
        # 回显英文原文既看不懂，也容易把 key/内部地址带出去。
        logger.info(
            "model probe failed provider=%s model=%s reason=%s detail=%s",
            cfg.get("provider"),
            cfg.get("model"),
            reason,
            raw_detail,
        )
        hint = _PROBE_HINTS.get(reason)
        detail = hint or "连接失败：请检查服务商、模型名与端点设置后重试。"
        _audit_probe(cfg, "failure", reason)
        return {
            "ok": False,
            "reason": reason,
            "hint": hint or "",
            "detail": detail,
            "latency_ms": elapsed,
            "provider": cfg.get("provider"),
            "vendor": vendor_for(cfg.get("provider"), cfg.get("base_url")),
            "model": cfg.get("model"),
        }
    elapsed = round((time.monotonic() - started) * 1000.0, 1)
    _audit_probe(cfg, "success", "")
    return {
        "ok": True,
        "reason": "",
        "hint": "",
        "detail": _sanitize(str(reply)[:120], api_key),
        "latency_ms": elapsed,
        "provider": cfg.get("provider"),
        "vendor": vendor_for(cfg.get("provider"), cfg.get("base_url")),
        "model": cfg.get("model"),
    }


def _audit_probe(cfg: Dict[str, Any], outcome: str, reason: str) -> None:
    try:
        from ..ai.audit import emit

        emit(
            "ai.model_config.test_connection",
            module="src.gateway.models",
            outcome=outcome,
            reason=reason,
            provider=str(cfg.get("provider") or ""),
            vendor=vendor_for(cfg.get("provider"), cfg.get("base_url")),
            model=str(cfg.get("model") or ""),
        )
    except Exception as exc:  # noqa: BLE001
        # Never silently drop an audit write: it is the only durable record that
        # a connection test happened at all.
        logger.error(
            "failed to emit model test-connection audit event: %s",
            exc,
            exc_info=True,
        )
        try:
            from ..observability.metrics import provider_errors_total

            provider_errors_total.labels(
                provider=str(cfg.get("provider") or "unknown"),
                model=str(cfg.get("model") or "unknown"),
                error_type="audit_emit_failed",
            ).inc()
        except Exception:  # pragma: no cover - metrics must not break requests
            pass


_PRIVATE_IP_FLAGS = (
    "is_private",
    "is_loopback",
    "is_link_local",
    "is_reserved",
    "is_unspecified",
    "is_multicast",
)


def _assert_url_allowed(raw: str, *, allow_private: bool) -> str:
    """校验待探测的 ``base_url``，堵住管理员 SSRF 面。

    * 只接受 ``http`` / ``https``：``file://`` / ``gopher://`` 等一律 400；
    * 调用方**现填**的地址必须是公网可达主机 —— 解析后任一地址落在私网/
      回环/链路本地/保留段即 400（否则管理员端点可被用来探测内网与元数据
      服务，例如 ``169.254.169.254``）。

    ``allow_private=True`` 只用于**已保存配置**里的地址，或与已保存配置逐字
    相同的地址：它们由管理员事先落盘批准（本机 Ollama ``127.0.0.1`` 就是
    正常用法），不是本次请求凭空构造的探测目标。
    """
    value = (raw or "").strip()
    if not value:
        return ""
    parsed = urlparse(value)
    if parsed.scheme not in ("http", "https"):
        raise HTTPException(
            status_code=400,
            detail=f"base_url 仅支持 http/https，收到：{parsed.scheme or '(空)'}",
        )
    host = parsed.hostname
    if not host:
        raise HTTPException(status_code=400, detail="base_url 缺少合法主机名")
    if allow_private:
        return value
    try:
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    except (socket.gaierror, UnicodeError, ValueError) as exc:
        logger.info("base_url host unresolvable host=%r: %s", host, exc)
        raise HTTPException(
            status_code=400,
            detail=f"填写的服务地址里，主机名「{host}」解析不了。请检查拼写或网络后重试。",
        )
    for info in infos:
        try:
            address = ipaddress.ip_address(info[4][0])
        except ValueError:
            raise HTTPException(status_code=400, detail=f"base_url 主机解析结果异常：{host}")
        if any(getattr(address, flag) for flag in _PRIVATE_IP_FLAGS):
            raise HTTPException(
                status_code=400,
                detail=(
                    "base_url 指向私网/回环/链路本地地址，已拒绝（SSRF 防护）："
                    f"{host} -> {address}"
                ),
            )
    return value


def _test_cfg(payload: TestPayload, scope=None) -> Dict[str, Any]:
    stored = None
    if payload.model_id:
        if scope is None:
            stored = _run_guarded(_store().resolve, payload.model_id)
        else:
            stored = _run_guarded(
                _store().resolve,
                payload.model_id,
                visible_to=scope.visible_to,
                allow_unowned=scope.allow_unowned,
            )
        if stored is None:
            raise HTTPException(status_code=404, detail=f"模型配置不存在：{payload.model_id}")
    provider = payload.provider.strip().lower()
    if provider not in ProviderFactory._providers:
        raise HTTPException(status_code=400, detail=f"不支持的 provider 类型：{provider}")
    api_key = payload.api_key
    # 编辑态回传的掩码不是新 Key：测试时回落到已保存的真实 Key。
    if api_key and is_masked_placeholder(api_key):
        api_key = None
    if not api_key and stored:
        api_key = stored.get("api_key")
    # 调用方现填的地址按公网收紧；但若它**恰好等于**已保存配置里的地址，
    # 说明该目标已被管理员落盘批准（本机 Ollama 就是这种正常用法），此时
    # 放行私网/回环。这样既关掉了"任意地址探测内网"的 SSRF 面，又不至于把
    # 模型管理界面的"测试当前编辑配置"对本地模型一刀切禁掉。
    raw_caller = (payload.base_url or "").strip()
    stored_url = stored.get("base_url") if stored else None
    caller_url = _validate_base_url(
        raw_caller,
        provider=provider,
        stored_url=stored_url,
    )
    resolved_url = caller_url or _validate_base_url(
        stored_url, provider=provider, stored_url=stored_url
    )
    return {
        "name": stored.get("name") if stored else "connection-test",
        "provider": provider,
        "model": payload.model.strip(),
        "base_url": resolved_url or None,
        "api_key": api_key,
        "timeout": min(float(payload.timeout), 20.0),
    }


@router.post("/models/test")
def test_connection(
    payload: TestPayload, manager=Depends(require_model_manager)
) -> Dict[str, Any]:
    scope = key_scope_for(principal=_principal(manager))
    return _probe(_test_cfg(payload, scope))


@router.post("/models/{model_id}/test")
def test_saved_model(
    model_id: str, manager=Depends(require_model_manager)
) -> Dict[str, Any]:
    scope = key_scope_for(principal=_principal(manager))
    cfg = _run_guarded(
        _store().resolve,
        model_id,
        visible_to=scope.visible_to,
        allow_unowned=scope.allow_unowned,
    )
    if cfg is None:
        raise HTTPException(status_code=404, detail=f"模型配置不存在或已禁用：{model_id}")
    cfg = dict(cfg)
    # 已保存配置：仅对本机 Ollama 保留私网/回环放行；其他私网地址重新收紧。
    cfg["base_url"] = _validate_base_url(
        cfg.get("base_url") or "",
        provider=cfg.get("provider"),
        stored_url=cfg.get("base_url"),
    )
    cfg["timeout"] = min(float(cfg.get("timeout") or 30.0), 20.0)
    return _probe(cfg)
