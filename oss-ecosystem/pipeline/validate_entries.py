#!/usr/bin/env python
"""校验 `oss-ecosystem/capabilities/*.yaml` 是否满足 `schema/capability-entry.schema.yaml` 的字段契约。

为什么需要它：`capability-entry.schema.yaml` 开头写着
「目的：让『每个发现的项目都必须分析』变成**可校验**的事」，并指名了本文件。
但在此之前本文件并不存在 —— 那份契约一直只是**散文**，没有任何东西在执行它。
代价已经出现：2026-09-15 的 tier2 批次里，5 条 `license` 被 GitHub API 记成 `NOASSERTION`
且 `commercial_risk` 写了 `medium`，直接违反 schema 第 21 行「非标准则原样字符串 + commercial_risk=high」，
而**没有任何检查发现**。这和 Bandit 的教训同源：工具在不在不重要，**跑不跑**才重要。

硬失败（每条都能回溯到 schema 原文，且不依赖任何"体例偏好"）：
  1. 每条记录必须齐备 `required_fields`（14 项）且非空；
  2. `id` 形如 LHX-OSS-#### 且**全库唯一**（跨文件）；
  3. `source_platform` 必须存在于 `sources.yaml` 的 id 集合；
  4. 枚举字段合法：`enhances_liuhao` ∈ yes|partial|no、`needs_refactor` 以 none|light|heavy 起头、
     `adoption_decision` ∈ 四值；
  5. `commercial_risk`、`security_risk` 必须以 low|medium|high 起头；
  6. `license` 必须可判定：命中 SPDX 清单（或 SPDX 表达式）即通过；
     **非标准则 `commercial_risk` 必须是 high**（schema 第 21 行）；
  7. `evidence` 必须引用至少一个**被 git 跟踪**的文件（`*.json` / `*.yaml` / `*.yml`）。
     本仓有两种合法的来源形态：某次扫描（`scan-*.json`）或某个源本身（`sources.yaml`），
     故只校验"引用的来源可被他人复核"，不规定它必须是哪一个。
     **刻意要求"被跟踪"而非"存在"**：`bandit-report.json` 这类本地产物在 `.gitignore` 里，
     在我机器上存在、在别人克隆里不存在 —— 用它当证据等于写了一句**别人无法证伪的话**；
  8. schema 点名要拦的**套话**（「提升效率」「业界领先」「值得吸收」…）一律判失败。

警告（打印但不阻断，因为属"体例差异"而非契约违规 —— 刻意不把它们变成假失败）：
  a. `commercial_risk` / `security_risk` 只写了裸等级。schema 写的是「等级 + 原因」，
     但既有"深度分析"体例一贯把原因写进 `decision_note`，本仓从未统一到字段内，
     故记为待收敛项而非违规；
  b. `verdict` 不在 adopt|evaluate|watch|reject 四值内。`triage-*.yaml` 用它表达候选决策，
     而 `s2-s5-landed.yaml` 用同一字段名表达**落地结果**（partially-absorbed / remediated…），
     属体例差异。

已知体例豁免（显式列出，不静默跳过）：
  `s2-s5-landed.yaml` 是「落地复记」体例 —— 记录**本仓自有代码与本轮已成事实**，
  不是第三方候选，故第 6 条对它不适用（其 license 字段本就写着「内部代码」）。

用法：
    .venv\\Scripts\\python.exe oss-ecosystem/pipeline/validate_entries.py

退出码：0 = 无硬失败（警告仍可能打印）；1 = 存在契约违规。
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
OSS_ROOT = REPO_ROOT / "oss-ecosystem"
CAP_DIR = OSS_ROOT / "capabilities"
SCHEMA = OSS_ROOT / "schema" / "capability-entry.schema.yaml"
SOURCES = OSS_ROOT / "sources.yaml"
SPDX_LIST = OSS_ROOT / "state" / "spdx-licenses.json"

# 第 6 条豁免：落地复记体例（记本仓自有代码与既成事实，非第三方候选）
LICENSE_RULE_EXEMPT = {"s2-s5-landed.yaml"}
# 体例差异：本文件用 `verdict` 表达落地结果，而非候选决策
VERDICT_OUTCOME_STYLE = {"s2-s5-landed.yaml"}

ID_RE = re.compile(r"^LHX-OSS-\d{4}$")
EVIDENCE_FILE_RE = re.compile(r"[\w.-]+\.(?:json|yaml|yml)")
SPDX_OPERATOR_RE = re.compile(r"\s+(?:AND|OR|WITH)\s+")

# schema 点名："『提升效率』『业界领先』『值得吸收』这类句子一律判为无效"
CLICHES = ("提升效率", "业界领先", "业内领先", "值得吸收", "大幅提升", "降本增效", "赋能")

ENHANCES = {"yes", "partial", "no"}
NEEDS_REFACTOR = ("none", "light", "heavy")
RISK_LEVELS = ("low", "medium", "high")
DECISIONS = {"adopt", "evaluate", "watch", "reject"}


def _load_yaml(path: Path):
    import yaml

    with path.open(encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def _normalise_bool(value):
    """YAML 1.1 会把裸写的 yes/no 解析成 bool —— 还原成语义值。"""
    if value is True:
        return "yes"
    if value is False:
        return "no"
    return value


def _is_known_spdx(lic: str, spdx_ids: set[str]) -> bool:
    lic = (lic or "").strip()
    if not lic:
        return False
    if lic in spdx_ids:
        return True
    parts = [p.strip("() ") for p in SPDX_OPERATOR_RE.split(lic)]
    return len(parts) > 1 and all(p in spdx_ids for p in parts if p)


def _tracked_files() -> set[str] | None:
    """仓库里被 git 跟踪的文件（相对 REPO_ROOT 的 posix 路径）。取不到返回 None。"""
    import subprocess

    try:
        proc = subprocess.run(
            ["git", "ls-files"], cwd=str(REPO_ROOT), capture_output=True, text=True, check=True
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    return {line.strip() for line in proc.stdout.splitlines() if line.strip()}


def _evidence_source_resolves(token: str, tracked: set[str]) -> bool:
    """evidence 里提到的文件名，是否对应一个被跟踪的文件（按 basename 或路径后缀匹配）。"""
    return any(path == token or path.endswith("/" + token) for path in tracked)


def collect_entries() -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for path in sorted(CAP_DIR.glob("*.yaml")):
        entries = _load_yaml(path).get("entries")
        out[path.name] = entries if isinstance(entries, list) else []
    return out


def main() -> int:
    for path in (SCHEMA, SOURCES, SPDX_LIST):
        if not path.exists():
            print(f"[FAIL] 缺少判据文件 {path}")
            return 1

    required = _load_yaml(SCHEMA).get("required_fields") or []
    source_ids = {s["id"] for s in (_load_yaml(SOURCES).get("sources") or []) if s.get("id")}
    with SPDX_LIST.open(encoding="utf-8") as fh:
        spdx_ids = set(json.load(fh).get("ids") or [])

    if not required:
        print(f"[FAIL] {SCHEMA} 没有 required_fields，无法判定")
        return 1
    if not source_ids:
        print(f"[FAIL] {SOURCES} 没有 sources，无法判定")
        return 1

    files = collect_entries()
    if not any(files.values()):
        print("[FAIL] capabilities/ 下没有任何记录")
        return 1

    failures: list[str] = []
    warnings: list[str] = []
    seen_ids: dict[str, str] = {}
    total = 0
    evidence_skipped = 0
    tracked_files = _tracked_files()

    for fname, entries in files.items():
        for idx, entry in enumerate(entries):
            total += 1
            eid = entry.get("id") or f"<{fname}#{idx} 缺 id>"
            where = f"{fname} [{eid}]"

            missing = [f for f in required if entry.get(f) in (None, "", [], {})]
            if missing:
                failures.append(f"{where}: 缺必填字段 {missing}")
                continue

            if not ID_RE.match(str(entry["id"])):
                failures.append(f"{where}: id 不符合 LHX-OSS-#### 格式")
            if entry["id"] in seen_ids:
                failures.append(f"{where}: id 与 {seen_ids[entry['id']]} 重复")
            else:
                seen_ids[entry["id"]] = fname

            if entry.get("source_platform") not in source_ids:
                failures.append(
                    f"{where}: source_platform={entry.get('source_platform')!r} 不在 sources.yaml"
                )

            if _normalise_bool(entry.get("enhances_liuhao")) not in ENHANCES:
                failures.append(f"{where}: enhances_liuhao={entry.get('enhances_liuhao')!r} 非法")

            nr = str(entry.get("needs_refactor"))
            if not nr.startswith(NEEDS_REFACTOR):
                failures.append(f"{where}: needs_refactor={nr!r} 必须以 none|light|heavy 起头")

            for field in ("commercial_risk", "security_risk"):
                raw = str(entry.get(field)).strip()
                if not raw.startswith(RISK_LEVELS):
                    failures.append(f"{where}: {field}={raw!r} 必须以 low|medium|high 起头")
                elif raw in RISK_LEVELS:
                    warnings.append(f"{where}: {field} 只写了裸等级 {raw!r}，schema 要求「+ 原因」")

            if entry.get("adoption_decision") not in DECISIONS:
                failures.append(
                    f"{where}: adoption_decision={entry.get('adoption_decision')!r} 不在四值之内"
                )
            if "verdict" in entry and entry.get("verdict") not in DECISIONS:
                if fname in VERDICT_OUTCOME_STYLE:
                    warnings.append(
                        f"{where}: verdict={entry.get('verdict')!r} 属落地结果词表（体例差异），非违规"
                    )
                else:
                    failures.append(f"{where}: verdict={entry.get('verdict')!r} 不在四值之内")

            lic = str(entry.get("license"))
            if not _is_known_spdx(lic, spdx_ids) and fname not in LICENSE_RULE_EXEMPT:
                if not str(entry.get("commercial_risk")).startswith("high"):
                    failures.append(
                        f"{where}: license={lic!r} 非标准 SPDX（schema 第 21 行要求此情形下 "
                        f"commercial_risk=high），当前为 {entry.get('commercial_risk')!r}"
                    )

            evidence = entry.get("evidence")
            if evidence is not None:
                if tracked_files is None:
                    evidence_skipped += 1
                else:
                    tokens = sorted(set(EVIDENCE_FILE_RE.findall(str(evidence))))
                    if not tokens:
                        failures.append(f"{where}: evidence 未引用任何 .json/.yaml 来源，不可回溯")
                    elif not any(_evidence_source_resolves(t, tracked_files) for t in tokens):
                        failures.append(
                            f"{where}: evidence 引用的 {tokens} 均不是 git 跟踪的文件，"
                            f"他人无法复核（本地产物 / 拼写错误？）"
                        )

            blob = " ".join(str(v) for v in entry.values())
            for cliche in CLICHES:
                if cliche in blob:
                    failures.append(f"{where}: 出现套话 {cliche!r}（schema 判定为无效表述）")

    print(f"已扫描 {len(files)} 个文件、{total} 条记录")
    for fname, entries in files.items():
        notes = []
        if fname in LICENSE_RULE_EXEMPT:
            notes.append("豁免第 6 条：落地复记体例，非第三方候选")
        if fname in VERDICT_OUTCOME_STYLE:
            notes.append("verdict 用落地结果词表")
        suffix = f"（{'；'.join(notes)}）" if notes else ""
        print(f"  {fname}: {len(entries)} 条{suffix}")

    if evidence_skipped:
        print()
        print(f"[WARN] 取不到 git 文件列表，跳过 {evidence_skipped} 条 evidence 复核（第 7 条）")

    if warnings:
        verbose = "--verbose" in sys.argv
        print()
        print(f"[WARN] {len(warnings)} 项待收敛（不阻断，属体例差异而非契约违规）：")
        groups: dict[str, list[str]] = {}
        for item in warnings:
            key = "裸等级未带原因" if "裸等级" in item else "verdict 体例差异"
            groups.setdefault(key, []).append(item)
        for key, items in sorted(groups.items()):
            shown = items if verbose else items[:3]
            print(f"  ! {key}：{len(items)} 项")
            for item in shown:
                print(f"      {item}")
            if not verbose and len(items) > len(shown):
                print(f"      …（另 {len(items) - len(shown)} 项，用 --verbose 看全量）")

    if failures:
        print()
        print(f"[FAIL] 发现 {len(failures)} 项契约违规：")
        for item in failures:
            print(f"  - {item}")
        return 1

    print()
    print("[GREEN] capabilities/ 全部记录满足 schema 字段契约")
    return 0


if __name__ == "__main__":
    sys.exit(main())
