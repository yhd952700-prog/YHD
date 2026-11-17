"""声纹认人（说话人验证）：已落地（2026-11-15 第 4 段）。

做法（轻量，不用 torch）：
* 引擎：``sherpa-onnx``（Apache-2.0）+ ``onnxruntime``（MIT）；
  说话人嵌入模型是 3D-Speaker 的 ERes2Net-Base（中英文 16k），由
  sherpa-onnx 官方发布页转成 ONNX（约 38MB）。3D-Speaker 工具箱本身是
  Apache-2.0（GitHub modelscope/3D-Speaker 的 license 字段与 README
  原文）；sherpa-onnx 发布页注明“每个模型各有自己的许可，请看对应
  仓库”，对应仓库就是 3D-Speaker。模型文件**不随包带走**：第一次
  打开声纹时按需下载一次（见 :func:`start_model_download`），之后离线。
* 注册：每人录 2–3 句（建议 3 句，每句 1–30 秒），提特征向量后取
  平均（归一化）当“声纹”，只存本机；原始录音不存。
* 比对：余弦相似度 >= 阈值算“是本人”。默认阈值 0.5（sherpa-onnx
  说话人识别官方示例的默认值）；注册满 2 句自动标定：阈值 =
  ``min(同人两两相似度) - 0.05``，钳在 [0.30, 0.65]。
  沙箱实测（真人声，sherpa-onnx 发布页自带的 fangjun / leijun /
  liudehua 测试录音）：同人 0.647–0.824，异人 0.003–0.282，默认阈值
  0.5 落在分离带中间。真人声标定仍要 Joseph 在他机器上录自己的声音
  再跑一遍——这里的数字是公开测试录音的，不是他的声音。
* 不做登录时用声纹验身（没人提这个要求）：只做注册、验证（设置里
  “验证一下”）、状态、删除。登录联动是以后的一步。

隐私规矩（Joseph 授权，写死，不可绕过）：
1. 只存本机（``<数据目录>/voice/voiceprint.json`` + ``voiceprint.key``）。
2. 只有账号本人能看：记录按账号隔离，端点只认令牌主体；主账号也
   看不到子账号的声纹（ViewAs 视角下一律 403）；不给主账号加
   “删子账号声纹”的功能。
3. 随时能删：删了就是真删掉（记录与密文一起删）。
4. 不进备份、不进迁移包、不进导出：两个文件名在
   ``src/upgrade/backups.py`` 的“不得打包”名单里（有测试）。
5. 特征加密存：复用 ``src/gateway/totp.py`` 的 AES-GCM 模式——32 字节
   密钥单独存 ``voiceprint.key``（0600），AAD 绑账号与用途
   ``voiceprint-embedding-v1:<账号>``；密钥丢了旧特征解不开，只能
   重新注册（不静默回落到明文）。
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import logging
import os
import secrets
import shutil
import subprocess
import tempfile
import threading
import time
import urllib.request
import wave
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from src.common.atomic_write import atomic_write_bytes, atomic_write_text
from src.common.engines import EngineUnavailableError, data_dir

try:  # numpy 是 onnxruntime 的依赖，正常都有；没有时调用方如实报错。
    import numpy as np
except ImportError:  # pragma: no cover - 缺 numpy 的环境
    np = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)

#: 说话人嵌入模型（sherpa-onnx 官方发布页的 speaker-recongition-models）。
MODEL_NAME = "3dspeaker_speech_eres2net_base_sv_zh-cn_3dspeaker_16k.onnx"
MODEL_URL = (
    "https://github.com/k2-fsa/sherpa-onnx/releases/download/"
    f"speaker-recongition-models/{MODEL_NAME}"
)
#: 2026-11-15 实测下载：39,593,761 字节，与发布页 checksum.txt 一致。
MODEL_SIZE_BYTES = 39_593_761
MODEL_SHA256 = "1a331345f04805badbb495c775a6ddffcdd1a732567d5ec8b3d5749e3c7a5e4b"
MODEL_SOURCE = (
    "sherpa-onnx speaker-recongition-models 发布页，源模型为 "
    "ModelScope 3D-Speaker 的 ERes2Net-Base（Apache-2.0）"
)
MODEL_LICENSE = "Apache-2.0（3D-Speaker 工具箱；见 modelscope/3D-Speaker）"

#: 默认阈值：sherpa-onnx 说话人识别官方示例的默认值（余弦相似度）。
DEFAULT_THRESHOLD = 0.5
#: 标定阈值的钳制带：低于 0.30 误认太多，高于 0.65 自己的声音都可能认不出。
CALIBRATED_MIN = 0.30
CALIBRATED_MAX = 0.65
#: 标定时从最小同人相似度里扣掉的余量。
CALIBRATION_MARGIN = 0.05
#: 同人最小相似度低于这个值，标定结果标“不可靠”（录音质量/内容有问题）。
RELIABLE_INTRA_MIN = 0.5

#: 单句录音：最短 1 秒（模型至少要 1 秒才有稳定特征），最长 30 秒。
MIN_CLIP_SECONDS = 1.0
MAX_CLIP_SECONDS = 30.0
#: 录音太轻（均方根低于此值）算“没收到声音”，不拿静音去注册/验证。
MIN_RMS = 0.002
#: 单条上传上限：25MB（与 STT 同口径）。
MAX_AUDIO_BYTES = 25 * 1024 * 1024

#: 建议注册句数（界面文案用；只 1 句也能注册，但阈值标定不了）。
RECOMMENDED_SAMPLES = 3

VOICEPRINT_STORE_ENV = "LIUHAO_VOICEPRINT_STORE_FILE"
VOICEPRINT_KEY_ENV = "LIUHAO_VOICEPRINT_KEY_FILE"
VOICEPRINT_MODEL_ENV = "LIUHAO_VOICEPRINT_MODEL_FILE"

#: 隐私规矩（写死进状态响应与界面，不可由设置改掉）。
PRIVACY_RULES: Dict[str, Any] = {
    "stored_locally_only": True,
    "visible_only_to_self": True,
    "deletable_anytime": True,
    "included_in_backup": False,
    "included_in_export": False,
    "raw_audio_stored": False,
    "text": (
        "声纹只存本机，只有你本人能看，随时能删，不进备份、不进迁移包、"
        "不进导出；原始录音不保存，只存加密后的声纹特征。主账号也看不到"
        "子账号的声纹。"
    ),
}

_INSTALL_HINT = (
    "声纹认人还没装好：先装 sherpa-onnx（pip install sherpa-onnx，"
    "Apache-2.0，含 onnxruntime MIT），再打开声纹按需下载一次说话人"
    "模型（约 38MB，来源：sherpa-onnx speaker-recongition-models 发布页，"
    "之后离线可用）；最后录 2–3 句注册，标定比对阈值。"
)

_store_lock = threading.RLock()
_extractor_lock = threading.Lock()
_extractor: Any = None
_extractor_model: Optional[str] = None


# --------------------------------------------------------------------------- #
# 路径
# --------------------------------------------------------------------------- #


def voiceprint_dir() -> Path:
    return data_dir() / "voice" / "voiceprint"


def model_path() -> Path:
    explicit = (os.environ.get(VOICEPRINT_MODEL_ENV) or "").strip()
    if explicit:
        return Path(explicit).expanduser()
    return voiceprint_dir() / MODEL_NAME


def store_path() -> str:
    explicit = (os.environ.get(VOICEPRINT_STORE_ENV) or "").strip()
    if explicit:
        return explicit
    return str(data_dir() / "voice" / "voiceprint.json")


def key_path() -> str:
    explicit = (os.environ.get(VOICEPRINT_KEY_ENV) or "").strip()
    if explicit:
        return explicit
    return str(data_dir() / "voice" / "voiceprint.key")


# --------------------------------------------------------------------------- #
# 音频解码：wav 用标准库；其余格式走 ffmpeg（没有就老实说）
# --------------------------------------------------------------------------- #


def _require_numpy() -> Any:
    if np is None:
        raise EngineUnavailableError(
            "声纹认人需要 numpy（onnxruntime 的依赖），本机没有装。",
            "pip install numpy（或重装 sherpa-onnx，它会一起装上）。",
        )
    return np


def decode_audio(data: bytes, filename: str = "recording.wav") -> Any:
    """录音字节 -> 16kHz 单声道 float32 样本（[-1, 1]）。

    wav 直接用标准库解；mp3/m4a/webm/ogg/flac 等走本机 ffmpeg（沙箱与
    多数 Linux 有；Joseph 的 Windows 若没装 ffmpeg，传 wav 就行，界面
    会明说）。太短、太长、太轻、太大的录音直接报原因，不拿去提特征。
    """
    npy = _require_numpy()
    suffix = Path(filename or "recording.wav").suffix.lower()
    if not data:
        raise ValueError("录音是空的，请重新录一段再发。")
    if len(data) > MAX_AUDIO_BYTES:
        raise ValueError(f"录音太大（{len(data) // (1024 * 1024)}MB），单条上限 25MB，请分段发。")
    if suffix == ".wav":
        samples = _decode_wav(data)
    elif suffix in {".mp3", ".m4a", ".ogg", ".webm", ".flac", ".aac", ".opus"}:
        samples = _decode_with_ffmpeg(data, suffix)
    else:
        raise ValueError(f"不支持的录音格式：{suffix or '（没写后缀）'}；请传 wav，或装好 ffmpeg 后再传 mp3 / webm。")
    seconds = len(samples) / 16000
    if seconds < MIN_CLIP_SECONDS:
        raise ValueError(f"录音太短（{seconds:.1f} 秒），至少录 1 秒再试。")
    if seconds > MAX_CLIP_SECONDS:
        raise ValueError(f"录音太长（{seconds:.0f} 秒），单句上限 30 秒，请分段录。")
    rms = float(npy.sqrt(npy.mean(npy.asarray(samples, dtype=npy.float64) ** 2))) if len(samples) else 0.0
    if rms < MIN_RMS:
        raise ValueError("这段录音太轻、几乎没声音，请靠近麦克风再录一段。")
    return samples


def _decode_wav(data: bytes) -> Any:
    npy = _require_numpy()
    try:
        with wave.open(io.BytesIO(data), "rb") as wav:
            channels = wav.getnchannels()
            width = wav.getsampwidth()
            rate = wav.getframerate()
            frames = wav.readframes(wav.getnframes())
    except (wave.Error, EOFError) as exc:
        raise ValueError(f"这段 wav 录音读不出来：{exc}，请重新录一段。") from exc
    if width == 2:
        audio = npy.frombuffer(frames, dtype="<i2").astype(npy.float32) / 32768.0
    elif width == 1:
        audio = (npy.frombuffer(frames, dtype=npy.uint8).astype(npy.float32) - 128.0) / 128.0
    elif width == 4:
        audio = npy.frombuffer(frames, dtype="<i4").astype(npy.float32) / 2147483648.0
    else:
        raise ValueError(f"不支持的 wav 位宽：{width * 8} 位，请用 16 位录音。")
    if channels > 1:
        audio = audio.reshape(-1, channels).mean(axis=1).astype(npy.float32)
    if rate != 16000:
        audio = _resample(audio, rate, 16000)
    return npy.ascontiguousarray(audio, dtype=npy.float32)


def _resample(audio: Any, src_rate: int, dst_rate: int) -> Any:
    npy = _require_numpy()
    if src_rate == dst_rate or len(audio) == 0:
        return audio
    target_len = max(1, int(round(len(audio) * dst_rate / src_rate)))
    idx = npy.linspace(0, len(audio) - 1, target_len)
    return npy.interp(idx, npy.arange(len(audio)), audio).astype(npy.float32)


def _decode_with_ffmpeg(data: bytes, suffix: str) -> Any:
    npy = _require_numpy()
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        raise EngineUnavailableError(
            "这段录音不是 wav，需要 ffmpeg 才能解码，但本机没装 ffmpeg。",
            "请传 wav 格式的录音，或在本机装好 ffmpeg 后再试（Windows 去 ffmpeg 官网下）。",
        )
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(data)
        tmp_name = tmp.name
    try:
        proc = subprocess.run(
            [ffmpeg, "-v", "error", "-i", tmp_name, "-ar", "16000", "-ac", "1",
             "-f", "s16le", "-"],
            capture_output=True, timeout=60,
        )
    except subprocess.TimeoutExpired as exc:
        raise ValueError("解码这段录音超时（60 秒），请换一段短一点的再试。") from exc
    finally:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
    if proc.returncode != 0 or not proc.stdout:
        raise ValueError("这段录音解不出来（格式不对或文件坏了），请重新录一段。")
    return (npy.frombuffer(proc.stdout, dtype="<i2").astype(npy.float32) / 32768.0)


# --------------------------------------------------------------------------- #
# 特征提取（sherpa-onnx 3D-Speaker ERes2Net-Base）
# --------------------------------------------------------------------------- #


def _sherpa_available() -> bool:
    try:
        import sherpa_onnx  # noqa: F401
    except Exception:
        return False
    return True


def _get_extractor() -> Any:
    """懒加载的单例提特征器；模型没下载时如实报，不编特征。"""
    global _extractor, _extractor_model
    path = model_path()
    with _extractor_lock:
        if _extractor is not None and _extractor_model == str(path):
            return _extractor
        if not _sherpa_available():
            raise EngineUnavailableError(
                "声纹认人还没装好：sherpa-onnx 没装，提不出声纹特征。",
                "pip install sherpa-onnx（Apache-2.0），再打开声纹下载一次模型。",
            )
        if not path.is_file():
            raise EngineUnavailableError(
                "说话人特征模型还没下载（约 38MB），提不出声纹特征。",
                "在设置 → 声纹认人里点“下载模型”，联网一次，之后离线可用。",
            )
        import sherpa_onnx

        config = sherpa_onnx.SpeakerEmbeddingExtractorConfig(
            model=str(path), num_threads=2, provider="cpu"
        )
        _extractor = sherpa_onnx.SpeakerEmbeddingExtractor(config)
        _extractor_model = str(path)
        return _extractor


def _extract_embedding_from_samples(samples: Any, sample_rate: int = 16000) -> Any:
    """一段 16kHz 单声道样本 -> 归一化的 512 维声纹向量（float64）。

    测试用 monkeypatch 换掉这个函数（注入确定性向量）；生产走真模型。
    """
    npy = _require_numpy()
    extractor = _get_extractor()
    stream = extractor.create_stream()
    stream.accept_waveform(sample_rate, npy.asarray(samples, dtype=npy.float32))
    embedding = npy.asarray(extractor.compute(stream), dtype=npy.float64)
    norm = float(npy.linalg.norm(embedding))
    if norm == 0.0:
        raise ValueError("这段录音提不出声纹特征（全是静音或噪声），请重新录一段。")
    return embedding / norm


def cosine_similarity(a: Any, b: Any) -> float:
    """两段归一化向量的余弦相似度（-1..1；1 = 同一个人方向完全一致）。"""
    npy = _require_numpy()
    va = npy.asarray(a, dtype=npy.float64)
    vb = npy.asarray(b, dtype=npy.float64)
    denom = float(npy.linalg.norm(va) * npy.linalg.norm(vb))
    if denom == 0.0:
        return 0.0
    return float(va @ vb / denom)


# --------------------------------------------------------------------------- #
# 阈值标定
# --------------------------------------------------------------------------- #


def calibrate_threshold(intra_similarities: Sequence[float]) -> Tuple[float, Dict[str, Any]]:
    """按注册样本两两相似度定阈值。

    满 2 句才标定：阈值 = 最小同人相似度 - 0.05，钳在 [0.30, 0.65]；
    只有 1 句标定不了，回默认阈值并老实说没标定。同人相似度本身就很低
    （< 0.5）时标“不可靠”：录音可能有问题，界面会提示重录。
    """
    values = [float(v) for v in intra_similarities if v is not None]
    if len(values) < 1:
        return DEFAULT_THRESHOLD, {
            "calibrated": False, "reliable": False,
            "intra_min": None, "intra_mean": None,
            "reason": "注册样本不足 2 句，无法标定。",
        }
    intra_min = min(values)
    intra_mean = sum(values) / len(values)
    threshold = min(max(intra_min - CALIBRATION_MARGIN, CALIBRATED_MIN), CALIBRATED_MAX)
    return threshold, {
        "calibrated": True,
        "reliable": bool(intra_min >= RELIABLE_INTRA_MIN),
        "intra_min": intra_min,
        "intra_mean": intra_mean,
        "reason": "" if intra_min >= RELIABLE_INTRA_MIN else "同人相似度偏低，建议重录更清楚的注册语音。",
    }


def _pairwise_intra(embeddings: Sequence[Any]) -> List[float]:
    pairs: List[float] = []
    for i in range(len(embeddings)):
        for j in range(i + 1, len(embeddings)):
            pairs.append(cosine_similarity(embeddings[i], embeddings[j]))
    return pairs


# --------------------------------------------------------------------------- #
# 加密存储（复用 totp.py 的 AES-GCM 模式：密钥文件 0600、AAD 绑账号与用途）
# --------------------------------------------------------------------------- #


def _secure_chmod(path: str) -> None:
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass  # Windows 上 0600 没有同等语义：不阻断，文件仍在数据目录内。


def _load_or_create_key() -> bytes:
    path = Path(key_path())
    if path.is_file():
        key = path.read_bytes()
        if len(key) != 32:
            raise ValueError("声纹密钥文件损坏（长度不对），请删除声纹注册后重新注册")
        return key
    key = secrets.token_bytes(32)
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_bytes(str(path), key)
    _secure_chmod(str(path))
    return key


def _encrypt_embedding(principal: str, raw: bytes) -> str:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    key = _load_or_create_key()
    nonce = secrets.token_bytes(12)
    aad = f"voiceprint-embedding-v1:{principal}".encode("utf-8")
    blob = AESGCM(key).encrypt(nonce, raw, aad)
    return base64.b64encode(nonce + blob).decode("ascii")


def _decrypt_embedding(principal: str, stored: str) -> Optional[bytes]:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    try:
        raw = base64.b64decode(str(stored or "").encode("ascii"), validate=True)
        nonce, blob = raw[:12], raw[12:]
        key = _load_or_create_key()
        aad = f"voiceprint-embedding-v1:{principal}".encode("utf-8")
        return AESGCM(key).decrypt(nonce, blob, aad)
    except Exception:  # noqa: BLE001 - 解不开就是“没有可用声纹”，不展开原因
        logger.warning("声纹特征解密失败 principal=%s", principal)
        return None


def _vec_to_bytes(vec: Any) -> bytes:
    npy = _require_numpy()
    return npy.asarray(vec, dtype="<f4").tobytes()


def _bytes_to_vec(raw: bytes) -> Any:
    npy = _require_numpy()
    return npy.frombuffer(raw, dtype="<f4").astype(npy.float64)


def _empty_record() -> Dict[str, Any]:
    return {
        "embedding_enc": "",
        "samples_enc": [],
        "threshold": DEFAULT_THRESHOLD,
        "calibrated": False,
        "reliable": False,
        "sample_count": 0,
        "intra_min": None,
        "intra_mean": None,
        "model": MODEL_NAME,
        "enrolled_at": None,
        "updated_at": None,
    }


def _load_store() -> Dict[str, Any]:
    path = Path(store_path())
    if not path.is_file():
        return {"version": 1, "principals": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"声纹记录文件损坏，拒绝读写：{exc}") from exc
    if not isinstance(data, dict) or not isinstance(data.get("principals"), dict):
        raise ValueError("声纹记录文件格式不对，拒绝读写")
    return data


def _save_store(data: Dict[str, Any]) -> None:
    path = Path(store_path())
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(str(path), json.dumps(data, ensure_ascii=False))
    _secure_chmod(str(path))


def _get_record(principal: str) -> Dict[str, Any]:
    data = _load_store()
    raw = data["principals"].get(principal)
    record = _empty_record()
    if isinstance(raw, dict):
        for key in record:
            if key in raw:
                record[key] = raw[key]
    return record


def _put_record(principal: str, record: Dict[str, Any]) -> None:
    with _store_lock:
        data = _load_store()
        data["principals"][principal] = record
        _save_store(data)


def _delete_record(principal: str) -> bool:
    with _store_lock:
        data = _load_store()
        if principal not in data["principals"]:
            return False
        del data["principals"][principal]
        _save_store(data)
        return True


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# --------------------------------------------------------------------------- #
# 注册 / 验证 / 标定 / 删除（只认 principal，不认请求体里的任何名字）
# --------------------------------------------------------------------------- #


def enroll(principal: str, clips: Sequence[Tuple[Any, str]]) -> Dict[str, Any]:
    """注册：``clips`` 是 ``(16kHz 单声道样本, 文件名)`` 的列表。

    每句提一个向量；平均后归一化当声纹；满 2 句自动标定阈值。重新注册会
    覆盖上一次的声纹（旧的密文一起换掉）。只返回摘要，不返回向量。
    """
    npy = _require_numpy()
    principal = (principal or "").strip()
    if not principal:
        raise ValueError("注册声纹需要先登录（没有账号）。")
    if not clips:
        raise ValueError("注册至少要录 1 句；建议录 2–3 句，认得更准。")
    embeddings = []
    for samples, _filename in clips:
        arr = npy.asarray(samples, dtype=npy.float32)
        seconds = len(arr) / 16000
        if seconds < MIN_CLIP_SECONDS:
            raise ValueError(f"有一句录音太短（{seconds:.1f} 秒），至少录 1 秒再试。")
        if seconds > MAX_CLIP_SECONDS:
            raise ValueError("有一句录音太长（超过 30 秒），请分段录。")
        embeddings.append(_extract_embedding_from_samples(arr))
    mean_vec = npy.mean(npy.stack(embeddings), axis=0)
    norm = float(npy.linalg.norm(mean_vec))
    if norm == 0.0:
        raise ValueError("这几句录音提不出声纹特征，请重新录一段。")
    mean_vec = mean_vec / norm
    pairs = _pairwise_intra(embeddings)
    threshold, report = calibrate_threshold(pairs)
    now = _now_iso()
    record = _get_record(principal)
    record.update(
        {
            "embedding_enc": _encrypt_embedding(principal, _vec_to_bytes(mean_vec)),
            "samples_enc": [
                _encrypt_embedding(principal, _vec_to_bytes(vec)) for vec in embeddings
            ],
            "threshold": threshold,
            "calibrated": bool(report["calibrated"]),
            "reliable": bool(report["reliable"]),
            "sample_count": len(embeddings),
            "intra_min": report["intra_min"],
            "intra_mean": report["intra_mean"],
            "model": MODEL_NAME,
            "enrolled_at": record.get("enrolled_at") or now,
            "updated_at": now,
        }
    )
    _put_record(principal, record)
    return {
        "enrolled": True,
        "sample_count": len(embeddings),
        "threshold": threshold,
        "calibrated": bool(report["calibrated"]),
        "reliable": bool(report["reliable"]),
        "intra_min": report["intra_min"],
        "intra_mean": report["intra_mean"],
        "calibration_note": report["reason"],
        "model": MODEL_NAME,
    }


def verify(principal: str, samples: Any) -> Dict[str, Any]:
    """验证：这段声音是不是 ``principal`` 本人。

    没注册返回 ``not_enrolled``（不是“认错”）；注册了返回分数与阈值，
    ``matched`` = 分数 >= 阈值。分数是余弦相似度，不是概率。
    """
    npy = _require_numpy()
    principal = (principal or "").strip()
    record = _get_record(principal) if principal else _empty_record()
    if not record.get("embedding_enc"):
        return {
            "matched": False, "score": None,
            "threshold": None, "enrolled": False,
            "reason": "not_enrolled",
            "message": "还没注册声纹：先录 2–3 句注册，再来验证。",
        }
    raw = _decrypt_embedding(principal, str(record["embedding_enc"]))
    if raw is None:
        return {
            "matched": False, "score": None,
            "threshold": float(record.get("threshold") or DEFAULT_THRESHOLD),
            "enrolled": True, "reason": "decrypt_failed",
            "message": "声纹解不开了（密钥文件可能丢了），请删除后重新注册。",
        }
    query = _extract_embedding_from_samples(npy.asarray(samples, dtype=npy.float32))
    score = cosine_similarity(query, _bytes_to_vec(raw))
    threshold = float(record.get("threshold") or DEFAULT_THRESHOLD)
    matched = bool(score >= threshold)
    return {
        "matched": matched,
        "score": round(score, 3),
        "threshold": threshold,
        "enrolled": True,
        "reason": "matched" if matched else "not_matched",
        "message": "认出来是你。" if matched else "这段声音不像你注册的声纹。",
    }


def calibrate(principal: str) -> Dict[str, Any]:
    """用已存的注册样本重算阈值（注册后、或觉得认得不准时点一次）。"""
    principal = (principal or "").strip()
    record = _get_record(principal) if principal else _empty_record()
    samples_enc = record.get("samples_enc") or []
    if not samples_enc:
        raise ValueError("还没注册声纹，没法标定阈值；先录 2–3 句注册。")
    embeddings = []
    for stored in samples_enc:
        raw = _decrypt_embedding(principal, str(stored))
        if raw is None:
            raise ValueError("声纹解不开了（密钥文件可能丢了），请删除后重新注册。")
        embeddings.append(_bytes_to_vec(raw))
    threshold, report = calibrate_threshold(_pairwise_intra(embeddings))
    record["threshold"] = threshold
    record["calibrated"] = bool(report["calibrated"])
    record["reliable"] = bool(report["reliable"])
    record["intra_min"] = report["intra_min"]
    record["intra_mean"] = report["intra_mean"]
    record["updated_at"] = _now_iso()
    _put_record(principal, record)
    return {
        "threshold": threshold,
        "calibrated": bool(report["calibrated"]),
        "reliable": bool(report["reliable"]),
        "intra_min": report["intra_min"],
        "intra_mean": report["intra_mean"],
        "sample_count": len(embeddings),
        "calibration_note": report["reason"],
    }


def delete_enrollment(principal: str) -> bool:
    """删除自己的声纹注册：记录与密文一起删，真没了（没有回收站）。"""
    return _delete_record((principal or "").strip())


# --------------------------------------------------------------------------- #
# 模型按需下载（打开声纹时下一次，不是装机时下）
# --------------------------------------------------------------------------- #


class _DownloadState:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.running = False
        self.downloaded = 0
        self.total = MODEL_SIZE_BYTES
        self.error = ""
        self.done = False


_DOWNLOAD = _DownloadState()


def _download_model() -> None:
    """后台下载模型文件（约 38MB），下完验大小与 SHA-256 再落盘。"""
    target_dir = voiceprint_dir()
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / MODEL_NAME
    tmp_path = target_dir / (MODEL_NAME + ".downloading")
    try:
        request = urllib.request.Request(MODEL_URL, headers={"User-Agent": "Jarvis/voiceprint"})
        with urllib.request.urlopen(request, timeout=120) as resp:
            total = int(resp.headers.get("Content-Length") or MODEL_SIZE_BYTES)
            with _DOWNLOAD.lock:
                _DOWNLOAD.total = total
            hasher = hashlib.sha256()
            downloaded = 0
            with open(tmp_path, "wb") as out:
                while True:
                    chunk = resp.read(1024 * 256)
                    if not chunk:
                        break
                    out.write(chunk)
                    hasher.update(chunk)
                    downloaded += len(chunk)
                    with _DOWNLOAD.lock:
                        _DOWNLOAD.downloaded = downloaded
        if tmp_path.stat().st_size != MODEL_SIZE_BYTES:
            raise ValueError(
                f"模型文件大小不对（下到 {tmp_path.stat().st_size} 字节，"
                f"应为 {MODEL_SIZE_BYTES} 字节），请重试。"
            )
        if hasher.hexdigest() != MODEL_SHA256:
            raise ValueError("模型文件校验没通过（SHA-256 对不上），请重试。")
        tmp_path.replace(target)
        with _DOWNLOAD.lock:
            _DOWNLOAD.done = True
            _DOWNLOAD.running = False
    except Exception as exc:  # noqa: BLE001 - 下载失败要如实报给界面
        logger.warning("声纹模型下载失败：%s", exc)
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass
        with _DOWNLOAD.lock:
            _DOWNLOAD.error = str(exc)
            _DOWNLOAD.running = False


def start_model_download() -> Dict[str, Any]:
    """开始下载模型（已下好直接报 done；下载中不重复开）。"""
    if model_path().is_file():
        return {"started": False, "done": True, "downloaded": MODEL_SIZE_BYTES,
                "total": MODEL_SIZE_BYTES, "error": ""}
    with _DOWNLOAD.lock:
        if _DOWNLOAD.running:
            return {"started": False, "done": False,
                    "downloaded": _DOWNLOAD.downloaded, "total": _DOWNLOAD.total,
                    "error": ""}
        _DOWNLOAD.running = True
        _DOWNLOAD.downloaded = 0
        _DOWNLOAD.error = ""
        _DOWNLOAD.done = False
    thread = threading.Thread(target=_download_model, name="voiceprint-model-download", daemon=True)
    thread.start()
    return {"started": True, "done": False, "downloaded": 0,
            "total": MODEL_SIZE_BYTES, "error": ""}


def model_download_status() -> Dict[str, Any]:
    with _DOWNLOAD.lock:
        state = {
            "started": _DOWNLOAD.running or _DOWNLOAD.done,
            "done": bool(_DOWNLOAD.done or model_path().is_file()),
            "downloaded": _DOWNLOAD.downloaded,
            "total": _DOWNLOAD.total,
            "error": _DOWNLOAD.error,
        }
    if model_path().is_file():
        state["done"] = True
        state["downloaded"] = MODEL_SIZE_BYTES
    return state


# --------------------------------------------------------------------------- #
# 状态（给 GET /v1/voice/voiceprint/status 用，不触发下载）
# --------------------------------------------------------------------------- #


def voiceprint_status(principal: str = "") -> Dict[str, Any]:
    """声纹认人可用状态：有模型才算“可用”，没注册就老实说没注册。"""
    principal = (principal or "").strip()
    sherpa = _sherpa_available()
    model_file = model_path()
    model_present = model_file.is_file()
    available = bool(sherpa and model_present)
    record = _get_record(principal) if principal else _empty_record()
    enrolled = bool(record.get("embedding_enc"))
    missing: List[str] = []
    if not sherpa:
        missing.append("sherpa-onnx 还没装（pip install sherpa-onnx）。")
    if not model_present:
        missing.append(
            "说话人特征模型还没下载（3D-Speaker ERes2Net-Base，约 38MB；"
            "在设置 → 声纹认人里点“下载模型”，联网一次，之后离线可用）。"
        )
    if not enrolled:
        missing.append("还没有注册录音：先录 2–3 句注册，贾维斯才认得出你。")
    elif not record.get("calibrated"):
        missing.append("比对阈值还没标定：只录了 1 句，再录 1 句就自动标定。")
    if available:
        install_hint = ""
    elif not sherpa:
        install_hint = _INSTALL_HINT
    elif not model_present:
        install_hint = (
            "在设置 → 声纹认人里点“下载模型”（约 38MB，联网一次，"
            "来源：sherpa-onnx speaker-recongition-models 发布页，之后离线可用）。"
        )
    elif not enrolled:
        install_hint = "录 2–3 句注册，标定比对阈值。"
    else:  # 引擎、模型、注册都在却不可用（防御分支）
        install_hint = ""
    return {
        "available": available,
        "engine": "sherpa-onnx" if available else None,
        "model": MODEL_NAME if model_present else None,
        "model_present": model_present,
        "model_size_mb": round(MODEL_SIZE_BYTES / (1024 * 1024), 1),
        "model_source": MODEL_SOURCE,
        "model_license": MODEL_LICENSE,
        "model_download": model_download_status(),
        "enrolled": enrolled,
        "enrolled_at": record.get("enrolled_at") if enrolled else None,
        "sample_count": int(record.get("sample_count") or 0) if enrolled else 0,
        "threshold": float(record.get("threshold") or DEFAULT_THRESHOLD) if enrolled else None,
        "calibrated": bool(record.get("calibrated")) if enrolled else False,
        "reliable": bool(record.get("reliable")) if enrolled else False,
        "enrolled_speakers": [principal] if enrolled and principal else [],
        "missing": missing,
        "install_hint": install_hint,
        "note": (
            "声纹只认“是不是你本人”，不做登录验身；验证只在设置里“验证一下”。"
            if available else
            "声纹认人还没接好：缺上面列的东西，贾维斯分不清是谁在说话，也不会假装分得清。"
        ),
        "privacy": dict(PRIVACY_RULES),
        "default_threshold": DEFAULT_THRESHOLD,
    }
