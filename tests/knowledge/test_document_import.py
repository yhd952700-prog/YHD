"""File import: PDF / DOCX / HTML extraction, limits and page provenance.

The PDF fixtures are **real** PDFs built with pypdf's writer: a text page has a
real content stream + Type1 font, a scanned page is a page without any text
operator, an encrypted PDF uses pypdf's own RC4 encryption. That keeps the
failure modes honest instead of asserting on hand-written byte soup.
"""

from __future__ import annotations

import io
from typing import List, Tuple

import pytest
from fastapi.testclient import TestClient

from src.knowledge.chunker import chunk_paged_document
from src.knowledge.documents import KnowledgeDocumentService
from src.knowledge.extractors import (
    DEFAULT_LIMITS,
    CorruptedDocument,
    EncryptedPdfError,
    EmptyDocument,
    ExtractionLimits,
    FileTooLarge,
    SCANNED_PDF_MESSAGE,
    ScannedPdfError,
    TooManyPages,
    UnsupportedFileType,
    extract_document,
)


# ---------------------------------------------------------------------------
# Fixture builders
# ---------------------------------------------------------------------------


def pdf_with_text(pages: List[str]) -> bytes:
    """A valid PDF whose every page carries a real text layer."""
    from pypdf import PdfWriter
    from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

    writer = PdfWriter()
    for text in pages:
        page = writer.add_blank_page(width=612, height=792)
        font = DictionaryObject(
            {
                NameObject("/Type"): NameObject("/Font"),
                NameObject("/Subtype"): NameObject("/Type1"),
                NameObject("/BaseFont"): NameObject("/Helvetica"),
            }
        )
        font_ref = writer._add_object(font)
        page[NameObject("/Resources")] = DictionaryObject(
            {NameObject("/Font"): DictionaryObject({NameObject("/F1"): font_ref})}
        )
        content = DecodedStreamObject()
        content.set_data(
            ("BT /F1 24 Tf 72 720 Td (%s) Tj ET" % text).encode("latin-1")
        )
        page[NameObject("/Contents")] = writer._add_object(content)
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def blank_pdf(page_count: int = 1) -> bytes:
    """A PDF with pages but **no text layer** -- the scanned-document shape."""
    from pypdf import PdfWriter

    writer = PdfWriter()
    for _ in range(page_count):
        writer.add_blank_page(width=612, height=792)
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def encrypted_pdf(password: str = "secret") -> bytes:
    from pypdf import PdfWriter

    writer = PdfWriter()
    writer.add_blank_page(width=612, height=792)
    writer.encrypt(password)
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def docx_bytes() -> bytes:
    from docx import Document

    document = Document()
    document.add_heading("安装指南", level=1)
    document.add_paragraph("执行 pip install 完成安装。")
    document.add_heading("配置", level=2)
    document.add_paragraph("设置访问令牌。")
    table = document.add_table(rows=2, cols=2)
    table.cell(0, 0).text = "键"
    table.cell(0, 1).text = "值"
    table.cell(1, 0).text = "token"
    table.cell(1, 1).text = "abc"
    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


_HTML_BYTES = (
    "<html><head><title>运维手册</title><script>var leak = 'SECRET';</script></head>"
    "<body><h1>概览</h1><p>欢迎 &amp; 使用。</p>"
    "<h2>步骤</h2><ul><li>第一步</li><li>第二步</li></ul></body></html>"
).encode("utf-8")


# ---------------------------------------------------------------------------
# Extraction unit tests
# ---------------------------------------------------------------------------


