"""Regression: read-only live web research for time-sensitive questions.

Covered (per the acceptance criteria):
* online / offline / blocked provider behaviour;
* per-provider timeout isolation and empty results;
* sources without a URL are DROPPED, never patched or invented;
* citations carry site + clickable link + fetch time;
* the kill switch restores the previous model-knowledge behaviour exactly;
* outbound queries are audited without response bodies, headers or keys.
"""
from __future__ import annotations

import json
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

import src.ai.tools_local as tools_local
import src.ai.web_research as wr
from src.ai.llm_support import compose_execution_summary


# --------------------------------------------------------------------- server
class _Handler(BaseHTTPRequestHandler):
    def _json(self, payload, status: int = 200) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):  # noqa: N802 - stdlib contract
        path = urllib.parse.urlsplit(self.path).path
        if path == "/hn.json":
            self._json({
                "hits": [
                    {
                        "title": "开源项目 Alpha",
                        "url": "https://github.com/example/alpha",
                        "created_at": "2026-11-05T08:00:00Z",
                    },
                    {
                        "title": "开源项目 Beta",
                        "url": "https://example.org/beta",
                        "created_at": "2026-11-04T08:00:00Z",
                    },
                ]
            })
        elif path == "/empty.json":
            self._json({"hits": []})
        elif path == "/slow.json":
            time.sleep(2.5)
            self._json({
                "hits": [{
                    "title": "慢源项目",
                    "url": "https://slow.example/item",
                    "created_at": "2026-11-05T08:00:00Z",
                }]
            })
        elif path == "/forbidden":
            self._json({"message": "rate limited"}, status=403)
        else:
            self._json({"error": "not found"}, status=404)

    def log_message(self, *args):  # keep the test output clean
        return


@pytest.fixture()
def web_server():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        yield base
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def _hn_provider(base: str, path: str):
    def provider(phrase, limit, timeout, since_days):
        payload = json.loads(wr.http_get(base + path, timeout))
        items = []
        for hit in payload.get("hits") or []:
            title = str(hit.get("title") or "")
            url = str(hit.get("url") or "")
            if not title or not url:
                continue
            items.append(wr.SourceItem(
                title=title,
                url=url,
                site=urllib.parse.urlsplit(url).netloc,
                published_at=wr._iso(hit.get("created_at")),
            ))
        return items

    provider.__name__ = f"server-{path}"
    return provider


def _static_provider(items):
    def provider(phrase, limit, timeout, since_days):
        return list(items)

    provider.__name__ = "static"
    return provider


def _report(ok: bool = True, error: str = "") -> wr.ResearchReport:
    return wr.ResearchReport(
        query="帮我查一下今天有什么值得关注的 AI 开源项目",
        search_phrase="open source project AI",
        fetched_at="2026-11-06T10:30:00+08:00",
        sources=[
            wr.SourceItem(
                title="开源项目 Alpha",
                url="https://github.com/example/alpha",
                site="github.com",
                published_at="2026-11-05T16:00:00+08:00",
            ),
            wr.SourceItem(
                title="开源项目 Beta",
                url="https://example.org/beta",
                site="example.org",
                published_at="2026-11-04T16:00:00+08:00",
            ),
        ] if ok else [],
        providers=["hackernews", "github"],
        ok=ok,
        error=error,
    )


