"""语音引擎提供者抽象（大升级 U3）。

此前 STT/TTS 只有本机引擎、且调用方（gateway/voice.py）直接绑死
``transcribe_audio`` / ``synthesize_speech`` 两个函数：想接云端识别
只能改调用方。本模块把三段（识别 / 合成 / 唤醒后处理）收成可插拔的
provider：

* ``local``（默认）：本机离线引擎（faster-whisper / whisper.cpp、
  Kokoro），录音不出本机——默认行为与升级前逐字一致；
* ``openai``：OpenAI 兼容的 ``/audio/transcriptions`` 与 ``/audio/speech``，
  显式配置才启用（环境变量或语音设置文件），Key 只从环境变量读、
  绝不进日志/状态/响应。

唤醒段：设备端流式唤醒（Vosk/sherpa，见 wake.py）之外，补一个纯文本
后处理 ``extract_wake_command``——识别文字里命中唤醒词后剥出命令本体，
供"整段录音转写再唤醒"的链路复用同一套唤醒词口径。

配置口径（优先级：环境变量 > 语音设置文件 > 默认 local）：
``LIUHAO_VOICE_STT_PROVIDER`` / ``LIUHAO_VOICE_TTS_PROVIDER``；
云端连接：``LIUHAO_VOICE_OPENAI_BASE_URL`` / ``LIUHAO_VOICE_OPENAI_KEY`` /
``LIUHAO_VOICE_STT_MODEL``（默认 whisper-1）/ ``LIUHAO_VOICE_TTS_MODEL``
（默认 tts-1）/ ``LIUHAO_VOICE_TTS_VOICE``（默认 alloy）。
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict, Optional

from src.common.engines import EngineUnavailableError

from .stt import TranscribeResult, stt_status, transcribe_audio
from .tts import DEFAULT_VOICE, SynthResult, synthesize_speech, tts_status
from .wake import WAKE_WORD, contains_wake_word, wake_status

logger = logging.getLogger("liuhao.voice.providers")

_CLOUD_HINT = (
    "云端语音还没配好：设置 LIUHAO_VOICE_OPENAI_BASE_URL 与 "
    "LIUHAO_VOICE_OPENAI_KEY 后重试；或把语音引擎切回本机（local）。"
)


def _settings_file_providers() -> Dict[str, str]:
    """语音设置文件里的 provider 选择（settings.py 只管调参数，这里只读原始键）。"""
    try:
        from .settings import settings_path

        path = settings_path()
        if path.is_file():
            loaded = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                return {
                    str(key): str(value)
                    for key, value in loaded.items()
                    if key in {"stt_provider", "tts_provider"}
                }
    except Exception:  # noqa: BLE001 - 设置读不到就按默认，不阻断语音
        pass
    return {}


def configured_provider(kind: str) -> str:
    """kind = 'stt' | 'tts' → 'local' | 'openai'（默认 local）。"""
    env_name = f"LIUHAO_VOICE_{kind.upper()}_PROVIDER"
    raw = (os.environ.get(env_name) or "").strip().lower()
    if raw in {"local", "openai"}:
        return raw
    from_file = _settings_file_providers().get(f"{kind}_provider", "").strip().lower()
    if from_file in {"local", "openai"}:
        return from_file
    return "local"


def _cloud_config() -> Dict[str, str]:
    return {
        "base_url": (os.environ.get("LIUHAO_VOICE_OPENAI_BASE_URL") or "").strip(),
        "api_key": (os.environ.get("LIUHAO_VOICE_OPENAI_KEY") or "").strip(),
        "stt_model": (os.environ.get("LIUHAO_VOICE_STT_MODEL") or "whisper-1").strip(),
        "tts_model": (os.environ.get("LIUHAO_VOICE_TTS_MODEL") or "tts-1").strip(),
        "tts_voice": (os.environ.get("LIUHAO_VOICE_TTS_VOICE") or "alloy").strip(),
    }


def _require_cloud_config() -> Dict[str, str]:
    config = _cloud_config()
    if not config["base_url"] or not config["api_key"]:
        raise EngineUnavailableError("云端语音引擎未配置。", _CLOUD_HINT)
    return config


def _cloud_post(path: str, config: Dict[str, str], **kwargs: Any):
    """云端语音 HTTP 调用：Key 只进请求头；失败只抛中性 RuntimeError。"""
    import requests

    url = config["base_url"].rstrip("/") + path
    try:
        response = requests.post(
            url,
            headers={"Authorization": f"Bearer {config['api_key']}"},
            timeout=60,
            proxies={"http": None, "https": None},
            **kwargs,
        )
    except Exception as exc:  # noqa: BLE001 - 连接类失败统一中性化
        raise RuntimeError(f"云端语音服务连接失败（{type(exc).__name__}）") from exc
    if response.status_code >= 400:
        # 不回显上游原文（可能含请求细节），只报状态码这一层事实。
        raise RuntimeError(f"云端语音服务返回 HTTP {response.status_code}")
    return response


class LocalSttProvider:
    name = "local"

    def transcribe(self, data: bytes, filename: str, language: str = "zh") -> TranscribeResult:
        return transcribe_audio(data, filename, language)


class OpenAiSttProvider:
    name = "openai"

    def transcribe(self, data: bytes, filename: str, language: str = "zh") -> TranscribeResult:
        config = _require_cloud_config()
        response = _cloud_post(
            "/audio/transcriptions",
            config,
            files={"file": (filename or "recording.webm", data)},
            data={"model": config["stt_model"], "language": language or "zh"},
        )
        try:
            payload = response.json()
            text = str(payload.get("text") or "")
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError("云端语音识别返回了无法解析的结果") from exc
        if not text.strip():
            raise RuntimeError("云端语音识别没有返回文字（录音太短或全是静音？）")
        return TranscribeResult(
            text=text,
            language=language or "zh",
            engine="openai-audio",
            model=config["stt_model"],
        )


class LocalTtsProvider:
    name = "local"

    def synthesize(self, text: str, voice: str = "", speed: float = 1.0) -> SynthResult:
        # 空音色 = 用默认音色（V1 闭环修正：会话状态机等调用方不带音色时，
        # 此前会把空串直传引擎、被以「没有这个中文声音」拒绝，播报段断链）。
        return synthesize_speech(text, voice or DEFAULT_VOICE, speed)


#: OpenAI 语音合成的已知音色；本机 Kokoro 的音色名（如 zm_010）传给云端
#: 只会被拒，命中不了这张表的一律回落到配置的云端默认音色。
_CLOUD_TTS_VOICES = {
    "alloy", "ash", "ballad", "coral", "echo", "fable",
    "nova", "onyx", "sage", "shimmer", "verse",
}


class OpenAiTtsProvider:
    name = "openai"

    def synthesize(self, text: str, voice: str = "", speed: float = 1.0) -> SynthResult:
        config = _require_cloud_config()
        requested = (voice or "").strip()
        chosen_voice = requested if requested in _CLOUD_TTS_VOICES else config["tts_voice"]
        response = _cloud_post(
            "/audio/speech",
            config,
            json={
                "model": config["tts_model"],
                "input": text,
                "voice": chosen_voice,
                "response_format": "wav",
                "speed": speed,
            },
        )
        if not response.content:
            raise RuntimeError("云端语音合成返回了空音频")
        return SynthResult(
            wav_bytes=response.content,
            sample_rate=24000,
            voice=chosen_voice,
            engine="openai-audio",
        )


def get_stt_provider():
    if configured_provider("stt") == "openai":
        return OpenAiSttProvider()
    return LocalSttProvider()


def get_tts_provider():
    if configured_provider("tts") == "openai":
        return OpenAiTtsProvider()
    return LocalTtsProvider()


def extract_wake_command(text: str) -> Optional[str]:
    """识别文字命中唤醒词 → 返回剥掉唤醒词的命令本体；未命中返回 None。

    命中但后面没内容时返回空串（只是喊了声"贾维斯"，等下一句）。
    """
    if not contains_wake_word(text):
        return None
    compact = (text or "").replace(" ", "").replace("　", "")
    index = compact.find(WAKE_WORD)
    command = compact[index + len(WAKE_WORD):] if index >= 0 else ""
    return command.lstrip("，,。！!？?：:、 ")


def voice_pipeline_status() -> Dict[str, Any]:
    """语音全链路状态（设置页/聊天共用口径）：引擎、当前 provider、唤醒。"""
    stt = stt_status()
    tts = tts_status()
    stt_provider = configured_provider("stt")
    tts_provider = configured_provider("tts")
    if stt_provider == "openai":
        config = _cloud_config()
        stt = {
            **stt,
            "available": bool(config["base_url"] and config["api_key"]),
            "engine": "openai-audio",
            "model": config["stt_model"],
            "note": "云端识别：录音会发到所配置的云端服务。",
            "install_hint": "" if config["base_url"] and config["api_key"] else _CLOUD_HINT,
        }
    if tts_provider == "openai":
        config = _cloud_config()
        tts = {
            **tts,
            "available": bool(config["base_url"] and config["api_key"]),
            "engine": "openai-audio",
            "voice": config["tts_voice"],
            "note": "云端合成：文字会发到所配置的云端服务。",
            "install_hint": "" if config["base_url"] and config["api_key"] else _CLOUD_HINT,
        }
    try:
        from .session import session_status

        session = session_status()
    except Exception:  # noqa: BLE001 - 会话状态读不到不许拖垮语音状态
        session = {"state": "unknown"}
    return {
        "stt": stt,
        "tts": tts,
        "wake": wake_status(),
        "providers": {"stt": stt_provider, "tts": tts_provider},
        "session": session,
        "ready": bool(stt["available"] and tts["available"]),
    }
