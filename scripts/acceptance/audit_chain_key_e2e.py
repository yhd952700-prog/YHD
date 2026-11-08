"""审计链加密钥 —— 改前/改后实测（真服务 + 真库改写）。

这个脚本回答一个问题：**拿到库写权限的人整链重算之后，校验还能不能看出来？**

三段实验，全部对着真实进程和真实 SQLite 文件做：

1. **改前（无密钥链）**：``LIUHAO_AUDIT_CHAIN_KEY_MODE=off``，停机后把所有行
   重算成 SHA-256。重启后 ``/v1/audit/verify`` 仍然是 ``ok=true`` —— 这正是
   要压掉的弱点。
2. **改后（带密钥链）**：
   * 整链降级成 SHA-256 再重算 -> 不绿（``degraded=true``），并说明“无法用
     密钥证明”；
   * 保留 ``hmac-sha256`` 声明、贴假哈希 -> 红（``ok=false``, ``degraded=false``）。
3. **备份恢复缺密钥**：把带密钥的库复制到另一台实例、删掉本机密钥，重启后
   校验必须是“缺密钥、只能结构检查”的可读降级，而不是全红或全绿。

运行：
    .venv\\Scripts\\python.exe scripts/acceptance/audit_chain_key_e2e.py
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import time

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, REPO)
sys.path.insert(0, os.path.join(REPO, "scripts", "acceptance"))

from account_tree_e2e import PASSWORDS, Instance, Report, request  # noqa: E402

from src.kernels.audit.hashutil import canonical_json  # noqa: E402


def register_and_login(inst: Instance, principal: str = "root1") -> str:
    status, body = request(
        "POST", inst.base + "/v1/accounts/register",
        {"principal": principal, "password": PASSWORDS[0],
         "display_name": "主账号"},
    )
    if status != 200:
        raise RuntimeError(f"register failed: {status} {body}")
    status, body = request(
        "POST", inst.base + "/v1/auth/login",
        {"principal": principal, "secret": PASSWORDS[0]},
    )
    if status != 200:
        raise RuntimeError(f"login failed: {status} {body}")
    return str(body.get("access_token") or "")


def login_only(inst: Instance, principal: str = "root1") -> str:
    status, body = request(
        "POST", inst.base + "/v1/auth/login",
        {"principal": principal, "secret": PASSWORDS[0]},
    )
    if status != 200:
        raise RuntimeError(f"login failed: {status} {body}")
    return str(body.get("access_token") or "")


def verify(inst: Instance, token: str) -> dict:
    status, body = request(
        "GET", inst.base + "/v1/audit/verify?full=true", token=token)
    if status != 200:
        raise RuntimeError(f"verify failed: {status} {body}")
    return body


def summary(inst: Instance, token: str) -> dict:
    status, body = request("GET", inst.base + "/v1/audit/summary", token=token)
    if status != 200:
        raise RuntimeError(f"summary failed: {status} {body}")
    return body


def max_seq(inst: Instance, token: str) -> int:
    status, body = request(
        "GET", inst.base + "/v1/audit/events?limit=1&reverse=true", token=token)
    if status != 200:
        raise RuntimeError(f"events failed: {status} {body}")
    rows = (body or {}).get("events") or (body or {}).get("items") or []
    return int(rows[0].get("seq") or 0) if rows else 0


def audit_db(inst: Instance) -> str:
    return os.path.join(inst.root, "audit_store.db")


def _remove_wal(path: str) -> None:
    for suffix in ("-wal", "-shm"):
        try:
            os.remove(path + suffix)
        except FileNotFoundError:
            pass


def _with_retry(fn, *, attempts: int = 20, delay: float = 0.5):
    """Windows 上进程刚被杀掉时 -wal/-shm 句柄可能还没释放，重试。"""
    for attempt in range(attempts):
        try:
            return fn()
        except (sqlite3.OperationalError, PermissionError):
            if attempt == attempts - 1:
                raise
            time.sleep(delay)


def forge_entire_chain(db_path: str, *, declare_keyed: bool) -> int:
    """模拟拿到库写权限：把所有事件重算成无密钥 SHA-256。

    ``declare_keyed=True`` 时保留 ``hash_alg=hmac-sha256`` 声明，但攻击者
    没有密钥，只能贴一个普通 SHA-256 哈希（必红）。
    """
    def _run() -> int:
        conn = sqlite3.connect(db_path, timeout=10)
        try:
            rows = conn.execute(
                "SELECT seq, event_id, event_type, principal_id, scope, timestamp, "
                "correlation_id, outcome, details FROM audit_events ORDER BY seq ASC"
            ).fetchall()
            prev_hash = None
            prev_link = None
            for (seq, event_id, event_type, principal_id, scope, timestamp,
                 correlation_id, outcome, details_json) in rows:
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
                conn.execute(
                    "UPDATE audit_events SET event_hash=?, prev_event_hash=?, "
                    "hash_alg=?, link_hash=? WHERE seq=?",
                    (event_hash, prev_hash,
                     "hmac-sha256" if declare_keyed else "sha256", link, seq),
                )
                prev_hash, prev_link = event_hash, link
            conn.execute(
                "UPDATE chain_state SET last_seq=?, last_hash=?, last_link_hash=? "
                "WHERE id=1",
                (rows[-1][0], prev_hash, prev_link),
            )
            conn.execute("DELETE FROM verification_checkpoints")
            conn.commit()
            return len(rows)
        finally:
            conn.close()

    return _with_retry(_run)


def copy_db(src: str, dst: str) -> None:
    def _run() -> None:
        _remove_wal(dst)
        src_conn = sqlite3.connect(src, timeout=10)
        dst_conn = sqlite3.connect(dst, timeout=10)
        try:
            src_conn.backup(dst_conn)
        finally:
            dst_conn.close()
            src_conn.close()

    _with_retry(_run)


def scan_log_for_key(inst: Instance, key_path: str) -> bool:
    """密钥正文不得出现在服务日志里（不写进日志的直接证据）。"""
    try:
        with open(key_path, "rb") as handle:
            blob = handle.read()
        with open(inst.log_path, "rb") as handle:
            log = handle.read()
    except OSError:
        return True
    body = blob.split(b"\n", 1)[1] if b"\n" in blob else blob
    return body not in log


def key_material_absent(key_path: str, payload: bytes) -> bool:
    """导出归档里不得出现密钥正文（base64 段与整文件都查一遍）。"""
    try:
        with open(key_path, "rb") as handle:
            raw = handle.read()
    except OSError:
        return True
    body = raw.split(b"\n", 1)[1] if b"\n" in raw else raw
    return body not in payload and raw not in payload


def main() -> int:
    report = Report()
    cleanup: list[Instance] = []

    # ---------------- 1. 改前：无密钥链，整链重算后仍“完好” ----------------
    unkeyed = Instance(
        "chain-unkeyed",
        env_extra={"LIUHAO_AUDIT_CHAIN_KEY_MODE": "off"},
    )
    cleanup.append(unkeyed)
    token = register_and_login(unkeyed)
    before = verify(unkeyed, token)
    report.check("改前·无密钥", "正常链校验通过", before.get("ok"), True)
    report.check(
        "改前·无密钥", "库里没有任何带密钥事件",
        int((before.get("details", {}).get("chain_key") or {}).get("keyed_events", -1)),
        0,
    )
    unkeyed.stop(cleanup=False)
    forged = forge_entire_chain(audit_db(unkeyed), declare_keyed=False)
    report.check("改前·无密钥", "整链重算行数 > 0", forged > 0, True,
                 evidence=f"forged={forged}")
    unkeyed.start()
    after_forge = verify(unkeyed, token)
    report.check(
        "改前·无密钥", "整链重算后仍显示完好（旧弱点）",
        after_forge.get("ok"), True,
        evidence=f"degraded={after_forge.get('degraded')}",
    )
    unkeyed.stop(cleanup=False)

    # ---------------- 2. 改后：带密钥链 ----------------
    key_root = tempfile.mkdtemp(prefix="chain-keyed-")
    key_path = os.path.join(key_root, "audit_chain.key")
    keyed = Instance(
        "chain-keyed",
        root=key_root,
        env_extra={
            "LIUHAO_AUDIT_CHAIN_KEY_MODE": "on",
            "LIUHAO_AUDIT_CHAIN_KEY_FILE": key_path,
        },
    )
    cleanup.append(keyed)
    token = register_and_login(keyed)

    healthy = verify(keyed, token)
    report.check("改后·带密钥", "正常追加后校验通过", healthy.get("ok"), True)
    key_info = (healthy.get("details", {}) or {}).get("chain_key") or {}
    report.check("改后·带密钥", "事件确实带密钥", int(key_info.get("keyed_events", 0)) > 0, True,
                 evidence=f"keyed_events={key_info.get('keyed_events')}")
    report.check("改后·带密钥", "密钥文件在本机落盘", os.path.exists(key_path), True)
    report.check("改后·带密钥", "密钥正文不写进日志",
                 scan_log_for_key(keyed, key_path), True)

    summ = summary(keyed, token)
    report.check("保留期", "默认保留 90 天",
                 int((summ.get("retention") or {}).get("days", -1)), 90)
    report.check("保留期", "界面数据标记为会删除（非永久）",
                 bool((summ.get("retention") or {}).get("enabled")), True)
    login_events = request(
        "GET", keyed.base + "/v1/audit/events?action=login&limit=5",
        token=token)[1]
    rows = login_events.get("events") or login_events.get("items") or []
    login = rows[0] if rows else {}
    report.check("登录字段", "登录事件带 target=principal",
                 login.get("target"), "root1")
    report.check("登录字段", "details.target 也在",
                 (login.get("details") or {}).get("target"), "root1")

    # 备份一份“原始带密钥库”，供第 3 段恢复实验使用。
    keyed.stop(cleanup=False)
    original_copy = os.path.join(key_root, "audit_keyed_original.db")
    copy_db(audit_db(keyed), original_copy)

    # 2a. 整链降级成 sha256 再重算 -> 不绿
    forged = forge_entire_chain(audit_db(keyed), declare_keyed=False)
    report.check("改后·带密钥", "降级重算行数 > 0", forged > 0, True,
                 evidence=f"forged={forged}")
    keyed.start()
    downgraded = verify(keyed, token)
    details = downgraded.get("details") or {}
    report.check("改后·带密钥", "降级重算后不显示完好",
                 downgraded.get("ok"), False)
    report.check("改后·带密钥", "降级重算被标记为降级",
                 downgraded.get("degraded"), True)
    report.check("改后·带密钥", "降级原因可读",
                 details.get("degraded_reason"), "chain_key_downgraded")
    report.check("改后·带密钥", "无密钥链本身仍自洽（如实展示）",
                 details.get("structural_ok"), True)
    report.check("改后·带密钥", "明确说无法用密钥证明",
                 "无法用密钥证明" in str(downgraded.get("message") or ""), True,
                 evidence=str(downgraded.get("message") or "")[:80])

    # 2b. 保留 hmac 声明、贴假哈希 -> 红（不是琥珀）
    keyed.stop(cleanup=False)
    forge_entire_chain(audit_db(keyed), declare_keyed=True)
    keyed.start()
    faked = verify(keyed, token)
    report.check("改后·带密钥", "假密钥声明被检出（红）", faked.get("ok"), False)
    report.check("改后·带密钥", "这是篡改而不是缺密钥",
                 bool(faked.get("degraded")), False)
    keyed.stop(cleanup=False)

    # ---------------- 3. 备份恢复：缺密钥时明确降级 ----------------
    restore_root = tempfile.mkdtemp(prefix="chain-restore-")
    restore_key = os.path.join(restore_root, "audit_chain.key")
    restored = Instance(
        "chain-restore",
        root=restore_root,
        env_extra={
            "LIUHAO_AUDIT_CHAIN_KEY_MODE": "on",
            "LIUHAO_AUDIT_CHAIN_KEY_FILE": restore_key,
        },
    )
    cleanup.append(restored)
    token = register_and_login(restored)
    restored.stop(cleanup=False)
    # 真实恢复：带密钥的库搬过来，本机密钥不带过来。
    copy_db(original_copy, audit_db(restored))
    try:
        os.remove(restore_key)
    except FileNotFoundError:
        pass
    restored.start()
    degraded = verify(restored, token)
    d_details = degraded.get("details") or {}
    report.check("恢复·缺密钥", "不能伪装成完好", degraded.get("ok"), False)
    report.check("恢复·缺密钥", "明确是降级态", degraded.get("degraded"), True)
    report.check("恢复·缺密钥", "只做结构检查",
                 d_details.get("verification_mode"), "structural_only")
    report.check("恢复·缺密钥", "结构检查结果单独给出",
                 d_details.get("structural_ok"), True)
    report.check("恢复·缺密钥", "可读提示原样返回",
                 degraded.get("message"),
                 "缺少校验密钥，完整性只能做结构检查")
    restored.stop(cleanup=False)

    # ---------------- 4. 0.1.0 升级：无密钥前缀 + 升级后带密钥尾巴 ----------------
    # 这是所有老用户的真实状态：升级前写下的记录永远不会带密钥，升级后才会
    # 出现 hmac-sha256。整条链既不是“完好”（旧前缀无法证明未被重写），也不是
    # “损坏”（尾巴可证明、结构自洽），必须是第四种状态：部分可证明。
    upgrade_root = tempfile.mkdtemp(prefix="chain-upgrade-")
    upgrade_key = os.path.join(upgrade_root, "audit_chain.key")
    upgraded = Instance(
        "chain-upgrade",
        root=upgrade_root,
        env_extra={
            "LIUHAO_AUDIT_CHAIN_KEY_MODE": "off",  # 0.1.0：还没有密钥
            "LIUHAO_AUDIT_CHAIN_KEY_FILE": upgrade_key,
        },
    )
    cleanup.append(upgraded)
    token = register_and_login(upgraded)
    legacy = verify(upgraded, token)
    legacy_key = (legacy.get("details") or {}).get("chain_key") or {}
    report.check("升级·混合链", "升级前全是无密钥事件",
                 int(legacy_key.get("keyed_events", -1)), 0)
    report.check("升级·混合链", "升级前密钥文件不存在",
                 os.path.exists(upgrade_key), False)
    report.check("升级·混合链", "旧库本身不报部分可证明",
                 bool(legacy.get("partial")), False)

    upgraded.stop(cleanup=False)
    upgraded.env["LIUHAO_AUDIT_CHAIN_KEY_MODE"] = "on"  # 升级后：开启密钥
    upgraded.start()
    token = login_only(upgraded)  # 升级后第一件事：登录（产生带密钥事件）
    mixed = verify(upgraded, token)
    m_details = mixed.get("details") or {}
    m_key = m_details.get("chain_key") or {}
    mixed_message = str(mixed.get("message") or "")
    report.check("升级·混合链", "混合链整体校验能跑完", mixed.get("ok"), True)
    report.check("升级·混合链", "不是“完好”：标记为部分可证明",
                 mixed.get("partial"), True)
    report.check("升级·混合链", "不是“损坏”（未降级、未失败）",
                 bool(mixed.get("degraded")), False)
    report.check("升级·混合链", "校验模式可读",
                 m_details.get("verification_mode"), "keyed_partial")
    report.check("升级·混合链", "升级后确有带密钥事件",
                 int(m_key.get("keyed_events", 0)) > 0, True,
                 evidence=f"keyed_events={m_key.get('keyed_events')}")
    report.check("升级·混合链", "提示语点明“部分可证明”",
                 "部分可证明" in mixed_message, True, evidence=mixed_message)
    report.check("升级·混合链", "提示语点明旧前缀只能结构检查",
                 "升级前写入的无密钥记录" in mixed_message
                 and "只能做结构检查" in mixed_message, True,
                 evidence=mixed_message)

    # 导出回执必须提前说明“密钥不随包”，而不是等恢复完才解释。
    receipt_status, receipt = request(
        "POST", upgraded.base + "/v1/accounts/backup/export",
        {"scope": "all", "passphrase": "upgrade-pass-123"}, token=token)
    report.check("升级·混合链", "凭据包导出成功", receipt_status, 200,
                 evidence=str(receipt)[:100])
    receipt_notice = str((receipt or {}).get("notice") or "")
    report.check("升级·混合链", "导出回执提前说明密钥不随包",
                 "校验密钥都不随包" in receipt_notice, True,
                 evidence=receipt_notice[:120])

    archive_status, archive = request(
        "GET", upgraded.base + "/v1/audit/export?limit=50", token=token)
    report.check("升级·混合链", "审计归档导出成功", archive_status, 200,
                 evidence=str(archive)[:100])
    archive_integrity = (archive or {}).get("integrity") or {}
    report.check("升级·混合链", "归档里同样是部分可证明",
                 bool(archive_integrity.get("partial")), True)
    report.check("升级·混合链", "归档不含校验密钥",
                 key_material_absent(upgrade_key, json.dumps(archive).encode()), True)
    report.check("升级·混合链", "归档说明不含校验密钥",
                 "不含校验密钥" in str(archive.get("notice") or ""), True)

    # ---------------- 5. 密钥损坏：不可读也必须能用（可用降级） ----------------
    broken_root = tempfile.mkdtemp(prefix="chain-broken-")
    broken_key = os.path.join(broken_root, "audit_chain.key")
    broken = Instance(
        "chain-broken",
        root=broken_root,
        env_extra={
            "LIUHAO_AUDIT_CHAIN_KEY_MODE": "on",
            "LIUHAO_AUDIT_CHAIN_KEY_FILE": broken_key,
        },
    )
    cleanup.append(broken)
    token = register_and_login(broken)
    before_seq = max_seq(broken, token)
    broken.stop(cleanup=False)
    # 模拟“用户配置损坏 / 换用户后 DPAPI 解不开”：文件在，但解不出密钥。
    with open(broken_key, "wb") as handle:
        handle.write(b"LIUHAO-AUDIT-CHAIN-KEY-DPAPI-1\nnot-a-valid-blob")
    broken.start()
    health_status, health = request("GET", broken.base + "/v1/health")
    report.check("密钥损坏", "程序照常启动", health_status, 200,
                 evidence=str(health)[:80])
    token = login_only(broken)  # 登录本身就要写审计事件
    after_seq = max_seq(broken, token)
    report.check("密钥损坏", "审计写入没有失败（登录成功且新增事件）",
                 after_seq > before_seq, True,
                 evidence=f"{before_seq} -> {after_seq}")
    broken_verify = verify(broken, token)
    b_details = broken_verify.get("details") or {}
    report.check("密钥损坏", "不能显示完好", broken_verify.get("ok"), False)
    report.check("密钥损坏", "明确是降级态", broken_verify.get("degraded"), True)
    report.check("密钥损坏", "降级原因为密钥不可读",
                 b_details.get("degraded_reason"), "chain_key_unreadable")
    report.check("密钥损坏", "只能做结构检查",
                 b_details.get("verification_mode"), "structural_only")
    report.check("密钥损坏", "结构本身仍自洽",
                 b_details.get("structural_ok"), True)
    report.check("密钥损坏", "提示语原样返回",
                 broken_verify.get("message"),
                 "缺少校验密钥，完整性只能做结构检查")
    broken.stop(cleanup=False)

    # ---------------- 汇总 ----------------
    failed = report.failed()
    print("\n" + "=" * 72)
    print(f"检查 {len(report.rows)} 项，失败 {len(failed)} 项")
    if failed:
        for row in failed:
            print(f"  FAIL {row['item']} · {row['check']}: "
                  f"got={row['got']!r} expected={row['expected']!r}")
    else:
        print("全部通过")
    print("=" * 72)

    if os.environ.get("LIUHAO_KEEP_E2E_DATA") != "1":
        for inst in cleanup:
            inst.stop(cleanup=True)
        for path in (key_root, restore_root, upgrade_root, broken_root):
            shutil.rmtree(path, ignore_errors=True)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