# ---------------------------------------------------------------- live search
class TestLiveSearch:
    def test_real_sources_carry_site_link_and_timestamp(self, web_server):
        for name in ("今天", "最新", "最近"):
            assert wr.is_time_sensitive(f"有什么{name}的 AI 项目")

        report = wr.search(
            "帮我查一下今天有什么值得关注的 AI 开源项目",
            providers=[_hn_provider(web_server, "/hn.json")],
            timeout=3.0,
        )
        assert report.ok, report.error
        assert len(report.sources) == 2
        for source in report.sources:
            assert source.title
            assert source.url.startswith("http")
            assert source.site
            assert source.published_at, "a real timestamp must not be dropped"
        assert "open source" in report.search_phrase

    def test_empty_result_is_an_honest_failure_not_an_invention(self, web_server):
        report = wr.search(
            "今天最新 AI 项目",
            providers=[_hn_provider(web_server, "/empty.json")],
            timeout=3.0,
        )
        assert not report.ok
        assert report.sources == []
        assert "无结果" in report.error

    def test_source_without_url_is_dropped_not_patched(self, web_server):
        report = wr.search(
            "今天最新 AI 项目",
            providers=[
                _static_provider([
                    wr.SourceItem(title="没有链接的项目", url="", site=""),
                    wr.SourceItem(
                        title="有链接的项目",
                        url="https://example.org/real",
                        site="example.org",
                    ),
                ])
            ],
            timeout=2.0,
        )
        assert report.ok
        assert [s.title for s in report.sources] == ["有链接的项目"]

    def test_slow_provider_does_not_take_down_a_fast_one(self, web_server):
        fast = _hn_provider(web_server, "/hn.json")
        slow = _hn_provider(web_server, "/slow.json")
        started = time.monotonic()
        report = wr.search(
            "今天最新 AI 项目",
            providers=[slow, fast],
            timeout=1.0,
        )
        elapsed = time.monotonic() - started
        assert report.ok, "one slow provider must not fail the whole search"
        assert {s.title for s in report.sources} >= {"开源项目 Alpha", "开源项目 Beta"}
        assert elapsed < 5.0, "the search must stay bounded by the per-call budget"

    def test_blocked_provider_is_isolated(self, web_server):
        blocked = _hn_provider(web_server, "/forbidden")
        fast = _hn_provider(web_server, "/hn.json")
        report = wr.search("最新 AI 项目", providers=[blocked, fast], timeout=3.0)
        assert report.ok
        assert "403" not in (report.error or "")  # partial success has no fatal error

    def test_all_providers_failing_is_readable_and_never_raises(self, web_server):
        report = wr.search(
            "今天最新 AI 项目",
            providers=[_hn_provider(web_server, "/forbidden")],
            timeout=3.0,
        )
        assert not report.ok
        assert "HTTP 403" in report.error

    def test_unknown_provider_name_is_reported(self):
        report = wr.search("最新 AI 项目", providers=["does-not-exist"], timeout=2.0)
        assert not report.ok
        assert "未知检索源" in report.error


# ------------------------------------------------------------------- switches
class TestConfiguration:
    def test_default_is_off(self, monkeypatch, tmp_path):
        monkeypatch.delenv(wr.ENABLE_ENV, raising=False)
        monkeypatch.setenv(wr.SETTINGS_FILE_ENV, str(tmp_path / "privacy.json"))
        assert wr.enabled() is False

    def test_ui_choice_is_persisted_and_respected(self, monkeypatch, tmp_path):
        monkeypatch.delenv(wr.ENABLE_ENV, raising=False)
        monkeypatch.setenv(wr.SETTINGS_FILE_ENV, str(tmp_path / "privacy.json"))
        assert wr.enabled() is False
        wr.set_research_enabled(True)
        assert wr.enabled() is True
        wr.set_research_enabled(False)
        assert wr.enabled() is False

    def test_explicit_env_wins_over_ui_choice(self, monkeypatch, tmp_path):
        monkeypatch.setenv(wr.SETTINGS_FILE_ENV, str(tmp_path / "privacy.json"))
        wr.set_research_enabled(True)
        for value in ("0", "false", "off", "no", "disabled"):
            monkeypatch.setenv(wr.ENABLE_ENV, value)
            assert wr.enabled() is False
        monkeypatch.setenv(wr.ENABLE_ENV, "1")
        wr.set_research_enabled(False)
        assert wr.enabled() is True

    def test_disclosure_names_the_three_sites(self, monkeypatch):
        monkeypatch.delenv(wr.PROVIDERS_ENV, raising=False)
        text = wr.disclosure_text()
        for host in ("hn.algolia.com", "api.github.com", "export.arxiv.org"):
            assert host in text

    def test_arxiv_endpoint_is_https(self, monkeypatch):
        captured = {}

        def fake_get(url, timeout, headers=None):
            captured["url"] = url
            return (
                '<feed xmlns="http://www.w3.org/2005/Atom"></feed>'
            )

        monkeypatch.setattr(wr, "http_get", fake_get)
        wr._arxiv("AI", 5, 3.0, None)
        assert captured["url"].startswith("https://export.arxiv.org/")


    def test_provider_list_and_timeout_are_configurable(self, monkeypatch):
        monkeypatch.setenv(wr.PROVIDERS_ENV, "github")
        assert wr.provider_names() == ["github"]
        monkeypatch.setenv(wr.TIMEOUT_ENV, "3")
        assert wr.timeout_seconds() == 3.0
        monkeypatch.setenv(wr.TIMEOUT_ENV, "0")
        assert wr.timeout_seconds() >= 2.0

    def test_provider_seam_is_replaceable(self):
        sentinel = wr.SourceItem(
            title="自定义源", url="https://custom.example/x", site="custom.example"
        )
        wr.register_provider("custom", _static_provider([sentinel]))
        try:
            report = wr.search("最新项目", providers=["custom"], timeout=2.0)
            assert report.ok
            assert report.sources[0].title == "自定义源"
        finally:
            wr.PROVIDERS.pop("custom", None)


