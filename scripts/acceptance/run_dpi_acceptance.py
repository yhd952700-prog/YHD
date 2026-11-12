#!/usr/bin/env python
"""一条命令跑完高 DPI 验收：桌面缩放组合 + 控制台 dPR=2 首屏。

对应验收清单：
  1. 125% / 175% 非整档，及双屏 1.25 ↔ 1.5 之间移动；
  2. 1920×1080 → 1366×768 → 1280×720 → 断开远程桌面回本机 150%；
  3. 控制台在 devicePixelRatio=2 下的首屏可用性；
  4. 逐条 PASS/FAIL + 关键尺寸，证据图落 ``docs/dpi-evidence/``。

用法
----
    python scripts/acceptance/run_dpi_acceptance.py              # 全部
    python scripts/acceptance/run_dpi_acceptance.py --desktop-only
    python scripts/acceptance/run_dpi_acceptance.py --console-only

汇总报告：``docs/dpi-evidence/dpi-acceptance-report.json``。

退出码：``0`` 全部通过；``1`` 有失败（阻断）；``3`` 桌面用例因当前不是
交互式桌面会话被跳过（无头 / 服务会话），**不做判定**。
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
EVIDENCE_DIR = REPO / "docs" / "dpi-evidence"
SCALING_REPORT = EVIDENCE_DIR / "dpi-scaling-report.json"
CONSOLE_REPORT = EVIDENCE_DIR / "console-dpr2-report.json"
MERGED_REPORT = EVIDENCE_DIR / "dpi-acceptance-report.json"


def _run(script: Path) -> int:
    completed = subprocess.run(
        [sys.executable, str(script)],
        cwd=str(REPO),
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return completed.returncode


def _load(path: Path) -> dict[str, object] | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="高 DPI 验收总入口")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--desktop-only", action="store_true")
    group.add_argument("--console-only", action="store_true")
    args = parser.parse_args(argv)

    codes: dict[str, int] = {}
    if not args.console_only:
        codes["desktop"] = _run(HERE / "dpi_scaling_acceptance.py")
    if not args.desktop_only:
        codes["console"] = _run(HERE / "console_dpr2_acceptance.py")

    scaling = _load(SCALING_REPORT) or {}
    console = _load(CONSOLE_REPORT) or {}
    cases = list(scaling.get("cases", []))  # type: ignore[arg-type]
    console_checks = list(console.get("checks", []))  # type: ignore[arg-type]
    total = len(cases) + (1 if console_checks else 0)
    passed = sum(1 for item in cases if item.get("passed"))
    console_passed = 1 if console.get("passed") else 0
    passed += console_passed
    skipped = [name for name, code in codes.items() if code == 3]

    EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
    MERGED_REPORT.write_text(
        json.dumps(
            {
                "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                "exit_codes": codes,
                "skipped": skipped,
                "passed": passed,
                "total": total,
                "desktop": scaling,
                "console": console,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    print("\n=== 汇总 ===")
    if codes.get("desktop") == 3:
        print("  SKIP  desktop-dpi（需要交互式桌面会话：无头 / 服务会话下无法验证）")
    else:
        for item in cases:
            print(f"  {'PASS' if item.get('passed') else 'FAIL'}  {item.get('case')}")
    if console_checks:
        print(f"  {'PASS' if console.get('passed') else 'FAIL'}  console-dpr2")
    print(f"\n合计：{passed}/{total} 通过")
    if skipped:
        print(f"跳过：{', '.join(skipped)}（需交互式桌面会话，本轮未判定）")
    print(f"报告：{MERGED_REPORT.relative_to(REPO)}")

    # 失败优先：有 1 就是阻断，不会被"另一边跳过"盖掉；全都跳过才算 3。
    if any(code == 1 for code in codes.values()) or passed != total:
        return 1
    if skipped:
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
