"""执行侧模型凭据解析必须只有一个入口（账号体系接入前的接缝）。"""

from src.ai import llm_support, providers


class _FakeProvider:
    def __init__(self, reply: str = "ok") -> None:
        self.reply = reply
        self.prompts = []

    def generate_with_retry(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return self.reply


def test_execution_provider_resolves_through_the_single_seam(monkeypatch):
    sentinel = _FakeProvider()
    monkeypatch.setattr(providers, "resolve_provider", lambda: sentinel)

    assert llm_support.resolve_execution_provider("sub-account-a") is sentinel


class _Mock(providers.MockProvider):
    def __init__(self) -> None:
        super().__init__("mock", "mock-model")


def test_default_generate_goes_through_the_seam(monkeypatch):
    fake = _FakeProvider("连通成功")
    monkeypatch.setattr(llm_support, "resolve_execution_provider", lambda requester=None: fake)

    assert llm_support.default_generate("hi", requester="sub-account-a") == "连通成功"
    assert fake.prompts == ["hi"]


def test_default_generate_still_refuses_the_mock_provider(monkeypatch):
    monkeypatch.setattr(providers, "resolve_provider", lambda: _Mock())

    try:
        llm_support.default_generate("hi")
    except llm_support.ModelUnavailable as exc:
        assert "MockProvider" in str(exc)
    else:  # pragma: no cover - would mean a fake answer was returned
        raise AssertionError("MockProvider must never produce a success")
