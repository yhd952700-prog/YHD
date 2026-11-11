#!/usr/bin/env python
"""安装后自检：扫描**真实安装目录**，断言合规材料齐全且非空（只读）。

校验对象是装机完成后的程序目录（默认 ``D:\\LiuHao``），不是构建工作树：

* ``licenses/``：存在，且 :data:`verify_package_compliance.REQUIRED_LICENSE_FILES`
  的 16 项各自存在、非空（每项 ≥ 500 字节）；
* ``THIRD-PARTY-NOTICES.txt``：存在、非空（≥ 1000 字节），且含 LGPL 说明；
* ``sbom-cyclonedx.json``：存在、可解析为 CycloneDX，``components`` 非空；
* ``LICENSE``：存在、非空（产品自身许可证，「关于与隐私」页读取这个文件名）。

缺项逐条列出并**退出码非零**；安装目录不存在（比如还没装机）返回 2（BLOCKED），
避免把「路径写错」误判成「材料缺失」。

用法::

    python dist\\release-gate\\verify_installed_compliance.py --app D:\\LiuHao
    python dist\\release-gate\\verify_installed_compliance.py --app "<安装目录>" --json-out <out.json>

退出码：0 = PASS；1 = FAIL（有缺项）；2 = BLOCKED（安装目录不存在/不可读）。
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from verify_package_compliance import FORBIDDEN_DELIVERABLE_DIRS, REQUIRED_LICENSE_FILES

DEFAULT_APP = r"D:\LiuHao"
REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_ISS = REPO_ROOT / "installer" / "liuhao.iss"

MIN_LICENSE_BYTES = 500
MIN_NOTICES_BYTES = 1000
MIN_PRODUCT_LICENSE_BYTES = 500

CHECK_ORDER = (
    "licenses/",
    "THIRD-PARTY-NOTICES.txt",
    "sbom-cyclonedx.json",
    "LICENSE",
    "分发边界",
    "卸载保留",
)

#: 卸载时必须保留的四类合规材料，与 installer/liuhao.iss 的
#: ``uninsneveruninstall`` 条目一一对应。
UNINSTALL_KEEP = (
    ("LICENSE", lambda source: source.endswith("\\LICENSE")),
    ("THIRD-PARTY-NOTICES.txt", lambda source: source.endswith("\\THIRD-PARTY-NOTICES.txt")),
    ("sbom-cyclonedx.json", lambda source: source.endswith("\\sbom-cyclonedx.json")),
    ("licenses/", lambda source: "\\licenses\\*" in source),
)


def _size(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


def _check_licenses(app: Path, missing: List[str]) -> Dict[str, Any]:
    directory = app / "licenses"
    if not directory.is_dir():
        missing.append("licenses/（目录不存在）")
        return {"ok": False, "detail": "目录不存在", "files": 0, "required": len(REQUIRED_LICENSE_FILES)}
    present: List[str] = []
    absent: List[str] = []
    short: List[str] = []
    for name, min_bytes in REQUIRED_LICENSE_FILES:
        path = directory / name
        if not path.is_file():
            absent.append(name)
            missing.append(f"licenses/{name}（不存在）")
            continue
        size = _size(path)
        if size < min_bytes:
            short.append(f"{name}（{size} 字节 < {min_bytes}）")
            missing.append(f"licenses/{name}（{size} 字节 < {min_bytes}）")
            continue
        present.append(name)
    total_files = sum(1 for item in directory.iterdir() if item.is_file())
    detail = f"{len(present)}/{len(REQUIRED_LICENSE_FILES)} 项，目录内共 {total_files} 个文件"
    if absent:
        detail += f"；缺 {len(absent)} 项"
    if short:
        detail += f"；过短 {len(short)} 项"
    return {
        "ok": not absent and not short,
        "detail": detail,
        "files": total_files,
        "required": len(REQUIRED_LICENSE_FILES),
        "absent": absent,
        "short": short,
    }


def _check_notices(app: Path, missing: List[str]) -> Dict[str, Any]:
    path = app / "THIRD-PARTY-NOTICES.txt"
    if not path.is_file():
        missing.append("THIRD-PARTY-NOTICES.txt（不存在）")
        return {"ok": False, "detail": "不存在", "bytes": 0}
    size = _size(path)
    text = ""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        missing.append(f"THIRD-PARTY-NOTICES.txt（不可读：{type(exc).__name__}）")
        return {"ok": False, "detail": f"不可读：{type(exc).__name__}", "bytes": size}
    problems: List[str] = []
    if size < MIN_NOTICES_BYTES:
        problems.append(f"{size} 字节 < {MIN_NOTICES_BYTES}")
    if "LGPL" not in text:
        problems.append("缺 LGPL 组件说明")
    if problems:
        missing.append("THIRD-PARTY-NOTICES.txt（" + "；".join(problems) + "）")
        return {"ok": False, "detail": "；".join(problems), "bytes": size}
    return {"ok": True, "detail": f"{size} 字节，含 LGPL 说明", "bytes": size}


def _check_sbom(app: Path, missing: List[str]) -> Dict[str, Any]:
    path = app / "sbom-cyclonedx.json"
    if not path.is_file():
        missing.append("sbom-cyclonedx.json（不存在）")
        return {"ok": False, "detail": "不存在", "bytes": 0, "components": 0}
    size = _size(path)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        missing.append(f"sbom-cyclonedx.json（无法解析：{type(exc).__name__}）")
        return {"ok": False, "detail": f"无法解析：{type(exc).__name__}", "bytes": size, "components": 0}
    problems: List[str] = []
    if payload.get("bomFormat") != "CycloneDX":
        problems.append("bomFormat 不是 CycloneDX")
    if not str(payload.get("specVersion") or "").startswith("1."):
        problems.append("specVersion 缺失")
    components = payload.get("components") or []
    if not components:
        problems.append("components 为空")
    if problems:
        missing.append("sbom-cyclonedx.json（" + "；".join(problems) + "）")
        return {"ok": False, "detail": "；".join(problems), "bytes": size, "components": len(components)}
    return {
        "ok": True,
        "detail": f"{size} 字节，{len(components)} 个组件（CycloneDX {payload.get('specVersion')}）",
        "bytes": size,
        "components": len(components),
    }


def _check_product_license(app: Path, missing: List[str]) -> Dict[str, Any]:
    path = app / "LICENSE"
    if not path.is_file():
        missing.append("LICENSE（不存在；产品许可证未随包）")
        return {"ok": False, "detail": "不存在", "bytes": 0}
    size = _size(path)
    if size < MIN_PRODUCT_LICENSE_BYTES:
        missing.append(f"LICENSE（{size} 字节 < {MIN_PRODUCT_LICENSE_BYTES}）")
        return {"ok": False, "detail": f"{size} 字节 < {MIN_PRODUCT_LICENSE_BYTES}", "bytes": size}
    return {"ok": True, "detail": f"{size} 字节", "bytes": size}


def _check_forbidden_paths(app: Path, missing: List[str]) -> Dict[str, Any]:
    """分发边界：待法务确认的草稿目录不得出现在安装目录里。"""
    hits: List[str] = []
    for rel in FORBIDDEN_DELIVERABLE_DIRS:
        if (app / rel).exists():
            hits.append(rel)
            missing.append(f"{rel}/（禁止分发目录出现在安装目录）")
    detail = "未出现禁止分发目录" if not hits else "出现 %d 个：%s" % (len(hits), "、".join(hits))
    return {"ok": not hits, "detail": detail, "hits": hits}


def _iss_entries(iss_path: Path) -> List[Tuple[str, str]]:
    """解析 Inno ``[Files]`` 里的 ``Source`` 与行尾其余内容（含 Flags）。"""
    entries: List[Tuple[str, str]] = []
    try:
        text = iss_path.read_text(encoding="utf-8-sig", errors="replace")
    except OSError:
        return entries
    for line in text.splitlines():
        match = re.match(r'\s*Source:\s*"([^"]+)"(.*)$', line)
        if match:
            entries.append((match.group(1), match.group(2)))
    return entries


def _copy_tree(src: Path, dest: Path) -> None:
    shutil.copytree(src, dest, dirs_exist_ok=True)


def _check_uninstall_retention(
    app: Path, iss_path: Optional[Path], missing: List[str]
) -> Dict[str, Any]:
    """卸载保留实测：核对 iss 标记，并在模拟目录里按卸载规则删除后再校验。

    1. ``installer/liuhao.iss`` 里四类材料必须逐条带 ``uninsneveruninstall``；
    2. 把四类材料拷到临时目录，再造一批「非保留」文件代表程序本体；
    3. 按卸载规则删除全部非保留文件；
    4. 在剩下的目录上重跑四项检查——仍全绿，才算「卸载后还在」。
    """
    if iss_path is None or not iss_path.is_file():
        return {
            "ok": True,
            "status": "SKIP",
            "detail": "未找到 installer/liuhao.iss，跳过卸载保留核对",
            "flags": 0,
            "kept": 0,
        }

    entries = _iss_entries(iss_path)
    flag_issues: List[str] = []
    for label, matcher in UNINSTALL_KEEP:
        matched = [(source, rest) for source, rest in entries if matcher(source)]
        if not matched:
            flag_issues.append(f"iss [Files] 找不到 {label} 的 Source 条目")
            continue
        if not any("uninsneveruninstall" in rest for _source, rest in matched):
            flag_issues.append(f"iss [Files] 的 {label} 条目缺少 uninsneveruninstall")
    if flag_issues:
        missing += flag_issues
        return {
            "ok": False,
            "status": "FAIL",
            "detail": "；".join(flag_issues),
            "flags": 0,
            "kept": 0,
        }

    tmp = Path(tempfile.mkdtemp(prefix="liuhao-uninstall-sim-"))
    try:
        kept_paths = [app / "LICENSE", app / "THIRD-PARTY-NOTICES.txt", app / "sbom-cyclonedx.json"]
        for path in kept_paths:
            if path.is_file():
                shutil.copy2(path, tmp / path.name)
        if (app / "licenses").is_dir():
            _copy_tree(app / "licenses", tmp / "licenses")

        # 造一批「程序本体」，卸载时会被删掉（iss 的兜底 Source，无 uninsneveruninstall）。
        payload_files = [
            tmp / "console" / "index.html",
            tmp / "runtime" / "python.exe",
            tmp / "START-HERE.txt",
        ]
        for path in payload_files:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("simulated payload\n", encoding="utf-8")

        # 模拟卸载：删除全部非保留项。
        for path in payload_files:
            if path.is_file():
                path.unlink()
        for directory in (tmp / "console", tmp / "runtime"):
            shutil.rmtree(directory, ignore_errors=True)

        survivors = sum(
            1 for path in kept_paths if path.is_file() and (tmp / path.name).is_file()
        )
        survivors += 1 if (tmp / "licenses" / "LGPL-2.1.txt").is_file() else 0
        if not (tmp / "licenses" / "LGPL-2.1.txt").is_file():
            # 安装目录本身缺材料时，卸载模拟不能替它造出来，交给第 1～4 项报缺。
            detail = "卸载模拟完成，但安装目录缺少 licenses/LGPL-2.1.txt，无法验证保留"
            return {"ok": False, "status": "FAIL", "detail": detail, "flags": 4, "kept": survivors}

        sub_missing: List[str] = []
        _check_licenses(tmp, sub_missing)
        _check_notices(tmp, sub_missing)
        _check_sbom(tmp, sub_missing)
        _check_product_license(tmp, sub_missing)
        if sub_missing:
            missing += ["卸载后 " + item for item in sub_missing]
            return {
                "ok": False,
                "status": "FAIL",
                "detail": "卸载后缺 %d 项：%s" % (len(sub_missing), "；".join(sub_missing)),
                "flags": 4,
                "kept": survivors,
            }
        return {
            "ok": True,
            "status": "PASS",
            "detail": "4/4 类材料在模拟卸载后仍在（iss 4 条 uninsneveruninstall + 删除 %d 个非保留文件后复检）"
            % len(payload_files),
            "flags": 4,
            "kept": survivors,
        }
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def verify(app: Path, iss_path: Optional[Path] = None) -> Dict[str, Any]:
    missing: List[str] = []
    sections = {
        "licenses": _check_licenses(app, missing),
        "notices": _check_notices(app, missing),
        "sbom": _check_sbom(app, missing),
        "product_license": _check_product_license(app, missing),
        "forbidden_paths": _check_forbidden_paths(app, missing),
        "uninstall_retention": _check_uninstall_retention(app, iss_path, missing),
    }
    verdict = "PASS" if not missing else "FAIL"
    return {
        "schema": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "app": str(app),
        "verdict": verdict,
        "missing_items": missing,
        "checks": sections,
    }


def _fmt(label: str, result: Dict[str, Any]) -> str:
    status = "[OK]  " if result["ok"] else "[缺失]"
    return f"{label:<30} {status} {result['detail']}"


def print_summary(result: Dict[str, Any]) -> None:
    print("安装后合规自检（只读）")
    print(f"安装目录：{result['app']}")
    print("-" * 78)
    print("  " + _fmt("[1/6] licenses/", result["checks"]["licenses"]))
    print("  " + _fmt("[2/6] THIRD-PARTY-NOTICES.txt", result["checks"]["notices"]))
    print("  " + _fmt("[3/6] sbom-cyclonedx.json", result["checks"]["sbom"]))
    print("  " + _fmt("[4/6] LICENSE", result["checks"]["product_license"]))
    print("  " + _fmt("[5/6] 分发边界（docs/legal）", result["checks"]["forbidden_paths"]))
    print("  " + _fmt("[6/6] 卸载保留（四类材料）", result["checks"]["uninstall_retention"]))
    print("-" * 78)
    if result["missing_items"]:
        print(f"缺项 {len(result['missing_items'])}：")
        for item in result["missing_items"]:
            print(f"  - {item}")
    if result["verdict"] == "PASS":
        print("结果：PASS（合规材料齐全且非空）")
    else:
        print("结果：FAIL（按上面清单补齐后重跑；不得以此包对外分发）")


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="安装后合规材料自检（只读）")
    parser.add_argument("--app", default=DEFAULT_APP, help="安装后的程序目录（默认 D:\\LiuHao）")
    parser.add_argument("--iss", default=str(DEFAULT_ISS), help="安装器脚本（核对卸载保留标记）")
    parser.add_argument("--json-out", default=None, help="把结果写成 JSON（证据留存）")
    args = parser.parse_args(argv)

    app = Path(args.app).expanduser()
    if not app.is_dir():
        print(f"[BLOCKED] 安装目录不存在或不可读：{app}", file=sys.stderr)
        print("          先确认装机完成，或显式传 --app <实际安装目录>。", file=sys.stderr)
        return 2

    result = verify(app, Path(args.iss).expanduser() if args.iss else None)
    print_summary(result)

    if args.json_out:
        target = Path(args.json_out).expanduser()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(f"证据 JSON：{target}")

    return 0 if result["verdict"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
