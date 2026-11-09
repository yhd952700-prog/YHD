"""5xx 错误信封：用户可读文案显式透传 / 内部错误仍被掩码。

背景
----
统一掩码把 5xx 的字符串 ``detail`` 换成通用文案后，"知识库未启用…请执行
ollama pull …" 这类**照着做就能恢复**的运维文案也被吃掉了。修复规则：

* 只有显式继承 ``UserReadableHTTPException`` 的 5xx 才允许透传（不猜字符串）；
* 透传前仍过 ``is_public_5xx_detail_safe``（路径/堆栈/SQL/数据库名一律掩码）；
* 未标记的 5xx 继续返回 ``internal_error_body``，原文只进日志。
"""
from __future__ import annotations

import inspect

import pytest
from fastapi import HTTPException
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from src.gateway.display import (
    INTERNAL_ERROR_MESSAGE,
    UserReadableHTTPException,
    is_public_5xx_detail_safe,
    sanitize_user_copy,
)


@pytest.fixture
def app_client(monkeypatch, tmp_path):
    monkeypatch.setenv("LIUHAO_HUMAN_IDENTITIES_BACKEND", "file")
    monkeypatch.setenv("LIUHAO_HUMAN_IDENTITIES_FILE", str(tmp_path / "humans.json"))
    monkeypatch.setenv(
        "LIUHAO_HUMAN_IDENTITIES_INTEGRITY_KEY", "public-5xx-test-key"
    )
    import src.kernels.identity as identity_module

    monkeypatch.setattr(identity_module, "_global_manager", None)
    from src.gateway.main import get_app

    app = get_app()
    with TestClient(app) as client:
        yield client, app


def _headers(subject: str = "someone") -> dict:
    from src.security import get_jwt_handler

    token, _ = get_jwt_handler().create_token(
        subject=subject,
        permissions=["owner"],
        metadata={"kind": "human", "scope": "L0"},
    )
    return {"Authorization": f"Bearer {token}"}


def test_knowledge_unconfigured_503_detail_is_user_readable(
    app_client, monkeypatch, tmp_path
):
    """知识库未配置 → 503 的「知识库未启用…」必须能被用户看到。"""
    from src.knowledge.documents import KnowledgeDocumentService
    from src.knowledge.embedding import EmbeddingUnavailableError
    from src.knowledge.vector_store import VectorStore

    class _BoomEmbedder:
        name = "unavailable"
        model = "none"

        def embed(self, texts):
            raise EmbeddingUnavailableError(
                "没有可用的 embedding provider。请启动本机 Ollama"
                "（http://127.0.0.1:11434），或设置 LIUHAO_EMBEDDING_PROVIDER。"
            )

    service = KnowledgeDocumentService(
        vector_store=VectorStore(), embedder=_BoomEmbedder()
    )
    import src.api.routes.knowledge as knowledge_module

    monkeypatch.setattr(knowledge_module, "get_knowledge_service", lambda: service)
    client, _app = app_client
    resp = client.post(
        "/v1/knowledge/documents",
        headers=_headers(),
        json={"text": "hello"},
    )
    assert resp.status_code == 503
    detail = resp.json()["detail"]
    assert "知识库未启用" in detail
    assert "ollama" in detail.lower() or "OLLAMA" in detail
    assert detail != INTERNAL_ERROR_MESSAGE


def test_unmarked_5xx_with_internal_path_is_masked(app_client):
    """未标记的 5xx 即使 detail 是中文，也不能把内部路径带上屏。"""
    client, app = app_client

    def _boom():
        raise HTTPException(
            status_code=500,
            detail=r"读取失败：C:\Users\secret\src\gateway\main.py line 123",
        )

    app.router.routes.append(APIRoute("/_test_internal_5xx", _boom, methods=["GET"]))
    resp = client.get("/_test_internal_5xx")
    assert resp.status_code == 500
    body = resp.json()
    assert body["detail"] == INTERNAL_ERROR_MESSAGE
    assert "secret" not in resp.text
    assert "trace_id" in body


def test_marked_5xx_still_masked_when_detail_leaks_internals(app_client):
    """显式标记不是免检：文案里带路径/SQL 时仍按通用掩码返回。"""
    client, app = app_client

    def _boom():
        raise UserReadableHTTPException(
            status_code=503,
            detail="请检查 C:\\Users\\secret\\app.db 并执行 SELECT * FROM users",
        )

    app.router.routes.append(APIRoute("/_test_marked_leak", _boom, methods=["GET"]))
    resp = client.get("/_test_marked_leak")
    assert resp.status_code == 503
    assert resp.json()["detail"] == INTERNAL_ERROR_MESSAGE


