"""V2(b)：晨间简报语音播报。

链路：聊天里说「播报简报」→ 本模块生成简报文本、清洗成可朗读文本、
逐段用现有 TTS 供应方合成（后台线程，慢引擎不阻塞聊天）→ 控制台主题层
轮询待播队列、逐段播放（浏览器负责出声）；说「停」→ 服务端状态置
stopped、播放端段间检查即停。引擎没装时如实失败（带安装提示），
绝不假装播报。
"""

from __future__ import annotations

import re
import threading
import time
import uuid
from typing import Any, Callable, Dict, List, Optional

_MAX_CHUNK_CHARS = 120
_TTL_SECONDS = 30 * 60

_MD_LINK_RE = re.compile(r"\[([^\]]*)\]\([^)]*\)")
_MD_MARK_RE = re.compile(r"[#>*`_~]+")
_LIST_MARK_RE = re.compile(r"^\s*(?:[-•·]|\d+[.、)])\s*", re.MULTILINE)
_URL_RE = re.compile(r"https?://\S+")
_WS_RE = re.compile(r"\s+")


def speech_text(raw: str) -> str:
    """把简报的 Markdown 文本清洗成适合朗读的纯文本。"""
    text = _MD_LINK_RE.sub(r"\1", str(raw or ""))
    text = _URL_RE.sub("", text)
    text = _LIST_MARK_RE.sub("", text)
    text = _MD_MARK_RE.sub("", text)
    return _WS_RE.sub(" ", text).strip()


def split_speech_chunks(text: str, max_chars: int = _MAX_CHUNK_CHARS) -> List[str]:
    """按句末标点切段，每段不超过 max_chars（长句硬切）。"""
    clean = speech_text(text)
    if not clean:
        return []
    sentences = re.split(r"(?<=[。！？；…])", clean)
    chunks: List[str] = []
    buf = ""
    for sentence in sentences:
        sentence = sentence.strip()
        if not sentence:
            continue
        while len(sentence) > max_chars:
            if buf:
                chunks.append(buf)
                buf = ""
            chunks.append(sentence[:max_chars])
            sentence = sentence[max_chars:]
        if len(buf) + len(sentence) <= max_chars:
            buf += sentence
        else:
            if buf:
                chunks.append(buf)
            buf = sentence
    if buf:
        chunks.append(buf)
    return [c for c in chunks if c.strip()]


class _Broadcast:
    def __init__(self, broadcast_id: str, owner: str, text: str, chunks: List[str]):
        self.id = broadcast_id
        self.owner = owner
        self.text = text
        self.chunks = chunks
        self.audio: List[Optional[bytes]] = [None] * len(chunks)
        self.status = "preparing"  # preparing | ready | stopped | failed
        self.error = ""
        self.created_at = time.time()
        self.engine = ""


class BriefingSpeechService:
    """每账号一份待播状态；合成在后台线程，播放由前端逐段拉取。"""

    def __init__(
        self,
        *,
        briefing_builder: Optional[Callable[[str], str]] = None,
        synthesizer: Optional[Callable[[str], Any]] = None,
    ) -> None:
        self._briefing_builder = briefing_builder
        self._synthesizer = synthesizer
        self._lock = threading.Lock()
        self._by_owner: Dict[str, _Broadcast] = {}

    # -- 依赖（可注入，测试用假引擎） ------------------------------------
    def _build_text(self, owner: str) -> str:
        if self._briefing_builder is not None:
            return self._briefing_builder(owner)
        from src.tasks.briefing import build_morning_briefing

        return build_morning_briefing(owner)

    def _synth(self, text: str) -> Any:
        if self._synthesizer is not None:
            return self._synthesizer(text)
        from src.voice.providers import get_tts_provider

        return get_tts_provider().synthesize(text)

    # -- 对外 -------------------------------------------------------------
    def start(self, owner: str) -> Dict[str, Any]:
        """生成简报并开始合成；立即返回，合成进度用 status 查。"""
        raw = self._build_text(owner)
        chunks = split_speech_chunks(raw)
        if not chunks:
            return {"status": "failed", "error": "简报是空的，没有可播报的内容。"}
        broadcast = _Broadcast(uuid.uuid4().hex[:12], owner, speech_text(raw), chunks)
        with self._lock:
            old = self._by_owner.get(owner)
            if old is not None:
                old.status = "stopped"
            self._by_owner[owner] = broadcast
        thread = threading.Thread(
            target=self._synth_all, args=(broadcast,), daemon=True
        )
        thread.start()
        return self.status(owner)

    def _synth_all(self, broadcast: _Broadcast) -> None:
        try:
            for idx, chunk in enumerate(broadcast.chunks):
                if broadcast.status == "stopped":
                    return
                result = self._synth(chunk)
                broadcast.audio[idx] = result.wav_bytes
                broadcast.engine = getattr(result, "engine", "") or broadcast.engine
            if broadcast.status != "stopped":
                broadcast.status = "ready"
        except Exception as exc:  # noqa: BLE001 - 如实上报，不假装
            from src.voice.session import human_voice_error

            broadcast.status = "failed"
            broadcast.error = human_voice_error(exc, stage="speak")

    def stop(self, owner: str) -> Dict[str, Any]:
        with self._lock:
            broadcast = self._by_owner.get(owner)
            if broadcast is not None and broadcast.status in ("preparing", "ready"):
                broadcast.status = "stopped"
        return self.status(owner)

    def status(self, owner: str) -> Dict[str, Any]:
        with self._lock:
            broadcast = self._by_owner.get(owner)
        if broadcast is None:
            return {"status": "idle"}
        ready_chunks = sum(1 for a in broadcast.audio if a)
        return {
            "status": broadcast.status,
            "broadcast_id": broadcast.id,
            "chunks_total": len(broadcast.chunks),
            "chunks_ready": ready_chunks,
            "engine": broadcast.engine,
            "error": broadcast.error,
            "text": broadcast.text,
        }

    def pending(self, owner: str) -> Optional[Dict[str, Any]]:
        """主题层轮询用：有 ready 的待播就返回清单，没有返回 None。"""
        with self._lock:
            broadcast = self._by_owner.get(owner)
        if broadcast is None or broadcast.status != "ready":
            return None
        return {
            "broadcast_id": broadcast.id,
            "chunks": len(broadcast.chunks),
            "status": broadcast.status,
        }

    def chunk_audio(self, owner: str, broadcast_id: str, index: int) -> Optional[bytes]:
        with self._lock:
            broadcast = self._by_owner.get(owner)
        if broadcast is None or broadcast.id != broadcast_id:
            return None
        if broadcast.status == "stopped":
            return None
        if 0 <= index < len(broadcast.audio):
            return broadcast.audio[index]
        return None

    def mark_played(self, owner: str, broadcast_id: str) -> Dict[str, Any]:
        """前端播完（或用户关掉）后回执，避免重复播同一条。"""
        with self._lock:
            broadcast = self._by_owner.get(owner)
            if broadcast is not None and broadcast.id == broadcast_id:
                broadcast.status = "done"
        return self.status(owner)


_SERVICE: Optional[BriefingSpeechService] = None
_SERVICE_LOCK = threading.Lock()


def get_briefing_speech_service() -> BriefingSpeechService:
    global _SERVICE
    with _SERVICE_LOCK:
        if _SERVICE is None:
            _SERVICE = BriefingSpeechService()
        return _SERVICE
