"""会话分页 + 历史搜索极限基线：1000 会话 / 单会话 500 轮的真实 HTTP 耗时。

不是单元测试，是"会不会卡住用户"的压测：起一台隔离实例（临时数据目录），
按真实键口径 ``liuhao-{principal}-{session}`` 灌入 50 万行，然后测界面真正会发的
请求：

* 会话列表分页 —— ``GET /v1/chat/sessions?limit=50&offset=0/50``；
* 单会话历史分页 —— ``GET /v1/chat/history?session_id=...&limit=200&offset=0/200``；
* 历史搜索 —— ``GET /v1/sessions/search?q=...``（FTS5 bigram 索引）；
* 降级路径 —— 单字中文查询必须回退逐行扫描，并明说"搜索变慢"；
* 索引体积 —— ``dbstat`` 统计 ``conversation_fts%`` 页大小。

门槛：首屏/翻页/搜索 p95 < 2 秒。超过即在该行标 SLOW，``--strict`` 时退出码 1。

运行：
    .venv\\Scripts\\python.exe scripts/acceptance/session_scale_baseline.py
    .venv\\Scripts\\python.exe scripts/acceptance/session_scale_baseline.py `
        --sessions 1000 --turns 500 --keep

判红条件（可直接进 preflight）：
* 任一行 p95 >= 2000 ms；
* 搜索 ``mode`` 不是 ``fts5-bigram`` 或 ``ready=false``；
* 会话分页 ``count`` 不等于请求的 limit，或 ``total`` 不等于会话数。
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sqlite3
import sys
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, REPO)

from scripts.acceptance.account_tree_e2e import (  # noqa: E402
    Instance,
    configure_harness,
    request,
)

SLOW_THRESHOLD_MS = 2000.0
SEARCH_ROUNDS = 10
PAGE_ROUNDS = 5

#: 会话主题：让不同会话命中不同关键词（否则 50 万行全命中，测不出选择性）。
TOPICS = (
    "签合同与预算",
    "客户续约",
    "季度复盘",
    "供应链风险",
    "招聘计划",
    "产品定价",
    "市场投放",
    "数据合规",
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _percentile(samples: List[float], ratio: float) -> float:
    if not samples:
        return 0.0
    ordered = sorted(samples)
    index = min(len(ordered) - 1, max(0, math.ceil(ratio * len(ordered)) - 1))
    return ordered[index]


def _timed(fn: Callable[[], Any], rounds: int = PAGE_ROUNDS) -> Dict[str, Any]:
    samples: List[float] = []
    value: Any = None
    for _ in range(rounds):
        started = time.perf_counter()
        value = fn()
        samples.append((time.perf_counter() - started) * 1000)
    return {
        "ms_best": round(min(samples), 1),
        "ms_p50": round(_percentile(samples, 0.50), 1),
        "ms_p95": round(_percentile(samples, 0.95), 1),
        "ms_worst": round(max(samples), 1),
        "rounds": rounds,
        "value": value,
    }


def _payload_bytes(body: Any) -> int:
    return len(json.dumps(body, ensure_ascii=False).encode("utf-8"))


def _seed(db_path: str, principal: str, sessions: int, turns: int) -> Dict[str, Any]:
    """批量灌数据（单事务、NORMAL 同步），并返回体量统计。"""
    from src.ai.conversation_store import ConversationStore

    # 先用真实 store 建表建索引，避免压测脚本自己复制 schema 走样。
    ConversationStore(db_path)
    connection = sqlite3.connect(db_path, timeout=60)
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA synchronous=NORMAL")
    rows: List[Tuple[str, int, str, str, float]] = []
    base_ts = time.time() - sessions * turns
    for s in range(sessions):
        key = f"liuhao-{principal}-session-{s:04d}"
        topic = TOPICS[s % len(TOPICS)]
        for turn in range(1, turns + 1):
            role = "user" if turn % 2 == 1 else "assistant"
            text = (
                f"第 {s:04d} 号会话 第 {turn:03d} 轮：请帮我把季度复盘的要点整理成三条，"
                f"并给出一个可以直接执行的下一步。本会话主题：{topic}。"
                f"（压测占位文本，长度贴近真实消息）"
            )
            rows.append((key, turn, role, text, base_ts + s * turns + turn))
    started = time.perf_counter()
    connection.executemany(
        "INSERT INTO conversation_turns (principal_id, turn, role, content, created_at) "
        "VALUES (?, ?, ?, ?, ?)",
        rows,
    )
    connection.commit()
    elapsed_ms = (time.perf_counter() - started) * 1000
    connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    totals = connection.execute(
        "SELECT COUNT(*), COUNT(DISTINCT principal_id) FROM conversation_turns"
    ).fetchone()
    size_bytes = os.path.getsize(db_path)
    indexes = [
        row[1]
        for row in connection.execute("PRAGMA index_list('conversation_turns')").fetchall()
    ]
    connection.close()
    return {
        "rows": int(totals[0]),
        "sessions": int(totals[1]),
        "seed_ms": round(elapsed_ms, 1),
        "db_bytes": size_bytes,
        "indexes": indexes,
    }


def _build_index(db_path: str) -> Dict[str, Any]:
    """在脚本进程里把 FTS5 索引建到底（网关预热默认关闭，避免抢 CPU）。"""
    from src.ai.conversation_store import ConversationStore

    store = ConversationStore(db_path)
    started = time.perf_counter()
    status = store.build_search_index()
    elapsed_ms = (time.perf_counter() - started) * 1000
    return {**status, "build_ms": round(elapsed_ms, 1)}


def _index_stats(db_path: str) -> Dict[str, Any]:
    """FTS 索引体积（dbstat 按表名前缀统计页大小）。"""
    connection = sqlite3.connect(db_path, timeout=60)
    try:
        pages = connection.execute(
            "SELECT COALESCE(SUM(pgsize), 0) FROM dbstat "
            "WHERE name LIKE 'conversation_fts%'"
        ).fetchone()[0]
        index_bytes: Optional[int] = int(pages)
    except sqlite3.Error:
        index_bytes = None
    connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    total_bytes = os.path.getsize(db_path)
    connection.close()
    return {"index_bytes": index_bytes, "db_bytes": total_bytes}


def _register_root(inst: Instance) -> str:
    status, _ = request(
        "POST", inst.base + "/v1/accounts/register",
        {"principal": "root1", "password": "rootpass123", "display_name": "主账号"},
    )
    if status != 200:
        raise RuntimeError(f"注册根账号失败：{status}")
    status, body = request(
        "POST", inst.base + "/v1/auth/login",
        {"principal": "root1", "secret": "rootpass123"},
    )
    token = body.get("access_token") or ""
    if status != 200 or not token:
        raise RuntimeError(f"根账号登录失败：{status} {body}")
    return token


def run(args: argparse.Namespace) -> int:
    root = args.data_root or os.path.join(
        REPO, ".acceptance", "session_scale", datetime.now().strftime("%m%d-%H%M%S")
    )
    # 索引由脚本自己建：网关预热若同时开工会和压测抢 CPU，测出的 p95 不干净。
    os.environ["LIUHAO_SEARCH_INDEX_WARMUP"] = "0"
    configure_harness(port_base=args.port_base, data_root=root)
    report: Dict[str, Any] = {
        "started_at": _now_iso(),
        "params": {"sessions": args.sessions, "turns": args.turns},
        "threshold_ms": SLOW_THRESHOLD_MS,
        "rows": [],
        "bottlenecks": [],
        "checks": [],
    }
    inst = Instance("liuhao-scale-")
    try:
        token = _register_root(inst)
        db_path = inst.env["CONVERSATION_DB_PATH"]
        seed = _seed(db_path, "root1", args.sessions, args.turns)
        report["dataset"] = seed

        index = _build_index(db_path)
        index_stats = _index_stats(db_path)
        report["index"] = {**index, **index_stats}

        auth = {"Authorization": f"Bearer {token}"}

        def get(path: str) -> Tuple[int, Any]:
            return request("GET", inst.base + path, headers=auth)

        # ── 首屏：会话列表只取一页 ────────────────────────────────
        page1 = _timed(lambda: get(f"/v1/chat/sessions?limit=50&offset=0"))
        page2 = _timed(lambda: get("/v1/chat/sessions?limit=50&offset=50"))
        _, body1 = page1["value"]
        _, body2 = page2["value"]
        page1_count = len(body1.get("sessions") or [])
        page1_total = int(body1.get("total") or 0)
        page1_bytes = _payload_bytes(body1)
        report["checks"].append(
            {
                "name": "会话分页 limit=50",
                "ok": page1_count == min(50, args.sessions)
                and page1_total == args.sessions
                and (
                    args.sessions <= 50
                    or page2["value"][1].get("sessions") != body1.get("sessions")
                ),
                "detail": f"第1页 {page1_count} 条 / 总数 {page1_total} / "
                f"第2页首条 {str((body2.get('sessions') or [''])[0])}",
            }
        )

        # ── 单会话历史：最近一页 + 更早一页 ──────────────────────
        long_session = f"session-{args.sessions - 1:04d}"
        latest = _timed(
            lambda: get(f"/v1/chat/history?session_id={long_session}&limit=200&offset=0")
        )
        older = _timed(
            lambda: get(f"/v1/chat/history?session_id={long_session}&limit=200&offset=200")
        )
        _, latest_body = latest["value"]
        _, older_body = older["value"]
        latest_count = len(latest_body.get("history") or [])
        older_count = len(older_body.get("history") or [])
        expected_messages = args.turns * 2
        report["checks"].append(
            {
                "name": "历史分页 limit=200",
                "ok": latest_count == min(200, expected_messages)
                and older_count == max(0, expected_messages - 200)
                and int(latest_body.get("total") or 0) == expected_messages
                and bool(latest_body.get("has_more")) == (expected_messages > 200),
                "detail": f"最新页 {latest_count} 条 / 更早页 {older_count} 条 / "
                f"总数 {latest_body.get('total')}",
            }
        )

        # ── 搜索：常见 2 字 / 3 字 / 英文 / 单字降级 ──────────────
        searches = {
            "常见 2 字（复盘）": "复盘",
            "3 字短语（季度复盘）": "季度复盘",
            "主题词（供应链）": "供应链",
            "单字降级（客）": "客",
        }
        search_timing: Dict[str, Dict[str, Any]] = {}
        for label, query in searches.items():
            path = f"/v1/sessions/search?q={urllib.parse.quote(query)}&limit=20&offset=0"
            search_timing[label] = _timed(
                lambda p=path: get(p), rounds=SEARCH_ROUNDS
            )
        _, fts_body = search_timing["常见 2 字（复盘）"]["value"]
        _, scan_body = search_timing["单字降级（客）"]["value"]
        report["checks"].append(
            {
                "name": "搜索走 FTS5 索引",
                "ok": fts_body.get("mode") == "fts5-bigram"
                and bool(fts_body.get("ready"))
                and int(fts_body.get("total") or 0) > 0,
                "detail": f"mode={fts_body.get('mode')} ready={fts_body.get('ready')} "
                f"total={fts_body.get('total')}",
            }
        )
        report["checks"].append(
            {
                "name": "单字查询降级并明说变慢",
                "ok": scan_body.get("mode") == "scan"
                and bool(scan_body.get("degraded"))
                and "变慢" in str(scan_body.get("notice") or ""),
                "detail": f"mode={scan_body.get('mode')} notice={scan_body.get('notice')}",
            }
        )

        # ── 挂载回填标题（界面并发 20 × history）──────────────────
        def first_screen() -> List[Tuple[int, Any]]:
            ids = [f"session-{i:04d}" for i in range(min(20, args.sessions))]

            def one(session_id: str) -> Tuple[int, Any]:
                return get(f"/v1/chat/history?session_id={session_id}")

            with ThreadPoolExecutor(max_workers=8) as pool:
                return list(pool.map(one, ids))

        mount = _timed(first_screen, rounds=3)

        def row(name: str, measured: Dict[str, Any], note: str) -> None:
            report["rows"].append(
                {
                    "name": name,
                    "ms_p50": measured["ms_p50"],
                    "ms_p95": measured["ms_p95"],
                    "ms_best": measured["ms_best"],
                    "ms_worst": measured["ms_worst"],
                    "slow": measured["ms_p95"] >= SLOW_THRESHOLD_MS,
                    "note": note,
                }
            )

        row(
            "首屏 会话列表第1页（limit=50）",
            page1,
            f"返回 {page1_count}/{page1_total} 个会话，{page1_bytes} 字节",
        )
        row(
            "会话列表第2页（offset=50）",
            page2,
            "续页与第1页不重叠，滚到底继续加载",
        )
        row(
            "打开长会话 最新一页（limit=200）",
            latest,
            f"返回 {latest_count}/{(latest_body.get('total') or 0)} 条，"
            f"{_payload_bytes(latest_body)} 字节",
        )
        row(
            "加载更早的消息（offset=200）",
            older,
            f"返回 {older_count} 条，保持时间顺序",
        )
        for label, measured in search_timing.items():
            _, body = measured["value"]
            row(
                f"搜索 {label}",
                measured,
                f"mode={body.get('mode')} total={body.get('total')} "
                f"{_payload_bytes(body)} 字节"
                + (f"；{body.get('notice')}" if body.get("notice") else ""),
            )
        row(
            "首屏 回填标题（并发 20 × history 全量）",
            mount,
            "界面挂载时最多并发 20 个 history（标题需要首条消息，暂未分页）",
        )

        slow = [r for r in report["rows"] if r["slow"]]
        failed = [c for c in report["checks"] if not c["ok"]]
        if failed:
            for check in failed:
                report["bottlenecks"].append(
                    f"能力校验未通过：{check['name']}（{check['detail']}）"
                )
        for item in slow:
            report["bottlenecks"].append(
                f"{item['name']} p95 {item['ms_p95']} ms 超过 {SLOW_THRESHOLD_MS:.0f} ms"
            )
        report["slow_count"] = len(slow)
        report["check_fail_count"] = len(failed)
        report["finished_at"] = _now_iso()

        fts = report["index"]
        print("\n=== 会话分页 / 搜索极限基线 ===")
        print(
            f"数据：{args.sessions} 会话 × {args.turns} 轮 = {seed['rows']} 行，"
            f"库 {seed['db_bytes'] / 1e6:.1f} MB，灌入 {seed['seed_ms'] / 1000:.1f}s"
        )
        print(
            f"索引：mode={fts.get('mode')}，建索引 {fts.get('build_ms', 0) / 1000:.1f}s，"
            f"索引体积 "
            + (
                f"{(fts.get('index_bytes') or 0) / 1e6:.1f} MB"
                if fts.get("index_bytes") is not None
                else "不可用（dbstat 未编译）"
            )
            + f"，含索引后库 {fts.get('db_bytes', seed['db_bytes']) / 1e6:.1f} MB"
        )
        print(f"门槛：p95 < {SLOW_THRESHOLD_MS:.0f} ms\n")
        print("| 操作 | p50 | p95 | 最好/最差 | 判定 | 说明 |")
        print("| ---- | --- | --- | --------- | ---- | ---- |")
        for item in report["rows"]:
            verdict = "SLOW" if item["slow"] else "OK"
            print(
                f"| {item['name']} | {item['ms_p50']} ms | {item['ms_p95']} ms | "
                f"{item['ms_best']}/{item['ms_worst']} ms | {verdict} | {item['note']} |"
            )
        print("\n能力校验：")
        for check in report["checks"]:
            print(f"- [{'PASS' if check['ok'] else 'FAIL'}] {check['name']}：{check['detail']}")
        if report["bottlenecks"]:
            print("\n瓶颈 / 判红：")
            for index, text in enumerate(report["bottlenecks"], start=1):
                print(f"{index}. {text}")
        else:
            print("\n瓶颈：无（p95 全部低于 2 秒，能力校验全绿）")

        os.makedirs(os.path.join(REPO, ".acceptance", "session_scale"), exist_ok=True)
        out = os.path.join(REPO, ".acceptance", "session_scale", "report.json")
        with open(out, "w", encoding="utf-8") as handle:
            json.dump(report, handle, ensure_ascii=False, indent=2)
        print(f"\n报告：{out}")
        if args.strict and (report["slow_count"] or report["check_fail_count"]):
            return 1
        return 0
    finally:
        inst.stop(cleanup=not args.keep)
        if args.keep:
            print(f"数据目录保留：{inst.root}")


def _parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="会话分页与历史搜索极限基线")
    parser.add_argument("--sessions", type=int, default=1000)
    parser.add_argument("--turns", type=int, default=500)
    parser.add_argument("--port-base", type=int, default=25080)
    parser.add_argument("--data-root", default=None)
    parser.add_argument("--keep", action="store_true", help="保留临时数据目录")
    parser.add_argument("--strict", action="store_true", help="超 2 秒或能力校验失败时退出码 1")
    return parser.parse_args(argv)


if __name__ == "__main__":
    raise SystemExit(run(_parse_args()))