class TestPdfExtraction:
    def test_text_layer_is_extracted_per_page(self):
        document = extract_document(
            pdf_with_text(["Page one ALPHA", "Page two BETA"]), "manual.pdf"
        )
        assert document.file_type == "pdf"
        assert document.page_count == 2
        assert [page for page, _ in document.pages] == [1, 2]
        assert "ALPHA" in document.pages[0][1]
        assert "BETA" in document.pages[1][1]
        assert "ALPHA" in document.text and "BETA" in document.text

    def test_scanned_pdf_is_an_error_never_an_empty_success(self):
        with pytest.raises(ScannedPdfError) as raised:
            extract_document(blank_pdf(2), "scan.pdf")
        assert str(raised.value) == SCANNED_PDF_MESSAGE
        assert raised.value.http_status == 422

    def test_encrypted_pdf_reports_an_explicit_error(self):
        with pytest.raises(EncryptedPdfError) as raised:
            extract_document(encrypted_pdf(), "locked.pdf")
        assert "已加密" in str(raised.value)
        assert raised.value.http_status == 422

    def test_corrupt_pdf_reports_an_explicit_error(self):
        with pytest.raises(CorruptedDocument) as raised:
            extract_document(b"%PDF-1.4 truncated garbage", "broken.pdf")
        assert "损坏" in str(raised.value)
        assert raised.value.http_status == 422

    def test_page_limit_is_enforced_with_a_readable_reason(self):
        limits = ExtractionLimits(max_pdf_pages=1)
        with pytest.raises(TooManyPages) as raised:
            extract_document(blank_pdf(2), "too-long.pdf", limits=limits)
        assert "2 页" in str(raised.value) and "最多 1 页" in str(raised.value)
        assert raised.value.http_status == 413

    def test_file_size_limit_is_enforced_before_parsing(self):
        limits = ExtractionLimits(max_file_bytes=100)
        with pytest.raises(FileTooLarge) as raised:
            extract_document(pdf_with_text(["x"]), "big.pdf", limits=limits)
        assert "文件过大" in str(raised.value)
        assert raised.value.http_status == 413

    def test_extracted_length_limit_is_enforced(self):
        limits = ExtractionLimits(max_extracted_chars=10)
        with pytest.raises(FileTooLarge) as raised:
            extract_document(b"0123456789ABCDEF", "long.txt", limits=limits)
        assert "正文过长" in str(raised.value)


class TestDocxExtraction:
    def test_heading_hierarchy_survives_into_heading_path(self):
        document = extract_document(docx_bytes(), "manual.docx")
        assert document.file_type == "docx"
        assert document.text.startswith("# 安装指南")
        assert "## 配置" in document.text
        assert "| 键 | 值 |" in document.text

        from src.knowledge.chunker import chunk_document_structured

        chunks = chunk_document_structured(document.text)
        paths = [chunk.heading_path for chunk in chunks]
        assert ("安装指南",) in paths
        assert ("安装指南", "配置") in paths

    def test_corrupt_docx_reports_an_explicit_error(self):
        with pytest.raises(CorruptedDocument) as raised:
            extract_document(b"not a zip at all", "broken.docx")
        assert "DOCX" in str(raised.value)

    def test_empty_docx_is_rejected(self):
        from docx import Document

        buffer = io.BytesIO()
        Document().save(buffer)
        with pytest.raises(EmptyDocument):
            extract_document(buffer.getvalue(), "empty.docx")


class TestHtmlExtraction:
    def test_headings_are_preserved_and_scripts_are_dropped(self):
        document = extract_document(_HTML_BYTES, "guide.html")
        assert document.file_type == "html"
        assert document.title == "运维手册"
        assert "# 概览" in document.text
        assert "## 步骤" in document.text
        assert "欢迎 & 使用" in document.text
        assert "SECRET" not in document.text

    def test_empty_html_is_rejected(self):
        with pytest.raises(EmptyDocument):
            extract_document(b"<html><body></body></html>", "empty.html")


class TestPlainTextAndSuffix:
    def test_txt_and_md_still_work(self):
        for filename in ("a.txt", "b.md"):
            document = extract_document(b"# heading\n\nbody", filename)
            assert document.text == "# heading\n\nbody"

    def test_unsupported_suffix_is_refused(self):
        with pytest.raises(UnsupportedFileType) as raised:
            extract_document(b"PK\x03\x04", "sheet.xlsx")
        assert ".xlsx" in str(raised.value)
        assert raised.value.http_status == 415


class TestPagedChunking:
    def test_every_chunk_keeps_its_page_number(self):
        pages: List[Tuple[int, str]] = [
            (1, "# 第一章\n\n" + "甲" * 600),
            (2, "# 第二章\n\n" + "乙" * 600),
        ]
        chunks = chunk_paged_document(pages)
        assert {chunk.page for chunk in chunks} == {1, 2}
        assert any(chunk.page == 1 and "甲" in chunk.text for chunk in chunks)
        assert any(chunk.page == 2 and "乙" in chunk.text for chunk in chunks)
        assert [chunk.index for chunk in chunks] == list(range(len(chunks)))


