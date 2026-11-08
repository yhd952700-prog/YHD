"""审计链密钥（HMAC）—— 让“拿到库写权限就整链重算”不再能伪造。

这里锁定三件事，对应交付要求：

1. 带密钥时，**整链重算的伪造必须被检出**（不是静默绿灯）；
2. 正常追加不受影响（仍能追加、仍能验过）；
3. 密钥缺失时是**第三种状态**：``ok=False`` + ``degraded=True`` +
   完整消息“缺少校验密钥，完整性只能做结构检查”，且结构检查结果单独给出。

另外覆盖：生产默认开启、开发默认关闭、局部降级（密钥链之后出现无密钥行）
同样不能给绿灯、密钥文件不进入备份/诊断包。
"""
from __future__ import annotations

import hashlib
import json
import sqlite3

import pytest

from src.kernels.audit import (
    AuditEventType,
    AuditScope,
    AuditStore,
    DEFAULT_HASH_ALG,
)
from src.kernels.audit import chain_key
from src.kernels.audit.hashutil import canonical_json
from src.upgrade.backups import is_protected_state_file


def _keyed_store(tmp_path, monkeypatch) -> tuple[AuditStore, object]:
    """密钥模式强制开启，密钥文件放在本用例自己的目录里。"""
    key_path = tmp_path / "audit_chain.key"
    monkeypatch.setenv("LIUHAO_AUDIT_CHAIN_KEY_MODE", "on")
    monkeypatch.setenv("LIUHAO_AUDIT_CHAIN_KEY_FILE", str(key_path))
    monkeypatch.delenv("LIUHAO_AUDIT_CHAIN_KEY", raising=False)
    monkeypatch.setattr(chain_key, "_cached_key", None)
    monkeypatch.setattr(chain_key, "_cache_loaded", False)
    store = AuditStore(str(tmp_path / "audit.db"))
    return store, key_path


def _log(store: AuditStore, count: int) -> None:
    for i in range(count):
        store.log_event(
            AuditEventType.ACCESS_CHECK, f"p{i}", AuditScope.L1, "allow",
            details={"op": f"op{i}"}, correlation_id=f"corr-{i}",
        )


def _forge_entire_chain_unkeyed(
    db_path: str, *, declare_keyed: bool = False
) -> None:
    """模拟拿到库写权限的攻击者：把所有行重算成无密钥 SHA-256。

    ``declare_keyed=True`` 时保留 ``hash_alg=hmac-sha256`` 的声明（攻击者没有
    密钥，算不出 HMAC，只能贴一个假的 SHA-256 哈希），用于证明“保留声明也
    一定会红”。
    """
    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute(
            "SELECT seq, event_id, event_type, principal_id, scope, timestamp, "
            "correlation_id, outcome, details, hash_alg FROM audit_events "
            "ORDER BY seq ASC"
        ).fetchall()
        prev_hash = None
        prev_link = None
        for (seq, event_id, event_type, principal_id, scope, timestamp,
             correlation_id, outcome, details_json, _alg) in rows:
            payload = {
                "event_id": event_id,
                "event_type": event_type,
                "principal_id": principal_id,
                "scope": scope,
                "timestamp": timestamp,
                "correlation_id": correlation_id,
                "outcome": outcome,
                "details": json.loads(details_json) if details_json else {},
            }
            event_hash = hashlib.sha256(
                canonical_json(payload).encode()).hexdigest()
            link = hashlib.sha256(
                ((prev_link or "") + event_hash).encode()).hexdigest()
            alg = "hmac-sha256" if declare_keyed else "sha256"
            conn.execute(
                "UPDATE audit_events SET event_hash = ?, prev_event_hash = ?, "
                "hash_alg = ?, link_hash = ? WHERE seq = ?",
                (event_hash, prev_hash, alg, link, seq),
            )
            prev_hash, prev_link = event_hash, link
        conn.execute(
            "UPDATE chain_state SET last_seq = ?, last_hash = ?, "
            "last_link_hash = ? WHERE id = 1",
            (rows[-1][0], prev_hash, prev_link),
        )
        # 派生 checkpoint 不能留下来替旧链背书。
        conn.execute("DELETE FROM verification_checkpoints")
        conn.commit()
    finally:
        conn.close()


