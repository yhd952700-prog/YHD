"""语音唤醒词“贾维斯”：sherpa-onnx 关键词 spots（KWS 专用模型）已接通。

接通经过（2026-11-15 实测，不是猜的；测试脚本与原始数字见
``~/workspace/verify-d-20261113/tmp/wakeword/``，本文件只写结论）：

- **Vosk 路走不通**（上一轮已实测，数字不变）：小模型限定词表 10 句
  无关话误唤 1 次；大模型 5 句唤醒全漏听（“贾维斯”被听成“故为私 /
  古为食 / 禁锢维斯 ……”）；Whisper 也一样听错。名字太生僻，通用
  语音识别的语言模型不认它。
- **sherpa-onnx KeywordSpotter + KWS 专用模型走通了**：模型是
  ``sherpa-onnx-kws-zipformer-wenetspeech-3.3M-2024-01-01``（Apache-2.0，
  sherpa-onnx kws-models 发布页，32,654,866 字节，SHA-256 与发布页
  checksum.txt 一致），关键词写成拼音 ``j iǎ w éi s ī @贾维斯``。
  检出 23/24、误唤 0/31——过了“负样本 0 误唤、正样本检出 ≥90%”的硬标准。
- **关键坑：采样率**。先用 VITS 中文模型（8kHz 输出）合成正样本测，
  只检出 4/24——不是模型不行，是 8k 音频缺高频信息；换成 16kHz 的
  Matcha 中文模型合成同样的句子，检出升到 23/24。Piper 不可用：
  espeak-ng 的中文前端连“贾”字都不认识（读成英文拼写），合成的第
  一个音节就是错的；edge-tts 连不上微软的网（沙箱代理 SSL 报错）。
  麦克风本来就是 16k，所以这不影响真机使用，只影响“拿什么做测试”。
- **没过的路线也记下来**：ASR 14M 模型当 spots 用 0/24；hotwords 加权
  ASR（加权 5.0）1/24；阈值 0.25→0.10、加分 1.0→3.0、前置静音、
  多读音变体、fp32/int8 两档检查点——都不改变结论。10dB 大噪音下
  检出掉到 9/24（20dB 轻噪音仍是 22/24），吵环境里别指望它。

诚实口径（面板上原样写，不藏着）：
- 正样本 24 句全是 **合成语音**（Matcha 中文单音色 × 8 句 × 语速
  0.9/1.0/1.1），不是真人声；**真人声验证还没做**，要 Joseph 在他
  机器上说几遍“贾维斯”，用设置里的“验证一下”当场测。
- 负样本 31 条里 16 条是真人声（声纹固件 6 条 + 模型自带测试录音
  10 条），15 条是合成无关句；误唤 0 次。
- 模型不随包带走：打开唤醒词时按需下载一次（约 32MB），下完验
  大小与 SHA-256 再解压，之后离线。常驻监听默认不开，设置里能关。
- 唤醒后目前只记一笔事件（上次唤醒时间 + 列表，面板可见）；听到
  唤醒词就自动弹操作台、开始收音，是下一步（要接桌面窗口唤起）。

Vosk 小模型下载还留着（老路，没拆），但唤醒不再走 Vosk。
"""

from __future__ import annotations

import hashlib
import io
import json
import logging
import os
import queue
import tarfile
import threading
import urllib.request
import wave
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from src.common.engines import EngineUnavailableError, data_dir

try:  # numpy 是 onnxruntime 的依赖，正常都有；没有时调用方如实报错。
    import numpy as np
except ImportError:  # pragma: no cover - 缺 numpy 的环境
    np = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)

#: 唤醒词（写死：Joseph 定的就是“贾维斯”）。
WAKE_WORD = "贾维斯"

#: Vosk 小模型：44MB，2026-11-15 实测可下载、可加载。
SMALL_MODEL_NAME = "vosk-model-small-cn-0.22"
SMALL_MODEL_URL = "https://alphacephei.com/vosk/models/vosk-model-small-cn-0.22.zip"
#: Vosk 大模型：1.36GB，实测仍听不出“贾维斯”（见模块说明），不要为唤醒下它。
BIG_MODEL_NAME = "vosk-model-cn-0.22"

#: 小模型文件约 44MB：下载进度按这个估算（实际以 Content-Length 为准）。
SMALL_MODEL_BYTES = 43_898_754

