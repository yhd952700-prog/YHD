#!/usr/bin/env python
"""成品包许可证 / SBOM 齐备性校验（只读，不执行安装包）。

校验对象是**成品包内容**，不是打包工作树：

* ``--payload <dir>``：直接扫描包内容（含 ``runtime/Lib/site-packages``、
  ``licenses/``、``THIRD-PARTY-NOTICES.txt``）；
* ``--exe <setup.exe>``：用 innounp 解包到临时目录后扫描（只读，运行
  ``setup.exe`` 以外的解包器；不会执行安装程序）；
* ``--upgrade-zip <liuhao-*.zip>``：用标准库 ``zipfile`` 只读解包到临时目录后扫描
  （不执行包内任何内容）；要求 ``docs/compliance/`` 下同时带许可证文本、NOTICES、
  SBOM 与 LGPL 材料。该位置受升级器 ``INSTALL_ROOTS`` 白名单约束（``docs`` 允许），
  根目录放 ``licenses/`` 会被升级器拒绝。

输出三件事：

1. 许可证文本是否齐全（必需全文 + 每个发行包自带的文本）；
2. SBOM 是否覆盖包内全部发行包（组件、版本、许可证字段、文本路径）；
3. 缺少哪一项（逐条列出，退出码 1）；无法解包时退出码 2。

另外固定检查「分发边界」：``docs/legal``（AI 起草、待法务确认的草稿）不得出现在
payload、安装目录或升级包里，命中即 FAIL。

用法::

    python dist/release-gate/verify_package_compliance.py --payload dist\\payload\\app
    python dist/release-gate/verify_package_compliance.py --exe dist\\installer\\LiuHao-Setup-0.1.1.exe
    python dist/release-gate/verify_package_compliance.py --upgrade-zip dist\\release\\liuhao-0.1.1.zip
    python dist/release-gate/verify_package_compliance.py --exe <exe> --sbom <sbom.json> --json-out <out.json>

退出码：0 = PASS；1 = FAIL（有缺项）；2 = BLOCKED（无法读取成品包内容）。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parents[1]
DEFAULT_SBOM = SCRIPT_DIR / "sbom-cyclonedx.json"
DEFAULT_LGPL_SOURCES = SCRIPT_DIR / "lgpl-sources"
DEFAULT_INNOUNP = SCRIPT_DIR / "tools" / "innounp-2" / "innounp.exe"

#: 升级包内的合规材料目录。必须是 ``INSTALL_ROOTS`` 允许的 ``docs`` 下，
#: 否则升级器会因路径不在白名单而拒绝整包。
UPGRADE_COMPLIANCE_DIR = "docs/compliance"

#: 任何交付物（payload / 安装目录 / 升级包）里都不允许出现的目录。
#: ``docs/legal`` 只有 AI 起草、未经法务确认的草稿，只能留在仓库供审阅，
#: 随包发出去等于把未定稿条款当正式文本对外发布。
FORBIDDEN_DELIVERABLE_DIRS: Tuple[str, ...] = ("docs/legal",)

#: 随包必须存在的许可证全文（与 packaging/third_party_notices.py 的
#: REQUIRED_LICENSE_FILES 保持一致；校验只看成品包里的文件）。
REQUIRED_LICENSE_FILES: Tuple[Tuple[str, int], ...] = (
    ("Apache-2.0.txt", 500),
    ("libffi-MIT.txt", 500),
    ("SQLite-Public-Domain.txt", 500),
    ("Tcl-Tk-license.terms", 500),
    ("Microsoft-VC-Runtime.txt", 500),
    ("React-MIT.txt", 500),
    ("pywin32-BSD-3-Clause.txt", 500),
    ("expat-MIT.txt", 500),
    ("zlib-License.txt", 500),
    ("libmpdec-BSD-2-Clause.txt", 500),
    ("Unicode-License.txt", 500),
    ("Tix-License.txt", 500),
    ("LGPL-2.1.txt", 500),
    ("adodbapi-LGPL-2.1.txt", 500),
    ("libiconv-NOTICE.txt", 500),
    ("LGPL-SOURCE-OFFER.txt", 1000),
)

LICENSE_NAME_PATTERN = re.compile(r"^(licen[cs]e|copying|notice)", re.IGNORECASE)
TABLE_ROW = re.compile(r"^\|\s*\d+\s*\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|")
SHA256_PATTERN = re.compile(r"SHA256[：:]\s*([0-9a-fA-F]{64})")


# --------------------------------------------------------------------------- helpers
def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def parse_metadata(path: Path) -> Dict[str, List[str]]:
    headers: Dict[str, List[str]] = {}
    current: Optional[str] = None
    for line in read_text(path).splitlines():
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


def first(headers: Dict[str, List[str]], key: str) -> str:
    values = headers.get(key) or [""]
    return values[0].strip()


# --------------------------------------------------------------------------- input
def resolve_app_dir(path: Path) -> Optional[Path]:
    """接受 app 目录、payload 根目录或更高层的构建目录。"""
    if (path / "runtime" / "Lib" / "site-packages").is_dir():
        return path
    for relative in ("app", "payload/app", "payload"):
        candidate = path / relative
        if (candidate / "runtime" / "Lib" / "site-packages").is_dir():
            return candidate
    for pattern in ("*/runtime/Lib/site-packages", "*/*/runtime/Lib/site-packages"):
        for site_packages in sorted(path.glob(pattern)):
            if site_packages.is_dir():
                return site_packages.parents[2]
    return None


def find_innounp(explicit: Optional[Path]) -> Optional[Path]:
    candidates: List[Optional[str]] = [
        str(explicit) if explicit else None,
        os.environ.get("INNOUNP"),
        str(DEFAULT_INNOUNP),
        shutil.which("innounp"),
    ]
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return Path(candidate)
    return None


def extract_installer(exe: Path, innounp: Path) -> Path:
    tmp = Path(tempfile.mkdtemp(prefix="liuhao-verify-"))
    cmd = [str(innounp), "-x", "-b", "-q", "-y", "-d" + str(tmp), str(exe)]
    proc = subprocess.run(
        cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=900
    )
    if proc.returncode != 0:
        shutil.rmtree(tmp, ignore_errors=True)
        raise RuntimeError(
            "innounp 解包失败（exit=%s）：%s"
            % (proc.returncode, (proc.stderr or proc.stdout or "").strip()[-400:])
        )
    return tmp


def extract_upgrade_zip(zip_path: Path) -> Path:
    """只读解包升级包；拒绝绝对路径 / 上跳路径，不执行包内任何内容。"""
    tmp = Path(tempfile.mkdtemp(prefix="liuhao-verify-zip-"))
    root = tmp.resolve()
    try:
        with zipfile.ZipFile(zip_path) as archive:
            for info in archive.infolist():
                name = info.filename
                if not name or name.startswith(("/", "\\")) or re.match(r"^[A-Za-z]:", name):
                    raise RuntimeError("升级包含绝对路径：%s" % name)
                target = (tmp / name).resolve()
                try:
                    inside = os.path.commonpath([str(root), str(target)]) == str(root)
                except ValueError:
                    inside = False
                if not inside:
                    raise RuntimeError("升级包含越界路径：%s" % name)
            archive.extractall(tmp)
    except zipfile.BadZipFile as exc:
        shutil.rmtree(tmp, ignore_errors=True)
        raise RuntimeError("升级包无法作为 zip 读取：%s" % exc)
    except Exception:
        shutil.rmtree(tmp, ignore_errors=True)
        raise
    return tmp


# --------------------------------------------------------------------------- package
def collect_distributions(site_packages: Path) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    for dist_info in sorted(site_packages.iterdir()):
        if not dist_info.is_dir() or not dist_info.name.endswith(".dist-info"):
            continue
        metadata = dist_info / "METADATA"
        if not metadata.is_file():
            continue
        headers = parse_metadata(metadata)
        name = first(headers, "Name") or dist_info.name.split("-")[0]
        version = first(headers, "Version") or "?"
        license_files = license_files_for(dist_info, site_packages, name)
        has_text = any((site_packages / relative).stat().st_size > 0 for relative in license_files)
        items.append(
            {
                "dist_info": dist_info.name,
                "name": name,
                "version": version,
                "license_files": license_files,
                "has_text": has_text,
            }
        )
    return items


def license_files_for(dist_info: Path, site_packages: Path, name: str) -> List[str]:
    found: List[str] = []
    licenses_dir = dist_info / "licenses"
    if licenses_dir.is_dir():
        found.extend(
            path.relative_to(site_packages).as_posix()
            for path in sorted(licenses_dir.rglob("*"))
            if path.is_file()
        )
    if not found:
        found.extend(
            path.relative_to(site_packages).as_posix()
            for path in sorted(dist_info.iterdir())
            if path.is_file() and LICENSE_NAME_PATTERN.match(path.name)
        )
    if name.lower() == "pywin32":
        for extra in ("win32/license.txt", "win32com/License.txt", "pythonwin/license.txt"):
            candidate = site_packages / extra
            if candidate.is_file() and extra not in found:
                found.append(extra)
    return found


# --------------------------------------------------------------------------- checks
def check_license_texts(app: Path, dists: List[Dict[str, Any]]) -> List[str]:
    issues: List[str] = []
    licenses = app / "licenses"
    if not licenses.is_dir():
        return ["缺少 licenses/ 目录"]
    for filename, minimum in REQUIRED_LICENSE_FILES:
        path = licenses / filename
        if not path.is_file():
            issues.append("licenses/%s 缺失" % filename)
        elif path.stat().st_size < minimum:
            issues.append("licenses/%s 仅 %d 字节（要求 ≥%d）" % (filename, path.stat().st_size, minimum))

    lgpl = read_text(licenses / "LGPL-2.1.txt")
    if lgpl and "gnu lesser general public license" not in lgpl.casefold():
        issues.append("licenses/LGPL-2.1.txt 内容不是 LGPL-2.1 全文")
    adodbapi = read_text(licenses / "adodbapi-LGPL-2.1.txt")
    if adodbapi and "lesser general public license" not in adodbapi.casefold():
        issues.append("licenses/adodbapi-LGPL-2.1.txt 内容不含 LGPL 全文")
    notice = read_text(licenses / "libiconv-NOTICE.txt")
    if notice and ("iconv" not in notice.casefold() or "LGPL" not in notice):
        issues.append("licenses/libiconv-NOTICE.txt 未说明 iconv / LGPL")
    offer = read_text(licenses / "LGPL-SOURCE-OFFER.txt")
    if offer:
        for marker in ("lxml-6.1.3.tar.gz", "libiconv-1.17.tar.gz", "3 年", "team@liuhao.ai"):
            if marker not in offer:
                issues.append("licenses/LGPL-SOURCE-OFFER.txt 缺少关键承诺：%s" % marker)

    for dist in dists:
        if not dist["license_files"]:
            issues.append("%s 没有任何许可证文本文件" % dist["dist_info"])
        elif not dist["has_text"]:
            issues.append("%s 的许可证文本文件为空" % dist["dist_info"])
    return issues


def _section(text: str, start_marker: str, end_marker: str) -> str:
    start = text.find(start_marker)
    if start < 0:
        return ""
    end = text.find(end_marker, start + len(start_marker))
    return text[start:] if end < 0 else text[start:end]


def check_notices(app: Path, dists: List[Dict[str, Any]]) -> List[str]:
    notices = app / "THIRD-PARTY-NOTICES.txt"
    if not notices.is_file():
        return ["缺少 THIRD-PARTY-NOTICES.txt"]
    text = read_text(notices)
    issues: List[str] = []

    section2 = _section(text, "## 2. Python 第三方包清单", "## 3. ")
    rows: Dict[str, str] = {}
    row_count = 0
    for line in section2.splitlines():
        match = TABLE_ROW.match(line)
        if match:
            row_count += 1
            rows[match.group(1).strip().casefold()] = match.group(2).strip()
    if row_count != len(dists):
        issues.append("第 2 节表格 %d 行，包内实际 %d 个发行包" % (row_count, len(dists)))
    for dist in dists:
        key = dist["name"].casefold()
        if key not in rows:
            issues.append("NOTICES 第 2 节缺少包：%s" % dist["name"])
        elif rows[key] != dist["version"]:
            issues.append(
                "NOTICES 版本不一致：%s 写着 %s，包内为 %s"
                % (dist["name"], rows[key], dist["version"])
            )

    declared = re.search(r"Python 第三方包[：:]\s*(\d+)\s*个", text)
    if not declared or int(declared.group(1)) != len(dists):
        issues.append("NOTICES 汇总的包数量与包内实际不一致")

    required_markers = (
        "adodbapi",
        "iconv",
        "libiconv-NOTICE.txt",
        "LGPL-SOURCE-OFFER.txt",
        "2 个 LGPL-2.1",
    )
    for marker in required_markers:
        if marker not in text:
            issues.append("NOTICES 缺少 LGPL 嵌入组件说明标记：%s" % marker)
    return issues


def check_sbom(
    sbom_path: Optional[Path], app: Path, dists: List[Dict[str, Any]]
) -> Tuple[List[str], Dict[str, Any]]:
    issues: List[str] = []
    summary: Dict[str, Any] = {
        "path": str(sbom_path) if sbom_path else "",
        "components": 0,
        "covered": 0,
        "total": len(dists),
        "coverage_percent": 0.0,
        "embedded_components": 0,
    }
    if sbom_path is None or not sbom_path.is_file():
        issues.append("SBOM 文件不存在：%s" % (sbom_path or "(未指定)"))
        return issues, summary

    try:
        data = json.loads(read_text(sbom_path))
    except json.JSONDecodeError as exc:
        return ["SBOM 不是合法 JSON：%s" % exc], summary
    if data.get("bomFormat") != "CycloneDX":
        issues.append("SBOM 的 bomFormat 不是 CycloneDX")
    components = data.get("components") or []
    summary["components"] = len(components)

    site_packages = app / "runtime" / "Lib" / "site-packages"
    by_dist: Dict[str, Dict[str, Any]] = {}
    embedded = 0
    for component in components:
        props = {
            str(item.get("name")): str(item.get("value"))
            for item in (component.get("properties") or [])
            if isinstance(item, dict)
        }
        if "liuhao:embedded-in" in props:
            embedded += 1
        if props.get("liuhao:dist-info"):
            by_dist[props["liuhao:dist-info"]] = component

        # 校验 SBOM 声明的许可证文本路径确实存在于成品包内
        base = site_packages if props.get("liuhao:dist-info") and "liuhao:embedded-in" not in props else app
        raw_paths = props.get("liuhao:license-files") or ""
        for raw in raw_paths.split(","):
            item = raw.strip()
            if not item or item.startswith("(") or item.startswith("无"):
                continue
            if not (base / item).is_file():
                issues.append("SBOM 组件 %s 引用的文本不存在：%s" % (component.get("name"), item))

    summary["embedded_components"] = embedded

    for dist in dists:
        component = by_dist.get(dist["dist_info"])
        if component is None:
            issues.append("SBOM 未覆盖发行包：%s" % dist["name"])
            continue
        if str(component.get("version")) != dist["version"]:
            issues.append(
                "SBOM 版本不一致：%s 写着 %s，包内为 %s"
                % (dist["name"], component.get("version"), dist["version"])
            )
            continue
        if not component.get("licenses"):
            issues.append("SBOM 组件 %s 缺少 licenses 字段" % dist["name"])
            continue
        props = {
            str(item.get("name")): str(item.get("value"))
            for item in (component.get("properties") or [])
            if isinstance(item, dict)
        }
        if dist["has_text"] and props.get("liuhao:license-text-present") != "true":
            issues.append("SBOM 组件 %s 未标记许可证文本随包" % dist["name"])
            continue
        summary["covered"] += 1

    total = len(dists)
    summary["coverage_percent"] = round(summary["covered"] * 100.0 / total, 1) if total else 0.0
    return issues, summary


def expected_sha_for(text: str, filename: str) -> Optional[str]:
    index = text.find(filename)
    if index < 0:
        return None
    match = SHA256_PATTERN.search(text[index:index + 500])
    return match.group(1).lower() if match else None


def check_lgpl_sources(lgpl_dir: Path, app: Path) -> List[str]:
    issues: List[str] = []
    if not lgpl_dir.is_dir():
        return ["LGPL 源码归档目录不存在：%s" % lgpl_dir]
    offer = read_text(app / "licenses" / "LGPL-SOURCE-OFFER.txt")
    archive_offer = read_text(lgpl_dir / "SOURCE-OFFER.txt")
    if not archive_offer:
        issues.append("lgpl-sources/SOURCE-OFFER.txt 缺失")
    elif offer and archive_offer != offer:
        issues.append("lgpl-sources/SOURCE-OFFER.txt 与包内 licenses/LGPL-SOURCE-OFFER.txt 不一致")

    for filename in ("lxml-6.1.3.tar.gz", "libiconv-1.17.tar.gz"):
        path = lgpl_dir / filename
        if not path.is_file():
            issues.append("lgpl-sources/%s 缺失" % filename)
            continue
        if path.stat().st_size < 1024 * 1024:
            issues.append("lgpl-sources/%s 仅 %d 字节（源码包异常）" % (filename, path.stat().st_size))
            continue
        expected = expected_sha_for(offer, filename)
        if not expected:
            issues.append("包内 LGPL 源码要约未给出 %s 的 SHA256" % filename)
        elif sha256_file(path) != expected:
            issues.append("lgpl-sources/%s 的 SHA256 与包内要约不一致" % filename)
    return issues


def check_iconv_link_mode(app: Path, notices_text: str) -> Tuple[List[str], str]:
    issues: List[str] = []
    site_packages = app / "runtime" / "Lib" / "site-packages"
    pyd_files = sorted(site_packages.glob("lxml/etree*.pyd"))
    if not pyd_files:
        return issues, "unknown"

    try:
        sys.path.insert(0, str(SCRIPT_DIR))
        from pe_imports import read_pe_imports
        imports = read_pe_imports(pyd_files[0])
    except Exception as exc:  # noqa: BLE001 - 解析失败按缺项报告
        return ["无法读取 %s 的 PE 导入表：%s" % (pyd_files[0].name, exc)], "unknown"

    dynamic = any("libiconv" in name.casefold() for name in imports)
    if dynamic:
        if "libiconv-2.dll" not in notices_text or "动态链接" not in notices_text:
            issues.append("etree.pyd 动态依赖 libiconv，但 NOTICES 未写明 libiconv-2.dll / 动态链接")
        if not (site_packages / "lxml" / "libiconv-2.dll").is_file():
            issues.append("etree.pyd 动态依赖 libiconv，但包内缺少 lxml/libiconv-2.dll")
        return issues, "dynamic"

    if "静态链接" not in notices_text:
        issues.append("etree.pyd 未导入 libiconv-2.dll（静态链接），但 NOTICES 未写明静态链接")
    return issues, "static"


# ------------------------------------------------------------------ upgrade zip
def component_properties(component: Dict[str, Any]) -> Dict[str, str]:
    return {
        str(item.get("name")): str(item.get("value"))
        for item in (component.get("properties") or [])
        if isinstance(item, dict)
    }


def check_forbidden_paths(root: Path) -> List[str]:
    """分发边界：禁止分发的目录不得出现在包内（按相对路径精确比对）。"""
    issues: List[str] = []
    for rel in FORBIDDEN_DELIVERABLE_DIRS:
        if (root / rel).exists():
            issues.append(
                "出现禁止分发目录 %s：只能留在仓库供审阅，不得随包/随安装目录分发" % rel
            )
    return issues


def check_upgrade_layout(bundle: Path) -> List[str]:
    """根目录出现合规材料会被升级器白名单拒绝，必须放在 docs/compliance/。"""
    issues: List[str] = []
    for stray in ("licenses", "THIRD-PARTY-NOTICES.txt", "sbom-cyclonedx.json"):
        if (bundle / stray).exists():
            issues.append(
                "升级包根目录出现 %s；会破坏 INSTALL_ROOTS 白名单，应放到 %s/"
                % (stray, UPGRADE_COMPLIANCE_DIR)
            )
    return issues


def check_upgrade_sbom(
    path: Path,
) -> Tuple[List[str], Dict[str, Any], List[Dict[str, Any]]]:
    issues: List[str] = []
    summary: Dict[str, Any] = {
        "path": str(path),
        "components": 0,
        "covered": 0,
        "total": 0,
        "coverage_percent": 0.0,
        "embedded_components": 0,
    }
    if not path.is_file():
        return ["升级包缺少 %s/sbom-cyclonedx.json" % UPGRADE_COMPLIANCE_DIR], summary, []

    try:
        data = json.loads(read_text(path))
    except json.JSONDecodeError as exc:
        return ["升级包内 SBOM 不是合法 JSON：%s" % exc], summary, []
    if data.get("bomFormat") != "CycloneDX":
        issues.append("升级包内 SBOM 的 bomFormat 不是 CycloneDX")
    if not data.get("specVersion"):
        issues.append("升级包内 SBOM 缺少 specVersion")
    components = data.get("components") or []
    summary["components"] = len(components)
    if not components:
        issues.append("升级包内 SBOM 没有任何组件")

    dist_components: List[Dict[str, Any]] = []
    covered = 0
    for component in components:
        props = component_properties(component)
        if "liuhao:embedded-in" in props:
            summary["embedded_components"] += 1
        if not props.get("liuhao:dist-info"):
            continue
        dist_components.append(component)
        if not component.get("licenses"):
            issues.append("SBOM 组件 %s 缺少 licenses 字段" % component.get("name"))
        elif props.get("liuhao:license-text-present") != "true":
            issues.append("SBOM 组件 %s 未标记许可证文本随包" % component.get("name"))
        else:
            covered += 1

    total = len(dist_components)
    summary["total"] = total
    summary["covered"] = covered
    summary["coverage_percent"] = round(covered * 100.0 / total, 1) if total else 0.0
    return issues, summary, dist_components


def check_upgrade_notices(text: str, dist_components: List[Dict[str, Any]]) -> List[str]:
    if not text:
        return ["升级包缺少 %s/THIRD-PARTY-NOTICES.txt" % UPGRADE_COMPLIANCE_DIR]
    issues: List[str] = []
    section2 = _section(text, "## 2. Python 第三方包清单", "## 3. ")
    rows: Dict[str, str] = {}
    row_count = 0
    for line in section2.splitlines():
        match = TABLE_ROW.match(line)
        if match:
            row_count += 1
            rows[match.group(1).strip().casefold()] = match.group(2).strip()
    if row_count != len(dist_components):
        issues.append(
            "NOTICES 第 2 节表格 %d 行，SBOM 中 %d 个发行包" % (row_count, len(dist_components))
        )
    for component in dist_components:
        name = str(component.get("name"))
        version = str(component.get("version"))
        if name.casefold() not in rows:
            issues.append("NOTICES 第 2 节缺少包：%s" % name)
        elif rows[name.casefold()] != version:
            issues.append(
                "NOTICES 版本不一致：%s 写着 %s，SBOM 为 %s"
                % (name, rows[name.casefold()], version)
            )
    declared = re.search(r"Python 第三方包[：:]\s*(\d+)\s*个", text)
    if not declared or int(declared.group(1)) != len(dist_components):
        issues.append("NOTICES 汇总的包数量与 SBOM 不一致")
    for marker in ("adodbapi", "iconv", "libiconv-NOTICE.txt", "LGPL-SOURCE-OFFER.txt", "2 个 LGPL-2.1"):
        if marker not in text:
            issues.append("NOTICES 缺少 LGPL 嵌入组件说明标记：%s" % marker)
    return issues


def check_upgrade_lgpl_materials(compliance: Path) -> List[str]:
    issues: List[str] = []
    for filename in (
        "LGPL-2.1.txt",
        "adodbapi-LGPL-2.1.txt",
        "libiconv-NOTICE.txt",
        "LGPL-SOURCE-OFFER.txt",
    ):
        if not (compliance / "licenses" / filename).is_file():
            issues.append("升级包缺少 licenses/%s" % filename)
    return issues


def check_upgrade_iconv_declaration(notices_text: str) -> Tuple[List[str], str]:
    if "静态链接" in notices_text:
        return [], "static"
    if "动态链接" in notices_text and "libiconv-2.dll" in notices_text:
        return [], "dynamic"
    return ["升级包 NOTICES 未声明 iconv 的链接方式（静态/动态）"], "unknown"


def verify_upgrade_zip(bundle: Path, input_info: Dict[str, Any]) -> Tuple[Dict[str, Any], int]:
    compliance = bundle / UPGRADE_COMPLIANCE_DIR
    layout_issues = check_upgrade_layout(bundle)
    forbidden_issues = check_forbidden_paths(bundle)
    notices_path = compliance / "THIRD-PARTY-NOTICES.txt"
    notices_text = read_text(notices_path)

    license_issues = check_license_texts(compliance, [])
    sbom_issues, sbom_summary, dist_components = check_upgrade_sbom(
        compliance / "sbom-cyclonedx.json"
    )
    notices_issues = check_upgrade_notices(notices_text, dist_components)
    lgpl_issues = check_upgrade_lgpl_materials(compliance)
    iconv_issues, iconv_mode = check_upgrade_iconv_declaration(notices_text)

    missing_items = (
        forbidden_issues + layout_issues + license_issues + notices_issues + sbom_issues
        + lgpl_issues + iconv_issues
    )
    licenses_dir = compliance / "licenses"
    result: Dict[str, Any] = {
        "generated_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "input": input_info,
        "package": {
            "app_dir": str(bundle),
            "compliance_dir": str(compliance),
            "distributions": 0,
            "components": sbom_summary["components"],
            "license_files": len(list(licenses_dir.glob("*"))) if licenses_dir.is_dir() else 0,
            "notices_bytes": notices_path.stat().st_size if notices_path.is_file() else 0,
        },
        "license_texts": {
            "status": "PASS" if not license_issues else "FAIL",
            "required": len(REQUIRED_LICENSE_FILES),
            "missing": license_issues,
        },
        "notices": {
            "status": "PASS" if not notices_issues else "FAIL",
            "issues": notices_issues,
        },
        "sbom": dict(sbom_summary, status="PASS" if not sbom_issues else "FAIL", issues=sbom_issues),
        "lgpl_sources": {
            "status": "PASS" if not lgpl_issues else "FAIL",
            "issues": lgpl_issues,
        },
        "iconv": {
            "status": "PASS" if not iconv_issues else "FAIL",
            "link_mode": iconv_mode,
            "issues": iconv_issues,
        },
        "layout": {
            "status": "PASS" if not layout_issues else "FAIL",
            "issues": layout_issues,
        },
        "forbidden_paths": {
            "status": "PASS" if not forbidden_issues else "FAIL",
            "issues": forbidden_issues,
        },
    }
    result["verdict"] = {
        "status": "PASS" if not missing_items else "FAIL",
        "summary": (
            "许可证文本齐全、SBOM 覆盖、无缺项"
            if not missing_items
            else "缺少 %d 项，见 missing_items" % len(missing_items)
        ),
        "missing_items": missing_items,
    }
    return result, 0 if not missing_items else 1


# --------------------------------------------------------------------------- report
def verify(
    app: Path,
    input_info: Dict[str, Any],
    sbom_path: Optional[Path],
    lgpl_dir: Path,
) -> Tuple[Dict[str, Any], int]:
    site_packages = app / "runtime" / "Lib" / "site-packages"
    dists = collect_distributions(site_packages)
    notices_text = read_text(app / "THIRD-PARTY-NOTICES.txt")

    license_issues = check_license_texts(app, dists)
    notices_issues = check_notices(app, dists)
    sbom_issues, sbom_summary = check_sbom(sbom_path, app, dists)
    lgpl_issues = check_lgpl_sources(lgpl_dir, app)
    iconv_issues, iconv_mode = check_iconv_link_mode(app, notices_text)
    forbidden_issues = check_forbidden_paths(app)

    missing_items = (
        forbidden_issues + license_issues + notices_issues + sbom_issues + lgpl_issues
        + iconv_issues
    )
    result: Dict[str, Any] = {
        "generated_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "input": input_info,
        "package": {
            "app_dir": str(app),
            "distributions": len(dists),
            "license_files": len(list((app / "licenses").glob("*"))),
            "notices_bytes": (app / "THIRD-PARTY-NOTICES.txt").stat().st_size
            if (app / "THIRD-PARTY-NOTICES.txt").is_file() else 0,
        },
        "license_texts": {
            "status": "PASS" if not license_issues else "FAIL",
            "required": len(REQUIRED_LICENSE_FILES),
            "missing": license_issues,
        },
        "notices": {
            "status": "PASS" if not notices_issues else "FAIL",
            "issues": notices_issues,
        },
        "sbom": dict(sbom_summary, status="PASS" if not sbom_issues else "FAIL", issues=sbom_issues),
        "lgpl_sources": {
            "status": "PASS" if not lgpl_issues else "FAIL",
            "issues": lgpl_issues,
        },
        "iconv": {
            "status": "PASS" if not iconv_issues else "FAIL",
            "link_mode": iconv_mode,
            "issues": iconv_issues,
        },
        "forbidden_paths": {
            "status": "PASS" if not forbidden_issues else "FAIL",
            "issues": forbidden_issues,
        },
    }
    result["verdict"] = {
        "status": "PASS" if not missing_items else "FAIL",
        "summary": (
            "许可证文本齐全、SBOM 覆盖、无缺项"
            if not missing_items
            else "缺少 %d 项，见 missing_items" % len(missing_items)
        ),
        "missing_items": missing_items,
    }
    return result, 0 if not missing_items else 1


def print_summary(result: Dict[str, Any]) -> None:
    source = result["input"]
    print("=" * 72)
    print("成品包许可证 / SBOM 校验")
    print("=" * 72)
    print("输入：%s  %s" % (source.get("type"), source.get("path")))
    if source.get("sha256"):
        print("SHA256：%s" % source["sha256"])
    if source.get("extracted_to"):
        print("解包：%s（%s）" % (source["extracted_to"], source.get("extractor")))
    package = result["package"]
    is_zip = source.get("type") == "upgrade-zip"
    if is_zip:
        print("包内：%d 个许可证文件 / NOTICES %d 字节 / SBOM %d 个组件"
              % (package["license_files"], package["notices_bytes"], package.get("components", 0)))
    else:
        print("包内：%d 个发行包 / %d 个许可证文件 / NOTICES %d 字节"
              % (package["distributions"], package["license_files"], package["notices_bytes"]))
    print("-" * 72)
    sections = [
        ("许可证文本", result["license_texts"], "missing"),
        ("NOTICES 一致性", result["notices"], "issues"),
        ("SBOM 覆盖", result["sbom"], "issues"),
        ("LGPL 材料" if is_zip else "LGPL 源码归档", result["lgpl_sources"], "issues"),
        ("iconv 链接方式", result["iconv"], "issues"),
    ]
    if is_zip:
        sections.append(("升级包布局", result["layout"], "issues"))
    sections.append(("分发边界（docs/legal）", result["forbidden_paths"], "issues"))
    for title, section, detail_key in sections:
        detail = section.get(detail_key) or []
        if title == "许可证文本":
            detail_text = "必需 %d 项" % section["required"] if section["status"] == "PASS" else "%d 项问题" % len(detail)
        elif title == "SBOM 覆盖":
            detail_text = "%d/%d（%.1f%%），%d 个组件" % (
                section["covered"], section["total"], section["coverage_percent"], section["components"]
            ) if section["status"] == "PASS" else "%d 项问题" % len(detail)
        elif title == "iconv 链接方式":
            detail_text = "%s" % section["link_mode"]
        else:
            detail_text = "无问题" if section["status"] == "PASS" else "%d 项问题" % len(detail)
        print("[%s] %s：%s" % (section["status"], title, detail_text))

    verdict = result["verdict"]
    print("-" * 72)
    print("结论：%s —— %s" % (verdict["status"], verdict["summary"]))
    if verdict["missing_items"]:
        for item in verdict["missing_items"]:
            print("  - %s" % item)
    print("=" * 72)


# --------------------------------------------------------------------------- main
def main() -> int:
    parser = argparse.ArgumentParser(description="成品包许可证 / SBOM 齐备性校验")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--payload", type=Path, help="payload 目录（app 目录或其上层构建目录）")
    group.add_argument("--exe", type=Path, help="Inno Setup 安装包；用 innounp 只读解包后校验")
    group.add_argument(
        "--upgrade-zip",
        type=Path,
        help="升级包 zip；用标准库 zipfile 只读解包后校验 docs/compliance/ 下的材料",
    )
    parser.add_argument("--sbom", type=Path, default=None, help="CycloneDX SBOM 路径")
    parser.add_argument("--lgpl-sources", type=Path, default=DEFAULT_LGPL_SOURCES, help="LGPL 源码归档目录")
    parser.add_argument("--innounp", type=Path, default=None, help="innounp.exe 路径")
    parser.add_argument("--keep-extracted", type=Path, default=None, help="保留解包目录（默认校验后删除）")
    parser.add_argument("--json-out", type=Path, default=None, help="把完整结果写入 JSON")
    args = parser.parse_args()

    sbom_path = args.sbom or DEFAULT_SBOM
    temp_dir: Optional[Path] = None
    input_info: Dict[str, Any] = {}

    try:
        if args.exe:
            exe = args.exe.resolve()
            if not exe.is_file():
                raise RuntimeError("安装包不存在：%s" % exe)
            extractor = find_innounp(args.innounp)
            if extractor is None:
                print("[BLOCKED] 未找到 innounp，无法只读解包 Inno Setup 安装包。", file=sys.stderr)
                print("         安装 innounp 或指定 --innounp；也可直接对构建 payload 使用 --payload。", file=sys.stderr)
                return 2
            temp_dir = extract_installer(exe, extractor)
            app = resolve_app_dir(temp_dir)
            if app is None:
                print("[BLOCKED] 解包完成，但未找到 runtime/Lib/site-packages：%s" % temp_dir, file=sys.stderr)
                return 2
            input_info = {
                "type": "exe",
                "path": str(exe),
                "sha256": sha256_file(exe),
                "extractor": str(extractor),
                "extracted_to": str(app),
            }
        elif args.upgrade_zip:
            archive = args.upgrade_zip.resolve()
            if not archive.is_file():
                raise RuntimeError("升级包不存在：%s" % archive)
            temp_dir = extract_upgrade_zip(archive)
            input_info = {
                "type": "upgrade-zip",
                "path": str(archive),
                "sha256": sha256_file(archive),
                "extractor": "zipfile",
                "extracted_to": str(temp_dir),
            }
        else:
            candidate = args.payload.resolve()
            app = resolve_app_dir(candidate)
            if app is None:
                print("[BLOCKED] %s 中未找到 runtime/Lib/site-packages" % candidate, file=sys.stderr)
                return 2
            input_info = {"type": "payload", "path": str(candidate), "app_dir": str(app)}

        if args.upgrade_zip:
            result, code = verify_upgrade_zip(temp_dir, input_info)
        else:
            result, code = verify(app, input_info, sbom_path, args.lgpl_sources)
        print_summary(result)
        if args.json_out:
            args.json_out.parent.mkdir(parents=True, exist_ok=True)
            args.json_out.write_text(
                json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
            print("JSON：%s" % args.json_out)
        return code
    except RuntimeError as exc:
        print("[BLOCKED] %s" % exc, file=sys.stderr)
        return 2
    finally:
        if temp_dir is not None:
            if args.keep_extracted:
                args.keep_extracted.parent.mkdir(parents=True, exist_ok=True)
                if args.keep_extracted.exists():
                    shutil.rmtree(args.keep_extracted)
                shutil.move(str(temp_dir), str(args.keep_extracted))
            else:
                shutil.rmtree(temp_dir, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
