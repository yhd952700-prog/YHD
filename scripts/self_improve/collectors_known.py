"""解析 docs/known-gaps.md：只把「明确仍然开着」的条目变成缺口。

已修/已关闭/已解除的行会被状态过滤掉；风险接受的行保留但降一级。
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from collectors import _clean, _first_token, _is_open_status, _severity_from_text, iter_tables, read_text, rel
from models import Gap, auto_eligibility

STATUS_IN_HEADING_RE = re.compile(r"状态[：:]\s*\**([^*）)]+)")
PATH_RE = re.compile(r"(?<![\w/])((?:[\w.-]+/)+[\w.-]+\.(?:py|md|tsx?|jsx?|json|ya?ml|ini|ps1|iss|toml|sql))")
OPEN_HEADING_RE = re.compile(r"(已知|未修|未验证|部分|待|需|风险|下次|必须|未接入)")


def _slug(text: str) -> str:
    digest = hashlib.sha1(_clean(text).encode("utf-8")).hexdigest()[:10]
    return digest


def _heading_key(heading: str) -> str:
    token = _first_token(heading)
    return f"known:{token}" if token else f"known:section:{_slug(heading)}"


def _body(lines: Sequence[str], start: int) -> Tuple[str, List[Tuple[int, str]]]:
    body_lines: List[Tuple[int, str]] = []
    i = start
    while i < len(lines):
        line = lines[i]
        if line.startswith("#") or (line.strip() == "---" and body_lines):
            break
        if line.strip():
            body_lines.append((i + 1, line.strip()))
        i += 1
    excerpt = " ".join(_clean(t) for _, t in body_lines[:8])
    return excerpt[:800], body_lines


def _close_direction(body_lines: Sequence[Tuple[int, str]]) -> str:
    markers = ("什么算关掉它", "How to close", "关掉标准", "生成时机必须固定", "怎么关")
    for idx, (_, line) in enumerate(body_lines):
        if any(marker in line for marker in markers):
            collected: List[str] = []
            for _, follow in body_lines[idx + 1 : idx + 6]:
                if follow.startswith("**") and collected:
                    break
                collected.append(_clean(follow))
            text = " ".join(collected).strip()
            if text:
                return text[:400]
    return ""


def _paths_from(text: str) -> List[str]:
    found = []
    for match in PATH_RE.finditer(text):
        candidate = match.group(1).replace("\\", "/")
        if candidate not in found:
            found.append(candidate)
    return found


def _packages_in(text: str, package_names: Sequence[str]) -> List[str]:
    low = text.lower()
    found = []
    for name in package_names:
        needle = name.lower()
        if not needle:
            continue
        if needle.startswith("@"):
            hit = needle in low
        else:
            hit = re.search(rf"(?<![a-z0-9_.-]){re.escape(needle)}(?![a-z0-9_.-])", low) is not None
        if hit:
            found.append(needle)
    return sorted(set(found))


def _status_risk(status: str) -> bool:
    return bool(re.search(r"风险接受|已知未修|accepted|不加拒跑门", status, re.IGNORECASE))


def _ecosystem(text: str) -> str:
    low = text.lower()
    if "console" in low or "apps/console" in low:
        return "console"
    if "frontend" in low or "前端" in low:
        return "frontend"
    if "后端" in text or "backend" in low:
        return "backend"
    if "sdk" in low:
        return "sdk"
    return "any"


def _make_gap(
    *,
    key: str,
    title: str,
    status: str,
    evidence: str,
    suggestion: str,
    source_text: str,
    line_no: int,
    package_names: Sequence[str],
    derive_packages: bool = True,
    severity_text: Optional[str] = None,
) -> Gap:
    title = _clean(title)
    title = re.split(r"（原实现|（原", title)[0].strip()
    if len(title) > 150:
        title = title[:147] + "…"
    full_status = _clean(status)
    status = full_status
    severity = _severity_from_text(f"{title} {status} {source_text if severity_text is None else severity_text}")
    if re.search(r"决定不做|决定延后|deferred|design-only|不做", status, re.IGNORECASE) and "部分" not in status:
        severity = "minor"
    # 「已修/已完成」但仍有未验证残留的，不能按 blocker 处理；残留本身仍是缺口。
    if re.search(r"^(已修|已完成|已关闭|已更新)", status) and re.search(r"未验证|未确认|未实测|剩余", status):
        if severity == "blocker":
            severity = "major"
    # 代码已修、只剩「真实安装包/真机」复测的，归到 minor；这类复测已由安装器/真机条目覆盖。
    if re.search(r"^(已修|已完成|已关闭)", status) and re.search(
        r"未在真实|未在安装包|仅在.*(?:验证|沙箱)|仅在.*复测|未在.*复测", status
    ):
        severity = "minor"
    risk = _status_risk(full_status)
    paths = _paths_from(f"{title} {source_text}")
    keys = [key]
    if derive_packages:
        ecosystem = _ecosystem(f"{title} {status} {source_text}")
        for package in _packages_in(f"{title} {status} {source_text}", package_names):
            keys.append(f"dep:{ecosystem}:{package}")
    auto, reason = auto_eligibility("known-gaps", paths, f"{title} {status}")
    evidence_text = (
        f"{rel(Path(__file__).resolve().parents[2] / 'docs/known-gaps.md')}:{line_no}: "
        f"{title} | 状态={full_status[:400]}"
    )
    if evidence:
        evidence_text += f"\n{evidence[:400]}"
    return Gap(
        source="known-gaps",
        title=title,
        severity=severity,
        evidence=evidence_text,
        suggested_direction=_clean(suggestion)[:400] if suggestion else _fallback_direction(status, risk),
        auto_eligible=auto,
        reason=reason,
        merge_key=key,
        merge_keys=keys,
        paths=paths,
        status=status,
        risk_accepted=risk,
    )


def _fallback_direction(status: str, risk: bool) -> str:
    if risk:
        return "该条已登记为已知未修/风险接受；只有出现真实案例或触发条件时才修，先把触发条件写进台账。"
    if "安全" in status:
        return "先对齐两套安全校验口径，补针对性回归，再决定是否放宽拦截。"
    if re.search(r"未验证|未实测|未接入|未做", status):
        return "按关闭标准补真实环境验证或接线，保留可复现证据后再改代码。"
    return "先复核证据与关闭标准，再做最小改动并补回归。"


def collect_known_gaps(path: Path, package_names: Sequence[str]) -> Tuple[List[Gap], Dict[str, Any]]:
    meta: Dict[str, Any] = {
        "path": rel(path),
        "status": "SKIP",
        "tables_seen": 0,
        "tables_skipped": [],
        "sections_read": 0,
        "rows_read": 0,
    }
    if not path.is_file():
        meta["note"] = "known-gaps.md 不存在"
        return [], meta
    text = read_text(path)
    lines = text.splitlines()
    gaps: List[Gap] = []

    # 1) 带「状态：...」的独立章节（例如「已知未修」小节）
    for idx, line in enumerate(lines):
        if not line.startswith("## ") or line.startswith("### "):
            continue
        status_match = STATUS_IN_HEADING_RE.search(line)
        if not status_match:
            continue
        status = _clean(status_match.group(1))
        if not _is_open_status(status) or not OPEN_HEADING_RE.search(status):
            continue
        if status.startswith("已核实") and "未" not in status:
            continue
        heading = _clean(line.lstrip("#").strip())
        title = re.sub(r"（[^）]*状态[：:][^）]*）", "", heading).strip()
        title = re.sub(r"\*\*.*$", "", title).strip() or heading
        excerpt, body_lines = _body(lines, idx + 1)
        suggestion = _close_direction(body_lines)
        gaps.append(
            _make_gap(
                key=_heading_key(heading),
                title=title,
                status=status,
                evidence=f"正文摘要: {excerpt[:350]}",
                suggestion=suggestion,
                source_text=f"{excerpt} {suggestion}",
                line_no=idx + 1,
                package_names=package_names,
                severity_text=status,
            )
        )
        meta["sections_read"] += 1

    # 2) 表格行
    for table in iter_tables(text):
        meta["tables_seen"] += 1
        section = table.section
        header = [_clean(c) for c in table.header]
        status_idx = next(
            (i for i, cell in enumerate(header) if "状态" in cell or cell.lower() == "status"),
            None,
        )
        if "逐条落地状态" in section or "四类归属统计" in section:
            meta["tables_skipped"].append({"line": table.start_line, "section": section, "reason": "战略/统计状态表，不是缺口登记"})
            continue
        if "缺口登记" in section:
            for row in table.rows:
                token = _first_token(row.cells[0]) if row.cells else ""
                if not token or not token.startswith("G-"):
                    continue
                title = row.cells[1] if len(row.cells) > 1 else row.cells[0]
                open_reason = row.cells[2] if len(row.cells) > 2 else ""
                close = row.cells[3] if len(row.cells) > 3 else ""
                gaps.append(
                    _make_gap(
                        key=f"known:{token}",
                        title=title,
                        status="仍然开着（缺口登记）",
                        evidence="",
                        suggestion=close,
                        source_text=f"{open_reason} {close}",
                        line_no=row.line_no,
                        package_names=package_names,
                    )
                )
                meta["rows_read"] += 1
            continue
        if "看起来有、实际不生效" in section:
            for row in table.rows:
                token = _first_token(row.cells[0]) if row.cells else ""
                if not token or not token.startswith("R-"):
                    continue
                joined = " | ".join(row.cells)
                if "~~" in joined or any(marker in joined for marker in ("已解除", "已修", "已关闭")):
                    continue
                title = row.cells[1] if len(row.cells) > 1 else row.cells[0]
                truth = row.cells[2] if len(row.cells) > 2 else ""
                proof = row.cells[3] if len(row.cells) > 3 else ""
                gaps.append(
                    _make_gap(
                        key=f"known:{token}",
                        title=f"{title} → {truth}" if truth else title,
                        status="看起来有、实际不生效",
                        evidence=f"真相/证据: {truth} {proof}"[:350],
                        suggestion="确认该功能是否应接入运行时；要接入就补接线与回归，不打算接入就从对外描述中移除。",
                        source_text=f"{truth} {proof}",
                        line_no=row.line_no,
                        package_names=package_names,
                    )
                )
                meta["rows_read"] += 1
            continue
        if status_idx is None:
            continue
        suggestion_idx = next(
            (
                i for i, cell in enumerate(header)
                if any(marker in cell for marker in ("还差什么", "关掉标准", "修复方向", "怎么写", "写法"))
            ),
            None,
        )
        for row in table.rows:
            if not row.cells:
                continue
            joined = " | ".join(row.cells)
            if "~~" in joined:
                continue
            status = _clean(row.cells[status_idx]) if status_idx < len(row.cells) else ""
            if not _is_open_status(status):
                continue
            token = _first_token(row.cells[0])
            # 有些表没有编号列：缺口在 0 列、状态在 1 列，不能把状态当标题。
            if status_idx == 0:
                title_value = row.cells[1] if len(row.cells) > 1 else row.cells[0]
            elif status_idx == 1:
                title_value = row.cells[0]
            elif len(row.cells) > 1:
                title_value = row.cells[1]
            else:
                title_value = row.cells[0]
            key = f"known:{token}" if token else f"known:row:{_slug(joined)}"
            if suggestion_idx is not None and suggestion_idx < len(row.cells):
                suggestion = row.cells[suggestion_idx]
            elif status_idx == 1 and len(row.cells) > 2:
                suggestion = row.cells[-1]
            else:
                suggestion = ""
            gaps.append(
                _make_gap(
                    key=key,
                    title=title_value,
                    status=status,
                    evidence=f"行内容: {joined[:350]}",
                    suggestion=suggestion,
                    source_text=joined,
                    line_no=row.line_no,
                    package_names=package_names,
                )
            )
            meta["rows_read"] += 1

    meta["status"] = "PASS" if not gaps else "FAIL"
    return gaps, meta
