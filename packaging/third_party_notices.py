#!/usr/bin/env python
"""生成随包分发的 THIRD-PARTY-NOTICES.txt。

为什么要有这个脚本
------------------
旧做法是手工维护 ``packaging/THIRD-PARTY-NOTICES.txt``，于是版本号会漂移：
清单写 ``anthropic==1.4.0 / openai==3.8.0``，实际打进包的是 1.3.0 / 3.13.0；
包里有 ``bcrypt`` / ``google-generativeai``，清单里却没有。

现在改为**以实际打进包的内容为准**：构建时扫描
``<payload>/runtime/Lib/site-packages/*.dist-info`` 生成包清单，
再用 ``packaging/third-party-notices.template.txt`` 的固定说明拼出全文。
手工维护的部分只剩模板里的叙述文字，包名/版本/许可证/文本位置全部现算。

用法::

    # 构建期：把声明写进 payload 程序根目录
    python packaging/third_party_notices.py --app dist/payload/app

    # 校验某个已生成的声明是否与 payload 实装一致（不一致退出码 1）
    python packaging/third_party_notices.py --app dist/payload/app \\
        --check dist/payload/app/THIRD-PARTY-NOTICES.txt

    # 只看会生成什么
    python packaging/third_party_notices.py --app dist/payload/app --stdout
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent
TEMPLATE_FILE = REPO_ROOT / "packaging" / "third-party-notices.template.txt"
REQUIREMENTS_FILE = REPO_ROOT / "requirements.txt"

#: 许可证名里出现这些片段即视为限制性/非宽松许可，构建直接失败。
RESTRICTIVE_MARKERS = (
    "AGPL", "GPL-2", "GPL-3", "GPLv2", "GPLv3", "LGPL",
    "SSPL", "Elastic-2.0", "BSL-1.1", "BUSL", "OpenRAIL", "CC-BY-NC",
)

#: 元数据口径与随包文本不一致、必须按随包文本列示的包。
LICENSE_OVERRIDES: Dict[str, Tuple[str, str]] = {
    "pywin32": ("BSD-3-Clause（随包文本）；见第 5 节说明", "win32/license.txt"),
}

#: 许可证 Classifier → 短名。仅覆盖实际出现的值，未知值原样保留 Classifier 尾部。
CLASSIFIER_ALIASES = {
    "MIT License": "MIT",
    "Apache Software License": "Apache-2.0",
    "BSD License": "BSD-3-Clause",
    "Python Software Foundation License": "PSF-2.0",
    "Mozilla Public License 2.0 (MPL 2.0)": "MPL-2.0",
    "The Unlicense (Unlicense)": "Unlicense",
    "Zope Public License": "ZPL-2.1",
}

#: 包内许可证文本可能的名字（大小写不敏感比较）。
LICENSE_NAME_PATTERN = re.compile(r"^(licen[cs]e|copying|notice)", re.IGNORECASE)

#: 嵌入层（二进制/子包）里的弱 copyleft 组件：顶层 dist 清单看不到，
#: 但 notices 必须单独列示。构建门禁会校验这些条目仍在。
EMBEDDED_LGPL_COMPONENTS: Tuple[str, ...] = ("adodbapi", "iconv")

#: 必须随包的许可证全文（相对 licenses/）。缺失或内容过短即构建失败。
REQUIRED_LICENSE_FILES: Tuple[str, ...] = (
    "Apache-2.0.txt",
    "libffi-MIT.txt",
    "SQLite-Public-Domain.txt",
    "Tcl-Tk-license.terms",
    "Microsoft-VC-Runtime.txt",
    "React-MIT.txt",
    "pywin32-BSD-3-Clause.txt",
    "expat-MIT.txt",
    "zlib-License.txt",
    "libmpdec-BSD-2-Clause.txt",
    "Unicode-License.txt",
    "Tix-License.txt",
    "LGPL-2.1.txt",
    "adodbapi-LGPL-2.1.txt",
    "libiconv-NOTICE.txt",
    "LGPL-SOURCE-OFFER.txt",
)
REQUIRED_LICENSE_MIN_BYTES = 500

#: notices 正文必须出现的嵌入组件/义务标记。
EMBEDDED_MARKERS: Tuple[str, ...] = (
    "adodbapi", "iconv", "LGPL-2.1", "静态链接",
    "libiconv-NOTICE.txt", "LGPL-SOURCE-OFFER.txt",
)


class Distribution:
    __slots__ = ("name", "version", "license", "license_files", "home", "dist_info")

    def __init__(self, name: str, version: str, license_: str, license_files: List[str],
                 home: str, dist_info: str) -> None:
        self.name = name
        self.version = version
        self.license = license_
        self.license_files = license_files
        self.home = home
        self.dist_info = dist_info


def _parse_headers(text: str) -> Dict[str, List[str]]:
    """解析 METADATA 头部（支持重复字段），值里的换行按 RFC 822 续行处理。"""
    headers: Dict[str, List[str]] = {}
    current: Optional[str] = None
    for line in text.splitlines():
        if not line.strip():
            if current is not None:
                break
            continue
        if line[:1] in (" ", "\t") and current:
            headers[current][-1] += " " + line.strip()
            continue
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        current = key.strip()
        headers.setdefault(current, []).append(value.strip())
    return headers


def _short_license(headers: Dict[str, List[str]]) -> str:
    expression = (headers.get("License-Expression") or [""])[0].strip()
    if expression:
        return expression
    declared = (headers.get("License") or [""])[0].strip()
    if declared and len(declared) <= 80 and "\n" not in declared:
        return declared
    for classifier in headers.get("Classifier") or []:
        if classifier.startswith("License ::"):
            tail = classifier.split(" :: ")[-1].strip()
            return CLASSIFIER_ALIASES.get(tail, tail)
    return "见随包文本"


def _home_page(headers: Dict[str, List[str]]) -> str:
    preferred = ("homepage", "source", "repository", "documentation")
    candidates: List[Tuple[str, str]] = []
    for entry in headers.get("Project-URL") or []:
        if "," not in entry:
            continue
        label, url = entry.split(",", 1)
        candidates.append((label.strip().lower(), url.strip()))
    for wanted in preferred:
        for label, url in candidates:
            if wanted in label:
                return url
    if candidates:
        return candidates[0][1]
    home = (headers.get("Home-page") or [""])[0].strip()
    return home


def _license_files(dist_info: Path, site_packages: Path) -> List[str]:
    found: List[str] = []
    licenses_dir = dist_info / "licenses"
    if licenses_dir.is_dir():
        for entry in sorted(licenses_dir.rglob("*")):
            if entry.is_file():
                found.append(entry.relative_to(site_packages).as_posix())
    if not found:
        for entry in sorted(dist_info.iterdir()):
            if entry.is_file() and LICENSE_NAME_PATTERN.match(entry.name):
                found.append(entry.relative_to(site_packages).as_posix())
    return found


def collect(site_packages: Path) -> List[Distribution]:
    if not site_packages.is_dir():
        raise SystemExit(f"找不到 site-packages：{site_packages}")
    items: List[Distribution] = []
    for dist_info in sorted(site_packages.iterdir()):
        if not dist_info.is_dir() or not dist_info.name.endswith(".dist-info"):
            continue
        metadata = dist_info / "METADATA"
        if not metadata.is_file():
            continue
        headers = _parse_headers(metadata.read_text(encoding="utf-8", errors="replace"))
        name = (headers.get("Name") or [dist_info.name.split("-")[0]])[0]
        version = (headers.get("Version") or ["?"])[0]
        if name.lower() in ("liuhao", "liuhao-ai-os"):
            continue
        license_files = _license_files(dist_info, site_packages)
        override = LICENSE_OVERRIDES.get(name.lower())
        if override:
            license_, extra = override
            if (site_packages / extra).is_file() and extra not in license_files:
                license_files.insert(0, extra)
        else:
            license_ = _short_license(headers)
        items.append(Distribution(
            name=name,
            version=version,
            license_=license_,
            license_files=license_files,
            home=_home_page(headers),
            dist_info=dist_info.name,
        ))
    items.sort(key=lambda item: item.name.lower())
    return items


def _cell(value: str) -> str:
    return value.replace("|", "\\|").strip() or "—"


def _license_cell(dist: Distribution) -> str:
    if not dist.license_files:
        return "见随包文本"
    shown = dist.license_files[:3]
    text = "<br>".join(f"`{path}`" for path in shown)
    if len(dist.license_files) > len(shown):
        text += f" 等 {len(dist.license_files)} 个"
    return text


def render_table(items: Iterable[Distribution]) -> str:
    lines = [
        "| # | 名称 | 版本 | 许可证（声明） | 许可证文本随包位置 | 来源链接 | 状态 |",
        "|---|---|---|---|---|---|---|",
    ]
    for index, dist in enumerate(items, 1):
        status = "已验证" if dist.license_files else "未找到随包文本"
        lines.append(
            "| {index} | {name} | {version} | {license} | {files} | {home} | {status} |".format(
                index=index,
                name=_cell(dist.name),
                version=_cell(dist.version),
                license=_cell(dist.license),
                files=_license_cell(dist),
                home=_cell(dist.home or "（元数据未提供）"),
                status=status,
            )
        )
    return "\n".join(lines)


def _normalize(name: str) -> str:
    """PEP 503 名称归一：大小写不敏感，``-``/``_``/``.`` 等价。

    METADATA 里的 ``Name`` 与 requirements.txt 的写法并不总是一致
    （``pydantic_core`` vs ``pydantic-core``、``docstring_parser`` vs
    ``docstring-parser``）。不归一会把已锁定的包误报成“未在 requirements.txt
    单列”，清单看起来就像又漂了一遍。
    """
    return re.sub(r"[-_.]+", "-", name).strip().lower()


def _requirements_pins() -> Dict[str, str]:
    pins: Dict[str, str] = {}
    if not REQUIREMENTS_FILE.is_file():
        return pins
    for line in REQUIREMENTS_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "==" not in line:
            continue
        name, version = line.split("==", 1)
        # ``pywin32==311 ; sys_platform == 'win32'`` —— 环境标记不属于版本号。
        version = version.split(";", 1)[0].strip()
        pins[_normalize(name)] = version
    return pins


def render_version_note(items: List[Distribution]) -> str:
    """第 5 节第 7 条：requirements.txt 与随包实装版本的差异（现算）。"""
    pins = _requirements_pins()
    mismatches = [
        f"{dist.name}（锁 {pins[_normalize(dist.name)]} / 实装 {dist.version}）"
        for dist in items
        if _normalize(dist.name) in pins
        and pins[_normalize(dist.name)] != dist.version
    ]
    unpinned = [dist.name for dist in items if _normalize(dist.name) not in pins]
    if not mismatches:
        note = "requirements.txt 与随包实装版本一致。"
    else:
        note = (
            "requirements.txt 与随包实装版本的差异（以随包实装为准）："
            + "、".join(mismatches) + "。"
        )
    if unpinned:
        shown = "、".join(unpinned[:8])
        more = f" 等 {len(unpinned)} 个" if len(unpinned) > 8 else ""
        note += (
            f"\n   另有 {len(unpinned)} 个随包包未在 requirements.txt 里单列"
            f"（多为传递依赖）：{shown}{more}。"
        )
    return note + "\n   本清单由构建过程从 payload 的 *.dist-info 现场生成，不需要人工比对。"


def _restrictive_hits(items: List[Distribution]) -> List[str]:
    hits: List[str] = []
    for dist in items:
        for marker in RESTRICTIVE_MARKERS:
            if marker.lower() in dist.license.lower():
                hits.append(f"{dist.name} {dist.version}（{dist.license}）")
                break
    return hits


def _runtime_component_count(template: str) -> int:
    """第 3 节表格的行数 —— 与模板同源，避免手写计数再次漂移。"""
    section = template.split("## 3.", 1)[1].split("## 4.", 1)[0]
    return sum(1 for line in section.splitlines() if re.match(r"^\|\s*\d+\s*\|", line))


def render_restrictive_statement(items: List[Distribution]) -> str:
    """如实描述：顶层 0 个；嵌入层 N 个 LGPL。"""
    hits = _restrictive_hits(items)
    if hits:
        raise SystemExit("检测到限制性许可证，构建中止：" + "、".join(hits))
    names = "、".join(EMBEDDED_LGPL_COMPONENTS)
    return (
        "顶层（Python 发行包）0 个；嵌入层（runtime/ 与 licenses/ 覆盖的二进制/子包）"
        f"{len(EMBEDDED_LGPL_COMPONENTS)} 个 LGPL-2.1（{names}）。二者均已附 "
        "LGPL-2.1 全文，并给出源码与可重链接方案（见第 3.1 节）；构建脚本在顶层"
        "检出 AGPL/GPL/LGPL/SSPL/Elastic-2.0/BSL/OpenRAIL/CC-BY-NC 时直接中止。"
    )


def _validate(text: str, app_dir: Path, items: List[Distribution]) -> None:
    """生成后的自检：清单 ↔ 实装 ↔ 许可证文本必须三方一致。"""
    problems: List[str] = []
    if "{{" in text or "}}" in text:
        problems.append("模板仍有未替换的占位符")
    for name in REQUIRED_LICENSE_FILES:
        path = app_dir / "licenses" / name
        if not path.is_file():
            problems.append(f"缺少许可证文本：licenses/{name}")
        elif path.stat().st_size < REQUIRED_LICENSE_MIN_BYTES:
            problems.append(
                f"许可证文本过短（{path.stat().st_size} 字节）：licenses/{name}"
            )
    for dist in items:
        if not dist.license_files:
            problems.append(f"发行包无随包许可证文本：{dist.name} {dist.version}")
    for marker in EMBEDDED_MARKERS:
        if marker not in text:
            problems.append(f"声明中缺少嵌入组件标记：{marker}")
    if problems:
        raise SystemExit("第三方许可声明校验失败：\n  - " + "\n  - ".join(problems))


def render(app_dir: Path) -> str:
    template = TEMPLATE_FILE.read_text(encoding="utf-8")
    site_packages = app_dir / "runtime" / "Lib" / "site-packages"
    items = collect(site_packages)
    if not items:
        raise SystemExit(f"{site_packages} 下没有找到任何 *.dist-info")
    text = (
        template.replace("{{PACKAGE_COUNT}}", str(len(items)))
        .replace("{{PACKAGE_TABLE}}", render_table(items))
        .replace("{{VERSION_NOTE}}", render_version_note(items))
        .replace("{{RUNTIME_COMPONENT_COUNT}}", str(_runtime_component_count(template)))
        .replace("{{RESTRICTIVE_STATEMENT}}", render_restrictive_statement(items))
    )
    _validate(text, app_dir, items)
    return text


def write(app_dir: Path, out: Optional[Path] = None) -> Path:
    text = render(app_dir)
    target = out or (app_dir / "THIRD-PARTY-NOTICES.txt")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8", newline="\n")
    return target


def check(app_dir: Path, path: Path) -> int:
    expected = render(app_dir)
    actual = path.read_text(encoding="utf-8") if path.is_file() else ""
    if actual == expected:
        print(f"[ok] {path} 与 payload 实装一致")
        return 0
    expected_lines = expected.splitlines()
    actual_lines = actual.splitlines()
    for number, (left, right) in enumerate(zip(expected_lines, actual_lines), 1):
        if left != right:
            print(f"[FAIL] 第 {number} 行不一致：\n  期望: {left}\n  实际: {right}")
            break
    else:
        print(
            f"[FAIL] 行数不一致：期望 {len(expected_lines)} 行，实际 {len(actual_lines)} 行"
        )
    return 1


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="按 payload 实装内容生成第三方许可声明")
    parser.add_argument("--app", required=True, help="payload 的程序目录（含 runtime/）")
    parser.add_argument("--out", help="输出路径（默认 <app>/THIRD-PARTY-NOTICES.txt）")
    parser.add_argument("--check", help="只校验：把该文件与现场生成结果比对")
    parser.add_argument("--stdout", action="store_true", help="打印生成结果，不写文件")
    args = parser.parse_args(argv)

    app_dir = Path(args.app).expanduser().resolve()
    if args.stdout:
        sys.stdout.write(render(app_dir))
        return 0
    if args.check:
        return check(app_dir, Path(args.check).expanduser().resolve())
    target = write(app_dir, Path(args.out).expanduser().resolve() if args.out else None)
    print(f"[ok] 已生成第三方许可声明：{target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
