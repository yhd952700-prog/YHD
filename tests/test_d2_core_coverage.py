"""D 组补测试：健康 / 成本 / 知识库分块与持久化 / 诚实边界。

只覆盖本环境能 import 的模块（标准库 + 已装的 cryptography）：
chunker、health_check、cost_tracker、vector_store、external_anchor、
tpm_provider、backup_manager。需要 FastAPI/SQLAlchemy 等重依赖的模块
不在此列（见报告「仍无测试」清单）。
"""
from __future__ import annotations

import hashlib
import os
import sqlite3

import pytest

from src.backup.backup_manager import (
    BackupManager,
    BackupSource,
    BackupType,
    decrypt_backup_bytes,
    derive_backup_key,
    encrypt_backup_bytes,
)
from src.cost.cost_tracker import (
    AggregationPeriod,
    AlertLevel,
    CostCategory,
    CostTracker,
)
from src.health.health_check import (
    CheckType,
    FunctionHealthCheck,
    HealthCheckRegistry,
    HealthStatus,
)
from src.kernels.audit.dual_signature import DEFAULT_SIGN_ALG, SIGN_ALGORITHMS
from src.kernels.audit.external_anchor import (
    ExternalLogTransport,
    ExternalTransparencyAnchor,
    LocalReferenceLog,
    merkle_inclusion_proof,
    merkle_root,
    verify_inclusion_proof,
)
from src.knowledge.chunker import chunk_document_structured, chunk_text
from src.knowledge.vector_store import PersistentVectorStore, cosine_similarity
from src.security.evidence.tpm_provider import TpmTimestampProvider


# --------------------------------------------------------------------- #
# chunker
# --------------------------------------------------------------------- #
class TestChunkText:
    def test_empty_text_is_no_chunks(self):
        assert chunk_text("") == []
        assert chunk_text("", chunk_size=10) == []

    def test_short_text_is_one_chunk(self):
        assert chunk_text("hello world", chunk_size=512) == ["hello world"]

    def test_overlap_is_real_overlap_not_just_adjacent(self):
        chunks = chunk_text("abcdefghij", chunk_size=4, chunk_overlap=2)
        assert chunks[0] == "abcd"
        assert chunks[1] == "cdef"
        assert chunks[2] == "efgh"
        # 最后一个 chunk 是尾巴，不是凭空补的。
        assert chunks[-1] == "ij"

    def test_overlap_at_least_chunk_size_is_clamped(self):
        chunks = chunk_text("abcdefghij", chunk_size=4, chunk_overlap=4)
        assert chunks[0] == "abcd"
        assert all(len(c) <= 4 for c in chunks)

    def test_structured_chunking_keeps_heading_path_and_code_intact(self):
        text = (
            "# 第一章 部署\n\n第一段讲准备工作。\n\n"
            "```python\nprint('a')\nprint('b')\n```\n\n"
            "## 第二节 启动\n\n第二段讲启动步骤。\n"
        )
        chunks = chunk_document_structured(text, chunk_size=512)
        assert chunks, "should produce chunks"
        assert any("第一章" in c.context_header for c in chunks)
        code = [c for c in chunks if "print('a')" in c.text]
        assert len(code) == 1, "fenced code must never be split"
        assert "print('b')" in code[0].text
        assert not chunk_document_structured("   \n  ")
        with pytest.raises(ValueError):
            chunk_document_structured("x", chunk_size=0)


