"""状态机、连续失败计数、锁死与 known-good 记录。

所有写操作都落到 ``D:\\LiuHaoSelfUpgrade\\broker\\``（测试/开发环境可覆盖）。
同一账号下这些文件挡不住恶意篡改，只防误操作和并发——与文档口径一致。
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .manifest import canonical_json, sha256_bytes


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


STATES = (
    "IDLE",
    "REQUESTED",
    "PREFLIGHT",
    "GATES",
    "PACKAGE",
    "INSTALL",
    "SMOKE",
    "DONE",
    "ROLLED_BACK",
    "FAILED",
    "LOCKED",
    "BLOCKED_ENV",
)
TRANSIENT = {"REQUESTED", "PREFLIGHT", "GATES", "PACKAGE", "INSTALL", "SMOKE"}


@dataclass
class StateSnapshot:
    state: str = "IDLE"
    consecutive_failures: int = 0
    max_consecutive_failures: int = 3
    attempt_id: str = ""
    last_error: str = ""
    updated_at: str = field(default_factory=_now)
    history: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "consecutive_failures": self.consecutive_failures,
            "max_consecutive_failures": self.max_consecutive_failures,
            "attempt_id": self.attempt_id,
            "last_error": self.last_error,
            "updated_at": self.updated_at,
            "history": self.history[-50:],
        }


class StateStore:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.state_path = self.root / "state.json"
        self.journal_path = self.root / "journal.jsonl"
        self.known_good_path = self.root / "known-good.json"

    # ---------------------------------------------------------------- 状态
    def load(self) -> StateSnapshot:
        if not self.state_path.is_file():
            return StateSnapshot()
        data = json.loads(self.state_path.read_text(encoding="utf-8-sig"))
        snap = StateSnapshot(
            state=data.get("state", "IDLE"),
            consecutive_failures=int(data.get("consecutive_failures", 0)),
            max_consecutive_failures=int(data.get("max_consecutive_failures", 3)),
            attempt_id=data.get("attempt_id", ""),
            last_error=data.get("last_error", ""),
            updated_at=data.get("updated_at", ""),
            history=list(data.get("history", [])),
        )
        # 崩溃恢复：上次停在瞬态，说明进程中途死了，按一次失败处理，避免无限重试。
        if snap.state in TRANSIENT:
            snap.consecutive_failures += 1
            snap.last_error = f"检测到上次未完成的 {snap.state}，按失败计"
            snap.state = "LOCKED" if snap.consecutive_failures >= snap.max_consecutive_failures else "FAILED"
            snap.history.append({"at": _now(), "event": "crash_recovery", "detail": snap.last_error})
            self.save(snap)
        return snap

    def save(self, snap: StateSnapshot) -> None:
        snap.updated_at = _now()
        payload = snap.to_dict()
        tmp = self.state_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, self.state_path)

    def enter(self, snap: StateSnapshot, state: str, detail: str = "") -> StateSnapshot:
        if state not in STATES:
            raise ValueError(f"未知状态 {state}")
        snap.state = state
        if detail:
            snap.last_error = detail
        snap.history.append({"at": _now(), "event": "enter", "state": state, "detail": detail})
        self.save(snap)
        self.journal({"event": "enter", "state": state, "detail": detail})
        return snap

    def success(self, snap: StateSnapshot, detail: str = "") -> StateSnapshot:
        snap.state = "DONE"
        snap.consecutive_failures = 0
        snap.last_error = detail
        snap.history.append({"at": _now(), "event": "success", "detail": detail})
        self.save(snap)
        self.journal({"event": "success", "detail": detail})
        return snap

    def failure(self, snap: StateSnapshot, detail: str) -> StateSnapshot:
        snap.consecutive_failures += 1
        snap.last_error = detail
        if snap.consecutive_failures >= snap.max_consecutive_failures:
            snap.state = "LOCKED"
            event = "locked"
        else:
            snap.state = "FAILED"
            event = "failure"
        snap.history.append({"at": _now(), "event": event, "detail": detail})
        self.save(snap)
        self.journal({"event": event, "failures": snap.consecutive_failures, "detail": detail})
        return snap

    def unlock(self, snap: StateSnapshot, reason: str) -> StateSnapshot:
        if not reason.strip():
            raise ValueError("解锁必须写明理由")
        snap.state = "IDLE"
        snap.consecutive_failures = 0
        snap.last_error = ""
        snap.history.append({"at": _now(), "event": "unlock", "detail": reason})
        self.save(snap)
        self.journal({"event": "unlock", "reason": reason})
        return snap

    # ---------------------------------------------------------------- 已知可用版本
    def known_good(self) -> dict[str, Any] | None:
        if not self.known_good_path.is_file():
            return None
        return json.loads(self.known_good_path.read_text(encoding="utf-8-sig"))

    def set_known_good(self, record: dict[str, Any]) -> None:
        payload = dict(record)
        payload.setdefault("recorded_at", _now())
        tmp = self.known_good_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, self.known_good_path)
        self.journal({"event": "known_good", **payload})

    # ---------------------------------------------------------------- 审计日志
    def journal(self, record: dict[str, Any]) -> None:
        prev = ""
        if self.journal_path.is_file():
            lines = [ln for ln in self.journal_path.read_text(encoding="utf-8-sig").splitlines() if ln.strip()]
            if lines:
                try:
                    prev = json.loads(lines[-1]).get("line_sha256", "")
                except json.JSONDecodeError:
                    prev = ""
        body = dict(record)
        body.setdefault("at", _now())
        body["prev"] = prev
        body["line_sha256"] = sha256_bytes(canonical_json(body).encode("utf-8"))
        with open(self.journal_path, "a", encoding="utf-8") as handle:
            handle.write(canonical_json(body) + "\n")