def test_marked_5xx_with_safe_detail_is_passed_through(app_client):
    """显式标记 + 安全文案 → 原样透传（只做术语脱敏，不改业务含义）。"""
    client, app = app_client
    message = "知识库未启用：请启动本机 Ollama 后重试。"

    def _boom():
        raise UserReadableHTTPException(status_code=503, detail=message)

    app.router.routes.append(APIRoute("/_test_marked_safe", _boom, methods=["GET"]))
    resp = client.get("/_test_marked_safe")
    assert resp.status_code == 503
    assert resp.json()["detail"] == message


# --------------------------------------------------------------------------- #
# 运维类 5xx：逐条判定"可操作"后显式透传；含内部细节的继续掩码
# --------------------------------------------------------------------------- #
_PROVIDER_ENVS = (
    "LIUHAO_EMBEDDING_PROVIDER",
    "LIUHAO_EMBEDDING_MODEL",
    "LIUHAO_EMBEDDING_API_KEY",
    "LIUHAO_KNOWLEDGE_ALLOW_MOCK",
    "OPENAI_API_KEY",
    "OPENAI_BASE_URL",
    "OLLAMA_BASE_URL",
    "LIUHAO_OLLAMA_BASE_URL",
    "LIUHAO_ENV",
    "APP_ENV",
)


@pytest.fixture
def account_client(monkeypatch, tmp_path):
    """真实网关 + 独立临时工作区 + 已注册主账号，供业务面端点使用。"""
    for name in _PROVIDER_ENVS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("LIUHAO_ENV", "development")
    monkeypatch.setenv("LIUHAO_KNOWLEDGE_ALLOW_MOCK", "1")
    monkeypatch.setenv("LIUHAO_EMBEDDING_PROVIDER", "mock")
    monkeypatch.setenv("LIUHAO_WORKSPACE_ROOT", str(tmp_path))
    monkeypatch.setenv("LIUHAO_HUMAN_IDENTITIES_BACKEND", "file")
    monkeypatch.setenv("LIUHAO_HUMAN_IDENTITIES_FILE", str(tmp_path / "humans.json"))
    monkeypatch.setenv(
        "LIUHAO_HUMAN_IDENTITIES_INTEGRITY_KEY", "public-5xx-test-key"
    )
    monkeypatch.setenv("AUDIT_DB_PATH", str(tmp_path / "audit.db"))
    monkeypatch.setenv("CONVERSATION_DB_PATH", str(tmp_path / "conversation.db"))
    monkeypatch.setenv("HTTP_PROXY", "")
    monkeypatch.setenv("HTTPS_PROXY", "")
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")

    import src.gateway.identity as identity_module
    import src.kernels.identity as kernels_identity
    from src.gateway.ai_management import AIStateManager
    from src.knowledge.documents import reset_knowledge_service

    monkeypatch.setattr(kernels_identity, "_global_manager", None)
    AIStateManager._instance = None
    reset_knowledge_service()
    kernels_identity.get_identity_manager().register_root_account("someone")

    from src.gateway.main import get_app

    with TestClient(get_app()) as client:
        yield client, identity_module

    AIStateManager._instance = None
    reset_knowledge_service()
    monkeypatch.setattr(kernels_identity, "_global_manager", None)


class _FakeManager:
    """替身：按需在列举/查询时抛错，用来触达 503 分支。"""

    lifecycle = None

    def __init__(self, *, list_boom: bool = False, get_boom: bool = False):
        self._list_boom = list_boom
        self._get_boom = get_boom

    def list_identities(self):
        if self._list_boom:
            raise RuntimeError("registry enumeration failed")
        return []

    def get_identity(self, _id):
        if self._get_boom:
            raise RuntimeError("identity lookup failed")
        return None

    def get_identity_by_principal(self, _name):
        if self._get_boom:
            raise RuntimeError("identity lookup failed")
        return None

    def describe_identity_namespaces(self):
        return {}


def _patch_kernel_manager(monkeypatch, behavior):
    """只让 ``gateway.identity._manager`` 看到替换结果，其它调用方仍拿真实管理器。

    ``require_human_principal`` / ``_caller_scope`` 也会取管理器；若全局替换，
    认证会先失败，测不到端点里那条 503。
    """
    import src.kernels.identity as kernels_identity

    real = kernels_identity.get_identity_manager()

    def _fake():
        if any(frame.function == "_manager" for frame in inspect.stack()):
            return behavior(real)
        return real

    monkeypatch.setattr(kernels_identity, "get_identity_manager", _fake)


