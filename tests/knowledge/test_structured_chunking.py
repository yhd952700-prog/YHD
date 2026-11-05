"""A 回归：结构感知分块（标题路径、段落、表格/代码完整性、超长硬切）。"""

from __future__ import annotations

from typing import List

import pytest

from src.knowledge.chunker import chunk_document_structured
from src.knowledge.documents import KnowledgeDocumentService


class _FakeEmbedder:
    name = "test-embedder"
    model = "test-model"

    def embed(self, texts: List[str]) -> List[List[float]]:
        # 用长度与首字符哈希构造确定性向量，保证可复现
        return [
            [1.0, float(len(text) % 97), float(sum(map(ord, text[:8])) % 997)]
            for text in texts
        ]


def _chunks(text: str, size: int = 512, overlap: int = 50):
    return chunk_document_structured(text, chunk_size=size, chunk_overlap=overlap)


class TestHeadingPath:
    def test_markdown_heading_stack_becomes_breadcrumb(self):
        text = "\n".join(
            [
                "# 安装指南",
                "总体介绍。",
                "",
                "## 部署",
                "如下所示，需要先安装依赖。",
                "",
                "## 回滚",
                "按相反顺序执行。",
            ]
        )
        chunks = _chunks(text, size=40)
        deploy = [c for c in chunks if "如下所示" in c.text]
        assert deploy, "部署小节必须产生 chunk"
        assert deploy[0].heading_path == ("安装指南", "部署")
        assert deploy[0].context_header == "安装指南 / 部署"
        assert deploy[0].embed_text.startswith("[安装指南 / 部署]")

        rollback = [c for c in chunks if "按相反顺序" in c.text]
        assert rollback[0].heading_path == ("安装指南", "回滚")

    def test_chinese_chapter_and_item_headings(self):
        text = "\n".join(
            [
                "第一章 总则",
                "本章说明适用范围。",
                "第一节 适用对象",
                "一、内部员工",
                "适用于所有正式员工。",
                "二、外部顾问",
                "按合同约定执行。",
            ]
        )
        chunks = _chunks(text, size=200)
        employee = [c for c in chunks if "正式员工" in c.text][0]
        assert employee.heading_path == ("第一章 总则", "第一节 适用对象", "一、内部员工")

        consultant = [c for c in chunks if "合同约定" in c.text][0]
        assert consultant.heading_path == (
            "第一章 总则",
            "第一节 适用对象",
            "二、外部顾问",
        )

    def test_same_level_heading_pops_sibling(self):
        text = "\n".join(
            ["# A", "a 内容", "## B", "b 内容", "## C", "c 内容"]
        )
        chunks = _chunks(text, size=200)
        c_chunk = [c for c in chunks if "c 内容" in c.text][0]
        assert c_chunk.heading_path == ("A", "C"), "同级标题必须弹栈而不是层层嵌套"


class TestStructurePreservation:
    def test_paragraph_boundaries_are_kept(self):
        first = "第一段内容" * 5
        second = "第二段内容" * 5
        chunks = _chunks(f"{first}\n\n{second}", size=40)
        assert len(chunks) == 2
        assert chunks[0].text == first
        assert chunks[1].text == second

    def test_table_is_never_split_even_when_oversized(self):
        rows = "\n".join(f"| 列A | 列B | 值{i} |" for i in range(20))
        chunks = _chunks(f"# 配置表\n\n{rows}", size=40)
        table_chunks = [c for c in chunks if c.text.startswith("|")]
        assert len(table_chunks) == 1, "表格必须整体保留，不得按 chunk_size 拆开"
        assert table_chunks[0].text == rows
        assert len(table_chunks[0].text) > 40

    def test_code_block_is_never_split(self):
        code = "\n".join(
            ["```python", "def deploy():", "    for step in steps:", "        run(step)", "```"]
        )
        chunks = _chunks(f"# 脚本\n\n{code}", size=40)
        code_chunks = [c for c in chunks if c.text.startswith("```")]
        assert len(code_chunks) == 1
        assert code_chunks[0].text == code

    def test_overlong_paragraph_degrades_to_overlapping_hard_cut(self):
        paragraph = "".join(f"{i:03d}" for i in range(60))  # 180 chars, 无空格
        chunks = _chunks(f"# 长段落\n\n{paragraph}", size=60, overlap=10)
        assert len(chunks) >= 3
        for chunk in chunks:
            assert len(chunk.text) <= 60
            assert chunk.heading_path == ("长段落",)
        # 相邻块存在 overlap（上一块尾部 10 字符 == 下一块开头）
        for previous, current in zip(chunks, chunks[1:]):
            assert previous.text[-10:] == current.text[:10]

    def test_blank_input_returns_no_chunks(self):
        assert _chunks("") == []
        assert _chunks("   \n\n  ") == []


class TestIngestUsesStructuredChunks:
    def test_ingest_stores_heading_metadata_and_hit_carries_header(self):
        service = KnowledgeDocumentService(embedder=_FakeEmbedder())
        body = "\n".join(
            [
                "# 运维手册",
                "## 备份",
                "如下所示，备份每天凌晨执行。",
            ]
        )
        result = service.ingest(body, doc_id="ops")
        assert result["chunks"] >= 1

        hits = service.search("备份", top_k=10)["hits"]
        hit = [item for item in hits if "备份每天凌晨" in item["text"]][0]
        assert hit["metadata"]["heading_path"] == ["运维手册", "备份"]
        assert hit["metadata"]["context_header"] == "运维手册 / 备份"
        assert hit["text"].startswith("[运维手册 / 备份]\n")

    def test_legacy_rows_without_heading_metadata_still_readable(self):
        service = KnowledgeDocumentService(embedder=_FakeEmbedder())
        # 模拟批次 1 之前写入的旧行：只有 text，没有 heading_path/context_header
        service._store().insert("legacy#0", [1.0, 0.0, 0.0], {"text": "旧数据内容"})

        hits = service.search("旧数据", top_k=5)["hits"]
        legacy = [item for item in hits if item["metadata"].get("document_id") is None]
        assert legacy and legacy[0]["text"] == "旧数据内容"
        assert "heading_path" not in legacy[0]["metadata"]


@pytest.mark.parametrize("size", [16, 32, 64])
def test_every_body_chunk_respects_size(size):
    text = "# 标题\n\n" + "\n\n".join("段落" * 6 for _ in range(10))
    chunks = _chunks(text, size=size)
    assert chunks
    assert all(len(chunk.text) <= size for chunk in chunks)