def _patch_store(monkeypatch, store: AuditStore) -> None:
    """让网关的 ``_run_verification`` 用本用例的 store。"""
    import src.kernels.audit as audit_kernel

    monkeypatch.setattr(audit_kernel, "get_audit_store", lambda: store)


def _mixed_store(tmp_path, monkeypatch) -> tuple[AuditStore, object]:
    """模拟 0.1.0 老用户升级：先 2 条无密钥，升级后 1 条带密钥。"""
    key_path = tmp_path / "audit_chain.key"
    monkeypatch.setenv("LIUHAO_AUDIT_CHAIN_KEY_MODE", "off")
    monkeypatch.setenv("LIUHAO_AUDIT_CHAIN_KEY_FILE", str(key_path))
    monkeypatch.delenv("LIUHAO_AUDIT_CHAIN_KEY", raising=False)
    monkeypatch.setattr(chain_key, "_cached_key", None)
    monkeypatch.setattr(chain_key, "_cache_loaded", False)
    store = AuditStore(str(tmp_path / "audit.db"))
    _log(store, 2)
    monkeypatch.setenv("LIUHAO_AUDIT_CHAIN_KEY_MODE", "on")
    monkeypatch.setattr(chain_key, "_cached_key", None)
    monkeypatch.setattr(chain_key, "_cache_loaded", False)
    store.log_event(
        AuditEventType.ACCESS_CHECK, "p2", AuditScope.L1, "allow")
    return store, key_path


def _forge_unkeyed_prefix(db_path: str, *, upto_seq: int) -> None:
    """只重写旧的无密钥前缀，并把后续链接重新串起来。

    这正是密钥保护不到的边界：攻击者没有密钥，但可以改前缀，再按已存在的
    （无法伪造的）带密钥 event_hash 重算后续 link_hash，让纯哈希校验通过。
    """
    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute(
            "SELECT seq, event_id, event_type, principal_id, scope, timestamp, "
            "correlation_id, outcome, details, event_hash FROM audit_events "
            "ORDER BY seq ASC"
        ).fetchall()
        prev_hash = None
        prev_link = None
        for (seq, event_id, event_type, principal_id, scope, timestamp,
             correlation_id, outcome, details_json, event_hash) in rows:
            if seq <= upto_seq:
                payload = {
                    "event_id": event_id,
                    "event_type": event_type,
                    "principal_id": principal_id,
                    "scope": scope,
                    "timestamp": timestamp,
                    "correlation_id": correlation_id,
                    "outcome": outcome,
                    "details": {**json.loads(details_json or "{}"), "tampered": True},
                }
                event_hash = hashlib.sha256(
                    canonical_json(payload).encode()).hexdigest()
            link = hashlib.sha256(
                ((prev_link or "") + event_hash).encode()).hexdigest()
            conn.execute(
                "UPDATE audit_events SET event_hash = ?, prev_event_hash = ?, "
                "details = ?, link_hash = ? WHERE seq = ?",
                (
                    event_hash,
                    prev_hash,
                    canonical_json({
                        "event_id": event_id,
                        "event_type": event_type,
                        "principal_id": principal_id,
                        "scope": scope,
                        "timestamp": timestamp,
                        "correlation_id": correlation_id,
                        "outcome": outcome,
                        "details": (
                            {**json.loads(details_json or "{}"), "tampered": True}
                            if seq <= upto_seq
                            else json.loads(details_json or "{}")
                        ),
                    }),
                    link,
                    seq,
                ),
            )
            prev_hash, prev_link = event_hash, link
        conn.execute(
            "UPDATE chain_state SET last_seq = ?, last_hash = ?, "
            "last_link_hash = ? WHERE id = 1",
            (rows[-1][0], prev_hash, prev_link),
        )
        conn.execute("DELETE FROM verification_checkpoints")
        conn.commit()
    finally:
        conn.close()


