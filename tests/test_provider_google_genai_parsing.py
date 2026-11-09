"""GoogleProvider 对 ``google-genai`` 响应的解析路径（假客户端）。

真实成功调用需要有效 API Key，本机没有，所以此前只有"假 Key → 400"
这一条证据——它只能证明请求真的发到了 Google，证明不了我们读响应的代码。
本文件桩掉 ``google.genai``，把 ``generate()`` 的解析分支跑实：

* 正常单段返回；
* 多段返回（SDK 的 ``response.text`` 会拼接全部 text part，不能只取第一段）；
* 空返回（传输成功但无内容，必须交给 ``generate_with_retry`` 判失败，
  不能当成空字符串"成功"）；
* 上游异常原样上抛，不被吞掉；
* 缺 ``google-genai`` 时报可操作的错误，而不是 ``ImportError`` 裸奔。

桩的语义与 SDK 保持一致：``response.text`` 拼接候选里所有 text part；
没有 text part 时返回 ``None``。
"""

from __future__ import annotations

import sys
import types

import pytest

from error_types import ProviderEmptyResponseError
from src.ai.providers import GoogleProvider


class _Part:
    def __init__(self, text: str):
        self.text = text


class _Content:
    def __init__(self, parts):
        self.parts = parts


class _Candidate:
    def __init__(self, parts):
        self.content = _Content(parts)


class _Response:
    """Mimics ``GenerateContentResponse.text``: join of every text part."""

    def __init__(self, parts):
        self.candidates = [_Candidate(parts)]

    @property
    def text(self):
        if not self.candidates:
            return None
        texts = [
            part.text
            for part in self.candidates[0].content.parts
            if getattr(part, "text", None)
        ]
        return "".join(texts) if texts else None


class _HttpOptions:
    """Mimics ``google.genai.types.HttpOptions``（测试只用到 ``base_url``）。"""

    def __init__(self, base_url=None, **kwargs):
        self.base_url = base_url


class _Models:
    def __init__(self, handler):
        self._handler = handler
        self.calls = []

    def generate_content(self, *, model, contents):
        self.calls.append({"model": model, "contents": contents})
        return self._handler(model, contents)


def _install_fake_genai(monkeypatch, handler):
    """安装假的 ``google.genai``，返回按调用顺序记录的 Client 实例。"""
    clients = []

    class FakeClient:
        def __init__(self, api_key=None, **kwargs):
            self.api_key = api_key
            self.models = _Models(handler)
            clients.append(self)

    module = types.ModuleType("google.genai")
    module.Client = FakeClient
    # 真实 ``google/genai/__init__.py`` 无条件执行 ``from . import types``，
    # ``__all__`` 里也是 ``['Client', 'interactions', 'types']``：``types`` 与
    # ``Client`` 同属公开面。桩只给 ``Client`` 就不是真实包的形状了。
    types_module = types.ModuleType("google.genai.types")
    types_module.HttpOptions = _HttpOptions
    module.types = types_module
    package = types.ModuleType("google")
    package.__path__ = []  # 让它表现得像一个已安装的包
    package.genai = module
    monkeypatch.setitem(sys.modules, "google", package)
    monkeypatch.setitem(sys.modules, "google.genai", module)
    monkeypatch.setitem(sys.modules, "google.genai.types", types_module)
    return clients


def _provider(**kwargs) -> GoogleProvider:
    return GoogleProvider("google", "gemini-2.5-flash", api_key="test-key", **kwargs)


def test_generate_returns_single_part_text(monkeypatch):
    clients = _install_fake_genai(
        monkeypatch, lambda model, contents: _Response([_Part("你好，世界")])
    )

    assert _provider().generate("ping") == "你好，世界"

    assert clients[0].api_key == "test-key"
    assert clients[0].models.calls == [
        {"model": "gemini-2.5-flash", "contents": "ping"}
    ]


def test_generate_keeps_every_part(monkeypatch):
    _install_fake_genai(
        monkeypatch,
        lambda model, contents: _Response(
            [_Part("第一段"), _Part("第二段"), _Part("第三段")]
        ),
    )

    assert _provider().generate("ping") == "第一段第二段第三段"


def test_generate_honours_model_override(monkeypatch):
    clients = _install_fake_genai(
        monkeypatch, lambda model, contents: _Response([_Part("ok")])
    )

    assert _provider().generate("ping", model="gemini-2.5-pro") == "ok"
    assert clients[0].models.calls[0]["model"] == "gemini-2.5-pro"


def test_empty_response_is_not_a_success(monkeypatch):
    _install_fake_genai(monkeypatch, lambda model, contents: _Response([]))
    provider = _provider(max_retries=0)

    # 传输成功但没有内容：generate() 原样返回 SDK 的 None，
    # 由 generate_with_retry() 的统一判空点报失败。
    assert provider.generate("ping") is None
    with pytest.raises(ProviderEmptyResponseError):
        provider.generate_with_retry("ping")


def test_generate_propagates_upstream_error(monkeypatch):
    def boom(model, contents):
        raise RuntimeError("400 API_KEY_INVALID")

    _install_fake_genai(monkeypatch, boom)

    with pytest.raises(RuntimeError, match="API_KEY_INVALID"):
        _provider().generate("ping")


def test_generate_reports_missing_google_genai_sdk(monkeypatch):
    class _BlockGoogle:
        def find_spec(self, fullname, path=None, target=None):
            if fullname == "google" or fullname.startswith("google."):
                raise ModuleNotFoundError(
                    f"No module named {fullname!r}", name=fullname
                )
            return None

    monkeypatch.delitem(sys.modules, "google", raising=False)
    monkeypatch.delitem(sys.modules, "google.genai", raising=False)
    monkeypatch.setattr(sys, "meta_path", [_BlockGoogle()] + sys.meta_path)

    with pytest.raises(RuntimeError, match="google-genai"):
        _provider().generate("ping")


def test_partial_genai_install_reports_an_actionable_error(monkeypatch):
    """包已装但没导出 ``types``（残缺/冻结安装）也不能裸抛 ``ImportError``。

    此时 ``from google.genai import types`` 抛的是普通 ``ImportError``，而不是
    ``ModuleNotFoundError``；产品契约是"缺 SDK 报可操作的错"，所以两条路必须
    汇到同一个 ``RuntimeError``。
    """
    _install_fake_genai(monkeypatch, lambda model, contents: _Response([_Part("ok")]))
    monkeypatch.delattr(sys.modules["google.genai"], "types", raising=False)
    monkeypatch.delitem(sys.modules, "google.genai.types", raising=False)

    with pytest.raises(RuntimeError, match="google-genai"):
        _provider().generate("ping")
