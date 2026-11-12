"""命令行入口：``python -m src.selfupgrade <command>``。

命令：
  verify-lock   校验 release-lock 完整性（出包前必跑）
  status        查看状态机 / 连续失败 / known-good
  run           触发一次自升级（只装测试目录，需 --approve）
  gates         只跑门禁
  rollback      回滚到 known-good（需 --approve）
  rescue        生成救援启动器（--launch 立即执行）
  unlock        Boss 解锁（需 --approve --reason）
  request       AI 写升级请求
  package       dry-run 出包（真实出包需 --allow-real，本轮不跑）
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import paths, rescue
from .gates import run_gates
from .manifest import verify_manifest
from .models import EXIT_ARG_IO, EXIT_DIFF, EXIT_PASS
from .policy import PolicyError, load_policy
from .rollback import rollback_to_known_good
from .runner import run_candidate, save_request
from .state import StateStore


def _print_verify(result) -> None:
    if result.ok:
        print(f"PASS seq={result.seq} manifest={result.manifest_sha256[:12]}…")
    else:
        print(f"FAIL seq={result.seq} 问题 {len(result.problems)} 项")
        for problem in result.problems:
            print(f"  - {problem}")
        for failure in result.failures:
            print(f"FAIL {failure.target}")
            print(f"  expected sha256 : {failure.expected_sha256}")
            print(f"  actual   sha256 : {failure.actual_sha256}")
            if failure.diff_unavailable:
                print("  changed lines   : 无法取回盖章内容（diff_unavailable）")
            else:
                print(f"  changed lines   : {failure.added + failure.removed} (+{failure.added} / -{failure.removed})")
                for line in failure.changed_lines:
                    print(f"    {line}")
        for drift in result.source_drift:
            print(f"DRIFT {drift}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m src.selfupgrade")
    parser.add_argument("--repo", default=str(paths.REPO_ROOT))
    parser.add_argument("--state-root", default=str(paths.STATE_ROOT))
    sub = parser.add_subparsers(dest="command", required=True)

    p_verify = sub.add_parser("verify-lock")
    p_verify.add_argument("--json", action="store_true")
    p_verify.add_argument("--check-source", action="store_true")

    sub.add_parser("status")

    p_run = sub.add_parser("run")
    p_run.add_argument("--installer", required=True)
    p_run.add_argument("--version", required=True)
    p_run.add_argument("--approve", action="store_true")
    p_run.add_argument("--gates", action="store_true")
    p_run.add_argument("--install-dir", default="")
    p_run.add_argument("--data-dir", default="")
    p_run.add_argument("--port", type=int, default=0)

    p_gates = sub.add_parser("gates")
    p_gates.add_argument("--no-frontend", action="store_true")

    p_rb = sub.add_parser("rollback")
    p_rb.add_argument("--approve", action="store_true")
    p_rb.add_argument("--install-dir", default="")
    p_rb.add_argument("--data-dir", default="")
    p_rb.add_argument("--port", type=int, default=0)

    p_rescue = sub.add_parser("rescue")
    p_rescue.add_argument("--launch", action="store_true")

    p_unlock = sub.add_parser("unlock")
    p_unlock.add_argument("--approve", action="store_true")
    p_unlock.add_argument("--reason", required=True)

    p_req = sub.add_parser("request")
    p_req.add_argument("--installer", required=True)
    p_req.add_argument("--version", required=True)
    p_req.add_argument("--reason", default="")

    p_pkg = sub.add_parser("package")
    p_pkg.add_argument("--version", required=True)
    p_pkg.add_argument("--out-root", required=True)
    p_pkg.add_argument("--allow-real", action="store_true")

    args = parser.parse_args(argv)
    repo = Path(args.repo)
    lock_dir = repo / "release-lock"

    if args.command == "verify-lock":
        result = verify_manifest(repo, lock_dir, check_source=args.check_source)
        if args.json:
            print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
        else:
            _print_verify(result)
        return result.exit_code

    if args.command == "status":
        store = StateStore(Path(args.state_root) / "broker")
        snap = store.load()
        print(json.dumps({"state": snap.to_dict(), "known_good": store.known_good()}, ensure_ascii=False, indent=2))
        return EXIT_PASS

    if args.command == "gates":
        results = run_gates(repo, include_frontend=not args.no_frontend)
        bad = 0
        for item in results:
            print(f"[{item.status}] {item.name} {item.duration_seconds:.1f}s")
            if item.status != "pass":
                bad += 1
                if item.output_tail:
                    print(item.output_tail)
        return EXIT_PASS if bad == 0 else EXIT_DIFF

    if args.command == "run":
        try:
            policy = load_policy(lock_dir)
        except PolicyError as exc:
            print(f"策略错误：{exc}")
            return EXIT_ARG_IO
        return run_candidate(
            repo,
            Path(args.installer),
            args.version,
            approve=args.approve,
            do_gates=args.gates,
            policy=policy,
            store=StateStore(Path(args.state_root) / "broker"),
            install_dir=Path(args.install_dir) if args.install_dir else None,
            data_dir=Path(args.data_dir) if args.data_dir else None,
            port=args.port or None,
        )

    if args.command == "rollback":
        if not args.approve:
            print("拒绝：回滚需要 Boss 手动确认（--approve）。")
            return EXIT_ARG_IO
        store = StateStore(Path(args.state_root) / "broker")
        known = store.known_good()
        if not known:
            print("没有 known-good 记录。")
            return EXIT_DIFF
        policy = load_policy(lock_dir)
        install_dir = Path(args.install_dir) if args.install_dir else Path(policy.target_install_dir)
        data_dir = Path(args.data_dir) if args.data_dir else Path(policy.target_data_dir)
        result = rollback_to_known_good(
            repo,
            known,
            install_dir,
            data_dir,
            args.port or policy.smoke_port,
            paths.evidence_dir() / "manual-rollback",
        )
        print(f"{'PASS' if result.ok else 'FAIL'} {result.detail}")
        return EXIT_PASS if result.ok else EXIT_DIFF

    if args.command == "rescue":
        store = StateStore(Path(args.state_root) / "broker")
        known = store.known_good()
        if not known:
            print("没有 known-good 记录，无法生成救援。")
            return EXIT_DIFF
        policy = load_policy(lock_dir)
        cmd = rescue.write_rescue(
            known,
            Path(policy.target_install_dir),
            Path(policy.target_data_dir),
        )
        print(f"救援启动器：{cmd}")
        if args.launch:
            rescue.launch(cmd)
        return EXIT_PASS

    if args.command == "unlock":
        if not args.approve:
            print("拒绝：解锁需要 Boss 手动确认（--approve）。")
            return EXIT_ARG_IO
        store = StateStore(Path(args.state_root) / "broker")
        snap = store.load()
        store.unlock(snap, args.reason)
        print(f"已解锁：{args.reason}")
        return EXIT_PASS

    if args.command == "request":
        store = StateStore(Path(args.state_root) / "broker")
        path = save_request(
            store,
            {
                "installer": args.installer,
                "version": args.version,
                "reason": args.reason,
                "source": "python -m src.selfupgrade request",
            },
        )
        print(f"请求已写入：{path}")
        return EXIT_PASS

    if args.command == "package":
        from .package import run_package

        return run_package(
            repo,
            args.version,
            Path(args.out_root),
            staging_root=Path(args.state_root) / "staging" / args.version,
            dry_run=not args.allow_real,
            allow_real=args.allow_real,
        )

    parser.error(f"未知命令 {args.command}")
    return EXIT_ARG_IO


if __name__ == "__main__":
    sys.exit(main())
