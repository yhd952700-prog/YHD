"""Goal archive: the hot file stays bounded, archived goals stay readable.

Soak evidence: ``liuhao_goals.json`` grew 0.17 -> 4.96 MB (+2.42 MB/h) and
every save rewrote the whole file, so ``POST /v1/goals`` p50 grew with history.
Terminal goals now move to append-only JSONL shards (gzipped when stale/oversized)
while the hot file keeps at most ``LIUHAO_GOALS_MAX_ACTIVE`` entries.
Read paths (list / detail) must keep returning them.
"""

from __future__ import annotations

import json
import os

from src.gateway.ai_management import AIStateManager


def _entry(index: int, state: str = "completed") -> dict:
    return {
        "goal_id": f"g{index:04d}",
        "state": state,
        "natural_language": f"goal {index}",
        "scope": "L1",
        "created_at": 1.0 + index,
        "tasks": [],
        "project_id": "proj",
        "owner_account_id": "owner",
        "replan_suggested": False,
        "error": None,
    }


def _manager(tmp_path, monkeypatch, limit: int = 16):
    monkeypatch.setenv("LIUHAO_WORKSPACE_ROOT", str(tmp_path))
    monkeypatch.setenv("LIUHAO_GOALS_MAX_ACTIVE", str(limit))
    AIStateManager._instance = None
    return AIStateManager()


def _goals_path(tmp_path) -> str:
    return str(tmp_path / "liuhao_goals.json")


def test_hot_file_is_bounded_and_archived_goals_remain_listable(
        tmp_path, monkeypatch):
    path = _goals_path(tmp_path)
    goals = {f"g{i:04d}": _entry(i) for i in range(200)}
    for i in range(200, 205):
        goals[f"g{i:04d}"] = _entry(i, state="planned")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(goals, fh)

    mgr = _manager(tmp_path, monkeypatch, limit=16)
    try:
        # Startup compaction: terminal goals past the budget are archived.
        with mgr._goal_lock:
            assert len(mgr._goals) <= 16
        hot_ids = set(mgr._goals)
        assert {f"g{i:04d}" for i in range(200, 205)} <= hot_ids

        full, index = mgr._goals_archive_paths()
        assert os.path.exists(full)
        assert os.path.exists(index)
        archived_lines = sum(
            1 for _ in mgr._read_archive_lines(full))
        hot_terminal = sum(
            1 for entry in mgr._goals.values()
            if entry.get("state") == "completed")
        assert archived_lines == 200 - hot_terminal

        # List view returns hot + archived (read-path compatible).
        listed = mgr.list_goals()
        assert {item["goal_id"] for item in listed} == {
            f"g{i:04d}" for i in range(205)
        }
        archived_items = [item for item in listed if item.get("archived")]
        assert archived_items
        assert all("tasks" not in item for item in archived_items)

        # Detail lookup for an archived goal returns the FULL entry.
        archived_id = archived_items[0]["goal_id"]
        detail = mgr.get_goal(archived_id)
        assert detail["goal_id"] == archived_id
        assert detail["tasks"] == []
    finally:
        AIStateManager._instance = None


def test_archive_survives_restart_and_stale_shards_are_gzipped(
        tmp_path, monkeypatch):
    path = _goals_path(tmp_path)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump({f"g{i:04d}": _entry(i) for i in range(100)}, fh)

    mgr = _manager(tmp_path, monkeypatch, limit=16)
    try:
        mgr.list_goals()
    finally:
        AIStateManager._instance = None

    # A second process must still see the archived goals after restart.
    mgr2 = _manager(tmp_path, monkeypatch, limit=16)
    try:
        listed = mgr2.list_goals()
        assert len(listed) == 100
        assert any(item.get("archived") for item in listed)

        # A shard from another day is compressed in place; readers transparently
        # open *.jsonl.gz.
        full, _index = mgr2._goals_archive_paths()
        directory = os.path.dirname(full)
        stem = os.path.basename(full).split(".archive-")[0]
        stale = os.path.join(directory, f"{stem}.archive-20200101.jsonl")
        os.makedirs(os.path.dirname(stale), exist_ok=True)
        with open(stale, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(_entry(999)) + "\n")
        mgr2._compress_stale_archive_shards()
        assert not os.path.exists(stale)
        assert os.path.exists(stale + ".gz")
        lines = list(mgr2._read_archive_lines(stale + ".gz"))
        assert len(lines) == 1
    finally:
        AIStateManager._instance = None