# --------------------------------------------------------------------- #
# health_check
# --------------------------------------------------------------------- #
class TestHealthCheck:
    def test_function_check_tuple_and_bool_forms(self):
        ok = FunctionHealthCheck("db", lambda: (HealthStatus.HEALTHY, "reachable"))
        assert ok.execute().status is HealthStatus.HEALTHY
        bad = FunctionHealthCheck("db", lambda: False)
        assert bad.execute().status is HealthStatus.UNHEALTHY

    def test_exception_is_unhealthy_when_critical_degraded_otherwise(self):
        def boom():
            raise RuntimeError("down")

        critical = FunctionHealthCheck("c", boom, critical=True)
        assert critical.execute().status is HealthStatus.UNHEALTHY
        assert "down" in critical.execute().message
        soft = FunctionHealthCheck("s", boom, critical=False)
        assert soft.execute().status is HealthStatus.DEGRADED

    def test_registry_aggregates_critical_vs_noncritical(self):
        registry = HealthCheckRegistry()
        registry.register(FunctionHealthCheck("ok", lambda: True))
        registry.register(
            FunctionHealthCheck("soft", lambda: False, critical=False)
        )
        result = registry.check_all()
        assert result.status is HealthStatus.DEGRADED
        assert result.healthy_count == 1
        assert result.unhealthy_count == 1

        registry.register(FunctionHealthCheck("hard", lambda: False, critical=True))
        result = registry.check_all()
        assert result.status is HealthStatus.UNHEALTHY

    def test_registry_history_records_each_run(self):
        registry = HealthCheckRegistry()
        registry.register(FunctionHealthCheck("ok", lambda: True))
        registry.check_all()
        registry.check_all()
        assert len(registry.get_history("ok")) == 2
        assert registry.get_history("missing") == []
        assert registry.check_one("missing") is None

    def test_check_type_filter(self):
        registry = HealthCheckRegistry()
        registry.register(
            FunctionHealthCheck("live", lambda: True, check_type=CheckType.LIVENESS)
        )
        registry.register(
            FunctionHealthCheck("ready", lambda: True, check_type=CheckType.READINESS)
        )
        assert registry.check_all(check_type=CheckType.LIVENESS).total_checks == 1


# --------------------------------------------------------------------- #
# cost_tracker
# --------------------------------------------------------------------- #
class TestCostTracker:
    def _tracker_with_budget(self):
        tracker = CostTracker()
        tracker.set_budget("monthly", 100.0, period=AggregationPeriod.MONTHLY)
        return tracker

    def test_record_and_totals_are_real_sums(self):
        tracker = CostTracker()
        tracker.record_cost(CostCategory.API_CALLS, 2.5, provider="openai")
        tracker.record_cost(CostCategory.API_CALLS, 1.5, provider="openai")
        tracker.record_cost(CostCategory.STORAGE, 10.0, provider="aws")
        assert tracker.total_cost() == pytest.approx(14.0)
        assert tracker.by_category()[CostCategory.API_CALLS] == pytest.approx(4.0)
        assert tracker.by_provider()["openai"] == pytest.approx(4.0)

    def test_negative_amount_rejected(self):
        with pytest.raises(ValueError):
            CostTracker().record_cost(CostCategory.COMPUTE, -1.0, provider="x")

    def test_budget_alerts_fire_at_50_80_100_with_right_levels(self):
        tracker = self._tracker_with_budget()
        tracker.record_cost(CostCategory.API_CALLS, 40.0, provider="openai")
        assert tracker.alerts == []
        tracker.record_cost(CostCategory.API_CALLS, 10.0, provider="openai")
        assert len(tracker.alerts) == 1
        assert tracker.alerts[0].level is AlertLevel.INFO
        tracker.record_cost(CostCategory.API_CALLS, 30.0, provider="openai")
        assert len(tracker.alerts) == 2
        assert tracker.alerts[1].level is AlertLevel.WARNING
        tracker.record_cost(CostCategory.API_CALLS, 20.0, provider="openai")
        assert len(tracker.alerts) == 3
        assert tracker.alerts[2].level is AlertLevel.CRITICAL
        budget = tracker.get_budget("monthly")
        assert budget is not None
        assert budget.utilization == pytest.approx(1.0)
        # is_over_budget 是严格大于：正好 100% 还不算超支。
        assert budget.is_over_budget is False
        tracker.record_cost(CostCategory.API_CALLS, 1.0, provider="openai")
        assert budget.is_over_budget is True

    def test_alerts_do_not_refire_for_the_same_threshold(self):
        tracker = self._tracker_with_budget()
        tracker.record_cost(CostCategory.API_CALLS, 60.0, provider="openai")
        tracker.record_cost(CostCategory.API_CALLS, 5.0, provider="openai")
        assert len(tracker.alerts) == 1  # 50% 只报一次

    def test_csv_export_contains_real_rows(self):
        tracker = CostTracker()
        tracker.record_cost(CostCategory.API_CALLS, 3.0, provider="openai")
        csv_text = tracker.to_csv()
        assert "api_calls" in csv_text and "3.0" in csv_text