@pytest.mark.parametrize(
    ("patch_target", "expected"),
    [
        ("manager", "身份服务暂时不可用，本次读取没有完成。请稍后重试。"),
        ("registry", "身份列表暂时读不出来，本次读取没有完成。请稍后重试。"),
        ("collect", "身份列表暂时读不出来，本次读取没有完成。请稍后重试。"),
    ],
)
def test_identity_service_503_details_are_user_readable(
    account_client, monkeypatch, patch_target, expected
):
    """身份服务 4 条可操作 503 中，列表侧 3 条必须原样透传。"""
    client, identity_module = account_client

    def _raise(_manager):
        raise RuntimeError("identity backend failed")

    if patch_target == "manager":
        _patch_kernel_manager(monkeypatch, _raise)
    elif patch_target == "registry":
        import src.kernels.identity as kernels_identity

        real = kernels_identity.get_identity_manager()

        def _registry_boom(*_args, **_kwargs):
            raise RuntimeError("registry describe failed")

        monkeypatch.setattr(real, "describe_identity_namespaces", _registry_boom)
        monkeypatch.setattr(identity_module, "_manager", lambda: real)
    else:
        monkeypatch.setattr(
            identity_module, "_manager", lambda: _FakeManager(list_boom=True)
        )

    resp = client.get("/v1/identity/principals", headers=_headers())
    assert resp.status_code == 503, resp.text
    assert resp.json()["detail"] == expected, resp.text


def test_identity_lookup_503_detail_is_user_readable(account_client, monkeypatch):
    client, identity_module = account_client
    monkeypatch.setattr(
        identity_module, "_manager", lambda: _FakeManager(get_boom=True)
    )
    resp = client.get("/v1/identity/principals/nobody", headers=_headers())
    assert resp.status_code == 503, resp.text
    assert resp.json()["detail"] == "身份查询没有完成，请稍后重试。", resp.text


def test_identity_lifecycle_503_with_internals_stays_masked(account_client, monkeypatch):
    """lifecycle 文案是英文实现细节 → 即使有标记也必须继续掩码。"""

    class _NotReady:
        def __init__(self, real):
            self.lifecycle = "SHUTDOWN"
            self._real = real

        def __getattr__(self, name):
            return getattr(self._real, name)

    _patch_kernel_manager(monkeypatch, _NotReady)
    client, _identity_module = account_client
    resp = client.get("/v1/identity/principals", headers=_headers())
    assert resp.status_code == 503, resp.text
    body = resp.json()
    assert body["detail"] == INTERNAL_ERROR_MESSAGE, resp.text
    assert "SHUTDOWN" not in resp.text


class _KnowledgeServiceStub:
    def __init__(self, *, enabled: bool, reason: str = "", ingest_boom: bool = False):
        self._enabled = enabled
        self._reason = reason
        self._ingest_boom = ingest_boom

    def status(self):
        return {"enabled": self._enabled, "reason": self._reason}

    def ingest(self, *_args, **_kwargs):
        if self._ingest_boom:
            from src.knowledge.embedding import EmbeddingUnavailableError

            raise EmbeddingUnavailableError("embedding provider offline")
        return {"deduplicated": False}


def test_data_portability_knowledge_status_503_is_user_readable(
    account_client, monkeypatch
):
    """知识库未启用的原因已经是可操作中文 → 原样透传。"""
    stub = _KnowledgeServiceStub(
        enabled=False, reason="知识库不可用：请先启动本机 Ollama 后重试。"
    )
    import src.knowledge.documents as documents_module

    monkeypatch.setattr(documents_module, "get_knowledge_service", lambda: stub)
    client, _identity_module = account_client
    resp = client.post(
        "/v1/knowledge/import",
        headers=_headers(),
        json={"documents": [{"text": "hello"}]},
    )
    assert resp.status_code == 503, resp.text
    assert resp.json()["detail"] == stub.status()["reason"], resp.text


def test_data_portability_knowledge_ingest_503_is_user_readable(
    account_client, monkeypatch
):
    """回导时 embedding 不可用 → 告知"暂时不可用，请稍后重试"。"""
    stub = _KnowledgeServiceStub(enabled=True, ingest_boom=True)
    import src.knowledge.documents as documents_module

    monkeypatch.setattr(documents_module, "get_knowledge_service", lambda: stub)
    client, _identity_module = account_client
    resp = client.post(
        "/v1/knowledge/import",
        headers=_headers(),
        json={"documents": [{"text": "hello"}]},
    )
    assert resp.status_code == 503, resp.text
    assert resp.json()["detail"] == "知识库暂时不可用，请稍后重试。", resp.text


