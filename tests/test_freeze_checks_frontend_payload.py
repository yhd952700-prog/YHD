"""frontend-payload 门禁：清单外只允许打包期注入的 console-contract.json。

历史缺陷：门禁只比「文件数量」，于是 payload 用 console-contract.json 顶替掉一个
真实前端文件时，数量仍然相等、门禁照样 PASS；反过来把 console-contract.json 当成
「多余文件」又会把正常 payload 判红。这里把规则钉死：白名单精确到
``console/console-contract.json`` 一个文件名，其它任何多出/缺少的文件都必须判红。
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
GATE_PATH = REPO_ROOT / "scripts" / "release" / "freeze_checks.py"


@pytest.fixture()
def gate():
    spec = importlib.util.spec_from_file_location("liuhao_freeze_checks", GATE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _build_tree(tmp_path: Path, gate) -> tuple[Path, Path]:
    """造一份「诚实」的 dist 与 payload：两份清单完全一致，另加打包注入的契约签章。"""
    repo = tmp_path / "repo"
    dist = repo / "apps" / "console" / "console" / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<html></html>", encoding="utf-8")
    (dist / "assets" / "app.js").write_text("console.log(1)", encoding="utf-8")

    app = tmp_path / "payload" / "app"
    (app / "console" / "assets").mkdir(parents=True)
    (app / "console" / "index.html").write_text("<html></html>", encoding="utf-8")
    (app / "console" / "assets" / "app.js").write_text("console.log(1)", encoding="utf-8")

    pairs = [
        ("console/" + path.relative_to(dist).as_posix(), gate._sha256_file(path))
        for path in dist.rglob("*")
        if path.is_file()
    ]
    stamp = {
        "frontend_version": "0.1.1",
        "console_files": len(pairs),
        "console_fingerprint": gate._set_fingerprint(pairs),
    }
    (app / "console" / "console-contract.json").write_text(
        json.dumps(stamp, ensure_ascii=False), encoding="utf-8"
    )
    return repo, app


def test_contract_file_alone_is_not_treated_as_extra(gate, tmp_path):
    repo, app = _build_tree(tmp_path, gate)
    assert gate.check_frontend_payload(repo, app, "0.1.1") == 0


def test_one_more_non_whitelisted_file_must_fail(gate, tmp_path):
    repo, app = _build_tree(tmp_path, gate)
    (app / "console" / "assets" / "stale-index.js").write_text("old bundle", encoding="utf-8")
    assert gate.check_frontend_payload(repo, app, "0.1.1") == 2


def test_missing_file_must_fail_even_when_count_compensated(gate, tmp_path):
    """多一个 + 少一个、总数不变 —— 旧的「只比数量」实现会漏掉这种情形。"""
    repo, app = _build_tree(tmp_path, gate)
    (app / "console" / "assets" / "app.js").unlink()
    (app / "console" / "assets" / "stale-index.js").write_text("old bundle", encoding="utf-8")
    assert gate.check_frontend_payload(repo, app, "0.1.1") == 2
