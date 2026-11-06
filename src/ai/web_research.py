"""Read-only live web research for time-sensitive questions.

Why this exists
---------------
``network_bus`` used to be answered entirely from the model's own weights, so a
question like "今天有什么值得关注的 AI 开源项目" could only ever get a labelled
"未接入实时联网检索" answer. That is honest, but it is not useful. This module
adds a **read-only** retrieval path built on public, key-free endpoints:

* Hacker News (Algolia) — real stories with real timestamps and links;
* GitHub Search — real repositories with real URLs, stars and update dates;
* arXiv — real preprints with real submission dates.

Hard rules encoded here:

1. **Never invent.** A source without a URL is dropped, not patched. If every
   provider fails, the caller gets an explicit failure reason and falls back to
   the old model-knowledge answer.
2. **Failures are isolated.** Each provider has its own deadline and its own
   error. One blocked/slow provider never fails the task, let alone the goal.
3. **No secrets in logs.** Only the query, the provider names and a sanitised
   error class are recorded. Response bodies and headers are never logged.
4. **Replaceable.** Providers live in ``PROVIDERS``; callers/tests can inject
   their own with ``search(..., providers=[...])`` or ``register_provider``.
"""
from __future__ import annotations

import json
import logging
import os
import re
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import (
    ThreadPoolExecutor,
    TimeoutError as FutureTimeout,
    as_completed,
)
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional, Sequence

from . import audit as _audit

logger = logging.getLogger("liuhao.ai.web_research")

ENABLE_ENV = "LIUHAO_WEB_RESEARCH"
PROVIDERS_ENV = "LIUHAO_WEB_RESEARCH_PROVIDERS"
TIMEOUT_ENV = "LIUHAO_WEB_RESEARCH_TIMEOUT_SECONDS"

DEFAULT_PROVIDERS = ("hackernews", "github", "arxiv")
DEFAULT_TIMEOUT_SECONDS = 8.0
MAX_SOURCES = 12
_USER_AGENT = "LiuHaoAIOS/1.0 (read-only research)"

_TRUTHY = frozenset({"1", "true", "yes", "on", "enable", "enabled"})
_FALSY = frozenset({"0", "false", "no", "off", "disable", "disabled"})

#: Deterministic Chinese -> English hints. These public endpoints index English
#: content far better, and we deliberately avoid an extra model round-trip for
#: something this mechanical (it would also make the live path depend on the
#: very model that might be down).
_CJK_HINTS: Sequence[tuple[str, str]] = (
    ("人工智能", "AI"),
    ("机器学习", "machine learning"),
    ("深度学习", "deep learning"),
    ("大模型", "LLM"),
    ("开源", "open source"),
    ("项目", "project"),
    ("芯片", "chip"),
    ("论文", "paper"),
    ("智能体", "agent"),
    ("框架", "framework"),
    ("工具", "tool"),
    ("发布", "release"),
    ("模型", "model"),
    # Time words carry no search value: the time window is applied structurally.
    ("值得关注", ""),
    ("今天", ""),
    ("今日", ""),
    ("最新", ""),
    ("最近", ""),
    ("近期", ""),
    ("本周", ""),
    ("这周", ""),
    ("帮我查一下", ""),
    ("查一下", ""),
)

_TIME_SENSITIVE = (
    "今天", "今日", "最新", "最近", "近期", "本周", "这周",
    "today", "latest", "recent", "this week", "new ",
)


@dataclass(frozen=True)
class SourceItem:
    """One real, citable web source."""

    title: str
    url: str
    site: str
    published_at: str = ""
    snippet: str = ""

    def as_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ResearchReport:
    """Outcome of one read-only search. Never raised, always returned."""

    query: str
    search_phrase: str
    fetched_at: str
    sources: List[SourceItem] = field(default_factory=list)
    providers: List[str] = field(default_factory=list)
    ok: bool = False
    error: str = ""

    def as_dict(self) -> Dict[str, Any]:
        return {
            "query": self.query,
            "search_phrase": self.search_phrase,
            "fetched_at": self.fetched_at,
            "providers": list(self.providers),
            "ok": self.ok,
            "error": self.error,
            "sources": [item.as_dict() for item in self.sources],
        }


# --------------------------------------------------------------------- config
def enabled() -> bool:
    """Whether live retrieval is on. Default ON; any explicit off value wins."""
    raw = (os.environ.get(ENABLE_ENV) or "").strip().lower()
    if not raw:
        return True
    if raw in _FALSY:
        return False
    if raw in _TRUTHY:
        return True
    return True


