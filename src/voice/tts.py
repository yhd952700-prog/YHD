"""文字转语音（TTS）：Kokoro-82M（Apache-2.0，代码与权重同许可）。

实现走 ``kokoro-onnx``（轻量，不拉 torch）；torch 版 ``kokoro`` 作后路。
模型文件**不在代码仓里**：第一次合成前，把下面两个文件下到
``<数据目录>/voice/kokoro/``（来源：kokoro-onnx 的模型文件发布页
https://github.com/thewh1teagle/kokoro-onnx/releases/tag/model-files-v1.1；
注意 hexgrad/Kokoro-82M 主仓只有 ``.pth`` 权重，没有 ``.onnx`` 文件）：

* ``kokoro-v1.1-zh.onnx``（约 310MB，FP32；FP16 约 155MB）
* ``voices-v1.1-zh.bin``（声音数据，约 51MB）

中文声音（实测为准，见下）：v1.1-zh 的声音是编号式的（zf_001…zf_099
女声、zm_009…zm_100 男声），默认 ``zf_001``（女声）；v1.0 的
``voices-v1.0.bin`` 才是起名字的 8 个中文声音（zf_xiaobei /
zf_xiaoni / zf_xiaoxiao / zf_xiaoyi / zm_yunjian / zm_yunxi /
zm_yunxia / zm_yunyang）。本模块优先用 v1.1-zh（中文更好），
``GET /v1/voice/status`` 返回的 ``voices_zh`` 永远是本机模型文件里
真实有的声音名，不写死。

许可提醒：中文发音要走 ``misaki[zh]``，且 kokoro-onnx 的中文音素转换
依赖系统 ``espeak-ng``（GPL-3.0）——实测：没装 espeak-ng 时中文合成
直接报 ``language "zh-cn" is not supported by the espeak backend``。
espeak-ng **不随安装包分发**，用户自行安装；缺失时中文合成明确报错，
不静默出声。英文不需要 espeak-ng。
"""

from __future__ import annotations

import io
import os
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from src.common.engines import EngineUnavailableError, data_dir

#: 单次合成上限：2000 字；再长请分段，前端按句切分后逐段播。
MAX_TEXT_CHARS = 2000

#: 按句切分的终结标点（中文 + 英文 + 换行）。逐句合成时用：
#: LLM 流每攒够一句就合成一句、播一句，不用等整段回完。
_SENTENCE_END = ("。", "？", "！", "；", "…", ".", "?", "!", ";", "\n")


def split_sentences(text: str) -> list:
    """把一段回复切成一句一句（保留句末标点）。

    不拆的情况：没有终结标点的一整段（返回原样一句）；切完丢掉纯空白
    的段。省略号“……”按一个终结符算，不切成三句。
    """
    sentences: list = []
    start = 0
    index = 0
    while index < len(text):
        char = text[index]
        if char == "…" and text[index:index + 2] == "……":
            end = index + 2
            piece = text[start:end].strip()
            if piece:
                sentences.append(piece)
            start = end
            index = end
            continue
        if char in _SENTENCE_END:
            piece = text[start:index + 1].strip()
            if piece:
                sentences.append(piece)
            start = index + 1
        index += 1
    tail = text[start:].strip()
    if tail:
        sentences.append(tail)
    return sentences

#: v1.0 模型文件的中文声音（起名字的 8 个；v1.1-zh 是编号式，见下）。
#: zf=女声，zm=男声。
ZH_VOICES_V10 = (
    "zf_xiaobei", "zf_xiaoni", "zf_xiaoxiao", "zf_xiaoyi",
    "zm_yunjian", "zm_yunxi", "zm_yunxia", "zm_yunyang",
)
#: v1.1-zh 默认声音（女声；实测 voices-v1.1-zh.bin 里第一个女声编号）。
DEFAULT_VOICE = "zf_001"
#: 兼容旧名：torch 版 kokoro 的默认声音仍是 v1.0 的名字。
DEFAULT_VOICE_V10 = "zf_xiaoxiao"

#: 向后兼容：默认声音列表在 v1.1-zh 下由模型文件实测，见 ``tts_status``。
ZH_VOICES = ZH_VOICES_V10