# --------------------------------------------------------------------------- #
# KWS 专用模型（sherpa-onnx KeywordSpotter，2026-11-15 第 5 段接通）
# --------------------------------------------------------------------------- #

#: KWS 专用模型（sherpa-onnx kws-models 发布页；Apache-2.0，见模型自带 README）。
KWS_MODEL_NAME = "sherpa-onnx-kws-zipformer-wenetspeech-3.3M-2024-01-01"
KWS_TARBALL_NAME = KWS_MODEL_NAME + ".tar.bz2"
KWS_TARBALL_URL = (
    "https://github.com/k2-fsa/sherpa-onnx/releases/download/"
    f"kws-models/{KWS_TARBALL_NAME}"
)
#: 2026-11-15 实测下载：32,654,866 字节，与发布页 checksum.txt 一致。
KWS_TARBALL_BYTES = 32_654_866
KWS_TARBALL_SHA256 = "b2f7c89690dc8ce4c6ed6afeab7cd800c36ad1421fb6b6302b4a4b194cf7f35f"
KWS_MODEL_SOURCE = (
    "sherpa-onnx kws-models 发布页（k2-fsa/sherpa-onnx），"
    "WenetSpeech 训练的 Zipformer 关键词模型"
)
KWS_LICENSE = "Apache-2.0"

#: 关键词文件行：拼音 token 空格分隔 + @ + 汉字（sherpa-onnx KWS 官方示例同款）。
KWS_KEYWORDS = "j iǎ w éi s ī @贾维斯"
#: spots 判定阈值：0.25 默认值；实测 0.10–0.25 结果一样（22/24 int8），取默认。
KWS_THRESHOLD = 0.25
#: spots 判定加分：1.0 默认值；实测 1.0–3.0 结果一样，取默认。
KWS_SCORE = 1.0

#: 模型文件（解压后模型目录里必须齐这 4 个才算“下好了”；用 fp32
#: epoch-99 检查点：16k 合成正样本检出 23/24，int8 同款是 22/24）。
KWS_ENCODER_NAME = "encoder-epoch-99-avg-1-chunk-16-left-64.onnx"
KWS_DECODER_NAME = "decoder-epoch-99-avg-1-chunk-16-left-64.onnx"
KWS_JOINER_NAME = "joiner-epoch-99-avg-1-chunk-16-left-64.onnx"
KWS_TOKENS_NAME = "tokens.txt"
KWS_MODEL_FILES = (KWS_ENCODER_NAME, KWS_DECODER_NAME, KWS_JOINER_NAME, KWS_TOKENS_NAME)

#: 2026-11-15 实测的检出数字（面板上原样写；正样本是合成语音，见模块说明）。
KWS_VALIDATION: Dict[str, Any] = {
    "engine": "sherpa-onnx KeywordSpotter（Zipformer KWS 3.3M，fp32）",
    "positive_detection": "23/24",
    "positive_samples": "24 句 16kHz 合成语音（Matcha 中文单音色 × 8 句 × 语速 0.9/1.0/1.1）",
    "false_alarms": "0/31",
    "negative_samples": "31 条（16 条真人声：声纹固件 6 条 + 模型自带测试录音 10 条；15 条合成无关句）",
    "noise_snr20db": "22/24",
    "noise_snr10db": "9/24",
    "real_voice_validated": False,
}

#: 测试用：用已解压好的模型目录代替 <数据目录>/voice/wake/<模型名>。
WAKE_KWS_MODEL_ENV = "LIUHAO_WAKE_KWS_MODEL_DIR"

#: 自检录音（设置里“验证一下”）：最短 0.5 秒（单说“贾维斯”约 1 秒），
#: 最长 30 秒（与声纹同口径）；太轻的录音直接报原因，不拿去判定。
CHECK_MIN_SECONDS = 0.5
CHECK_MAX_SECONDS = 30.0
CHECK_MIN_RMS = 0.002
#: 自检单条上传上限：25MB（与声纹同口径）。
CHECK_MAX_AUDIO_BYTES = 25 * 1024 * 1024

#: 唤醒事件最多记 20 条（面板只看最近的）。
MAX_WAKE_EVENTS = 20


def wake_model_dir() -> Path:
    return data_dir() / "voice" / "wake"


def small_model_path() -> Path:
    return wake_model_dir() / SMALL_MODEL_NAME


def _vosk_installed() -> bool:
    try:
        import vosk  # noqa: F401
    except Exception:
        return False
    return True


