"""本地 ONNX 语义向量引擎（知识库 V6 真链路）。

为什么需要这一层：此前知识库的「本地」路径只有两种——外部 Ollama 守护
进程（要另装、另拉模型）和 :class:`LocalHashingEmbedder`（词法哈希，
``semantic=False``，同义词改写就检索不到）。纯安装、无 Key、无 Ollama
的机器因此永远拿不到**真语义**检索。本模块把一个小语义模型直接跑在
进程内：

* 引擎：``onnxruntime``（MIT）+ ``tokenizers``（Apache-2.0），均为可选
  组件（pyproject 的 ``knowledge`` extra），导入探测、缺失时诚实降级，
  口径与语音引擎（faster-whisper / kokoro-onnx）一致；
* 模型：``BAAI/bge-small-zh-v1.5`` 的量化 ONNX（MIT，512 维，约 95 MB），
  中英混合语料，主场中文。模型文件**不进 git**，落在数据目录
  ``<data_dir>/knowledge/embedding/bge-small-zh-v1.5/``，由本模块的下载器
  按产品既有口径落位（后台线程 + 逐文件大小/SHA-256 校验 + 状态查询，
  与 ``src/voice/wake.py`` 的 KWS 下载器同款）；
* 下载源：Hugging Face 仓库 ``Qdrant/bge-small-zh-v1.5``（fastembed 官方
  ONNX 导出），已在 ``src/security/egress_registry.py`` 登记
  ``huggingface.co``。下载只发 GET、不带任何用户数据；
* 向量口径：BGE 官方口径——[CLS] 池化 + L2 归一化。分数是真语义余弦，
  ``semantic=True``；但阈值是模型属性，接入时已按
  ``python -m src.knowledge.evaluation --provider local`` 的实测把校准
  档登记进 ``abstention.CALIBRATED_PROFILES``，换模型必须重跑评测。

诚实边界：引擎未装 / 模型未下载时，本模块只抛
:class:`EmbeddingUnavailableError` 并把「缺什么、怎么补」说清，绝不
回落到假向量；回落顺序（真 provider → 本地 ONNX → 哈希词法）由
``embedding.build_embedding_provider_with_local_fallback`` 统一编排。
"""

from __future__ import annotations

import hashlib
import logging
import threading
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from ..common.engines import data_dir
from .embedding import EmbeddingUnavailableError

logger = logging.getLogger("liuhao.knowledge.local_embedding")

#: 对外身份（abstention 校准与索引台账都按 ``name:model`` 认它）。
PROVIDER_NAME = "local"
MODEL_NAME = "bge-small-zh-v1.5"
MODEL_DIMENSIONS = 512
MODEL_REPO = "Qdrant/bge-small-zh-v1.5"
MODEL_SOURCE_URL = f"https://huggingface.co/{MODEL_REPO}"
_MODEL_DIR_NAME = "bge-small-zh-v1.5"

#: 模型文件清单：(文件名, 字节数, SHA-256)。2026-11-18 实测从
#: ``Qdrant/bge-small-zh-v1.5`` 下载并逐个校验（与 HF 的 LFS sha256 一致）。
MODEL_FILES: Tuple[Tuple[str, int, str], ...] = (
    ("config.json", 739, "9088751d39abbf86ec3d19ffca92ad62ad19075f7e59712e6c71217fa125d1d3"),
    (
        "model_optimized.onnx",
        94_781_076,
        "1294ea4b6331115a353d81f96b85e8c8d7fdcc284453d5b2fab5b016230aad38",
    ),
    ("ort_config.json", 1234, "97e78d1d21c2eb719e865b018f17915df6a12ed987446eb7f3f3a783a5afb1e1"),
    (
        "special_tokens_map.json",
        125,
        "b6d346be366a7d1d48332dbc9fdf3bf8960b5d879522b7799ddba59e76237ee3",
    ),
    (
        "tokenizer.json",
        439_125,
        "48cea5d44424912a6fd1ea647bf4fe50b55ab8b1e5879c3275f80e339e8fae26",
    ),
    (
        "tokenizer_config.json",
        367,
        "e6f3b96db926a37d4039995fbf5ad17de158dfb8f6343d607e4dbaad18d75f5a",
    ),
    ("vocab.txt", 109_540, "45bbac6b341c319adc98a532532882e91a9cefc0329aa57bac9ae761c27b291c"),
)
MODEL_TOTAL_BYTES = sum(size for _name, size, _sha in MODEL_FILES)
_ONNX_FILE = "model_optimized.onnx"
_TOKENIZER_FILE = "tokenizer.json"
_MAX_TOKENS = 512
_BATCH_SIZE = 32


