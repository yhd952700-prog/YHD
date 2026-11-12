#!/usr/bin/env python3
"""发布页换版器：按 FREEZE-RECORD.json 批量替换版本 / SHA256 / 字节 / MB / 发布日期。

发布页是手写静态 HTML，换一个版本要改 26 处（版本 15、SHA256 4、千分位字节 2、
裸字节 1、MB 3、发布日期 1）。手工改容易漏，这个脚本把「取值 -> 替换 -> 自检 ->
调 verify_release_page_values.py 复核」串成一条命令，替换前先核对旧页面的取值
个数，口径不符就拒绝写入，避免替换范围比现实更宽或更窄。

用法::

    python scripts/bump_release_page.py --record FREEZE-RECORD.json \\
        --page index.html --out index.html

    # 没有 FREEZE-RECORD.json 时（例如 0.1.1 历史产物）用显式取值：
    python scripts/bump_release_page.py --page index.html --out /tmp/out.html \\
        --version 0.1.1 --sha256 <64 位十六进制> --size 29567136 --release-date 2026-11-08

退出码：0 = 替换完成且复核 PASS；1 = 自检或复核 FAIL；2 = 参数 / 记录 / 日期不可用。

MB 口径：页面历史值 28.20 是 size/2**20（MiB），不是 size/1e6。默认
``--mb-mode preserve`` 从现页面反推口径，保证 0.1.1 跑出来逐字节相同；要改成
十进制 MB 用 ``--mb-mode decimal``（会把 28.20 改成 29.57，与历史页不同）。

发布日期：``verify_release_page_values.py`` 完全不检查日期，缺日期它照样 PASS。
本脚本要求日期必须能确定（``--release-date``、记录里的 ``release_date``、或
``built_at`` 的日期部分），拿不到直接退出 2 并且不写输出文件。
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

# 与 verify_release_page_values.py 保持同一套匹配口径。
# 三段或四段（0.1.2 / 0.1.2.1）；前面的 / 与后面的 :端口 用来排除 http://127.0.0.1:8080。
VERSION_RE = re.compile(r"(?<![\d./])\d+\.\d+\.\d+(?:\.\d+)?(?!\d)(?!\.\d)(?!:\d)")
SHA_RE = re.compile(r"\b[a-fA-F0-9]{64}\b")
EXE_RE = re.compile(r"Jarvis-Setup-([0-9][0-9A-Za-z.\-]*)\.exe", re.IGNORECASE)
GROUPED_BYTE_RE = re.compile(r"\b\d{1,3}(?:,\d{3}){2,}\b(?=\s*字节)")
BARE_BYTE_RE = re.compile(r"(?<![=\w./-])\d{7,9}(?!\d)")
MB_RE = re.compile(r"(\d+(?:\.\d+)?)\s*MB\b", re.IGNORECASE)
DATE_RE = re.compile(r'(发布日期</span><span class="v">)(\d{4}-\d{2}-\d{2})(</span>)')
DATE_ONLY_RE = re.compile(r"\d{4}-\d{2}-\d{2}")

# 页面已知的分布，替换前后都必须严格等于这些个数。
EXPECTED_COUNTS = {"版本": 15, "SHA256": 4, "千分位字节": 2, "裸字节": 1, "MB": 3, "发布日期": 1}


class BumpError(Exception):
    """参数、记录或页面结构不可用时抛出，退出码 2。"""


def _fail(message: str) -> None:
    raise BumpError(message)


def load_expected(args) -> dict:
    if args.record:
        try:
            data = json.loads(Path(args.record).read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError) as exc:
            _fail(f"读不到出包记录 {args.record}：{exc}")
        inst = data.get("installer") or data
        version = str(data.get("version") or inst.get("version") or "").strip()
        if not version:
            found = EXE_RE.search(str(inst.get("filename") or ""))
            version = found.group(1) if found else ""
        sha = str(inst.get("sha256") or data.get("setup_sha256") or "").strip()
        size_raw = inst.get("size", data.get("setup_bytes"))
        if size_raw is None:
            _fail("记录里缺少 installer.size")
        size = int(str(size_raw).replace(",", "").strip())
        if args.release_date:
            date, date_source = args.release_date, "--release-date"
        elif data.get("release_date"):
            date, date_source = str(data["release_date"]).strip()[:10], "记录 release_date"
        elif data.get("built_at"):
            date, date_source = str(data["built_at"]).strip()[:10], "记录 built_at"
        else:
            date, date_source = "", "记录无 built_at/release_date"
        record = args.record
    else:
        if not (args.version and args.sha256 and args.size is not None):
            _fail("需要 --record，或同时提供 --version / --sha256 / --size")
        version, sha, size = args.version, args.sha256, int(args.size)
        date, date_source = args.release_date or "", "--release-date"
        record = None

    if not VERSION_RE.fullmatch(version or ""):
        _fail(f"版本号格式不对：{version!r}")
    if not SHA_RE.fullmatch(sha or ""):
        _fail("sha256 必须是 64 位十六进制")
    if size <= 0:
        _fail(f"字节数不合法：{size}")
    if not DATE_ONLY_RE.fullmatch(date or ""):
        _fail(
            "拿不到可用的发布日期：需要 --release-date YYYY-MM-DD，"
            "或记录里带 release_date / built_at。"
            "verify_release_page_values.py 不检查日期，不能靠它兜底，因此这里拒绝换版。"
        )
    return {
        "record": record,
        "version": version,
        "sha256": sha.lower(),
        "size": size,
        "date": date,
        "date_source": date_source,
    }


def package_mb_matches(text: str) -> list[re.Match[str]]:
    """只取描述安装包体积的 MB，跳过磁盘空间这类数字（与复核脚本同一判据）。"""
    picked = []
    for match in MB_RE.finditer(text):
        window = text[max(0, match.start() - 60): match.end() + 60]
        if any(word in window for word in ("字节", "安装包", "体积")):
            picked.append(match)
    return picked


def detect_old(text: str) -> dict:
    versions = sorted(set(VERSION_RE.findall(text)))
    if len(versions) != 1:
        _fail(f"页面里的版本号不止一种或找不到：{versions}；先人工确认基线")
    shas = sorted({m.group(0) for m in SHA_RE.finditer(text)})
    if len(shas) != 1:
        _fail(f"页面里的 SHA256 不止一种或找不到：{shas}")
    grouped = GROUPED_BYTE_RE.findall(text)
    bare = BARE_BYTE_RE.findall(text)
    if len(grouped) != EXPECTED_COUNTS["千分位字节"] or len(set(grouped)) != 1:
        _fail(f"千分位字节应有 {EXPECTED_COUNTS['千分位字节']} 处且同值，实际 {grouped}")
    if len(bare) != EXPECTED_COUNTS["裸字节"]:
        _fail(f"裸字节应有 {EXPECTED_COUNTS['裸字节']} 处，实际 {bare}")
    size = int(bare[0])
    if int(grouped[0].replace(",", "")) != size:
        _fail(f"千分位字节 {grouped[0]} 与裸字节 {size} 不是同一个数")
    mbs = package_mb_matches(text)
    if len(mbs) != EXPECTED_COUNTS["MB"]:
        _fail(f"安装包 MB 应有 {EXPECTED_COUNTS['MB']} 处，实际 {len(mbs)} 处")
    values = {m.group(1) for m in mbs}
    if len(values) != 1:
        _fail(f"安装包 MB 取值不唯一：{sorted(values)}")
    mp = DATE_RE.search(text)
    if not mp:
        _fail("找不到「发布日期」字段")
    return {
        "version": versions[0],
        "sha256": shas[0],
        "size": size,
        "grouped": grouped[0],
        "bare": bare[0],
        "mb": mbs[0].group(1),
        "date": mp.group(2),
    }


def resolve_mb_mode(args, old: dict) -> str:
    if args.mb_mode != "preserve":
        return args.mb_mode
    mib = f"{old['size'] / 1024 ** 2:.2f}"
    dec = f"{old['size'] / 1e6:.2f}"
    if old["mb"] == mib and old["mb"] != dec:
        return "mib"
    if old["mb"] == dec and old["mb"] != mib:
        return "decimal"
    _fail(
        f"无法从页面反推 MB 口径（页面 {old['mb']}，MiB {mib}，十进制 MB {dec}）；"
        "请显式指定 --mb-mode mib|decimal"
    )


def count_new(text: str) -> dict:
    versions = VERSION_RE.findall(text)
    shas = [m.group(0) for m in SHA_RE.finditer(text)]
    grouped = GROUPED_BYTE_RE.findall(text)
    bare = BARE_BYTE_RE.findall(text)
    mbs = [m.group(1) for m in package_mb_matches(text)]
    mp = DATE_RE.search(text)
    return {
        "版本": versions,
        "SHA256": shas,
        "千分位字节": grouped,
        "裸字节": bare,
        "MB": mbs,
        "发布日期": [mp.group(2)] if mp else [],
    }


def self_check(text: str, expected: dict, old: dict) -> list[str]:
    problems = []
    actual = count_new(text)
    wanted = {
        "版本": [expected["version"]] * EXPECTED_COUNTS["版本"],
        "SHA256": [expected["sha256"].upper()] * EXPECTED_COUNTS["SHA256"],
        "千分位字节": [f"{expected['size']:,}"] * EXPECTED_COUNTS["千分位字节"],
        "裸字节": [str(expected["size"])] * EXPECTED_COUNTS["裸字节"],
        "MB": [expected["mb"]] * EXPECTED_COUNTS["MB"],
        "发布日期": [expected["date"]] * EXPECTED_COUNTS["发布日期"],
    }
    for name, want in wanted.items():
        got = actual[name]
        if got != want:
            problems.append(f"{name}：期望 {len(want)} 处 {want[0]!r}，实际 {len(got)} 处 {got}")
    residue = []
    if old["version"] != expected["version"] and old["version"] in actual["版本"]:
        residue.append(f"旧版本 {old['version']}")
    if old["sha256"].lower() != expected["sha256"] and \
            old["sha256"].lower() in [value.lower() for value in actual["SHA256"]]:
        residue.append(f"旧 SHA256 {old['sha256'][:8]}…")
    if old["size"] != expected["size"]:
        if old["grouped"] in actual["千分位字节"]:
            residue.append(f"旧千分位字节 {old['grouped']}")
        if old["bare"] in actual["裸字节"]:
            residue.append(f"旧裸字节 {old['bare']}")
    if old["date"] != expected["date"] and old["date"] in actual["发布日期"]:
        residue.append(f"旧发布日期 {old['date']}")
    for item in residue:
        problems.append(f"残留{item}")
    return problems


def replace_all(text: str, expected: dict, old: dict) -> str:
    def mb_sub(match: re.Match[str]) -> str:
        window = text[max(0, match.start() - 60): match.end() + 60]
        if not any(word in window for word in ("字节", "安装包", "体积")):
            return match.group(0)
        return match.group(0).replace(match.group(1), expected["mb"], 1)

    text = DATE_RE.sub(lambda m: m.group(1) + expected["date"] + m.group(3), text, count=1)
    text = MB_RE.sub(mb_sub, text)
    text = VERSION_RE.sub(expected["version"], text)
    text = SHA_RE.sub(expected["sha256"].upper(), text)
    text = GROUPED_BYTE_RE.sub(f"{expected['size']:,}", text)
    text = BARE_BYTE_RE.sub(str(expected["size"]), text)
    return text


def run_verify(expected: dict, out: Path, verify_script: Path) -> int:
    if not verify_script.is_file():
        print(f"[ERROR] 找不到复核脚本：{verify_script}", file=sys.stderr)
        return 2
    command = [sys.executable, str(verify_script), "--page", str(out)]
    if expected["record"]:
        command += ["--record", expected["record"]]
    else:
        command += [
            "--version", expected["version"],
            "--sha256", expected["sha256"],
            "--size", str(expected["size"]),
        ]
    print(f"复核命令：" + " ".join(command))
    sys.stdout.flush()
    return subprocess.call(command)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="按出包记录批量替换发布页里的版本/哈希/体积/日期")
    parser.add_argument("--page", required=True, help="现有发布页 HTML")
    parser.add_argument("--out", required=True, help="输出文件（可与 --page 相同）")
    parser.add_argument("--record", help="FREEZE-RECORD.json")
    parser.add_argument("--version", help="无记录时的期望版本")
    parser.add_argument("--sha256", help="无记录时的期望 SHA256")
    parser.add_argument("--size", type=int, help="无记录时的期望字节数")
    parser.add_argument("--release-date", help="发布日期 YYYY-MM-DD；优先于记录里的日期")
    parser.add_argument(
        "--mb-mode",
        choices=("preserve", "mib", "decimal"),
        default="preserve",
        help="MB 计算口径：preserve=跟随现页面（默认），mib=size/2^20，decimal=size/1e6",
    )
    parser.add_argument("--verify-script", help="复核脚本路径（默认取同目录 verify_release_page_values.py）")
    args = parser.parse_args(argv)

    try:
        expected = load_expected(args)
        source = Path(args.page)
        try:
            text = source.read_bytes().decode("utf-8")
        except OSError as exc:
            _fail(f"读不到页面 {args.page}：{exc}")
        except UnicodeDecodeError as exc:
            _fail(f"页面不是 UTF-8：{exc}")
        old = detect_old(text)
        if expected["date"] < old["date"]:
            _fail(f"发布日期倒退：新 {expected['date']} 早于页面现有 {old['date']}")
        mode = resolve_mb_mode(args, old)
        expected["mb"] = f"{expected['size'] / (1024 ** 2 if mode == 'mib' else 1e6):.2f}"
    except BumpError as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 2

    print(f"基线页面：{source}（{len(text.encode('utf-8'))} B）")
    print(
        "旧值：版本 {version} / SHA {sha:.8}… / {size:,} B（{grouped} / 裸 {bare}）/ {mb} MB / {date}".format(
            sha=old["sha256"], **old
        )
    )
    print(
        "新值：版本 {version} / SHA {sha:.8}… / {size:,} B / {mb} MB（{mode}）/ {date}（{src}）".format(
            sha=expected["sha256"], mode=mode, src=expected["date_source"], **expected
        )
    )
    if all(old[key] == expected[key] for key in ("version", "size", "date")) and \
            old["sha256"].lower() == expected["sha256"]:
        print("取值与页面完全相同：本次是自洽性重放，输出应与输入逐字节相同。")

    updated = replace_all(text, expected, old)
    problems = self_check(updated, expected, old)
    counts = count_new(updated)
    print("替换计数：" + " / ".join(f"{name} {len(values)} 处" for name, values in counts.items()))
    if problems:
        for problem in problems:
            print(f"[FAIL] {problem}")
        print("RESULT: SELF-CHECK FAIL（未写输出）")
        return 1

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(updated.encode("utf-8"))
    print(f"已写入：{out}（{len(updated.encode('utf-8'))} B）")

    verify_script = (
        Path(args.verify_script)
        if args.verify_script
        else Path(__file__).resolve().parent / "verify_release_page_values.py"
    )
    code = run_verify(expected, out, verify_script)
    if code != 0:
        print(f"RESULT: VERIFY FAIL（复核脚本退出码 {code}）")
        return 1
    print("RESULT: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