def _mic_available() -> bool:
    """本机有没有麦克风采集路（sounddevice 能 import 就算有；没设备时监听仍会失败并报原因）。"""
    try:
        import sounddevice  # noqa: F401
    except Exception:
        return False
    return True


def contains_wake_word(text: str) -> bool:
    """一段识别文字里有没有“贾维斯”（去空格后子串匹配；纯逻辑，可单测）。"""
    return WAKE_WORD in (text or "").replace(" ", "").replace("　", "")


def spot_in_result(result_json: str) -> bool:
    """Vosk 返回的 result/partial JSON 里有没有唤醒词；解析失败算没有。"""
    try:
        payload = json.loads(result_json)
    except (ValueError, TypeError):
        return False
    if not isinstance(payload, dict):
        return False
    for key in ("text", "partial"):
        value = payload.get(key)
        if isinstance(value, str) and contains_wake_word(value):
            return True
    return False


class _DownloadState:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.running = False
        self.downloaded = 0
        self.total = SMALL_MODEL_BYTES
        self.error = ""
        self.done = False


_DOWNLOAD = _DownloadState()


def _download_small_model() -> None:
    """后台下载小模型（44MB zip，解压到 <数据目录>/voice/wake/）。"""
    target_dir = wake_model_dir()
    target_dir.mkdir(parents=True, exist_ok=True)
    zip_path = target_dir / (SMALL_MODEL_NAME + ".zip")
    try:
        request = urllib.request.Request(SMALL_MODEL_URL, headers={"User-Agent": "Jarvis/0.4.6"})
        with urllib.request.urlopen(request, timeout=120) as resp, open(zip_path, "wb") as out:
            total = resp.headers.get("Content-Length")
            with _DOWNLOAD.lock:
                _DOWNLOAD.total = int(total) if total else SMALL_MODEL_BYTES
            while True:
                chunk = resp.read(1024 * 256)
                if not chunk:
                    break
                out.write(chunk)
                with _DOWNLOAD.lock:
                    _DOWNLOAD.downloaded += len(chunk)
        with zipfile.ZipFile(zip_path) as zf:
            zf.extractall(target_dir)
        try:
            zip_path.unlink()
        except OSError:
            pass
        with _DOWNLOAD.lock:
            _DOWNLOAD.done = True
            _DOWNLOAD.running = False
    except Exception as exc:  # noqa: BLE001 - 下载失败要如实报原因
        with _DOWNLOAD.lock:
            _DOWNLOAD.error = str(exc) or repr(exc)
            _DOWNLOAD.running = False


def start_small_model_download() -> Dict[str, Any]:
    """开始下载小模型（已在下时不重复起；已下好时直接报 done）。"""
    if small_model_path().is_dir():
        return {"started": False, "done": True, "note": "小模型已经下好了。"}
    with _DOWNLOAD.lock:
        if _DOWNLOAD.running:
            return {"started": False, "done": False, "note": "正在下载，别重复点。"}
        _DOWNLOAD.running = True
        _DOWNLOAD.downloaded = 0
        _DOWNLOAD.total = SMALL_MODEL_BYTES
        _DOWNLOAD.error = ""
        _DOWNLOAD.done = False
    thread = threading.Thread(target=_download_small_model, daemon=True, name="wake-model-download")
    thread.start()
    return {"started": True, "done": False, "note": "开始下载小模型（约 44MB，需联网一次）。"}


