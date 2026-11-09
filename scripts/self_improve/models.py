"""缺口数据模型、严重度规则、敏感区判定与合并逻辑。

规则在这里写死；扫描器只负责采集证据，不参与「这条严不严重」的判断。
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

SEVERITIES: Tuple[str, ...] = ("blocker", "major", "minor")
_RANK = {name: idx for idx, name in enumerate(SEVERITIES)}

#: 命中这些路径前缀的缺口一律不可自动修（用户指定 + 数据库迁移）。
SENSITIVE_PATH_PREFIXES: Tuple[str, ...] = (
    "src/security/",
    "src/gateway/policy.py",
    "installer/",
    "packaging/",
    "scripts/release/",
    "dist/release-gate/",
    "migrations/",
    "alembic/",
    "init-scripts/",
)

#: 依赖清单 / lock 文件：改它们会改变交付物，不允许无人值守自动改。
SENSITIVE_FILENAMES: Tuple[str, ...] = (
    "requirements.txt",
    "requirements-dev.txt",
    "package.json",
    "package-lock.json",
    "uv.lock",
    "poetry.lock",
    "Pipfile.lock",
    "pnpm-lock.yaml",
    "yarn.lock",
    "Cargo.lock",
    "go.sum",
    "pyproject.toml",
)

#: 内容命中即视为凭据/密钥相关；宁可误判为需人工，也不让自动线碰。
SENSITIVE_CONTENT_RE = re.compile(
    r"(密钥|凭据|私钥|口令|泄露|"
    r"secret|credential|password|passwd|api[_-]?key|private[_-]?key|"
    r"jwt|bearer|access[_-]?token|refresh[_-]?token)",
    re.IGNORECASE,
)

#: 「非核心区」白名单：这些路径里的 TODO/FIXME 才允许自动线处理。
AUTO_TODO_PREFIXES: Tuple[str, ...] = (
    "docs/",
    "tests/",
    "apps/console/",
    "examples/",
    "solutions/",
    "sdk/",
    "oss-ecosystem/",
)

#: 报告里 primary 来源的优先级：人工登记 > 安全 > SBOM > 测试 > 文档门禁 > 打包 > TODO。
SOURCE_PRIORITY: Tuple[str, ...] = (
    "known-gaps",
    "security",
    "sbom",
    "pytest",
    "docs-links",
    "docs-index",
    "packaging",
    "todo",
)

_ALWAYS_HUMAN_SOURCES = frozenset({"known-gaps", "security", "sbom", "pytest", "packaging"})
_DOC_AUTO_SOURCES = frozenset({"docs-links", "docs-index"})
_AUTO_SOURCES = frozenset({"todo"}) | _DOC_AUTO_SOURCES


def severity_rank(value: str) -> int:
    return _RANK.get(value, len(SEVERITIES))


def downgrade(value: str, levels: int = 1) -> str:
    """降一级，但不允许降到 minor 以下——「风险接受」不等于消失。"""
    idx = min(severity_rank(value) + max(1, levels), len(SEVERITIES) - 1)
    return SEVERITIES[idx]


def worse(a: str, b: str) -> str:
    return a if severity_rank(a) <= severity_rank(b) else b


def _norm_path(path: str) -> str:
    return path.replace("\\", "/").strip().lstrip("./")


def path_is_sensitive(path: str) -> bool:
    p = _norm_path(path)
    if not p:
        return False
    if p.endswith(SENSITIVE_FILENAMES):
        return True
    base = p.rsplit("/", 1)[-1]
    if base.endswith(".lock") or base in SENSITIVE_FILENAMES:
        return True
    return any(p.startswith(prefix) for prefix in SENSITIVE_PATH_PREFIXES)


def content_is_sensitive(text: str) -> bool:
    return bool(text and SENSITIVE_CONTENT_RE.search(text))


def auto_eligibility(source: str, paths: Sequence[str], text: str) -> Tuple[bool, str]:
    """返回 (auto_eligible, reason)。规则顺序固定，便于复现。"""
    if source in _ALWAYS_HUMAN_SOURCES:
        return False, "该来源属于人工判断/核心交付面，不允许无人值守自动修改"
    sensitive_path = next((p for p in paths if path_is_sensitive(p)), None)
    if sensitive_path:
        return False, f"命中敏感路径 {sensitive_path}，必须人工处理"
    if content_is_sensitive(text):
        return False, "命中密钥/凭据/令牌相关内容，必须人工处理"
    if source in _DOC_AUTO_SOURCES:
        return True, "纯文档修正，可在非核心区自动处理"
    if source in _AUTO_SOURCES:
        todo_path = next((p for p in paths if p), None)
        if todo_path and any(_norm_path(todo_path).startswith(pref) for pref in AUTO_TODO_PREFIXES):
            return True, "TODO/FIXME 位于非核心区，可自动整理"
        return False, "TODO/FIXME 位于核心区或路径不明确，需人工判断"
    return False, "来源不在自动修复白名单内"


@dataclass
class Gap:
    """一条缺口。merge_keys 支持跨来源合并（例如同一依赖包出现在两份报告里）。"""

    source: str
    title: str
    severity: str
    evidence: str
    suggested_direction: str
    auto_eligible: bool
    reason: str
    merge_key: str
    paths: List[str] = field(default_factory=list)
    merge_keys: List[str] = field(default_factory=list)
    status: str = ""
    risk_accepted: bool = False
    suspected_parallel: bool = False
    extra: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.merge_keys:
            self.merge_keys = [self.merge_key]
        if not self.paths:
            self.paths = []

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.extra.get("id", ""),
            "source": self.source,
            "sources": self.extra.get("sources", [self.source]),
            "severity": self.severity,
            "title": self.title,
            "evidence": self.evidence,
            "suggested_direction": self.suggested_direction,
            "auto_eligible": self.auto_eligible,
            "reason": self.reason,
            "status": self.status,
            "risk_accepted": self.risk_accepted,
            "suspected_parallel": self.suspected_parallel,
            "paths": self.paths,
            "merge_keys": self.merge_keys,
        }


def _stable_id(primary_key: str, source: str) -> str:
    digest = hashlib.sha1(primary_key.encode("utf-8")).hexdigest()[:8]
    return f"gap-{source}-{digest}"


def _source_rank(source: str) -> int:
    try:
        return SOURCE_PRIORITY.index(source)
    except ValueError:
        return len(SOURCE_PRIORITY)


def merge_gaps(gaps: Sequence[Gap]) -> List[Gap]:
    """按 merge_keys 做并查集合并；合并后仍保留所有来源与证据。"""
    parent: List[int] = list(range(len(gaps)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    key_owner: Dict[str, int] = {}
    for idx, gap in enumerate(gaps):
        for key in gap.merge_keys:
            if key in key_owner:
                union(key_owner[key], idx)
            else:
                key_owner[key] = idx

    # 同一份 known-gaps 里，不同编号（E-21 / G-10）描述的是同一条缺口时也合并。
    def norm_title(title: str) -> str:
        title = re.sub(r"^\s*(D|E|G|R|F)-\d+\s*", "", title)
        return re.sub(r"[^\w\u4e00-\u9fff]+", "", title).lower()

    known_indices = [i for i, gap in enumerate(gaps) if gap.source == "known-gaps"]
    for pos, i in enumerate(known_indices):
        a = norm_title(gaps[i].title)
        if len(a) < 5:
            continue
        for j in known_indices[pos + 1 :]:
            b = norm_title(gaps[j].title)
            if len(b) < 5:
                continue
            if a in b or b in a:
                union(i, j)

    groups: Dict[int, List[Gap]] = {}
    for idx, gap in enumerate(gaps):
        groups.setdefault(find(idx), []).append(gap)

    merged: List[Gap] = []
    for members in groups.values():
        ordered = sorted(
            members,
            key=lambda g: (_source_rank(g.source), severity_rank(g.severity), len(g.title), g.title),
        )
        primary = ordered[0]
        sources = sorted({g.source for g in members}, key=_source_rank)
        keys = sorted({k for g in members for k in g.merge_keys})
        severity = primary.severity
        risk = False
        suspected = False
        auto = True
        reasons: List[str] = []
        evidence_parts: List[str] = []
        all_paths: List[str] = []
        for gap in ordered:
            severity = worse(severity, gap.severity)
            risk = risk or gap.risk_accepted
            suspected = suspected or gap.suspected_parallel
            auto = auto and gap.auto_eligible
            if gap.reason and gap.reason not in reasons:
                reasons.append(gap.reason)
            chunk = f"[{gap.source}] {gap.evidence}".strip()
            if chunk not in evidence_parts:
                evidence_parts.append(chunk)
            all_paths.extend(p for p in gap.paths if p not in all_paths)
        if risk:
            severity = downgrade(severity, 1)

        if len(ordered) == 1:
            title = primary.title
        else:
            # 合并后优先用最简短、可读的标题（例如「E-14 手机真机登录」而不是整段正文）。
            title_candidates = [
                g.title
                for g in members
                if g.source == primary.source
                and len(g.title) > 5
                and not re.match(r"^(仍开放|部分|已修|已完成|已知未修)", g.title)
            ]
            title = min(title_candidates, key=len) if title_candidates else primary.title
            others = [g.title for g in ordered if g.title != title]
            if others:
                title = f"{title}（另见 {len(others)} 条同源记录）"

        generic_suggestion = ("先复核该条目的证据", "先确认该条目的关闭标准")
        suggestion = next(
            (
                g.suggested_direction
                for g in ordered
                if g.suggested_direction and not g.suggested_direction.startswith(generic_suggestion)
            ),
            primary.suggested_direction,
        )

        primary_key = keys[0] if keys else primary.merge_key
        merged_gap = Gap(
            source=primary.source,
            title=title,
            severity=severity,
            evidence="\n".join(evidence_parts),
            suggested_direction=suggestion,
            auto_eligible=auto,
            reason="；".join(reasons),
            merge_key=primary_key,
            paths=all_paths,
            merge_keys=keys,
            status=primary.status,
            risk_accepted=risk,
            suspected_parallel=suspected,
            extra={
                "id": _stable_id("|".join(keys) if keys else primary.merge_key, primary.source),
                "sources": sources,
                "merged_count": len(ordered),
            },
        )
        merged.append(merged_gap)

    merged.sort(
        key=lambda g: (
            1 if g.suspected_parallel else 0,
            severity_rank(g.severity),
            _source_rank(g.source),
            g.title,
        )
    )
    return merged


def distribution(gaps: Iterable[Gap]) -> Dict[str, int]:
    out = {name: 0 for name in SEVERITIES}
    for gap in gaps:
        out[gap.severity] = out.get(gap.severity, 0) + 1
    return out
