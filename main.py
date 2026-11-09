from __future__ import annotations

import argparse
import json
import os

from src.api.app import MetricsApplication
from src.api.providers_metrics import default_metrics_database_url
from src.core.lifecycle import LifecycleManager


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="LiuHao-AI-OS metrics runtime")
    parser.add_argument("command", choices=["health", "ready", "startup", "record"], help="Command to execute")
    parser.add_argument(
        "--database-url",
        default=(os.environ.get("DATABASE_URL") or "").strip() or None,
        help="Database URL（默认数据根下的 verify_metrics.db）",
    )
    parser.add_argument("--provider", default="openai")
    parser.add_argument("--model", default="gpt-4o-mini")
    parser.add_argument("--latency-ms", type=float, default=220.5)
    parser.add_argument("--success-rate", type=float, default=0.99)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    # 只在真正执行命令时解析默认库位置，避免 ``--help`` 也触发建库/迁移。
    database_url = args.database_url or default_metrics_database_url()

    if args.command == "health":
        app = MetricsApplication(database_url=database_url)
        print(json.dumps(app.health(), indent=2, sort_keys=True))
        return 0

    if args.command == "ready":
        app = MetricsApplication(database_url=database_url)
        print(json.dumps(app.ready(), indent=2, sort_keys=True))
        return 0

    if args.command == "startup":
        manager = LifecycleManager(database_url=database_url)
        print(json.dumps(manager.startup(), indent=2, sort_keys=True))
        return 0

    app = MetricsApplication(database_url=database_url)
    result = app.record_metric(
        provider=args.provider,
        model=args.model,
        latency_ms=args.latency_ms,
        success_rate=args.success_rate,
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