def wake_status() -> Dict[str, Any]:
    """唤醒词真实状态：KWS 引擎/模型/监听各有没有，监听没开就是没开。"""
    with _DOWNLOAD.lock:
        download = {
            "running": _DOWNLOAD.running,
            "downloaded_bytes": _DOWNLOAD.downloaded,
            "total_bytes": _DOWNLOAD.total,
            "done": _DOWNLOAD.done,
            "error": _DOWNLOAD.error,
        }
    small_present = small_model_path().is_dir()
    kws_present = kws_model_present()
    sherpa = _sherpa_available()
    kws_available = bool(sherpa and kws_present)
    with _LISTENER_LOCK:
        events = [dict(e) for e in _WAKE_EVENTS]
        last_wake_at = _LAST_WAKE_AT
        listening_error = _LISTENER_ERROR
    if kws_available:
        note = (
            "本地唤醒词“贾维斯”已接通（sherpa-onnx 关键词模型，检出 23/24、"
            "误唤 0/31）。正样本是合成语音，不是真人声——真人声还没验证，"
            "请在设置里说几遍“贾维斯”点“验证一下”当场测；吵环境里（10dB "
            "噪音）检出会掉，介意的话别在吵的地方用。"
        )
        install_hint = ""
    elif not sherpa:
        note = (
            "本地唤醒还没法用：没装 sherpa-onnx（pip install sherpa-onnx，"
            "一键安装包自带这个引擎；源码自装时 pip install sherpa-onnx），"
            "装好再下载 KWS 模型。"
        )
        install_hint = "先装 sherpa-onnx，再在这里下载 KWS 唤醒模型（约 32MB）。"
    elif not kws_present:
        note = (
            "本地唤醒还没接通：KWS 唤醒模型还没下（约 32MB，sherpa-onnx "
            "kws-models 发布页，Apache-2.0）。点“下载唤醒模型”，下完验"
            "大小与 SHA-256 再用，之后离线。"
        )
        install_hint = "点“下载唤醒模型”（约 32MB，联网一次）。"
    else:  # pragma: no cover - 防御分支：sherpa 与模型都在却不可用
        note = "本地唤醒状态异常：引擎与模型都在，但 spots 加载失败，详见监听报错。"
        install_hint = ""
    return {
        "wake_word": WAKE_WORD,
        "engine": "sherpa-onnx-kws" if kws_available else None,
        "vosk_installed": _vosk_installed(),
        "model_present": small_present,
        "model_name": SMALL_MODEL_NAME,
        "model_dir": str(wake_model_dir()),
        "mic_available": _mic_available(),
        "listening": is_listening(),
        "listening_enabled": get_listening_enabled(),
        "listening_error": listening_error,
        "last_wake_at": last_wake_at,
        "events": events,
        "download": download,
        "available": kws_available,
        "install_hint": install_hint,
        "note": note,
        # KWS 专用模型（本轮接通的这条路；Vosk 那组字段上面留着，只作兼容）。
        "kws_engine": "sherpa-onnx" if sherpa else None,
        "kws_model_present": kws_present,
        "kws_model_name": KWS_MODEL_NAME,
        "kws_model_size_mb": round(KWS_TARBALL_BYTES / (1024 * 1024), 1),
        "kws_model_source": KWS_MODEL_SOURCE,
        "kws_available": kws_available,
        "kws_download": kws_download_status(),
        "validation": dict(KWS_VALIDATION),
    }


# --------------------------------------------------------------------------- #
# KWS 模型：按需下载（打开唤醒词时下一次，不是装机时下）+ 校验 + 解压
# --------------------------------------------------------------------------- #


def kws_model_dir() -> Path:
    """KWS 模型目录：默认 <数据目录>/voice/wake/<模型名>；测试可用环境变量指到已解压的目录。"""
    env = (os.environ.get(WAKE_KWS_MODEL_ENV) or "").strip()
    if env:
        return Path(env)
    return wake_model_dir() / KWS_MODEL_NAME


def kws_model_present() -> bool:
    """模型目录里 4 个文件齐了才算“下好了”（关键词文件解压后顺手写，不算在内）。"""
    d = kws_model_dir()
    return all((d / name).is_file() for name in KWS_MODEL_FILES)


def kws_keywords_path() -> Path:
    return kws_model_dir() / "keywords.txt"


def _ensure_keywords_file(model_dir: Optional[Path] = None) -> None:
    """关键词文件写成“贾维斯”那一行（模型自带的是示例关键词，必须覆盖；
    丢了也能补，不用重下模型）。"""
    path = (model_dir or kws_model_dir()) / "keywords.txt"
    try:
        current = path.read_text(encoding="utf-8").strip() if path.is_file() else ""
    except OSError:
        current = ""
    if current == KWS_KEYWORDS:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(KWS_KEYWORDS + "\n", encoding="utf-8")


def _sherpa_available() -> bool:
    try:
        import sherpa_onnx  # noqa: F401
    except Exception:
        return False
    return True


def _require_numpy() -> Any:
    if np is None:
        raise EngineUnavailableError(
            "唤醒词需要 numpy（onnxruntime 的依赖），本机没有装。",
            "pip install numpy（或重装 sherpa-onnx，它会一起装上）。",
        )
    return np


