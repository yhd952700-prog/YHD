"""大升级 U3 契约：语音引擎可插拔 + 音频注入管线演练。

本机无麦克风、无本机语音引擎（faster-whisper/Kokoro 未装），演练口径：
* 云端 provider 对本地假 HTTP 服务真发请求（multipart 转写 / JSON 合成）；
* 网关端点注入假 provider：音频文件 → 文字、文字 → wav 字节，全链路通；
* 唤醒词文本后处理与 wake.py 同一套口径；
* 失败必须人话：云端 500 只报中性错误，Key 绝不出现在错误/日志里；
* 默认 provider 恒为 local（升级前行为不变）。
"""
from __future__ import annotations

import io
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.common.engines import EngineUnavailableError
from src.voice import providers as voice_providers
from src.voice.stt import TranscribeResult
from src.voice.tts import SynthResult


class _CloudStub:
    """假云端语音服务：/audio/transcriptions 回固定文字，/audio/speech 回 wav 字节。"""

    def __init__(self, fail: bool = False) -> None:
        self.calls: list[dict] = []
        stub = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):  # 静音
                pass

            def do_POST(self):  # noqa: N802
                length = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(length) if length else b""
                stub.calls.append({
                    "path": self.path,
                    "auth": self.headers.get("Authorization"),
                    "content_type": self.headers.get("Content-Type"),
                    "body": body,
                })
                if fail:
                    self.send_response(500)
                    self.end_headers()
                    self.wfile.write(b'{"error": "boom"}')
                    return
                if self.path.endswith("/audio/transcriptions"):
                    payload = json.dumps({"text": "贾维斯，帮我记个事"}).encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(payload)))
                    self.end_headers()
                    self.wfile.write(payload)
                elif self.path.endswith("/audio/speech"):
                    payload = b"RIFF-fake-wav-bytes"
                    self.send_response(200)
                    self.send_header("Content-Type", "audio/wav")
                    self.send_header("Content-Length", str(len(payload)))
                    self.end_headers()
                    self.wfile.write(payload)
                else:
                    self.send_response(404)
                    self.end_headers()

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base_url = f"http://127.0.0.1:{self.server.server_address[1]}"

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture()
def cloud(monkeypatch):
    stub = _CloudStub()
    monkeypatch.setenv("LIUHAO_VOICE_OPENAI_BASE_URL", stub.base_url)
    monkeypatch.setenv("LIUHAO_VOICE_OPENAI_KEY", "sk-test-secret-key")
    yield stub
    stub.close()


class TestProviderSelection:
    def test_default_is_local(self, monkeypatch):
        monkeypatch.delenv("LIUHAO_VOICE_STT_PROVIDER", raising=False)
        monkeypatch.delenv("LIUHAO_VOICE_TTS_PROVIDER", raising=False)
        assert voice_providers.configured_provider("stt") == "local"
        assert voice_providers.configured_provider("tts") == "local"
        assert isinstance(voice_providers.get_stt_provider(), voice_providers.LocalSttProvider)

    def test_env_selects_cloud(self, monkeypatch, cloud):
        monkeypatch.setenv("LIUHAO_VOICE_STT_PROVIDER", "openai")
        assert isinstance(voice_providers.get_stt_provider(), voice_providers.OpenAiSttProvider)

    def test_cloud_without_config_is_honest(self, monkeypatch):
        monkeypatch.setenv("LIUHAO_VOICE_STT_PROVIDER", "openai")
        monkeypatch.delenv("LIUHAO_VOICE_OPENAI_BASE_URL", raising=False)
        monkeypatch.delenv("LIUHAO_VOICE_OPENAI_KEY", raising=False)
        with pytest.raises(EngineUnavailableError):
            voice_providers.get_stt_provider().transcribe(b"x", "a.webm")