# --------------------------------------------------------------------------- #
# 正常追加不受影响
# --------------------------------------------------------------------------- #

def test_keyed_mode_signs_new_events_and_still_verifies(tmp_path, monkeypatch):
    store, key_path = _keyed_store(tmp_path, monkeypatch)
    _log(store, 3)

    rows = store.query_events(limit=10)
    assert [r["hash_alg"] for r in rows] == [chain_key.HMAC_ALG] * 3
    assert key_path.exists(), "密钥必须在本机落盘（受 ACL/DPAPI 保护）"

    ok, total = store.verify_integrity()
    assert ok is True
    assert total == 3

    profile = store.chain_key_profile()
    assert profile["status"] == "available"
    assert profile["keyed_events"] == 3
    assert profile["degraded"] is False


def test_key_file_is_excluded_from_backups_and_diagnostics():
    from src.gateway.diagnostic_bundle import BUNDLE_MEMBERS, OPTIONAL_MEMBERS

    assert is_protected_state_file("audit_chain.key") is True
    assert is_protected_state_file("data/audit_chain.key") is True
    allowed = set(BUNDLE_MEMBERS) | set(OPTIONAL_MEMBERS)
    assert "audit_chain.key" not in allowed, "诊断包是白名单，密钥绝不能是成员"


# --------------------------------------------------------------------------- #
# 整链重算：必须被检出（关键证据）
# --------------------------------------------------------------------------- #

def test_full_chain_recomputation_keeping_key_declaration_is_red(
    tmp_path, monkeypatch
):
    """攻击者没有密钥，保留 hmac 声明只能贴假哈希 -> 必红。"""
    store, _key_path = _keyed_store(tmp_path, monkeypatch)
    _log(store, 4)

    # 先直接改内容（最简单的篡改），确认真实 API 会红。
    _forge_entire_chain_unkeyed(store._db_path, declare_keyed=True)
    ok, _total = store.verify_integrity()
    assert ok is False, "保留 hmac-sha256 声明的整链重算必须被检出"

    profile = store.chain_key_profile()
    assert profile["degraded"] is False, "这是篡改（红），不是缺少密钥（琥珀）"


def test_full_chain_recomputation_downgraded_to_sha256_is_never_green(
    tmp_path, monkeypatch
):
    """把整链翻成 sha256 再重算：无密钥校验会过，但产品面绝不给绿灯。

    这正是无密钥哈希链的固有弱点：``hash_alg`` 不在签名负载里。所以本用例
    断言两件事：
    * 内核的“这份无密钥链是否自洽”确实会是 True（记录事实）；
    * 网关校验返回 ``ok=False`` + ``degraded=True``，并明确说明无法用密钥
      证明，而不是静默全绿。
    """
    from src.gateway import audit as audit_gateway

    store, _key_path = _keyed_store(tmp_path, monkeypatch)
    _log(store, 4)
    _forge_entire_chain_unkeyed(store._db_path, declare_keyed=False)

    ok, total = store.verify_integrity()
    assert total == 4
    assert ok is True, (
        "前提：整链被降级重算后，单看无密钥内容校验是自洽的——"
        "这正是需要密钥/降级检测的原因"
    )

    profile = store.chain_key_profile()
    assert profile["degraded"] is True
    assert profile["degraded_reason"] == "chain_key_downgraded"
    assert profile["keyed_events"] == 0

    _patch_store(monkeypatch, store)
    result = audit_gateway._run_verification(full=True)
    assert result["ok"] is False, "降级重算的链不能在产品面显示为完好"
    assert result["degraded"] is True
    assert result["details"]["verification_mode"] == "unkeyed_degraded"
    assert result["details"]["degraded_reason"] == "chain_key_downgraded"
    assert result["details"]["structural_ok"] is True
    assert "无法用密钥证明" in (result["message"] or "")