def _require_kws_available() -> None:
    """没引擎或没模型时报 503（不编结果）：detail 给用户看，install_hint 说下一步。"""
    if not _sherpa_available():
        raise EngineUnavailableError(
            "本地唤醒还没法用：没装 sherpa-onnx。",
            "先装 sherpa-onnx（语音 extra 里有），再下载 KWS 唤醒模型。",
        )
    if not kws_model_present():
        raise EngineUnavailableError(
            "本地唤醒还没接通：KWS 唤醒模型还没下。",
            "在语音设置里点“下载唤醒模型”（约 32MB，联网一次）。",
        )


def verify_kws_tarball(path: Path) -> None:
    """验下载包：大小与 SHA-256 都对上才放行；不对就报原因，不解压。"""
    path = Path(path)
    size = path.stat().st_size
    if size != KWS_TARBALL_BYTES:
        raise ValueError(
            f"唤醒模型包大小不对（下到 {size} 字节，应为 {KWS_TARBALL_BYTES} 字节），请重试。"
        )
    hasher = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            chunk = f.read(1024 * 256)
            if not chunk:
                break
            hasher.update(chunk)
    if hasher.hexdigest() != KWS_TARBALL_SHA256:
        raise ValueError("唤醒模型包校验没通过（SHA-256 对不上），请重试。")


def extract_kws_tarball(tarball: Path, dest_dir: Path) -> Path:
    """解压唤醒模型包到 dest_dir，写好关键词文件，返回模型目录。"""
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    with tarfile.open(tarball, "r:bz2") as tf:
        tf.extractall(dest_dir)
    model_dir = dest_dir / KWS_MODEL_NAME
    if not all((model_dir / name).is_file() for name in KWS_MODEL_FILES):
        raise ValueError("唤醒模型包解压后文件不全（缺编码器/解码器/合并器/词表），请重试。")
    _ensure_keywords_file(model_dir)
    return model_dir


class _KwsDownloadState:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.running = False
        self.downloaded = 0
        self.total = KWS_TARBALL_BYTES
        self.error = ""
        self.done = False


_KWS_DOWNLOAD = _KwsDownloadState()


def _download_kws_model() -> None:
    """后台下载 KWS 模型包（约 32MB），验大小与 SHA-256 再解压落盘。"""
    target_dir = wake_model_dir()
    target_dir.mkdir(parents=True, exist_ok=True)
    tarball = target_dir / (KWS_TARBALL_NAME + ".downloading")
    try:
        request = urllib.request.Request(KWS_TARBALL_URL, headers={"User-Agent": "Jarvis/wake-kws"})
        with urllib.request.urlopen(request, timeout=120) as resp:
            total = int(resp.headers.get("Content-Length") or KWS_TARBALL_BYTES)
            with _KWS_DOWNLOAD.lock:
                _KWS_DOWNLOAD.total = total
            with open(tarball, "wb") as out:
                downloaded = 0
                while True:
                    chunk = resp.read(1024 * 256)
                    if not chunk:
                        break
                    out.write(chunk)
                    downloaded += len(chunk)
                    with _KWS_DOWNLOAD.lock:
                        _KWS_DOWNLOAD.downloaded = downloaded
        verify_kws_tarball(tarball)
        extract_kws_tarball(tarball, target_dir)
        try:
            tarball.unlink()
        except OSError:
            pass
        with _KWS_DOWNLOAD.lock:
            _KWS_DOWNLOAD.done = True
            _KWS_DOWNLOAD.running = False
    except Exception as exc:  # noqa: BLE001 - 下载失败要如实报给界面
        logger.warning("KWS 唤醒模型下载失败：%s", exc)
        try:
            tarball.unlink(missing_ok=True)
        except OSError:
            pass
        with _KWS_DOWNLOAD.lock:
            _KWS_DOWNLOAD.error = str(exc)
            _KWS_DOWNLOAD.running = False


def start_kws_model_download() -> Dict[str, Any]:
    """开始下载 KWS 模型（已下好直接报 done；下载中不重复开）。"""
    if kws_model_present():
        return {"started": False, "done": True, "downloaded": KWS_TARBALL_BYTES,
                "total": KWS_TARBALL_BYTES, "error": ""}
    with _KWS_DOWNLOAD.lock:
        if _KWS_DOWNLOAD.running:
            return {"started": False, "done": False,
                    "downloaded": _KWS_DOWNLOAD.downloaded, "total": _KWS_DOWNLOAD.total,
                    "error": ""}
        _KWS_DOWNLOAD.running = True
        _KWS_DOWNLOAD.downloaded = 0
        _KWS_DOWNLOAD.error = ""
        _KWS_DOWNLOAD.done = False
    thread = threading.Thread(target=_download_kws_model, name="wake-kws-download", daemon=True)
    thread.start()
    return {"started": True, "done": False, "downloaded": 0,
            "total": KWS_TARBALL_BYTES, "error": ""}


