"""语音转文字（STT）：faster-whisper 首选，whisper.cpp 作后路。

模型文件**不在代码仓里**：默认模型 ``small``（中文够用，约 466MB），
第一次转写时 faster-whisper 自动从 Hugging Face 下载到
``<数据目录>/voice/models/``，之后完全离线。嫌大可用
``LIUHAO_STT_MODEL=tiny``（约 75MB，中文准确率明显下降）或
``base``（约 145MB）；模型名就是 faster-whisper / Whisper 官方的
模型名（tiny/base/small/medium/large-v3）。

whisper.cpp 后路：本机装好 ``whisper-cli``（或旧名 ``main``）且
``LIUHAO_WHISPER_CPP_MODEL`` 指向 ``.bin`` 模型文件时才可用；
faster-whisper 可用时永远不走这条路。
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from src.common.engines import EngineUnavailableError, data_dir
from src.voice.settings import vad_min_silence_ms

#: 单条录音上限：25MB，约 25 分钟 16kHz 单声道 wav；再长请分段发。
MAX_AUDIO_BYTES = 25 * 1024 * 1024

#: 接受的录音后缀（浏览器 MediaRecorder 常出 webm/opus，手机常出 m4a）。
AUDIO_SUFFIXES = frozenset(
    {".wav", ".mp3", ".m4a", ".ogg", ".webm", ".flac", ".aac", ".opus"}
)

#: 默认模型：small。tiny 省空间但中文错得多；medium/large 更准但更慢。
DEFAULT_MODEL = "small"

_FASTER_WHISPER_HINT = (
    "语音识别还没装好：先装 faster-whisper（pip install faster-whisper），"
    "首次使用会自动下载 small 模型（约 466MB，需联网一次，之后离线可用）；"
    "也可以改用更小的模型：设置环境变量 LIUHAO_STT_MODEL=tiny。"
)
_WHISPER_CPP_HINT = (
    "语音识别还没装好：装 faster-whisper（pip install faster-whisper），"
    "或在本机装 whisper.cpp 并把 LIUHAO_WHISPER_CPP_MODEL 指向模型文件。"
)


@dataclass(frozen=True)
class TranscribeResult:
    """一次转写的结果：只含模型真实输出，不补、不猜。"""

    text: str
    language: str
    engine: str  # "faster-whisper" | "whisper.cpp"
    model: str


def stt_model_name() -> str:
    return (os.environ.get("LIUHAO_STT_MODEL") or DEFAULT_MODEL).strip() or DEFAULT_MODEL


def stt_model_dir() -> Path:
    return data_dir() / "voice" / "models"


def _check_audio(data: bytes, filename: str) -> str:
    suffix = Path(filename or "").suffix.lower()
    if suffix and suffix not in AUDIO_SUFFIXES:
        raise ValueError(
            f"不支持的录音格式：{suffix}；支持 "
            + " / ".join(sorted(AUDIO_SUFFIXES))
        )
    if len(data) == 0:
        raise ValueError("录音是空的，请重新录一段再发。")
    if len(data) > MAX_AUDIO_BYTES:
        raise ValueError(
            f"录音太大（{len(data) // (1024 * 1024)}MB），单条上限 25MB，请分段发。"
        )
    return suffix or ".webm"


def _faster_whisper_available() -> bool:
    try:
        import faster_whisper  # noqa: F401
    except Exception:
        return False
    return True


def _whisper_cpp_binary() -> Optional[str]:
    for name in ("whisper-cli", "whisper-cpp", "main"):
        path = shutil.which(name)
        if path:
            return path
    return None


def stt_status() -> dict:
    """语音识别可用状态（给 GET /v1/voice/status 用，不触发下载）。"""
    faster = _faster_whisper_available()
    cpp = _whisper_cpp_binary()
    engine: Optional[str] = None
    if faster:
        engine = "faster-whisper"
    elif cpp:
        engine = "whisper.cpp"
    return {
        "available": engine is not None,
        "engine": engine,
        "model": stt_model_name(),
        "model_dir": str(stt_model_dir()),
        "faster_whisper_installed": faster,
        "whisper_cpp_binary": cpp,
        "install_hint": "" if engine else _FASTER_WHISPER_HINT,
        "note": "离线本机识别，录音不送上云；模型首次使用需联网下载一次。",
    }


def transcribe_audio(data: bytes, filename: str, language: str = "zh") -> TranscribeResult:
    """录音字节 -> 文字。引擎缺失时抛 :class:`EngineUnavailableError`。"""
    suffix = _check_audio(data, filename)
    lang = (language or "zh").strip() or "zh"
    if _faster_whisper_available():
        return _transcribe_faster_whisper(data, suffix, lang)
    cpp = _whisper_cpp_binary()
    if cpp:
        return _transcribe_whisper_cpp(data, suffix, lang, cpp)
    raise EngineUnavailableError("语音识别还没装好，无法把录音转成文字。", _WHISPER_CPP_HINT)


def _transcribe_faster_whisper(data: bytes, suffix: str, language: str) -> TranscribeResult:
    from faster_whisper import WhisperModel

    model_name = stt_model_name()
    model_dir = stt_model_dir()
    model_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(data)
        tmp_path = tmp.name
    try:
        # CPU + int8：普通 Windows 笔记本能跑；有显卡的用户以后可改 device="cuda"。
        model = WhisperModel(
            model_name, device="cpu", compute_type="int8", download_root=str(model_dir)
        )
        try:
            # 防静音幻觉（7-5 §5.1）：VAD 过滤 + 不以前文为条件，否则
            # Whisper 会在静音段编出“谢谢”类的话；min_silence 取设置值
            #（默认 800ms，设置里 400/600/800/1200 四档按真机实测调）。
            segments, info = model.transcribe(
                tmp_path,
                language=language,
                vad_filter=True,
                vad_parameters={"min_silence_duration_ms": vad_min_silence_ms(), "speech_pad_ms": 400},
                condition_on_previous_text=False,
            )
            text = "".join(seg.text for seg in segments).strip()
        except TypeError as exc:
            # faster-whisper 1.2.1 的文件解码走 PyAV；实测 av 19 会因
            # ``metadata_errors`` 参数被删而抛 TypeError。wav 可以不走 av：
            # 标准库读 PCM、重采样到 16k 后直接把数组交给模型。
            if "metadata_errors" not in str(exc):
                raise
            if suffix != ".wav":
                raise RuntimeError(
                    "录音解码失败：本机 PyAV 版本与 faster-whisper 不兼容，"
                    "且这段录音不是 wav（只有 wav 能绕开 PyAV 解码）。请把"
                    " PyAV 降到 14.x，或先把录音转成 wav 再发。"
                ) from exc
            text, lang = _transcribe_wav_array(model, tmp_path, language)
            return TranscribeResult(
                text=text, language=lang, engine="faster-whisper", model=model_name,
            )
        return TranscribeResult(
            text=text,
            language=info.language or language,
            engine="faster-whisper",
            model=model_name,
        )
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


def _transcribe_wav_array(model, wav_path: str, language: str) -> tuple:
    """wav 文件 -> (文字, 语言)：标准库解码 + 线性重采样到 16k，绕开 PyAV。"""
    import wave

    import numpy as np

    with wave.open(wav_path, "rb") as wav:
        sample_rate = wav.getframerate()
        channels = wav.getnchannels()
        width = wav.getsampwidth()
        frames = wav.readframes(wav.getnframes())
    if width != 2:
        raise RuntimeError("只支持 16-bit PCM wav 的备用解码。")
    pcm = np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0
    if channels > 1:
        pcm = pcm.reshape(-1, channels).mean(axis=1)
    if sample_rate != 16000:
        target_len = int(len(pcm) * 16000 / sample_rate)
        pcm = np.interp(
            np.linspace(0, len(pcm), target_len), np.arange(len(pcm)), pcm
        ).astype(np.float32)
    segments, info = model.transcribe(
        pcm,
        language=language,
        vad_filter=True,
        vad_parameters={"min_silence_duration_ms": vad_min_silence_ms(), "speech_pad_ms": 400},
        condition_on_previous_text=False,
    )
    text = "".join(seg.text for seg in segments).strip()
    return text, info.language or language


def _transcribe_whisper_cpp(
    data: bytes, suffix: str, language: str, binary: str
) -> TranscribeResult:
    model_path = (os.environ.get("LIUHAO_WHISPER_CPP_MODEL") or "").strip()
    if not model_path or not Path(model_path).is_file():
        raise EngineUnavailableError(
            "找到 whisper.cpp，但没找到模型文件。",
            "把 LIUHAO_WHISPER_CPP_MODEL 指向 whisper.cpp 的 .bin 模型文件"
            "（例如 ggml-small.bin，约 466MB）。",
        )
    with tempfile.TemporaryDirectory() as tmpdir:
        audio_path = Path(tmpdir) / f"input{suffix}"
        audio_path.write_bytes(data)
        out_prefix = str(Path(tmpdir) / "out")
        proc = subprocess.run(
            [binary, "-m", model_path, "-f", str(audio_path), "-l", language,
             "-otxt", "-of", out_prefix, "--no-timestamps"],
            capture_output=True, text=True, timeout=600,
        )
        if proc.returncode != 0:
            raise RuntimeError(f"whisper.cpp 转写失败：{(proc.stderr or '').strip()[-500:]}")
        txt_path = Path(out_prefix + ".txt")
        text = txt_path.read_text(encoding="utf-8").strip() if txt_path.is_file() else ""
        return TranscribeResult(
            text=text, language=language, engine="whisper.cpp",
            model=Path(model_path).name,
        )
