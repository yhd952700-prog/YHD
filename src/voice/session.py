"""语音会话状态机（大升级第二季 V1：语音真落地）。

U3 把识别 / 合成 / 唤醒做成了可插拔的服务层，但三段是散的：唤醒监听
（``wake.py``）检出唤醒词只记一笔事件，之后没有人接棒。本模块把整条
本地链路收成一个**会话状态机**，一轮对话在状态之间走完闭环：

    off ──start──▶ listening（常听，音频块持续过唤醒 spots）
    listening ──检出唤醒词──▶ awake（开窗收命令）
    awake ──说完（静音达标）/ 超时无声──▶ transcribing / 回 listening
    transcribing ──有文字──▶ thinking（命令交给处理器）
    thinking ──有回复──▶ speaking（逐句合成并送播放端）
    speaking ──播完──▶ listening（继续常听，等下一声唤醒）
    任一段失败 ──▶ 把人话提示交给提示端（能播就播报），回 listening

注入点（全部可换，演练与真机共用同一台状态机）：
* ``chunks``：音频块来源（float32 16k 单声道）。真机 = 麦克风采集
  （``wake._mic_chunks`` 同款迭代器）；演练 = 从 wav 文件切块注入。
* ``spot_fn``：唤醒检出（默认 ``wake.spot_samples``，真 sherpa KWS）。
* ``stt_provider`` / ``tts_provider``：默认走 U3 服务层
  （``providers.get_stt_provider()`` / ``get_tts_provider()``）。
* ``command_handler``：命令文字 → 回复文字（网关侧接聊天链路）。
* ``audio_out``：合成音频的去处（真机 = 控制台播放通道；演练 = 收集）。
* ``prompt_out``：人话提示的去处（默认尝试用 TTS 播报，失败只记录）。

延迟预算（毫秒，可用环境变量调，见 ``_budget_ms``）：每轮把实测耗时
与预算对表，超了记进轮次记录的 ``overruns``——只如实标记，不假装达标：
* ``capture``：唤醒命中 → 收命令结束的实测时长（只记数，不设预算——
  这段取决于人什么时候开口，不是机器的账）；
* ``transcribe``：说完 → 转写出文字，预算随音频时长放宽
  （基础 2000ms + 每秒音频 250ms，本地小模型 CPU 口径）；
* ``first_audio``：回复就绪 → 第一句合成出声，预算 3000ms；
* ``turn_total``：唤醒 → 播报结束，预算 30000ms（处理器含 LLM，只作参考线）。

失败人话口径（``human_voice_error``）：引擎没装、云端没配、没听清、
超时——全部翻成用户能直接照着做的话，原始异常只进日志不进用户面。
状态机本身不吞异常细节：轮次记录里 ``error`` 存人话，日志存原文。

进程内单例（``current_session``）：状态供 ``/v1/voice/status`` 与
聊天工具 ``voice_status`` 同口径展示；未来主题中央语音钮的状态联动
读的也是这份状态（经状态接口）。会话默认不开——常听要用户在设置里
打开常驻监听后，由网关侧按开关启停（见 ``sync_with_listening_setting``）。
"""

from __future__ import annotations

import io
import logging
import os
import threading
import time
import wave
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterable, List, Optional

from src.common.engines import EngineUnavailableError

logger = logging.getLogger("liuhao.voice.session")

#: 状态名（前端联动与状态接口共用这套词）。
STATE_OFF = "off"
STATE_LISTENING = "listening"
STATE_AWAKE = "awake"
STATE_TRANSCRIBING = "transcribing"
STATE_THINKING = "thinking"
STATE_SPEAKING = "speaking"

#: 收命令时的静音判定：RMS 低于此值算静音（float32 口径，麦克风底噪之下）。
SILENCE_RMS = 0.01
#: 命令最长收多少秒（超了截断转写，不无限收）。
MAX_COMMAND_SECONDS = 30.0
#: 唤醒后等开口的窗口：窗口内全是静音就当误唤/没想好，回常听。
AWAKE_WINDOW_SECONDS = 6.0
#: 收命令时，说完判定：连续静音达到此秒数算说完。
END_SILENCE_SECONDS = 0.8