def kws_download_status() -> Dict[str, Any]:
    with _KWS_DOWNLOAD.lock:
        state = {
            "started": _KWS_DOWNLOAD.running or _KWS_DOWNLOAD.done,
            "done": bool(_KWS_DOWNLOAD.done or kws_model_present()),
            "downloaded": _KWS_DOWNLOAD.downloaded,
            "total": _KWS_DOWNLOAD.total,
            "error": _KWS_DOWNLOAD.error,
        }
    if kws_model_present():
        state["done"] = True
        state["downloaded"] = KWS_TARBALL_BYTES
    return state


# --------------------------------------------------------------------------- #
# spots 检出（文件 / 采样 / 自检录音）
# --------------------------------------------------------------------------- #

_SPOTTER_LOCK = threading.Lock()
_SPOTTER: Any = None


def _get_spotter() -> Any:
    """懒加载 KeywordSpotter（fp32 检查点；没引擎/没模型时报 503，不编结果）。"""
    global _SPOTTER
    with _SPOTTER_LOCK:
        if _SPOTTER is not None:
            return _SPOTTER
        _require_kws_available()
        import sherpa_onnx

        d = kws_model_dir()
        _ensure_keywords_file()
        _SPOTTER = sherpa_onnx.KeywordSpotter(
            tokens=str(d / KWS_TOKENS_NAME),
            encoder=str(d / KWS_ENCODER_NAME),
            decoder=str(d / KWS_DECODER_NAME),
            joiner=str(d / KWS_JOINER_NAME),
            num_threads=2,
            keywords_file=str(kws_keywords_path()),
            keywords_score=KWS_SCORE,
            keywords_threshold=KWS_THRESHOLD,
            provider="cpu",
        )
        return _SPOTTER


def _reset_spotter_for_tests() -> None:
    """测试用：清掉缓存的 spots 器（换模型目录后重载）。"""
    global _SPOTTER
    with _SPOTTER_LOCK:
        _SPOTTER = None


def _spot_stream(spotter: Any, samples: Any, sample_rate: int) -> bool:
    """一段 16k 采样里有没有“贾维斯”：流式喂入 + 0.66 秒尾部静音，检出即停。"""
    npy = _require_numpy()
    audio = npy.ascontiguousarray(samples, dtype=npy.float32)
    if len(audio) == 0:
        return False
    stream = spotter.create_stream()
    stream.accept_waveform(sample_rate, audio)
    tail = npy.zeros(int(0.66 * sample_rate), dtype=npy.float32)
    stream.accept_waveform(sample_rate, tail)
    stream.input_finished()
    while spotter.is_ready(stream):
        spotter.decode_stream(stream)
        if spotter.get_result(stream) != "":
            return True
    return False


def spot_samples(samples: Any, sample_rate: int = 16000) -> bool:
    """采样数组里有没有“贾维斯”（调用方保证已是单声道 float32，采样率随传随用）。"""
    if samples is None or len(samples) == 0:
        return False
    return _spot_stream(_get_spotter(), samples, sample_rate)


def _decode_wav_bytes(data: bytes) -> Any:
    """wav 字节 -> 16k 单声道 float32（标准库解；非 16k 线性重采样，与声纹同款）。"""
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
        target_len = max(1, int(round(len(audio) * 16000 / rate)))
        idx = npy.linspace(0, len(audio) - 1, target_len)
        audio = npy.interp(idx, npy.arange(len(audio)), audio).astype(npy.float32)
    return npy.ascontiguousarray(audio, dtype=npy.float32)


def spot_wav_file(path: Path) -> bool:
    """一个 wav 文件里有没有“贾维斯”（测试与自检用；非 wav 直接报原因）。"""
    path = Path(path)
    if path.suffix.lower() != ".wav":
        raise ValueError("自检只认 wav 文件，请传 wav 格式的录音。")
    return spot_samples(_decode_wav_bytes(path.read_bytes()), 16000)


