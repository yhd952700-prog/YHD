"""Provider 默认行为回归测试（方向②：端到端真实运行）。

锁定 ``get_provider()`` 的行为：
- 无 ``AI_PROVIDER_TYPE``（os.environ 与 .env 均无）时默认 MockProvider
  （不触发假 key 网络调用）
- 默认 model 为 ``mock-model``（非占位符 ``[REDACTED]``）
- 显式指定 type 时仍按 env 生效（openai -> OpenAIProvider）
- ``.env`` 文件兜底：os.environ 未设置时经 ConfigManager 读 ``.env``
  （优先级：os.environ > .env > 默认；见 ``_provider_env``）
- ``OpenAIProvider`` 的 ``OPENAI_BASE_URL`` / ``OPENAI_PROXY`` 同样走
  ``_provider_env``（回归：原先只读 os.environ，写在 ``.env`` 里不生效）
- ``AI_PROVIDER_TIMEOUT`` 可选覆盖超时；非法值回落默认并打印提示
- ``OpenAIProvider`` 把 ``self.timeout`` 真的交给 SDK 客户端
"""

import pytest


def _clear_env(monkeypatch):
    for k in (
        "AI_PROVIDER_TYPE",
        "AI_PROVIDER_MODEL",
        "AI_PROVIDER_KEY",
        "AI_PROVIDER_NAME",
    ):
        monkeypatch.delenv(k, raising=False)


def _stub_dotenv(monkeypatch, dotenv=None):
    """桩掉 ConfigManager 的 .env 查询，隔离项目真实 .env 的影响。"""
    import src.config_manager as cm

    store = dict(dotenv or {})
    monkeypatch.setattr(cm, "get", lambda key, default=None: store.get(key, default))


def _isolate(monkeypatch, env=None, dotenv=None):
    """完全隔离：清 os.environ + 桩 .env，再可选注入 env / dotenv 值。"""
    _clear_env(monkeypatch)
    _stub_dotenv(monkeypatch, dotenv)
    for k, v in (env or {}).items():
        monkeypatch.setenv(k, v)


def test_get_provider_defaults_to_mock(monkeypatch):
    _isolate(monkeypatch)  # os.environ 与 .env 均无
    from src.ai.providers import MockProvider, get_provider, reset_provider

    reset_provider()
    try:
        p = get_provider()
        assert isinstance(p, MockProvider)
    finally:
        reset_provider()


def test_get_provider_default_model_not_placeholder(monkeypatch):
    _isolate(monkeypatch)
    from src.ai.providers import get_provider, reset_provider

    reset_provider()
    try:
        p = get_provider()
        assert p.model == "mock-model"
        assert "[REDACTED]" not in p.model
    finally:
        reset_provider()


def test_get_provider_respects_explicit_type(monkeypatch):
    _isolate(monkeypatch, env={"AI_PROVIDER_TYPE": "openai", "AI_PROVIDER_MODEL": "gpt-4o-mini"})
    from src.ai.providers import OpenAIProvider, get_provider, reset_provider

    reset_provider()
    try:
        p = get_provider()
        assert isinstance(p, OpenAIProvider)
        assert p.model == "gpt-4o-mini"
    finally:
        reset_provider()


# ==================== _provider_env：os.environ > .env > 默认 ====================


def test_provider_env_os_environ_wins(monkeypatch):
    _isolate(
        monkeypatch,
        env={"AI_PROVIDER_TYPE": "openai"},
        dotenv={"AI_PROVIDER_TYPE": "ollama"},
    )
    from src.ai.providers import _provider_env

    assert _provider_env("AI_PROVIDER_TYPE", "mock") == "openai"


def test_provider_env_dotenv_fallback(monkeypatch):
    _isolate(monkeypatch, dotenv={"AI_PROVIDER_TYPE": "ollama"})
    from src.ai.providers import _provider_env

    assert _provider_env("AI_PROVIDER_TYPE", "mock") == "ollama"


def test_provider_env_default_when_both_absent(monkeypatch):
    _isolate(monkeypatch)
    from src.ai.providers import _provider_env

    assert _provider_env("AI_PROVIDER_TYPE", "mock") == "mock"


def test_provider_env_config_manager_unavailable(monkeypatch):
    """ConfigManager 抛异常时静默回退默认值（provider 可用性优先）。"""
    _clear_env(monkeypatch)
    import src.config_manager as cm

    def _boom(key, default=None):
        raise RuntimeError("config manager down")

    monkeypatch.setattr(cm, "get", _boom)
    from src.ai.providers import _provider_env

    assert _provider_env("AI_PROVIDER_TYPE", "mock") == "mock"


def test_get_provider_reads_dotenv(monkeypatch):
    """.env 兜底端到端：os.environ 未设置时按 .env 的 ollama 配置实例化。"""
    _isolate(
        monkeypatch,
        dotenv={
            "AI_PROVIDER_TYPE": "ollama",
            "AI_PROVIDER_MODEL": "qwen2.5:3b",
        },
    )
    from src.ai.providers import OllamaProvider, get_provider, reset_provider

    reset_provider()
    try:
        p = get_provider()
        assert isinstance(p, OllamaProvider)
        assert p.model == "qwen2.5:3b"
    finally:
        reset_provider()