def timeout_seconds() -> float:
    raw = os.environ.get(TIMEOUT_ENV, "")
    try:
        value = float(raw) if raw else DEFAULT_TIMEOUT_SECONDS
    except (TypeError, ValueError):
        value = DEFAULT_TIMEOUT_SECONDS
    return max(2.0, value)


def provider_names() -> List[str]:
    raw = os.environ.get(PROVIDERS_ENV, "")
    if not raw:
        return list(DEFAULT_PROVIDERS)
    return [name.strip().lower() for name in raw.split(",") if name.strip()]


# --------------------------------------------------------------------- helpers
def is_time_sensitive(query: str) -> bool:
    lowered = (query or "").lower()
    return any(word in lowered for word in _TIME_SENSITIVE)


def build_search_phrase(query: str) -> str:
    """Turn a Chinese question into a useful English keyword phrase.

    Deterministic on purpose: the retrieval path must not depend on the model.
    """
    text = (query or "").strip()
    if not text:
        return ""
    remaining = text
    hints: List[str] = []
    for chinese, english in _CJK_HINTS:
        if chinese and chinese in remaining:
            remaining = remaining.replace(chinese, " ")
            if english:
                hints.append(english)
    ascii_words = [
        word for word in re.findall(r"[A-Za-z0-9][A-Za-z0-9._+-]*", remaining)
        if len(word) >= 2
    ]
    parts: List[str] = []
    for token in hints + ascii_words:
        if token.lower() not in {p.lower() for p in parts}:
            parts.append(token)
    phrase = " ".join(parts).strip()
    return phrase or text


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _site_of(url: str) -> str:
    try:
        return urllib.parse.urlsplit(url).netloc.lower()
    except ValueError:
        return ""


def _clean(text: Any, limit: int = 240) -> str:
    value = re.sub(r"\s+", " ", str(text or "")).strip()
    return value[:limit]


def _iso(value: Any) -> str:
    """Keep a provider timestamp only when it is parseable; never fabricate one."""
    raw = str(value or "").strip()
    if not raw:
        return ""
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return ""
    return parsed.astimezone(timezone.utc).astimezone().isoformat(timespec="seconds")


def _safe_error(exc: BaseException) -> str:
    """A short, non-leaking error label.

    URLs are deliberately excluded: they can contain the user's query, and the
    requirement is that the query is recorded *once* as a query, never smuggled
    into an exception string.
    """
    if isinstance(exc, urllib.error.HTTPError):
        return f"HTTP {exc.code}"
    if isinstance(exc, (TimeoutError, socket.timeout, FutureTimeout)):
        return "timeout"
    if isinstance(exc, urllib.error.URLError):
        reason = exc.reason
        return f"network: {reason}" if isinstance(reason, str) else "network unreachable"
    if isinstance(exc, (json.JSONDecodeError, ET.ParseError)):
        return "invalid response"
    return type(exc).__name__


def http_get(url: str, timeout: float, headers: Optional[Dict[str, str]] = None) -> str:
    request_headers = {"User-Agent": _USER_AGENT, "Accept": "*/*"}
    if headers:
        request_headers.update(headers)
    request = urllib.request.Request(url, headers=request_headers)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        raw = response.read()
    return raw.decode("utf-8", errors="replace")


# ------------------------------------------------------------------- providers
Provider = Callable[[str, int, float, Optional[int]], List[SourceItem]]


def _hackernews(phrase: str, limit: int, timeout: float, since_days: Optional[int]) -> List[SourceItem]:
    params: Dict[str, Any] = {
        "query": phrase,
        "tags": "story",
        "hitsPerPage": limit,
    }
    if since_days:
        cutoff = int((datetime.now(timezone.utc) - timedelta(days=since_days)).timestamp())
        params["numericFilters"] = f"created_at_i>{cutoff}"
    url = "https://hn.algolia.com/api/v1/search_by_date?" + urllib.parse.urlencode(params)
    payload = json.loads(http_get(url, timeout))
    results: List[SourceItem] = []
    for hit in payload.get("hits") or []:
        title = _clean(hit.get("title") or hit.get("story_title"))
        link = str(hit.get("url") or "").strip()
        object_id = str(hit.get("objectID") or "").strip()
        if not link and object_id:
            link = f"https://news.ycombinator.com/item?id={object_id}"
        if not title or not link:
            continue
        results.append(SourceItem(
            title=title,
            url=link,
            site=_site_of(link) or "news.ycombinator.com",
            published_at=_iso(hit.get("created_at")),
            snippet=_clean(hit.get("story_text") or hit.get("comment_text"), 160),
        ))
    return results


