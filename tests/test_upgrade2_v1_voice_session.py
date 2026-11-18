"""大升级第二季 V1：语音会话状态机全链演练（音频文件注入，无麦克风）。

链路：样本块流（由 wav 字节切块，模拟麦克风 0.1s 一块）→ 假 spots
检出唤醒 → 收命令（静音判定说完）→ 假 STT 转写 → 命令处理器 →
假 TTS 逐句合成 → 收集播放。断言状态轨迹、各段耗时、预算对表与
失败人话口径。真引擎（sherpa KWS / faster-whisper / Kokoro）的实测
在 upgrade2-evidence 的真机演练脚本里，本文件只守状态机契约。
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.common.engines import EngineUnavailableError
from src.voice import session as sess
from src.voice.stt import TranscribeResult
from src.voice.tts import SynthResult


def _tone(seconds: float, amp: float = 0.2, freq: float = 220.0) -> np.ndarray:
    t = np.arange(int(16000 * seconds), dtype=np.float32) / 16000.0
    return (amp * np.sin(2 * np.pi * freq * t)).astype(np.float32)


def _silence(seconds: float) -> np.ndarray:
    return np.zeros(int(16000 * seconds), dtype=np.float32)


def _chunks(*segments: np.ndarray) -> list:
    out = []
    for seg in segments:
        step = 1600
        out.extend(seg[i : i + step] for i in range(0, len(seg), step))
    return out


class FakeStt:
    def __init__(self, text: str = "今天天气怎么样", exc: Exception | None = None):
        self.text = text
        self.exc = exc
        self.calls = []

    def transcribe(self, data: bytes, filename: str, language: str = "zh"):
        self.calls.append((len(data), filename))
        if self.exc is not None:
            raise self.exc
        return TranscribeResult(text=self.text, language="zh", engine="fake", model="fake")


class FakeTts:
    def __init__(self, exc: Exception | None = None):
        self.exc = exc
        self.texts = []

    def synthesize(self, text: str, voice: str = "", speed: float = 1.0):
        if self.exc is not None:
            raise self.exc
        self.texts.append(text)
        return SynthResult(wav_bytes=b"WAV:" + text.encode(), sample_rate=24000,
                           voice="zf_001", engine="fake")


def _spot_first_loud(samples, _rate):
    """假 spots：第一个有声块即'检出唤醒词'（唤醒与命令同段的演练口径）。"""
    return sess._rms(samples) >= sess.SILENCE_RMS


def _make_session(**kwargs):
    played: list = []
    prompts: list = []
    session = sess.VoiceSession(
        spot_fn=kwargs.pop("spot_fn", _spot_first_loud),
        stt_provider=kwargs.pop("stt_provider", FakeStt()),
        tts_provider=kwargs.pop("tts_provider", FakeTts()),
        command_handler=kwargs.pop("command_handler", lambda text: "今天晴天，适合出门。记得带伞。"),
        audio_out=played.append,
        prompt_out=prompts.append,
        **kwargs,
    )
    return session, played, prompts


def test_full_loop_states_and_timings():
    session, played, _prompts = _make_session()
    # 静音（不触发）→ 有声（唤醒+命令一体）→ 静音说完 → 流结束
    stream = _chunks(_silence(0.3), _tone(1.2), _silence(1.2))
    session.run_stream(stream)
    status = session.status()
    assert status["state"] == "off"
    assert status["turns_done"] == 1
    turn = status["last_turn"]
    assert turn["transcript"] == "今天天气怎么样"
    assert turn["states"] == ["awake", "transcribing", "thinking", "speaking"]
    assert turn["reply_chars"] > 0
    for key in ("capture", "transcribe", "think", "first_audio", "turn_total"):
        assert turn["timings_ms"][key] is not None, key
    # 回复两句 → 合成两段音频
    assert len(played) == 2
    assert status["budgets_ms"]["first_audio"] == 3000


def test_wake_without_speech_returns_to_listening():
    session, played, prompts = _make_session()
    # spots 对所有块都报检出，但全程静音：窗口超时当误唤，不转写不播报
    session2, played2, prompts2 = _make_session(spot_fn=lambda s, r: True)
    session2.run_stream(_chunks(_silence(7.0)))
    status = session2.status()
    assert status["turns_done"] == 0
    assert played2 == [] and prompts2 == []
    assert session.state == "off"  # 第一个 session 没跑过


def test_empty_transcript_prompts_not_heard():
    session, played, prompts = _make_session(stt_provider=FakeStt(text="  "))
    session.run_stream(_chunks(_tone(1.0), _silence(1.2)))
    status = session.status()
    assert status["last_prompt"] == sess.PROMPT_NOT_HEARD
    assert prompts == [sess.PROMPT_NOT_HEARD]
    assert status["last_turn"]["states"] == ["awake", "transcribing"]


def test_stt_unavailable_becomes_human_prompt():
    exc = EngineUnavailableError("语音识别还没装好，无法把录音转成文字。", "先装 faster-whisper。")
    session, _played, prompts = _make_session(stt_provider=FakeStt(exc=exc))
    session.run_stream(_chunks(_tone(1.0), _silence(1.2)))
    status = session.status()
    assert "faster-whisper" in status["last_turn"]["error"]
    assert prompts and "faster-whisper" in prompts[0]
    # 原始异常类型不许漏到用户面
    assert "EngineUnavailableError" not in prompts[0]


def test_tts_failure_reports_text_fallback_prompt():
    session, _played, prompts = _make_session(
        tts_provider=FakeTts(exc=RuntimeError("boom-internal-detail"))
    )
    session.run_stream(_chunks(_tone(1.0), _silence(1.2)))
    status = session.status()
    assert status["last_turn"]["error"] == sess.PROMPT_TTS_DOWN
    assert "boom-internal-detail" not in str(status["last_turn"])
    assert prompts == [sess.PROMPT_TTS_DOWN]


def test_human_voice_error_mapping():
    assert sess.human_voice_error(TimeoutError()) == sess.PROMPT_TIMEOUT
    assert sess.human_voice_error(RuntimeError("云端语音识别没有返回文字（录音太短或全是静音？）")) == sess.PROMPT_NOT_HEARD
    assert sess.human_voice_error(RuntimeError("x"), stage="wake").startswith("唤醒词引擎")
    assert sess.human_voice_error(RuntimeError("x")) == sess.PROMPT_GENERIC


def test_wav_roundtrip_chunks():
    samples = _tone(0.5)
    wav = sess.samples_to_wav_bytes(samples)
    chunks = sess.wav_bytes_to_chunks(wav)
    joined = np.concatenate(chunks)
    assert abs(len(joined) - len(samples)) <= 1600
    assert sess._rms(joined) > sess.SILENCE_RMS


def test_transcribe_budget_scales_with_audio():
    assert sess.transcribe_budget_ms(0) == 2000
    assert sess.transcribe_budget_ms(10) == 4500


def test_session_status_in_pipeline(monkeypatch):
    from src.voice import providers

    status = providers.voice_pipeline_status()
    assert "session" in status
    assert status["session"]["state"] in {
        "off", "listening", "awake", "transcribing", "thinking", "speaking", "unknown",
    }
    assert "budgets_ms" in status["session"] or status["session"]["state"] == "unknown"


def test_overrun_flagged_not_hidden(monkeypatch):
    # 把转写预算压到 0.5ms 的等效口径：用环境变量覆盖成 1ms，慢假 STT 必超
    monkeypatch.setenv("LIUHAO_VOICE_BUDGET_TRANSCRIBE_MS", "1")

    class SlowStt(FakeStt):
        def transcribe(self, data, filename, language="zh"):
            import time

            time.sleep(0.02)
            return super().transcribe(data, filename, language)

    session, _played, _prompts = _make_session(stt_provider=SlowStt())
    session.run_stream(_chunks(_tone(0.6), _silence(1.2)))
    turn = session.status()["last_turn"]
    assert "transcribe" in turn["overruns"]


def test_local_tts_provider_empty_voice_falls_back_to_default(monkeypatch):
    # V1 真引擎演练暴露：会话以空音色调 LocalTtsProvider 时，空串直传引擎
    # 被以「没有这个中文声音」拒绝、播报断链。空音色必须回落默认音色。
    from src.voice import providers

    seen = {}

    def fake_synthesize(text, voice, speed=1.0):
        seen["voice"] = voice
        return "ok"

    monkeypatch.setattr(providers, "synthesize_speech", fake_synthesize)
    out = providers.LocalTtsProvider().synthesize("你好")
    assert out == "ok"
    assert seen["voice"] == providers.DEFAULT_VOICE


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
