"""oss_radar 自测 —— 只测不依赖网络的逻辑，保证 CI 稳定。

真实 API 的可达性由 `python pipeline/oss_radar.py --probe` 人工复核，
**不写进自动化测试**：外部源随时可能限流或改版，会让 CI 红灯归因跑偏。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]  # tests/ -> 仓库根
sys.path.insert(0, str(ROOT / "oss-ecosystem" / "pipeline"))

import oss_radar as radar  # noqa: E402
import yaml  # noqa: E402

SOURCES = ROOT / "oss-ecosystem" / "sources.yaml"
INTENTS = ROOT / "oss-ecosystem" / "intents.yaml"
GENE_MAP = ROOT / "oss-ecosystem" / "genebank" / "GENE-MAP.yaml"
SCHEMA = ROOT / "oss-ecosystem" / "schema" / "capability-entry.schema.yaml"
CAP_DIR = ROOT / "oss-ecosystem" / "capabilities"


# --------------------------------------------------------------- yaml 可加载
def test_sources_and_intents_load():
    src = radar.load_sources()
    ints = radar.load_intents()
    assert len(src) >= 10, "信号源数量异常"
    assert len(ints) == 72, f"覆盖蓝图应为 72 类，实际 {len(ints)}"


def test_intents_ids_unique_and_complete():
    # YAML 1.1 会把 `08`/`09` 解析成字符串（非法八进制），其余为整数 —— 类型是混合的。
    # 使用侧一律按字符串处理，因此这里也统一余零后比较。
    ids = [str(i["id"]).zfill(2) for i in radar.load_intents()]
    assert len(ids) == len(set(ids)), "存在重复的 intent id"
    assert sorted(ids) == [str(n).zfill(2) for n in range(1, 73)], \
        "intent id 必须完整覆盖 1..72"


# --------------------------------------------------- 交叉引用：不许悬空引用
def test_intent_sources_exist():
    """intents.yaml 里写的源必须在 sources.yaml 中存在，否则扫描时会静默跳过。"""
    src = radar.load_sources()
    bad = [(i["id"], s) for i in radar.load_intents()
           for s in (i.get("sources") or []) if s not in src]
    assert not bad, f"引用了不存在的源：{bad}"


def test_adapters_cover_declared_sources():
    """sources.yaml 声明了 discover 的源必须有适配器 —— 否则永远报 N/A 却不自知。"""
    missing = [s["id"] for s in radar.load_sources().values()
               if s.get("discover") and s["id"] not in radar.ADAPTERS
               and s.get("auth") != "required"]
    assert not missing, f"有 discover 却缺 Python 适配器：{missing}"


# ----------------------------------------------------------- 字段值与约定
def test_priority_values_valid():
    ok = {"P0", "P1", "P2"}
    bad = [(i["id"], i.get("priority")) for i in radar.load_intents()
           if i.get("priority") not in ok]
    assert not bad, f"priority 取值非法：{bad}"


def test_kind_values_valid():
    ok = {"platform", "topic", "horizon"}
    bad = [(i["id"], i.get("kind")) for i in radar.load_intents() if i.get("kind") not in ok]
    assert not bad, f"kind 取值非法：{bad}"


def test_gene_slugs_resolve():
    """自己用到的 gene slug 必须在 GENE-MAP 里定义过，否则断言落不到.callee。"""
    genes = {g["slug"] for g in yaml.safe_load(GENE_MAP.read_text(encoding="utf-8"))["genebank"]}
    bad = [(i["id"], g) for i in radar.load_intents() for g in (i.get("gene") or []) if g not in genes]
    assert not bad, f"引用了未定义的 gene slug：{bad}"


def test_genebank_has_twelve_genes():
    data = yaml.safe_load(GENE_MAP.read_text(encoding="utf-8"))
    assert len(data["genebank"]) == 12, "基因库应为 12 类"


def test_full_scan_sources_have_adapters():
    """全量型源若缺适配器，扫描会恒产出 0 条却毫无报错 —— 必须挡住。"""
    missing = [s for s in radar.FULL_SCAN_SOURCES if s not in radar.ADAPTERS]
    assert not missing, f"全量型源缺适配器：{missing}"


def test_render_substitutes_query():
    assert radar.render("https://x/{query}?q={query}", "abc") == "https://x/abc?q=abc"


def test_probe_query_is_nonempty():
    """空查询词会让 GitHub 返回 422、npm 返回 400。这是已经踩过的坑。"""
    assert radar.PROBE_QUERY.strip(), "探活查询词不得为空"


# ------------------------------------------------------- 能力条目完整性校验
def _load_capability_files():
    if not CAP_DIR.exists():
        pytest.skip("capabilities 目录尚不存在")
    return sorted(CAP_DIR.glob("*.yaml"))


def test_capability_entries_have_required_fields():
    schema = yaml.safe_load(SCHEMA.read_text(encoding="utf-8"))
    required = schema["required_fields"]
    ids_seen: set[str] = set()

    for f in _load_capability_files():
        for entry in yaml.safe_load(f.read_text(encoding="utf-8")).get("entries", []):
            missing = [k for k in required if k not in entry]
            assert not missing, f"{f.name}:{entry.get('id')} 缺必填字段 {missing}"

            eid = entry["id"]
            assert eid not in ids_seen, f"能力条目 id 重复：{eid}"
            ids_seen.add(eid)


def test_capability_entries_valid_enums():
    schema = yaml.safe_load(SCHEMA.read_text(encoding="utf-8"))
    decisions = set(schema["decision_legend"].keys())

    for f in _load_capability_files():
        for entry in yaml.safe_load(f.read_text(encoding="utf-8")).get("entries", []):
            d = entry.get("adoption_decision")
            assert d in decisions, f"{f.name}:{entry.get('id')} adoption_decision={d} 不在 {decisions}"
            assert entry.get("enhances_liuhao") in {"yes", "partial", "no"}, \
                f"{f.name}:{entry.get('id')} enhances_liuhao 取值非法"
            assert entry.get("needs_refactor") in {"none", "light", "heavy"}, \
                f"{f.name}:{entry.get('id')} needs_refactor 取值非法"


def test_reject_and_adopt_have_rationale():
    """拒绝/采纳都必须写理由 —— 没有理由的决定等于没做决定。"""
    for f in _load_capability_files():
        for entry in yaml.safe_load(f.read_text(encoding="utf-8")).get("entries", []):
            if entry.get("adoption_decision") in {"reject", "adopt"}:
                note = (entry.get("decision_note") or "").strip()
                assert len(note) > 20, f"{f.name}:{entry.get('id')} 缺充分理由"


def test_no_placeholder_tokens_in_entries():
    """禁止占位符进入交付物。"""
    import re
    pat = re.compile(r"\b(TODO|TBD|FIXME|XXX-XXX|<placeholder>)\b")
    for f in _load_capability_files():
        for entry in yaml.safe_load(f.read_text(encoding="utf-8")).get("entries", []):
            for k, v in entry.items():
                if isinstance(v, str):
                    m = pat.search(v)
                    assert not m, f"{f.name}:{entry.get('id')} 字段 {k} 含占位符 {m.group(0)}"