def test_partial_downgrade_after_keyed_rows_is_never_green(tmp_path, monkeypatch):
    """密钥链之后出现无密钥行（局部降级）同样不能是绿灯。"""
    from src.gateway import audit as audit_gateway

    store, _key_path = _keyed_store(tmp_path, monkeypatch)
    _log(store, 2)

    # 运维显式关掉密钥模式（也可以是攻击者把新写入降级）。
    monkeypatch.setenv("LIUHAO_AUDIT_CHAIN_KEY_MODE", "off")
    monkeypatch.setattr(chain_key, "_cached_key", None)
    monkeypatch.setattr(chain_key, "_cache_loaded", False)
    _log(store, 2)

    rows = store.query_events(limit=10)
    assert [r["hash_alg"] for r in rows][:2] == [chain_key.HMAC_ALG] * 2
    assert rows[2]["hash_alg"] == DEFAULT_HASH_ALG

    profile = store.chain_key_profile()
    assert profile["degraded"] is True
    assert profile["degraded_reason"] == "chain_key_downgraded"

    _patch_store(monkeypatch, store)
    result = audit_gateway._run_verification(full=True)
    assert result["ok"] is False
    assert result["degraded"] is True


def test_downgraded_chain_cannot_be_whitewashed_by_one_keyed_append(
    tmp_path, monkeypatch
):
    """把整链降级后再等一条正常带密钥追加，也不能洗成绿灯。

    没有密钥起点标记时，这种“无密钥前缀 + 带密钥尾巴”看起来与旧库迁移
    完全一样；有标记之后，标记之后的任何无密钥行都是降级证据。
    """
    store, _key_path = _keyed_store(tmp_path, monkeypatch)
    _log(store, 3)
    _forge_entire_chain_unkeyed(store._db_path, declare_keyed=False)

    # 攻击者停手，等系统自己追加一条合法事件。
    event = store.log_event(
        AuditEventType.ACCESS_CHECK, "p9", AuditScope.L1, "allow")
    assert event.hash_alg == chain_key.HMAC_ALG

    profile = store.chain_key_profile()
    assert profile["keyed_events"] == 1
    assert profile["degraded"] is True
    assert profile["degraded_reason"] == "chain_key_downgraded"


def test_legacy_unkeyed_prefix_then_keyed_is_not_flagged(tmp_path, monkeypatch):
    """合法的“旧库迁移”不能被误判成降级：标记之前允许无密钥行。"""
    key_path = tmp_path / "audit_chain.key"
    monkeypatch.setenv("LIUHAO_AUDIT_CHAIN_KEY_MODE", "off")
    monkeypatch.setenv("LIUHAO_AUDIT_CHAIN_KEY_FILE", str(key_path))
    monkeypatch.delenv("LIUHAO_AUDIT_CHAIN_KEY", raising=False)
    monkeypatch.setattr(chain_key, "_cached_key", None)
    monkeypatch.setattr(chain_key, "_cache_loaded", False)

    store = AuditStore(str(tmp_path / "audit.db"))
    _log(store, 2)
    assert [r["hash_alg"] for r in store.query_events(limit=10)] == [
        DEFAULT_HASH_ALG] * 2

    monkeypatch.setenv("LIUHAO_AUDIT_CHAIN_KEY_MODE", "on")
    monkeypatch.setattr(chain_key, "_cached_key", None)
    monkeypatch.setattr(chain_key, "_cache_loaded", False)
    event = store.log_event(
        AuditEventType.ACCESS_CHECK, "p3", AuditScope.L1, "allow")
    assert event.hash_alg == chain_key.HMAC_ALG

    profile = store.chain_key_profile()
    assert profile["key_epoch_seq"] == 3
    assert profile["keyed_events"] == 1
    assert profile["degraded"] is False
    # 旧前缀只能用结构检查、尾巴可用密钥证明：既不是“完好”，也不是“损坏”。
    assert profile["partial"] is True
    assert "部分可证明" in profile["partial_message"]
    assert "前 2 条" in profile["partial_message"]
    assert "第 3 条" in profile["partial_message"]

    ok, total = store.verify_integrity()
    assert ok is True
    assert total == 3

    from src.gateway import audit as audit_gateway

    _patch_store(monkeypatch, store)
    result = audit_gateway._run_verification(full=True)
    assert result["ok"] is True, "没有发现篡改，不能报成损坏"
    assert result["partial"] is True, "混合链不能报成完好"
    assert result["details"]["verification_mode"] == "keyed_partial"
    assert "部分可证明" in result["message"]


