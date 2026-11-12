#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""一条命令产出「贾维斯哪里有问题、多严重、先修哪个」。

用法（在本仓库根执行）::

    python scripts/self_improve/scan_gaps.py                 # 跑全量 pytest + 全部门禁
    python scripts/self_improve/scan_gaps.py --skip-pytest  # 只读登记/报告/门禁（开发扫描器时用）
    python scripts/self_improve/scan_gaps.py --pytest-junit outputs/self-improve/.pytest-junit.xml
    python scripts/self_improve/scan_gaps.py --red-proof     # 先跑红证，再继续扫描
    python scripts/self_improve/scan_gaps.py --skip-preflight

输出（只写这里）::

    outputs/self-improve/gap-report.json
    outputs/self-improve/gap-report.md

判定规则见 models.py / collectors_*.py，全部是确定性代码，不依赖模型判断。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(REPO_ROOT))

from collectors import (  # noqa: E402
    OUT_DIR,
    collect_docs_gates,
    collect_pytest,
    collect_sbom,
    collect_security,
    git_dirty_files,
    git_head,
    read_text,
    rel,
    run_pytest,
)
from collectors_known import collect_known_gaps  # noqa: E402
from collectors_ops import collect_packaging, collect_todos, run_red_proof  # noqa: E402
from models import (  # noqa: E402
    AUTO_TODO_PREFIXES,
    SENSITIVE_FILENAMES,
    SENSITIVE_PATH_PREFIXES,
    SOURCE_PRIORITY,
    Gap,
    distribution,
    merge_gaps,
    severity_rank,
)

DOCS = REPO_ROOT / "docs"
KNOWN_GAPS = DOCS / "known-gaps.md"
SECURITY_REPORT = DOCS / "security/dependency-vulnerability-report-2026-11-08.md"
SBOM = DOCS / "compliance/sbom-cyclonedx.json"
REQUIREMENTS = REPO_ROOT / "requirements.txt"
PYPROJECT = REPO_ROOT / "pyproject.toml"


def _short(text: str, limit: int = 220) -> str:
    text = " / ".join(line.strip() for line in text.splitlines() if line.strip())
    return text[:limit] + ("…" if len(text) > limit else "")


def _md_escape(text: str) -> str:
    return text.replace("|", "\\|")


def _top_five(gaps: Sequence[Gap]) -> List[Gap]:
    candidates = [
        g for g in gaps
        if not g.suspected_parallel
        and not g.risk_accepted
        and not re.search(r"30\s*方向|战略落地|浸泡|进行中", g.title)
    ]
    ranked = sorted(
        candidates,
        key=lambda g: (
            severity_rank(g.severity),
            SOURCE_PRIORITY.index(g.source) if g.source in SOURCE_PRIORITY else 99,
            g.title,
        ),
    )
    # 硬证据（门禁/测试红）优先占一席，避免「现在最该修」被人工登记里的长期项淹没。
    hard = [g for g in ranked if g.source in ("pytest", "packaging", "docs-links", "docs-index")]
    picked: List[Gap] = []
    for gap in hard[:2] + ranked:
        if gap not in picked:
            picked.append(gap)
        if len(picked) == 5:
            break
    return sorted(
        picked,
        key=lambda g: (
            severity_rank(g.severity),
            SOURCE_PRIORITY.index(g.source) if g.source in SOURCE_PRIORITY else 99,
            g.title,
        ),
    )


