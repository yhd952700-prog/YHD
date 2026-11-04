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

import re
import time
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from .policy import require_bearer_payload
from ..ai.model_config import (
    ModelConfigError,
    SUPPORTED_PROVIDERS,
    get_model_config_store,
)
from ..ai.providers import ProviderFactory, provider_from_config

router = APIRouter(prefix="/v1", tags=["models"])

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


class ModelUpdatePayload(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=64)
    provider: Optional[str] = None
    model: Optional[str] = Field(None, min_length=1, max_length=128)
    base_url: Optional[str] = Field(None, max_length=512)
    api_key: Optional[str] = Field(None, max_length=512, description="留空表示不修改")
    timeout: Optional[float] = Field(None, ge=1.0, le=600.0)
    enabled: Optional[bool] = None
    is_default: Optional[bool] = None


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


def _run_guarded(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except ModelConfigError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail)


@router.get("/models")
def list_models() -> Dict[str, Any]:
    store = _store()
    return {
        "models": store.list_models(),
        "default_id": store.default_id(),
        "count": len(store.list_models()),
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
                "requires_api_key": name not in PROVIDERS_WITHOUT_KEY,
                "supports_base_url": name not in ("mock", "google"),
            }
            for name in SUPPORTED_PROVIDERS
        ]
    }


@router.get("/models/{model_id}")
def get_model(model_id: str) -> Dict[str, Any]:
    return _run_guarded(_store().get_public, model_id)


@router.post("/models")
def create_model(payload: ModelPayload, admin=Depends(require_model_admin)) -> Dict[str, Any]:
    return _run_guarded(_store().create, payload.model_dump(), _principal(admin))


@router.put("/models/{model_id}")
def update_model(
    model_id: str, payload: ModelUpdatePayload, admin=Depends(require_model_admin)
) -> Dict[str, Any]:
    data = payload.model_dump(exclude_unset=True)
    return _run_guarded(_store().update, model_id, data, _principal(admin))


@router.delete("/models/{model_id}")
def delete_model(model_id: str, admin=Depends(require_model_admin)) -> Dict[str, Any]:
    return _run_guarded(_store().delete, model_id, _principal(admin))


@router.post("/models/{model_id}/default")
def set_default(model_id: str, admin=Depends(require_model_admin)) -> Dict[str, Any]:
    return _run_guarded(_store().set_default, model_id, _principal(admin))


# --------------------------------------------------------------------------- #
# 连通性测试
# --------------------------------------------------------------------------- #
_STATUS_RE = re.compile(r"\b(401|403|404|429|5\d\d)\b")


def _classify_error(exc: BaseException) -> str:
    """把上游异常归类成前端可区分的原因。

    分类只看**异常类型与状态码**，不依赖具体厂商文案；文案本身会变化。
    """
    name = type(exc).__name__.lower()
    message = str(exc).lower()
    if "timeout" in name or "timed out" in message or "timeout" in message:
        return "timeout"
    if name in {"connectionerror", "connecterror", "newconnectionerror"} or any(
        token in message
        for token in ("connection refused", "failed to connect", "name or service not known",
                      "temporary failure in name resolution", "max retries exceeded",
                      "unreachable", "no route to host")
    ):
        return "unreachable"
    status = getattr(exc, "status_code", None) or getattr(exc, "status", None)
    code = status if isinstance(status, int) else None
    if code is None:
        match = _STATUS_RE.search(message)
        code = int(match.group(1)) if match else None
    if code in (401, 403):
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


def _probe(cfg: Dict[str, Any]) -> Dict[str, Any]:
    api_key = cfg.get("api_key")
    provider = provider_from_config(cfg)
    started = time.monotonic()
    try:
        reply = provider.generate("ping", max_tokens=8, temperature=0.0)
    except Exception as exc:
        elapsed = round((time.monotonic() - started) * 1000.0, 1)
        reason = _classify_error(exc)
        detail = _sanitize(f"{type(exc).__name__}: {exc}", api_key)
        _audit_probe(cfg, "failure", reason)
        return {
            "ok": False,
            "reason": reason,
            "detail": detail,
            "latency_ms": elapsed,
            "provider": cfg.get("provider"),
            "model": cfg.get("model"),
        }
    elapsed = round((time.monotonic() - started) * 1000.0, 1)
    _audit_probe(cfg, "success", "")
    return {
        "ok": True,
        "reason": "",
        "detail": _sanitize(str(reply)[:120], api_key),
        "latency_ms": elapsed,
        "provider": cfg.get("provider"),
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
            model=str(cfg.get("model") or ""),
        )
    except Exception:
        pass


def _test_cfg(payload: TestPayload) -> Dict[str, Any]:
    stored = None
    if payload.model_id:
        stored = _run_guarded(_store().resolve, payload.model_id)
        if stored is None:
            raise HTTPException(status_code=404, detail=f"模型配置不存在：{payload.model_id}")
    provider = payload.provider.strip().lower()
    if provider not in ProviderFactory._providers:
        raise HTTPException(status_code=400, detail=f"不支持的 provider 类型：{provider}")
    api_key = payload.api_key
    if not api_key and stored:
        api_key = stored.get("api_key")
    return {
        "name": stored.get("name") if stored else "connection-test",
        "provider": provider,
        "model": payload.model.strip(),
        "base_url": payload.base_url or (stored.get("base_url") if stored else None),
        "api_key": api_key,
        "timeout": min(float(payload.timeout), 20.0),
    }


@router.post("/models/test")
def test_connection(payload: TestPayload, admin=Depends(require_model_admin)) -> Dict[str, Any]:
    return _probe(_test_cfg(payload))


@router.post("/models/{model_id}/test")
def test_saved_model(model_id: str, admin=Depends(require_model_admin)) -> Dict[str, Any]:
    cfg = _run_guarded(_store().resolve, model_id)
    if cfg is None:
        raise HTTPException(status_code=404, detail=f"模型配置不存在或已禁用：{model_id}")
    cfg = dict(cfg)
    cfg["timeout"] = min(float(cfg.get("timeout") or 30.0), 20.0)
    return _probe(cfg)
