"""能力缺口分析：候选仓库 → 鎏灏现有能力的**粗粒度**对照。

判定口径（必须如实写进报告，不能包装成语义级判断）：

* 在 ``src/``、``apps/console/src``、``frontend/src``、``scripts/`` 中按
  **文件名 + 文本关键词**匹配；
* 命中文件 >= 3 记为「已有」，1-2 个记为「半成品」，0 个记为「缺失」；
* 列出命中文件的真实相对路径作为证据。

这是只读分析：不修改任何被扫描的文件。
"""

from __future__ import annotations

import os
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

from src.intel.models import Candidate, GapStatus

_SCAN_ROOTS: Sequence[str] = ("src", "apps/console/src", "frontend/src", "scripts")
_TEXT_SUFFIXES = frozenset({".py", ".ts", ".tsx", ".js", ".jsx", ".md"})
_SKIP_PARTS = frozenset(
    {"__pycache__", "node_modules", ".venv", "venv", "dist", "build", ".git"}
)

_EXISTING_MIN_FILES = 3
_PARTIAL_MIN_FILES = 1
_MAX_FILE_BYTES = 200_000
_MAX_FILES = 3000
_MAX_EVIDENCE_FILES = 5


@dataclass(frozen=True)
class Capability:
    key: str
    label: str
    keywords: tuple[str, ...]


#: 与 Y1.0 蓝图对齐的能力清单；关键词刻意保持宽泛，便于粗对照。
CAPABILITIES: Sequence[Capability] = (
    Capability("agent-orchestration", "AI Agent 编排", ("agent", "orchestrat", "workflow", "planner")),
    Capability("memory-retrieval", "记忆与检索", ("memory", "retriev", "embedding", "vector")),
    Capability("knowledge-rag", "知识与 RAG", ("knowledge", "rag", "chunk")),
    Capability(
        "security-compliance",
        "安全与合规",
        ("security", "permission", "rbac", "compliance", "audit"),
    ),
    Capability(
        "observability-metrics",
        "可观测与指标",
        ("observ", "metric", "trace", "telemetry"),
    ),
    Capability("sandbox-plugin", "沙箱与插件", ("sandbox", "plugin", "restricted")),
    Capability(
        "deployment-distribution",
        "部署与分发",
        ("deploy", "distribution", "container", "docker"),
    ),
    Capability("data-pipeline", "数据管道", ("pipeline", "ingest", "etl")),
)


@dataclass
class CapabilityFinding:
    capability: str
    status: GapStatus
    files: list[str]
    scanned_files: int
    truncated: bool


def _iter_text_files(base: Path, budget: list[int]) -> Iterable[tuple[Path, str]]:
    """在当前根目录下遍历可读文本文件；超过总文件预算即停止。"""
    if not base.is_dir():
        return
    for current_root, dirnames, filenames in os.walk(base):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_PARTS]
        for filename in filenames:
            if budget[0] >= _MAX_FILES:
                return
            path = Path(current_root) / filename
            if path.suffix.lower() not in _TEXT_SUFFIXES:
                continue
            try:
                if path.stat().st_size > _MAX_FILE_BYTES:
                    continue
                text = path.read_text(encoding="utf-8", errors="replace").lower()
            except OSError:
                continue
            budget[0] += 1
            yield path, text


def analyze_capabilities(repo_root: Path | str) -> list[CapabilityFinding]:
    """扫描本仓库现状，返回每个能力的粗粒度状态与证据文件。"""
    root = Path(repo_root)
    budget = [0]
    truncated = False
    matches: dict[str, list[str]] = {cap.key: [] for cap in CAPABILITIES}

    for root_name in _SCAN_ROOTS:
        base = root / root_name
        for path, text in _iter_text_files(base, budget):
            try:
                relative = path.relative_to(root).as_posix()
            except ValueError:
                relative = path.as_posix()
            haystack = f"{relative.lower()}\n{text}"
            for cap in CAPABILITIES:
                if len(matches[cap.key]) >= _MAX_EVIDENCE_FILES:
                    continue
                if any(keyword in haystack for keyword in cap.keywords):
                    matches[cap.key].append(relative)
        if budget[0] >= _MAX_FILES:
            truncated = True
            break

    findings: list[CapabilityFinding] = []
    for cap in CAPABILITIES:
        files = matches[cap.key]
        if len(files) >= _EXISTING_MIN_FILES:
            status = GapStatus.EXISTING
        elif len(files) >= _PARTIAL_MIN_FILES:
            status = GapStatus.PARTIAL
        else:
            status = GapStatus.MISSING
        findings.append(
            CapabilityFinding(
                capability=cap.key,
                status=status,
                files=files,
                scanned_files=budget[0],
                truncated=truncated,
            )
        )
    return findings


def select_capabilities(candidate: Candidate) -> list[Capability]:
    """按候选的 topics/描述/名称挑选相关能力；识别不出时退回全量粗对照。"""
    haystack = " ".join(
        [
            candidate.name or "",
            candidate.description or "",
            " ".join(candidate.topics or []),
        ]
    ).lower()
    matched = [
        cap for cap in CAPABILITIES if any(keyword in haystack for keyword in cap.keywords)
    ]
    return matched if matched else list(CAPABILITIES)