def _github(phrase: str, limit: int, timeout: float, since_days: Optional[int]) -> List[SourceItem]:
    query = f"{phrase} in:name,description,readme" if phrase else "AI"
    if since_days:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=since_days)).date().isoformat()
        query += f" created:>{cutoff}"
    params = {"q": query, "sort": "stars", "order": "desc", "per_page": limit}
    url = "https://api.github.com/search/repositories?" + urllib.parse.urlencode(params)
    body = http_get(url, timeout, headers={"Accept": "application/vnd.github+json"})
    payload = json.loads(body)
    results: List[SourceItem] = []
    for item in payload.get("items") or []:
        name = _clean(item.get("full_name") or item.get("name"), 120)
        link = str(item.get("html_url") or "").strip()
        if not name or not link:
            continue
        stars = item.get("stargazers_count")
        description = _clean(item.get("description"), 160)
        snippet = f"{description}（★{stars}）" if stars is not None else description
        results.append(SourceItem(
            title=name,
            url=link,
            site=_site_of(link) or "github.com",
            published_at=_iso(item.get("updated_at") or item.get("created_at")),
            snippet=snippet,
        ))
    return results


_ARXIV_NS = {"atom": "http://www.w3.org/2005/Atom"}


def _arxiv(phrase: str, limit: int, timeout: float, since_days: Optional[int]) -> List[SourceItem]:
    if not phrase:
        return []
    params = {
        "search_query": f"all:{phrase}",
        "start": 0,
        "max_results": limit,
        "sortBy": "submittedDate",
        "sortOrder": "descending",
    }
    url = "http://export.arxiv.org/api/query?" + urllib.parse.urlencode(params)
    body = http_get(url, timeout)
    root = ET.fromstring(body)
    results: List[SourceItem] = []
    for entry in root.findall("atom:entry", _ARXIV_NS):
        title = _clean(entry.findtext("atom:title", default="", namespaces=_ARXIV_NS), 160)
        link = _clean(entry.findtext("atom:id", default="", namespaces=_ARXIV_NS), 300)
        published = entry.findtext("atom:published", default="", namespaces=_ARXIV_NS)
        summary = _clean(entry.findtext("atom:summary", default="", namespaces=_ARXIV_NS), 180)
        if not title or not link:
            continue
        results.append(SourceItem(
            title=title,
            url=link,
            site=_site_of(link) or "arxiv.org",
            published_at=_iso(published),
            snippet=summary,
        ))
    return results


PROVIDERS: Dict[str, Provider] = {
    "hackernews": _hackernews,
    "github": _github,
    "arxiv": _arxiv,
}


def register_provider(name: str, provider: Provider) -> None:
    """Replace/extend a provider (the supported extension seam)."""
    key = (name or "").strip().lower()
    if key:
        PROVIDERS[key] = provider


# ---------------------------------------------------------------------- search
def _resolve_providers(
    providers: Optional[Sequence[Any]],
) -> List[tuple[str, Optional[Provider]]]:
    names = list(providers) if providers is not None else provider_names()
    resolved: List[tuple[str, Optional[Provider]]] = []
    for entry in names:
        if callable(entry):
            resolved.append((getattr(entry, "__name__", "custom"), entry))
        else:
            key = str(entry).strip().lower()
            resolved.append((key, PROVIDERS.get(key)))
    return resolved


def _dedupe(items: Sequence[SourceItem]) -> List[SourceItem]:
    seen: set[str] = set()
    unique: List[SourceItem] = []
    for item in items:
        key = item.url.rstrip("/")
        if not item.url or key in seen:
            continue
        seen.add(key)
        unique.append(item)
    unique.sort(key=lambda item: item.published_at or "", reverse=True)
    return unique


