#!/usr/bin/env python3
"""鎏灏全球开源生态雷达（OSS Radar）

定位：oss-ecosystem/ 的执行引擎。**发现候选**，不做决策。
  sources.yaml  = 平台源（哪里去找）
  intents.yaml  = 72 类覆盖蓝图（找什么、为什么要找）
  state/        = 每轮扫描的真实留痕

设计纪律（违反即 bug）：
  1. **诚实失败**：源不可达 / 需鉴权 / 无发现端点，必须显式写进结果，
     绝不退化成"返回空列表"假装扫过。这是本项目 Round 83/84 修掉的那类谎报。
  2. **不编造端点**：discover 为 null 的源，本脚本不会尝试拼 URL 去猜。
  3. **可复现**：每次扫描输出 state/scan-<UTC>.json，含源状态与原始条目数。

用法：
  python pipeline/oss_radar.py --probe           # 健康检查所有源，打印可达性表
  python pipeline/oss_radar.py --plan            # 打印 72 类覆盖统计
  python pipeline/oss_radar.py --verify          # 校验两份 yaml 交叉引用一致
  python pipeline/oss_radar.py --scan --priority P0 --per-intent 10
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import httpx
import yaml

ROOT = Path(__file__).resolve().parent.parent
SOURCES_YAML = ROOT / "sources.yaml"
INTENTS_YAML = ROOT / "intents.yaml"
STATE_DIR = ROOT / "state"

USER_AGENT = "LiuHao-OSS-Radar/0.1 (+https://github.com/yhd952700-prog/YHD)"

# 状态字面量 —— 全部显式，禁止用空列表表达失败
OK = "ok"
UNREACHABLE = "unreachable"
NOT_DISCOVERABLE = "not-discoverable"
AUTH_REQUIRED = "auth-required"
OUT_OF_SCOPE = "out-of-scope"
ERROR = "error"

# 全量型源：拉全量后**本地**按关键词过滤，因此 queries 为空也要执行。
# 若沿用"无查询即跳过"，这类源会恒产出 0 条却毫无报错 —— 属于必须消灭的静默失败。
FULL_SCAN_SOURCES = {"mcp_registry", "linux_foundation", "spdx"}


# --------------------------------------------------------------------------- 数据模型
@dataclass
class SourceResult:
    source_id: str
    label: str
    status: str
    count: int = 0
    detail: str = ""
    elapsed_ms: int = 0
    items: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class Candidate:
    """统一候选条目。字段缺失写 None，不编造。"""
    source: str
    intent: str
    name: str
    url: str | None = None
    description: str | None = None
    stars: int | None = None
    license: str | None = None
    updated_at: str | None = None
    language: str | None = None
    raw_score: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# --------------------------------------------------------------------------- 加载
def load_sources() -> dict[str, dict[str, Any]]:
    data = yaml.safe_load(SOURCES_YAML.read_text(encoding="utf-8"))
    return {s["id"]: s for s in data.get("sources", [])}


def load_intents() -> list[dict[str, Any]]:
    data = yaml.safe_load(INTENTS_YAML.read_text(encoding="utf-8"))
    return data.get("coverage", [])


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


# --------------------------------------------------------------------------- HTTP
def _request(method: str, url: str, params: dict | None = None,
             json_body: dict | None = None, timeout: float = 20.0) -> httpx.Response:
    with httpx.Client(timeout=timeout, follow_redirects=True,
                      headers={"User-Agent": USER_AGENT, "Accept": "application/json"}) as c:
        if method.upper() == "POST":
            return c.post(url, json=json_body or {}, params=params)
        return c.get(url, params=params)


# 探活专用查询词。必须是**非空**的：GitHub search 传空 q 返回 422、
# npm search 传空 text 返回 400 —— 这不是源不可用，是 probe 自己传错了参数。
PROBE_QUERY = "agent"


def render(template: str, query: str) -> str:
    return template.replace("{query}", query)


# --------------------------------------------------------------------------- 适配器
# 每个适配器：(source_spec, query, per_page) -> list[Candidate]
# 返回条目即可，HTTP 异常由调用方统一收敛成 UNREACHABLE。

def adapt_github(spec: dict, intent_id: str, query: str, per_page: int) -> list[Candidate]:
    url = spec["discover"]["url"]
    params = {k: render(v, query) if isinstance(v, str) else v
              for k, v in spec["discover"].get("params", {}).items()}
    params["per_page"] = per_page
    r = _request("GET", url, params=params)
    r.raise_for_status()
    out = []
    for it in r.json().get("items", []):
        lic = it.get("license") or {}
        out.append(Candidate(
            source="github",
            intent=intent_id,
            name=it.get("full_name", ""),
            url=it.get("html_url"),
            description=(it.get("description") or "")[:300],
            stars=it.get("stargazers_count"),
            license=lic.get("spdx_id") or lic.get("key"),
            updated_at=it.get("updated_at"),
            language=it.get("language"),
            raw_score=float(it.get("stargazers_count") or 0),
        ))
    return out


def adapt_gitlab(spec: dict, intent_id: str, query: str, per_page: int) -> list[Candidate]:
    url = spec["discover"]["url"]
    params = {k: render(v, query) if isinstance(v, str) else v
              for k, v in spec["discover"].get("params", {}).items()}
    params["per_page"] = per_page
    r = _request("GET", url, params=params)
    r.raise_for_status()
    out = []
    for it in r.json():
        out.append(Candidate(
            source="gitlab", intent=intent_id,
            name=it.get("path_with_namespace", ""),
            url=it.get("web_url"),
            description=(it.get("description") or "")[:300],
            stars=it.get("star_count"),
            license=None,  # GitLab v4 projects 列表不返回 license，需 item 端点二次确认
            updated_at=it.get("last_activity_at"),
            raw_score=float(it.get("star_count") or 0),
        ))
    return out


def adapt_npm(spec: dict, intent_id: str, query: str, per_page: int) -> list[Candidate]:
    url = spec["discover"]["url"]
    params = {k: render(v, query) if isinstance(v, str) else v
              for k, v in spec["discover"].get("params", {}).items()}
    params["size"] = per_page
    r = _request("GET", url, params=params)
    r.raise_for_status()
    out = []
    for obj in r.json().get("objects", []):
        p = obj.get("package", {})
        out.append(Candidate(
            source="npm", intent=intent_id,
            name=p.get("name", ""),
            url=p.get("links", {}).get("repository") or p.get("links", {}).get("homepage"),
            description=(p.get("description") or "")[:300],
            license=p.get("license"),
            updated_at=p.get("date"),
            raw_score=float(obj.get("score", {}).get("final", 0.0)) * 1000,
        ))
    return out


def adapt_huggingface(spec: dict, intent_id: str, query: str, per_page: int) -> list[Candidate]:
    url = spec["discover"]["url"]
    params = {k: render(v, query) if isinstance(v, str) else v
              for k, v in spec["discover"].get("params", {}).items()}
    params["limit"] = per_page
    r = _request("GET", url, params=params)
    r.raise_for_status()
    out = []
    for it in r.json():
        card = it.get("cardData") or {}
        out.append(Candidate(
            source="huggingface", intent=intent_id,
            name=it.get("modelId") or it.get("id", ""),
            url=f"https://huggingface.co/{it.get('modelId') or it.get('id', '')}",
            license=card.get("license") if isinstance(card, dict) else None,
            stars=it.get("likes"),
            updated_at=it.get("lastModified"),
            raw_score=float(it.get("downloads") or 0),
        ))
    return out


def _mcp_fetch_all(url: str, max_pages: int, timeout: float = 30.0) -> list[dict]:
    """分页拉取 MCP registry。**实测 limit 硬上限 100**（>100 返回 422
    'expected number <= 100'），因此必须走 metadata.nextCursor 翻页而不是一次拉爆。"""
    servers: list[dict] = []
    cursor: str | None = None
    for _ in range(max_pages):
        params: dict[str, Any] = {"limit": 100}  # 上限，不可调大
        if cursor:
            params["cursor"] = cursor
        r = _request("GET", url, params=params, timeout=timeout)
        r.raise_for_status()
        payload = r.json()
        servers.extend(payload.get("servers", []))
        cursor = (payload.get("metadata") or {}).get("nextCursor")
        if not cursor:
            break
    return servers


def adapt_mcp_registry(spec: dict, intent_id: str, query: str, per_page: int) -> list[Candidate]:
    """MCP 官方 registry：忽略 query 一次拉全量（分页），再本地按名字/描述过滤。"""
    url = spec["discover"]["url"]
    servers = _mcp_fetch_all(url, max_pages=spec.get("max_pages", 20))
    needle = query.lower().replace(" ", "")
    out = []
    for it in servers:
        srv = it.get("server", it)
        name = srv.get("name", "")
        desc = (srv.get("description") or "")
        blob = (name + desc).lower().replace(" ", "")
        if needle and needle not in blob:
            continue
        repo = srv.get("repository")
        out.append(Candidate(
            source="mcp_registry", intent=intent_id,
            name=name,
            url=repo.get("url") if isinstance(repo, dict) else None,
            description=desc[:300],
            license=None,
            raw_score=0.0,
        ))
    return sorted(out, key=lambda c: c.name)[:per_page]


# landscape.yml 约 1.1MB，每个 intent 都重拉既慢又会把连接拉断
# （实测出现过 RemoteProtocolError: Server disconnected）。同一进程内缓存。
_LANDSCAPE_CACHE: dict | None = None


def _load_landscape(url: str) -> dict:
    global _LANDSCAPE_CACHE
    if _LANDSCAPE_CACHE is None:
        r = _request("GET", url, params={}, timeout=60.0)
        r.raise_for_status()
        _LANDSCAPE_CACHE = yaml.safe_load(r.text)
    return _LANDSCAPE_CACHE


def adapt_landscape(spec: dict, intent_id: str, query: str, per_page: int) -> list[Candidate]:
    """CNCF landscape.yml（YAML 而非 JSON），按 item.name/repo_url 本地匹配。"""
    url = spec["discover"]["url"]
    data = _load_landscape(url)
    # 实测结构为三层嵌套：landscape(list of category) → subcategories → items。
    # 每一层都不是 `items`，直接 data.get("items") 会得到 None 并静默产出 0 条
    # —— 因此本机一次全量源空转未能被发现，现已加 ERROR 检测。
    items: list[dict] = []
    node = (data or {}).get("landscape", []) if isinstance(data, dict) else []
    if isinstance(node, list):
        for cat in node:
            if not isinstance(cat, dict):
                continue
            for sub in (cat.get("subcategories") or []):
                if not isinstance(sub, dict):
                    continue
                for it in (sub.get("items") or []):
                    if isinstance(it, dict):
                        items.append(it)
    elif isinstance(node, dict):
        items = [i for i in (node.get("items") or []) if isinstance(i, dict)]

    needle = query.lower().replace(" ", "")
    out = []
    for it in items:
        name = it.get("name", "")
        urls = [u for u in (it.get("repo_url"), it.get("homepage_url")) if u]
        blob = (name + " " + (it.get("description") or "")).lower().replace(" ", "")
        if needle and needle not in blob:
            continue
        out.append(Candidate(
            source="linux_foundation", intent=intent_id,
            name=name,
            url=urls[0] if urls else None,
            description=(it.get("description") or "")[:300],
            license=None,
            raw_score=float(it.get("stars") or 0),
        ))
    out.sort(key=lambda c: c.raw_score, reverse=True)
    return out[:per_page]


def adapt_spdx(spec: dict, intent_id: str, query: str, per_page: int) -> list[Candidate]:
    """SPDX 许可证清单 —— **不是发现源，是基准真值**。
    拉下来缓存到 state/spdx-licenses.json，供 License Gate 校验：
    任何候选的 license 字段若不在表里（Elastic-2.0 / SUL / OpenRAIL-M 等），
    直接标 high-risk。返回 0 条候选是语义正确的（这里不产候选）。"""
    url = spec["discover"]["url"]
    r = _request("GET", url, params={}, timeout=40.0)
    r.raise_for_status()
    data = r.json()
    licenses = data.get("licenses", [])
    cache = STATE_DIR / "spdx-licenses.json"
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps({
        "fetched_at": utc_now(),
        "version": data.get("licenseListVersion"),
        "count": len(licenses),
        "ids": sorted({lic.get("licenseId") for lic in licenses if lic.get("licenseId")}),
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    # 约定：用一个特殊候选向调用方回报"拉到多少条"，调用方据此区分 0 网络结果 vs 0 条基准
    return [Candidate(source="spdx", intent="__baseline__",
                      name=f"SPDX license baseline ({len(licenses)} ids)",
                      url=str(cache), raw_score=float(len(licenses)))]


def adapt_dockerhub(spec: dict, intent_id: str, query: str, per_page: int) -> list[Candidate]:
    url = spec["discover"]["url"]
    params = {k: render(v, query) if isinstance(v, str) else v
              for k, v in spec["discover"].get("params", {}).items()}
    params["page_size"] = min(per_page, 25)
    r = _request("GET", url, params=params)
    r.raise_for_status()
    out = []
    for it in r.json().get("results", []):
        out.append(Candidate(
            source="dockerhub", intent=intent_id,
            name=it.get("repo_name") or it.get("name", ""),
            url=f'https://hub.docker.com/r/{it.get("repo_name") or it.get("name", "")}',
            description=(it.get("short_description") or "")[:300],
            stars=it.get("star_count"),
            updated_at=it.get("last_updated"),
            raw_score=float(it.get("pull_count") or 0),
        ))
    return out


ADAPTERS: dict[str, Callable[..., list[Candidate]]] = {
    "github": adapt_github,
    "gitlab": adapt_gitlab,
    "npm": adapt_npm,
    "huggingface": adapt_huggingface,
    "mcp_registry": adapt_mcp_registry,
    "linux_foundation": adapt_landscape,
    "spdx": adapt_spdx,
    "dockerhub": adapt_dockerhub,
}


# --------------------------------------------------------------------------- 执行
# probe 阶段对"重"源的最小请求方式：避免健康检查就把全量数据拉下来。
# spdx **不**在此列 —— 它必须走适配器以便顺便缓存基准表。
_PROBE_LIGHT_PARAMS: dict[str, dict[str, Any]] = {
    "mcp_registry": {"limit": 1},      # 实测 >100 会 422；全量要翻 20 页，probe 不该做
    "linux_foundation": {},            # landscape.yml 约 1.1MB
}


def probe_source(spec: dict, timeout: float = 20.0) -> SourceResult:
    """单源健康检查。只看能不能拿到预期的东西，不做全量拉取。"""
    label = spec.get("label", spec["id"])
    sid = spec["id"]
    started = time.monotonic()

    if spec.get("auth") == "required":
        return SourceResult(sid, label, AUTH_REQUIRED, 0,
                            "该源强制鉴权，未配置凭据时跳过（诚实报 skipped）")

    if spec.get("discover") is None:
        return SourceResult(sid, label, NOT_DISCOVERABLE, 0,
                            "官方无发现端点，本轮作为被动源，不主动扫描")

    if sid not in ADAPTERS:
        return SourceResult(sid, label, NOT_DISCOVERABLE, 0,
                            "sources.yaml 有 discover 但 Python 侧未实现适配器 —— 待补")

    d = spec["discover"]
    try:
        if sid in _PROBE_LIGHT_PARAMS:
            params = {k: render(v, PROBE_QUERY) if isinstance(v, str) else v
                      for k, v in d.get("params", {}).items()}
            params.update(_PROBE_LIGHT_PARAMS[sid])
            r = _request(d.get("method", "GET"), d["url"], params=params, timeout=timeout)
            r.raise_for_status()
            items = [Candidate(source=sid, intent="_probe", name="probe")]
        else:
            items = ADAPTERS[sid](spec, "_probe", PROBE_QUERY, 1)
        elapsed = int((time.monotonic() - started) * 1000)
        note = ""
        if sid == "spdx":
            note = f"基准表已缓存 {int(items[0].raw_score) if items else 0} 个 license id"
        return SourceResult(sid, label, OK, len(items), note or f"{elapsed}ms", elapsed)
    except httpx.HTTPStatusError as e:
        return SourceResult(sid, label, UNREACHABLE, 0, f"HTTP {e.response.status_code}",
                            int((time.monotonic() - started) * 1000))
    except Exception as e:  # noqa: BLE001 —— 探测阶段必须收敛所有异常，但保留类型
        return SourceResult(sid, label, UNREACHABLE, 0, f"{type(e).__name__}: {e}"[:200],
                            int((time.monotonic() - started) * 1000))


def run_scan(sources: dict[str, dict], intents: list[dict], priorities: list[str],
             per_intent: int, sleep_s: float, limit_groups: list[str] | None = None) -> dict[str, Any]:
    started = time.monotonic()
    results: list[SourceResult] = []
    all_candidates: list[dict] = []
    skipped_intents: list[dict] = []

    chosen = [i for i in intents if i.get("priority") in priorities]
    if limit_groups:
        chosen = [i for i in chosen if i.get("group") in limit_groups]

    for intent in chosen:
        intent_id = str(intent.get("id", "?"))
        queries = intent.get("queries") or []
        src_ids = intent.get("sources") or []

        # 全量型源（拉全量后本地过滤，不需要查询词）即使 queries 为空也要跑一次。
        # 上一版把它们判成"无查询 → 跳过"，导致 MCP/CNCF 恒产出 0 条还不出错 —— 典型的静默失败。
        queries_for_src: dict[str, list[str]] = {}
        for sid in src_ids:
            if sid in FULL_SCAN_SOURCES:
                queries_for_src[sid] = queries or [""]
            else:
                queries_for_src[sid] = queries

        if intent.get("kind") == "horizon" or not src_ids or (not queries and not any(queries_for_src.values())):
            skipped_intents.append({
                "id": intent_id, "label": intent.get("label"),
                "reason": "kind=horizon 或无可用查询/源，本轮不扫描",
            })
            continue

        for src_id in src_ids:
            my_queries = queries_for_src.get(src_id, [])
            if not my_queries:
                continue
            spec = sources.get(src_id)
            if spec is None:
                results.append(SourceResult(src_id, src_id, ERROR, 0,
                                            f"intents.yaml 引用了不存在的源 {src_id}"))
                continue
            if spec.get("discover") is None:
                results.append(SourceResult(src_id, spec.get("label", src_id), NOT_DISCOVERABLE, 0,
                                            f"intent {intent_id}: 源无发现端点"))
                continue
            if src_id not in ADAPTERS:
                results.append(SourceResult(src_id, spec.get("label", src_id), NOT_DISCOVERABLE, 0,
                                            "缺 Python 适配器"))
                continue
            if spec.get("auth") == "required":
                results.append(SourceResult(src_id, spec.get("label", src_id), AUTH_REQUIRED, 0,
                                            f"intent {intent_id}: 需鉴权，已跳过"))
                continue

            got: list[Candidate] = []
            for q in my_queries:
                try:
                    got.extend(ADAPTERS[src_id](spec, intent_id, q, per_intent))
                except Exception as e:  # noqa: BLE001
                    results.append(SourceResult(src_id, spec.get("label", src_id), UNREACHABLE, 0,
                                                f"intent {intent_id} query={q!r}: {type(e).__name__}: {e}"[:200]))
                time.sleep(sleep_s if src_id == "github" else 0.3)

            # 全量型源在正常拉取下不可能一条都没有。若为 0 必是异常，显式报错而非静默。
            if not got and src_id in FULL_SCAN_SOURCES and my_queries == [""]:
                results.append(SourceResult(src_id, spec.get("label", src_id), ERROR, 0,
                                            f"intent {intent_id}: 全量源产出 0 条 —— 异常，需人工核查"))

            # 同一 intent 内多 query 去重
            seen, uniq = set(), []
            for c in got:
                key = (c.source, c.name.lower())
                if key in seen:
                    continue
                seen.add(key)
                uniq.append(c)

            results.append(SourceResult(src_id, spec.get("label", src_id), OK, len(uniq),
                                        f"intent {intent_id} ({intent.get('label', '')})"))
            all_candidates.extend(c.to_dict() for c in uniq)

    return {
        "scanned_at": utc_now(),
        "elapsed_s": round(time.monotonic() - started, 1),
        "config": {"priorities": priorities, "per_intent": per_intent, "sleep_s": sleep_s},
        "source_results": [asdict(r) for r in results],
        "skipped_intents": skipped_intents,
        "candidates": all_candidates,
        "counts": {
            "intents_scanned": len(chosen) - len(skipped_intents),
            "intents_skipped": len(skipped_intents),
            "candidates": len(all_candidates),
            "unique_projects": len({c["name"].lower() for c in all_candidates}),
        },
    }


# --------------------------------------------------------------------------- 报表
def print_probe_table(results: list[SourceResult]) -> None:
    sym = {OK: "[OK]  ", NOT_DISCOVERABLE: "[N/A] ", AUTH_REQUIRED: "[AUTH]",
           UNREACHABLE: "[FAIL]", ERROR: "[ERR] "}
    print(f"{'':7}{'source':22}{'detail'}")
    print("-" * 78)
    for r in results:
        print(f"{sym.get(r.status, '[?]  '):7}{r.source_id:22}{r.detail or ('%dms' % r.elapsed_ms)}")


def print_plan(sources: dict, intents: list[dict]) -> None:
    from collections import Counter
    prio = Counter(i.get("priority") for i in intents)
    kind = Counter(i.get("kind") for i in intents)

    print(f"覆盖蓝图：共 {len(intents)} 类")
    print(f"  优先级  P0={prio.get('P0', 0)}  P1={prio.get('P1', 0)}  P2={prio.get('P2', 0)}")
    print(f"  性质    platform={kind.get('platform', 0)}  topic={kind.get('topic', 0)}  horizon={kind.get('horizon', 0)}")

    scannable = set(ADAPTERS) & {k for k, v in sources.items() if v.get("discover")}
    missing_ref, dangling = [], []
    for i in intents:
        for s in (i.get("sources") or []):
            if s not in sources:
                dangling.append((i.get("id"), s))
            elif s not in scannable:
                missing_ref.append((i.get("id"), s))

    print(f"\n可真跑的源（有 discover + 有适配器 + 无需鉴权）：{sorted(scannable)}")
    if dangling:
        print(f"  ⚠ 引用了不存在的源：{dangling}")
    uniq_missing = sorted(set(missing_ref))
    if uniq_missing:
        print(f"  · 引用了暂不可跑的源（本轮实际会跳过并显式报告）：{uniq_missing}")

    horizons = [i for i in intents if i.get("kind") == "horizon"]
    print(f"\n本轮明确不扫描的 horizon 类（{len(horizons)}）：")
    for i in horizons:
        print(f"  [{i.get('id')}] {i.get('label')} —— {(i.get('liuhao_gap') or '')[:60]}")


# --------------------------------------------------------------------------- CLI
def main() -> int:
    ap = argparse.ArgumentParser(description="鎏灏全球开源生态雷达")
    ap.add_argument("--probe", action="store_true", help="健康检查所有源")
    ap.add_argument("--plan", action="store_true", help="打印 72 类覆盖统计")
    ap.add_argument("--scan", action="store_true", help="执行发现扫描")
    ap.add_argument("--priority", default="P0", help="逗号分隔的优先级，默认 P0")
    ap.add_argument("--group", default="", help="限定 group，逗号分隔，如 agent,security")
    ap.add_argument("--per-intent", type=int, default=10, help="每个 query 取多少条")
    ap.add_argument("--sleep", type=float, default=7.0, help="GitHub 每 query 间隔秒（匿名限流）")
    ap.add_argument("--out", default="", help="输出路径，默认 state/scan-<ts>.json")
    args = ap.parse_args()

    sources = load_sources()
    intents = load_intents()

    if args.plan:
        print_plan(sources, intents)
        return 0

    if args.probe:
        print("信号源健康检查（2026-09-13，经本机代理）\n")
        print_probe_table([probe_source(s) for s in sources.values()])
        return 0

    if args.scan:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        prios = [p.strip() for p in args.priority.split(",") if p.strip()]
        groups = [g.strip() for g in args.group.split(",") if g.strip()] or None
        report = run_scan(sources, intents, prios, args.per_intent, args.sleep, groups)

        out = Path(args.out) if args.out else STATE_DIR / f"scan-{utc_now()}.json"
        out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

        c = report["counts"]
        print(f"扫描完成 {report['elapsed_s']}s")
        print(f"  intent 扫了 {c['intents_scanned']} / 跳过 {c['intents_skipped']}")
        print(f"  候选 {c['candidates']} 条，去重后项目 {c['unique_projects']} 个")
        for r in report["source_results"]:
            if r["status"] != OK:
                print(f"  ! {r['source_id']}: {r['status']} — {r['detail']}")
        print(f"\n报告：{out}")
        return 0

    ap.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
