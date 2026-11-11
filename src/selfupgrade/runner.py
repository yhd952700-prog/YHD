"""五段闭环编排（本轮只走到「装测试目录 + 冒烟 + 回滚」）。

调用方必须显式 ``approve=True``（Boss 手动触发）；没有定时器、没有缺口自动装。
"""

from __future__ import annotations

import json
import os
import secrets
import time
from datetime import datetime
from pathlib import Path

from . import paths, rescue
from .gates import run_gates
from .install import (
    check_console_pages,
    find_free_port,
    install_and_smoke,
    installed_version,
    stop_installed,
)
from .manifest import sha256_file, utc_now, verify_manifest
from .models import EXIT_ARG_IO, EXIT_DIFF, EXIT_PASS
from .policy import Policy, load_policy
from .preflight import preflight
from .rollback import rollback_to_known_good
from .state import StateStore


def _acquire_lock(store: StateStore) -> Path | None:
    lock = store.root / "attempt.lock"
    if lock.exists():
        age = time.time() - lock.stat().st_mtime
        if age < 6 * 3600:
            return None
        lock.unlink(missing_ok=True)
    try:
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        return None
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(utc_now())
    return lock


def run_candidate(
    repo: Path,
    installer: Path,
    version: str,
    *,
    approve: bool,
    do_gates: bool = False,
    policy: Policy | None = None,
    store: StateStore | None = None,
    install_dir: Path | None = None,
    data_dir: Path | None = None,
    port: int | None = None,
) -> int:
    repo = Path(repo)
    policy = policy or load_policy(repo / "release-lock")
    store = store or StateStore(paths.state_dir())

    if policy.require_boss_approval and not approve:
        print("拒绝：自升级只允许 Boss 手动触发（需要 --approve）。")
        return EXIT_ARG_IO

    snap = store.load()
    if snap.state == "LOCKED":
        print("拒绝：已锁死。Boss 手动解锁：python -m src.selfupgrade unlock --approve --reason \"...\"")
        return EXIT_DIFF

    lock = _acquire_lock(store)
    if lock is None:
        print("拒绝：已有一次自升级在进行。")
        return EXIT_ARG_IO

    try:
        install_dir, data_dir = paths.assert_safe_test_target(
            install_dir or policy.target_install_dir,
            data_dir or policy.target_data_dir,
        )
        installer = Path(installer)

        verify = verify_manifest(repo, repo / "release-lock", check_source=True)
        if verify.exit_code != 0:
            detail = "release-lock 校验失败：" + "；".join(verify.problems + verify.source_drift)
            store.failure(snap, detail)
            print(detail)
            return EXIT_DIFF

        problems = preflight(repo, installer, install_dir, data_dir)
        if problems:
            detail = "预检失败：" + "；".join(problems)
            store.failure(snap, detail)
            print(detail)
            return EXIT_ARG_IO

        attempt_id = datetime.now().strftime("%Y%m%d-%H%M%S")
        snap.attempt_id = attempt_id
        evidence = paths.evidence_dir() / attempt_id

        store.enter(snap, "PREFLIGHT", f"candidate={installer.name} version={version}")

        if do_gates:
            store.enter(snap, "GATES")
            results = run_gates(repo)
            for item in results:
                print(f"[{item.status}] {item.name} {item.duration_seconds:.1f}s")
            bad = [r for r in results if r.status != "pass"]
            if bad:
                detail = "门禁未全绿：" + "；".join(f"{r.name}={r.status}" for r in bad)
                store.failure(snap, detail)
                print(detail)
                return EXIT_DIFF

        store.enter(snap, "INSTALL", f"installing {installer.name}")
        chosen_port = find_free_port(port or policy.smoke_port)
        # 测试实例的管理员口令由本次运行给定：冒烟登录与六页面检查共用同一份，
        # 每次升级重新写一次凭据；口令不落盘、不进证据。只用于测试目录。
        admin_password = "Smoke" + secrets.token_hex(8)
        smoke = install_and_smoke(
            repo,
            installer,
            install_dir,
            data_dir,
            chosen_port,
            evidence,
            admin_password=admin_password,
        )
        print(f"安装/冒烟：{'PASS' if smoke.ok else 'FAIL'} {smoke.detail}")

        problem = "" if smoke.ok else smoke.detail
        if not problem:
            actual = installed_version(install_dir)
            if actual != version:
                problem = f"版本号不符：期望 {version}，安装树为 {actual or '读不到'}"
                print(problem)
        if not problem:
            pages_state, pages_detail = check_console_pages(
                repo,
                chosen_port,
                "liuhao-smoke",
                admin_password,
                evidence,
                install_dir=install_dir,
                data_dir=data_dir,
            )
            print(f"六页面：{pages_state} {pages_detail}")
            if pages_state == "fail":
                problem = f"六页面检查失败：{pages_detail}"
            elif pages_state == "not_verified":
                print("提醒：六页面未验证（见 pages_check.txt），不作为失败。")

        if not problem:
            known = {
                "version": version,
                "installer": str(installer),
                "sha256": sha256_file(installer),
                "install_dir": str(install_dir),
                "data_dir": str(data_dir),
                "port": chosen_port,
            }
            store.set_known_good(known)
            # 成功路径也留救援启动器：Boss 任何时候都能用一条命令回到这个可用版本。
            try:
                cmd = rescue.write_rescue(known, install_dir, data_dir)
                print(f"救援启动器：{cmd}")
            except OSError as exc:
                print(f"救援启动器写入失败：{exc}")
            store.success(snap, f"{version} 安装并冒烟通过")
            print(f"PASS：{version} 已装到 {install_dir}，known-good 已更新。")
            return EXIT_PASS

        store.failure(snap, problem)
        known = store.known_good()
        if known:
            store.enter(snap, "ROLLED_BACK", f"rollback -> {known.get('version')}")
            print(f"冒烟失败，回滚到 known-good {known.get('version')} …")
            stop_installed(install_dir)
            rolled = rollback_to_known_good(repo, known, install_dir, data_dir, chosen_port, evidence)
            if rolled.ok:
                store.enter(snap, "ROLLED_BACK", f"rollback ok -> {known.get('version')}")
                print(f"ROLLED_BACK：已回到 {known.get('version')}，可用。")
                return EXIT_DIFF
            print(f"回滚也失败：{rolled.detail}")
        else:
            print("没有 known-good，无法自动回滚。")

        # 硬指标：失败路径结束后必须留下救援启动器。
        if known:
            try:
                cmd = rescue.write_rescue(known, install_dir, data_dir)
                print(f"救援启动器：{cmd}")
            except OSError as exc:
                print(f"救援启动器写入失败：{exc}")
        store.enter(snap, "LOCKED", "回滚失败或没有 known-good，等待 Boss 手动处理")
        return EXIT_DIFF
    finally:
        lock.unlink(missing_ok=True)


def save_request(store: StateStore, payload: dict) -> Path:
    """AI 写升级请求；Broker 读取后仍需 Boss 授权。"""
    requests_dir = store.root.parent / "requests"
    requests_dir.mkdir(parents=True, exist_ok=True)
    req_id = payload.get("id") or datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    payload = dict(payload)
    payload.setdefault("requested_at", utc_now())
    payload["id"] = req_id
    path = requests_dir / f"{req_id}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    store.journal({"event": "request", "id": req_id, "payload": payload})
    return path
