"""P2-2：删除模型后，密钥密文不得还能从库文件或 WAL 里捞出来。

``delete`` 只做 ``DELETE`` 时，被释放的页仍是"空闲但内容完好"，加上连接用了
WAL，旧的 ``-wal`` 帧还会留一份副本；拿到 ``model_config.key`` 就能离线解开。
这里用真实字节检索验证修复：``secure_delete=ON`` + 删除后 ``wal_checkpoint(TRUNCATE)``。
"""
from __future__ import annotations

import sqlite3

from src.ai.model_config import ModelConfigStore


def _read_ciphertext(db_path, model_id: str) -> str:
    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute(
            "SELECT api_key_enc FROM ai_model_configs WHERE id = ?", (model_id,)
        ).fetchone()
    finally:
        conn.close()
    assert row and row[0], "创建后应有密文"
    return str(row[0])


def test_delete_leaves_no_ciphertext_in_db_or_wal(tmp_path):
    db_path = tmp_path / "model_registry.sqlite3"
    store = ModelConfigStore(
        db_path=str(db_path), key_path=str(tmp_path / "model_config.key")
    )
    created = store.create(
        {
            "name": "secure-delete-check",
            "provider": "openai",
            "model": "gpt-4o-mini",
            "api_key": "sk-secure-delete-proof-0123456789",
        },
        "admin",
    )

    cipher = _read_ciphertext(db_path, created["id"])
    assert cipher.startswith("enc:v1:")

    deleted = store.delete(created["id"], "admin")
    assert deleted["deleted"] == created["id"]

    needle = cipher.encode("utf-8")
    plain = b"sk-secure-delete-proof-0123456789"
    for suffix in ("", "-wal", "-shm"):
        candidate = db_path.with_name(db_path.name + suffix)
        if not candidate.exists():
            continue
        raw = candidate.read_bytes()
        assert needle not in raw, f"{candidate.name} 仍残留被删密钥的密文"
        assert plain not in raw, f"{candidate.name} 残留明文密钥"

    conn = sqlite3.connect(db_path)
    try:
        assert conn.execute(
            "SELECT COUNT(*) FROM ai_model_configs WHERE id = ?", (created["id"],)
        ).fetchone()[0] == 0
    finally:
        conn.close()