class TestPhraseSanitisation:
    def test_keywords_are_extracted_when_hints_hit(self):
        assert wr.build_search_phrase("帮我查一下 React 19 的新特性") == "React 19"

    def test_unmatched_chinese_is_never_sent_verbatim(self):
        query = (
            "请帮我查一下量子计算在表面码纠错方面有哪些值得关注的工程化技术路线"
            "以及相关的实现推荐和已知局限，还有低温控制与测量环节的实践进展"
        )
        phrase = wr.build_search_phrase(query)
        assert phrase != query
        assert len(phrase) <= wr.MAX_PHRASE_CHARS
        assert "帮我查一下" not in phrase

    def test_pii_is_stripped_before_egress(self):
        query = "我的邮箱是 alice@example.com 电话 13800138000 请帮我查一下最新论文"
        phrase = wr.build_search_phrase(query)
        assert "alice@example.com" not in phrase
        assert "13800138000" not in phrase
        assert "邮箱" not in phrase
        assert "电话" not in phrase

    def test_urls_and_keys_are_stripped(self):
        query = "看看 https://secret.example/path 和 sk-abcdefghijklmnop 这个，最新 AI"
        phrase = wr.build_search_phrase(query)
        assert "secret.example" not in phrase
        assert "sk-abcdefghijklmnop" not in phrase


