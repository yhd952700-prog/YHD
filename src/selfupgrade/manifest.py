"""release-lock 完整性校验：MANIFEST / CURRENT / STAMP-LOG / 逐文件哈希。

失败输出必须能回答四个问题：哪个文件、期望哈希、实际哈希、改了几行哪几行。
「期望内容」优先从 ``MANIFEST.source_commit`` 用 ``git show`` 取回；取不到时
退化为只报哈希与行数差异，并标 ``diff_unavailable``。
"""

from __future__ import annotations

import difflib
import hashlib
import json
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .models import (
    EXIT_ARG_IO,
    EXIT_DIFF,
    EXIT_NOT_VERIFIED,
    EXIT_PASS,
    FileFailure,
    VerifyResult,
)

VERSION_LINE = re.compile(r'(?m)^(\s*#define\s+AppVersion\s+)"[^"]*"')
MAX_DIFF_LINES = 20


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def canonical_json(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def normalize_content(target: str, mode: str, data: bytes) -> bytes:
    """只对安装器定义做版本行规范化；其余保持原字节。"""
    if mode == "normalized" and target.replace("\\", "/").endswith("installer/liuhao.iss"):
        text = data.decode("utf-8-sig")
        text = VERSION_LINE.sub(r'\1"@VERSION@"', text)
        return text.encode("utf-8")
    return data


def relaxed_version_test(source: str) -> str:
    """把硬编码版本断言换成「格式 + 跨文件一致」，并补 console package 断言。

    这是 ``tests/test_version_consistency.py`` 盖章副本的派生规则：仓库原件不改，
    自升级在 staging 里用这份副本，升版后不会自己挡住自己。
    """
    out = re.sub(r'(?m)^\s*assert APP_VERSION == "[^"]+"\s*$', "", source)
    extra = '''

def test_console_package_version_matches():
    """控制台子包版本也必须跟真源一致（原测试漏了这份）。"""
    assert json.loads(_read(REPO / "apps" / "console" / "console" / "package.json"))["version"] == APP_VERSION
'''
    if "test_console_package_version_matches" not in out:
        out = out.rstrip() + "\n" + extra
    return out


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        check=False,
    )


def git_show(repo: Path, commit: str, target: str) -> bytes | None:
    proc = _git(repo, "show", f"{commit}:{target}")
    return proc.stdout if proc.returncode == 0 else None


def _diff_report(expected: bytes, actual: bytes) -> tuple[int, int, list[str]]:
    exp = expected.decode("utf-8", "replace").splitlines()
    act = actual.decode("utf-8", "replace").splitlines()
    diff = list(
        difflib.unified_diff(exp, act, fromfile="stamped", tofile="actual", lineterm="")
    )
    added = sum(1 for line in diff if line.startswith("+") and not line.startswith("+++"))
    removed = sum(1 for line in diff if line.startswith("-") and not line.startswith("---"))
    body = [line for line in diff[2:] if not line.startswith("@@")]
    return added, removed, body[:MAX_DIFF_LINES]