_BUDGET_DEFAULTS = {
    "first_audio": 3000,
    "turn_total": 30000,
}


def _budget_ms(name: str) -> int:
    raw = (os.environ.get(f"LIUHAO_VOICE_BUDGET_{name.upper()}_MS") or "").strip()
    if raw.isdigit() and int(raw) > 0:
        return int(raw)
    return _BUDGET_DEFAULTS[name]


def transcribe_budget_ms(audio_seconds: float) -> int:
    """转写预算：基础 2000ms + 每秒音频 250ms（环境变量可整体覆盖）。"""
    raw = (os.environ.get("LIUHAO_VOICE_BUDGET_TRANSCRIBE_MS") or "").strip()
    if raw.isdigit() and int(raw) > 0:
        return int(raw)
    return int(2000 + 250 * max(0.0, audio_seconds))


# --------------------------------------------------------------------------- #
# 失败人话
# --------------------------------------------------------------------------- #

PROMPT_NOT_HEARD = "没听清，请再说一遍。"
PROMPT_NO_COMMAND = "我在呢，您说。"
PROMPT_TIMEOUT = "这次处理有点久，请再说一遍。"
PROMPT_STT_DOWN = "语音识别还没装好，暂时听不了话。"
PROMPT_TTS_DOWN = "语音播报还没装好，这次先看文字吧。"
PROMPT_GENERIC = "语音这边出了点问题，请再试一次。"


def human_voice_error(exc: BaseException, stage: str = "") -> str:
    """把语音链路的异常翻成人话：用户看得懂、知道下一步做什么。

    stage = 'stt' | 'tts' | 'wake' | ''（决定落在哪句兜底上）。
    引擎未装类（EngineUnavailableError）直接用它自带的人话 + 安装提示。
    """
    if isinstance(exc, EngineUnavailableError):
        hint = f" {exc.install_hint}" if exc.install_hint else ""
        return f"{exc.detail}{hint}".strip()
    if isinstance(exc, TimeoutError):
        return PROMPT_TIMEOUT
    text = str(exc) or ""
    if "没有返回文字" in text or "全是静音" in text:
        return PROMPT_NOT_HEARD
    if stage == "stt":
        return "语音识别失败了，请再说一遍；反复失败就先用打字。"
    if stage == "tts":
        return PROMPT_TTS_DOWN
    if stage == "wake":
        return "唤醒词引擎还没准备好，请先在设置里把唤醒模型下载好。"
    return PROMPT_GENERIC


# --------------------------------------------------------------------------- #
# 音频小工具（标准库 + numpy，与 stt.py 的备用解码同口径）
# --------------------------------------------------------------------------- #


def samples_to_wav_bytes(samples: Any, sample_rate: int = 16000) -> bytes:
    """float32 单声道样本 → 16-bit PCM wav 字节（喂给 STT provider）。"""
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


def wav_bytes_to_chunks(data: bytes, chunk_seconds: float = 0.1) -> List[Any]:
    """wav 字节 → float32 样本块列表（演练注入用；只支持 16-bit PCM wav）。"""
    import numpy as np

    with wave.open(io.BytesIO(data), "rb") as wav:
        sample_rate = wav.getframerate()
        channels = wav.getnchannels()
        width = wav.getsampwidth()
        frames = wav.readframes(wav.getnframes())
    if width != 2:
        raise ValueError("只支持 16-bit PCM wav 的注入解码。")
    pcm = np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0
    if channels > 1:
        pcm = pcm.reshape(-1, channels).mean(axis=1)
    if sample_rate != 16000:
        target = int(len(pcm) * 16000 / sample_rate)
        pcm = np.interp(
            np.linspace(0, len(pcm), target), np.arange(len(pcm)), pcm
        ).astype(np.float32)
    step = max(1, int(16000 * chunk_seconds))
    return [pcm[i : i + step] for i in range(0, len(pcm), step)]


def _rms(chunk: Any) -> float:
    import numpy as np

    arr = np.asarray(chunk, dtype=np.float32)
    if arr.size == 0:
        return 0.0
    return float(np.sqrt(np.mean(arr * arr)))


