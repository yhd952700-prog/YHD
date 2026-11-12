"""OSI 命令行入口：``python -m src.intel --repos ...``。

真实网络默认关闭；显式 ``--allow-network`` 或环境变量
``LIUHAO_INTEL_ALLOW_NETWORK=1`` 才会发起 GitHub 与 OSV 请求。``GITHUB_TOKEN``
只在内存中用于本次请求头，绝不落盘、不写日志、不进入任何记录。
"""

from __future__ import annotations

import argparse
import os
from collections.abc import Sequence

from src.intel.github_client import GitHubClient
from src.intel.models import LicenseCategory, ScanBudget
from src.intel.osv_client import OsvClient
from src.intel.scanner import IntelScanner
from src.intel.store import IntelStore


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m src.intel",
        description="贾维斯开源技术情报中心：只发现、只建议、不自动动手。",
    )
    parser.add_argument("--repos", nargs="+", required=True, help="GitHub 仓库清单")
    parser.add_argument(
        "--category",
        choices=[item.value for item in LicenseCategory],
        default=LicenseCategory.RUNTIME.value,
        help="许可证使用落点（默认 runtime）",
    )
    parser.add_argument("--max-repos", type=int, default=10, help="本轮仓库数量硬顶")
    parser.add_argument("--max-api-calls", type=int, default=60, help="本轮 API 调用硬顶")
    parser.add_argument("--max-tokens", type=int, default=20000, help="本轮 token 硬顶")
    parser.add_argument("--report", default=None, help="报告输出路径（默认 data/intel/reports）")
    parser.add_argument("--db", default=None, help="SQLite 路径（默认 data/intel/osi_intel.db）")
    parser.add_argument(
        "--allow-network",
        action="store_true",
        help="允许真实 GitHub 网络调用（默认关闭）",
    )
    parser.add_argument(
        "--no-report",
        action="store_true",
        help="只写数据库，不生成报告文件",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    allow_network = args.allow_network or os.environ.get(
        "LIUHAO_INTEL_ALLOW_NETWORK", ""
    ) == "1"

    budget = ScanBudget(
        max_repos=args.max_repos,
        max_api_calls=args.max_api_calls,
        max_tokens=args.max_tokens,
    )
    store = IntelStore(args.db)
    client = GitHubClient(
        allow_network=allow_network,
        budget=budget,
        token=os.environ.get("GITHUB_TOKEN") or None,
    )
    try:
        osv = OsvClient(allow_network=allow_network, budget=budget)
        scanner = IntelScanner(store, client, osv_client=osv)
        result = scanner.scan(
            args.repos,
            category=args.category,
            budget=budget,
            report_path=args.report,
            write_report_file=not args.no_report,
        )
    finally:
        client.close()
        store.close()
        if "osv" in locals():
            osv.close()

    print(f"仓库：{len(result.repo_urls)}；候选：{len(result.candidates)}")
    print(f"安全公告记录：{len(result.advisories)}；版本变更：{len(result.changes)}")
    print(f"建议采用/改造：{len(result.adoptable)}；未获取记录见报告")
    if result.report_path:
        print(f"报告：{result.report_path}")
    else:
        print("未生成报告文件（--no-report）")
    if not allow_network:
        print("真实网络默认关闭；本次所有抓取均记为「未获取」，未伪造任何数据。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
