"""Provider 默认行为回归测试（方向②：端到端真实运行）。

锁定 ``get_provider()`` 的行为：
- 无 ``AI_PROVIDER_TYPE``（os.environ 与 .env 均无）时默认 MockProvider
  （不触发假 key 网络调用）
- 默认 model 为 ``mock-model``（非占位符 ``[REDACTED]``）
- 显式指定 type 时仍按 env 生效（openai -> OpenAIProvider）
- ``.env`` 文件兜底：os.environ 未设置时经 ConfigManager 读 ``.env``
  （优先级：os.environ > .env > 默认；见 ``_provider_env``）
"""


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
