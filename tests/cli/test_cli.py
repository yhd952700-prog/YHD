"""Tests for the minimal LIUHAO developer CLI scaffold (src/cli).

Read-only subcommands only. Tests build a throwaway synthetic audit DB and
assert the CLI returns 0 and emits parseable JSON. Never touches the
production audit_store.db or any Evidence file.
"""

import json
import os
import sqlite3
import tempfile

import pytest

from src.cli import build_parser, main


def _make_audit_db(path: str, *, forked: bool = False) -> None:
    conn = sqlite3.connect(path)
    try:
        conn.executescript(
            """
            CREATE TABLE audit_events (
                event_id TEXT PRIMARY KEY, event_hash TEXT NOT NULL,
                prev_event_hash TEXT, seq INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE chain_state (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                last_seq INTEGER NOT NULL, last_hash TEXT
            );
            """
        )
        prev = None
        n = 7
        for s in range(1, n + 1):
            h = f"h{s}"
            conn.execute(
                "INSERT INTO audit_events(event_id,event_hash,prev_event_hash,seq) "
                "VALUES(?,?,?,?)",
                (f"e{s}", h, prev, s),
            )
            prev = h
        if forked:
            # Add a duplicate seq (forked) row.
            conn.execute(
                "INSERT INTO audit_events(event_id,event_hash,prev_event_hash,seq) "
                "VALUES(?,?,?,?)",
                ("e6b", "h6b", "h5", 6),
            )
            conn.execute(
                "UPDATE chain_state SET last_seq=7, last_hash='h7'"
            )
        else:
            conn.execute(
                "INSERT INTO chain_state(id,last_seq,last_hash) VALUES(1,7,?)",
                (prev,),
            )
        conn.commit()
    finally:
        conn.close()


def test_build_parser_returns_parser():
    p = build_parser()
    assert p is not None
    assert p.prog == "liuhao-cli"


def test_cli_audit_detect_clean(capsys):
    db = tempfile.mktemp(suffix=".db")
    _make_audit_db(db, forked=False)
    try:
        rc = main(["audit", "detect", "--db", db])
        assert rc == 0
        out = capsys.readouterr().out
        report = json.loads(out)
        assert report["total_events"] == 7
        assert report["duplicate_seq_count"] == 0
        assert report["broken_joins_rowid"] == 0
        assert report["chain_state_consistent"] is True
    finally:
        if os.path.exists(db):
            os.remove(db)


def test_cli_audit_detect_forked(capsys):
    db = tempfile.mktemp(suffix=".db")
    _make_audit_db(db, forked=True)
    try:
        rc = main(["audit", "detect", "--db", db])
        assert rc == 0
        out = capsys.readouterr().out
        report = json.loads(out)
        assert report["total_events"] == 8  # 7 + 1 forked
        assert report["duplicate_seq_count"] == 1
        assert report["fork_seqs"] == [6]
    finally:
        if os.path.exists(db):
            os.remove(db)


def test_cli_bench_sizing(capsys):
    rc = main(
        [
            "bench",
            "sizing",
            "--events-per-day",
            "1000000",
            "--retention-days",
            "365",
            "--ceiling-eps",
            "5000",
        ]
    )
    assert rc == 0
    out = capsys.readouterr().out
    sizing = json.loads(out)
    assert sizing["total_events"] == 1_000_000 * 365
    assert "single_writer_bottleneck" in sizing


def test_cli_no_subcommand_errors():
    with pytest.raises(SystemExit):
        main([])