def test_prefix_tampered_before_upgrade_is_partial_not_ok(tmp_path, monkeypatch):
    """升级前（还没有密钥时）被改过的旧前缀，升级后会被带密钥的尾巴“锁死”，
    校验能通过——但它的写入时真实性无法证明。状态必须是“部分可证明”，
    绝不能显示“完好”。"""
    key_path = tmp_path / "audit_chain.key"
    monkeypatch.setenv("LIUHAO_AUDIT_CHAIN_KEY_MODE", "off")
    monkeypatch.setenv("LIUHAO_AUDIT_CHAIN_KEY_FILE", str(key_path))
    monkeypatch.delenv("LIUHAO_AUDIT_CHAIN_KEY", raising=False)
    monkeypatch.setattr(chain_key, "_cached_key", None)
    monkeypatch.setattr(chain_key, "_cache_loaded", False)

    store = AuditStore(str(tmp_path / "audit.db"))
    _log(store, 2)
    # 升级前攻击者改旧库：整链重算成无密钥（此时还没有密钥，无法被发现）。
    _forge_entire_chain_unkeyed(store._db_path)

    # 升级：从下一条开始带密钥。
    monkeypatch.setenv("LIUHAO_AUDIT_CHAIN_KEY_MODE", "on")
    monkeypatch.setattr(chain_key, "_cached_key", None)
    monkeypatch.setattr(chain_key, "_cache_loaded", False)
    store.log_event(
        AuditEventType.ACCESS_CHECK, "p2", AuditScope.L1, "allow")

    ok, total = store.verify_integrity()
    assert ok is True, "篡改发生在加密钥之前，事后链本身是自洽的"
    assert total == 3

    profile = store.chain_key_profile()
    assert profile["degraded"] is False
    assert profile["partial"] is True, "旧前缀的写入时真实性不可证明，不能给“完好”"

    from src.gateway import audit as audit_gateway

    _patch_store(monkeypatch, store)
    result = audit_gateway._run_verification(full=True)
    assert result["ok"] is True
    assert result["partial"] is True
    assert result["details"]["verification_mode"] == "keyed_partial"
    assert "部分可证明" in result["message"]


def test_rewriting_prefix_after_keying_is_detected(tmp_path, monkeypatch):
    """加密钥之后再改旧前缀：带密钥的尾巴把前缀钉住了，直接检出（红）。"""
    store, _ = _mixed_store(tmp_path, monkeypatch)
    _forge_unkeyed_prefix(store._db_path, upto_seq=2)

    ok, _total = store.verify_integrity()
    assert ok is False, "带密钥的 link 覆盖了前缀，事后重写必须被检出"


# --------------------------------------------------------------------------- #
# 密钥缺失：明确降级，不静默全红/全绿
# --------------------------------------------------------------------------- #