#: 两套 ONNX 模型文件（优先 v1.1-zh，中文更好；都没有时退到 v1.0）。
_ONNX_MODEL_SETS = (
    ("kokoro-v1.1-zh.onnx", "voices-v1.1-zh.bin", DEFAULT_VOICE),
    ("kokoro-v1.0.onnx", "voices-v1.0.bin", DEFAULT_VOICE_V10),
)

_KOKORO_HINT = (
    "语音合成还没装好：先装 kokoro-onnx 与 misaki[zh]"
    "（pip install kokoro-onnx \"misaki[zh]\"），再把 Kokoro-82M 的"
    "中文模型文件 kokoro-v1.1-zh.onnx 与 voices-v1.1-zh.bin 下到"
    " <数据目录>/voice/kokoro/（见 docs/voice-ocr-calendar-mobile.md）；"
    "中文发音需要系统 espeak-ng（用户自行安装，不随包分发）。"
)


@dataclass(frozen=True)
class SynthResult:
    wav_bytes: bytes
    sample_rate: int
    voice: str
    engine: str  # "kokoro-onnx" | "kokoro"


def kokoro_dir() -> Path:
    return data_dir() / "voice" / "kokoro"


def _detect_onnx_model() -> Optional[tuple]:
    """本机已有的第一套 ONNX 模型：(模型文件, 声音文件, 默认声音)。

    声音名不写死：从声音文件里实测读出（v1.1-zh 是 zf_001 这类编号，
    v1.0 是 zf_xiaoxiao 这类名字），读不到就回落到该套的默认声音。
    """
    for model_name, voices_name, default_voice in _ONNX_MODEL_SETS:
        model = kokoro_dir() / model_name
        voices = kokoro_dir() / voices_name
        if model.is_file() and voices.is_file():
            return (model, voices, default_voice)
    return None


def _voices_in_file(voices_path: Path) -> list:
    """声音文件里真实有的声音名（只读键名，不加载音频数据）。"""
    try:
        import numpy as np

        with np.load(str(voices_path), allow_pickle=False) as data:
            return sorted(str(name) for name in data.files)
    except Exception:
        return []


def tts_status() -> dict:
    """语音合成可用状态（给 GET /v1/voice/status 用，不触发下载）。"""
    detected = _detect_onnx_model()
    try:
        import kokoro_onnx  # noqa: F401
        onnx_installed = True
    except Exception:
        onnx_installed = False
    try:
        import kokoro  # noqa: F401
        torch_installed = True
    except Exception:
        torch_installed = False
    engine: Optional[str] = None
    voices: list = []
    voice = DEFAULT_VOICE
    if onnx_installed and detected is not None:
        engine = "kokoro-onnx"
        voices = _voices_in_file(detected[1]) or [detected[2]]
        voice = detected[2]
    elif torch_installed:
        engine = "kokoro"
        voices = list(ZH_VOICES_V10)
        voice = DEFAULT_VOICE_V10
    return {
        "available": engine is not None,
        "engine": engine,
        "voice": voice,
        "voices_zh": voices,
        "model_dir": str(kokoro_dir()),
        "model_files_present": detected is not None,
        "kokoro_onnx_installed": onnx_installed,
        "kokoro_torch_installed": torch_installed,
        "install_hint": "" if engine else _KOKORO_HINT,
        "note": "离线本机合成，不送上云；中文发音需系统 espeak-ng（GPL-3.0，用户自行安装，不随包分发）。",
    }


def _check_text(text: str, voice: str, allowed_voices: list) -> str:
    clean = (text or "").strip()
    if not clean:
        raise ValueError("要读的文字是空的。")
    if len(clean) > MAX_TEXT_CHARS:
        raise ValueError(f"一次最多读 {MAX_TEXT_CHARS} 字（当前 {len(clean)} 字），请分段。")
    if allowed_voices and voice not in allowed_voices:
        raise ValueError(f"没有这个中文声音：{voice}；可选 " + " / ".join(allowed_voices[:12])
                         + ("……" if len(allowed_voices) > 12 else ""))
    return clean


def _wav_bytes(samples, sample_rate: int) -> bytes:
    """float32 音频 -> 16-bit PCM wav（标准库 wave，不引 soundfile）。"""
    import numpy as np

    arr = np.asarray(samples, dtype=np.float32)
    pcm = (np.clip(arr, -1.0, 1.0) * 32767).astype(np.int16)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(int(sample_rate))
        wav.writeframes(pcm.tobytes())
    return buf.getvalue()


