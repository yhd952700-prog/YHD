"""Command-line surface for controlled self-modification.

Examples::

    python -m src.selfmod propose --edits-file edits.json --title "修复 X" --test-command "python -m pytest -q tests/test_x.py"
    python -m src.selfmod show --proposal-id <id>
    python -m src.selfmod confirm --proposal-id <id> --approver owner
    python -m src.selfmod run --proposal-id <id> --token <token>
    python -m src.selfmod finalize --proposal-id <id> --token <token> --allow-promote
    python -m src.selfmod rollback --proposal-id <id>

``edits.json`` is a list of ``{"path": "src/foo.py", "content": "..."}``;
``content: null`` means delete the file.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, List, Optional

from .errors import SelfModError
from .models import ResourceLimits
from .patcher import render
from .service import SelfModService


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m src.selfmod",
        description="鎏灏受控自改代码：生成补丁、展示 diff、人工确认、副本测试、可回滚应用",
    )
    parser.add_argument("--workspace", default=".", help="工作区目录（默认当前目录）")
    parser.add_argument("--state-dir", default=None, help="状态目录（默认 <workspace>/.selfmod-state）")
    parser.add_argument("--timeout", type=float, default=300.0, help="测试超时秒数")
    parser.add_argument("--memory-mb", type=int, default=2048, help="测试内存上限 MB")
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--json", action="store_true", help="以 JSON 输出")
    sub = parser.add_subparsers(dest="command", required=True)

    p_propose = sub.add_parser("propose", parents=[common], help="生成提案与 diff（不改工作区）")
    p_propose.add_argument("--edits-file", required=True, help="编辑列表 JSON 文件")
    p_propose.add_argument("--title", required=True)
    p_propose.add_argument("--rationale", default="")
    p_propose.add_argument("--test-command", default=None)

    p_show = sub.add_parser("show", parents=[common], help="展示提案 diff")
    p_show.add_argument("--proposal-id", required=True)

    p_confirm = sub.add_parser("confirm", parents=[common], help="人工确认并签发一次性令牌")
    p_confirm.add_argument("--proposal-id", required=True)
    p_confirm.add_argument("--approver", required=True)
    p_confirm.add_argument("--stage", choices=["test", "promote"], default="test")

    p_run = sub.add_parser("run", parents=[common], help="在副本上应用并运行测试")
    p_run.add_argument("--proposal-id", required=True)
    p_run.add_argument("--token", required=True)

    p_finalize = sub.add_parser("finalize", parents=[common], help="测试通过后应用到真实工作区")
    p_finalize.add_argument("--proposal-id", required=True)
    p_finalize.add_argument("--token", required=True)
    p_finalize.add_argument("--allow-promote", action="store_true")

    p_rollback = sub.add_parser("rollback", parents=[common], help="回滚已应用的提案")
    p_rollback.add_argument("--proposal-id", required=True)

    sub.add_parser("list", parents=[common], help="列出提案")
    return parser


def _emit(args: argparse.Namespace, payload: Any) -> None:
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False, indent=2))


def main(argv: Optional[List[str]] = None) -> int:
    args = _parser().parse_args(argv)
    service = SelfModService(
        args.workspace,
        state_dir=args.state_dir,
        allow_promote=bool(getattr(args, "allow_promote", False)),
        limits=ResourceLimits(timeout_seconds=args.timeout, memory_mb=args.memory_mb),
    )
    try:
        if args.command == "propose":
            edits = json.loads(Path(args.edits_file).read_text(encoding="utf-8"))
            proposal = service.propose(
                title=args.title,
                rationale=args.rationale,
                edits=edits,
                test_command=args.test_command,
            )
            _emit(args, render(proposal) if not args.json else proposal)
            return 0
        if args.command == "show":
            print(service.show_diff(args.proposal_id))
            return 0
        if args.command == "confirm":
            token = service.confirm(
                args.proposal_id, approver=args.approver, stage=args.stage
            )
            _emit(
                args,
                {"proposal_id": args.proposal_id, "stage": args.stage, "token": token},
            )
            return 0
        if args.command == "run":
            result = service.run_on_copy(args.proposal_id, args.token)
            _emit(args, result if args.json else {
                "status": result["status"],
                "reason": result["reason"],
                "real_workspace_modified": result["real_workspace_modified"],
                "test": result["test"],
            })
            return 0 if result["status"] == "passed" else 3
        if args.command == "finalize":
            result = service.finalize(args.proposal_id, args.token)
            _emit(args, result if args.json else {
                "status": result["status"],
                "backup_dir": result.get("backup_dir"),
                "reason": result.get("reason"),
            })
            return 0
        if args.command == "rollback":
            result = service.rollback(args.proposal_id)
            _emit(args, result if args.json else {"status": result["status"], "reason": result["reason"]})
            return 0
        if args.command == "list":
            _emit(args, {"proposals": service.list()})
            return 0
        return 0
    except SelfModError as exc:
        print(f"失败：{exc}", file=sys.stderr)
        return 1
    finally:
        service.close()


if __name__ == "__main__":
    raise SystemExit(main())