# ==================== OpenAI 传输配置（base_url / timeout） ====================


def test_openai_base_url_reads_dotenv(monkeypatch):
    """``OPENAI_BASE_URL`` 写在 ``.env`` 里也必须生效。

    回归：``OpenAIProvider`` 原先用裸 ``os.environ.get("OPENAI_BASE_URL")`` 解析，
    而 ConfigManager 加载 ``.env`` 时**不导出到 os.environ**，于是该键写在 ``.env``
    里形同虚设 —— 自建/代理端点被静默忽略，请求打到 api.openai.com。
    """
    _isolate(
        monkeypatch,
        dotenv={"OPENAI_BASE_URL": "https://example.invalid/v1"},
    )
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    from src.ai.providers import OpenAIProvider

    p = OpenAIProvider(name="t", model="m", api_key="sk-test")
    assert p.base_url == "https://example.invalid/v1"


def test_openai_base_url_os_environ_wins(monkeypatch):
    """os.environ 优先于 ``.env``（与 ``_provider_env`` 既定优先级一致）。"""
    _isolate(
        monkeypatch,
        env={"OPENAI_BASE_URL": "https://from-env.invalid/v1"},
        dotenv={"OPENAI_BASE_URL": "https://from-dotenv.invalid/v1"},
    )
    from src.ai.providers import OpenAIProvider

    p = OpenAIProvider(name="t", model="m", api_key="sk-test")
    assert p.base_url == "https://from-env.invalid/v1"


def test_openai_client_honors_configured_timeout(monkeypatch):
    """``self.timeout`` 必须真的交给 SDK 客户端。

    回归：``_get_client`` 原先不传 timeout，SDK 自己默认 10 分钟，于是声明的
    超时形同虚设（其它 provider 均传 ``timeout=self.timeout``）。
    """
    _isolate(monkeypatch)
    import openai

    from src.ai.providers import OpenAIProvider

    captured = {}

    class _FakeClient:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr(openai, "OpenAI", _FakeClient)
    p = OpenAIProvider(name="t", model="m", api_key="sk-test", timeout=123.0)
    p._get_client()
    assert captured.get("timeout") == 123.0


def test_openai_client_disables_redirects(monkeypatch):
    """运行时必须关闭自动重定向，302 不能把请求带去内网。"""
    _isolate(monkeypatch)
    import httpx
    import openai

    from src.ai.providers import OpenAIProvider

    captured = {}

    class _FakeClient:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr(openai, "OpenAI", _FakeClient)
    p = OpenAIProvider(
        name="t",
        model="m",
        api_key="sk-test",
        base_url="https://api.openai.com/v1",
        timeout=5,
    )
    p._get_client()
    http_client = captured.get("http_client")
    assert isinstance(http_client, httpx.Client)
    assert http_client.follow_redirects is False


def test_openai_redirect_maps_to_neutral_error(monkeypatch):
    """3xx 一律拒绝；用户可见消息不含内网地址/上游原文。"""
    _isolate(monkeypatch)
    from types import SimpleNamespace

    from src.ai.providers import OpenAIProvider, ProviderHTTPError

    class _Redirect(Exception):
        status_code = 302

    class _Completions:
        def create(self, **kwargs):
            raise _Redirect("302 -> http://169.254.169.254/latest/meta-data/")

    p = OpenAIProvider(name="t", model="m", api_key="sk-test", timeout=5)
    p._client = SimpleNamespace(
        chat=SimpleNamespace(completions=_Completions())
    )
    with pytest.raises(ProviderHTTPError) as exc_info:
        p.chat([{"role": "user", "content": "hi"}])
    assert exc_info.value.status_code == 302
    assert "重定向" in str(exc_info.value)
    assert "169.254" not in str(exc_info.value)


def test_ollama_redirect_is_not_followed(monkeypatch):
    """Ollama 路径同样不跟随重定向，且错误消息中性。"""
    _isolate(monkeypatch)
    import requests

    from src.ai.providers import OllamaProvider, ProviderHTTPError

    captured = {}

    class _Response:
        status_code = 302
        text = "Found: http://169.254.169.254/latest/meta-data/"

    def _fake_post(url, **kwargs):
        captured["url"] = url
        captured.update(kwargs)
        return _Response()

    monkeypatch.setattr(requests, "post", _fake_post)
    p = OllamaProvider(
        name="o", model="qwen2.5:3b", base_url="http://127.0.0.1:11434", timeout=5
    )
    with pytest.raises(ProviderHTTPError) as exc_info:
        p.generate("hi")
    assert captured["allow_redirects"] is False
    assert "重定向" in str(exc_info.value)
    assert "169.254" not in str(exc_info.value)