def synthesize_speech(
    text: str, voice: str = DEFAULT_VOICE, speed: float = 1.0
) -> SynthResult:
    """文字 -> wav 字节。引擎/模型缺失时抛 :class:`EngineUnavailableError`。

    ``speed``：语速倍率，0.5–2.0，默认 1.0（正常语速）；超出范围抛
    ValueError。语速在合成时生效（Kokoro 的 speed 参数），不是播
    放时变速，所以音调不变、只是说得快慢不同。
    """
    if not 0.5 <= speed <= 2.0:
        raise ValueError(f"语速只能是 0.5–2.0 倍，收到 {speed}。")
    status = tts_status()
    if status["engine"] == "kokoro-onnx":
        clean = _check_text(text, voice, status["voices_zh"])
        return _synthesize_onnx(clean, voice, speed)
    if status["engine"] == "kokoro":
        clean = _check_text(text, voice, list(ZH_VOICES_V10))
        return _synthesize_torch(clean, voice, speed)
    raise EngineUnavailableError("语音合成还没装好，无法把文字读出来。", _KOKORO_HINT)


def _synthesize_onnx(text: str, voice: str, speed: float = 1.0) -> SynthResult:
    from kokoro_onnx import Kokoro

    detected = _detect_onnx_model()
    if detected is None:  # pragma: no cover - 状态检查已挡住
        raise EngineUnavailableError("语音合成还没装好，无法把文字读出来。", _KOKORO_HINT)
    model, voices, _default = detected
    # 中文发音走 misaki 的 ZHG2P（注音符号），再把音素直接交给模型：
    # 实测：kokoro-onnx 自带的 espeak 路径（lang="cmn"）读中文是错的
    # （"你好，我是贾维斯。今天是十一月十二日。" 被听成
    # "你到我死，苦味死……"），而 misaki ZHG2P + is_phonemes=True 读出来
    # 再转写是 "你好 我是賈維斯今天是11月12日"，基本正确。espeak 的
    # "cmn" 只作 misaki 缺失时的后路，但那条路中文是错的，所以 misaki
    # 缺失时直接报 503、不静默出声。
    version = "1.1" if "v1.1-zh" in model.name else "1.0"
    try:
        from misaki import zh as misaki_zh

        g2p = misaki_zh.ZHG2P(version=version)
        phonemes, _ = g2p(text)
    except Exception as exc:
        raise EngineUnavailableError(
            "中文语音合成需要 misaki[zh]（中文发音组件）还没装好。",
            "先装 misaki[zh]（pip install \"misaki[zh]\"），再重试。"
            f"原始错误：{exc}",
        ) from exc
    try:
        engine = Kokoro(str(model), str(voices))
        samples, sample_rate = engine.create(
            phonemes, voice=voice, speed=float(speed), lang="cmn", is_phonemes=True
        )
    except Exception as exc:
        raise EngineUnavailableError(
            "语音合成启动失败（可能是中文发音组件 espeak-ng 缺失）。",
            f"{_KOKORO_HINT} 原始错误：{exc}",
        ) from exc
    return SynthResult(
        wav_bytes=_wav_bytes(samples, sample_rate),
        sample_rate=int(sample_rate), voice=voice, engine="kokoro-onnx",
    )


def _synthesize_torch(text: str, voice: str, speed: float = 1.0) -> SynthResult:
    import numpy as np

    from kokoro import KPipeline

    try:
        pipe = KPipeline(lang_code="z")
        chunks = list(pipe(text, voice=voice, speed=float(speed)))
    except Exception as exc:
        raise EngineUnavailableError(
            "语音合成启动失败（可能是中文发音组件 espeak-ng 缺失）。",
            f"{_KOKORO_HINT} 原始错误：{exc}",
        ) from exc
    if not chunks:
        raise RuntimeError("Kokoro 没有合成出任何音频。")
    audio = np.concatenate([np.asarray(c.audio, dtype=np.float32) for c in chunks])
    sample_rate = 24000
    return SynthResult(
        wav_bytes=_wav_bytes(audio, sample_rate),
        sample_rate=sample_rate, voice=voice, engine="kokoro",
    )
