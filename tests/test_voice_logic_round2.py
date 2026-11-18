"""语音纯逻辑层专属测试（完善第二轮 A4）：settings 存取/校验 + turns 轮次记录。

只测不需要引擎/模型的纯逻辑：调参设置的默认值、合法档位校验、环境
变量覆盖口径，以及对话轮次的开始/事件/计时/结束/隔离。不碰 STT/TTS
引擎（模型需另行下载，真机项）。
"""

from __future__ import annotations

import json

import pytest

from src.voice import settings as voice_settings
from src.voice import turns


@pytest.fixture()
def voice_home(tmp_path, monkeypatch):
    monkeypatch.setenv("LIUHAO_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("LIUHAO_VAD_MIN_SILENCE_MS", raising=False)
    return tmp_path


def test_settings_defaults_when_no_file(voice_home):
    loaded = voice_settings.load_voice_settings()
    assert loaded["min_silence_duration_ms"] == 800
    assert loaded["interrupt_min_ms"] == 300
    assert loaded["interrupt_cooldown_ms"] == 500
    assert loaded["env_override"]["min_silence_duration_ms"] is False
    assert loaded["silence_options_ms"] == [400, 600, 800, 1200]


def test_settings_save_load_roundtrip(voice_home):
    saved = voice_settings.save_voice_settings(
        min_silence_duration_ms=600, interrupt_min_ms=200, interrupt_cooldown_ms=0
    )
    assert saved["min_silence_duration_ms"] == 600
    path = voice_home / "voice" / "settings.json"
    assert json.loads(path.read_text(encoding="utf-8"))["interrupt_min_ms"] == 200
    assert voice_settings.vad_min_silence_ms() == 600


@pytest.mark.parametrize("bad", [0, 500, 1000, -400])
def test_settings_reject_illegal_silence(voice_home, bad):
    with pytest.raises(ValueError):
        voice_settings.save_voice_settings(min_silence_duration_ms=bad)


@pytest.mark.parametrize("kwargs", [
    {"interrupt_min_ms": 99}, {"interrupt_min_ms": 1001},
    {"interrupt_cooldown_ms": -1}, {"interrupt_cooldown_ms": 2001},
])
def test_settings_reject_out_of_range_interrupt(voice_home, kwargs):
    with pytest.raises(ValueError):
        voice_settings.save_voice_settings(min_silence_duration_ms=800, **kwargs)


def test_settings_corrupt_file_falls_back_to_defaults(voice_home):
    path = voice_home / "voice" / "settings.json"
    path.parent.mkdir(parents=True)
    path.write_text("{not json", encoding="utf-8")
    loaded = voice_settings.load_voice_settings()
    assert loaded["min_silence_duration_ms"] == 800


def test_settings_illegal_stored_values_sanitized(voice_home):
    path = voice_home / "voice" / "settings.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({
        "min_silence_duration_ms": 500, "interrupt_min_ms": 5,
        "interrupt_cooldown_ms": 99999,
    }), encoding="utf-8")
    loaded = voice_settings.load_voice_settings()
    assert loaded["min_silence_duration_ms"] == 800
    assert loaded["interrupt_min_ms"] == 300
    assert loaded["interrupt_cooldown_ms"] == 500


def test_env_override_only_accepts_legal_options(voice_home, monkeypatch):
    voice_settings.save_voice_settings(min_silence_duration_ms=600)
    monkeypatch.setenv("LIUHAO_VAD_MIN_SILENCE_MS", "1200")
    loaded = voice_settings.load_voice_settings()
    assert loaded["min_silence_duration_ms"] == 1200
    assert loaded["env_override"]["min_silence_duration_ms"] is True
    monkeypatch.setenv("LIUHAO_VAD_MIN_SILENCE_MS", "999")
    loaded = voice_settings.load_voice_settings()
    assert loaded["min_silence_duration_ms"] == 600
    assert loaded["env_override"]["min_silence_duration_ms"] is False


@pytest.fixture()
def clean_turns():
    turns.reset_turns()
    yield
    turns.reset_turns()


def test_turn_lifecycle(clean_turns):
    turn = turns.start_turn("conv-1")
    assert turn["turn_id"].startswith("vturn_")
    assert turn["events"][0]["state"] == "listening"
    turns.note_event(turn["turn_id"], "transcribing")
    turns.note_timing(turn["turn_id"], "transcribe", 123.0)
    done = turns.finish_turn(turn["turn_id"], transcript="你好", reply_chars=10)
    assert done["transcript"] == "你好"
    assert done["reply_chars"] == 10
    assert done["ended_at"] is not None
    listed = turns.list_turns("conv-1")
    assert len(listed) == 1
    assert listed[0]["timings_ms"]["transcribe"] == 123.0
    states = [e["state"] for e in listed[0]["events"]]
    assert states == ["listening", "transcribing"]


def test_turn_isolation_between_conversations(clean_turns):
    turns.start_turn("conv-a")
    turns.start_turn("conv-b")
    turns.start_turn("conv-a")
    assert len(turns.list_turns("conv-a")) == 2
    assert len(turns.list_turns("conv-b")) == 1
    assert turns.list_turns("conv-none") == []


def test_turn_unknown_id_is_noop(clean_turns):
    turns.note_event("vturn_nope", "x")
    turns.note_timing("vturn_nope", "transcribe", 1.0)
    assert turns.finish_turn("vturn_nope") is None


def test_turn_interrupt_flag(clean_turns):
    turn = turns.start_turn("conv-9")
    done = turns.finish_turn(turn["turn_id"], interrupted=True, interrupt_reason="用户打断")
    assert done["interrupted"] is True
    assert done["interrupt_reason"] == "用户打断"