# ----------------------------------------------------------------- tool layer
class TestResearchTool:
    def test_live_answer_cites_sources_and_fetch_time(self, monkeypatch):
        monkeypatch.setenv(wr.ENABLE_ENV, "1")
        monkeypatch.setattr(wr, "search", lambda query, **kwargs: _report(ok=True))
        monkeypatch.setattr(
            tools_local, "_model_call", lambda prompt: "结论：Alpha 与 Beta 值得关注。"
        )

        output = tools_local.llm_research(query="今天有什么值得关注的 AI 开源项目")

        assert output["success"] is True
        assert output["basis"] == "live_web"
        text = output["result"]
        assert "结论：Alpha 与 Beta 值得关注。" in text
        assert "抓取时间 2026-11-06T10:30:00+08:00" in text
        assert "https://github.com/example/alpha" in text
        assert "github.com" in text
        assert [s["url"] for s in output["sources"]] == [
            "https://github.com/example/alpha",
            "https://example.org/beta",
        ]

    def test_retrieval_failure_falls_back_with_honest_label(self, monkeypatch):
        monkeypatch.setenv(wr.ENABLE_ENV, "1")
        monkeypatch.setattr(
            wr, "search", lambda query, **kwargs: _report(ok=False, error="github: HTTP 403")
        )
        monkeypatch.setattr(
            tools_local, "_model_call", lambda prompt: "基于已有知识的回答。"
        )

        output = tools_local.llm_research(query="今天有什么值得关注的 AI 开源项目")

        assert output["success"] is True, "retrieval failure must not fail the task"
        assert output["basis"] == "model_knowledge"
        assert "联网检索没成功" in output["result"]
        assert "你的问题照常回答" in output["result"]
        # 技术细节只留在 retrieval_error / 审计里，不糊到用户脸上。
        assert "HTTP 403" not in output["result"]
        assert output["retrieval_error"] == "github: HTTP 403"
        assert "基于已有知识的回答。" in output["result"]

    def test_pii_is_scrubbed_before_clause_splitting(self, monkeypatch):
        """邮箱/电话/密钥/URL 必须在按标点切句之前就抹掉，不能留残片出网。"""
        monkeypatch.setenv(wr.ENABLE_ENV, "1")
        captured = {}

        def _capture(query, **kwargs):
            captured["query"] = query
            return _report(ok=False, error="offline")

        monkeypatch.setattr(wr, "search", _capture)
        monkeypatch.setattr(tools_local, "_model_call", lambda prompt: "回答")

        tools_local.llm_research(
            query=(
                "今天有什么值得关注的 AI 开源项目？我的邮箱 alice@example.com，"
                "手机号 13800138000，鉴权串 sk-live-ABCD1234567890SECRET，"
                "参考链接 https://internal.example.com/private?token=abc123"
            )
        )

        search_input = captured["query"]
        phrase = wr.build_search_phrase(search_input)
        for secret in (
            "alice",
            "example.com",
            "13800138000",
            "sk-live-ABCD1234567890SECRET",
            "internal.example.com",
        ):
            assert secret not in search_input, f"{secret} 残片仍进入检索输入"
            assert secret not in phrase, f"{secret} 残片仍进入检索词"
        assert phrase == "open source project AI"

    def test_disabled_switch_matches_previous_behaviour(self, monkeypatch):
        monkeypatch.setenv(wr.ENABLE_ENV, "0")

        def _must_not_be_called(*args, **kwargs):
            raise AssertionError("live retrieval must not run when disabled")

        monkeypatch.setattr(wr, "search", _must_not_be_called)
        monkeypatch.setattr(
            tools_local, "_model_call", lambda prompt: "基于已有知识的回答。"
        )

        output = tools_local.llm_research(query="今天有什么值得关注的 AI 开源项目")

        assert output["success"] is True
        assert output["basis"] == "model_knowledge"
        assert output["retrieval_error"] == ""
        assert "未接入实时联网检索" in output["result"]
        assert "基于已有知识的回答。" in output["result"]

    def test_model_failure_after_successful_retrieval_still_returns_sources(self, monkeypatch):
        monkeypatch.setenv(wr.ENABLE_ENV, "1")
        monkeypatch.setattr(wr, "search", lambda query, **kwargs: _report(ok=True))

        def _broken(prompt):
            raise TimeoutError("model timed out")

        monkeypatch.setattr(tools_local, "_model_call", _broken)

        output = tools_local.llm_research(query="今天有什么值得关注的 AI 开源项目")

        assert output["success"] is True, "the retrieval itself succeeded"
        assert output["basis"] == "live_web"
        assert "直接列出原始来源" in output["result"]
        assert "https://github.com/example/alpha" in output["result"]


# ------------------------------------------------------------- synthesis layer
def _upstream_payload(sources=None):
    return [{
        "task": "调研",
        "output": {
            "success": True,
            "result": "Alpha 与 Beta 值得关注。",
            "sources": sources if sources is not None else [
                {
                    "title": "开源项目 Alpha",
                    "url": "https://github.com/example/alpha",
                    "site": "github.com",
                    "published_at": "2026-11-05T16:00:00+08:00",
                },
                {
                    "title": "开源项目 Beta",
                    "url": "https://example.org/beta",
                    "site": "example.org",
                    "published_at": "2026-11-04T16:00:00+08:00",
                },
            ],
            "fetched_at": "2026-11-06T10:30:00+08:00",
        },
    }]


class TestSynthesisTool:
    def test_model_text_that_drops_citations_gets_them_reappended(self, monkeypatch):
        monkeypatch.setattr(
            tools_local, "_model_call", lambda prompt: "结论：Alpha 与 Beta 值得关注。"
        )

        output = tools_local.llm_synthesis(
            goal="整理三条要点", upstream=_upstream_payload()
        )

        assert output["success"] is True
        assert output["basis"] == "live_web"
        assert "结论：Alpha 与 Beta 值得关注。" in output["result"]
        assert "https://github.com/example/alpha" in output["result"]
        assert "https://example.org/beta" in output["result"]
        assert "抓取时间 2026-11-06T10:30:00+08:00" in output["result"]
        assert output["fetched_at"] == "2026-11-06T10:30:00+08:00"

    def test_model_failure_keeps_upstream_citations(self, monkeypatch):
        def _broken(prompt):
            raise TimeoutError("model timed out")

        monkeypatch.setattr(tools_local, "_model_call", _broken)

        output = tools_local.llm_synthesis(
            goal="整理三条要点", upstream=_upstream_payload()
        )

        assert output["success"] is True, "upstream work must not be lost"
        assert "https://github.com/example/alpha" in output["result"]
        assert "https://example.org/beta" in output["result"]

    def test_upstream_without_sources_adds_nothing(self, monkeypatch):
        monkeypatch.setattr(tools_local, "_model_call", lambda prompt: "纯文本结论。")

        output = tools_local.llm_synthesis(
            goal="整理三条要点", upstream=_upstream_payload(sources=[])
        )

        assert output["success"] is True
        assert "来源" not in output["result"]
        assert "sources" not in output
        assert "https://" not in output["result"]