def search(
    query: str,
    *,
    limit: int = 6,
    timeout: Optional[float] = None,
    providers: Optional[Sequence[Any]] = None,
) -> ResearchReport:
    """Run the configured providers with isolated deadlines. Never raises."""
    started = time.monotonic()
    phrase = build_search_phrase(query)
    budget = timeout if timeout is not None else timeout_seconds()
    since_days = 7 if is_time_sensitive(query) else None
    resolved = _resolve_providers(providers)
    provider_labels = [name for name, _fn in resolved]
    fetched_at = _now_iso()

    collected: List[SourceItem] = []
    provider_results: List[Dict[str, Any]] = []
    errors: List[str] = []

    if not resolved:
        errors.append("没有启用任何检索源")
    else:
        with ThreadPoolExecutor(max_workers=max(1, len(resolved))) as pool:
            futures: Dict[Any, tuple[str, float]] = {}
            for name, fn in resolved:
                if fn is None:
                    errors.append(f"{name}: 未知检索源")
                    provider_results.append({
                        "provider": name, "ok": False, "source_count": 0,
                        "error": "unknown provider",
                    })
                    continue
                futures[pool.submit(fn, phrase, limit, budget, since_days)] = (
                    name, time.monotonic()
                )

            try:
                for future in as_completed(futures, timeout=budget + 1.5):
                    name, submitted = futures[future]
                    elapsed_ms = int((time.monotonic() - submitted) * 1000)
                    try:
                        items = [
                            item for item in future.result()
                            if item.url and item.title
                        ]
                    except BaseException as exc:  # noqa: BLE001 - isolation is the point
                        safe = _safe_error(exc)
                        provider_results.append({
                            "provider": name, "ok": False, "source_count": 0,
                            "elapsed_ms": elapsed_ms, "error": safe,
                        })
                        errors.append(f"{name}: {safe}")
                        continue
                    collected.extend(items)
                    provider_results.append({
                        "provider": name, "ok": True,
                        "source_count": len(items), "elapsed_ms": elapsed_ms,
                    })
                    if not items:
                        errors.append(f"{name}: 无结果")
            except FutureTimeout:
                for future, (name, submitted) in futures.items():
                    if future.done():
                        continue
                    future.cancel()
                    elapsed_ms = int((time.monotonic() - submitted) * 1000)
                    provider_results.append({
                        "provider": name, "ok": False, "source_count": 0,
                        "elapsed_ms": elapsed_ms, "error": "timeout",
                    })
                    errors.append(f"{name}: timeout")

    sources = _dedupe(collected)[:MAX_SOURCES]
    report = ResearchReport(
        query=query,
        search_phrase=phrase,
        fetched_at=fetched_at,
        sources=sources,
        providers=provider_labels,
        ok=bool(sources),
        error="" if sources else "；".join(errors) or "检索没有返回任何来源",
    )

    elapsed_ms = int((time.monotonic() - started) * 1000)
    logger.info(
        "web research providers=%s sources=%d ok=%s elapsed_ms=%d",
        ",".join(provider_labels) or "-",
        len(sources),
        report.ok,
        elapsed_ms,
    )
    _audit_search(report, provider_results, elapsed_ms)
    return report


def _audit_search(
    report: ResearchReport,
    provider_results: Sequence[Dict[str, Any]],
    elapsed_ms: int,
) -> None:
    """Audit the outbound query without recording response bodies or secrets.

    ``sites`` is recorded because the requirement is "目标站点"; it is derived
    from the public result URLs. No headers, cookies or API keys exist here at
    all, and the raw response bodies are intentionally omitted.
    """
    sites = sorted({item.site for item in report.sources if item.site})
    try:
        _audit.emit(
            "p9.web.research",
            module="src.ai.web_research",
            outcome="success" if report.ok else "failure",
            reason="" if report.ok else report.error[:200],
            query=report.query[:300],
            search_phrase=report.search_phrase[:200],
            providers=list(report.providers)[:8],
            sites=sites[:8],
            provider_results=[dict(item) for item in provider_results][:8],
            source_count=len(report.sources),
            fetched_at=report.fetched_at,
            elapsed_ms=elapsed_ms,
        )
    except Exception as exc:  # noqa: BLE001 - auditing must never break research
        logger.warning("web research audit failed: %s", type(exc).__name__)


def format_sources(sources: Sequence[SourceItem], fetched_at: str) -> str:
    """A deterministic, citable source block. No model involvement."""
    if not sources:
        return ""
    lines = [f"来源（抓取时间 {fetched_at}）："]
    for index, item in enumerate(sources, start=1):
        published = f"，发布于 {item.published_at}" if item.published_at else ""
        lines.append(f"{index}. {item.title} — {item.site} — {item.url}{published}")
    return "\n".join(lines)


def format_source_dicts(
    sources: Sequence[Dict[str, Any]],
    fetched_at: str = "",
) -> str:
    """Same as :func:`format_sources` for already-serialized sources."""
    items = [
        SourceItem(
            title=str(item.get("title") or ""),
            url=str(item.get("url") or ""),
            site=str(item.get("site") or ""),
            published_at=str(item.get("published_at") or ""),
        )
        for item in sources
        if isinstance(item, dict) and str(item.get("url") or "").strip()
    ]
    return format_sources(items, fetched_at)


__all__ = [
    "DEFAULT_PROVIDERS",
    "DEFAULT_TIMEOUT_SECONDS",
    "ENABLE_ENV",
    "PROVIDERS",
    "PROVIDERS_ENV",
    "ResearchReport",
    "SourceItem",
    "TIMEOUT_ENV",
    "build_search_phrase",
    "enabled",
    "format_source_dicts",
    "format_sources",
    "http_get",
    "is_time_sensitive",
    "provider_names",
    "register_provider",
    "search",
    "timeout_seconds",
]