def _render_markdown(
    gaps: Sequence[Gap],
    summary: Dict[str, Any],
    run: Dict[str, Any],
    repo: Dict[str, Any],
) -> str:
    lines: List[str] = []
    lines.append("# Jarvis · 缺口扫描报告")
    lines.append("")
    lines.append(f"- 生成时间：{run['generated_at']}")
    lines.append(f"- 仓库：`{repo['root']}`")
    lines.append(f"- HEAD：`{repo['head']}`；工作树未提交文件：**{repo['dirty_count']}** 个")
    lines.append(f"- 本次扫描耗时：**{run['duration_seconds']:.1f}s**")
    lines.append(f"- 缺口总数：**{summary['total']}**；自动可修：{summary['auto_eligible']}；疑似并行改动：{summary['suspected_parallel']}")
    lines.append("")

    # 0. 现在最该修的 5 件事
    lines.append("## 现在最该修的 5 件事")
    lines.append("")
    top = _top_five(gaps)
    if top:
        for idx, gap in enumerate(top, 1):
            lines.append(f"{idx}. **[{gap.severity}] {gap.title}**")
            lines.append(f"   - ID：`{gap.extra.get('id')}`；来源：{', '.join(gap.extra.get('sources', [gap.source]))}")
            lines.append(f"   - 证据：{_short(gap.evidence, 260)}")
            lines.append(f"   - 方向：{gap.suggested_direction}")
    else:
        lines.append("当前没有「非风险接受、非疑似并行改动」的缺口。风险接受项仍在下面的完整清单里，不代表消失。")
    lines.append("")
    lines.append(
        "> 排序规则：`blocker > major > minor`；已标风险接受、疑似并行改动、或属于"
        "「进行中/战略汇总」的条目不进入「现在就修」，但完整保留在下面的清单中。"
    )
    lines.append("")

    # 1. 分布
    dist = summary["severity"]
    lines.append("## 严重度分布")
    lines.append("")
    lines.append("| blocker | major | minor | 合计 | 自动可修 | 风险接受 | 疑似并行 |")
    lines.append("|---|---|---|---|---|---|---|")
    lines.append(
        f"| {dist.get('blocker',0)} | {dist.get('major',0)} | {dist.get('minor',0)} | "
        f"{summary['total']} | {summary['auto_eligible']} | {summary['risk_accepted']} | "
        f"{summary['suspected_parallel']} |"
    )
    lines.append("")
    if summary["sources"]:
        lines.append("来源分布：" + "、".join(f"`{k}` {v}" for k, v in summary["sources"].items()))
        lines.append("")

    # 2. 门禁与测试运行
    lines.append("## 运行事实（门禁 / pytest / 红证）")
    lines.append("")
    lines.append("### 文档与打包门禁")
    lines.append("")
    lines.append("| 门禁 | 状态 | 退出码 | 耗时(s) | 说明 |")
    lines.append("|---|---|---|---|---|")
    for gate in run["gates"]:
        note = gate.get("note") or gate.get("summary") or _short(gate.get("output_tail", ""), 120)
        lines.append(
            f"| {gate.get('name')} | {gate.get('status')} | {gate.get('exit_code', '')} | "
            f"{gate.get('elapsed_seconds', '')} | {_md_escape(_short(note or '', 160))} |"
        )
    lines.append("")
    p = run["pytest"]
    lines.append("### pytest")
    lines.append("")
    lines.append(f"- 状态：**{p.get('status')}**；用例 {p.get('tests',0)}；失败 {p.get('failures',0)}；错误 {p.get('errors',0)}；跳过 {p.get('skipped',0)}")
    lines.append(f"- 耗时：{p.get('duration_seconds')} 秒；JUnit：`{p.get('junit')}`；console：`{p.get('console_log')}`")
    if p.get("note"):
        lines.append(f"- 备注：{p['note']}")
    if p.get("console_tail"):
        lines.append("")
        lines.append("```text")
        lines.append(p["console_tail"])
        lines.append("```")
    lines.append("")
    red = run.get("red_proof")
    lines.append("### 红证（故意造一条失败测试）")
    lines.append("")
    if red:
        lines.append(f"- 结论：**{'PASS' if red.get('caught') else 'FAIL'}** — {red.get('note','')}")
        lines.append(f"- 做法：在 `{red.get('probe_file')}` 写一条 `assert False`，运行 pytest 后用同一 collector 解析 JUnit；抓到时立即删除探针与 JUnit，只保留 `outputs/self-improve/red-proof.json` 作为证据。")
        lines.append(f"- 命令：`{red.get('command')}`")
        lines.append(f"- 抓到：`{red.get('caught_gap',{}).get('id')}` {red.get('caught_gap',{}).get('title')}")
    else:
        lines.append("- 未执行。运行 `python scripts/self_improve/scan_gaps.py --red-proof --skip-pytest` 可生成证据。")
    lines.append("")

    # 3. 分严重度
    lines.append("## 完整缺口清单（按严重度排序）")
    lines.append("")
    for level in ("blocker", "major", "minor"):
        bucket = [g for g in gaps if g.severity == level]
        lines.append(f"### {level}（{len(bucket)}）")
        lines.append("")
        if not bucket:
            lines.append("无。")
            lines.append("")
            continue
        lines.append("| ID | 来源 | 缺口 | 自动可修 | 风险接受 | 证据（首行） |")
        lines.append("|---|---|---|---|---|---|")
        for gap in bucket:
            lines.append(
                f"| `{gap.extra.get('id')}` | {', '.join(gap.extra.get('sources', [gap.source]))} | "
                f"{_md_escape(_short(gap.title, 140))} | {'是' if gap.auto_eligible else '否'} | "
                f"{'是' if gap.risk_accepted else '否'} | {_md_escape(_short(gap.evidence, 180))} |"
            )
        lines.append("")
        # 细节（避免表格吞掉证据）
        for gap in bucket:
            lines.append(f"- `{gap.extra.get('id')}` **{gap.title}**")
            for evidence_line in gap.evidence.splitlines()[:4]:
                lines.append(f"  - {evidence_line}")
            if len(gap.evidence.splitlines()) > 4:
                lines.append(f"  - …（完整证据见 JSON）")
            lines.append(f"  - 方向：{gap.suggested_direction}")
            lines.append(f"  - 自动可修：{'是' if gap.auto_eligible else '否'}；理由：{gap.reason}")
        lines.append("")

    # 4. 疑似并行
    lines.append("## 疑似并行改动导致（不计为真缺陷）")
    lines.append("")
    parallel = [g for g in gaps if g.suspected_parallel]
    if parallel:
        lines.append("这些失败/门禁红命中了工作树里未提交的文件；先等对应并行线收敛，再判断是不是真回归。")
        lines.append("")
        for gap in parallel:
            lines.append(f"- `{gap.extra.get('id')}` [{gap.severity}] {gap.title} — {_short(gap.evidence, 180)}")
    else:
        lines.append("无。")
    lines.append("")

    # 5. 自动可修
    lines.append("## 自动可修清单（非核心区）")
    lines.append("")
    auto = [g for g in gaps if g.auto_eligible]
    if auto:
        for gap in auto:
            lines.append(f"- `{gap.extra.get('id')}` [{gap.severity}] {gap.title} — {_short(gap.evidence, 180)}")
    else:
        lines.append("无。自动线不允许触碰安全/网关策略/安装器/打包/发布门禁/依赖清单/lock/迁移/密钥相关内容。")
    lines.append("")

    # 6. TODO
    todo = run.get("todo")
    lines.append("## TODO / FIXME / XXX 统计")
    lines.append("")
    if todo:
        lines.append(f"- 全仓命中：**{todo.get('total',0)}** 处；标记分布：{todo.get('markers')}")
        lines.append(f"- 命中文件数（按计数）：" + "、".join(f"`{p}` {n}" for p, n in todo.get("top_files", [])[:10]))
        lines.append(f"- 报告中逐条展开前 {todo.get('top_n_reported',0)} 条（其余保留在 JSON 的计数里）。")
    lines.append("")

    # 7. 规则
    lines.append("## 判定规则（写死在脚本里）")
    lines.append("")
    lines.append("- 敏感路径/内容命中 → `auto_eligible=false`：")
    lines.append("  - 路径：" + "、".join(f"`{p}`" for p in SENSITIVE_PATH_PREFIXES))
    lines.append("  - 文件：" + "、".join(f"`{p}`" for p in SENSITIVE_FILENAMES) + "、任何 `*.lock`")
    lines.append("  - 内容：密钥/凭据/口令/secret/credential/password/api key/private key/JWT/token 等")
    lines.append("- 同一条缺口跨来源重复 → 按 `merge_keys` 并查集合并，保留全部来源与证据，不丢证据。")
    lines.append("- `known-gaps.md` 中标记「风险接受/accepted」→ 严重度降一级，但不允许消失。")
    lines.append("- 安全报告只读「第二轮收口表」（§14 与 §11），升级前的历史表不算现状。")
    lines.append("- 测试失败若命中工作树未提交文件 → 标 `suspected_parallel=true`，降为 minor，并在报告中单列。")
    lines.append("")
    lines.append("## 边界")
    lines.append("")
    lines.append("- 本扫描只读；唯一写盘位置是 `outputs/self-improve/`（含 pytest JUnit 与红证临时文件，红证用完即删）。")
    lines.append("- 报告是「证据清单」，不是修复承诺；任何条目都要先复核证据再改代码。")
    return "\n".join(lines) + "\n"