class TestCloudProviders:
    def test_stt_round_trip(self, monkeypatch, cloud):
        monkeypatch.setenv("LIUHAO_VOICE_STT_PROVIDER", "openai")
        result = voice_providers.get_stt_provider().transcribe(b"fake-audio", "rec.webm", "zh")
        assert result.text == "贾维斯，帮我记个事"
        assert result.engine == "openai-audio"
        call = cloud.calls[-1]
        assert call["auth"] == "Bearer sk-test-secret-key"
        assert b"whisper-1" in call["body"]

    def test_tts_round_trip_and_voice_fallback(self, monkeypatch, cloud):
        monkeypatch.setenv("LIUHAO_VOICE_TTS_PROVIDER", "openai")
        # 传本机 Kokoro 音色名 → 必须回落到云端默认音色，不许原样发出去
        result = voice_providers.get_tts_provider().synthesize("你好", voice="zm_010")
        assert result.wav_bytes == b"RIFF-fake-wav-bytes"
        sent = json.loads(cloud.calls[-1]["body"])
        assert sent["voice"] == "alloy"
        assert sent["response_format"] == "wav"

    def test_cloud_failure_is_neutral_and_key_free(self, monkeypatch):
        stub = _CloudStub(fail=True)
        monkeypatch.setenv("LIUHAO_VOICE_OPENAI_BASE_URL", stub.base_url)
        monkeypatch.setenv("LIUHAO_VOICE_OPENAI_KEY", "sk-test-secret-key")
        try:
            with pytest.raises(RuntimeError) as info:
                voice_providers.OpenAiSttProvider().transcribe(b"x", "a.webm")
            message = str(info.value)
            assert "500" in message
            assert "sk-test-secret-key" not in message
            assert "boom" not in message, "上游原文不许回显给用户"
        finally:
            stub.close()


class TestWakeCommand:
    def test_extract_command(self):
        assert voice_providers.extract_wake_command("贾维斯，帮我记个事") == "帮我记个事"
        assert voice_providers.extract_wake_command("贾维斯") == ""
        assert voice_providers.extract_wake_command("今天天气怎么样") is None


class _FakeStt:
    name = "fake"

    def transcribe(self, data, filename, language="zh"):
        assert data == b"fake-audio-bytes"
        return TranscribeResult(text="帮我记个事", language="zh", engine="fake", model="fake-1")


class _FakeTts:
    name = "fake"

    def synthesize(self, text, voice="", speed=1.0):
        return SynthResult(wav_bytes=b"RIFF-out", sample_rate=24000, voice="fake", engine="fake")


class TestGatewayPipeline:
    def _client(self, monkeypatch):
        monkeypatch.setattr(voice_providers, "get_stt_provider", lambda: _FakeStt())
        monkeypatch.setattr(voice_providers, "get_tts_provider", lambda: _FakeTts())
        # gateway.voice 在导入时已绑定函数名，补丁要打在它的命名空间上
        import src.gateway.voice as gateway_voice

        monkeypatch.setattr(gateway_voice, "get_stt_provider", lambda: _FakeStt())
        monkeypatch.setattr(gateway_voice, "get_tts_provider", lambda: _FakeTts())
        app = FastAPI()
        app.include_router(gateway_voice.router)
        return TestClient(app)

    def test_audio_file_in_text_and_wav_out(self, monkeypatch):
        client = self._client(monkeypatch)
        response = client.post(
            "/v1/voice/transcribe",
            files={"file": ("rec.webm", io.BytesIO(b"fake-audio-bytes"))},
        )
        assert response.status_code == 200
        assert response.json()["text"] == "帮我记个事"
        assert response.json()["engine"] == "fake"

        spoken = client.post("/v1/voice/speak", json={"text": "好的，已记下"})
        assert spoken.status_code == 200
        assert spoken.content == b"RIFF-out"
        assert spoken.headers["x-engine"] == "fake"

    def test_status_reports_pipeline(self, monkeypatch):
        client = self._client(monkeypatch)
        body = client.get("/v1/voice/status").json()
        assert "providers" in body and "wake" in body
