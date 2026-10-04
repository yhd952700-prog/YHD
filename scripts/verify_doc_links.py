#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
verify_doc_links.py — a markdown link in this repo must point at something real.

WHY THIS EXISTS
---------------
A relative link is a claim: "the thing I am describing is over there". When it
dangles, the reader who follows it is told, silently, that the document they were
sent to does not exist -- and, worse, that the *concept* it named may not exist.

Two real instances found while auditing `docs/` (2026-10-04):

  * `docs/ARCHITECTURE.md` twice linked `../CODEX-CONTRACT.md`. `CODEX-CONTRACT.md`
    lives in `docs/`, not the repo root, so `../` pointed one level too high. The
    link sat in the **"快速导航 / 读者应该先读"** table -- i.e. it was the
    recommended entry point for an AI coding agent, and it was dead. That file
    also ends with "完整 25 条见 ../CODEX-CONTRACT.md", so the 25 hard rules were
    unreachable from the architecture overview.

  * A repo-wide sweep found 57 dangling links. Most were in `docs/archive/`
    (`file:///d:/...` absolute links captured from a 2026 audit) and in vendored
    third-party READMEs. Those are deliberately NOT touched: `archive/` declares
    "保持原貌" (preserve as-is) and rewriting history to fix a link would be a
    worse lie than the broken link. So the gate scopes them out explicitly rather
    than pretending they do not exist.

WHAT IT CHECKS
--------------
Every relative markdown link in the ACTIVE documentation surface resolves to an
existing file or directory. Absolute `http(s)://`, `mailto:`, `file://`, and pure
fragment (`#anchor`) links are out of scope: the first two are not ours to
validate, and `file://` links are only meaningful on the machine that wrote them.

HONESTY BOUNDARY
----------------
PASS means "no relative link in the scanned surface dangles". It does NOT mean the
linked document is accurate, current, or safe -- link resolution is the weakest
possible check, and this gate is explicitly not a claim about content. It also
does not validate anchors: `#some-heading` may not match a heading in the target
file, and markdown renderers differ on how they generate heading slugs. Anchor
verification was left out rather than approximated, because a check that is
usually wrong teaches people to ignore the output.
"""

from __future__ import annotations

import os
import re

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Roots whose markdown is scanned. Root-level project docs are part of the
# outward face: a reader arriving from the repo homepage meets them first.
SCAN_ROOTS = ("docs",)
SCAN_ROOT_FILES = (
    "README.md",
    "ACCEPTANCE.md",
    "CONTRIBUTING.md",
    "MASTER_PROJECT_READINESS.md",
    "RELEASE-READINESS-CONTRACT.md",
)

# `docs/archive/` is excluded BY THE DOCUMENT'S OWN DECLARATION
# ("归档原则：archive/ 内的历史文档保持原貌，正文中的文件路径为整理前的原始路径，
#  不作改动"). Its `file:///d:/...` links are a faithful record of where paths
# pointed when the text was written. Rewriting them would falsify a historical
# snapshot to make a linter green.
EXCLUDED_PREFIXES = ("docs/archive/",)

# Third-party / vendored trees.
EXCLUDED_SUBSTRINGS = (
    "node_modules",
    ".venv",
    "/eap/",
    "oss-ecosystem/",
    "__pycache__",
    ".git/",
)

LINK_RE = re.compile(r"\[[^\]]*\]\(([^)\s]+)\)")


def _is_excluded(rel: str) -> bool:
    if rel.startswith(EXCLUDED_PREFIXES):
        return True
    norm = rel.replace("\\", "/")
    return any(s in norm for s in EXCLUDED_SUBSTRINGS)


def _scan_file(abs_path: str, rel: str, broken: list) -> int:
    with open(abs_path, encoding="utf-8", errors="replace") as fh:
        text = fh.read()
    checked = 0
    for lineno, line in enumerate(text.split("\n"), 1):
        for m in LINK_RE.finditer(line):
            target = m.group(1)
            if target.startswith(("http://", "https://", "#", "mailto:", "file://")):
                continue
            path = target.split("#", 1)[0].split("?", 1)[0]
            if not path:
                continue
            checked += 1
            resolved = os.path.normpath(
                os.path.join(os.path.dirname(rel), path)
            ).replace("\\", "/")
            if not os.path.exists(os.path.join(REPO_ROOT, resolved)):
                broken.append((rel, lineno, target, resolved))
    return checked


def main() -> int:
    broken: list = []
    scanned = 0
    total_links = 0

    targets: list = []
    for root in SCAN_ROOTS:
        base = os.path.join(REPO_ROOT, root)
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = [
                d for d in dirnames if d not in {"__pycache__", "node_modules", ".git"}
            ]
            for fn in filenames:
                if fn.endswith(".md"):
                    full = os.path.join(dirpath, fn)
                    targets.append((full, os.path.relpath(full, REPO_ROOT).replace("\\", "/")))
    for fn in SCAN_ROOT_FILES:
        full = os.path.join(REPO_ROOT, fn)
        if os.path.isfile(full):
            targets.append((full, fn))

    for full, rel in targets:
        if _is_excluded(rel):
            continue
        scanned += 1
        total_links += _scan_file(full, rel, broken)

    print(f"scanned {scanned} markdown file(s); checked {total_links} relative link(s)")
    print(f"excluded by policy: {EXCLUDED_PREFIXES} (declared 'preserve as-is')")

    if broken:
        print(f"\nRESULT: FAIL — {len(broken)} dangling relative link(s):\n")
        for rel, lineno, target, resolved in broken:
            print(f"  {rel}:{lineno}  ->  {target}")
            print(f"      resolves to: {resolved}  (does not exist)")
        print(
            "\nA dangling link is a claim that the thing it names is not there."
            "\nFix the PATH if the file exists, or fix the PROSE if it does not --"
            "\ndo not delete the sentence that made the claim."
        )
        return 1

    print("\nRESULT: PASS — no dangling relative links in the active documentation surface.")
    print(
        "      BOUNDARY: link resolution only. A resolving link says nothing about\n"
        "      whether the target document is accurate, current, or safe to follow.\n"
        "      Anchors (#fragment) are NOT verified -- left out rather than guessed."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