def check_recording(data: bytes, filename: str = "recording.wav") -> Dict[str, Any]:
    """设置里“验证一下”：一段录音里有没有“贾维斯”，当场给结果。

    录音太短/太长/太轻直接报原因；没引擎/没模型报 503（由调用方转）。
    返回 detected + 时长 + 一句人话说明（不报分数：spots 只给有/没有）。
    """
    _require_kws_available()
    npy = _require_numpy()
    if not data:
        raise ValueError("录音是空的，请重新录一段再发。")
    if len(data) > CHECK_MAX_AUDIO_BYTES:
        raise ValueError(f"录音太大（{len(data) // (1024 * 1024)}MB），单条上限 25MB，请分段发。")
    suffix = Path(filename or "recording.wav").suffix.lower()
    if suffix == ".wav":
        samples = _decode_wav_bytes(data)
    elif suffix in {".mp3", ".m4a", ".ogg", ".webm", ".flac", ".aac", ".opus"}:
        samples = _decode_with_ffmpeg(data, suffix)
    else:
        raise ValueError(f"不支持的录音格式：{suffix or '（没写后缀）'}；请传 wav，或装好 ffmpeg 后再传 mp3 / webm。")
    seconds = len(samples) / 16000
    if seconds < CHECK_MIN_SECONDS:
        raise ValueError(f"录音太短（{seconds:.1f} 秒），至少录 {CHECK_MIN_SECONDS} 秒再试。")
    if seconds > CHECK_MAX_SECONDS:
        raise ValueError(f"录音太长（{seconds:.0f} 秒），单句上限 30 秒，请分段录。")
    rms = float(npy.sqrt(npy.mean(npy.asarray(samples, dtype=npy.float64) ** 2))) if len(samples) else 0.0
    if rms < CHECK_MIN_RMS:
        raise ValueError("这段录音太轻、几乎没声音，请靠近麦克风再录一段。")
    detected = bool(spot_samples(samples, 16000))
    return {
        "detected": detected,
        "duration_seconds": round(seconds, 2),
        "detail": (
            "听到了“贾维斯”✓" if detected
            else "这段里没听到“贾维斯”：请对着麦克风清楚地说一遍“贾维斯”再试。"
        ),
    }


def _decode_with_ffmpeg(data: bytes, suffix: str) -> Any:
    """非 wav 录音走本机 ffmpeg 解成 16k 单声道（没有 ffmpeg 就老实说）。"""
    import shutil
    import subprocess
    import tempfile

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
    return npy.frombuffer(proc.stdout, dtype="<i2").astype(npy.float32) / 32768.0


# --------------------------------------------------------------------------- #
# 常驻监听：默认不开，设置里能关；开起来是真监听（麦克风 → spots → 记事件）
# --------------------------------------------------------------------------- #


def _wake_settings_path() -> Path:
    return wake_model_dir() / "wake.json"


def _load_wake_settings() -> Dict[str, Any]:
    try:
        path = _wake_settings_path()
        if path.is_file():
            loaded = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                return loaded
    except (OSError, ValueError):
        pass
    return {}


def _save_wake_settings(data: Dict[str, Any]) -> None:
    path = _wake_settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def get_listening_enabled() -> bool:
    """常驻监听开关（默认关；只读设置文件，不启动监听）。"""
    return bool(_load_wake_settings().get("listening_enabled", False))


def is_listening() -> bool:
    """监听线程是不是真在跑（开关开了 ≠ 在听：没麦克风时线程起不来）。"""
    with _LISTENER_LOCK:
        thread = _LISTENER_THREAD
        return thread is not None and thread.is_alive()


_LISTENER_LOCK = threading.Lock()
_LISTENER_THREAD: Optional[threading.Thread] = None
_LISTENER_STOP = threading.Event()
_LISTENER_ERROR = ""
_WAKE_EVENTS: List[Dict[str, Any]] = []
_LAST_WAKE_AT: Optional[str] = None


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _record_wake(source_label: str) -> None:
    """记一笔唤醒事件（上次时间 + 列表，面板可见；只记时间与来源，不存音频）。"""
    global _LAST_WAKE_AT
    event = {"at": _now_iso(), "source": source_label}
    with _LISTENER_LOCK:
        _LAST_WAKE_AT = event["at"]
        _WAKE_EVENTS.append(event)
        del _WAKE_EVENTS[: -MAX_WAKE_EVENTS]


