#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""发布来源唯一性门禁：只允许从 dist\\installer 顶层发布。

回答一个具体问题：如果有人从 dist\\_archive（或 brand-verify / encpath /
任何快照目录）拿了个旧包去发布，这个脚本能不能拦住？——能，三条硬规则：

  1) 来源目录必须是 <repo>\\dist\\installer 顶层；归档/快照目录直接 FAIL；
  2) 待发布 exe 的文件名在 dist 下（排除 *_archive*）不得有第二个不同哈希的副本；
  3) 待发布 exe 的字节/SHA256 必须与同目录 SHA256SUMS.txt 首行一致；
     有 FREEZE-RECORD.json 时，记录里的 filename/version/size/sha256 也必须一致。

可选 --page 时，再调用 scripts/verify_release_page_values.py 核对发布页，
页面上的版本/哈希/字节/文件名/data-copy 全部必须等于本目录的实测值。

用法：
  python scripts/release/verify_publish_source.py \
      --repo-root . --installer-dir dist/installer \
      --page release-page.html --expected-version 0.1.2

退出码：0=通过；1=有违规（拒绝发布）；2=参数或 IO 错误。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

SHA256SUMS_RE = re.compile(r"^(?P<hash>[0-9A-Fa-f]{64})\s+\*?(?P<name>\S.*?)\s*$")

# 路径段命中即视为「归档目录」：可以存在，但绝不能作为发布来源，也不参与同名冲突计数。
ARCHIVED_PARTS = {"_archive"}