def local_embedding_model_dir() -> Path:
    """模型落位目录（数据目录下，不在代码仓里；与语音模型同口径）。"""
    return data_dir() / "knowledge" / "embedding" / _MODEL_DIR_NAME


def local_engine_available() -> bool:
    """onnxruntime + tokenizers 是否可导入（可选组件，探测不抛）。"""
    try:
        import onnxruntime  # noqa: F401
        import tokenizers  # noqa: F401
    except Exception:  # noqa: BLE001 - 缺组件是正常状态，不是异常
        return False
    return True


def local_model_present(model_dir: Optional[Path] = None) -> bool:
    """全部模型文件都在且大小对得上（便宜检查；SHA 在下载时逐个验）。"""
    root = Path(model_dir) if model_dir is not None else local_embedding_model_dir()
    for name, size, _sha in MODEL_FILES:
        candidate = root / name
        try:
            if not candidate.is_file() or candidate.stat().st_size != size:
                return False
        except OSError:
            return False
    return True


def local_embedding_unavailable_reason() -> Optional[str]:
    """缺什么就说什么：返回 None 表示引擎与模型都就绪。"""
    if not local_engine_available():
        return (
            "本地向量引擎未安装（可选组件 onnxruntime / tokenizers 缺失）："
            "请安装产品的 knowledge 扩展组件后重试"
        )
    if not local_model_present():
        return (
            f"本地向量模型 {MODEL_NAME} 尚未下载（约 {MODEL_TOTAL_BYTES // (1024 * 1024)} MB，"
            "只下载一次）：可调用 start_local_model_download() 下载，"
            "或把模型文件放到 "
            f"{local_embedding_model_dir()}"
        )
    return None


# --------------------------------------------------------------------------- #
# 下载器（与 src/voice/wake.py 的 KWS 下载器同款：后台线程 + 状态 + 校验）
# --------------------------------------------------------------------------- #


class _LocalModelDownloadState:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.running = False
        self.downloaded = 0
        self.total = MODEL_TOTAL_BYTES
        self.error = ""
        self.done = False
        self.file = ""


_LOCAL_DOWNLOAD = _LocalModelDownloadState()


def _verify_file(path: Path, size: int, sha256: str) -> bool:
    try:
        if path.stat().st_size != size:
            return False
        hasher = hashlib.sha256()
        with open(path, "rb") as fh:
            while True:
                chunk = fh.read(1024 * 256)
                if not chunk:
                    break
                hasher.update(chunk)
        return hasher.hexdigest() == sha256
    except OSError:
        return False


