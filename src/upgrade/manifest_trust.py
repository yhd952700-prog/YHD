"""Signed release manifest: the self-bootstrapping trust anchor.

为什么需要这一层
----------------
远端升级曾要求客户端预先配置 ``manifest_sha256``（固定清单哈希）。问题在于
**这个哈希每次发布都会变**：如果它必须由用户/安装器下发，那就等于每次升级
前都要人工改一次配置——自动升级永远走不通。

现在把信任锚从"每次发布都变的清单哈希"换成"长期不变（或可轮换）的 Ed25519
公钥"：

* 清单自身带 ``manifest_signature``（签名覆盖除签名字段外的整份清单）；
* 客户端只需配置公钥（``LIUHAO_UPGRADE_PUBLIC_KEY``），首次之后即可自动检查；
* ``manifest_sha256`` 退化为**可选的首引导固定值**：配置了就额外校验，不配置
  则完全依赖清单签名。它不再需要每次发布重新下发。

防重放 / 防降级
---------------
签名只证明"这份清单是持钥者发的"，不能阻止把**旧**的合法清单重放回来。因此
签名清单必须带单调递增的 ``sequence``，客户端在 ``upgrade-state`` 里持久化
"已接受的最大 sequence / 清单摘要 / 最高版本"，规则：

* ``sequence`` 变小 → 拒绝（旧清单重放）；
* ``sequence`` 相同但清单内容摘要不同 → 拒绝（同序号分叉/重放）；
* 最高版本低于已记录的可信版本 → 拒绝（降级清单）；
* 下载/安装阶段再用同一份状态拒绝旧版本发布包（防重放旧包）。

``sequence`` 由签发工具在每次签清单时递增；它受签名保护，攻击者没有私钥无法
伪造更大的序号。
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Mapping, Optional

from src.common.atomic_write import atomic_write_text

from .downloader import _decode_public_key
from .errors import ManifestError, SignatureError

MANIFEST_SIGNATURE_FIELD = "manifest_signature"
MANIFEST_SIGNATURE_ALGORITHM_FIELD = "manifest_signature_algorithm"
MANIFEST_SEQUENCE_FIELD = "sequence"

TRUST_STATE_SCHEMA = "liuhao.upgrade-manifest-trust/1"
TRUST_STATE_FILENAME = "manifest-trust.json"

_SIGNATURE_FIELDS = (MANIFEST_SIGNATURE_FIELD, MANIFEST_SIGNATURE_ALGORITHM_FIELD)


def canonical_manifest_payload(manifest: Mapping[str, Any]) -> bytes:
    """签名/验签共用的规范化字节。

    签名对象是"清单的语义内容"，不是磁盘上的排版：键排序、无空白、UTF-8。
    以 ``_`` 开头的内部字段（如 ``_parsed_releases``）与两个签名字段不参与，
    其余字段（含 ``sequence`` 与每个 release 的 ``signature``）全部覆盖。
    """
    body = {
        key: value
        for key, value in manifest.items()
        if not str(key).startswith("_") and key not in _SIGNATURE_FIELDS
    }
    return json.dumps(
        body, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def canonical_manifest_sha256(manifest: Mapping[str, Any]) -> str:
    return hashlib.sha256(canonical_manifest_payload(manifest)).hexdigest()


def manifest_sequence(manifest: Mapping[str, Any]) -> Optional[int]:
    """``sequence``：签名清单的单调序号；未提供返回 ``None``。"""
    value = manifest.get(MANIFEST_SEQUENCE_FIELD)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ManifestError(
            "发布清单 sequence 必须是 >= 1 的整数（用于防重放，序号只能递增）"
        )
    return value


def verify_manifest_signature(
    manifest: Mapping[str, Any],
    public_key: Optional[str | bytes],
    *,
    require: bool,
) -> bool:
    """验证清单级签名。

    ``require=True``（未配置固定清单哈希的远端源）时，缺签名或缺公钥都是硬
    失败——这正是"签名是唯一信任依据"的含义。``require=False`` 时保留旧的
    "固定哈希"引导模式：没有签名就返回 ``False``，由调用方决定是否放行。
    """
    signature = manifest.get(MANIFEST_SIGNATURE_FIELD)
    if not signature:
        if require:
            raise SignatureError(
                "远端发布清单必须携带清单签名（manifest_signature）："
                "未配置固定清单哈希时，签名是唯一的信任依据。"
            )
        return False
    if not isinstance(signature, str):
        raise SignatureError("发布清单 manifest_signature 必须是字符串")
    algorithm = str(
        manifest.get(MANIFEST_SIGNATURE_ALGORITHM_FIELD) or "ed25519"
    ).strip().lower()
    if algorithm != "ed25519":
        raise SignatureError(
            f"不支持的清单签名算法：{algorithm!r}（当前仅支持 ed25519）"
        )
    if not public_key:
        if require:
            raise SignatureError(
                "发布清单带签名，但没有配置可信 Ed25519 公钥"
                "（LIUHAO_UPGRADE_PUBLIC_KEY），无法验证来源。"
            )
        return False
    key = _decode_public_key(public_key)
    try:
        raw = base64.b64decode(signature.encode("utf-8"), validate=True)
    except (ValueError, binascii.Error) as exc:
        raise SignatureError(f"发布清单 manifest_signature 不是合法 base64：{exc}") from exc
    from cryptography.exceptions import InvalidSignature

    try:
        key.verify(raw, canonical_manifest_payload(manifest))
    except InvalidSignature as exc:
        raise SignatureError(
            "发布清单签名验证失败：清单内容与签名不匹配，可能被篡改。"
        ) from exc
    return True


@dataclass(frozen=True)
class ManifestTrustState:
    """已接受过的签名清单状态（防重放/防降级的水位线）。"""

    sequence: int
    version: str
    manifest_sha256: str = ""
    updated_at: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema": TRUST_STATE_SCHEMA,
            "sequence": self.sequence,
            "version": self.version,
            "manifest_sha256": self.manifest_sha256,
            "updated_at": self.updated_at,
        }


class ManifestTrustStore:
    """``upgrade-state/manifest-trust.json`` 的读写。

    文件损坏或不可写时**不静默放行**：防重放状态一旦丢失，旧清单就能重放。
    缺失（首次运行）才是正常的引导状态。
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def load(self) -> Optional[ManifestTrustState]:
        if not self.path.is_file():
            return None
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ManifestError(
                f"清单可信状态文件损坏（{self.path}）：{exc}。"
                "请确认升级状态目录未被篡改；必要时删除该文件以重新引导。"
            ) from exc
        if not isinstance(data, dict) or data.get("schema") != TRUST_STATE_SCHEMA:
            raise ManifestError(
                f"清单可信状态文件格式不认识（{self.path}），拒绝继续以防重放。"
            )
        sequence = data.get("sequence")
        if isinstance(sequence, bool) or not isinstance(sequence, int) or sequence < 1:
            raise ManifestError(f"清单可信状态文件里的 sequence 无效（{self.path}）")
        version = str(data.get("version") or "").strip()
        if not version:
            raise ManifestError(f"清单可信状态文件缺少 version（{self.path}）")
        return ManifestTrustState(
            sequence=sequence,
            version=version,
            manifest_sha256=str(data.get("manifest_sha256") or ""),
            updated_at=str(data.get("updated_at") or ""),
        )

    def save(self, state: ManifestTrustState) -> None:
        atomic_write_text(
            self.path,
            json.dumps(state.to_dict(), ensure_ascii=False, indent=2) + "\n",
        )
