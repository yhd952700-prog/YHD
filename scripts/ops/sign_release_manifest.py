#!/usr/bin/env python
"""Release manifest signing tool for LiuHao AI OS (Ed25519).

维护者用它做三件事：

* ``keygen``           生成 Ed25519 密钥对（私钥落本机、DPAPI 绑定，不进仓库）
* ``sign``             给 ``LIUHAO_RELEASE.json`` 的每个发布项写 ``signature``，
                       并写清单级 ``manifest_signature`` 与递增的 ``sequence``
* ``export-public-key``导出公钥（PEM 或 base64），用于客户端
                       ``LIUHAO_UPGRADE_PUBLIC_KEY`` 与公钥轮换记录

设计约束（与 ``src/upgrade/downloader.py`` 的校验口径一致）：

* 签名对象是**发布包文件字节**（不是清单文本）；``signature`` 是 base64，
  ``signature_algorithm`` 固定 ``ed25519``。
* 签名前先核对清单里的 ``sha256`` / ``size`` 与实际文件一致：不一致就拒绝
  签名，绝不把"另一份文件"签成"这份清单"。
* 私钥用项目既有的 machine-bound 封装（Windows DPAPI 用户级绑定；非 Windows
  显式为 ``LIUHAO-RAW-1``），换机/换用户解不开是预期行为。
* 私钥目录必须位于仓库之外；工具从不打印私钥内容。

示例::

    python scripts/ops/sign_release_manifest.py keygen
    python scripts/ops/sign_release_manifest.py sign \
        --manifest dist/release/LIUHAO_RELEASE.json --artifact-dir dist/release
    python scripts/ops/sign_release_manifest.py export-public-key --format base64
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Optional, Sequence

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519

from src.common.atomic_write import atomic_write_bytes, atomic_write_text
from src.security.encryption import (
    MachineBoundKeyError,
    is_machine_bound_payload,
    unwrap_machine_bound,
    wrap_machine_bound,
)
from src.upgrade.sources import parse_manifest
from src.upgrade.manifest_trust import (
    MANIFEST_SIGNATURE_ALGORITHM_FIELD,
    MANIFEST_SIGNATURE_FIELD,
    MANIFEST_SEQUENCE_FIELD,
    canonical_manifest_payload,
)

PRIVATE_FILE_NAME = "release-signing.ed25519"
PUBLIC_PEM_NAME = "release-signing.pub.pem"
PUBLIC_B64_NAME = "release-signing.pub.b64"

#: 与项目其它本机绑定密钥一样，用独立 purpose 做 DPAPI entropy：
#: 同一台机器上的其它密钥解不开这把私钥。
_KEY_PURPOSE = "liuhao-upgrade-release-signing-v1"

SIGNATURE_ALGORITHM = "ed25519"


class SigningError(RuntimeError):
    """可预期的操作失败：打印中文说明，不甩 traceback。"""


def default_key_dir() -> Path:
    """默认私钥目录：用户本机数据目录，永远不在仓库内。"""
    local_app_data = (os.environ.get("LOCALAPPDATA") or "").strip()
    if os.name == "nt" and local_app_data:
        return Path(local_app_data) / "LiuHao" / "upgrade-signing"
    return Path.home() / ".config" / "liuhao" / "upgrade-signing"


def _ensure_outside_repo(key_dir: Path) -> None:
    try:
        key_dir.resolve().relative_to(_REPO_ROOT)
    except ValueError:
        return
    raise SigningError(
        "拒绝把签名私钥写进仓库目录：请使用默认目录，或把 --key-dir 指向仓库之外。"
    )


def _load_private_key(key_path: Path) -> ed25519.Ed25519PrivateKey:
    if not key_path.is_file():
        raise SigningError(
            f"找不到私钥文件：{key_path}。先运行 keygen，或确认 --key-file 指向正确位置。"
        )
    payload = key_path.read_bytes()
    if not is_machine_bound_payload(payload):
        raise SigningError(
            "私钥文件缺少本机绑定版本头（LIUHAO-DPAPI-1/LIUHAO-RAW-1），"
            "拒绝把它当成可信私钥使用。请用本工具重新生成。"
        )
    try:
        plain, mode = unwrap_machine_bound(payload, purpose=_KEY_PURPOSE)
    except MachineBoundKeyError as exc:
        code = getattr(exc, "code", "")
        if code == "cross-machine":
            raise SigningError(
                "这把私钥绑定在另一台机器/另一个 Windows 用户上，当前环境解不开。"
                "请在原机器导出公钥；确需换机请重新生成密钥并走公钥轮换流程。"
            ) from exc
        if code == "dpapi-unavailable":
            raise SigningError(
                "当前环境没有可用的 Windows DPAPI，无法解开本机绑定的私钥。"
                "请在原来的 Windows 用户会话中运行。"
            ) from exc
        raise SigningError(f"私钥文件无法解开（{code or 'unknown'}），请从备份恢复。") from exc
    try:
        key = serialization.load_pem_private_key(plain, password=None)
    except Exception as exc:  # noqa: BLE001 - 统一转成可读错误
        raise SigningError("私钥内容不是合法的 Ed25519 PKCS#8 PEM。") from exc
    if not isinstance(key, ed25519.Ed25519PrivateKey):
        raise SigningError("私钥不是 Ed25519 密钥。")
    del plain, mode, payload
    return key


def _public_pem(key: ed25519.Ed25519PrivateKey) -> str:
    return key.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode("ascii")


def _public_b64(key: ed25519.Ed25519PrivateKey) -> str:
    raw = key.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )
    return base64.b64encode(raw).decode("ascii")


def public_key_fingerprint(key: ed25519.Ed25519PrivateKey) -> str:
    """公钥指纹（SHA256 hex）。轮换时用它核对"客户端内置的到底是哪把公钥"。"""
    raw = key.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )
    return hashlib.sha256(raw).hexdigest()


def generate_keypair(key_dir: Path, *, force: bool = False) -> dict:
    """生成密钥对；返回路径与指纹（绝不包含私钥内容）。"""
    key_dir = Path(key_dir)
    _ensure_outside_repo(key_dir)
    private_path = key_dir / PRIVATE_FILE_NAME
    if private_path.exists() and not force:
        raise SigningError(
            f"私钥已存在：{private_path}。覆盖会使旧发布包无法再用旧钥匙签名；"
            "确需重新生成请加 --force。"
        )
    key_dir.mkdir(parents=True, exist_ok=True)
    private = ed25519.Ed25519PrivateKey.generate()
    pem = private.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    atomic_write_bytes(private_path, wrap_machine_bound(pem, purpose=_KEY_PURPOSE))
    atomic_write_text(key_dir / PUBLIC_PEM_NAME, _public_pem(private) + "\n")
    atomic_write_text(key_dir / PUBLIC_B64_NAME, _public_b64(private) + "\n")
    return {
        "private_key": str(private_path),
        "public_pem": str(key_dir / PUBLIC_PEM_NAME),
        "public_b64": str(key_dir / PUBLIC_B64_NAME),
        "fingerprint": public_key_fingerprint(private),
    }


def export_public_key(key_path: Path, *, fmt: str = "pem") -> str:
    key = _load_private_key(Path(key_path))
    value = _public_pem(key) if fmt == "pem" else _public_b64(key)
    return value if value.endswith("\n") else value + "\n"


def _artifact_path(
    entry: dict, manifest_path: Path, artifact_dir: Optional[Path]
) -> Path:
    raw = str(entry.get("artifact") or "").strip()
    if not raw:
        raise SigningError(f"发布项 {entry.get('version')!r} 缺少 artifact 字段。")
    candidate = Path(raw)
    if candidate.is_absolute():
        return candidate
    base = Path(artifact_dir) if artifact_dir else manifest_path.parent
    return base / candidate


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sign_manifest(
    manifest_path: Path,
    key_path: Path,
    *,
    artifact_dir: Optional[Path] = None,
    versions: Optional[Iterable[str]] = None,
    sequence: Optional[int] = None,
) -> list[dict]:
    """给清单里的发布项写签名并原子落盘；返回每个发布项的摘要。

    除每个发布项的 ``signature`` 外，还给**清单自身**写
    ``manifest_signature``（覆盖整份清单）与 ``sequence``（单调递增的防重放
    序号）。客户端只需固定公钥，不必在每次发布后重新下发清单哈希。
    """
    manifest_path = Path(manifest_path)
    if not manifest_path.is_file():
        raise SigningError(f"找不到发布清单：{manifest_path}")
    text = manifest_path.read_text(encoding="utf-8")
    try:
        parse_manifest(text)  # 只做结构/字段校验（sha256 必须 64 位 hex 等）
        data = json.loads(text)
    except Exception as exc:  # noqa: BLE001 - 统一转成可读错误
        raise SigningError(f"发布清单不合法：{exc}") from exc

    releases = data.get("releases")
    if not isinstance(releases, list) or not releases:
        raise SigningError("发布清单里没有 releases。")
    wanted = {str(v) for v in versions} if versions else None

    key = _load_private_key(Path(key_path))
    signed: list[dict] = []
    for entry in releases:
        if not isinstance(entry, dict):
            raise SigningError("releases 里的每一项都必须是对象。")
        version = str(entry.get("version") or "").strip()
        if wanted is not None and version not in wanted:
            continue
        artifact = _artifact_path(entry, manifest_path, artifact_dir)
        if not artifact.is_file():
            raise SigningError(
                f"发布包不存在：{artifact}。签名必须针对真实文件；"
                "请先构建，或用 --artifact-dir 指到发布目录。"
            )
        actual_sha = _sha256_file(artifact)
        declared_sha = str(entry.get("sha256") or "").strip().lower()
        if actual_sha != declared_sha:
            raise SigningError(
                f"发布包 {artifact.name} 的 SHA256 与清单不一致，拒绝签名：\n"
                f"  清单：{declared_sha}\n  实际：{actual_sha}\n"
                "先修正清单（或重新构建），再签名；绝不能把另一份文件签成这份清单。"
            )
        actual_size = artifact.stat().st_size
        declared_size = entry.get("size")
        if declared_size is not None and int(declared_size) != actual_size:
            raise SigningError(
                f"发布包 {artifact.name} 的 size 与清单不一致（清单 {declared_size}，"
                f"实际 {actual_size}），拒绝签名。"
            )
        signature = base64.b64encode(key.sign(artifact.read_bytes())).decode("ascii")
        entry["signature"] = signature
        entry["signature_algorithm"] = SIGNATURE_ALGORITHM
        signed.append(
            {
                "version": version,
                "artifact": artifact.name,
                "sha256": actual_sha,
                "size": actual_size,
            }
        )

    if wanted is not None and not signed:
        raise SigningError(
            "指定的 --version 在清单里都不存在：" + ", ".join(sorted(wanted))
        )

    # 清单级签名：sequence 只能递增，攻击者没有私钥无法伪造更大的序号，
    # 客户端据此拒绝“旧清单重放”。
    existing = data.get(MANIFEST_SEQUENCE_FIELD)
    if sequence is None:
        sequence = (
            existing + 1
            if isinstance(existing, int) and not isinstance(existing, bool) and existing >= 1
            else 1
        )
    if isinstance(sequence, bool) or not isinstance(sequence, int) or sequence < 1:
        raise SigningError("sequence 必须是 >= 1 的整数（用于防重放，只能递增）")
    data[MANIFEST_SEQUENCE_FIELD] = sequence
    data.setdefault(
        "generated_at", datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    )
    # 签名覆盖“除签名字段外的整份清单”；先移除旧签名再规范化，避免旧值干扰。
    data.pop(MANIFEST_SIGNATURE_FIELD, None)
    data.pop(MANIFEST_SIGNATURE_ALGORITHM_FIELD, None)
    data[MANIFEST_SIGNATURE_ALGORITHM_FIELD] = SIGNATURE_ALGORITHM
    data[MANIFEST_SIGNATURE_FIELD] = base64.b64encode(
        key.sign(canonical_manifest_payload(data))
    ).decode("ascii")

    atomic_write_text(
        manifest_path, json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    )
    return signed


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="sign_release_manifest.py",
        description="贾维斯发布清单签名工具（Ed25519；私钥本机绑定，不进仓库）。",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_keygen = sub.add_parser("keygen", help="生成 Ed25519 密钥对")
    p_keygen.add_argument("--key-dir", type=Path, default=None)
    p_keygen.add_argument("--force", action="store_true", help="覆盖已存在的私钥（不可恢复）")

    p_sign = sub.add_parser("sign", help="给发布清单写 signature 与 manifest_signature")
    p_sign.add_argument("--manifest", type=Path, required=True)
    p_sign.add_argument("--key-file", type=Path, default=None)
    p_sign.add_argument(
        "--artifact-dir",
        type=Path,
        default=None,
        help="发布包所在目录（默认与清单同目录）",
    )
    p_sign.add_argument(
        "--sequence",
        type=int,
        default=None,
        help="清单防重放序号（默认在已有值上加 1；首次为 1）",
    )
    p_sign.add_argument(
        "--version",
        action="append",
        default=None,
        help="只签指定版本（可重复；默认全部）",
    )

    p_export = sub.add_parser("export-public-key", help="导出公钥")
    p_export.add_argument("--key-file", type=Path, default=None)
    p_export.add_argument("--format", choices=("pem", "base64"), default="pem")
    p_export.add_argument("--out", type=Path, default=None)
    return parser


def _resolve_key_file(explicit: Optional[Path], key_dir: Optional[Path] = None) -> Path:
    if explicit is not None:
        return Path(explicit)
    return Path(key_dir or default_key_dir()) / PRIVATE_FILE_NAME


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "keygen":
            info = generate_keypair(
                Path(args.key_dir) if args.key_dir else default_key_dir(),
                force=args.force,
            )
            print("已生成 Ed25519 密钥对（私钥已本机绑定，不在仓库内）：")
            print(f"  私钥：{info['private_key']}")
            print(f"  公钥：{info['public_pem']}")
            print(f"  公钥(base64)：{info['public_b64']}")
            print(f"  公钥指纹(SHA256)：{info['fingerprint']}")
            print("下一步：把公钥指纹记入轮换台账；只分发公钥，永远不要拷走私钥。")
            return 0

        if args.command == "sign":
            key_path = _resolve_key_file(args.key_file)
            signed = sign_manifest(
                Path(args.manifest),
                key_path,
                artifact_dir=Path(args.artifact_dir) if args.artifact_dir else None,
                versions=args.version,
                sequence=args.sequence,
            )
            key = _load_private_key(key_path)
            print(f"已签名 {len(signed)} 个发布项：")
            for item in signed:
                print(
                    f"  - {item['version']}  {item['artifact']}  "
                    f"sha256={item['sha256']}  size={item['size']}"
                )
            print(f"  签名算法：{SIGNATURE_ALGORITHM}")
            print("  清单签名：已写入 manifest_signature（sequence 单调递增，防重放）")
            print(f"  公钥指纹(SHA256)：{public_key_fingerprint(key)}")
            print(f"  清单已更新：{Path(args.manifest)}")
            return 0

        if args.command == "export-public-key":
            key_path = _resolve_key_file(args.key_file)
            text = export_public_key(key_path, fmt=args.format)
            if args.out:
                atomic_write_text(Path(args.out), text)
                print(f"公钥已写入：{args.out}")
            else:
                sys.stdout.write(text)
            key = _load_private_key(key_path)
            print(
                f"公钥指纹(SHA256)：{public_key_fingerprint(key)}",
                file=sys.stderr,
            )
            return 0
    except SigningError as exc:
        print(f"失败：{exc}", file=sys.stderr)
        return 1
    parser.error("未知子命令")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