def _mic_chunks(stop: threading.Event) -> Iterable[Any]:
    """麦克风采集：sounddevice 16k 单声道，每 0.1 秒一块；没装组件/没设备直接抛错。"""
    try:
        import sounddevice as sd
    except Exception as exc:
        raise EngineUnavailableError(
            "常驻监听开不起来：本机没装麦克风采集组件 sounddevice。",
            "装好 sounddevice（语音 extra 里有），再打开常驻监听。",
        ) from exc
    npy = _require_numpy()
    audio_queue: "queue.Queue[Any]" = queue.Queue(maxsize=50)

    def _callback(indata: Any, frames: int, time_info: Any, status: Any) -> None:
        if status:
            logger.warning("麦克风采集状态：%s", status)
        try:
            audio_queue.put_nowait(npy.array(indata[:, 0], dtype=npy.float32, copy=True))
        except queue.Full:
            pass

    with sd.InputStream(
        samplerate=16000, channels=1, dtype="float32",
        blocksize=1600, callback=_callback,
    ):
        while not stop.is_set():
            try:
                yield audio_queue.get(timeout=0.2)
            except queue.Empty:
                continue


def _listener_loop(audio_source: Optional[Iterable[Any]], source_label: str) -> None:
    """监听线程：音频块 → spots 流；检出一次记一笔事件，清流继续听。"""
    global _LISTENER_ERROR
    try:
        spotter = _get_spotter()
    except Exception as exc:  # noqa: BLE001 - 引擎/模型问题记进状态，不炸网关
        with _LISTENER_LOCK:
            _LISTENER_ERROR = str(exc)
        return
    stream = spotter.create_stream()
    try:
        source = audio_source if audio_source is not None else _mic_chunks(_LISTENER_STOP)
        for chunk in source:
            if _LISTENER_STOP.is_set():
                break
            if chunk is None or len(chunk) == 0:
                continue
            stream.accept_waveform(16000, chunk)
            while spotter.is_ready(stream):
                spotter.decode_stream(stream)
                if spotter.get_result(stream) != "":
                    _record_wake(source_label)
                    spotter.reset_stream(stream)
                    break
    except Exception as exc:  # noqa: BLE001 - 麦克风断了等运行时错误记进状态
        with _LISTENER_LOCK:
            _LISTENER_ERROR = str(exc) or repr(exc)
        logger.warning("常驻监听停了：%s", exc)


def start_listener(
    audio_source: Optional[Iterable[Any]] = None, source_label: str = "mic"
) -> bool:
    """启动监听线程（已在跑时不重复起）。audio_source 只给测试注入用。"""
    global _LISTENER_THREAD
    with _LISTENER_LOCK:
        if _LISTENER_THREAD is not None and _LISTENER_THREAD.is_alive():
            return False
        _LISTENER_ERROR = ""
        _LISTENER_STOP.clear()
        thread = threading.Thread(
            target=_listener_loop, args=(audio_source, source_label),
            name="wake-listener", daemon=True,
        )
        _LISTENER_THREAD = thread
        thread.start()
        return True


def stop_listener() -> bool:
    """停掉监听线程（没在跑时返回 False）。"""
    global _LISTENER_THREAD
    with _LISTENER_LOCK:
        thread = _LISTENER_THREAD
        _LISTENER_THREAD = None
    if thread is None:
        return False
    _LISTENER_STOP.set()
    thread.join(timeout=2.0)
    return True


def set_listening_enabled(enabled: bool) -> Dict[str, Any]:
    """常驻监听开关：开要先有引擎与模型（没有就拒绝并说清缺什么，不瞎开）；
    关就停线程。返回开关实际状态 + 报错（开起来但麦克风起不来时 error 里写原因）。"""
    enabled = bool(enabled)
    if enabled:
        try:
            _require_kws_available()
        except EngineUnavailableError as exc:
            return {"enabled": False, "listening": False, "error": exc.detail}
        settings = _load_wake_settings()
        settings["listening_enabled"] = True
        _save_wake_settings(settings)
        start_listener()
        return {
            "enabled": True,
            "listening": is_listening(),
            "error": _LISTENER_ERROR,
        }
    settings = _load_wake_settings()
    settings["listening_enabled"] = False
    _save_wake_settings(settings)
    stop_listener()
    return {"enabled": False, "listening": False, "error": ""}
