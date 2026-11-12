"""采集器：只读地读取仓库事实，产出 Gap 列表。每个采集器独立失败，不拖垮整次扫描。"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

from models import (
    Gap,
    auto_eligibility,
    downgrade,
    worse,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = REPO_ROOT / "outputs" / "self-improve"


def rel(path: Path) -> str:
    try:
        return path.resolve().relative_to(REPO_ROOT).as_posix()
    except ValueError:
        return path.as_posix()


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


# ---------------------------------------------------------------------------
# markdown 表格解析
# ---------------------------------------------------------------------------
@dataclass
class TableRow:
    cells: List[str]
    line_no: int


@dataclass
class MarkdownTable:
    section: str
    header: List[str]
    rows: List[TableRow]
    start_line: int


_SEP_RE = re.compile(r"^\s*\|?[\s:|-]+\|?\s*$")


def _split_row(line: str) -> List[str]:
    line = line.strip()
    if line.startswith("|"):
        line = line[1:]
    if line.endswith("|"):
        line = line[:-1]
    return [cell.strip() for cell in line.split("|")]


def iter_tables(text: str) -> List[MarkdownTable]:
    lines = text.splitlines()
    tables: List[MarkdownTable] = []
    section = ""
    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        if stripped.startswith("#"):
            section = stripped.lstrip("#").strip()
        if stripped.startswith("|") and i + 1 < len(lines) and _SEP_RE.match(lines[i + 1]):
            header = _split_row(line)
            start = i + 1
            i += 2
            rows: List[TableRow] = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                if not _SEP_RE.match(lines[i]):
                    rows.append(TableRow(_split_row(lines[i]), i + 1))
                i += 1
            tables.append(MarkdownTable(section, header, rows, start))
            continue
        i += 1
    return tables


def _status_index(header: Sequence[str]) -> Optional[int]:
    for idx, cell in enumerate(header):
        low = cell.lower()
        if "状态" in cell or low == "status" or "status" in low:
            return idx
    return None


def _clean(text: str) -> str:
    text = text.replace("~~", "")
    text = re.sub(r"\*\*", "", text)
    text = re.sub(r"`", "", text)
    return re.sub(r"\s+", " ", text).strip()


def _first_token(text: str) -> str:
    m = re.match(r"^\s*(D-\d+|E-\d+|G-\d+|R-\d+|F-\d+|\d+)\b", _clean(text))
    return m.group(1) if m else ""


OPEN_STATUS_RE = re.compile(
    r"(open|accepted|未修|未改|未做|未验证|未确认|部分|决定延后|决定不做|"
    r"仍开放|in-progress|planned-next|design-only|deferred|未实现|"
    r"缺|风险接受|发布后处理|已知|需(?:重跑|复验|补|重新|在真实|重建)|"
    r"待重建|待补|未接入|无法|阻塞|必须|开放)",
    re.IGNORECASE,
)
CLOSED_ONLY_RE = re.compile(r"^(已修|已关闭|已解除|已提交|已完成|已更新|已核实)", re.IGNORECASE)


def _is_open_status(status: str) -> bool:
    if not status:
        return False
    if "~~" in status:
        return False
    if CLOSED_ONLY_RE.search(status):
        # 「已修/已完成」后面如果只剩“后续如需…”不算开放；
        # 只有明确还有未验证/部分/待补/必须回填等残留时才继续算缺口。
        residual = re.search(
            r"未(?:验证|修|做|接入|实测|确认|复验|关闭)|部分|仍开放|"
            r"待(?:重建|补|复验|确认)|需(?:重跑|复验|补|重新)|"
            r"剩余|尚(?:未|需|待)|必须(?:回填|重|补)",
            status,
        )
        if not residual:
            return False
    return bool(OPEN_STATUS_RE.search(status))


def _severity_from_text(text: str) -> str:
    # 先把否定表述去掉，避免「不阻塞用户」被当成阻塞。
    sanitized = re.sub(
        r"(?:不|不会|不再|未|没有)(?:阻塞|阻断|影响用户|影响交付|影响使用)",
        "",
        text,
    )
    low = sanitized.lower()
    if re.search(r"进行中|测试中|浸泡中|持续观测|观察中", sanitized):
        return "minor"
    level = "minor"
    if any(
        token in sanitized
        for token in ("越权", "数据丢失", "泄露", "无法登录", "阻塞", "不可用", "安全相关")
    ) or "critical" in low:
        level = "blocker"
    elif any(
        token in sanitized
        for token in (
            "部分", "未验证", "未修", "缺", "未接入", "未做", "风险", "未实现",
            "仍开放", "待", "发布后处理", "不可达", "失败", "不一致", "漂移",
        )
    ) or any(token in low for token in ("high", "major", "error", "failed")):
        level = "major"
    # 已经写了「已改/已修/已实现」的阻塞描述是历史背景，当前缺口按 major 处理。
    if level == "blocker" and re.search(r"已(?:改|修|实现|关闭|解除)", sanitized):
        level = "major"
    return level


# ---------------------------------------------------------------------------
# git 工作树
# ---------------------------------------------------------------------------
def git_dirty_files() -> Set[str]:
    try:
        proc = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
        )
    except Exception:
        return set()
    dirty: Set[str] = set()
    for line in proc.stdout.splitlines():
        if not line.strip():
            continue
        raw = line[3:].strip()
        if " -> " in raw:
            raw = raw.split(" -> ", 1)[1]
        raw = raw.strip('"')
        dirty.add(raw.replace("\\", "/"))
    return dirty


def git_head() -> str:
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=30,
        )
        return proc.stdout.strip()
    except Exception:
        return "unknown"


def _repo_relative(path_text: str) -> str:
    text = path_text.replace("\\", "/").strip()
    root = REPO_ROOT.as_posix().rstrip("/") + "/"
    if text.startswith(root):
        return text[len(root):]
    return text


# ---------------------------------------------------------------------------
# pytest
# ---------------------------------------------------------------------------
def _normalize_signature(message: str) -> str:
    text = message.strip().splitlines()[0] if message.strip() else ""
    text = _repo_relative(text)
    text = re.sub(r"[A-Za-z]:/[^\s\"')]+", "<path>", text)
    text = re.sub(r"0x[0-9a-fA-F]+", "0xADDR", text)
    text = re.sub(r"\s+", " ", text)
    return text[:200] or "no-message"


def _extract_paths_from_text(text: str, dirty: Set[str]) -> Set[str]:
    found: Set[str] = set()
    for match in re.finditer(r"(?:[A-Za-z]:[\\/][^\s\"')]+)", text):
        candidate = _repo_relative(match.group(0))
        if candidate in dirty:
            found.add(candidate)
    for match in re.finditer(r'File "([^"]+)"', text):
        candidate = _repo_relative(match.group(1))
        if candidate in dirty:
            found.add(candidate)
    for candidate in dirty:
        if candidate and candidate in text.replace("\\", "/"):
            found.add(candidate)
    return found


def collect_pytest(
    junit_path: Optional[Path],
    console_log: Optional[Path],
    dirty_files: Set[str],
    duration_seconds: Optional[float] = None,
) -> Tuple[List[Gap], Dict[str, Any]]:
    meta: Dict[str, Any] = {
        "junit": rel(junit_path) if junit_path else None,
        "console_log": rel(console_log) if console_log else None,
        "status": "SKIP",
        "tests": 0,
        "failures": 0,
        "errors": 0,
        "skipped": 0,
        "duration_seconds": round(duration_seconds, 2) if duration_seconds else None,
        "note": "",
    }
    if not junit_path or not junit_path.is_file():
        meta["note"] = "没有 JUnit XML；pytest 未运行或未完成"
        return [], meta

    try:
        root = ET.parse(junit_path).getroot()
    except ET.ParseError as exc:
        meta["note"] = f"JUnit XML 解析失败：{exc}"
        return [], meta

    cases = list(root.iter("testcase"))
    failures = [c for c in cases if c.find("failure") is not None]
    errors = [c for c in cases if c.find("error") is not None]
    skipped = [c for c in cases if c.find("skipped") is not None]
    meta.update(
        status="PASS" if not failures and not errors else "FAIL",
        tests=len(cases),
        failures=len(failures),
        errors=len(errors),
        skipped=len(skipped),
    )
    total_time = 0.0
    for case in cases:
        try:
            total_time += float(case.get("time") or 0)
        except ValueError:
            pass
    if duration_seconds:
        meta["duration_seconds"] = round(duration_seconds, 2)
        meta["duration_source"] = "measured_wall_clock"
    elif console_log and console_log.is_file():
        try:
            stat = console_log.stat()
            meta["duration_seconds"] = round(max(0.0, stat.st_mtime - stat.st_ctime), 1)
            meta["duration_source"] = "console_wall_clock"
        except OSError:
            meta["duration_seconds"] = round(total_time, 2)
            meta["duration_source"] = "junit_sum"
    else:
        meta["duration_seconds"] = round(total_time, 2)
        meta["duration_source"] = "junit_sum"

    grouped: Dict[str, Dict[str, Any]] = {}
    for case, kind in [(c, "failure") for c in failures] + [(c, "error") for c in errors]:
        node = case.find(kind)
        assert node is not None
        message = node.get("message") or ""
        body = node.text or ""
        signature = _normalize_signature(message or body)
        merge_key = f"pytest:{kind}:{signature}"
        entry = grouped.setdefault(
            merge_key,
            {
                "kind": kind,
                "signature": signature,
                "cases": [],
                "messages": [],
                "texts": [],
                "suspected": False,
            },
        )
        test_id = f"{case.get('classname','')}::{case.get('name','')}".strip(":")
        entry["cases"].append(test_id)
        if message:
            entry["messages"].append(message)
        if body:
            entry["texts"].append(body)
        file_attr = (case.get("file") or "").replace("\\", "/")
        if file_attr and any(d == file_attr or file_attr.endswith("/" + d) for d in dirty_files):
            entry["suspected"] = True
        if _extract_paths_from_text(body + "\n" + message, dirty_files):
            entry["suspected"] = True

    gaps: List[Gap] = []
    for merge_key, entry in grouped.items():
        kind = entry["kind"]
        suspected = bool(entry["suspected"])
        base_severity = "blocker" if kind == "error" else "major"
        severity = "minor" if suspected else base_severity
        label = "测试错误（采集/夹具）" if kind == "error" else "测试失败"
        if suspected:
            label += "（疑似并行改动导致）"
        title = f"{label}: {entry['signature']}"
        evidence_lines = []
        for idx, test_id in enumerate(entry["cases"][:8], 1):
            evidence_lines.append(f"{idx}. {test_id}")
        if len(entry["cases"]) > 8:
            evidence_lines.append(f"... 另有 {len(entry['cases']) - 8} 条同签名用例")
        first_msg = entry["messages"][0] if entry["messages"] else ""
        if first_msg:
            evidence_lines.append(f"message: {first_msg[:300]}")
        first_body = entry["texts"][0] if entry["texts"] else ""
        if first_body:
            trace = [ln.strip() for ln in first_body.splitlines() if ln.strip()][-3:]
            evidence_lines.append("traceback tail: " + " | ".join(trace)[:400])
        evidence = f"{rel(junit_path)}: {'; '.join(entry['cases'][:2])}\n" + "\n".join(evidence_lines)
        auto, reason = auto_eligibility("pytest", [], title)
        suggested = (
            "先单独复现该用例，并与基线失败集合做 diff；若命中工作树里未提交的并行改动，"
            "等那条线收敛后重跑，不要把它当成新回归。"
        )
        gaps.append(
            Gap(
                source="pytest",
                title=title,
                severity=severity,
                evidence=evidence,
                suggested_direction=suggested,
                auto_eligible=auto,
                reason=reason if suspected else reason + "；失败需人工分诊",
                merge_key=merge_key,
                paths=[],
                suspected_parallel=suspected,
            )
        )

    if console_log and console_log.is_file():
        tail = "\n".join(console_log.read_text(encoding="utf-8", errors="replace").splitlines()[-6:])
        meta["console_tail"] = tail[-1200:]
    return gaps, meta


def run_pytest(
    python: str,
    target: Optional[Sequence[str]] = None,
    timeout_seconds: int = 0,
    junit_path: Optional[Path] = None,
) -> Tuple[Optional[Path], Optional[Path], Optional[float], str]:
    """真实跑 pytest。返回 (junit, console_log, duration, note)。"""
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    junit = junit_path or (OUT_DIR / ".pytest-junit.xml")
    console = OUT_DIR / ".pytest-console.log"
    cmd = [
        python,
        "-m",
        "pytest",
        "-q",
        "--maxfail=0",
        "-p",
        "no:cacheprovider",
        "--junitxml",
        str(junit),
    ]
    if target:
        cmd.extend(target)
    started = time.perf_counter()
    note = ""
    try:
        proc = subprocess.run(
            cmd,
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_seconds or None,
        )
        console.write_text(proc.stdout + "\n" + proc.stderr, encoding="utf-8")
        note = f"exit={proc.returncode}"
    except subprocess.TimeoutExpired:
        elapsed = time.perf_counter() - started
        note = f"pytest 超过 {timeout_seconds}s 被终止；结果不完整"
        return junit if junit.exists() else None, console if console.exists() else None, elapsed, note
    elapsed = time.perf_counter() - started
    return (junit if junit.exists() else None), console, elapsed, note


# ---------------------------------------------------------------------------
# docs 门禁
# ---------------------------------------------------------------------------
def _run_script(name: str, script: str, timeout: int = 300) -> Dict[str, Any]:
    path = REPO_ROOT / script
    meta: Dict[str, Any] = {
        "name": name,
        "command": f"{Path(sys.executable).name} {script}",
        "status": "SKIP",
        "exit_code": None,
        "elapsed_seconds": None,
        "summary": "",
        "output_tail": "",
    }
    if not path.is_file():
        meta["note"] = f"脚本不存在：{script}"
        return meta
    started = time.perf_counter()
    try:
        proc = subprocess.run(
            [sys.executable, script],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        meta["note"] = f"超时（>{timeout}s），标记 SKIP"
        meta["elapsed_seconds"] = round(time.perf_counter() - started, 2)
        return meta
    meta["elapsed_seconds"] = round(time.perf_counter() - started, 2)
    meta["exit_code"] = proc.returncode
    meta["status"] = "PASS" if proc.returncode == 0 else "FAIL"
    out = proc.stdout + ("\n" + proc.stderr if proc.stderr else "")
    meta["output_tail"] = "\n".join(out.splitlines()[-12:])[-1500:]
    summary = [ln.strip() for ln in out.splitlines() if ln.strip() and not ln.strip().startswith("BOUNDARY")]
    meta["summary"] = " | ".join(summary[-4:])[-800:]
    return meta


def collect_docs_gates() -> Tuple[List[Gap], List[Dict[str, Any]]]:
    gaps: List[Gap] = []
    metas: List[Dict[str, Any]] = []

    link_meta = _run_script("doc-links", "scripts/verify_doc_links.py")
    metas.append(link_meta)
    if link_meta["status"] == "FAIL":
        out = link_meta["output_tail"]
        for match in re.finditer(r"^\s+(?P<file>.+):(?P<line>\d+)\s+->\s+(?P<target>.+?)\s*$", out, re.MULTILINE):
            target = match.group("target").strip()
            evidence = f"{match.group('file').strip()}:{match.group('line')} -> {target}"
            auto, reason = auto_eligibility("docs-links", [target], evidence)
            gaps.append(
                Gap(
                    source="docs-links",
                    title=f"文档链接失效: {target}",
                    severity="minor",
                    evidence=f"scripts/verify_doc_links.py 输出: {evidence}",
                    suggested_direction="修路径（文件存在）或修正文（概念不存在），不要删掉做此声明的句子。",
                    auto_eligible=auto,
                    reason=reason,
                    merge_key=f"doclink:{target.replace(chr(92), '/')}",
                    paths=[target],
                )
            )
        if not gaps:
            gaps.append(
                Gap(
                    source="docs-links",
                    title="文档链接门禁失败（未能逐条解析）",
                    severity="minor",
                    evidence=link_meta["summary"] or link_meta["output_tail"],
                    suggested_direction="跑一次 scripts/verify_doc_links.py 查看具体 dangling link。",
                    auto_eligible=False,
                    reason="门禁失败但输出无法解析，需人工查看",
                    merge_key="doclink:unparsed",
                )
            )

    index_meta = _run_script("docs-index", "scripts/verify_docs_index_coverage.py")
    metas.append(index_meta)
    if index_meta["status"] == "FAIL":
        out = index_meta["output_tail"]
        missing: List[str] = []
        in_list = False
        for line in out.splitlines():
            stripped = line.strip()
            if stripped.lower().startswith("the full list"):
                in_list = True
                continue
            if stripped.startswith("RESULT"):
                in_list = False
                continue
            if in_list and line.startswith("    ") and stripped and not stripped.startswith("-"):
                missing.append(stripped)
        for path in missing[:30]:
            auto, reason = auto_eligibility("docs-index", [path], path)
            gaps.append(
                Gap(
                    source="docs-index",
                    title=f"文档索引缺口: {path}",
                    severity="minor",
                    evidence=f"scripts/verify_docs_index_coverage.py 输出: {path} 未出现在 docs/README.md",
                    suggested_direction="按路径把该文档补进 docs/README.md，或确认它是否应被删除。",
                    auto_eligible=auto,
                    reason=reason,
                    merge_key=f"docindex:{path}",
                    paths=[path],
                )
            )
        if not missing:
            failure_lines = [
                ln.strip()[2:].strip()
                for ln in out.splitlines()
                if ln.strip().startswith("- ") and "RESULT" not in ln
            ]
            for line in failure_lines[:30]:
                auto, reason = auto_eligibility("docs-index", ["docs/README.md"], line)
                gaps.append(
                    Gap(
                        source="docs-index",
                        title=f"文档索引缺口: {line[:180]}",
                        severity="minor",
                        evidence=f"scripts/verify_docs_index_coverage.py 输出: {line}",
                        suggested_direction="按路径把缺失文档补进 docs/README.md，或删除索引里不存在的路径。",
                        auto_eligible=auto,
                        reason=reason,
                        merge_key=f"docindex:{line[:120]}",
                        paths=["docs/README.md"],
                    )
                )
        if not missing and not failure_lines:
            gaps.append(
                Gap(
                    source="docs-index",
                    title="文档索引覆盖门禁失败（未能逐条解析）",
                    severity="minor",
                    evidence=index_meta["summary"] or index_meta["output_tail"],
                    suggested_direction="跑一次 scripts/verify_docs_index_coverage.py 查看未收录文档。",
                    auto_eligible=False,
                    reason="门禁失败但输出无法解析，需人工查看",
                    merge_key="docindex:unparsed",
                )
            )
    return gaps, metas


# ---------------------------------------------------------------------------
# 安全报告
# ---------------------------------------------------------------------------
_LEVELS = {
    "CRITICAL": "blocker",
    "HIGH": "major",
    "MEDIUM": "minor",
    "LOW": "minor",
    "UNKNOWN": "minor",
}


def _max_level(text: str) -> str:
    upper = text.upper()
    for level in ("CRITICAL", "HIGH", "MEDIUM", "LOW", "UNKNOWN"):
        if level in upper:
            return _LEVELS[level]
    return "minor"


def collect_security(report_path: Path) -> Tuple[List[Gap], Dict[str, Any]]:
    meta: Dict[str, Any] = {
        "path": rel(report_path),
        "status": "SKIP",
        "tables_read": [],
        "backend_packages": [],
        "console_packages": [],
        "frontend_packages": [],
        "sdk_dev_items": 0,
        "package_names": [],
    }
    if not report_path.is_file():
        meta["note"] = "安全报告不存在"
        return [], meta
    text = read_text(report_path)
    tables = iter_tables(text)
    gaps: List[Gap] = []
    package_names: Set[str] = set()

    def add_group(
        title: str,
        rows: List[TableRow],
        key_group: str,
        dev_only: bool,
        unreachable: bool,
        ecosystem: str,
    ) -> None:
        severity = "minor"
        keys = [key_group]
        evidence = []
        packages = []
        for row in rows:
            cells = row.cells
            if not cells:
                continue
            package = _clean(cells[0])
            if not package or package in {"包", "项"}:
                continue
            packages.append(package)
            package_names.add(package)
            keys.append(f"dep:{ecosystem}:{package.lower()}")
            row_text = " | ".join(cells)
            severity = worse(severity, _max_level(row_text))
            evidence.append(f"{rel(report_path)}:{row.line_no}: {row_text[:400]}")
        if not packages:
            return
        if unreachable:
            severity = downgrade(severity, 1)
        if dev_only:
            severity = downgrade(severity, 1)
        auto, reason = auto_eligibility("security", [], title)
        gaps.append(
            Gap(
                source="security",
                title=title,
                severity=severity,
                evidence="\n".join(evidence),
                suggested_direction="按报告给出的修复版本升级并跑对应回归；若被上游精确 pin 卡住，登记风险接受并写明解锁条件。",
                auto_eligible=auto,
                reason=reason,
                merge_key=key_group,
                merge_keys=keys,
                paths=["requirements.txt"] if not dev_only else ["apps/console/console/package-lock.json"],
            )
        )

    for table in tables:
        header = [_clean(c) for c in table.header]
        section = table.section
        # 只读「第二轮」之后的收口表；§2.1 是升级前历史，不能当成现状。
        if section.startswith("14.") or section.startswith("## 14") or "14." in section[:12]:
            if "当前版本" in " ".join(header) and "可达性" in " ".join(header):
                add_group(
                    "后端剩余依赖漏洞（不可达，风险接受）",
                    table.rows,
                    "depgroup:backend-remaining",
                    dev_only=False,
                    unreachable=True,
                    ecosystem="backend",
                )
                meta["tables_read"].append(f"L{table.start_line} 后端剩余漏洞")
                meta["backend_packages"] = [
                    _clean(r.cells[0]) for r in table.rows if r.cells
                ]
            elif "advisory" in " ".join(header).lower() and "严重度" in " ".join(header):
                add_group(
                    "console devDependencies 漏洞（生产依赖 0）",
                    table.rows,
                    "depgroup:console-dev",
                    dev_only=True,
                    unreachable=False,
                    ecosystem="console",
                )
                meta["tables_read"].append(f"L{table.start_line} console 漏洞")
                meta["console_packages"] = [
                    _clean(r.cells[0]) for r in table.rows if r.cells
                ]
        elif section.startswith("3.1") or section.startswith("3.2"):
            if "严重度" in " ".join(header):
                add_group(
                    f"前端 devDependencies 漏洞（{section[:3]}）",
                    table.rows,
                    "depgroup:frontend-dev",
                    dev_only=True,
                    unreachable=False,
                    ecosystem="frontend",
                )
                meta["tables_read"].append(f"L{table.start_line} frontend 漏洞")
                meta["frontend_packages"] = [
                    _clean(r.cells[0]) for r in table.rows if r.cells
                ]

    # SDK 只有文字结论，没有表格。
    sdk_match = re.search(r"devDeps\s*(\d+)\s*条", text)
    if sdk_match:
        count = int(sdk_match.group(1))
        meta["sdk_dev_items"] = count
        line_no = text[: sdk_match.start()].count("\n") + 1
        if count > 0:
            auto, reason = auto_eligibility("security", [], "sdk devDependencies")
            gaps.append(
                Gap(
                    source="security",
                    title=f"SDK devDependencies {count} 条（生产依赖 0 条）",
                    severity=downgrade("major", 1),
                    evidence=f"{rel(report_path)}:{line_no}: {sdk_match.group(0)}；npm audit --omit=dev = 0",
                    suggested_direction="跟 TypeScript 主版本一起升级 devDeps；生产依赖保持 0 命中。",
                    auto_eligible=auto,
                    reason=reason,
                    merge_key="depgroup:sdk-dev",
                    paths=["sdk/javascript/liuhao-sdk/package-lock.json"],
                )
            )

    meta["status"] = "PASS" if gaps else "PASS(no-open-vulns)"
    meta["package_names"] = sorted(package_names)
    return gaps, meta


# ---------------------------------------------------------------------------
# SBOM
# ---------------------------------------------------------------------------
def _norm_pkg(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name.strip().lower())


def collect_sbom(sbom_path: Path, requirements_path: Path, pyproject_path: Path) -> Tuple[List[Gap], Dict[str, Any]]:
    meta: Dict[str, Any] = {
        "path": rel(sbom_path),
        "status": "SKIP",
        "components": 0,
        "lock_packages": 0,
        "covered": 0,
        "version_drift": [],
        "project_version": None,
        "sbom_version": None,
        "sbom_timestamp": None,
        "stale_vs_lock_mtime": None,
    }
    if not sbom_path.is_file():
        meta["note"] = "SBOM 不存在"
        return [], meta
    try:
        data = json.loads(read_text(sbom_path))
    except json.JSONDecodeError as exc:
        meta["note"] = f"SBOM JSON 解析失败：{exc}"
        return [], meta

    components = data.get("components") or []
    meta["components"] = len(components)
    pins: Dict[str, str] = {}
    if requirements_path.is_file():
        for line in read_text(requirements_path).splitlines():
            match = re.match(r"^\s*([A-Za-z0-9_.-]+)==([^\s;]+)", line)
            if match:
                pins[_norm_pkg(match.group(1))] = match.group(2).strip()
    meta["lock_packages"] = len(pins)

    sbom_versions: Dict[str, str] = {}
    for component in components:
        name = component.get("name") or ""
        version = component.get("version") or ""
        if name and version:
            sbom_versions[_norm_pkg(name)] = version

    covered = sorted(set(pins) & set(sbom_versions))
    meta["covered"] = len(covered)
    drift: List[Tuple[str, str, str]] = []
    for name in covered:
        if pins[name] != sbom_versions[name]:
            drift.append((name, sbom_versions[name], pins[name]))
    meta["version_drift"] = [{"package": n, "sbom": s, "lock": l} for n, s, l in drift]

    sbom_ts = (data.get("metadata") or {}).get("timestamp")
    meta["sbom_timestamp"] = sbom_ts
    sbom_mtime = None
    try:
        sbom_mtime = datetime.fromtimestamp(sbom_path.stat().st_mtime, tz=timezone.utc)
    except OSError:
        pass
    lock_mtime = None
    if requirements_path.is_file():
        try:
            lock_mtime = datetime.fromtimestamp(requirements_path.stat().st_mtime, tz=timezone.utc)
        except OSError:
            pass
    if sbom_mtime and lock_mtime:
        meta["stale_vs_lock_mtime"] = sbom_mtime < lock_mtime

    meta_version = ((data.get("metadata") or {}).get("component") or {}).get("version")
    meta["sbom_version"] = meta_version
    project_version = None
    if pyproject_path.is_file():
        try:
            import tomllib

            project_version = tomllib.loads(read_text(pyproject_path)).get("project", {}).get("version")
        except Exception:
            project_version = None
    meta["project_version"] = project_version

    gaps: List[Gap] = []
    evidence = []
    for name, sbom_v, lock_v in drift:
        evidence.append(f"{rel(sbom_path)}: {name} 记录 {sbom_v}，requirements.txt 为 {lock_v}")
    if drift:
        title = "SBOM 版本漂移: " + "、".join(name for name, _, _ in drift)
    else:
        title = "SBOM 早于依赖锁变更"
    if meta["stale_vs_lock_mtime"]:
        evidence.append(
            f"{rel(sbom_path)} mtime={sbom_mtime.isoformat()} 早于 {rel(requirements_path)} "
            f"mtime={lock_mtime.isoformat()}"
        )
    if drift or meta["stale_vs_lock_mtime"]:
        auto, reason = auto_eligibility("sbom", [rel(sbom_path)], title)
        gaps.append(
            Gap(
                source="sbom",
                title=title,
                severity="major",
                evidence="\n".join(evidence) or f"{rel(sbom_path)} 与 lock 时间顺序不一致",
                suggested_direction="在 payload 组装完成之后、Inno Setup 编译之前重新生成 SBOM，并与 payload 实际版本逐条比对。",
                auto_eligible=auto,
                reason=reason,
                merge_key="sbom:drift",
                merge_keys=["sbom:drift"] + [f"dep:any:{n}" for n, _, _ in drift],
                paths=[rel(sbom_path), rel(requirements_path)],
                status="已知；下次打包必须重生成",
            )
        )

    if meta_version and project_version and meta_version != project_version:
        auto, reason = auto_eligibility("sbom", [rel(sbom_path)], "SBOM project version")
        gaps.append(
            Gap(
                source="sbom",
                title=f"SBOM 项目版本与 pyproject 不一致: {meta_version} vs {project_version}",
                severity="major",
                evidence=f"{rel(sbom_path)} metadata.component.version={meta_version}；{rel(pyproject_path)} project.version={project_version}",
                suggested_direction="以实际构建版本为准重新生成 SBOM，不允许手改。",
                auto_eligible=auto,
                reason=reason,
                merge_key="sbom:project-version",
                paths=[rel(sbom_path), rel(pyproject_path)],
            )
        )

    meta["status"] = "PASS" if not gaps else "FAIL"
    return gaps, meta
