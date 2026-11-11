"""Boss 手动盖章：复制受保护文件、算哈希、写 MANIFEST/CURRENT/STAMP-LOG。

只由 Boss 手动执行，不做自动同步。必须显式 ``--approve``。
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from src.selfupgrade.manifest import (  # noqa: E402
    canonical_json,
    normalize_content,
    relaxed_version_test,
    sha256_bytes,
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _git(repo: Path, *args: str) -> str:
    proc = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, encoding="utf-8")
    return proc.stdout.strip() if proc.returncode == 0 else ""


def _render(entry: dict, source_bytes: bytes, target: str) -> bytes:
    mode = entry.get("mode", "exact")
    if mode == "derived" and target.endswith("tests/test_version_consistency.py"):
        text = source_bytes.decode("utf-8-sig")
        return relaxed_version_test(text).encode("utf-8")
    return normalize_content(target, mode, source_bytes)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", default=str(REPO))
    parser.add_argument("--reason", required=True)
    parser.add_argument("--approve", action="store_true")
    parser.add_argument("--bootstrap", action="store_true", help="首次建账：允许源码未提交")
    parser.add_argument("--no-copy", action="store_true", help="只重算哈希，不复制源码")
    parser.add_argument("--commit", action="store_true", help="盖章后 git commit release-lock")
    args = parser.parse_args(argv)

    if not args.approve:
        print("拒绝：盖章必须 Boss 手动确认（--approve）。")
        return 2

    repo = Path(args.repo)
    lock = repo / "release-lock"
    spec = json.loads((lock / "FILES.json").read_text(encoding="utf-8-sig"))
    manifest_path = lock / "MANIFEST.json"
    previous = json.loads(manifest_path.read_text(encoding="utf-8-sig")) if manifest_path.is_file() else {}
    previous_hashes = {e["target"]: e.get("sha256") for e in previous.get("entries", [])}

    entries = []
    changed = []
    for entry in spec["files"]:
        target = entry["target"]
        source = repo / entry["source"]
        dest = lock / target
        if not args.no_copy:
            if not source.is_file():
                print(f"缺少源码 {source}")
                return 2
            data = _render(entry, source.read_bytes(), target)
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(data)
        elif not dest.is_file():
            print(f"缺少盖章副本 {dest}")
            return 2

        data = dest.read_bytes()
        digest = sha256_bytes(data)
        entries.append(
            {
                "target": target,
                "source": entry["source"],
                "mode": entry.get("mode", "exact"),
                "sha256": digest,
                "size": len(data),
            }
        )
        if previous_hashes.get(target) != digest:
            changed.append(target)

    seq = int(previous.get("seq", 0)) + 1
    commit = _git(repo, "rev-parse", "HEAD") or ("WORKTREE" if args.bootstrap else "")
    if not commit:
        print("无法读取 HEAD；首次建账请加 --bootstrap。")
        return 3
    author = _git(repo, "log", "-1", "--format=%an") or "unknown"

    manifest = {
        "schema": "liuhao.release-lock/1",
        "seq": seq,
        "source_commit": commit,
        "git_author": author,
        "stamped_at": _utc_now(),
        "reason": args.reason,
        "entries": entries,
    }
    manifest_bytes = (json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
    manifest_path.write_bytes(manifest_bytes)
    manifest_sha = sha256_bytes(manifest_bytes)
    (lock / "CURRENT").write_text(
        json.dumps({"seq": seq, "manifest_sha256": manifest_sha}, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    log_path = lock / "STAMP-LOG.jsonl"
    prev = ""
    if log_path.is_file():
        lines = [ln for ln in log_path.read_text(encoding="utf-8-sig").splitlines() if ln.strip()]
        if lines:
            prev = json.loads(lines[-1]).get("line_sha256", "")
    record = {
        "seq": seq,
        "prev": prev,
        "manifest_sha256": manifest_sha,
        "source_commit": commit,
        "git_author": author,
        "stamped_at": manifest["stamped_at"],
        "changed": changed,
        "reason": args.reason,
    }
    record["line_sha256"] = sha256_bytes(canonical_json(record).encode("utf-8"))
    with open(log_path, "a", encoding="utf-8") as handle:
        handle.write(canonical_json(record) + "\n")

    if args.commit:
        subprocess.run(["git", "-C", str(repo), "add", "release-lock"], check=False)
        subprocess.run(
            ["git", "-C", str(repo), "commit", "-m", f"lock: seq {seq} ({args.reason})"],
            check=False,
        )

    print(f"PASS seq={seq} manifest={manifest_sha[:12]}… changed={len(changed)}")
    for item in changed:
        print(f"  ~ {item}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
