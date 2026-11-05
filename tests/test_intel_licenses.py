"""许可证判定单元测试：三分类、黑名单、unknown 拒绝。"""

from __future__ import annotations

import pytest

from src.intel.licenses import (
    BLACKLIST_RUNTIME,
    assess_license_text,
    decide_for_category,
)
from src.intel.models import LicenseCategory, LicenseVerdict

MIT_TEXT = (
    "MIT License\n\n"
    "Permission is hereby granted, free of charge, to any person obtaining a copy "
    "of this software and associated documentation files (the \"Software\"), to deal "
    "in the Software without restriction.\n\n"
    "THE SOFTWARE IS PROVIDED \"AS IS\", WITHOUT WARRANTY OF ANY KIND."
)

APACHE_TEXT = "Apache License\nVersion 2.0, January 2004\nhttp://www.apache.org/licenses/"

BSD_TEXT = (
    "Redistribution and use in source and binary forms, with or without modification, "
    "are permitted provided that the following conditions are met."
)

GPL_TEXT = "GNU GENERAL PUBLIC LICENSE\nVersion 3, 29 June 2007"

AGPL_TEXT = "GNU AFFERO GENERAL PUBLIC LICENSE\nVersion 3, 19 November 2007"

LGPL_TEXT = "GNU LESSER GENERAL PUBLIC LICENSE\nVersion 3, 29 June 2007"

SSPL_TEXT = "Server Side Public License\nVersion 1, October 2018"

BUSL_TEXT = "Business Source License 1.1"

CC_NC_TEXT = "Creative Commons Attribution-NonCommercial 4.0 International Public License"


@pytest.mark.parametrize(
    ("text", "license_id", "verdict"),
    [
        (MIT_TEXT, "MIT", LicenseVerdict.PERMISSIVE),
        (APACHE_TEXT, "Apache-2.0", LicenseVerdict.PERMISSIVE),
        (BSD_TEXT, "BSD-3-Clause", LicenseVerdict.PERMISSIVE),
        (GPL_TEXT, "GPL-3.0", LicenseVerdict.COPYLEFT),
        (AGPL_TEXT, "AGPL-3.0", LicenseVerdict.COPYLEFT),
        (LGPL_TEXT, "LGPL-3.0", LicenseVerdict.COPYLEFT),
        (SSPL_TEXT, "SSPL-1.0", LicenseVerdict.NON_OSI),
        (BUSL_TEXT, "BUSL-1.1", LicenseVerdict.NON_OSI),
        (CC_NC_TEXT, "CC-BY-NC-4.0", LicenseVerdict.NON_OSI),
        ("All rights reserved. This is proprietary software.", "unknown", LicenseVerdict.UNKNOWN),
    ],
)
def test_license_text_classification(text, license_id, verdict):
    assessment = assess_license_text(text)
    assert assessment.license_id == license_id
    assert assessment.verdict is verdict
    assert len(assessment.raw_sha256) == 64


def test_mit_is_allowed_for_runtime():
    decision = decide_for_category(assess_license_text(MIT_TEXT), LicenseCategory.RUNTIME)
    assert decision.allowed is True
    assert decision.requires_note is False


@pytest.mark.parametrize("text", [GPL_TEXT, AGPL_TEXT, SSPL_TEXT, BUSL_TEXT, CC_NC_TEXT])
def test_runtime_rejects_blacklist(text):
    decision = decide_for_category(assess_license_text(text), LicenseCategory.RUNTIME)
    assert decision.allowed is False
    assert decision.license_id in BLACKLIST_RUNTIME or decision.verdict is LicenseVerdict.NON_OSI


def test_unknown_license_is_rejected_everywhere():
    text = "All rights reserved. This is proprietary software."
    for category in LicenseCategory:
        decision = decide_for_category(assess_license_text(text), category)
        assert decision.allowed is False
        assert "unknown" in decision.reason


def test_lgpl_is_allowed_for_tool_but_rejected_for_runtime():
    assessment = assess_license_text(LGPL_TEXT)
    tool_decision = decide_for_category(assessment, LicenseCategory.TOOL)
    assert tool_decision.allowed is True
    assert tool_decision.requires_note is True

    runtime_decision = decide_for_category(assessment, LicenseCategory.RUNTIME)
    assert runtime_decision.allowed is False


def test_copyleft_is_relaxed_for_build_with_a_note():
    decision = decide_for_category(assess_license_text(GPL_TEXT), LicenseCategory.BUILD)
    assert decision.allowed is True
    assert decision.requires_note is True


def test_empty_license_text_is_unknown():
    assessment = assess_license_text("")
    assert assessment.license_id == "unknown"
    assert assessment.verdict is LicenseVerdict.UNKNOWN
