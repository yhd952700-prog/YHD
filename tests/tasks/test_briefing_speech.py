"""V2(b)：简报语音播报服务（假引擎注入，不依赖真 TTS）。"""

from __future__ import annotations

import time
from types import SimpleNamespace

from src.tasks.briefing_speech import (
    BriefingSpeechService,
    speech_text,
    split_speech_chunks,
)

BRIEFING = """# 今日简报

- 昨天的例程都跑完了，[详情](https://example.com/x) 见报告。
- 今天上午 9 点开会，**记得带材料**。
- 有 2 个待审批等你处理。"""


def _fake_synth(text: str):
    return SimpleNamespace(
        wav_bytes=b"WAV:" + text.encode("utf-8"), sample_rate=24000,
        voice="fake", engine="fake-tts",
    )


def _wait_status(service, owner, want, timeout=3.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        status = service.status(owner)
        if status.get("status") == want:
            return status
        time.sleep(0.02)
    return service.status(owner)


def test_speech_text_strips_markdown():
    clean = speech_text(BRIEFING)
    assert "#" not in clean and "*" not in clean
    assert "https://" not in clean
    assert "详情" in clean and "记得带材料" in clean


def test_split_chunks_respect_limit_and_sentences():
    chunks = split_speech_chunks("一句话。" * 40)
    assert chunks and all(len(c) <= 120 for c in chunks)
    assert "".join(chunks).count("一句话") == 40


def test_broadcast_full_lifecycle():
    service = BriefingSpeechService(
        briefing_builder=lambda owner: BRIEFING, synthesizer=_fake_synth
    )
    started = service.start("owner1")
    assert started["status"] in ("preparing", "ready")
    ready = _wait_status(service, "owner1", "ready")
    assert ready["chunks_total"] >= 1
    assert ready["chunks_ready"] == ready["chunks_total"]
    assert ready["engine"] == "fake-tts"

    pending = service.pending("owner1")
    assert pending and pending["broadcast_id"] == ready["broadcast_id"]
    audio = service.chunk_audio("owner1", pending["broadcast_id"], 0)
    assert audio and audio.startswith(b"WAV:")

    service.mark_played("owner1", pending["broadcast_id"])
    assert service.status("owner1")["status"] == "done"
    assert service.pending("owner1") is None


def test_broadcast_stop_blocks_audio():
    service = BriefingSpeechService(
        briefing_builder=lambda owner: BRIEFING, synthesizer=_fake_synth
    )
    service.start("owner1")
    ready = _wait_status(service, "owner1", "ready")
    service.stop("owner1")
    assert service.status("owner1")["status"] == "stopped"
    assert service.pending("owner1") is None
    assert service.chunk_audio("owner1", ready["broadcast_id"], 0) is None


def test_broadcast_engine_failure_is_honest():
    def _boom(text: str):
        raise RuntimeError("kokoro 模型不存在")

    service = BriefingSpeechService(
        briefing_builder=lambda owner: BRIEFING, synthesizer=_boom
    )
    service.start("owner1")
    failed = _wait_status(service, "owner1", "failed")
    assert failed["status"] == "failed"
    assert failed["error"]  # 有人类可读原因，不假装在播
    assert service.pending("owner1") is None


def test_broadcast_empty_briefing_fails_cleanly():
    service = BriefingSpeechService(
        briefing_builder=lambda owner: "   ", synthesizer=_fake_synth
    )
    result = service.start("owner1")
    assert result["status"] == "failed"