def _read_red_proof() -> Optional[Dict[str, Any]]:
    path = OUT_DIR / "red-proof.json"
    if not path.is_file():
        return None
    try:
        return json.loads(read_text(path))
    except json.JSONDecodeError:
        return None


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="只读缺口扫描器（self-improve 地基）")
    parser.add_argument("--repo", default=str(REPO_ROOT), help="仓库根（默认从脚本位置推断）")
    parser.add_argument("--pytest-junit", default="", help="复用已有 JUnit XML，不重新跑 pytest")
    parser.add_argument("--pytest-target", nargs="*", default=None, help="只跑指定测试路径（调试扫描器用）")
    parser.add_argument("--pytest-timeout", type=int, default=0, help="pytest 超时秒数；0=不限")
    parser.add_argument("--skip-pytest", action="store_true", help="跳过 pytest（只读登记/报告/门禁）")
    parser.add_argument("--skip-preflight", action="store_true", help="跳过 preflight（其余 freeze_checks 仍跑）")
    parser.add_argument("--red-proof", action="store_true", help="先跑一次故意失败的红证，再继续扫描")
    parser.add_argument("--todo-top", type=int, default=30, help="TODO 逐条展开条数")
    parser.add_argument("--out-dir", default=str(OUT_DIR), help="输出目录（默认 outputs/self-improve）")
    args = parser.parse_args(argv)

    repo_root = Path(args.repo).resolve()
    out_dir = Path(args.out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    started = time.perf_counter()
    generated_at = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
    dirty = git_dirty_files()
    head = git_head()

    timings: Dict[str, float] = {}
    collector_errors: Dict[str, str] = {}

    def timed(name: str, fn):
        t0 = time.perf_counter()
        try:
            result = fn()
        except Exception as exc:  # 一个采集器坏掉不影响其余
            collector_errors[name] = f"{type(exc).__name__}: {exc}"
            result = None
        timings[name] = round(time.perf_counter() - t0, 2)
        print(f"[scan] {name}: {timings[name]}s")
        return result

    red_proof = None
    if args.red_proof:
        red_proof = run_red_proof(sys.executable)
        print(f"[scan] red-proof: caught={red_proof.get('caught')}")

    security_gaps, security_meta = timed("security", lambda: collect_security(SECURITY_REPORT)) or ([], {})
    package_names = security_meta.get("package_names", [])

    known_gaps, known_meta = timed("known-gaps", lambda: collect_known_gaps(KNOWN_GAPS, package_names)) or ([], {})
    sbom_gaps, sbom_meta = timed("sbom", lambda: collect_sbom(SBOM, REQUIREMENTS, PYPROJECT)) or ([], {})

    docs_gaps, docs_metas = timed("docs-gates", collect_docs_gates) or ([], [])
    for gap in docs_gaps:
        if any(path in dirty for path in gap.paths):
            gap.suspected_parallel = True
    packaging_gaps, packaging_metas = timed(
        "packaging", lambda: collect_packaging(sys.executable, include_preflight=not args.skip_preflight)
    ) or ([], [])
    todo_gaps, todo_meta = timed("todo", lambda: collect_todos(args.todo_top)) or ([], {})

    pytest_meta: Dict[str, Any]
    pytest_gaps: List[Gap] = []
    if args.skip_pytest:
        pytest_meta = {
            "status": "SKIP",
            "note": "按 --skip-pytest 跳过",
            "junit": None,
            "console_log": None,
            "tests": 0,
            "failures": 0,
            "errors": 0,
            "skipped": 0,
            "duration_seconds": None,
        }
    elif args.pytest_junit:
        junit = Path(args.pytest_junit)
        if not junit.is_absolute():
            junit = repo_root / junit
        console = out_dir / ".pytest-console.log"
        pytest_gaps, pytest_meta = timed(
            "pytest", lambda: collect_pytest(junit, console if console.is_file() else None, dirty)
        ) or ([], {})
        pytest_meta["note"] = "复用已有 JUnit XML；未重新跑 pytest"
    else:
        junit, console, elapsed, note = run_pytest(
            sys.executable,
            target=args.pytest_target,
            timeout_seconds=args.pytest_timeout,
            junit_path=out_dir / ".pytest-junit.xml",
        )
        pytest_gaps, pytest_meta = collect_pytest(junit, console, dirty, elapsed)
        pytest_meta["note"] = note
        timings["pytest"] = round(elapsed or 0, 2)

    all_gaps = merge_gaps(
        list(security_gaps)
        + list(known_gaps)
        + list(sbom_gaps)
        + list(docs_gaps)
        + list(packaging_gaps)
        + list(todo_gaps)
        + list(pytest_gaps)
    )

    dist = distribution(all_gaps)
    sources_counter = Counter()
    for gap in all_gaps:
        for source in gap.extra.get("sources", [gap.source]):
            sources_counter[source] += 1

    summary = {
        "total": len(all_gaps),
        "severity": dist,
        "auto_eligible": sum(1 for g in all_gaps if g.auto_eligible),
        "risk_accepted": sum(1 for g in all_gaps if g.risk_accepted),
        "suspected_parallel": sum(1 for g in all_gaps if g.suspected_parallel),
        "merged_groups": sum(1 for g in all_gaps if g.extra.get("merged_count", 1) > 1),
        "sources": dict(sorted(sources_counter.items())),
    }

    if red_proof is None:
        red_proof = _read_red_proof()

    run_meta = {
        "generated_at": generated_at,
        "duration_seconds": round(time.perf_counter() - started, 2),
        "python": sys.executable,
        "out_dir": rel(out_dir),
        "collector_seconds": timings,
        "collector_errors": collector_errors,
        "pytest": pytest_meta,
        "gates": list(docs_metas) + list(packaging_metas),
        "red_proof": red_proof,
        "todo": todo_meta,
        "security": security_meta,
        "known_gaps": known_meta,
        "sbom": sbom_meta,
    }
    payload = {
        "schema_version": "1.0",
        "generated_at": generated_at,
        "repo": {
            "root": repo_root.as_posix(),
            "head": head,
            "dirty_count": len(dirty),
            "dirty_files": sorted(dirty),
        },
        "run": run_meta,
        "summary": summary,
        "rules": {
            "severity_order": ["blocker", "major", "minor"],
            "sensitive_paths": list(SENSITIVE_PATH_PREFIXES),
            "sensitive_filenames": list(SENSITIVE_FILENAMES),
            "sensitive_content": "密钥/凭据/口令/secret/credential/password/api key/private key/JWT/token",
            "auto_todo_prefixes": list(AUTO_TODO_PREFIXES),
            "source_priority": list(SOURCE_PRIORITY),
            "risk_accepted": "known-gaps 标记风险接受 → 降一级，不允许消失",
        },
        "gaps": [gap.to_dict() for gap in all_gaps],
    }

    json_path = out_dir / "gap-report.json"
    md_path = out_dir / "gap-report.md"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(
        _render_markdown(all_gaps, summary, {**run_meta, "gates": run_meta["gates"]}, payload["repo"]),
        encoding="utf-8",
    )

    print(f"[scan] total={summary['total']} severity={summary['severity']} "
          f"auto={summary['auto_eligible']} parallel={summary['suspected_parallel']} "
          f"duration={run_meta['duration_seconds']}s")
    print(f"[scan] wrote {json_path}")
    print(f"[scan] wrote {md_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
