"""前后端契约签章与校验（升级包 / 安装树）。

为什么需要它
------------
升级只替换后端、却把旧前端留在安装目录里，服务照样健康，但浏览器加载的是
旧界面：字段改名、接口路径变化时表现为随机 400、空面板或按钮点了没反应。
这种缺陷不会让 ``/v1/health`` 变红，所以必须单独设卡。

做法：安装树与控制台产物里带一份签章 ``console/console-contract.json``，
内容包含后端声明的契约版本、控制台文件指纹、本次发布的版本号。打包端生成，
安装端在两处校验：

* **落盘前**：包里必须有 ``console/index.html``；签章必须存在、格式正确、
  版本等于本次发布版本、契约版本等于**包内后端**声明、指纹等于**包内控制台**
  实际字节。任一项不符即拒绝升级，不做任何改动。
* **落盘后**：安装目录里的控制台与后端必须仍然一致，且真实启动的服务在
  ``/v1/health`` 上报告的 ``api_contract`` 必须与签章相同。不符即自检失败，
  管理器自动回滚。

纯开发/仓库布局（安装根根本没有 ``console/``）不受此门禁约束，但仍会记录
``not_applicable``，避免把开发流程卡死。
"""

from __future__ import annotations

import ast
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Tuple

from .errors import UpgradeError

CONTRACT_SCHEMA = "liuhao.console-contract/1"
CONTRACT_RELPATH = "console/console-contract.json"
INDEX_RELPATH = "console/index.html"
BACKEND_SOURCE_RELPATH = "src/gateway/contract.py"
_CONTRACT_ASSIGN = re.compile(
    r"""API_CONTRACT_VERSION\s*(?::\s*str\s*)?=\s*["']([^"']+)["']"""
)


class FrontendContractError(UpgradeError):
    """前端与后端不匹配，或包内缺少控制台产物。"""


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def console_files_under(mapping: Mapping[str, bytes]) -> Dict[str, bytes]:
    """挑出 ``console/`` 下的文件，排除签章自身，键统一成 POSIX 相对路径。"""
    picked: Dict[str, bytes] = {}
    for rel, data in mapping.items():
        normalized = rel.replace("\\", "/")
        if normalized == CONTRACT_RELPATH:
            continue
        if normalized.startswith("console/"):
            picked[normalized] = data
    return picked


def console_fingerprint(console_files: Mapping[str, bytes]) -> str:
    """对控制台文件集合做与顺序无关的稳定指纹。

    先按路径排序，再对每个文件写 ``路径 + 内容 SHA256``，最后整体再取一次
    SHA256。这样同一份构建产物无论在 ZIP 里还是解压到磁盘，指纹都一样。
    """
    digest = hashlib.sha256()
    for rel in sorted(console_files):
        digest.update(rel.encode("utf-8"))
        digest.update(b"\0")
        digest.update(_sha256_bytes(console_files[rel]).encode("ascii"))
        digest.update(b"\n")
    return digest.hexdigest()


def read_backend_contract_from_source(source: str) -> str:
    """从 ``src/gateway/contract.py`` 源码里取 ``API_CONTRACT_VERSION``。

    用 ``ast`` 只用字面量赋值，不执行包里的代码；解析失败再退回正则，
    两次都失败就拒绝（不能默认放行）。
    """
    try:
        tree = ast.parse(source)
        for node in tree.body:
            targets = []
            if isinstance(node, ast.Assign):
                targets = node.targets
                value = node.value
            elif isinstance(node, ast.AnnAssign) and node.value is not None:
                targets = [node.target]
                value = node.value
            else:
                continue
            for target in targets:
                if isinstance(target, ast.Name) and target.id == "API_CONTRACT_VERSION":
                    literal = ast.literal_eval(value)
                    if isinstance(literal, str) and literal.strip():
                        return literal.strip()
    except (SyntaxError, ValueError):
        pass
    match = _CONTRACT_ASSIGN.search(source)
    if match:
        return match.group(1).strip()
    raise FrontendContractError(
        "无法从包内 src/gateway/contract.py 读取 API_CONTRACT_VERSION："
        "后端没有声明契约版本，无法判断前端是否匹配，已拒绝升级。"
    )


def build_stamp(
    console_files: Mapping[str, bytes],
    *,
    backend_contract: str,
    version: str,
    built_at: Optional[str] = None,
) -> Dict[str, Any]:
    """生成可直接写盘的签章内容（不含签章文件自身）。"""
    if not console_files:
        raise FrontendContractError(
            "控制台产物为空：升级包没有可用于校验的前端文件，已拒绝升级。"
        )
    return {
        "schema": CONTRACT_SCHEMA,
        "api_contract": str(backend_contract),
        "frontend_version": str(version),
        "console_fingerprint": console_fingerprint(console_files),
        "console_files": len(console_files),
        "built_at": built_at
        or datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
    }


