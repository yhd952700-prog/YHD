"""发布前端 dist 契约测试（护栏 6：守发布物而非源码）。

断言对象是仓库根 ``console/`` 的发布 dist 本体——index.html 的主题挂载、
PWA 链接、wb 世代标记、主题叠加层四文件、资产可达性与签章指纹。
与 scripts/check_frontend_guardrails.py 同口径（脚本是发布清单入口，
本测试把同一组断言钉进 pytest 子集，防回归）。

注：apps/console 的 vitest 是 os 世代源码测试，非发布物覆盖，与本文件无关。
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONSOLE = ROOT / "console"
sys.path.insert(0, str(ROOT / "scripts"))

from check_frontend_guardrails import (  # noqa: E402
    _reachability,
    check_canon,
    check_dom,
    check_fingerprint,
)


def test_dist_canon_wb_generation_and_theme_files():
    assert check_canon() == []


def test_dist_index_links_manifest_and_theme():
    html = (CONSOLE / "index.html").read_text(encoding="utf-8", errors="ignore")
    assert "manifest.webmanifest" in html
    assert "theme/jarvis-theme.css" in html
    assert "theme/jarvis-future-theme.css" in html


def test_dist_no_orphan_assets():
    assets, seen = _reachability()
    assert assets - seen == set()


def test_dist_fingerprint_matches_stamp():
    assert check_fingerprint() == []


def test_dist_dom_contract_hooks_present():
    assert check_dom() == []