def test_runtime_guard_rejects_private_and_rebinding(monkeypatch):
    """每次请求前重新解析：解析结果变私网即拒绝；公网解析仍放行。"""
    from src.ai import net_guard

    public = [(2, 1, 6, "", ("93.184.216.34", 443))]
    private = [(2, 1, 6, "", ("169.254.169.254", 443))]
    answers = [public, private]
    calls = {"n": 0}

    def _fake_getaddrinfo(*args, **kwargs):
        answer = answers[min(calls["n"], len(answers) - 1)]
        calls["n"] += 1
        return answer

    monkeypatch.setattr(net_guard.socket, "getaddrinfo", _fake_getaddrinfo)
    net_guard.assert_safe_runtime_target("https://rebind.example.com/v1")
    with pytest.raises(net_guard.RuntimeTargetRejected) as exc_info:
        net_guard.assert_safe_runtime_target("https://rebind.example.com/v1")
    assert "内网" in str(exc_info.value)
    assert "169.254" not in str(exc_info.value)

    monkeypatch.setattr(
        net_guard.socket,
        "getaddrinfo",
        lambda *a, **k: [(2, 1, 6, "", ("10.0.0.7", 80))],
    )
    with pytest.raises(net_guard.RuntimeTargetRejected):
        net_guard.assert_safe_runtime_target("http://internal.example.com")


def test_provider_timeout_env_applied(monkeypatch):
    """``AI_PROVIDER_TIMEOUT`` 可选覆盖默认超时（慢上游需要更久）。"""
    _isolate(
        monkeypatch,
        env={
            "AI_PROVIDER_TYPE": "openai",
            "AI_PROVIDER_KEY": "sk-test",
            "AI_PROVIDER_TIMEOUT": "240",
        },
    )
    from src.ai.providers import get_provider, reset_provider

    reset_provider()
    try:
        assert get_provider().timeout == 240.0
    finally:
        reset_provider()


def test_provider_timeout_env_invalid_keeps_default(monkeypatch, caplog):
    """非法值不静默改行为：回落类默认超时，并在日志里留下提示（不抛异常）。"""
    caplog.set_level("WARNING", logger="liuhao.ai.providers")
    _isolate(
        monkeypatch,
        env={
            "AI_PROVIDER_TYPE": "openai",
            "AI_PROVIDER_KEY": "sk-test",
            "AI_PROVIDER_TIMEOUT": "not-a-number",
        },
    )
    from src.ai.providers import get_provider, reset_provider

    reset_provider()
    try:
        p = get_provider()
        assert p.timeout == 30.0
        assert "AI_PROVIDER_TIMEOUT" in caplog.text
    finally:
        reset_provider()


# ==================== ProviderFactory 插件式注册 ====================


@pytest.fixture
def registry():
    """暴露真实注册表，并在用例结束后恢复内置 7 种，避免污染其它测试。"""
    from src.ai.providers import ProviderFactory

    snapshot = dict(ProviderFactory._providers)
    try:
        yield ProviderFactory
    finally:
        ProviderFactory._providers.clear()
        ProviderFactory._providers.update(snapshot)


def test_register_provider_makes_it_creatable_without_editing_factory(registry):
    from src.ai.providers import BaseProvider

    class EchoProvider(BaseProvider):
        def generate(self, prompt, **kwargs):
            return f"echo:{prompt}"

    registry.register_provider("echo", EchoProvider)
    provider = registry.create_provider("echo", name="t", model="m")
    assert isinstance(provider, EchoProvider)
    assert provider.generate("hi") == "echo:hi"


def test_registered_name_is_case_insensitive(registry):
    from src.ai.providers import BaseProvider

    class VendorProvider(BaseProvider):
        def generate(self, prompt, **kwargs):
            return "vendor"

    registry.register_provider("  VenDor ", VendorProvider)
    assert "vendor" in registry.get_supported_providers()
    assert isinstance(registry.create_provider("VENDOR", name="t", model="m"), VendorProvider)


def test_register_provider_can_override_a_builtin(registry):
    from src.ai.providers import BaseProvider

    class ReplacementProvider(BaseProvider):
        def generate(self, prompt, **kwargs):
            return "replacement"

    registry.register_provider("mock", ReplacementProvider)
    assert isinstance(registry.create_provider("mock", name="t", model="m"), ReplacementProvider)


def test_unknown_provider_type_still_fails_and_lists_supported(registry):
    """新规则不能让未知类型变成静默回落：仍是显式 ValueError，且带候选。"""
    with pytest.raises(ValueError) as info:
        registry.create_provider("not-a-provider", name="t", model="m")
    message = str(info.value)
    assert "Unknown provider type: not-a-provider" in message
    assert "supported:" in message
    assert "mock" in message


def test_register_provider_rejects_a_non_provider_class(registry):
    with pytest.raises(TypeError) as info:
        registry.register_provider("bad", object)  # type: ignore[arg-type]
    assert "BaseProvider" in str(info.value)


def test_register_provider_rejects_an_empty_name(registry):
    from src.ai.providers import MockProvider

    with pytest.raises(ValueError):
        registry.register_provider("   ", MockProvider)
