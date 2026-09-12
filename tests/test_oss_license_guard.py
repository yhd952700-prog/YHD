"""`oss-registry.yaml` 与 `scripts/verify_oss_licenses.py` 的契约测试。

为什么要用 pytest 再钉一层：`verify_*.py` 只在 CI 的 guardrails job 里跑，
而登记册被人手改坏（删掉 acknowledged、漏登记新依赖）时，我们希望
**常规测试集**也立刻变红，不是等到某个 job 半夜失败。
"""
from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
REGISTRY = REPO_ROOT / "oss-registry.yaml"
SCRIPT = REPO_ROOT / "scripts" / "verify_oss_licenses.py"

REQUIRED_FIELDS = ("name", "license", "category", "risk")


@pytest.fixture(scope="module")
def registry() -> dict:
    assert REGISTRY.exists(), f"缺少登记册 {REGISTRY}"
    with REGISTRY.open(encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    assert isinstance(data, dict), "登记册顶层必须是 mapping"
    return data


@pytest.fixture(scope="module")
def guard_module():
    spec = importlib.util.spec_from_file_location("verify_oss_licenses", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_registry_has_components(registry):
    assert registry.get("components"), "登记册里至少要有一个组件"


def test_every_component_has_required_fields(registry):
    bad = []
    for comp in registry["components"]:
        missing = [f for f in REQUIRED_FIELDS if not comp.get(f)]
        if missing:
            bad.append(f"{comp.get('name') or '<unnamed>'}: 缺 {missing}")
    assert not bad, "登记不完整：\n" + "\n".join(bad)


def test_restricted_components_must_be_acknowledged(registry):
    """受限许可证必须有书面接受理由。这是本护栏的核心不变式。"""
    restricted = {c.lower().replace("-", "").replace(".", "").replace("_", "")
                  for c in registry.get("restricted_licenses") or []}

    def norm(text: str) -> str:
        return "".join(ch for ch in (text or "").lower() if ch.isalnum())

    offenders = []
    for comp in registry["components"]:
        if norm(comp.get("license")) in restricted and not (comp.get("acknowledged") or "").strip():
            offenders.append(comp["name"])
    assert not offenders, f"受限许可证未写 acknowledged 理由：{offenders}"


def test_component_names_are_unique(registry):
    names = [c["name"] for c in registry["components"] if c.get("name")]
    dupes = {n for n in names if names.count(n) > 1}
    assert not dupes, f"登记册里有重复组件：{sorted(dupes)}"


def test_allow_and_restricted_do_not_overlap(registry):
    def norm(text: str) -> str:
        return "".join(ch for ch in (text or "").lower() if ch.isalnum())

    overlap = {norm(a) for a in registry.get("allow_licenses") or []} & {
        norm(r) for r in registry.get("restricted_licenses") or []
    }
    assert not overlap, f"同一许可证同时出现在允许与受限名单：{overlap}"


@pytest.mark.parametrize(
    "declared,actual,expected",
    [
        ("MIT", "MIT", True),
        ("MIT", "MIT License", True),
        ("Apache-2.0", "Apache Software License", True),
        ("MPL-2.0 AND (Apache-2.0 OR MIT)", "MPL-2.0 AND (Apache-2.0 OR MIT)", True),
        ("MIT", "GPL-3.0", False),
        ("", "MIT", False),
    ],
)
def test_lic_compatible(guard_module, declared, actual, expected):
    assert guard_module.lic_compatible(declared, actual) is expected


def test_script_runs_clean(registry):
    """真实跑一遍护栏：本机装了的组件必须全部合规。"""
    proc = subprocess.run(
        [sys.executable, str(SCRIPT)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=str(REPO_ROOT),
    )
    assert proc.returncode == 0, f"护栏失败：\n{proc.stdout}\n{proc.stderr}"
    assert "GREEN" in proc.stdout
