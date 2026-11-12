"""Workflow checkpoint 的写入/读回类型一致性与 WAL 回归。

TOOL-10：`create_checkpoint` 曾把单个 node_id/edge_id 直接 `json.dumps` 成
JSON 字符串，读回时 `node_stack` / `remaining_edges` 变成 `str` 而不是
`List[str]`。这里锁死契约：写进去是什么类型，读回来必须还是什么类型。
"""

from __future__ import annotations

from src.workflow.checkpoint import CheckpointManager


def test_node_stack_and_edges_roundtrip_as_lists(tmp_path):
    manager = CheckpointManager(str(tmp_path / "checkpoints.db"))
    checkpoint_id = manager.create_checkpoint(
        workflow_id="wf-types",
        workflow_state={"step": 1},
        input_data={"q": "x"},
        output_data={"a": "y"},
        node_id="node-42",
        edge_id="edge-7",
        description="type roundtrip",
        tags=["t1"],
    )

    data = manager.get_checkpoint(checkpoint_id)
    assert data is not None
    assert data.node_stack == ["node-42"]
    assert data.remaining_edges == ["edge-7"]
    assert isinstance(data.node_stack, list)
    assert isinstance(data.remaining_edges, list)
    assert data.workflow_state == {"step": 1}
    assert data.done is False


def test_absent_ids_still_read_back_as_lists(tmp_path):
    manager = CheckpointManager(str(tmp_path / "checkpoints.db"))
    checkpoint_id = manager.create_checkpoint(
        workflow_id="wf-empty",
        workflow_state={},
        input_data={},
        output_data={},
    )

    data = manager.get_checkpoint(checkpoint_id)
    assert data is not None
    assert data.node_stack == []
    assert data.remaining_edges == []
    assert isinstance(data.node_stack, list)
    assert isinstance(data.remaining_edges, list)


def test_checkpoint_database_uses_wal(tmp_path):
    manager = CheckpointManager(str(tmp_path / "checkpoints.db"))
    mode = manager._conn.execute("PRAGMA journal_mode").fetchone()[0]
    assert str(mode).lower() == "wal"
