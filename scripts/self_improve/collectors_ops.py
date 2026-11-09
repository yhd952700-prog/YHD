"""打包门禁、TODO/FIXME 扫描、红证（故意造一条失败测试）。"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

from collectors import (
    OUT_DIR,
    REPO_ROOT,
    _clean,
    collect_pytest,
    read_text,
    rel,
)
from models import AUTO_TODO_PREFIXES, Gap, auto_eligibility, path_is_sensitive

TODO_RE = re.compile(r"(?<![\w])(TODO|FIXME|XXX)(?![\w])")

EXCLUDED_PREFIXES = (
    ".git/",
    ".venv/",
    "node_modules/",
    "dist/",
    "dist-mid-",
    "outputs/",
    "reports/",
    "logs/",
    "data/",
    "memory_data/",
    ".ruff_cache/",
    "__pycache__/",
    ".hypothesis/",
    ".tmp",
    ".pytest",
    "ui-evidence/",
    ".screenshots/",
    "docs/archive/",
    "packaging/licenses/",
    "frontend/node_modules/",
    "apps/console/console/node_modules/",
    "LiuHao-O/node_modules/",
)

TEXT_SUFFIXES = {
    ".py", ".pyi", ".ts", ".tsx", ".js", ".jsx", ".css", ".html", ".md",
    ".yaml", ".yml", ".toml", ".ini", ".cfg", ".ps1", ".sh", ".bat", ".json",
    ".sql", ".txt", ".rs", ".go", ".java", ".c", ".h", ".cpp", ".vue",
    ".svelte", ".astro", ".iss", ".mjs", ".cjs",
}


def _excluded(path: str) -> bool:
    p = path.replace("\\", "/")
    return any(p.startswith(prefix) for prefix in EXCLUDED_PREFIXES)


def _list_repo_files() -> List[str]:
    try:
        proc = subprocess.run(
            ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=120,
        )
        files = [ln.strip() for ln in proc.stdout.splitlines() if ln.strip()]
        if files:
            return files
    except Exception:
        pass
    files = []
    for path in REPO_ROOT.rglob("*"):
        if path.is_file():
            files.append(rel(path))
    return files


def collect_todos(top_n: int = 30) -> Tuple[List[Gap], Dict[str, Any]]:
    files = _list_repo_files()
    total = 0
    markers: Counter = Counter()
    by_file: Counter = Counter()
    items: List[Dict[str, Any]] = []
    scanned = 0

    for path_str in files:
        if _excluded(path_str):
            continue
        path = REPO_ROOT / path_str
        if path.suffix.lower() not in TEXT_SUFFIXES:
            continue
        try:
            if path.stat().st_size > 1_500_000:
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        scanned += 1
        for line_no, line in enumerate(text.splitlines(), 1):
            for match in TODO_RE.finditer(line):
                total += 1
                markers[match.group(1)] += 1
                by_file[path_str] += 1
                items.append(
                    {
                        "path": path_str,
                        "line": line_no,
                        "marker": match.group(1),
                        "text": _clean(line)[:180],
                    }
                )

    meta = {
        "status": "PASS" if total == 0 else "FOUND",
        "total": total,
        "markers": dict(markers),
        "top_files": by_file.most_common(10),
        "scanned_files": scanned,
        "top_n_reported": min(top_n, len(items)),
    }

    # 先按路径/行号稳定排序，再取前 N 条；计数用全量。
    items.sort(key=lambda item: (item["path"], item["line"], item["marker"]))
    occurrence: Dict[Tuple[str, str], int] = {}
    gaps: List[Gap] = []
    for item in items[:top_n]:
        base = (item["path"], item["text"])
        occurrence[base] = occurrence.get(base, 0) + 1
        import hashlib

        digest = hashlib.sha1(base[1].encode("utf-8")).hexdigest()[:10]
        key = f"todo:{item['path']}:{digest}:{occurrence[base]}"
        title = f"{item['marker']} {item['path']}:{item['line']} {item['text'][:120]}"
        auto, reason = auto_eligibility("todo", [item["path"]], title)
        gaps.append(
            Gap(
                source="todo",
                title=title,
                severity="minor",
                evidence=f"{item['path']}:{item['line']}: {item['text']}",
                suggested_direction="确认注释是否仍需处理；若需要，按注释描述做最小改动并补回归；若已过时，删除注释。",
                auto_eligible=auto,
                reason=reason,
                merge_key=key,
                paths=[item["path"]],
            )
        )
    return gaps, meta


# ---------------------------------------------------------------------------
# 打包门禁
# ---------------------------------------------------------------------------
def _run_gate(name: str, cmd: Sequence[str], timeout: int, store_full: bool = False) -> Dict[str, Any]:
    meta: Dict[str, Any] = {
        "name": name,
        "command": " ".join(cmd),
        "status": "SKIP",
        "exit_code": None,
        "elapsed_seconds": None,
        "output_tail": "",
    }
    started = time.perf_counter()
    try:
        proc = subprocess.run(
            list(cmd),
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        meta["elapsed_seconds"] = round(time.perf_counter() - started, 2)
        meta["note"] = f"超时（>{timeout}s），标记 SKIP"
        return meta
    except OSError as exc:
        meta["note"] = f"无法启动：{exc}"
        return meta
    meta["elapsed_seconds"] = round(time.perf_counter() - started, 2)
    meta["exit_code"] = proc.returncode
    out = proc.stdout + ("\n" + proc.stderr if proc.stderr else "")
    if store_full:
        meta["output_full"] = out
    meta["output_tail"] = "\n".join(out.splitlines()[-15:])[-1800:]
    meta["status"] = "PASS" if proc.returncode == 0 else "FAIL"
    return meta


def collect_packaging(python: str, include_preflight: bool = True) -> Tuple[List[Gap], List[Dict[str, Any]]]:
    gaps: List[Gap] = []
    metas: List[Dict[str, Any]] = []
    freeze = REPO_ROOT / "scripts/release/freeze_checks.py"
    preflight = REPO_ROOT / "scripts/release/preflight.py"

    subs = [
        ("freeze:runbook", "runbook", 120, "major"),
        ("freeze:env", "env", 240, "major"),
        ("freeze:log-leak", "log-leak", 420, "blocker"),
        ("freeze:deps", "deps", 420, "blocker"),
        ("freeze:frontend-source", "frontend-source", 180, "major"),
    ]
    for key, sub, timeout, severity in subs:
        if not freeze.is_file():
            metas.append({"name": key, "status": "SKIP", "note": "scripts/release/freeze_checks.py 不存在"})
            continue
        meta = _run_gate(
            key,
            [python, "scripts/release/freeze_checks.py", sub, "--repo", "."],
            timeout,
        )
        metas.append(meta)
        if meta["status"] == "FAIL":
            gaps.append(
                Gap(
                    source="packaging",
                    title=f"打包门禁失败: freeze_checks {sub}",
                    severity=severity,
                    evidence=f"{meta['command']}\n{meta['output_tail']}",
                    suggested_direction="按门禁输出修根因；打包面改动必须人工复核并重新跑同一条门禁。",
                    auto_eligible=False,
                    reason="打包/安装面属于敏感区，不允许自动修改",
                    merge_key=key,
                    paths=["scripts/release/freeze_checks.py", "packaging/"],
                )
            )

    # frontend-payload：只有存在 payload 时才可能跑。
    payload = _find_payload()
    if payload:
        version = _project_version()
        meta = _run_gate(
            "freeze:frontend-payload",
            [
                python, "scripts/release/freeze_checks.py", "frontend-payload",
                "--repo", ".", "--payload", rel(payload), "--version", version,
            ],
            240,
        )
        metas.append(meta)
        if meta["status"] == "FAIL":
            gaps.append(
                Gap(
                    source="packaging",
                    title="打包门禁失败: freeze_checks frontend-payload",
                    severity="major",
                    evidence=f"{meta['command']}\n{meta['output_tail']}",
                    suggested_direction="确认 payload 内控制台与当前构建产物同源，必要时重新构建 payload。",
                    auto_eligible=False,
                    reason="打包/安装面属于敏感区，不允许自动修改",
                    merge_key="freeze:frontend-payload",
                    paths=["dist/"],
                )
            )
    else:
        metas.append(
            {
                "name": "freeze:frontend-payload",
                "status": "SKIP",
                "note": "没有找到 dist/final-*/payload/app 或 dist/payload/app",
            }
        )

    if include_preflight and preflight.is_file():
        meta = _run_gate(
            "preflight",
            [python, "scripts/release/preflight.py", "--json"],
            900,
            store_full=True,
        )
        metas.append(meta)
        payload_json = _extract_json(meta.get("output_full", meta["output_tail"]))
        if meta["status"] == "FAIL":
            if isinstance(payload_json, dict) and isinstance(payload_json.get("results"), list):
                for item in payload_json["results"]:
                    if item.get("status") != "FAIL":
                        continue
                    name = str(item.get("name") or item.get("gate") or "unknown")
                    detail = str(item.get("detail") or item.get("message") or "")
                    suspected = bool(re.search(r"未提交|工作树|dirty|未推送|git status", detail + name, re.IGNORECASE))
                    gaps.append(
                        Gap(
                            source="packaging",
                            title=f"打包门禁失败: preflight / {name}" + ("（疑似并行改动导致）" if suspected else ""),
                            severity="minor" if suspected else "major",
                            evidence=f"scripts/release/preflight.py --json: [{name}] {detail[:600]}",
                            suggested_direction="按 preflight 的逐项证据修复；工作树/HEAD 类失败等并行线收敛后再跑。",
                            auto_eligible=False,
                            reason="打包门禁属于敏感区，不允许自动修改",
                            merge_key=f"preflight:{name}",
                            suspected_parallel=suspected,
                            paths=["scripts/release/preflight.py"],
                        )
                    )
            else:
                gaps.append(
                    Gap(
                        source="packaging",
                        title="打包门禁失败: preflight（JSON 无法解析）",
                        severity="major",
                        evidence=f"{meta['command']}\n{meta['output_tail']}",
                        suggested_direction="直接运行 preflight，按终端输出逐项修复。",
                        auto_eligible=False,
                        reason="打包门禁属于敏感区，不允许自动修改",
                        merge_key="preflight:unparsed",
                        paths=["scripts/release/preflight.py"],
                    )
                )
    else:
        metas.append({"name": "preflight", "status": "SKIP", "note": "按参数跳过 preflight"})

    return gaps, metas


def _project_version() -> str:
    pyproject = REPO_ROOT / "pyproject.toml"
    if pyproject.is_file():
        try:
            import tomllib

            return tomllib.loads(read_text(pyproject)).get("project", {}).get("version", "0.1.1")
        except Exception:
            pass
    return "0.1.1"


def _find_payload() -> Optional[Path]:
    candidates = []
    for base in (REPO_ROOT / "dist").glob("final-*/payload/app"):
        if base.is_dir():
            candidates.append(base)
    plain = REPO_ROOT / "dist/payload/app"
    if plain.is_dir():
        candidates.append(plain)
    if not candidates:
        return None
    return max(candidates, key=lambda p: p.stat().st_mtime)


def _extract_json(tail: str) -> Optional[Dict[str, Any]]:
    start = tail.find("{")
    end = tail.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        return json.loads(tail[start : end + 1])
    except json.JSONDecodeError:
        return None


# ---------------------------------------------------------------------------
# 红证
# ---------------------------------------------------------------------------
def run_red_proof(python: str) -> Dict[str, Any]:
    """造一条假失败测试 -> 扫描器抓到 -> 立刻删除。证据写到 outputs/self-improve/red-proof.json。"""
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    probe_dir = OUT_DIR / ".red-probe"
    junit = OUT_DIR / ".red-proof-junit.xml"
    proof_path = OUT_DIR / "red-proof.json"
    probe = probe_dir / "test_self_improve_red_proof.py"
    data: Dict[str, Any] = {
        "generated_at": None,
        "probe_file": rel(probe),
        "command": None,
        "exit_code": None,
        "caught": False,
        "caught_gap": None,
        "cleanup": "probe file and junit removed after capture",
        "note": "",
    }
    try:
        probe_dir.mkdir(parents=True, exist_ok=True)
        probe.write_text(
            "def test_self_improve_red_proof():\n"
            "    assert False, 'SELF_IMPROVE_RED_PROOF: deliberate failure; scanner must catch this'\n",
            encoding="utf-8",
        )
        cmd = [
            python, "-m", "pytest", "-q", "--maxfail=0", "-p", "no:cacheprovider",
            "--rootdir", str(probe_dir), "--junitxml", str(junit), str(probe),
        ]
        data["command"] = " ".join(cmd)
        proc = subprocess.run(
            cmd,
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=300,
        )
        data["exit_code"] = proc.returncode
        gaps, meta = collect_pytest(junit if junit.exists() else None, None, set())
        data["parsed"] = meta
        matched = [gap for gap in gaps if "SELF_IMPROVE_RED_PROOF" in (gap.title + gap.evidence)]
        data["caught"] = bool(matched) and meta.get("failures") == 1
        if matched:
            data["caught_gap"] = {
                "id": matched[0].extra.get("id"),
                "source": matched[0].source,
                "severity": matched[0].severity,
                "title": matched[0].title,
                "evidence": matched[0].evidence,
                "merged_count": matched[0].extra.get("merged_count"),
            }
        data["note"] = (
            "红证通过：故意失败的测试被 pytest 采集，JUnit 被 collector 解析成 gap。"
            if data["caught"]
            else "红证失败：扫描器没有抓到故意失败，先修采集链。"
        )
        from datetime import datetime, timezone

        data["generated_at"] = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
        proof_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    finally:
        try:
            shutil.rmtree(probe_dir)
        except OSError:
            pass
        try:
            junit.unlink()
        except OSError:
            pass
    return data