# --------------------------------------------------------------------- #
# vector_store: 真实余弦分 + sqlite 持久化 + FTS 关键词
# --------------------------------------------------------------------- #
class TestVectorStorePersistence:
    def test_cosine_scores_are_real_not_fabricated(self):
        assert cosine_similarity([1.0, 0.0], [1.0, 0.0]) == pytest.approx(1.0)
        assert cosine_similarity([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)
        assert cosine_similarity([1.0, 0.0], [-1.0, 0.0]) == pytest.approx(-1.0)
        # 维度不一致返回 0.0 而不是静默截断后给出看似正常的分数。
        assert cosine_similarity([1.0, 0.0], [1.0, 0.0, 0.0]) == 0.0

    def test_persistent_store_survives_reopen_with_real_scores(self, tmp_path):
        db = str(tmp_path / "vectors.sqlite3")
        store = PersistentVectorStore(db)
        store.insert("doc-a#0", [1.0, 0.0], {"text": "知识库持久化指南", "document_id": "doc-a"})
        store.insert("doc-b#0", [0.0, 1.0], {"text": "无关内容", "document_id": "doc-b"})
        store.close()

        reopened = PersistentVectorStore(db)
        try:
            hits = reopened.search([1.0, 0.0], top_k=2)
            assert hits[0][0] == "doc-a#0"
            assert hits[0][1] == pytest.approx(1.0)
            assert hits[1][0] == "doc-b#0"
            assert hits[1][1] == pytest.approx(0.0)
            assert reopened.count() == 2
        finally:
            reopened.close()

    def test_keyword_path_finds_chinese_doc_when_vectors_tie(self, tmp_path):
        store = PersistentVectorStore(str(tmp_path / "vectors.sqlite3"))
        try:
            # 常量向量让向量路无法区分：只有 FTS 关键词路能拉开差距。
            store.insert("a#0", [1.0, 0.0], {"text": "普通概述", "document_id": "a"})
            store.insert(
                "b#0",
                [1.0, 0.0],
                {"text": "知识库持久化与向量检索说明", "document_id": "b"},
            )
            hits, info = store.hybrid_search("知识库", [1.0, 0.0], top_k=2)
            # 向量路打平，关键词路必须把正确 chunk 顶到第一；第二名是平局的另一篇。
            assert hits[0][0] == "b#0"
            assert "b#0" in [h[0] for h in hits]
            assert info["mode"] == "hybrid"
            assert info["fts_available"] is True
            assert info["status"] == "ok"
        finally:
            store.close()

    def test_delete_removes_from_disk_too(self, tmp_path):
        db = str(tmp_path / "vectors.sqlite3")
        store = PersistentVectorStore(db)
        store.insert("x#0", [1.0, 0.0], {"text": "待删", "document_id": "x"})
        assert store.delete("x#0") is True
        store.close()
        assert PersistentVectorStore(db).count() == 0


# --------------------------------------------------------------------- #
# 诚实边界：外部锚定抽象传输、TPM —— 只能是 NotImplementedError
# --------------------------------------------------------------------- #
class TestHonestBoundaries:
    def test_external_transport_base_raises_not_implemented(self):
        transport = ExternalLogTransport()
        with pytest.raises(NotImplementedError):
            transport.submit_root("ab" * 32)

    def test_merkle_proof_round_trip_and_tamper_fails(self):
        leaves = [bytes([i]) * 4 for i in range(5)]
        root = merkle_root(leaves)
        assert merkle_root(leaves) == root  # 确定性
        assert merkle_root(list(reversed(leaves))) != root  # 顺序敏感
        for i, leaf in enumerate(leaves):
            proof = merkle_inclusion_proof(leaves, i)
            assert verify_inclusion_proof(leaf, i, proof, len(leaves), root)[0]
        # 篡改一片叶子：证明失效。
        assert not verify_inclusion_proof(
            b"\xff\xff\xff\xff", 0, merkle_inclusion_proof(leaves, 0), len(leaves), root
        )[0]
        with pytest.raises(ValueError):
            merkle_root([])

    def test_reference_log_receipt_is_self_attested_not_independent(self):
        priv, pub = SIGN_ALGORITHMS[DEFAULT_SIGN_ALG].generate_keypair()
        log = LocalReferenceLog(log_priv=priv, log_pub=pub)
        anchor = ExternalTransparencyAnchor(
            external_pub_pem=pub, transport=log, self_attested=True
        )
        assert anchor.independent is False
        root_hex = hashlib.sha256(b"notary-root").hexdigest()
        receipt = anchor.publish(root_hex)
        assert receipt.self_attested is True
        ok, _reason = anchor.verify_receipt(receipt, root_hex)
        assert ok is True

    def test_tpm_provider_is_interface_only(self):
        provider = TpmTimestampProvider(tpm_handle="0x81000001")
        assert provider.authority == "tpm-unconfigured"
        with pytest.raises(NotImplementedError):
            provider.sign(b"data")
        with pytest.raises(NotImplementedError):
            provider.generate()
        with pytest.raises(NotImplementedError):
            provider.rotate()
        with pytest.raises(NotImplementedError):
            provider.public_key_pem()
        with pytest.raises(NotImplementedError):
            provider.timestamp(b"digest")


# --------------------------------------------------------------------- #
# backup_manager：真实 AES-256-GCM 加密落盘
# --------------------------------------------------------------------- #
class TestBackupEncryption:
    def _manager(self, tmp_path, **kwargs):
        src = tmp_path / "data.txt"
        src.write_text("hello backup", encoding="utf-8")
        return BackupManager(
            backup_dir=tmp_path / "backups",
            source_paths={BackupSource.USER_DATA: src},
            **kwargs,
        )

    def test_encrypt_decrypt_bytes_round_trip(self):
        key = os.urandom(32)
        blob = encrypt_backup_bytes(b"secret payload", key)
        assert blob[:6] == b"LHBAK1"
        assert b"secret payload" not in blob  # 落盘字节里没有明文
        assert decrypt_backup_bytes(blob, key) == b"secret payload"

    def test_wrong_key_or_bad_magic_fails_closed(self):
        blob = encrypt_backup_bytes(b"x", os.urandom(32))
        with pytest.raises(ValueError):
            decrypt_backup_bytes(blob, os.urandom(32))
        with pytest.raises(ValueError):
            decrypt_backup_bytes(b"not-a-backup", os.urandom(32))
        with pytest.raises(ValueError):
            encrypt_backup_bytes(b"x", b"short")

    def test_derive_backup_key_is_deterministic_and_salted(self):
        salt = os.urandom(16)
        assert derive_backup_key("pw", salt) == derive_backup_key("pw", salt)
        assert derive_backup_key("pw", salt) != derive_backup_key("pw", os.urandom(16))
        assert len(derive_backup_key("pw", salt)) == 32
        with pytest.raises(ValueError):
            derive_backup_key("", salt)

    def test_encrypted_backup_round_trips_through_manager(self, tmp_path):
        key = derive_backup_key("correct-horse", b"fixed-salt-16b!")
        manager = self._manager(tmp_path, encryption_enabled=True, encryption_key=key)
        meta = manager.create_backup(backup_type=BackupType.FULL)
        assert meta.encrypted is True
        # file_path 是 manager 记的绝对路径
        import pathlib

        raw = pathlib.Path(meta.file_path).read_bytes()
        assert b"hello backup" not in raw
        assert manager.verify_backup(meta.backup_id) is True
        result = manager.restore_backup(meta.backup_id, target_dir=tmp_path / "restored")
        assert result.success is True
        assert (tmp_path / "restored" / "user_data").read_text(encoding="utf-8") == "hello backup"

    def test_encryption_without_key_refuses_and_writes_nothing(self, tmp_path):
        manager = self._manager(tmp_path, encryption_enabled=True)
        with pytest.raises(ValueError, match="encryption_key"):
            manager.create_backup()
        assert list((tmp_path / "backups").glob("*")) == []

    def test_key_without_flag_refuses_plaintext(self, tmp_path):
        manager = self._manager(tmp_path, encryption_key=os.urandom(32))
        with pytest.raises(ValueError, match="encryption_enabled=False"):
            manager.create_backup()

    def test_restore_without_key_fails_closed(self, tmp_path):
        key = os.urandom(32)
        manager = self._manager(tmp_path, encryption_enabled=True, encryption_key=key)
        meta = manager.create_backup()
        manager.encryption_key = None  # 模拟换机后密钥不在手里
        result = manager.restore_backup(meta.backup_id)
        assert result.success is False
        assert "encryption_key" in (result.error_message or "")