# ---------------------------------------------------------------------- audit
class TestAudit:
    def test_query_and_sites_are_audited_without_bodies_or_secrets(self, monkeypatch):
        events = []

        class _FakeAudit:
            @staticmethod
            def emit(event, **kwargs):
                events.append((event, kwargs))
                return True

        monkeypatch.setattr(wr, "_audit", _FakeAudit)
        sentinel = wr.SourceItem(
            title="自定义源", url="https://custom.example/x", site="custom.example",
            snippet="不应写入审计的正文",
        )
        wr.search("今天最新 AI 项目", providers=[_static_provider([sentinel])], timeout=2.0)

        assert events, "an outbound query must leave an audit trail"
        event, payload = events[-1]
        assert event == "p9.web.research"
        assert payload["query"] == "今天最新 AI 项目"
        assert payload["sites"] == ["custom.example"]
        assert payload["source_count"] == 1
        assert payload["outcome"] == "success"
        serialized = json.dumps(payload, ensure_ascii=False)
        assert "不应写入审计的正文" not in serialized
        assert "Authorization" not in serialized
        assert "headers" not in serialized


# -------------------------------------------------------------------- summary
def test_summary_keeps_real_citations_in_the_deterministic_path():
    records = [
        {
            "name": "调研",
            "capability": "network_bus",
            "status": "completed",
            "result": {
                "result": "Alpha 与 Beta 值得关注。",
                "sources": [
                    {
                        "title": "开源项目 Alpha",
                        "url": "https://github.com/example/alpha",
                        "site": "github.com",
                        "published_at": "2026-11-05T16:00:00+08:00",
                    }
                ],
                "fetched_at": "2026-11-06T10:30:00+08:00",
            },
        }
    ]

    summary = compose_execution_summary("整理三条要点", records, generator=None)

    assert "来源" in summary
    assert "https://github.com/example/alpha" in summary
    assert "github.com" in summary
    assert "抓取时间 2026-11-06T10:30:00+08:00" in summary


def _summary_records():
    return [
        {
            "name": "调研",
            "capability": "network_bus",
            "status": "completed",
            "result": {
                "result": "Alpha 与 Beta 值得关注。",
                "sources": [
                    {
                        "title": "开源项目 Alpha",
                        "url": "https://github.com/example/alpha",
                        "site": "github.com",
                        "published_at": "2026-11-05T16:00:00+08:00",
                    }
                ],
                "fetched_at": "2026-11-06T10:30:00+08:00",
            },
        }
    ]


def test_summary_rejects_a_model_summary_that_invents_links():
    invented = "今天最火的是 https://github.com/fake/made-up-project，发布于 2026-11-06。"

    summary = compose_execution_summary(
        "整理三条要点", _summary_records(), generator=lambda prompt: invented
    )

    assert "fake" not in summary
    assert "https://github.com/example/alpha" in summary
    assert "抓取时间 2026-11-06T10:30:00+08:00" in summary


def test_summary_requires_real_links_instead_of_bare_claims():
    bare = "据我了解，今天最火的是 Alpha 和 Beta，都是很不错的项目。"

    summary = compose_execution_summary(
        "整理三条要点", _summary_records(), generator=lambda prompt: bare
    )

    assert "据我了解" not in summary
    assert "https://github.com/example/alpha" in summary


def test_summary_keeps_a_model_summary_that_cites_real_links():
    grounded = "结论：Alpha 值得关注（https://github.com/example/alpha）。"

    summary = compose_execution_summary(
        "整理三条要点", _summary_records(), generator=lambda prompt: grounded
    )

    assert grounded in summary
    assert "https://github.com/example/alpha" in summary
    assert "抓取时间 2026-11-06T10:30:00+08:00" in summary