class TestServicePageMetadata:
    class _Embedder:
        name = "fake"

        def embed(self, texts):
            return [[1.0, float(len(text) % 31)] for text in texts]

    def _service(self) -> KnowledgeDocumentService:
        return KnowledgeDocumentService(embedder=self._Embedder())

    def test_chunk_metadata_and_hits_expose_page(self):
        service = self._service()
        text = "Alpha handbook content.\n\nBeta handbook content."
        service.ingest(
            text,
            doc_id="pdf-doc",
            metadata={"source": "manual.pdf", "file_type": "pdf", "page_count": 2},
            pages=[(1, "Alpha handbook content."), (2, "Beta handbook content.")],
        )
        chunks = service.document_chunks("pdf-doc")["chunks"]
        assert {chunk["page"] for chunk in chunks} == {1, 2}
        assert {chunk["page_label"] for chunk in chunks} == {"第 1 页", "第 2 页"}

        result = service.search("Alpha handbook", top_k=5)
        assert result["hits"], result["retrieval"]
        hit = result["hits"][0]
        assert hit["page"] == 1
        assert hit["location"] == "第 1 页"
        assert hit["metadata"]["page_label"] == "第 1 页"
        assert hit["text"].startswith("[第 1 页]")

    def test_same_pdf_under_a_different_filename_is_deduplicated(self):
        service = self._service()
        text = extract_document(
            pdf_with_text(["Identical content DEDUPE"]), "manual.pdf"
        ).text
        first = service.ingest(text, doc_id="manual.pdf")
        second = service.ingest(text, doc_id="manual-copy.pdf")
        assert first["deduplicated"] is False
        assert second["deduplicated"] is True
        assert second["matched_document_id"] == first["document_id"]
        assert service.list_documents()["total"] == 1
        assert service.document_chunks("manual-copy.pdf")["count"] == 0


# ---------------------------------------------------------------------------
# HTTP upload surface
# ---------------------------------------------------------------------------

_PROVIDER_ENVS = (
    "LIUHAO_EMBEDDING_PROVIDER",
    "LIUHAO_EMBEDDING_MODEL",
    "LIUHAO_EMBEDDING_API_KEY",
    "LIUHAO_KNOWLEDGE_ALLOW_MOCK",
    "OPENAI_API_KEY",
    "OPENAI_BASE_URL",
    "OLLAMA_BASE_URL",
    "LIUHAO_OLLAMA_BASE_URL",
    "LIUHAO_ENV",
    "APP_ENV",
)


@pytest.fixture
def client(monkeypatch, tmp_path):
    for name in _PROVIDER_ENVS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("LIUHAO_ENV", "development")
    monkeypatch.setenv("LIUHAO_KNOWLEDGE_ALLOW_MOCK", "1")
    monkeypatch.setenv("LIUHAO_EMBEDDING_PROVIDER", "mock")
    monkeypatch.setenv("LIUHAO_HUMAN_IDENTITIES_BACKEND", "file")
    monkeypatch.setenv("LIUHAO_HUMAN_IDENTITIES_FILE", str(tmp_path / "humans.json"))
    import src.kernels.identity as identity_module

    monkeypatch.setattr(identity_module, "_global_manager", None)
    with TestClient(__import__("src.gateway.main", fromlist=["get_app"]).get_app()) as c:
        yield c


def _headers(scope: str = "L0"):
    from src.security import get_jwt_handler

    token, _ = get_jwt_handler().create_token(
        subject="kb-human", metadata={"kind": "human", "scope": scope}
    )
    return {"Authorization": f"Bearer {token}"}


