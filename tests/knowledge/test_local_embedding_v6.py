"""第二季 V6：本地 ONNX 语义向量引擎（src/knowledge/local_embedding.py）。

分工：
* 环境无关的逻辑测试（清单/就位检查/不可用原因/下载器校验/校准档/
  状态面/聊天导入工具）在任何环境都跑；
* 真引擎测试（真模型语义间隔、服务级入库检索）在引擎组件或模型文件
  缺失时 skip —— 它们的实测证据由 V6 演练脚本
  （upgrade2-evidence/v6_drill.py）以真网关形态另行留档。
"""

from __future__ import annotations

import hashlib
import io
import os
from pathlib import Path

import pytest

from src.knowledge import local_embedding
from src.knowledge.abstention import resolve_abstention_config
from src.knowledge.embedding import (
    EmbeddingUnavailableError,
    LocalHashingEmbedder,
    build_embedding_provider,
    build_embedding_provider_with_local_fallback,
)

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
    "LIUHAO_KB_ABSTAIN_CALIBRATED_FOR",
)


@pytest.fixture
def clean_env(monkeypatch):
    for name in _PROVIDER_ENVS:
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


@pytest.fixture
def tmp_data_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("LIUHAO_DATA_DIR", str(tmp_path))
    return tmp_path


def _sparse_file(path: Path, size: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as fh:
        fh.truncate(size)


# --------------------------------------------------------------------------- #
# 清单与就位检查
# --------------------------------------------------------------------------- #


def test_manifest_is_pinned_and_consistent():
    assert local_embedding.MODEL_NAME == "bge-small-zh-v1.5"
    assert local_embedding.MODEL_TOTAL_BYTES == 95_332_206
    assert len(local_embedding.MODEL_FILES) == 7
    for _name, size, sha in local_embedding.MODEL_FILES:
        assert size > 0
        assert len(sha) == 64
    assert local_embedding.LocalOnnxEmbedder.dimensions == 512


def test_model_present_requires_exact_sizes(tmp_data_dir):
    root = local_embedding.local_embedding_model_dir()
    assert local_embedding.local_model_present() is False
    for name, size, _sha in local_embedding.MODEL_FILES:
        _sparse_file(root / name, size)
    assert local_embedding.local_model_present() is True
    # 任一文件大小不对都算没下好。
    _sparse_file(root / "vocab.txt", 10)
    assert local_embedding.local_model_present() is False


# --------------------------------------------------------------------------- #
# 不可用原因（失败要人话）
# --------------------------------------------------------------------------- #


def test_unavailable_reason_names_missing_engine(monkeypatch, tmp_data_dir):
    monkeypatch.setattr(local_embedding, "local_engine_available", lambda: False)
    reason = local_embedding.local_embedding_unavailable_reason()
    assert reason is not None and "未安装" in reason
    with pytest.raises(EmbeddingUnavailableError) as info:
        local_embedding.build_local_embedder()
    assert "未安装" in str(info.value)


def test_unavailable_reason_names_missing_model(monkeypatch, tmp_data_dir):
    monkeypatch.setattr(local_embedding, "local_engine_available", lambda: True)
    reason = local_embedding.local_embedding_unavailable_reason()
    assert reason is not None and "尚未下载" in reason
    with pytest.raises(EmbeddingUnavailableError) as info:
        local_embedding.build_local_embedder()
    assert "尚未下载" in str(info.value)


def test_provider_local_choice_fails_honestly(clean_env, tmp_data_dir):
    clean_env.setenv("LIUHAO_EMBEDDING_PROVIDER", "local")
    with pytest.raises(EmbeddingUnavailableError) as info:
        build_embedding_provider()
    # 引擎缺失或模型缺失都行，但必须是本地引擎的人话原因，不是假向量。
    assert "本地向量" in str(info.value)


def test_provider_local_choice_rejects_unknown_model(clean_env, tmp_data_dir):
    clean_env.setenv("LIUHAO_EMBEDDING_PROVIDER", "local")
    clean_env.setenv("LIUHAO_EMBEDDING_MODEL", "some-other-model")
    with pytest.raises(EmbeddingUnavailableError) as info:
        build_embedding_provider()
    assert "只支持" in str(info.value)


# --------------------------------------------------------------------------- #
# 下载器：校验通过才落位，坏文件绝不冒充下好
# --------------------------------------------------------------------------- #


class _FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False


@pytest.fixture
def fake_manifest(monkeypatch, tmp_data_dir):
    """把钉住的清单换成小夹具（内容自洽的假文件），下载走真代码路径。"""
    fixtures = {}
    manifest = []
    for name, _size, _sha in local_embedding.MODEL_FILES:
        payload = f"fixture-bytes-for-{name}".encode("utf-8") * 4
        fixtures[name] = payload
        manifest.append((name, len(payload), hashlib.sha256(payload).hexdigest()))
    monkeypatch.setattr(local_embedding, "MODEL_FILES", tuple(manifest))
    monkeypatch.setattr(
        local_embedding, "MODEL_TOTAL_BYTES", sum(len(p) for p in fixtures.values())
    )
    state = local_embedding._LOCAL_DOWNLOAD
    with state.lock:
        state.running = False
        state.downloaded = 0
        state.total = local_embedding.MODEL_TOTAL_BYTES
        state.error = ""
        state.done = False
        state.file = ""
    return fixtures


def _serve(monkeypatch, fixtures, corrupt: str = ""):
    def fake_urlopen(request, timeout=None):
        name = request.full_url.rsplit("/", 1)[-1]
        payload = fixtures[name]
        if name == corrupt:
            payload = payload + b"tampered"
        return _FakeResponse(payload)

    monkeypatch.setattr(local_embedding.urllib.request, "urlopen", fake_urlopen)


def test_downloader_lands_verified_files(monkeypatch, fake_manifest):
    _serve(monkeypatch, fake_manifest)
    local_embedding._download_local_model()
    status = local_embedding.local_model_download_status()
    assert status["done"] is True
    assert status["error"] == ""
    assert local_embedding.local_model_present() is True


def test_downloader_rejects_corrupted_file(monkeypatch, fake_manifest):
    _serve(monkeypatch, fake_manifest, corrupt="tokenizer.json")
    local_embedding._download_local_model()
    status = local_embedding.local_model_download_status()
    assert status["done"] is False
    assert "校验没通过" in status["error"]
    assert local_embedding.local_model_present() is False
    root = local_embedding.local_embedding_model_dir()
    assert not (root / "tokenizer.json").exists()
    assert not (root / "tokenizer.json.downloading").exists()


# --------------------------------------------------------------------------- #
# 回落链顺序：真 provider → 本地 ONNX → 哈希词法
# --------------------------------------------------------------------------- #


def test_fallback_chain_uses_hashing_when_local_unavailable(clean_env, tmp_data_dir):
    clean_env.setenv("LIUHAO_EMBEDDING_PROVIDER", "openai")  # 无 key，必失败
    embedder = build_embedding_provider_with_local_fallback()
    assert isinstance(embedder, LocalHashingEmbedder)
    assert embedder.semantic is False


def test_fallback_chain_can_be_disabled(clean_env, tmp_data_dir):
    clean_env.setenv("LIUHAO_EMBEDDING_PROVIDER", "openai")
    clean_env.setenv("LIUHAO_KNOWLEDGE_LOCAL_FALLBACK", "0")
    with pytest.raises(EmbeddingUnavailableError):
        build_embedding_provider_with_local_fallback()


# --------------------------------------------------------------------------- #
# 校准档与状态面
# --------------------------------------------------------------------------- #


def test_abstention_profile_registered_for_local_model(clean_env):
    config, meta = resolve_abstention_config("local", "bge-small-zh-v1.5")
    assert meta["calibrated"] is True
    assert config.min_top_similarity == 0.45
    assert config.min_top_gap == 0.05
    assert config.strong_match_similarity == 0.85


class _FakeLocalEmbedder:
    name = "local"
    model = "bge-small-zh-v1.5"
    semantic = True
    local_model = True
    local_fallback = False

    def embed(self, texts):
        return [[1.0] + [0.0] * 511 for _ in texts]


def test_status_reports_local_model_mode():
    from src.knowledge.documents import KnowledgeDocumentService

    service = KnowledgeDocumentService(embedder=_FakeLocalEmbedder())
    status = service.status()
    assert status["enabled"] is True
    assert status["embedding_mode"] == "local_model"
    assert status["semantic"] is True
    assert "本地语义模型" in status["message"]


# --------------------------------------------------------------------------- #
# 池化数学（CLS + L2 归一化）
# --------------------------------------------------------------------------- #


def test_cls_pool_normalize_takes_first_token_and_normalizes():
    np = pytest.importorskip("numpy")
    hidden = np.zeros((2, 3, 4), dtype=np.float64)
    hidden[0, 0] = [3.0, 4.0, 0.0, 0.0]  # CLS 行：范数 5
    hidden[0, 1] = [100.0, 0.0, 0.0, 0.0]  # 非 CLS 不得影响结果
    hidden[1, 0] = [0.0, 0.0, 0.0, 2.0]
    vectors = local_embedding._cls_pool_normalize(hidden, 2)
    assert vectors[0] == pytest.approx([0.6, 0.8, 0.0, 0.0])
    assert vectors[1] == pytest.approx([0.0, 0.0, 0.0, 1.0])
    assert np.linalg.norm(vectors[0]) == pytest.approx(1.0)


# --------------------------------------------------------------------------- #
# 聊天导入工具
# --------------------------------------------------------------------------- #


def test_chat_ingest_tool_reports_unavailable(clean_env, tmp_data_dir, monkeypatch):
    from src.ai.tools.builtin import KnowledgeIngestTool
    from src.knowledge import tool as tool_module

    class _UnavailableService:
        def ingest(self, *args, **kwargs):
            raise EmbeddingUnavailableError("知识库未启用：测试用不可用原因")

    monkeypatch.setattr(
        tool_module, "get_knowledge_service", lambda: _UnavailableService()
    )
    result = KnowledgeIngestTool().execute(text="要存的内容", title="标题")
    assert result.ok is False
    assert "未启用" in (result.error or "")


def test_chat_ingest_tool_stores_via_service(monkeypatch):
    from src.ai.tools.builtin import KnowledgeIngestTool
    from src.knowledge import tool as tool_module

    captured = {}

    class _OkService:
        def ingest(self, text, doc_id=None, metadata=None):
            captured["text"] = text
            captured["doc_id"] = doc_id
            return {
                "document_id": "doc-1",
                "chunks": 3,
                "vectors": 3,
                "deduplicated": False,
            }

    monkeypatch.setattr(tool_module, "get_knowledge_service", lambda: _OkService())
    result = KnowledgeIngestTool().execute(text="要存的内容", title="我的标题")
    assert result.ok is True
    assert result.output["document_id"] == "doc-1"
    assert result.output["chunks"] == 3
    assert captured["doc_id"] == "我的标题"


def test_plugin_bridge_exposes_knowledge_ingest():
    from src.ai.tools import plugin_tools

    names = {entry["name"] for entry in plugin_tools.PLUGIN_TOOL_CATALOG}
    assert "knowledge_ingest" in names
    built = {tool.name for tool in plugin_tools.build_plugin_tools()}
    assert "knowledge_ingest" in built


# --------------------------------------------------------------------------- #
# 真引擎（引擎/模型缺失即 skip；实测证据另见 V6 演练）
# --------------------------------------------------------------------------- #

_MODEL_SRC = Path(
    os.environ.get("JARVIS_LOCAL_EMBEDDING_MODEL_DIR")
    or "/tmp/v6-models/bge-small-zh-v1.5"
)


def _real_model_dir(tmp_path: Path) -> Path:
    """把真模型以符号链接放进隔离数据目录（不复制 95MB）。"""
    if not local_embedding.local_engine_available():
        pytest.skip("onnxruntime/tokenizers 未安装")
    if not (_MODEL_SRC / "model_optimized.onnx").is_file():
        pytest.skip(f"真模型不在 {_MODEL_SRC}（演练环境未落位）")
    link = tmp_path / "knowledge" / "embedding" / "bge-small-zh-v1.5"
    link.parent.mkdir(parents=True, exist_ok=True)
    link.symlink_to(_MODEL_SRC, target_is_directory=True)
    return link


def test_real_engine_semantic_gap(tmp_path, monkeypatch):
    monkeypatch.setenv("LIUHAO_DATA_DIR", str(tmp_path))
    _real_model_dir(tmp_path)
    embedder = local_embedding.build_local_embedder()
    texts = [
        "员工出差住宿每晚报销上限四百元",
        "出差时住酒店一天最多能报 400 元",
        "仓库每月最后一个工作日盘点库存",
    ]
    vectors = embedder.embed(texts)
    assert len(vectors) == 3 and len(vectors[0]) == 512

    def cos(a, b):
        return sum(x * y for x, y in zip(a, b))

    assert cos(vectors[0], vectors[1]) > cos(vectors[0], vectors[2]) + 0.2


def test_real_engine_service_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setenv("LIUHAO_DATA_DIR", str(tmp_path))
    _real_model_dir(tmp_path)
    from src.knowledge.documents import KnowledgeDocumentService
    from src.knowledge.vector_store import PersistentVectorStore

    store = PersistentVectorStore(db_path=str(tmp_path / "kb.sqlite3"))
    service = KnowledgeDocumentService(
        vector_store=store, embedder=local_embedding.build_local_embedder()
    )
    travel = service.ingest(
        "公司差旅规定：出差乘坐高铁二等座，住宿每晚不超过四百元。",
        doc_id="差旅制度",
    )
    service.ingest(
        "仓库盘点流程：每月最后一个工作日全面盘点，差异超百分之二须说明。",
        doc_id="盘点流程",
    )
    found = service.search("出差住宾馆能报多少钱", top_k=2)
    assert found["hits"]
    # 改写查询（与原文零整词重叠）必须把差旅文档排第一。
    assert found["hits"][0]["metadata"]["document_id"] == travel["document_id"]
    missed = service.search("火星基地氧气价格", top_k=2)
    assert missed["retrieval"]["status"] == "no_relevant"
