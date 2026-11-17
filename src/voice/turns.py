"""语音轮次记录（任务 7 / 7-5 第 1 步：半双工连续对话）。

只记**文字轮次的诊断信息**，不存音频：轮次号、状态转换时刻、
转写文字、回复字数、各段耗时（说完检测 / 转写 / 首字 / 首音）、
是否被打断。用途是验收时对表（7-5 §5.4 的 A–D 四段延迟），
不是第二套聊天历史——真正的历史仍在聊天会话里。

进程内内存记录（最多 200 轮，轮转丢弃）；重启即丢，不落盘。
"""

from __future__ import annotations

import threading
import time
import uuid
from typing import Any, Dict, List, Optional

_MAX_TURNS = 200

_lock = threading.Lock()
_turns: List[Dict[str, Any]] = []


def _now() -> float:
    return time.time()


def start_turn(conversation_id: str) -> Dict[str, Any]:
    """开始一轮：返回轮次记录（调用方随后补各段时间戳）。"""
    turn = {
        "turn_id": f"vturn_{uuid.uuid4().hex[:12]}",
        "conversation_id": conversation_id,
        "started_at": _now(),
        "transcript": "",
        "reply_chars": 0,
        "interrupted": False,
        "interrupt_reason": "",
        "timings_ms": {
            "speech_end_to_transcribe": None,
            "transcribe": None,
            "first_token": None,
            "first_audio": None,
        },
        "events": [{"state": "listening", "at": _now()}],
        "ended_at": None,
    }
    with _lock:
        _turns.append(turn)
        del _turns[: -_MAX_TURNS]
    return turn


def note_event(turn_id: str, state: str) -> None:
    with _lock:
        for turn in _turns:
            if turn["turn_id"] == turn_id:
                turn["events"].append({"state": state, "at": _now()})
                return


def note_timing(turn_id: str, key: str, value_ms: float) -> None:
    with _lock:
        for turn in _turns:
            if turn["turn_id"] == turn_id:
                turn["timings_ms"][key] = value_ms
                return


def finish_turn(
    turn_id: str,
    *,
    transcript: str = "",
    reply_chars: int = 0,
    interrupted: bool = False,
    interrupt_reason: str = "",
) -> Optional[Dict[str, Any]]:
    with _lock:
        for turn in _turns:
            if turn["turn_id"] == turn_id:
                if transcript:
                    turn["transcript"] = transcript
                turn["reply_chars"] = reply_chars
                turn["interrupted"] = interrupted
                turn["interrupt_reason"] = interrupt_reason
                turn["ended_at"] = _now()
                return dict(turn)
    return None


def list_turns(conversation_id: str, limit: int = 20) -> List[Dict[str, Any]]:
    with _lock:
        matching = [t for t in _turns if t["conversation_id"] == conversation_id]
    return [dict(t) for t in matching[-max(1, min(100, limit)):]]


def reset_turns() -> None:
    """测试用：清空内存记录。"""
    with _lock:
        _turns.clear()