def test_missing_key_degrades_to_structural_check_with_readable_message(
    tmp_path, monkeypatch
):
    from src.gateway import audit as audit_gateway

    store, key_path = _keyed_store(tmp_path, monkeypatch)
    _log(store, 3)

    # 模拟“备份恢复到另一台机器 / 另一个用户”：库在，密钥不在。
    key_path.unlink()
    monkeypatch.setattr(chain_key, "_cached_key", None)
    monkeypatch.setattr(chain_key, "_cache_loaded", False)
    assert chain_key.status() == "missing"

    profile = store.chain_key_profile()
    assert profile["degraded"] is True
    assert profile["degraded_reason"] == "chain_key_missing"
    assert profile["message"] == "缺少校验密钥，完整性只能做结构检查"

    structural_ok, checked = store.verify_structure_only()
    assert structural_ok is True
    assert checked == 3

    _patch_store(monkeypatch, store)
    result = audit_gateway._run_verification(full=True)
    assert result["ok"] is False, "缺密钥不能伪装成完好"
    assert result["degraded"] is True
    assert result["details"]["verification_mode"] == "structural_only"
    assert result["details"]["degraded_reason"] == "chain_key_missing"
    assert result["details"]["structural_ok"] is True
    assert result["message"] == "缺少校验密钥，完整性只能做结构检查"


def test_missing_key_does_not_block_appends(tmp_path, monkeypatch):
    """恢复后追加不能因为缺密钥而失败；新事件如实记为无密钥。"""
    store, key_path = _keyed_store(tmp_path, monkeypatch)
    _log(store, 2)
    key_path.unlink()
    monkeypatch.setattr(chain_key, "_cached_key", None)
    monkeypatch.setattr(chain_key, "_cache_loaded", False)

    event = store.log_event(
        AuditEventType.ACCESS_CHECK, "p9", AuditScope.L1, "allow")
    assert event.hash_alg == DEFAULT_HASH_ALG
    assert store.query_events(limit=10)[-1]["event_id"] == event.event_id


def test_unreadable_key_degrades_and_still_appends(tmp_path, monkeypatch):
    """用户配置损坏 / DPAPI 解不开：状态必须是 unreadable（可读降级），
    不能当成“链被篡改”，也不能让追加失败。"""
    from src.gateway import audit as audit_gateway

    store, key_path = _keyed_store(tmp_path, monkeypatch)
    _log(store, 2)
    key_path.write_bytes(b"LIUHAO-AUDIT-CHAIN-KEY-DPAPI-1\nnot-valid-base64!!")
    monkeypatch.setattr(chain_key, "_cached_key", None)
    monkeypatch.setattr(chain_key, "_cache_loaded", False)

    assert chain_key.status() == "unreadable"
    profile = store.chain_key_profile()
    assert profile["degraded"] is True
    assert profile["degraded_reason"] == "chain_key_unreadable"
    assert profile["message"] == "缺少校验密钥，完整性只能做结构检查"

    event = store.log_event(
        AuditEventType.ACCESS_CHECK, "p9", AuditScope.L1, "allow")
    assert event.hash_alg == DEFAULT_HASH_ALG, "密钥不可读时追加必须降级而不是失败"

    _patch_store(monkeypatch, store)
    result = audit_gateway._run_verification(full=True)
    assert result["ok"] is False
    assert result["degraded"] is True
    assert result["details"]["verification_mode"] == "structural_only"
    assert result["details"]["structural_ok"] is True


