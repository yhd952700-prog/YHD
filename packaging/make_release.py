"""生成升级用的发布包与发布清单（严格按 docs/self-upgrade.md §2.2 规范 v1）。

产物（同一目录，默认 ``dist\\release``）：

    liuhao-<version>.zip      安装树 ZIP（根目录即安装根，不多包一层）
    LIUHAO_RELEASE.json       发布清单（schema = liuhao.release-manifest/1）
    dev-path-report.txt       开发目录路径扫描结果（排障用，不参与升级）

规范要点：
  * ZIP 只允许 ``src/``、``docs/``、``pyproject.toml``、``requirements.txt``、
    ``oss-registry.yaml`` 与根目录包内清单 ``LIUHAO_RELEASE.json``；
  * 禁止 ``data/``、``.git/``、``.venv/``、``.env``、``secrets/``、``*.pem``/``*.key``/``*.crt``；
  * ``sha256`` 对**整包字节**计算，``size`` 为字节数；
  * 包内版本（包根 LIUHAO_RELEASE.json + pyproject.toml）必须与清单 ``version`` 完全相等；
  * 有签名私钥才写 ``signature`` / ``signature_algorithm``，没有就**不写**空值或假签名；
  * 自己产生清单、自己回读校验；任何不一致直接失败，不产出坏清单。

用法（在仓库根执行）：

    .venv\\Scripts\\python.exe packaging\\make_release.py
    .venv\\Scripts\\python.exe packaging\\make_release.py --out dist\\release --strict-dev-paths
    $env:LIUHAO_UPGRADE_SIGNING_KEY = "D:\\keys\\liuhao-release-ed25519.pem"
    .venv\\Scripts\\python.exe packaging\\make_release.py --signing-key $env:LIUHAO_UPGRADE_SIGNING_KEY
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import subprocess
import sys
import tomllib
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Tuple

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

MANIFEST_NAME = "LIUHAO_RELEASE.json"
PACKAGE_SCHEMA = "liuhao.release-package/1"
MANIFEST_SCHEMA = "liuhao.release-manifest/1"
PRODUCT = "liuhao-ai-os"

PACKAGE_ROOTS = ("src", "docs", "pyproject.toml", "requirements.txt", "oss-registry.yaml")
# 与 src/upgrade/manager.py 的默认 install_roots / protected_paths 保持一致。
INSTALL_ROOTS = PACKAGE_ROOTS + (MANIFEST_NAME,)
PROTECTED_PATHS = (
    "data",
    ".git",
    ".venv",
    ".env",
    "secrets",
    "*.pem",
    "*.key",
    "*.crt",
    "upgrade-backups",
    "upgrade-state",
)
SKIP_DIR_NAMES = {"__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", ".git"}
SKIP_SUFFIXES = {".pyc", ".pyo"}
TEXT_SUFFIXES = {
    ".py", ".md", ".toml", ".txt", ".yaml", ".yml", ".json", ".iss",
    ".ps1", ".bat", ".cmd", ".ini", ".cfg", ".sh", ".sql",
}
# 开发机绝对路径 / 开发虚拟环境路径（含转义写法 D:\\... 与 .venv\\Scripts）。
DEV_PATH_RE = re.compile(
    r"(?i)(?:D:(?:\\\\|\\|/)LiuHao-AI-OS|\.venv(?:\\\\|\\|/)(?:Scripts|bin))"
)
FIXED_ZIP_TIME = (1980, 1, 1, 0, 0, 0)
MAX_ENTRIES = 100_000
MAX_UNCOMPRESSED = 2 * 1024 * 1024 * 1024
MAX_SIGNING_BYTES = 512 * 1024 * 1024


class BuildError(Exception):
    """构建失败（打印原因后以非零退出码结束）。"""


def _log(message: str = "") -> None:
    print(message, flush=True)


def _now_iso() -> str:
    return (
        datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    )


def _read_pyproject_version(path: Path) -> str:
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError, UnicodeDecodeError) as exc:
        raise BuildError(f"无法读取 {path}：{exc}") from exc
    project = data.get("project")
    value = project.get("version") if isinstance(project, dict) else None
    if not isinstance(value, str) or not value.strip():
        raise BuildError(f"{path} 缺少 [project].version")
    return value.strip()


def _validate_version(version: str, context: str) -> None:
    try:
        from packaging.version import InvalidVersion, Version

        Version(version)
    except ImportError:  # packaging 是运行依赖，缺了就直接失败而不是跳过校验
        raise BuildError("缺少 packaging 库，无法校验版本号（请先安装项目依赖）")
    except InvalidVersion as exc:
        raise BuildError(f"{context} 不是合法的 PEP 440 版本号：{version!r}") from exc


def _collect_files(root: Path) -> List[Tuple[str, Path]]:
    """按白名单收集待打包文件（相对 POSIX 路径 + 绝对路径），排序保证可复现。"""
    items: List[Tuple[str, Path]] = []
    seen: set[str] = set()
    for entry in PACKAGE_ROOTS:
        path = root / entry
        if not path.exists():
            if entry in ("requirements.txt", "oss-registry.yaml"):
                _log(f"  [跳过] 未找到 {entry}（允许缺失）")
                continue
            raise BuildError(f"缺少打包输入：{entry}")
        candidates: List[Path] = []
        if path.is_dir():
            candidates = sorted(path.rglob("*"))
        else:
            candidates = [path]
        for candidate in candidates:
            if candidate.is_symlink():
                raise BuildError(f"不允许符号链接：{candidate.relative_to(root).as_posix()}")
            if not candidate.is_file():
                continue
            parts = candidate.relative_to(root).parts
            if any(part in SKIP_DIR_NAMES for part in parts):
                continue
            if candidate.suffix.lower() in SKIP_SUFFIXES:
                continue
            rel = candidate.relative_to(root).as_posix()
            if rel in seen:
                raise BuildError(f"重复路径：{rel}")
            seen.add(rel)
            items.append((rel, candidate))
    items.sort(key=lambda item: item[0])
    if not items:
        raise BuildError("没有可打包的文件")
    if len(items) > MAX_ENTRIES:
        raise BuildError(f"文件数量过多（{len(items)} > {MAX_ENTRIES}）")
    total = sum(path.stat().st_size for _rel, path in items)
    if total > MAX_UNCOMPRESSED:
        raise BuildError(f"解压后体积超过上限（{total} 字节 > {MAX_UNCOMPRESSED}）")
    return items


def _zip_write(zf: zipfile.ZipFile, rel: str, data: bytes) -> None:
    info = zipfile.ZipInfo(filename=rel, date_time=FIXED_ZIP_TIME)
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = 0o100644 << 16
    zf.writestr(info, data)


def _scan_dev_paths(entries: List[Tuple[str, Path]]) -> List[Tuple[str, int, str]]:
    hits: List[Tuple[str, int, str]] = []
    for rel, path in entries:
        if path.suffix.lower() not in TEXT_SUFFIXES:
            continue
        try:
            if path.stat().st_size > 4 * 1024 * 1024:
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for lineno, line in enumerate(text.splitlines(), 1):
            if DEV_PATH_RE.search(line):
                hits.append((rel, lineno, line.strip()[:200]))
    return hits


def _write_report(out_dir: Path, hits: List[Tuple[str, int, str]]) -> Path:
    report = out_dir / "dev-path-report.txt"
    runtime = [item for item in hits if not item[0].startswith("docs/")]
    docs = [item for item in hits if item[0].startswith("docs/")]
    lines = [
        "开发目录路径扫描（packaging/make_release.py）",
        f"生成时间：{_now_iso()}",
        f"运行文件命中：{len(runtime)}（src/ 等参与运行的文件，属于缺陷）",
        f"文档引用命中：{len(docs)}（docs/ 里的历史链接，不影响安装与升级）",
        "",
        "说明：--strict-dev-paths 会把“运行文件命中”当作构建失败。",
        "",
        "[运行文件命中]",
    ]
    for rel, lineno, text in runtime:
        lines.append(f"{rel}:{lineno}: {text}")
    lines.append("")
    lines.append("[文档引用命中]")
    for rel, lineno, text in docs:
        lines.append(f"{rel}:{lineno}: {text}")
    report.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return report


def _load_signing_key(path: Path) -> bytes:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    try:
        key = serialization.load_pem_private_key(path.read_bytes(), password=None)
    except Exception as exc:  # noqa: BLE001 - 任何解析失败都要明确报错
        raise BuildError(f"签名私钥无法解析（需要未加密的 Ed25519 PEM）：{path}（{exc}）") from exc
    if not isinstance(key, Ed25519PrivateKey):
        raise BuildError(f"签名私钥不是 Ed25519：{path}（实际 {type(key).__name__}）")
    return key


def _verify_with_updater(zf: zipfile.ZipFile) -> None:
    """复用升级器的归档校验，确保打包端与安装端同一套规则。"""
    from src.upgrade.archive import validate_release_zip

    members = validate_release_zip(
        zf,
        install_roots=INSTALL_ROOTS,
        protected_prefixes=PROTECTED_PATHS,
        max_uncompressed_bytes=MAX_UNCOMPRESSED,
        max_entries=MAX_ENTRIES,
    )
    if not any(rel == "pyproject.toml" for rel, _info in members):
        raise BuildError("发布包内缺少 pyproject.toml")


def _verify_package(zip_path: Path, expected_version: str) -> Tuple[str, int]:
    """回读 ZIP，校验版本标记与 pyproject 版本；返回 (sha256, size)。"""
    sha = hashlib.sha256()
    with open(zip_path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            sha.update(chunk)
    size = zip_path.stat().st_size

    with zipfile.ZipFile(zip_path) as zf:
        _verify_with_updater(zf)
        try:
            marker = json.loads(zf.read(MANIFEST_NAME).decode("utf-8"))
        except (KeyError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise BuildError(f"发布包根目录缺少可解析的 {MANIFEST_NAME}：{exc}") from exc
        if marker.get("schema") != PACKAGE_SCHEMA:
            raise BuildError(f"包内清单 schema 必须是 {PACKAGE_SCHEMA}，实际 {marker.get('schema')!r}")
        if marker.get("product") != PRODUCT:
            raise BuildError(f"包内清单 product 必须是 {PRODUCT}，实际 {marker.get('product')!r}")
        marker_version = str(marker.get("version") or "").strip()
        if marker_version != expected_version:
            raise BuildError(
                f"包内 LIUHAO_RELEASE.json 版本 {marker_version!r} 与清单版本 {expected_version!r} 不一致"
            )
        try:
            pyproject_version = _read_pyproject_version_from_bytes(zf.read("pyproject.toml"))
        except KeyError as exc:
            raise BuildError("发布包内缺少 pyproject.toml") from exc
        if pyproject_version != expected_version:
            raise BuildError(
                f"包内 pyproject.toml 版本 {pyproject_version!r} 与清单版本 {expected_version!r} 不一致"
            )
    return sha.hexdigest(), size


def _read_pyproject_version_from_bytes(payload: bytes) -> str:
    data = tomllib.loads(payload.decode("utf-8"))
    project = data.get("project")
    value = project.get("version") if isinstance(project, dict) else None
    if not isinstance(value, str) or not value.strip():
        raise BuildError("包内 pyproject.toml 缺少 [project].version")
    return value.strip()


def _merge_history(manifest_path: Path, new_release: Dict[str, Any]) -> List[Dict[str, Any]]:
    releases: List[Dict[str, Any]] = []
    if manifest_path.is_file():
        try:
            previous = json.loads(manifest_path.read_text(encoding="utf-8"))
            for item in previous.get("releases") or []:
                version = str(item.get("version") or "").strip()
                if not version or version == new_release["version"]:
                    continue
                releases.append(item)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            _log("  [提示] 既有发布清单不可解析，将只写入本次发布")
    releases.append(new_release)
    try:
        from packaging.version import Version

        releases.sort(key=lambda item: Version(str(item.get("version"))))
    except Exception:  # noqa: BLE001 - 排序失败不影响清单有效性
        pass
    return releases


def _verify_manifest_file(manifest_path: Path, zip_path: Path, version: str) -> None:
    try:
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BuildError(f"回读发布清单失败：{exc}") from exc
    if data.get("schema") != MANIFEST_SCHEMA:
        raise BuildError(f"发布清单 schema 必须是 {MANIFEST_SCHEMA}")
    releases = data.get("releases")
    if not isinstance(releases, list) or not releases:
        raise BuildError("发布清单 releases 必须是非空数组")
    try:
        from src.upgrade.sources import parse_manifest, select_latest

        parsed = parse_manifest(json.dumps(data, ensure_ascii=False))
        latest = select_latest(parsed)
    except Exception as exc:  # noqa: BLE001 - 升级器的校验失败就是构建失败
        raise BuildError(f"发布清单未通过升级器校验：{exc}") from exc
    if latest.version != version:
        raise BuildError(f"发布清单最新版本 {latest.version!r} 不是本次版本 {version!r}")
    if latest.artifact != zip_path.name:
        raise BuildError(f"发布清单 artifact {latest.artifact!r} 与产物文件名 {zip_path.name!r} 不一致")
    sha, size = _verify_package(zip_path, version)
    if latest.sha256 != sha:
        raise BuildError("发布清单 sha256 与回读 ZIP 计算值不一致")
    if latest.size != size:
        raise BuildError(f"发布清单 size {latest.size} 与回读 ZIP 字节数 {size} 不一致")


def _run_cli_check(root: Path, out_dir: Path) -> Tuple[int, str]:
    """用真实升级 CLI 读一遍刚产出的清单（版本与当前源码一致时应为“已是最新”）。"""
    cmd = [
        sys.executable,
        "-m",
        "src.upgrade",
        "check",
        "--app-root",
        str(root),
        "--release-dir",
        str(out_dir),
    ]
    completed = subprocess.run(
        cmd,
        cwd=str(root),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return completed.returncode, (completed.stdout or "") + (completed.stderr or "")


def build(args: argparse.Namespace) -> int:
    root = Path(args.root).resolve()
    out_dir = Path(args.out).resolve() if args.out else root / "dist" / "release"
    out_dir.mkdir(parents=True, exist_ok=True)

    pyproject = root / "pyproject.toml"
    version = (args.version or _read_pyproject_version(pyproject)).strip()
    _validate_version(version, "发布版本号")
    if args.version:
        _validate_version(_read_pyproject_version(pyproject), "pyproject.toml 版本号")
        if version != _read_pyproject_version(pyproject):
            raise BuildError(
                "--version 与 pyproject.toml 版本不一致："
                f"{version!r} != {_read_pyproject_version(pyproject)!r}"
            )

    released_at = args.released_at or _now_iso()
    zip_name = f"liuhao-{version}.zip"
    zip_path = out_dir / zip_name
    manifest_path = out_dir / MANIFEST_NAME

    _log(f"鎏灏发布包构建  version={version}")
    _log(f"  仓库    {root}")
    _log(f"  输出    {out_dir}")

    entries = _collect_files(root)
    total_bytes = sum(path.stat().st_size for _rel, path in entries)
    _log(f"  输入    {len(entries)} 个文件 / {total_bytes / 1024 / 1024:.1f} MB（未压缩）")

    hits = _scan_dev_paths(entries)
    runtime_hits = [item for item in hits if not item[0].startswith("docs/")]
    report = _write_report(out_dir, hits)
    for scope in ("src", "docs"):
        count = sum(1 for rel, _ln, _text in hits if rel.startswith(scope + "/"))
        if count:
            _log(f"  [开发路径] {scope}/ 命中 {count} 个文件（详见 {report}）")
    if runtime_hits and args.strict_dev_paths:
        for rel, lineno, text in runtime_hits[:20]:
            _log(f"    {rel}:{lineno}: {text}")
        raise BuildError(
            f"--strict-dev-paths：发布包仍有 {len(runtime_hits)} 处开发目录路径字面量，"
            f"需先由源码归属方清理（报告：{report}）"
        )

    marker = {
        "schema": PACKAGE_SCHEMA,
        "product": PRODUCT,
        "version": version,
        "released_at": released_at,
    }
    marker_bytes = (json.dumps(marker, ensure_ascii=False, indent=2) + "\n").encode("utf-8")

    tmp_zip = out_dir / f".{zip_name}.tmp"
    tmp_zip.unlink(missing_ok=True)
    with zipfile.ZipFile(tmp_zip, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        _zip_write(zf, MANIFEST_NAME, marker_bytes)
        for rel, path in entries:
            _zip_write(zf, rel, path.read_bytes())
    os.replace(tmp_zip, zip_path)

    sha256, size = _verify_package(zip_path, version)
    _log(f"  产物    {zip_name}  {size} 字节（{size / 1024 / 1024:.1f} MB）")
    _log(f"  SHA256  {sha256}")

    signature: str | None = None
    key_path = args.signing_key or os.environ.get("LIUHAO_UPGRADE_SIGNING_KEY") or ""
    if key_path:
        key_file = Path(key_path).expanduser()
        if not key_file.is_file():
            raise BuildError(f"配置了签名私钥但文件不存在：{key_file}（拒绝产出未签名清单冒充已签名）")
        if size > MAX_SIGNING_BYTES:
            raise BuildError(f"发布包超过 {MAX_SIGNING_BYTES} 字节，拒绝在内存中签名")
        private_key = _load_signing_key(key_file)
        signature = base64.b64encode(private_key.sign(zip_path.read_bytes())).decode("ascii")
        _log("  签名    Ed25519（对 ZIP 完整字节）已写入清单")
    else:
        _log("  签名    未配置私钥，清单不含 signature 字段（不写空值/假签名）")

    release: Dict[str, Any] = {
        "version": version,
        "artifact": zip_name,
        "sha256": sha256,
        "size": size,
        "notes": args.notes or (
            f"鎏灏 LiuHao AI OS {version}：源码与文档整包；不含模型权重、不含任何密钥。"
            "安装器未做代码签名，Windows 首次运行需选择“仍要运行”。"
        ),
        "released_at": released_at,
    }
    if signature:
        release["signature"] = signature
        release["signature_algorithm"] = "ed25519"
    if runtime_hits:
        release["notes"] = (
            release["notes"]
            + f"（打包扫描发现 {len(runtime_hits)} 处运行文件里的开发目录路径字面量，见 dev-path-report.txt）"
        )

    releases = _merge_history(manifest_path, release)
    manifest = {
        "schema": MANIFEST_SCHEMA,
        "channel": args.channel,
        "generated_at": _now_iso(),
        "releases": releases,
    }
    tmp_manifest = out_dir / f".{MANIFEST_NAME}.tmp"
    tmp_manifest.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    os.replace(tmp_manifest, manifest_path)
    _log(f"  清单    {manifest_path}")

    _verify_manifest_file(manifest_path, zip_path, version)
    _log("  自检    清单 size/sha256/版本/artifact 与回读 ZIP 一致")

    if not args.no_cli_check:
        code, output = _run_cli_check(root, out_dir)
        _log(f"  升级器  python -m src.upgrade check --release-dir {out_dir} → exit {code}")
        for line in output.strip().splitlines():
            _log(f"          {line}")
        if code != 0:
            _log("  [失败] 升级器没有读取通过这份清单（见上方输出）")
            manifest_path.unlink(missing_ok=True)
            raise BuildError("升级器校验未通过：已删除本次清单，不产出坏清单")

    _log("")
    _log(f"完成：{zip_path}")
    _log(f"      {manifest_path}")
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python packaging/make_release.py",
        description="生成升级发布包 ZIP 与 LIUHAO_RELEASE.json（规范见 docs/self-upgrade.md §2.2）",
    )
    parser.add_argument("--root", default=str(REPO_ROOT), help="仓库根目录（默认脚本上级目录）")
    parser.add_argument("--out", default=None, help="输出目录（默认 <root>/dist/release）")
    parser.add_argument("--version", default=None, help="发布版本号（默认读 pyproject.toml，必须一致）")
    parser.add_argument("--signing-key", default=None, help="Ed25519 私钥 PEM；也可用环境变量 LIUHAO_UPGRADE_SIGNING_KEY")
    parser.add_argument("--notes", default=None, help="清单 notes（默认自动生成）")
    parser.add_argument("--channel", default="stable", help="清单 channel 字段（升级器忽略）")
    parser.add_argument("--released-at", default=None, help="released_at（默认当前 UTC 时间）")
    parser.add_argument(
        "--strict-dev-paths",
        action="store_true",
        help="开发目录路径字面量直接判定构建失败（源码清理完成后建议打开）",
    )
    parser.add_argument(
        "--no-cli-check",
        action="store_true",
        help="不调用 src.upgrade check 做端到端读取验证（仅排查环境问题用）",
    )
    return parser


def main(argv: List[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    args = _build_parser().parse_args(argv)
    try:
        return build(args)
    except BuildError as exc:
        print(f"[失败] {exc}", file=sys.stderr, flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
