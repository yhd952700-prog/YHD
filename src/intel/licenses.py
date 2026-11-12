"""许可证判定：以 LICENSE 原文为准，不靠仓库标签猜。

三分类（使用落点）：

* ``runtime`` —— 必须宽松；本文件黑名单（GPL / AGPL / SSPL / BUSL / CC-BY-NC）
  一律拒绝。
* ``build``  —— 可放宽；copyleft 可用于独立构建工具，但必须注明约束。
* ``tool``   —— 独立进程调用；LGPL 类可接受，但必须注明动态链接/修改义务。

任何 ``unknown`` 一律拒绝进入「建议采用」列表。
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

from src.intel.models import LicenseCategory, LicenseVerdict

#: runtime 落点下的硬黑名单（ID 或家族）。
BLACKLIST_RUNTIME = frozenset(
    {
        "GPL-2.0",
        "GPL-3.0",
        "AGPL-3.0",
        "SSPL-1.0",
        "BUSL-1.1",
        "CC-BY-NC-4.0",
    }
)

#: 不属于 OSI 批准的开源许可证（source-available / 非商业限制）。
NON_OSI_IDS = frozenset(
    {
        "SSPL-1.0",
        "BUSL-1.1",
        "CC-BY-NC-4.0",
        "Elastic-2.0",
        "Commons-Clause",
    }
)


@dataclass(frozen=True)
class LicenseAssessment:
    """一段 LICENSE 原文的判定结果。"""

    license_id: str
    verdict: LicenseVerdict
    raw_sha256: str
    matched_evidence: str


@dataclass(frozen=True)
class LicenseDecision:
    """结合使用落点后的采用结论。"""

    allowed: bool
    category: LicenseCategory
    reason: str
    requires_note: bool
    license_id: str
    verdict: LicenseVerdict


def _normalise(text: str) -> str:
    return " ".join((text or "").upper().split())


def _detect(text: str) -> tuple[str, LicenseVerdict, str]:
    """返回 ``(license_id, verdict, 命中的证据短语)``。

    检测顺序刻意「限制性优先」：双许可证或拼接文本时，先命中更严格的许可证，
    宁可误拒，不可误放。
    """
    up = _normalise(text)
    if not up:
        return "unknown", LicenseVerdict.UNKNOWN, ""

    non_osi_checks: list[tuple[str, str]] = [
        ("SSPL-1.0", "SERVER SIDE PUBLIC LICENSE"),
        ("BUSL-1.1", "BUSINESS SOURCE LICENSE"),
        ("CC-BY-NC-4.0", "NONCOMMERCIAL"),
        ("Elastic-2.0", "ELASTIC LICENSE"),
        ("Commons-Clause", "COMMONS CLAUSE"),
    ]
    for license_id, phrase in non_osi_checks:
        if phrase in up:
            return license_id, LicenseVerdict.NON_OSI, phrase

    if "AFFERO GENERAL PUBLIC LICENSE" in up or re.search(r"\bAGPL", up):
        return "AGPL-3.0", LicenseVerdict.COPYLEFT, "AGPL"
    if "LESSER GENERAL PUBLIC LICENSE" in up or re.search(r"\bLGPL", up):
        license_id = "LGPL-3.0" if "VERSION 3" in up else "LGPL-2.1"
        return license_id, LicenseVerdict.COPYLEFT, "LGPL"
    if "GNU GENERAL PUBLIC LICENSE" in up or re.search(r"\bGPL", up):
        license_id = "GPL-2.0" if "VERSION 2" in up else "GPL-3.0"
        return license_id, LicenseVerdict.COPYLEFT, "GPL"
    if "MOZILLA PUBLIC LICENSE" in up:
        license_id = "MPL-2.0" if "VERSION 2" in up else "MPL-1.1"
        return license_id, LicenseVerdict.COPYLEFT, "MOZILLA PUBLIC LICENSE"

    permissive_checks: list[tuple[str, str]] = [
        ("Apache-2.0", "APACHE LICENSE"),
        ("MIT", "MIT LICENSE"),
        ("BSD-3-Clause", "REDISTRIBUTION AND USE IN SOURCE AND BINARY FORMS"),
        ("ISC", "PERMISSION TO USE, COPY, MODIFY, AND/OR DISTRIBUTE"),
        ("PSF-2.0", "PYTHON SOFTWARE FOUNDATION LICENSE"),
        ("Unlicense", "FREE AND UNENCUMBERED SOFTWARE"),
        ("Zlib", "ZLIB LICENSE"),
    ]
    for license_id, phrase in permissive_checks:
        if phrase in up:
            return license_id, LicenseVerdict.PERMISSIVE, phrase

    # 缩写兜底：短文本或只有 SPDX 标识时也能识别。
    if re.search(r"\bSSPL", up):
        return "SSPL-1.0", LicenseVerdict.NON_OSI, "SSPL"
    if re.search(r"\bBUSL", up):
        return "BUSL-1.1", LicenseVerdict.NON_OSI, "BUSL"
    if "CC-BY-NC" in up or "CC BY-NC" in up:
        return "CC-BY-NC-4.0", LicenseVerdict.NON_OSI, "CC-BY-NC"
    if "PERMISSION IS HEREBY GRANTED, FREE OF CHARGE" in up and "THE SOFTWARE IS PROVIDED" in up:
        return "MIT", LicenseVerdict.PERMISSIVE, "MIT permission grant"

    return "unknown", LicenseVerdict.UNKNOWN, ""


def assess_license_text(text: str) -> LicenseAssessment:
    """判定 LICENSE 原文，并返回原文 SHA-256 作为可追溯证据。"""
    raw = text or ""
    license_id, verdict, evidence = _detect(raw)
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    return LicenseAssessment(
        license_id=license_id,
        verdict=verdict,
        raw_sha256=digest,
        matched_evidence=evidence,
    )


def decide_for_category(
    assessment: LicenseAssessment, category: LicenseCategory | str
) -> LicenseDecision:
    """把许可证判定落到具体使用场景，返回是否可进入「建议采用」。"""
    cat = LicenseCategory(category)

    if assessment.verdict is LicenseVerdict.UNKNOWN:
        return LicenseDecision(
            allowed=False,
            category=cat,
            reason="许可证未识别（unknown），拒绝进入建议采用列表",
            requires_note=False,
            license_id=assessment.license_id,
            verdict=assessment.verdict,
        )

    if cat is LicenseCategory.RUNTIME:
        if (
            assessment.license_id in BLACKLIST_RUNTIME
            or assessment.verdict is not LicenseVerdict.PERMISSIVE
        ):
            return LicenseDecision(
                allowed=False,
                category=cat,
                reason=(
                    f"runtime 只接受宽松许可证；{assessment.license_id} 判定为 "
                    f"{assessment.verdict.value}"
                ),
                requires_note=False,
                license_id=assessment.license_id,
                verdict=assessment.verdict,
            )
        return LicenseDecision(
            allowed=True,
            category=cat,
            reason=f"{assessment.license_id} 为宽松许可证，可用于 runtime",
            requires_note=False,
            license_id=assessment.license_id,
            verdict=assessment.verdict,
        )

    if assessment.verdict is LicenseVerdict.NON_OSI or assessment.license_id in NON_OSI_IDS:
        return LicenseDecision(
            allowed=False,
            category=cat,
            reason=(
                f"{assessment.license_id} 属非 OSI 许可证（SSPL/BUSL/CC-BY-NC 类），"
                "拒绝进入建议采用列表"
            ),
            requires_note=False,
            license_id=assessment.license_id,
            verdict=assessment.verdict,
        )

    if assessment.verdict is LicenseVerdict.COPYLEFT:
        if cat is LicenseCategory.TOOL and assessment.license_id.startswith("LGPL"):
            note = "LGPL 类许可证在独立进程调用下可接受，需注明动态链接与修改义务"
        else:
            note = "copyleft 许可证仅可作为独立构建/工具进程使用，需注明约束"
        return LicenseDecision(
            allowed=True,
            category=cat,
            reason=note,
            requires_note=True,
            license_id=assessment.license_id,
            verdict=assessment.verdict,
        )

    return LicenseDecision(
        allowed=True,
        category=cat,
        reason=f"{assessment.license_id} 为宽松许可证，可在 {cat.value} 落点使用",
        requires_note=False,
        license_id=assessment.license_id,
        verdict=assessment.verdict,
    )


def unknown_assessment(reason: str) -> LicenseAssessment:
    """未获取 LICENSE 原文时的显式 unknown 判定（不伪造许可证标签）。"""
    return LicenseAssessment(
        license_id="unknown",
        verdict=LicenseVerdict.UNKNOWN,
        raw_sha256="",
        matched_evidence=reason,
    )