def test_unwritable_key_path_does_not_block_writes_or_startup(
    tmp_path, monkeypatch
):
    """密钥文件建不出来（只读目录 / 盘满 / ACL 拒绝）时，程序照常启动、
    审计照常写入，只是降级成无密钥链并如实报告。"""
    from src.gateway import audit as audit_gateway

    blocker = tmp_path / "blocker"
    blocker.write_text("not a directory", encoding="utf-8")
    key_path = blocker / "audit_chain.key"
    monkeypatch.setenv("LIUHAO_AUDIT_CHAIN_KEY_MODE", "on")
    monkeypatch.setenv("LIUHAO_AUDIT_CHAIN_KEY_FILE", str(key_path))
    monkeypatch.delenv("LIUHAO_AUDIT_CHAIN_KEY", raising=False)
    monkeypatch.setattr(chain_key, "_cached_key", None)
    monkeypatch.setattr(chain_key, "_cache_loaded", False)

    store = AuditStore(str(tmp_path / "audit.db"))  # 打不开密钥也不许拦启动
    event = store.log_event(
        AuditEventType.ACCESS_CHECK, "p1", AuditScope.L1, "allow")
    assert event.hash_alg == DEFAULT_HASH_ALG
    assert chain_key.status() == "missing"

    profile = store.chain_key_profile()
    assert profile["degraded"] is True
    assert profile["degraded_reason"] == "chain_key_missing"

    _patch_store(monkeypatch, store)
    result = audit_gateway._run_verification(full=True)
    assert result["ok"] is False
    assert result["degraded"] is True
    assert result["details"]["verification_mode"] == "structural_only"


# --------------------------------------------------------------------------- #
# 生产默认开、开发默认关
# --------------------------------------------------------------------------- #

def test_production_defaults_to_keyed_chain(tmp_path, monkeypatch):
    for name in ("LIUHAO_ENV", "ENV"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.delenv("LIUHAO_AUDIT_CHAIN_KEY_MODE", raising=False)
    monkeypatch.setenv("LIUHAO_AUDIT_CHAIN_KEY_FILE", str(tmp_path / "k.key"))
    monkeypatch.delenv("LIUHAO_AUDIT_CHAIN_KEY", raising=False)
    monkeypatch.setattr(chain_key, "_cached_key", None)
    monkeypatch.setattr(chain_key, "_cache_loaded", False)

    assert chain_key.key_mode() == "on"
    store = AuditStore(str(tmp_path / "audit.db"))
    event = store.log_event(
        AuditEventType.ACCESS_CHECK, "p1", AuditScope.L1, "allow")
    assert event.hash_alg == chain_key.HMAC_ALG
    assert (tmp_path / "k.key").exists()


def test_development_defaults_to_unkeyed_chain(tmp_path, monkeypatch):
    for name in ("APP_ENV", "LIUHAO_ENV", "ENV"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.delenv("LIUHAO_AUDIT_CHAIN_KEY_MODE", raising=False)
    monkeypatch.setenv("LIUHAO_AUDIT_CHAIN_KEY_FILE", str(tmp_path / "k.key"))
    monkeypatch.setattr(chain_key, "_cached_key", None)
    monkeypatch.setattr(chain_key, "_cache_loaded", False)

    assert chain_key.key_mode() == "off"
    store = AuditStore(str(tmp_path / "audit.db"))
    event = store.log_event(
        AuditEventType.ACCESS_CHECK, "p1", AuditScope.L1, "allow")
    assert event.hash_alg == DEFAULT_HASH_ALG
    assert not (tmp_path / "k.key").exists()


# --------------------------------------------------------------------------- #
# 保留期默认值 / 登录事件字段
# --------------------------------------------------------------------------- #

def test_default_retention_days_is_ninety(monkeypatch, tmp_path):
    from src.kernels.audit import _retention_settings

    monkeypatch.delenv("LIUHAO_AUDIT_RETENTION_DAYS", raising=False)
    monkeypatch.setenv("LIUHAO_AUDIT_RETENTION_ENABLED", "1")
    settings = _retention_settings()
    assert settings["days"] == 90
    assert settings["min_keep"] >= 1


def test_login_event_carries_target_principal():
    from src.gateway import audit as audit_gateway
    from src.gateway.auth import _audit

    _audit("login_success", "alice", {"reason": "ok"})
    event = audit_gateway._read_events(action="login", limit=5, reverse=True)[0]
    assert event["principal_id"] == "alice"
    assert event["target"] == "alice"
    assert event["details"]["target"] == "alice"
