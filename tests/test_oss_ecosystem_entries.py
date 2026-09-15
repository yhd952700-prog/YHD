"""`oss-ecosystem/capabilities/*.yaml` 字段契约的可执行门禁。

为什么需要这层：`oss-ecosystem/schema/capability-entry.schema.yaml` 开头写着
「让『每个发现的项目都必须分析』变成**可校验**的事」并指名了校验器，
但那个校验器此前**并不存在** —— 契约一直只是散文。代价已经出现：
2026-09-15 的 tier2 批次里 5 条 license 被记成裸 `NOASSERTION` 且风险等级填 `medium`，
直接违反 schema 第 21 行，而没有东西发现。

这个测试做三件事，缺一不可：
  1. 在**真实语料**上跑校验器，必须通过；
  2. 注入违规，必须**变红**（否则门禁会悄悄退化成 no-op —— 那正是本仓最怕的"静默谎报成功"）；
  3. README 里承诺的条数必须等于实际条数（此前 README 写 13 条、实际 32 条，已漂移过一次）。
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
OSS_ROOT = REPO_ROOT / "oss-ecosystem"
CAP_DIR = OSS_ROOT / "capabilities"
VALIDATOR = OSS_ROOT / "pipeline" / "validate_entries.py"
README = OSS_ROOT / "README.md"

# 注入用的临时探针文件；必须跑完即删，绝不能留在 capabilities/ 里被提交
PROBE_NAME = "_zz_probe_invalid.yaml"

_ENTRY = """  - id: {eid}
    name: {eid}
    source_platform: github
    url: https://example.com/probe
    function: 探针
    technical_value: 探针
    enhances_liuhao: "partial"
    absorbable: 探针
    needs_refactor: light
    integration_plan: 探针
    license: {license}
    commercial_risk: {commercial_risk}
    security_risk: low —— 探针
    performance_impact: 待测
    adoption_decision: watch
    evidence: "{evidence}"
{extra}"""


def run_validator() -> tuple[int, str]:
    proc = subprocess.run(
        [sys.executable, str(VALIDATOR)],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


def test_validator_script_is_where_schema_says_it_is():
    """schema 里白纸黑字写了 `pipeline/validate_entries.py` —— 别再让它只是承诺。"""
    assert VALIDATOR.exists(), f"schema 声明的校验器不存在：{VALIDATOR}"


def test_validator_passes_on_real_corpus():
    code, out = run_validator()
    assert code == 0, f"字段契约校验未通过：\n{out}"
    assert "[GREEN]" in out


def test_real_corpus_entries_are_loadable_and_non_trivial():
    """防止"文件在但内容空掉"这种假绿。"""
    total = 0
    for path in sorted(CAP_DIR.glob("*.yaml")):
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        entries = data.get("entries") or []
        assert entries, f"{path.name} 没有 entries"
        total += len(entries)
    assert total >= 40, f"记录总数 {total} 异常偏低，疑似语料被清空"


def _probe(case: str) -> str:
    """按类别造一条**故意违规**的记录。"""

    def build(eid: str, extra: str = "", **over) -> str:
        body = _ENTRY.format(
            eid=eid,
            license=over.get("license", "MIT"),
            commercial_risk=over.get("commercial_risk", "low —— 探针"),
            evidence=over.get("evidence", "scan-20260913T203529Z.json"),
            extra=extra,
        )
        if "enhances" in over:
            body = body.replace('enhances_liuhao: "partial"', f"enhances_liuhao: {over['enhances']}")
        return body

    if case == "missing-field":
        return build("LHX-OSS-9001").replace("    technical_value: 探针\n", "")
    if case == "nonspdx-low-risk":
        return build("LHX-OSS-9002", license="NOASSERTION", commercial_risk="low —— 探针")
    if case == "bad-enum":
        return build("LHX-OSS-9003", enhances="maybe")
    if case == "cliche":
        return build("LHX-OSS-9004", extra="    decision_note: 提升效率\n")
    if case == "untracked-evidence":
        return build("LHX-OSS-9005", evidence="见 bandit-report.json")
    raise AssertionError(f"未知用例 {case}")


@pytest.mark.parametrize(
    "case,expected_fragment",
    [
        ("missing-field", "缺必填字段"),
        ("nonspdx-low-risk", "非标准 SPDX"),
        ("bad-enum", "enhances_liuhao"),
        ("cliche", "套话"),
        ("untracked-evidence", "git 跟踪"),
    ],
)
def test_validator_catches_injected_violation(case, expected_fragment):
    """门禁必须有牙：注入一类违规就必须变红。"""
    probe = CAP_DIR / PROBE_NAME
    try:
        probe.write_text("entries:\n" + _probe(case), encoding="utf-8")
        code, out = run_validator()
        assert code == 1, f"注入「{case}」后校验器仍通过 —— 门禁是假的：\n{out}"
        assert expected_fragment in out, f"注入「{case}」未被识别，输出里没有 {expected_fragment!r}：\n{out}"
    finally:
        probe.unlink(missing_ok=True)
    # 清理后必须立刻恢复通过，证明失败确实来自注入
    code, out = run_validator()
    assert code == 0, f"探针删除后仍未恢复通过：\n{out}"


def test_readme_declared_corpus_size_matches_actual():
    """README 曾把 32 条写成 13 条 —— 让条数漂移能被测出来。"""
    files = sorted(CAP_DIR.glob("*.yaml"))
    actual_files = len(files)
    actual_entries = 0
    for path in files:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        actual_entries += len(data.get("entries") or [])

    text = README.read_text(encoding="utf-8")
    claims = re.findall(r"共\s*\*\*(\d+)\s*条记录\s*/\s*(\d+)\s*个文件\*\*", text)
    claims += re.findall(r"\*\*(\d+)\s*条\s*/\s*(\d+)\s*个文件\*\*", text)
    assert claims, "README 里找不到『N 条 / M 个文件』的声明，无法比对"

    for declared_entries, declared_files in claims:
        assert (int(declared_entries), int(declared_files)) == (actual_entries, actual_files), (
            f"README 声明 {declared_entries} 条 / {declared_files} 个文件，"
            f"实际 {actual_entries} 条 / {actual_files} 个文件"
        )