@pytest.mark.parametrize(
    ("method", "path", "body", "attr", "expected"),
    [
        (
            "POST",
            "/v1/goals",
            {"natural_language": "整理本周工作"},
            "create_and_execute_goal",
            "目标执行没有完成。请稍后重试；如果持续出现，请重启软件后再试。",
        ),
        (
            "POST",
            "/v1/goals/g1/replan",
            None,
            "replan_goal",
            "重新规划没有完成。请稍后重试；如果持续出现，请重启软件后再试。",
        ),
        (
            "POST",
            "/v1/goals/assign",
            {"agent_id": "a1", "instruction": "写一份总结"},
            "assign_task",
            "任务派发没有完成。请稍后重试；如果持续出现，请重启软件后再试。",
        ),
        (
            "POST",
            "/v1/goals/g1/tasks/t1/retry",
            None,
            "retry_task",
            "重试任务没有完成。请稍后重试；如果持续出现，请重启软件后再试。",
        ),
        (
            "GET",
            "/v1/employees",
            None,
            "list_all_employees",
            "读取员工列表没有完成。请稍后重试。",
        ),
    ],
)
def test_ai_management_operational_503s_are_user_readable(
    account_client, monkeypatch, method, path, body, attr, expected
):
    """目标执行/重规划/派发/重试/员工列表 5 处 503：全是可操作中文，必须透传。"""
    from src.gateway import ai_management

    def _boom(*_args, **_kwargs):
        raise RuntimeError("execution backend crashed")

    monkeypatch.setattr(ai_management.AIStateManager, attr, _boom)
    client, _identity_module = account_client
    resp = client.request(
        method, path, headers=_headers(), json=body if body is not None else None
    )
    assert resp.status_code == 503, resp.text
    assert resp.json()["detail"] == expected, resp.text


# --------------------------------------------------------------------------- #
# 脱敏替换不能吃掉收尾标点：只换敏感片段本身，``」`` / ``。`` / ``）`` / ``，`` 原样保留
# --------------------------------------------------------------------------- #
def test_sanitize_user_copy_keeps_closing_quote_after_path():
    """带路径 + 中文引号：替换路径本体，``」`` 与句末 ``。`` 都要在。"""
    text = (
        "无法写入「D:\\LiuHao-AI-OS\\data\\conversation.db」：磁盘已满。"
        "请清理磁盘可用空间后重试。"
    )
    out = sanitize_user_copy(text)
    assert "相关位置」：" in out, out
    assert "conversation.db" not in out
    assert "LiuHao-AI-OS" not in out
    assert out.endswith("。"), out


def test_sanitize_user_copy_keeps_punctuation_after_drive_path():
    """盘符路径后接 ``」，``：右引号和逗号都不能被路径匹配吞掉。"""
    text = "读取失败：C:\\Users\\secret\\app.db」，请稍后重试。"
    out = sanitize_user_copy(text)
    assert out == "读取失败：相关位置」，请稍后重试。", out
    assert "secret" not in out


def test_sanitize_user_copy_keeps_punctuation_in_sql_copy():
    """含 SQL 的文案：路径片段被替换、``）`` 与 ``，`` 保留；SQL 识别未放宽。"""
    text = "执行 SELECT * FROM users 失败（D:\\data\\app.db），请稍后重试。"
    out = sanitize_user_copy(text)
    assert "（相关位置），" in out, out
    assert "app.db" not in out
    assert out.endswith("。"), out
    # 收尾标点保留 ≠ 放宽识别：含 SQL / 路径的原文仍过不了 5xx 安全闸。
    assert not is_public_5xx_detail_safe(text)
    assert not is_public_5xx_detail_safe("执行 SELECT * FROM users 失败")
    assert not is_public_5xx_detail_safe("请检查 C:\\Users\\secret\\app.db")


def test_sanitize_user_copy_keeps_normal_cjk_quotes():
    """正常中文含 ``「」`` 且无内部片段：一字不改（包括引号与句末标点）。"""
    text = "本轮对话未能保存「工作区」：磁盘已满。请清理磁盘可用空间后重试。"
    assert sanitize_user_copy(text) == text
