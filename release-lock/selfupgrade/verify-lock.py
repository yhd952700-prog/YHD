"""出包前校验 release-lock 完整性。退出码：0=PASS，1=有差异，2=参数/IO，3=NOT VERIFIED。"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from src.selfupgrade.manifest import verify_manifest  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", default=str(REPO))
    parser.add_argument("--lock", default="")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--check-source", action="store_true")
    args = parser.parse_args(argv)

    repo = Path(args.repo)
    lock = Path(args.lock) if args.lock else repo / "release-lock"
    result = verify_manifest(repo, lock, check_source=args.check_source)

    if args.json:
        print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
        return result.exit_code

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
    return result.exit_code


if __name__ == "__main__":
    sys.exit(main())