def verify_console_set(
    all_files: Mapping[str, bytes],
    *,
    backend_contract: Optional[str] = None,
    version: Optional[str] = None,
    source: str = "升级包",
) -> Dict[str, Any]:
    """校验一份「安装根整体文件集合」的前后端一致性。

    ``all_files`` 至少应包含 ``console/**`` 与 ``src/gateway/contract.py``。
    返回值是签章内容，供写入 ledger 作为证据。
    """
    if INDEX_RELPATH not in all_files:
        raise FrontendContractError(
            f"{source}缺少控制台构建产物 {INDEX_RELPATH}："
            "安装后界面会停留在旧版本（或直接空白），已拒绝继续。"
            "请用包含前端的安装树重新打包："
            "packaging/stage_payload.py → packaging/make_release.py --app-root。"
        )
    raw_stamp = all_files.get(CONTRACT_RELPATH)
    if raw_stamp is None:
        raise FrontendContractError(
            f"{source}缺少前后端契约签章 {CONTRACT_RELPATH}："
            "无法确认前端与后端属于同一次构建，已拒绝继续。"
        )
    try:
        stamp = json.loads(raw_stamp.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise FrontendContractError(
            f"{source}的契约签章无法解析（{exc}）：已拒绝继续。"
        ) from exc
    if not isinstance(stamp, dict) or stamp.get("schema") != CONTRACT_SCHEMA:
        raise FrontendContractError(
            f"{source}的契约签章格式不正确（schema 必须是 {CONTRACT_SCHEMA}）：已拒绝继续。"
        )

    declared = str(stamp.get("api_contract") or "").strip()
    backend_source = all_files.get(BACKEND_SOURCE_RELPATH)
    actual_backend = (
        read_backend_contract_from_source(backend_source.decode("utf-8", errors="replace"))
        if backend_source is not None
        else backend_contract
    )
    if not actual_backend:
        raise FrontendContractError(
            f"{source}缺少后端契约声明（{BACKEND_SOURCE_RELPATH}）：已拒绝继续。"
        )
    if declared != actual_backend:
        raise FrontendContractError(
            f"前后端契约不一致：前端签章要求 API 契约 {declared!r}，"
            f"后端声明为 {actual_backend!r}。"
            "为避免界面与接口错配（例如旧界面调用新接口），已拒绝升级。"
        )

    console_files = console_files_under(all_files)
    actual_fingerprint = console_fingerprint(console_files)
    if actual_fingerprint != stamp.get("console_fingerprint"):
        raise FrontendContractError(
            "控制台产物与契约签章不匹配"
            f"（签章 {stamp.get('console_fingerprint')!r} ≠ 实际 {actual_fingerprint!r}）。"
            "常见原因：旧前端被混进了新包，或打包后没有重新签章。已拒绝升级。"
        )
    if int(stamp.get("console_files") or 0) != len(console_files):
        raise FrontendContractError(
            "控制台文件数量与契约签章不一致"
            f"（签章 {stamp.get('console_files')} ≠ 实际 {len(console_files)}）。已拒绝升级。"
        )
    if version is not None and str(stamp.get("frontend_version") or "") != str(version):
        raise FrontendContractError(
            "前端版本与本次发布版本不一致"
            f"（前端 {stamp.get('frontend_version')!r} ≠ 发布 {version!r}）："
            "升级后界面不会更新，已拒绝升级。"
        )
    return stamp


def verify_package(zip_file: Any, version: str) -> Dict[str, Any]:
    """校验升级包（打开的 ``zipfile.ZipFile``）。"""
    names = [name for name in zip_file.namelist() if not name.endswith("/")]
    payload: Dict[str, bytes] = {}
    for name in names:
        normalized = name.replace("\\", "/")
        if normalized.startswith("console/") or normalized == BACKEND_SOURCE_RELPATH:
            payload[normalized] = zip_file.read(name)
    return verify_console_set(payload, version=version, source="升级包")


def verify_installed(app_root: Path | str) -> Tuple[bool, Dict[str, Any], str]:
    """校验安装目录。返回 ``(是否适用, 签章, 说明)``。

    安装根没有 ``console/`` 时视为纯开发布局，不适用（返回 False），
    但调用方必须把 ``not_applicable`` 记进 ledger，不能静默。
    """
    root = Path(app_root)
    console_dir = root / "console"
    if not console_dir.is_dir():
        return False, {}, "安装根没有 console/（开发/便携布局），前后端契约门禁不适用"
    files: Dict[str, bytes] = {}
    for path in console_dir.rglob("*"):
        if path.is_file():
            files[path.relative_to(root).as_posix()] = path.read_bytes()
    backend_path = root / BACKEND_SOURCE_RELPATH
    if backend_path.is_file():
        files[BACKEND_SOURCE_RELPATH] = backend_path.read_bytes()
    stamp = verify_console_set(files, source="安装目录")
    return True, stamp, ""


def verify_health_contract(
    stamp: Mapping[str, Any], health_body: Mapping[str, Any]
) -> None:
    """真实启动的服务必须在 /v1/health 上报告同一个 ``api_contract``。"""
    running = str(health_body.get("api_contract") or "").strip()
    expected = str(stamp.get("api_contract") or "").strip()
    if not running:
        raise FrontendContractError(
            "运行中的服务没有在 /v1/health 报告 api_contract："
            "无法确认前后端属于同一次构建，已拒绝放行。"
        )
    if running != expected:
        raise FrontendContractError(
            f"运行中的后端契约 {running!r} 与安装目录前端签章 {expected!r} 不一致："
            "界面与接口可能错配，已拒绝放行。"
        )
