#!/usr/bin/env python3
"""发布页手写数值核对：版本 / SHA256 / 体积必须与出包记录一致。

发布页是独立的静态 HTML，数值全部手写、没有构建注入。换版时逐处核对容易漏，
这个脚本把页面里所有「会被当成版本 / 哈希 / 字节」的文本与 `FREEZE-RECORD.json`
做一次机械比对，专门盯着最容易漏的三类：下载按钮 href、`data-copy` 属性、裸字节数。

用法::

    python scripts/verify_release_page_values.py --page index.html --record FREEZE-RECORD.json
    python scripts/verify_release_page_values.py --page index.html \
        --version 0.1.1 --sha256 <64 位十六进制> --size 29567136

退出码：0 = 全部一致；1 = 存在差异；2 = 参数或文件错误。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

# 仓库根加入 sys.path：与其余 scripts/verify_*.py 同一约定，
# 由 tests/test_guardrail_scripts.py 对所有 verify_*.py 强制检查。
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# 页面里的版本号（三段 0.1.2 / 四段 0.1.2.1）。不接数字、不接 `.数字`；
# 前面的 `/` 与后面的 `:端口` 用来把 http://127.0.0.1:8080 排除在版本之外；
# 允许 `v0.1.1`（前面是字母）和 `0.1.1.exe`（后面是 .exe）。
VERSION_RE = re.compile(r"(?<![\d./])\d+\.\d+\.\d+(?:\.\d+)?(?!\d)(?!\.\d)(?!:\d)")
VERSION_ONLY_RE = re.compile(r"\d+\.\d+\.\d+(?:\.\d+)?")
SHA_RE = re.compile(r"\b[a-fA-F0-9]{64}\b")
EXE_RE = re.compile(r"LiuHao-Setup-([0-9][0-9A-Za-z.\-]*)\.exe", re.IGNORECASE)
DATA_COPY_RE = re.compile(r'data-copy\s*=\s*"([^"]*)"', re.IGNORECASE)
# 千分位字节必须紧跟「字节」，避免把 rgb(255,255,255) 这类颜色算进来。
GROUPED_BYTE_RE = re.compile(r"\b\d{1,3}(?:,\d{3}){2,}\b(?=\s*字节)")
# 裸字节数；排除 URL 的 LinkId=2124703 这类参数（前面是 = / . / /）。
BARE_BYTE_RE = re.compile(r"(?<![=\w./-])\d{7,9}(?!\d)")
APPROX_MB_RE = re.compile(r"(\d+(?:\.\d+)?)\s*MB\b", re.IGNORECASE)


def _line_no(text: str, pos: int) -> int:
    return text.count("\n", 0, pos) + 1


def _iter(pattern: re.Pattern[str], text: str, group: int = 0):
    for match in pattern.finditer(text):
        yield _line_no(text, match.start()), match.group(group)


def load_expected(args) -> tuple[str, int, str]:
    """从 FREEZE-RECORD.json（installer.*）或三个显式参数取期望值。"""
    if args.record:
        data = json.loads(Path(args.record).read_text(encoding="utf-8-sig"))
        inst = data.get("installer", data)
        version = str(inst.get("version") or "").strip()
        filename = str(inst.get("filename") or "").strip()
        sha = str(inst.get("sha256") or "").strip()
        size_raw = inst.get("size")
        if not version:
            found = EXE_RE.search(filename)
            version = found.group(1) if found else ""
        if size_raw is None:
            raise ValueError("记录里缺少 installer.size")
        size = int(str(size_raw).replace(",", "").strip())
    else:
        if not (args.version and args.sha256 and args.size is not None):
            raise ValueError("需要 --record，或同时提供 --version / --sha256 / --size")
        version, sha, size = args.version, args.sha256, int(args.size)

    if not VERSION_ONLY_RE.fullmatch(version or ""):
        raise ValueError(f"版本号格式不对：{version!r}")
    if not SHA_RE.fullmatch(sha or ""):
        raise ValueError("sha256 必须是 64 位十六进制")
    return version, size, sha.lower()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="核对发布页手写数值与出包记录是否一致")
    parser.add_argument("--page", required=True, help="发布页 HTML 文件")
    parser.add_argument("--record", help="FREEZE-RECORD.json（读取 installer.*）")
    parser.add_argument("--version", help="期望版本（无 --record 时使用）")
    parser.add_argument("--sha256", help="期望 SHA256（无 --record 时使用）")
    parser.add_argument("--size", type=int, help="期望安装包字节数（无 --record 时使用）")
    args = parser.parse_args(argv)

    try:
        text = Path(args.page).read_text(encoding="utf-8-sig")
    except OSError as exc:
        print(f"[ERROR] 读不到页面：{exc}")
        return 2
    try:
        version, size, sha = load_expected(args)
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
        print(f"[ERROR] 期望值不可用：{exc}")
        return 2

    problems: list[str] = []
    warnings: list[str] = []
    counts = {name: 0 for name in ("版本", "SHA256", "文件名", "data-copy", "字节", "近似体积", "其他体积")}

    def add(kind: str, line: int, message: str) -> None:
        problems.append(f"[FAIL] {kind} 第 {line} 行：{message}")

    for line, value in _iter(VERSION_RE, text):
        counts["版本"] += 1
        if value != version:
            add("版本", line, f"{value} != {version}")

    for line, value in _iter(SHA_RE, text):
        counts["SHA256"] += 1
        if value.lower() != sha:
            add("SHA256", line, "页面哈希与出包记录不一致")

    for line, value in _iter(EXE_RE, text, 1):
        counts["文件名"] += 1
        if value != version:
            add("文件名", line, f"LiuHao-Setup-{value}.exe 的版本 != {version}")

    for line, value in _iter(DATA_COPY_RE, text, 1):
        counts["data-copy"] += 1
        stripped = value.strip()
        if SHA_RE.fullmatch(stripped):
            if stripped.lower() != sha:
                add("data-copy", line, "复制按钮里是旧哈希")
            continue
        names = [m.group(1) for m in EXE_RE.finditer(value)]
        if not names:
            add("data-copy", line, "既不是哈希，也不含 LiuHao-Setup-<版本>.exe")
        for name in names:
            if name != version:
                add("data-copy", line, f"复制命令里的文件名版本 {name} != {version}")

    for line, value in _iter(GROUPED_BYTE_RE, text):
        counts["字节"] += 1
        if int(value.replace(",", "")) != size:
            add("字节", line, f"{value} != {size:,}")

    for line, value in _iter(BARE_BYTE_RE, text):
        counts["字节"] += 1
        if int(value) != size:
            add("字节", line, f"裸字节 {value} != {size}")

    mib = size / 1024**2
    decimal_mb = size / 1e6
    for match in APPROX_MB_RE.finditer(text):
        value = match.group(1)
        line = _line_no(text, match.start())
        # 只核对「描述安装包体积」的 MB；磁盘空间（如 400 MB）不是包体积。
        window = text[max(0, match.start() - 60): match.end() + 60]
        if not any(word in window for word in ("字节", "安装包", "体积")):
            counts["其他体积"] += 1
            continue
        counts["近似体积"] += 1
        shown = float(value)
        if abs(shown - mib) <= 0.05:
            warnings.append(
                f"[WARN] 近似体积 第 {line} 行：{value} MB 实际是按 MiB 算的"
                f"（{mib:.2f} MiB / {decimal_mb:.2f} MB），单位标注不准确"
            )
            continue
        if abs(shown - decimal_mb) <= 0.05:
            continue
        add("近似体积", line, f"{value} MB 与记录 {size} 字节不符（{mib:.2f} MiB / {decimal_mb:.2f} MB）")

    for warning in warnings:
        print(warning)
    for problem in problems:
        print(problem)

    summary = " / ".join(f"{name} {count} 处" for name, count in counts.items())
    print(f"扫描：{summary}")
    if problems:
        print(f"RESULT: FAIL（{len(problems)} 处不一致）")
        return 1
    print("RESULT: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
