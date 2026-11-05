"""M4 回归：PII 检测必须真正改写输出，中文 PII 必须覆盖，脱敏失败必须 fail-closed。"""

from __future__ import annotations

from src.knowledge import pii
from src.knowledge.pii import KnowledgeSecurityPolicy, PIIType
from src.knowledge.security import KnowledgeSecurityPolicy as GatePolicy


class TestRedactionActuallyRewritesText:
    def test_email_phone_id_and_card_are_removed(self):
        text = (
            "联系邮箱 zhang.san@example.com，手机 13812345678，"
            "身份证 110101199003074512，银行卡 4111 1111 1111 1111。"
        )
        redacted = pii.redact_text(text)

        for secret in (
            "zhang.san@example.com",
            "13812345678",
            "110101199003074512",
            "4111 1111 1111 1111",
        ):
            assert secret not in redacted
        assert "[REDACTED]" in redacted
        assert "联系邮箱" in redacted  # 非 PII 文本保留

    def test_detect_returns_spans_and_list_matches(self):
        text = "手机号 13900001111"
        result = KnowledgeSecurityPolicy().detect_pii(text)
        assert PIIType.PHONE in result.pii_types
        phone = result.matches[PIIType.PHONE]
        assert isinstance(phone.matches, list)
        assert phone.matches == ["13900001111"]
        assert phone.positions == [(4, 15)]

    def test_address_is_redacted(self):
        redacted = pii.redact_text("我住在 123 Main Street, NY 10001。")
        assert "123 Main Street" not in redacted
        assert "[REDACTED]" in redacted


class TestNameDetectionIsConservative:
    def test_labelled_chinese_name_is_redacted(self):
        redacted = pii.redact_text("联系人：张三，电话 13800000000")
        assert "张三" not in redacted
        assert "联系人" in redacted

    def test_ordinary_capitalised_words_are_not_treated_as_names(self):
        text = "Python Is A Programming Language. Monday Is A Day Of The Week."
        result = KnowledgeSecurityPolicy().detect_pii(text)
        assert PIIType.NAME not in result.pii_types
        assert result.redacted_text == text

    def test_honorific_english_name_is_redacted(self):
        redacted = pii.redact_text("Please call Mr. John Smith at +1 415 555 0100.")
        assert "John Smith" not in redacted
        assert "[REDACTED]" in redacted


class TestRedactionFailureIsFailClosed:
    def test_filter_content_never_returns_raw_text_when_redaction_raises(
        self, monkeypatch
    ):
        def boom(text):
            raise RuntimeError("pii engine crashed")

        monkeypatch.setattr(pii, "redact_text", boom)
        policy = GatePolicy(redact_pii=True)
        raw = "机密原文：身份证 110101199003074512"

        out = policy.filter_content(raw)

        assert out != raw
        assert "110101199003074512" not in out
        assert "PII 脱敏失败" in out

    def test_policy_can_still_be_disabled_explicitly(self):
        policy = GatePolicy(redact_pii=False)
        assert policy.filter_content("raw text") == "raw text"
