"""能力缺口对照：**唯一对照源**是 ``docs/capability-map.md``。

规则（按用户要求，不允许凭候选仓库 README 猜本仓能力）：

* 只解析该 Markdown 的层级标题与表格行；
* 状态严格四态：🟢 真接线 / 🟡 半成品 / 🔴 假实现 / ⚫ 完全没有；
* 证据只取表格里已经写明的文件路径（关 2/关 3/关 4 三列）；
* 每个候选都落全量对照；报告只展示与候选主题相关的那几行，并注明来源。

找不到能力地图时直接抛 ``FileNotFoundError``，不退回关键词扫描，也不编造。
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from src.intel.models import Candidate, GapRecord, GapStatus

CAPABILITY_MAP_RELATIVE = "docs/capability-map.md"

_STATE_MAP = (
    ("🟢", GapStatus.EXISTING, "真接线"),
    ("🟡", GapStatus.PARTIAL, "半成品"),
    ("🔴", GapStatus.FAKE, "假实现"),
    ("⚫", GapStatus.MISSING, "完全没有"),
)

_ROW_ID_RE = re.compile(r"^[0-9]+(?:\.[0-9]+)?$")
_LAYER_RE = re.compile(r"^##\s+(?P<number>[0-9]+)\.\s*(?P<title>.+?)\s*$")
_PATH_RE = re.compile(
    r"(?<![A-Za-z0-9])"
    r"(?P<path>(?:src|apps|scripts|docs|plugins|installer|packaging)/[A-Za-z0-9_./-]+)"
)
_ENGLISH_TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9._-]{2,}")
_CHINESE_TOKEN_RE = re.compile(r"[\u4e00-\u9fff]{2,}")
_MAX_EVIDENCE_CHARS = 600

#: 泛化词不参与主题匹配，否则 ``http``/``python``/``for`` 会命中几乎所有行。
_STOPWORDS = frozenset(
    {
        "a",
        "an",
        "and",
        "api",
        "apis",
        "app",
        "apps",
        "are",
        "async",
        "asyncio",
        "based",
        "built",
        "but",
        "can",
        "client",
        "code",
        "data",
        "easy",
        "fast",
        "file",
        "files",
        "for",
        "framework",
        "frameworks",
        "from",
        "generation",
        "has",
        "have",
        "http",
        "https",
        "into",
        "javascript",
        "library",
        "libraries",
        "model",
        "models",
        "new",
        "next",
        "not",
        "of",
        "on",
        "open",
        "over",
        "platform",
        "platforms",
        "project",
        "projects",
        "python",
        "server",
        "simple",
        "source",
        "support",
        "supports",
        "system",
        "systems",
        "test",
        "tests",
        "than",
        "that",
        "the",
        "then",
        "this",
        "to",
        "tool",
        "tools",
        "typescript",
        "under",
        "use",
        "used",
        "using",
        "via",
        "was",
        "web",
        "were",
        "will",
        "with",
        "you",
        "your",
    }
)


@dataclass(frozen=True)
class CapabilityRow:
    """能力地图里的一行。"""

    row_id: str
    layer: str
    title: str
    state: GapStatus
    state_label: str
    evidence: str
    file_paths: tuple[str, ...]


def _split_cells(line: str) -> list[str]:
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def _extract_paths(text: str) -> tuple[str, ...]:
    paths: list[str] = []
    for match in _PATH_RE.finditer(text):
        value = match.group("path").rstrip(".,;:)")
        if value and value not in paths:
            paths.append(value)
    return tuple(paths)


def parse_capability_map(text: str) -> list[CapabilityRow]:
    """解析能力地图；无法识别的行直接跳过，不猜测状态。"""
    rows: list[CapabilityRow] = []
    current_layer = "未分层"

    for raw_line in text.splitlines():
        line = raw_line.strip()
        layer_match = _LAYER_RE.match(line)
        if layer_match:
            current_layer = f"{layer_match.group('number')} {layer_match.group('title')}"
            continue
        if not line.startswith("|") or not line.endswith("|"):
            continue

        cells = _split_cells(line)
        if len(cells) < 3 or not _ROW_ID_RE.match(cells[0]):
            continue

        state: GapStatus | None = None
        state_label = ""
        for icon, status, label in _STATE_MAP:
            if icon in cells[2]:
                state = status
                state_label = label
                break
        if state is None:
            continue

        evidence = " | ".join(cell for cell in cells[3:] if cell)
        rows.append(
            CapabilityRow(
                row_id=cells[0],
                layer=current_layer,
                title=cells[1],
                state=state,
                state_label=state_label,
                evidence=evidence,
                file_paths=_extract_paths(evidence),
            )
        )
    return rows


def load_capability_map(path: Path | str) -> list[CapabilityRow]:
    """读取并解析能力地图；文件不存在时明确报错。"""
    map_path = Path(path)
    if not map_path.is_file():
        raise FileNotFoundError(
            f"能力对照唯一来源不存在：{map_path}；不会退回关键词扫描或凭 README 猜测"
        )
    rows = parse_capability_map(map_path.read_text(encoding="utf-8"))
    if not rows:
        raise ValueError(f"能力地图 {map_path} 中没有可解析的能力行")
    return rows


def _candidate_tokens(candidate: Candidate) -> set[str]:
    haystack = " ".join(
        [candidate.name or "", candidate.description or "", *candidate.topics]
    ).lower()
    tokens = {
        match.group(0).strip("._-")
        for match in _ENGLISH_TOKEN_RE.finditer(haystack)
        if match.group(0).strip("._-") not in _STOPWORDS
    }
    tokens.update(
        token
        for token in _CHINESE_TOKEN_RE.findall(haystack)
        if token not in _STOPWORDS
    )
    return {token for token in tokens if token}


def _row_tokens(row: CapabilityRow) -> set[str]:
    """只取标题、层级与证据路径，不把整段说明里的 URL/泛化词当匹配依据。"""
    haystack = f"{row.layer} {row.title}".lower()
    tokens = {match.group(0) for match in _ENGLISH_TOKEN_RE.finditer(haystack)}
    tokens.update(_CHINESE_TOKEN_RE.findall(haystack))
    # 文件路径按 / _ . - 切开，避免把 agent_runtime.py 当成一个整词。
    for part in re.split(r"[^a-z0-9]+", " ".join(row.file_paths).lower()):
        if len(part) >= 3:
            tokens.add(part)
    return tokens


def select_relevant_rows(
    candidate: Candidate, rows: Sequence[CapabilityRow]
) -> list[CapabilityRow]:
    """按候选 topics/描述/名称挑相关能力行。

    匹配对象是能力地图自己的标题、层级与证据路径，不读候选仓库 README。
    一个都匹配不上时返回空列表：报告会明确写「未在能力地图中定位到对应
    能力」，而不是拿泛化词硬凑出「缺失/半成品」再据此提建议。
    """
    tokens = _candidate_tokens(candidate)
    if not tokens:
        return []
    return [row for row in rows if tokens & _row_tokens(row)]


def build_gap_records(
    candidate: Candidate,
    rows: Sequence[CapabilityRow],
    assessed_at: str,
    *,
    map_path: str = CAPABILITY_MAP_RELATIVE,
) -> list[GapRecord]:
    """把一个候选与能力地图的对照写成 ``osi_gap_analysis`` 记录。

    全量行都落库（便于追溯），``relevant`` 标记只影响报告的展示与建议策略。
    """
    relevant_ids = {row.row_id for row in select_relevant_rows(candidate, rows)}
    topic_note = "主题匹配：是" if relevant_ids else "主题匹配：否（能力地图无对应项）"
    records: list[GapRecord] = []
    for row in rows:
        evidence = row.evidence[:_MAX_EVIDENCE_CHARS] or "-"
        records.append(
            GapRecord(
                repo_url=candidate.repo_url,
                capability=f"{row.row_id} {row.title}",
                status=row.state.value,
                file_paths=list(row.file_paths),
                assessed_at=assessed_at,
                note=(
                    f"唯一对照源：{map_path}；层：{row.layer}；"
                    f"状态：{row.state_label}；{topic_note}；证据：{evidence}"
                ),
                relevant=row.row_id in relevant_ids,
            )
        )
    return records


__all__ = [
    "CAPABILITY_MAP_RELATIVE",
    "CapabilityRow",
    "build_gap_records",
    "load_capability_map",
    "parse_capability_map",
    "select_relevant_rows",
]