class TestUploadHttp:
    def test_scanned_pdf_returns_the_exact_ocr_message(self, client):
        response = client.post(
            "/v1/knowledge/documents/upload",
            files={"file": ("scan.pdf", blank_pdf(2), "application/pdf")},
            headers=_headers(),
        )
        assert response.status_code == 422, response.text
        assert response.json()["detail"] == SCANNED_PDF_MESSAGE

    def test_encrypted_pdf_returns_a_clear_reason(self, client):
        response = client.post(
            "/v1/knowledge/documents/upload",
            files={"file": ("locked.pdf", encrypted_pdf(), "application/pdf")},
            headers=_headers(),
        )
        assert response.status_code == 422
        assert "已加密" in response.json()["detail"]

    def test_corrupt_pdf_is_not_reported_as_unsupported(self, client):
        response = client.post(
            "/v1/knowledge/documents/upload",
            files={"file": ("broken.pdf", b"%PDF-1.4 junk", "application/pdf")},
            headers=_headers(),
        )
        assert response.status_code == 422
        assert "损坏" in response.json()["detail"]

    def test_pdf_upload_is_searchable_with_page_provenance(self, client):
        headers = _headers()
        uploaded = client.post(
            "/v1/knowledge/documents/upload",
            files={
                "file": (
                    "manual.pdf",
                    pdf_with_text(
                        ["LAN Alpha installation", "LAN Beta backup"]
                    ),
                    "application/pdf",
                )
            },
            headers=headers,
        )
        assert uploaded.status_code == 200, uploaded.text
        body = uploaded.json()
        assert body["extraction"]["file_type"] == "pdf"
        assert body["extraction"]["page_count"] == 2
        assert body["extraction"]["paged"] is True

        chunks = client.get(
            f"/v1/knowledge/documents/{body['document_id']}/chunks",
            headers=headers,
        )
        assert chunks.status_code == 200
        assert {chunk["page"] for chunk in chunks.json()["chunks"]} == {1, 2}

        searched = client.post(
            "/v1/knowledge/search",
            json={"query": "Alpha", "top_k": 5},
            headers=headers,
        )
        assert searched.status_code == 200, searched.text
        result = searched.json()
        assert result["hits"], result["retrieval"]
        hit = result["hits"][0]
        assert hit["page"] == 1
        assert hit["location"] == "第 1 页"
        assert hit["text"].startswith("[第 1 页]")

    def test_docx_and_html_uploads_are_accepted(self, client):
        headers = _headers()
        docx_response = client.post(
            "/v1/knowledge/documents/upload",
            files={
                "file": (
                    "manual.docx",
                    docx_bytes(),
                    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                )
            },
            headers=headers,
        )
        assert docx_response.status_code == 200, docx_response.text
        assert docx_response.json()["extraction"]["file_type"] == "docx"

        html_response = client.post(
            "/v1/knowledge/documents/upload",
            files={"file": ("guide.html", _HTML_BYTES, "text/html")},
            headers=headers,
        )
        assert html_response.status_code == 200, html_response.text
        assert html_response.json()["extraction"]["file_type"] == "html"

        docx_chunks = client.get(
            f"/v1/knowledge/documents/{docx_response.json()['document_id']}/chunks",
            headers=headers,
        ).json()["chunks"]
        assert any(
            chunk["heading_path"] == ["安装指南", "配置"] for chunk in docx_chunks
        )

    def test_same_pdf_different_filename_is_deduplicated(self, client):
        headers = _headers()
        data = pdf_with_text(["Identical content DEDUPE"])
        first = client.post(
            "/v1/knowledge/documents/upload",
            files={"file": ("manual.pdf", data, "application/pdf")},
            headers=headers,
        )
        second = client.post(
            "/v1/knowledge/documents/upload",
            files={"file": ("manual-copy.pdf", data, "application/pdf")},
            headers=headers,
        )
        assert first.status_code == second.status_code == 200
        assert first.json()["deduplicated"] is False
        assert second.json()["deduplicated"] is True
        listed = client.get("/v1/knowledge/documents", headers=headers).json()
        assert listed["total"] == 1

    def test_upload_size_cap_is_reported_without_reading_to_ooms(
        self, client, monkeypatch
    ):
        import src.api.routes.knowledge as route_module

        monkeypatch.setattr(route_module, "_MAX_UPLOAD_BYTES", 8)
        response = client.post(
            "/v1/knowledge/documents/upload",
            files={"file": ("big.txt", b"x" * 64, "text/plain")},
            headers=_headers(),
        )
        assert response.status_code == 413
        assert "文件过大" in response.json()["detail"]

    def test_pdf_page_cap_is_reported_with_page_numbers(
        self, client, monkeypatch
    ):
        import src.knowledge.extractors as extractor_module

        monkeypatch.setattr(
            extractor_module,
            "DEFAULT_LIMITS",
            ExtractionLimits(max_pdf_pages=1),
        )
        response = client.post(
            "/v1/knowledge/documents/upload",
            files={"file": ("long.pdf", blank_pdf(2), "application/pdf")},
            headers=_headers(),
        )
        assert response.status_code == 413
        detail = response.json()["detail"]
        assert "2 页" in detail and "最多 1 页" in detail

    def test_unsupported_file_type_is_still_refused(self, client):
        response = client.post(
            "/v1/knowledge/documents/upload",
            files={"file": ("sheet.xlsx", b"PK\x03\x04", "application/octet-stream")},
            headers=_headers(),
        )
        assert response.status_code == 415
        assert ".xlsx" in response.json()["detail"]


def test_default_limits_are_explicit():
    assert DEFAULT_LIMITS.max_file_bytes == 20 * 1024 * 1024
    assert DEFAULT_LIMITS.max_pdf_pages == 300
    assert DEFAULT_LIMITS.max_uncompressed_bytes == 150 * 1024 * 1024
    assert DEFAULT_LIMITS.max_extracted_chars == 2_000_000