def verify_manifest(
    repo: Path,
    lock_dir: Path,
    *,
    check_source: bool = False,
) -> VerifyResult:
    repo = Path(repo)
    lock_dir = Path(lock_dir)

    manifest_path = lock_dir / "MANIFEST.json"
    current_path = lock_dir / "CURRENT"
    log_path = lock_dir / "STAMP-LOG.jsonl"
    files_path = lock_dir / "FILES.json"

    for path in (manifest_path, current_path, log_path, files_path):
        if not path.is_file():
            return VerifyResult(
                ok=False,
                exit_code=EXIT_ARG_IO,
                problems=[f"缺少 {path}"],
                checked_at=utc_now(),
            )

    manifest_bytes = manifest_path.read_bytes()
    manifest = json.loads(manifest_bytes.decode("utf-8-sig"))
    result = VerifyResult(
        ok=True,
        exit_code=EXIT_PASS,
        seq=manifest.get("seq"),
        manifest_sha256=sha256_bytes(manifest_bytes),
        checked_at=utc_now(),
    )

    # 1) CURRENT 与 MANIFEST 对得上
    current = read_json(current_path)
    if current.get("seq") != manifest.get("seq"):
        result.problems.append(
            f"CURRENT.seq={current.get('seq')} 与 MANIFEST.seq={manifest.get('seq')} 不一致"
        )
    if current.get("manifest_sha256") != result.manifest_sha256:
        result.problems.append(
            "CURRENT.manifest_sha256 与实际 MANIFEST.json 哈希不一致"
            f"（期望 {current.get('manifest_sha256')}，实际 {result.manifest_sha256}）"
        )

    # 2) STAMP-LOG 哈希链
    prev = ""
    last_seq = None
    lines = log_path.read_text(encoding="utf-8-sig").splitlines()
    for idx, raw in enumerate(lines, 1):
        if not raw.strip():
            continue
        try:
            obj = json.loads(raw)
        except json.JSONDecodeError as exc:
            result.problems.append(f"STAMP-LOG 第 {idx} 行不是合法 JSON：{exc}")
            break
        recorded = obj.pop("line_sha256", "")
        recomputed = sha256_bytes(canonical_json(obj).encode("utf-8"))
        if recorded != recomputed:
            result.problems.append(
                f"STAMP-LOG 第 {idx} 行被改动（记录 {recorded}，重算 {recomputed}）"
            )
        if obj.get("prev") != prev:
            result.problems.append(f"STAMP-LOG 第 {idx} 行 prev 与上一行不衔接（链断）")
        prev = recorded or recomputed
        last_seq = obj.get("seq")
    if last_seq != manifest.get("seq"):
        result.problems.append(
            f"STAMP-LOG 链尾 seq={last_seq} 与 MANIFEST.seq={manifest.get('seq')} 不一致"
        )

    # 3) 逐文件哈希
    files_spec = read_json(files_path)
    modes = {entry["target"]: entry.get("mode", "exact") for entry in files_spec.get("files", [])}
    entries = manifest.get("entries", [])
    if not entries:
        result.problems.append("MANIFEST.entries 为空")

    for entry in entries:
        target = entry.get("target", "")
        mode = entry.get("mode") or modes.get(target, "exact")
        path = lock_dir / target
        if not path.is_file():
            result.problems.append(f"缺少盖章副本 {target}")
            continue
        actual_raw = path.read_bytes()
        actual = normalize_content(target, mode, actual_raw)
        actual_sha = sha256_bytes(actual)
        expected_sha = entry.get("sha256", "")
        if actual_sha == expected_sha:
            continue

        failure = FileFailure(
            target=target,
            expected_sha256=expected_sha,
            actual_sha256=actual_sha,
        )
        expected_raw = git_show(repo, str(manifest.get("source_commit", "")), target)
        if expected_raw is not None:
            expected = normalize_content(target, mode, expected_raw)
            failure.added, failure.removed, failure.changed_lines = _diff_report(expected, actual)
        else:
            failure.diff_unavailable = True
        result.failures.append(failure)
        result.problems.append(
            f"{target} 哈希不一致（期望 {expected_sha[:12]}…，实际 {actual_sha[:12]}…）"
        )

    # 4) 可选：源码漂移（提醒 Boss 换章，不阻塞）
    if check_source:
        for entry in entries:
            target = entry.get("target", "")
            mode = entry.get("mode") or modes.get(target, "exact")
            source = entry.get("source")
            if not source or mode == "derived":
                continue
            source_path = repo / source
            if not source_path.is_file():
                result.source_drift.append(f"{source}（源码缺失）")
                continue
            source_sha = sha256_bytes(normalize_content(target, mode, source_path.read_bytes()))
            if source_sha != entry.get("sha256"):
                result.source_drift.append(f"{source} 与盖章副本不一致（该换章了）")

    if result.problems:
        result.ok = False
        result.exit_code = EXIT_DIFF
    elif result.source_drift:
        # 漂移只提醒，不判 FAIL；但让调用方知道不是「完全一致」。
        result.exit_code = EXIT_NOT_VERIFIED
    return result