# 目录名命中即视为「历史快照」：同上，禁止作为来源。
SNAPSHOT_PREFIXES = (
    "staging-",
    "precheck-",
    "candidate-",
    "final-",
    "nonfinal-build-",
    "brand-verify",
    "encpath",
    "obsolete",
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def display(repo_root: Path, path: Path) -> str:
    try:
        return str(path.resolve().relative_to(repo_root.resolve())).replace("/", "\\")
    except ValueError:
        return str(path)


def is_archived(path: Path) -> bool:
    return any(part.lower() in ARCHIVED_PARTS for part in path.parts)


def snapshot_hint(path: Path) -> str:
    for part in path.parts:
        low = part.lower()
        if low in ARCHIVED_PARTS:
            return part
        for prefix in SNAPSHOT_PREFIXES:
            if low.startswith(prefix):
                return part
    return ""


def parse_sums(sums_path: Path) -> tuple[str, str]:
    """返回 SHA256SUMS.txt 首行的 (hash, filename)。"""
    for raw in sums_path.read_text(encoding="utf-8-sig", errors="replace").splitlines():
        line = raw.strip()
        if not line:
            continue
        match = SHA256SUMS_RE.match(line)
        if not match:
            raise ValueError(f"SHA256SUMS.txt 首行格式不对：{line!r}")
        return match.group("hash").upper(), match.group("name")
    raise ValueError("SHA256SUMS.txt 是空的")


class Report:
    def __init__(self) -> None:
        self.failures: list[str] = []
        self.warnings: list[str] = []

    def ok(self, message: str) -> None:
        print(f"[OK] {message}")

    def fail(self, message: str) -> None:
        self.failures.append(message)
        print(f"[FAIL] {message}")

    def warn(self, message: str) -> None:
        self.warnings.append(message)
        print(f"[WARN] {message}")


def check_source_dir(repo_root: Path, installer_dir: Path, staging: bool, report: Report) -> None:
    canonical = (repo_root / "dist" / "installer").resolve()
    actual = installer_dir.resolve()
    hint = snapshot_hint(installer_dir)

    if hint:
        report.fail(
            f"数值来源命中归档/快照目录「{hint}」：{display(repo_root, actual)}；"
            "只允许 dist\\installer 顶层"
        )
        return

    if actual == canonical:
        report.ok(f"来源目录 {display(repo_root, actual)}（dist\\installer 顶层）")
        return

    if staging:
        if actual.name.lower() != "installer":
            report.fail(f"暂存目录必须叫 installer：{display(repo_root, actual)}")
        else:
            report.warn(
                f"暂存目录 {display(repo_root, actual)}：本次只是构建，不是发布；"
                "发布前必须对 dist\\installer 再跑一次本脚本"
            )
        return

    report.fail(
        f"数值来源不是 dist\\installer 顶层：{display(repo_root, actual)}"
        f"（期望 {display(repo_root, canonical)}）"
    )


def check_duplicates(repo_root: Path, exe_name: str, exe_hash: str, exe_size: int, report: Report) -> None:
    dist = repo_root / "dist"
    if not dist.is_dir():
        report.fail(f"找不到 dist 目录：{dist}")
        return

    live: list[Path] = []
    archived = 0
    groups: dict[str, set[str]] = {}
    for candidate in dist.rglob("LiuHao-Setup-*.exe"):
        if not candidate.is_file():
            continue
        if is_archived(candidate):
            archived += 1
            continue
        groups.setdefault(candidate.name.lower(), set()).add(sha256_file(candidate))
        if candidate.name.lower() == exe_name.lower():
            live.append(candidate)

    others = {name: hashes for name, hashes in groups.items() if len(hashes) > 1 and name != exe_name.lower()}
    print(
        f"扫描：dist 下 {sum(len(v) for v in groups.values())} 个非归档 exe、{archived} 个归档 exe；"
        f"同名多哈希 {len(others) + (1 if exe_name.lower() in groups and len(groups[exe_name.lower()]) > 1 else 0)} 组"
    )

    if exe_name.lower() not in groups:
        report.fail(f"待发布 exe 不在 dist 下：{exe_name}")
        return

    by_hash: dict[str, list[Path]] = {}
    for candidate in live:
        by_hash.setdefault(sha256_file(candidate), []).append(candidate)

    if len(by_hash) > 1:
        detail = "；".join(
            f"{display(repo_root, p)}（{p.stat().st_size} / {h[:8]}…{h[-4:]}）"
            for h, paths in sorted(by_hash.items())
            for p in sorted(paths)
        )
        report.fail(f"同名 exe 在 dist 下存在 {len(by_hash)} 个不同哈希（拿错文件的风险）：{detail}")
        return

    if len(live) > 1:
        report.warn(f"{exe_name} 有 {len(live)} 份同名同哈希副本（无害，建议去重）")
    report.ok(f"同名 exe 唯一：{exe_name} 仅 1 个哈希（{exe_size} 字节 / {exe_hash[:8]}…{exe_hash[-4:]}）")

    for name, hashes in sorted(others.items()):
        report.warn(f"其他同名多哈希（与本次发布无关，建议归档）：{name} 有 {len(hashes)} 个不同哈希")


def check_sums(installer_dir: Path, exe_path: Path, report: Report) -> tuple[str, str, int]:
    sums_path = installer_dir / "SHA256SUMS.txt"
    if not sums_path.is_file():
        raise FileNotFoundError(f"缺少 {sums_path}")
    sums_hash, sums_name = parse_sums(sums_path)
    if sums_name.lower() != exe_path.name.lower():
        raise ValueError(f"SHA256SUMS.txt 首行文件名 {sums_name} ≠ 待发布 exe {exe_path.name}")
    actual_hash = sha256_file(exe_path)
    actual_size = exe_path.stat().st_size
    if sums_hash != actual_hash:
        report.fail(f"SHA256SUMS 首行 {sums_hash[:8]}… 与实际 exe {actual_hash[:8]}… 不一致")
    else:
        report.ok(f"SHA256SUMS 首行与实际 exe 一致：{actual_hash[:8]}…{actual_hash[-4:]} / {actual_size} 字节")
    return sums_name, sums_hash, actual_size


def check_record(
    installer_dir: Path,
    exe_path: Path,
    exe_hash: str,
    exe_size: int,
    expected_version: str | None,
    require_record: bool,
    report: Report,
) -> str | None:
    record_path = installer_dir / "FREEZE-RECORD.json"
    if not record_path.is_file():
        message = "缺 FREEZE-RECORD.json（0.1.1 历史产物例外；0.1.2 起视为不可发布）"
        if require_record:
            report.fail(message)
        else:
            report.warn(message)
        return None

    data = json.loads(record_path.read_text(encoding="utf-8-sig"))
    inst = data.get("installer") or {}
    version = inst.get("version")
    problems = []
    if inst.get("filename") != exe_path.name:
        problems.append(f"filename={inst.get('filename')} ≠ {exe_path.name}")
    if inst.get("size") != exe_size:
        problems.append(f"size={inst.get('size')} ≠ {exe_size}")
    if str(inst.get("sha256", "")).upper() != exe_hash:
        problems.append(f"sha256={inst.get('sha256')} ≠ {exe_hash}")
    if expected_version and version != expected_version:
        problems.append(f"version={version} ≠ 构建版本 {expected_version}")
    if problems:
        report.fail("FREEZE-RECORD 与实际产物不一致：" + "；".join(problems))
    else:
        report.ok(f"FREEZE-RECORD 与产物一致：version={version}、size={exe_size}、sha256={exe_hash[:8]}…")
    return version


def check_page(
    repo_root: Path,
    page: Path,
    version: str | None,
    exe_hash: str,
    exe_size: int,
    page_script: Path | None,
    report: Report,
) -> None:
    script = page_script or (repo_root / "scripts" / "verify_release_page_values.py")
    if not script.is_file():
        report.fail(f"缺少页面核对脚本 {script}（补丁 0006 未落地），无法核对发布页")
        return
    if not page.is_file():
        report.fail(f"页面文件不存在：{page}")
        return
    if not version:
        report.fail("缺版本号，无法核对页面（请给 --expected-version 或补 FREEZE-RECORD.json）")
        return

    command = [
        sys.executable,
        str(script),
        "--page",
        str(page),
        "--version",
        version,
        "--sha256",
        exe_hash,
        "--size",
        str(exe_size),
    ]
    result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace")
    output = (result.stdout or "").strip()
    if result.stderr.strip():
        output = (output + "\n" + result.stderr.strip()).strip()
    if result.returncode == 0:
        summary = [line for line in output.splitlines() if line.startswith("扫描：")]
        report.ok("发布页数值与产物一致" + (f"（{summary[-1]}）" if summary else ""))
    elif result.returncode == 1:
        print(output)
        report.fail("发布页数值与产物不一致（明细见上）")
    else:
        print(output)
        report.fail(f"页面核对脚本返回 {result.returncode}（参数或文件错误）")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="发布来源唯一性门禁（dist\\installer 顶层 + 同名唯一 + SHA256SUMS 一致）")
    parser.add_argument("--repo-root", default=".", help="仓库根目录")
    parser.add_argument("--installer-dir", default="dist/installer", help="声称的数值来源目录")
    parser.add_argument("--page", help="发布页 HTML（可选；给了就同时核对页面数值）")
    parser.add_argument("--page-script", help="页面核对脚本路径（默认 scripts/verify_release_page_values.py）")
    parser.add_argument("--expected-version", help="本次构建版本；给了就要求 exe/记录与它一致")
    parser.add_argument("--require-record", action="store_true", help="缺 FREEZE-RECORD.json 即失败（发布态应开）")
    parser.add_argument("--staging", action="store_true", help="暂存目录模式：允许非 dist\\installer，但不代表可发布")
    args = parser.parse_args(argv)

    repo_root = Path(args.repo_root).resolve()
    installer_dir = Path(args.installer_dir)
    if not installer_dir.is_absolute():
        installer_dir = (repo_root / installer_dir).resolve()
    if not installer_dir.is_dir():
        print(f"[ERROR] 来源目录不存在：{installer_dir}")
        return 2

    report = Report()
    print(f"仓库：{repo_root}")
    print(f"来源：{installer_dir}")
    print("")

    check_source_dir(repo_root, installer_dir, args.staging, report)

    exe_name = ""
    exe_hash = ""
    exe_size = 0
    exe_path: Path | None = None
    try:
        exe_name = _exe_name_from_sums(installer_dir)
        exe_path = installer_dir / exe_name
        if not exe_path.is_file():
            raise FileNotFoundError(f"待发布 exe 不存在：{exe_path}")
        exe_name, exe_hash, exe_size = check_sums(installer_dir, exe_path, report)
    except (FileNotFoundError, ValueError) as exc:
        report.fail(str(exc))
        exe_path = None

    if exe_path is not None and exe_path.is_file():
        check_duplicates(repo_root, exe_name, exe_hash, exe_size, report)
        version = check_record(
            installer_dir,
            exe_path,
            exe_hash,
            exe_size,
            args.expected_version,
            args.require_record and not args.staging,
            report,
        )
        if args.page:
            effective_version = args.expected_version or version or _version_from_name(exe_name)
            check_page(
                repo_root,
                Path(args.page),
                effective_version,
                exe_hash,
                exe_size,
                Path(args.page_script) if args.page_script else None,
                report,
            )

    print("")
    if report.failures:
        print(f"RESULT: FAIL（{len(report.failures)} 项）— 拒绝发布")
        return 1
    print("RESULT: PASS" + ("（暂存构建，不是发布）" if args.staging else ""))
    return 0


def _version_from_name(exe_name: str) -> str | None:
    match = re.search(r"-(\d+\.\d+\.\d+)\.exe$", exe_name, re.IGNORECASE)
    return match.group(1) if match else None


def _exe_name_from_sums(installer_dir: Path) -> str:
    sums_path = installer_dir / "SHA256SUMS.txt"
    if not sums_path.is_file():
        raise FileNotFoundError(f"缺少 SHA256SUMS.txt：{sums_path}")
    _, name = parse_sums(sums_path)
    return name


if __name__ == "__main__":
    sys.exit(main())