# --------------------------------------------------------------------------- #
# 状态机
# --------------------------------------------------------------------------- #


@dataclass
class TurnRecord:
    """一轮语音对话的实测记录（状态轨迹 + 各段耗时 + 预算对表）。"""

    started_at: float = 0.0
    states: List[str] = field(default_factory=list)
    transcript: str = ""
    reply_chars: int = 0
    timings_ms: Dict[str, Optional[float]] = field(
        default_factory=lambda: {
            "capture": None,
            "transcribe": None,
            "think": None,
            "first_audio": None,
            "turn_total": None,
        }
    )
    overruns: List[str] = field(default_factory=list)
    prompt: str = ""  # 本轮给用户的人话提示（没听清/失败时才有）
    error: str = ""  # 人话版错误（原始异常只进日志）


class VoiceSession:
    """一台语音会话状态机。线程安全的状态读写；run_stream 阻塞式跑流。"""

    def __init__(
        self,
        *,
        spot_fn: Optional[Callable[[Any, int], bool]] = None,
        stt_provider: Any = None,
        tts_provider: Any = None,
        command_handler: Optional[Callable[[str], str]] = None,
        audio_out: Optional[Callable[[bytes], None]] = None,
        prompt_out: Optional[Callable[[str], None]] = None,
        sample_rate: int = 16000,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._spot_fn = spot_fn
        self._stt = stt_provider
        self._tts = tts_provider
        self._handler = command_handler
        self._audio_out = audio_out or (lambda _b: None)
        self._prompt_out = prompt_out
        self._sample_rate = sample_rate
        self._clock = clock
        self._lock = threading.Lock()
        self._state = STATE_OFF
        self._state_since = clock()
        self._last_turn: Optional[TurnRecord] = None
        self._last_prompt = ""
        self._turns_done = 0
        self._running = False

    # -- 状态读写 -------------------------------------------------------- #

    def _set_state(self, state: str, turn: Optional[TurnRecord] = None) -> None:
        with self._lock:
            self._state = state
            self._state_since = self._clock()
            if turn is not None and (not turn.states or turn.states[-1] != state):
                turn.states.append(state)

    @property
    def state(self) -> str:
        with self._lock:
            return self._state

    def status(self) -> Dict[str, Any]:
        with self._lock:
            last = self._last_turn
            return {
                "state": self._state,
                "state_seconds": round(self._clock() - self._state_since, 1),
                "running": self._running,
                "turns_done": self._turns_done,
                "last_prompt": self._last_prompt,
                "last_turn": None
                if last is None
                else {
                    "transcript": last.transcript,
                    "reply_chars": last.reply_chars,
                    "states": list(last.states),
                    "timings_ms": dict(last.timings_ms),
                    "overruns": list(last.overruns),
                    "prompt": last.prompt,
                    "error": last.error,
                },
                "budgets_ms": {
                    "first_audio": _budget_ms("first_audio"),
                    "turn_total": _budget_ms("turn_total"),
                    "transcribe_base": 2000,
                    "transcribe_per_audio_second": 250,
                },
            }

    # -- 依赖（懒加载默认值，演练时全注入） ------------------------------- #

    def _spot(self, samples: Any) -> bool:
        if self._spot_fn is not None:
            return bool(self._spot_fn(samples, self._sample_rate))
        from .wake import spot_samples

        return bool(spot_samples(samples, self._sample_rate))

    def _stt_provider(self) -> Any:
        if self._stt is None:
            from .providers import get_stt_provider

            self._stt = get_stt_provider()
        return self._stt

    def _tts_provider(self) -> Any:
        if self._tts is None:
            from .providers import get_tts_provider

            self._tts = get_tts_provider()
        return self._tts

    def _emit_prompt(self, text: str, turn: TurnRecord) -> None:
        """人话提示出口：优先外部注入，其次尝试 TTS 播报，最后只记录。"""
        turn.prompt = text
        with self._lock:
            self._last_prompt = text
        if self._prompt_out is not None:
            try:
                self._prompt_out(text)
                return
            except Exception:  # noqa: BLE001 - 提示出口坏了不许拖垮状态机
                logger.warning("语音提示出口失败", exc_info=True)
                return
        try:
            result = self._tts_provider().synthesize(text)
            self._audio_out(result.wav_bytes)
        except Exception:  # noqa: BLE001 - 播不出来就只记录（状态里能看到）
            logger.info("人话提示未能播报（TTS 不可用）：%s", text)

    # -- 主循环 ------------------------------------------------------------ #

    def run_stream(self, chunks: Iterable[Any]) -> None:
        """阻塞式跑完一条音频块流：常听 → 唤醒 → 收命令 → 转写 → 处理
        → 播报 → 回常听，直到流结束或 stop() 被调用。"""
        with self._lock:
            self._running = True
        self._set_state(STATE_LISTENING)
        try:
            iterator = iter(chunks)
            for chunk in iterator:
                if not self._running:
                    break
                if chunk is None or len(chunk) == 0:
                    continue
                if not self._spot(chunk):
                    continue
                self._run_one_turn(iterator)
                if not self._running:
                    break
                self._set_state(STATE_LISTENING)
        finally:
            with self._lock:
                self._running = False
            self._set_state(STATE_OFF)

    def stop(self) -> None:
        with self._lock:
            self._running = False

    def _run_one_turn(self, iterator: Iterable[Any]) -> None:
        import numpy as np

        turn = TurnRecord(started_at=self._clock())
        wake_at = self._clock()
        self._set_state(STATE_AWAKE, turn)

        # 收命令：窗口内等开口，开口后收到说完为止（或最长时长截断）。
        captured: List[Any] = []
        heard_speech = False
        silence_run = 0.0
        waited = 0.0
        chunk_seconds = 0.0
        for chunk in iterator:
            if not self._running:
                break
            if chunk is None or len(chunk) == 0:
                continue
            chunk_seconds = len(chunk) / self._sample_rate
            loud = _rms(chunk) >= SILENCE_RMS
            if not heard_speech:
                waited += chunk_seconds
                if loud:
                    heard_speech = True
                    captured.append(chunk)
                elif waited >= AWAKE_WINDOW_SECONDS:
                    break  # 唤醒后没人说话：当误唤，安静回常听
                continue
            captured.append(chunk)
            if loud:
                silence_run = 0.0
            else:
                silence_run += chunk_seconds
                if silence_run >= END_SILENCE_SECONDS:
                    break
            if sum(len(c) for c in captured) / self._sample_rate >= MAX_COMMAND_SECONDS:
                break

        capture_done = self._clock()
        # capture = 唤醒→收命令结束的实测时长（含等人和说话，只记数不设
        # 预算——这段长短取决于人开口多快，不是机器的账）。
        turn.timings_ms["capture"] = round((capture_done - wake_at) * 1000, 1)
        if not heard_speech or not captured:
            with self._lock:
                self._last_turn = turn
            return

        audio = np.concatenate(captured) if len(captured) > 1 else captured[0]
        audio_seconds = len(audio) / self._sample_rate

        # 转写。
        self._set_state(STATE_TRANSCRIBING, turn)
        t0 = self._clock()
        try:
            result = self._stt_provider().transcribe(
                samples_to_wav_bytes(audio, self._sample_rate),
                "command.wav",
                "zh",
            )
            text = (result.text or "").strip()
        except Exception as exc:  # noqa: BLE001 - 翻成人话提示，不炸会话
            logger.warning("语音转写失败：%s", exc)
            turn.error = human_voice_error(exc, stage="stt")
            turn.timings_ms["transcribe"] = round((self._clock() - t0) * 1000, 1)
            self._finish_turn(turn)
            self._emit_prompt(turn.error, turn)
            return
        turn.timings_ms["transcribe"] = round((self._clock() - t0) * 1000, 1)
        if turn.timings_ms["transcribe"] > transcribe_budget_ms(audio_seconds):
            turn.overruns.append("transcribe")
        turn.transcript = text
        if not text:
            self._finish_turn(turn)
            self._emit_prompt(PROMPT_NOT_HEARD, turn)
            return

        # 处理（命令 → 回复）。
        self._set_state(STATE_THINKING, turn)
        t0 = self._clock()
        try:
            reply = ""
            if self._handler is not None:
                reply = (self._handler(text) or "").strip()
        except Exception as exc:  # noqa: BLE001
            logger.warning("语音命令处理失败：%s", exc)
            turn.error = human_voice_error(exc)
            turn.timings_ms["think"] = round((self._clock() - t0) * 1000, 1)
            self._finish_turn(turn)
            self._emit_prompt(PROMPT_GENERIC, turn)
            return
        turn.timings_ms["think"] = round((self._clock() - t0) * 1000, 1)
        turn.reply_chars = len(reply)
        if not reply:
            self._finish_turn(turn)
            return

        # 播报：逐句合成，第一句出声即记 first_audio。
        self._set_state(STATE_SPEAKING, turn)
        from .tts import split_sentences

        t0 = self._clock()
        first = True
        try:
            for sentence in split_sentences(reply):
                synth = self._tts_provider().synthesize(sentence)
                if first:
                    turn.timings_ms["first_audio"] = round(
                        (self._clock() - t0) * 1000, 1
                    )
                    if turn.timings_ms["first_audio"] > _budget_ms("first_audio"):
                        turn.overruns.append("first_audio")
                    first = False
                self._audio_out(synth.wav_bytes)
        except Exception as exc:  # noqa: BLE001
            logger.warning("语音播报失败：%s", exc)
            turn.error = human_voice_error(exc, stage="tts")
            self._finish_turn(turn)
            self._emit_prompt(PROMPT_TTS_DOWN, turn)
            return
        self._finish_turn(turn)

    def _finish_turn(self, turn: TurnRecord) -> None:
        turn.timings_ms["turn_total"] = round((self._clock() - turn.started_at) * 1000, 1)
        if turn.timings_ms["turn_total"] > _budget_ms("turn_total"):
            turn.overruns.append("turn_total")
        with self._lock:
            self._last_turn = turn
            self._turns_done += 1


# --------------------------------------------------------------------------- #
# 进程内单例 + 与常驻监听开关的衔接
# --------------------------------------------------------------------------- #

_SESSION: Optional[VoiceSession] = None
_SESSION_LOCK = threading.Lock()
_SESSION_THREAD: Optional[threading.Thread] = None


def current_session() -> VoiceSession:
    global _SESSION
    with _SESSION_LOCK:
        if _SESSION is None:
            _SESSION = VoiceSession()
        return _SESSION


def session_status() -> Dict[str, Any]:
    """会话状态（状态接口/聊天工具共用；从未启动时如实报 off）。"""
    return current_session().status()


def start_session_background(
    chunks: Optional[Iterable[Any]] = None,
    *,
    command_handler: Optional[Callable[[str], str]] = None,
) -> Dict[str, Any]:
    """后台起一台会话状态机。chunks 为空 = 真麦克风采集（wake 同款）。

    麦克风起不来（没组件/没设备）时不假装在听：返回人话错误。
    """
    global _SESSION_THREAD
    session = current_session()
    if command_handler is not None:
        session._handler = command_handler  # noqa: SLF001 - 同模块装配点
    with _SESSION_LOCK:
        if _SESSION_THREAD is not None and _SESSION_THREAD.is_alive():
            return {"started": False, "reason": "already_running", **session.status()}
    if chunks is None:
        from .wake import _LISTENER_STOP, _mic_chunks

        chunks = _mic_chunks(_LISTENER_STOP)

        def _guarded() -> Iterable[Any]:
            try:
                yield from chunks
            except EngineUnavailableError as exc:
                session._emit_prompt(  # noqa: SLF001
                    human_voice_error(exc, stage="wake"), TurnRecord()
                )
                raise

        chunks = _guarded()
    thread = threading.Thread(
        target=session.run_stream, args=(chunks,), name="voice-session", daemon=True
    )
    with _SESSION_LOCK:
        _SESSION_THREAD = thread
    thread.start()
    return {"started": True, **session.status()}


def stop_session() -> Dict[str, Any]:
    session = current_session()
    session.stop()
    return session.status()
