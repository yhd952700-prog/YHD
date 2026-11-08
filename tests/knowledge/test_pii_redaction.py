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

    def test_labelled_english_name_is_redacted(self):
        redacted = pii.redact_text("Name: Alice Johnson; role: engineer")
        assert "Alice Johnson" not in redacted
        assert "role" in redacted


class _ConstantEmbedder:
    name = "constant"
    model = "constant"

    def embed(self, texts):
        return [[1.0, 0.0] for _ in texts]


class TestKnowledgeOutputIsRedacted:
    """检索结果 / 分块 / 文档列表是最后的展示面，密钥、路径、邮箱必须脱敏。"""

    def _service(self, tmp_path):
        from src.knowledge.documents import KnowledgeDocumentService
        from src.knowledge.vector_store import PersistentVectorStore

        return KnowledgeDocumentService(
            vector_store=PersistentVectorStore(str(tmp_path / "vectors.sqlite3")),
            embedder=_ConstantEmbedder(),
        )

    def test_search_chunks_and_list_redact_secrets_paths_and_emails(self, tmp_path):
        service = self._service(tmp_path)
        body = (
            "# 配置说明\n\n"
            "API Key: sk-live-ABCDEFGHIJKLMNOP\n"
            "配置文件 C:\\Users\\alice\\secret\\conf.ini\n"
            "联系邮箱 fred@example.com，手机 13812345678。\n"
        )
        service.ingest(
            body,
            doc_id="leaky",
            metadata={
                "filename": "C:\\Users\\alice\\secret\\manual.md",
                "title": "内部手册",
            },
        )

        hits = service.search("配置", top_k=5)["hits"]
        assert hits, "正常内容必须仍能被检索到"
        text = hits[0]["text"]
        assert "sk-live-ABCDEFGHIJKLMNOP" not in text
        assert "alice@example.com" not in text
        assert "fred@example.com" not in text
        assert "13812345678" not in text
        assert "C:\\Users\\alice" not in text
        # 文件名保留，引用仍能指向正确文件
        assert "conf.ini" in text
        assert "manual.md" in str(hits[0]["metadata"])

        chunks = service.document_chunks("leaky")["chunks"]
        assert chunks and all("sk-live" not in c["text"] for c in chunks)
        assert all("fred@example.com" not in c["text"] for c in chunks)

        listed = service.list_documents()["documents"]
        assert listed
        assert "C:\\Users\\alice" not in str(listed[0]["source"])
        assert "manual.md" in str(listed[0]["source"])


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