def _download_local_model() -> None:
    """逐文件下载 → 验大小与 SHA-256 → 落位。已就位的文件跳过（断点续传）。"""
    target_dir = local_embedding_model_dir()
    target_dir.mkdir(parents=True, exist_ok=True)
    try:
        for name, size, sha256 in MODEL_FILES:
            target = target_dir / name
            if target.is_file() and _verify_file(target, size, sha256):
                with _LOCAL_DOWNLOAD.lock:
                    _LOCAL_DOWNLOAD.downloaded += size
                continue
            with _LOCAL_DOWNLOAD.lock:
                _LOCAL_DOWNLOAD.file = name
            url = f"{MODEL_SOURCE_URL}/resolve/main/{name}"
            temp = target_dir / (name + ".downloading")
            request = urllib.request.Request(
                url, headers={"User-Agent": "Jarvis/knowledge-embedding"}
            )
            hasher = hashlib.sha256()
            written = 0
            with urllib.request.urlopen(request, timeout=120) as resp:
                with open(temp, "wb") as out:
                    while True:
                        chunk = resp.read(1024 * 256)
                        if not chunk:
                            break
                        out.write(chunk)
                        hasher.update(chunk)
                        written += len(chunk)
                        with _LOCAL_DOWNLOAD.lock:
                            _LOCAL_DOWNLOAD.downloaded += len(chunk)
            if written != size or hasher.hexdigest() != sha256:
                try:
                    temp.unlink(missing_ok=True)
                except OSError:
                    pass
                raise ValueError(
                    f"本地向量模型文件 {name} 校验没通过（大小或 SHA-256 对不上），请重试。"
                )
            temp.replace(target)
        with _LOCAL_DOWNLOAD.lock:
            _LOCAL_DOWNLOAD.done = True
            _LOCAL_DOWNLOAD.running = False
            _LOCAL_DOWNLOAD.file = ""
    except Exception as exc:  # noqa: BLE001 - 下载失败要如实报给状态面
        logger.warning("本地向量模型下载失败：%s", exc)
        with _LOCAL_DOWNLOAD.lock:
            _LOCAL_DOWNLOAD.error = str(exc)
            _LOCAL_DOWNLOAD.running = False
            _LOCAL_DOWNLOAD.file = ""


def start_local_model_download() -> Dict[str, Any]:
    """开始下载本地向量模型（已下好直接报 done；下载中不重复开）。"""
    if local_model_present():
        return {
            "started": False,
            "done": True,
            "downloaded": MODEL_TOTAL_BYTES,
            "total": MODEL_TOTAL_BYTES,
            "error": "",
            "file": "",
        }
    with _LOCAL_DOWNLOAD.lock:
        if _LOCAL_DOWNLOAD.running:
            return {
                "started": False,
                "done": False,
                "downloaded": _LOCAL_DOWNLOAD.downloaded,
                "total": _LOCAL_DOWNLOAD.total,
                "error": "",
                "file": _LOCAL_DOWNLOAD.file,
            }
        _LOCAL_DOWNLOAD.running = True
        _LOCAL_DOWNLOAD.downloaded = 0
        _LOCAL_DOWNLOAD.error = ""
        _LOCAL_DOWNLOAD.done = False
        _LOCAL_DOWNLOAD.file = ""
    thread = threading.Thread(
        target=_download_local_model, name="knowledge-embedding-download", daemon=True
    )
    thread.start()
    return {
        "started": True,
        "done": False,
        "downloaded": 0,
        "total": MODEL_TOTAL_BYTES,
        "error": "",
        "file": "",
    }


def local_model_download_status() -> Dict[str, Any]:
    with _LOCAL_DOWNLOAD.lock:
        state = {
            "started": _LOCAL_DOWNLOAD.running or _LOCAL_DOWNLOAD.done,
            "done": bool(_LOCAL_DOWNLOAD.done or local_model_present()),
            "downloaded": _LOCAL_DOWNLOAD.downloaded,
            "total": _LOCAL_DOWNLOAD.total,
            "error": _LOCAL_DOWNLOAD.error,
            "file": _LOCAL_DOWNLOAD.file,
        }
    if local_model_present():
        state["done"] = True
        state["downloaded"] = MODEL_TOTAL_BYTES
    return state


def local_embedding_status() -> Dict[str, Any]:
    """本地向量引擎全状态（供知识库状态面/排障用；只读，不触发下载）。"""
    return {
        "engine_installed": local_engine_available(),
        "model_present": local_model_present(),
        "model": MODEL_NAME,
        "model_dir": str(local_embedding_model_dir()),
        "model_bytes": MODEL_TOTAL_BYTES,
        "download": local_model_download_status(),
    }


# --------------------------------------------------------------------------- #
# 嵌入器本体
# --------------------------------------------------------------------------- #


def _cls_pool_normalize(hidden: Any, batch: int) -> List[List[float]]:
    """BGE 官方口径：取 [CLS] 向量 + L2 归一化（纯 numpy 运算）。"""
    import numpy as np

    cls = np.asarray(hidden, dtype=np.float64)[:, 0, :]
    norms = np.linalg.norm(cls, axis=1, keepdims=True)
    norms[norms == 0.0] = 1.0
    vectors = cls / norms
    return [[float(value) for value in row] for row in vectors[:batch]]


