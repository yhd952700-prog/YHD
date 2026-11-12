"""构建期品牌门禁必须能真的拦住旧主名/错字/死域名/旧版本号。"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

GUARD_PATH = Path(__file__).resolve().parent.parent / "packaging" / "brand_guard.py"
SPEC = importlib.util.spec_from_file_location("brand_guard", GUARD_PATH)
assert SPEC and SPEC.loader
brand_guard = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(brand_guard)

LOCKUP = brand_guard.brand_lockup()


def _clean_payload(root: Path) -> Path:
    files = {
        "START-HERE.txt": f"{LOCKUP} —— 先看这里\n",
        "BEFORE-INSTALL.txt": f"{LOCKUP}\n不含任何模型权重。\n",
        "THIRD-PARTY-NOTICES.txt": f"{LOCKUP}\n第三方商标归其各自所有者所有。\n",
        "console/index.html": f"<title>{LOCKUP}</title>\n",
        "console/manifest.webmanifest": json.dumps(
            {"name": LOCKUP, "short_name": "贾维斯"}, ensure_ascii=False
        ),
        "console/sw.js": "/* service worker */\n",
    }
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return root


def test_repo_surfaces_pass():
    assert brand_guard.scan() == []


def test_clean_payload_passes(tmp_path: Path):
    payload = _clean_payload(tmp_path)
    assert brand_guard.scan(payload=payload, skip_repo=True) == []


def test_old_main_name_fails(tmp_path: Path):
    payload = _clean_payload(tmp_path)
    with (payload / "START-HERE.txt").open("a", encoding="utf-8") as handle:
        handle.write("旧版自称：LIUHAO X\n")
    violations = brand_guard.scan(payload=payload, skip_repo=True)
    assert any("LIUHAO X" in item for item in violations)


def test_typo_and_dead_domain_fail(tmp_path: Path):
    payload = _clean_payload(tmp_path)
    with (payload / "THIRD-PARTY-NOTICES.txt").open("a", encoding="utf-8") as handle:
        handle.write("镭灏 / api.liuhao-ai-os.com\n")
    violations = brand_guard.scan(payload=payload, skip_repo=True)
    assert any("镭灏" in item for item in violations)
    assert any("api.liuhao-ai-os.com" in item for item in violations)


def test_old_name_hidden_deep_in_payload_fails(tmp_path: Path):
    payload = _clean_payload(tmp_path)
    deep = payload / "src" / "ai" / "legacy.py"
    deep.parent.mkdir(parents=True, exist_ok=True)
    deep.write_text('"""旧主名 LIUHAO X"""\n', encoding="utf-8")
    violations = brand_guard.scan(payload=payload, skip_repo=True)
    assert any("src/ai/legacy.py" in item and "LIUHAO X" in item for item in violations)


def test_repo_tree_scan_reports_file_and_line(tmp_path: Path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "legacy_brand.py").write_text(
        "# 旧主名\nbrand = 'LIUHAO X'\n", encoding="utf-8"
    )
    violations = brand_guard.scan(repo_root=tmp_path)
    assert any(
        "src/legacy_brand.py:2" in item and "LIUHAO X" in item for item in violations
    )


def test_repo_tree_allowlist_keeps_history(tmp_path: Path):
    archive = tmp_path / "docs" / "archive"
    archive.mkdir(parents=True)
    (archive / "old.md").write_text("LIUHAO X\n", encoding="utf-8")
    violations = brand_guard.scan(repo_root=tmp_path)
    assert not any("docs/archive/old.md" in item for item in violations)


def test_version_1_0_0_fails():
    hits = brand_guard._check_forbidden("openapi.yaml", "info:\n  version: 1.0.0\n")
    assert any("1.0.0" in item for item in hits)