class LocalOnnxEmbedder:
    """进程内 ONNX 语义嵌入器（无守护进程、无 Key、向量不出本机）。"""

    name = PROVIDER_NAME
    model = MODEL_NAME
    #: 真语义模型（与哈希词法回落的根本区别，状态面据此标注）。
    semantic = True
    #: 本地模型标记：状态面据此报 ``embedding_mode="local_model"``。
    local_model = True
    local_fallback = False
    dimensions = MODEL_DIMENSIONS

    def __init__(self, model_dir: Optional[Path] = None) -> None:
        self._model_dir = (
            Path(model_dir) if model_dir is not None else local_embedding_model_dir()
        )
        self._session = None
        self._tokenizer = None
        self._load_lock = threading.Lock()

    def _ensure_loaded(self) -> None:
        if self._session is not None:
            return
        with self._load_lock:
            if self._session is not None:
                return
            reason = None
            if not local_engine_available():
                reason = (
                    "本地向量引擎未安装（可选组件 onnxruntime / tokenizers 缺失）："
                    "请安装产品的 knowledge 扩展组件后重试"
                )
            elif not local_model_present(self._model_dir):
                reason = (
                    f"本地向量模型 {MODEL_NAME} 尚未下载（约 "
                    f"{MODEL_TOTAL_BYTES // (1024 * 1024)} MB）："
                    "先下载模型（start_local_model_download）再使用"
                )
            if reason is not None:
                raise EmbeddingUnavailableError(reason)
            try:
                import onnxruntime as ort
                from tokenizers import Tokenizer

                tokenizer = Tokenizer.from_file(str(self._model_dir / _TOKENIZER_FILE))
                tokenizer.enable_padding()
                tokenizer.enable_truncation(max_length=_MAX_TOKENS)
                session = ort.InferenceSession(
                    str(self._model_dir / _ONNX_FILE),
                    providers=["CPUExecutionProvider"],
                )
            except EmbeddingUnavailableError:
                raise
            except Exception as exc:  # noqa: BLE001 - 加载失败要翻成人话
                raise EmbeddingUnavailableError(
                    f"本地向量模型加载失败（{type(exc).__name__}: {exc}）；"
                    "模型文件可能损坏，可删除后重新下载："
                    f"{self._model_dir}"
                ) from exc
            self._tokenizer = tokenizer
            self._session = session

    def embed(self, texts: Sequence[str]) -> List[List[float]]:
        self._ensure_loaded()
        import numpy as np

        items = [str(text or "") for text in texts]
        vectors: List[List[float]] = []
        for start in range(0, len(items), _BATCH_SIZE):
            batch = items[start : start + _BATCH_SIZE]
            encodings = self._tokenizer.encode_batch(batch)
            feed = {
                "input_ids": np.array([e.ids for e in encodings], dtype=np.int64),
                "attention_mask": np.array(
                    [e.attention_mask for e in encodings], dtype=np.int64
                ),
                "token_type_ids": np.array(
                    [e.type_ids for e in encodings], dtype=np.int64
                ),
            }
            hidden = self._session.run(None, feed)[0]
            vectors.extend(_cls_pool_normalize(hidden, len(batch)))
        return vectors


def build_local_embedder() -> LocalOnnxEmbedder:
    """构造本地嵌入器；引擎/模型缺失时抛带人话原因的不可用错误。"""
    reason = local_embedding_unavailable_reason()
    if reason is not None:
        raise EmbeddingUnavailableError(reason)
    return LocalOnnxEmbedder()


__all__ = [
    "MODEL_FILES",
    "MODEL_NAME",
    "MODEL_REPO",
    "MODEL_TOTAL_BYTES",
    "PROVIDER_NAME",
    "LocalOnnxEmbedder",
    "build_local_embedder",
    "local_embedding_model_dir",
    "local_embedding_status",
    "local_embedding_unavailable_reason",
    "local_engine_available",
    "local_model_download_status",
    "local_model_present",
    "start_local_model_download",
]
