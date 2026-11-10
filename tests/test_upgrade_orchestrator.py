"""编排器（supervisor）的边界测试。

这里只测"必须成立且不依赖真实升级"的部分：journal 先落盘、等锁判据、
只按记录 pid 停进程、请求文件与运行副本。真正的停服→应用→重启→健康复核
在独立演练目录里用真实安装树跑，不塞进单测。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from src.common.instance_lock import InstanceLock
from src.upgrade import orchestrator
from src.upgrade.orchestrator import (
    OrchestratorBusy,
    Journal,
    needs_recovery,
    request_orchestrated_apply,
    spawn_supervisor,
    stop_recorded_gateway,
    wait_for_lock_release,
)


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _reap_process_while(proc: subprocess.Popen, stop: threading.Event) -> None:
    """后台线程持续 ``poll()`` 回收指定子进程（仅 POSIX 需要）。

    POSIX 上子进程退出后若父进程不 wait() 会一直保持僵尸态，而
    ``os.kill(pid, 0)`` / ``psutil.pid_exists`` 对僵尸进程都返回 True，
    「等 pid 消失」的判据就永远等不到；Windows 没有这个语义。
    这里在测试侧异步调用 ``Popen.poll()``（waitpid WNOHANG）及时收割
    僵尸，不改变被测代码的任何行为。
    """
    while not stop.is_set():
        if proc.poll() is not None:
            return
        stop.wait(0.05)


# ---------------------------------------------------------------------------
# journal：阶段必须先 fsync 落盘
# ---------------------------------------------------------------------------
def test_journal_fsyncs_every_phase_before_moving_on(tmp_path, monkeypatch):
    calls: list[int] = []
    real_fsync = os.fsync

    def counting_fsync(fd: int) -> None:
        calls.append(fd)
        real_fsync(fd)

    monkeypatch.setattr(orchestrator.os, "fsync", counting_fsync)

    journal = Journal(tmp_path)
    journal.update("prepared", message="备份已落盘", backup_id="b-1")
    journal.update("stopping", message="正在停服")

    assert len(calls) >= 2, "每个阶段都必须 fsync，而不是等最后一次性写"
    payload = json.loads(journal.path.read_text(encoding="utf-8"))
    assert payload["phase"] == "stopping"
    assert payload["backup_id"] == "b-1"
    assert [event["phase"] for event in payload["events"]] == ["prepared", "stopping"]
    assert not journal.path.with_name(journal.path.name + ".tmp").exists()


def test_interrupted_phases_require_recovery_but_terminal_ones_do_not(tmp_path):
    journal = Journal(tmp_path)
    for phase in ("prepared", "stopping", "waiting_lock", "applying", "starting"):
        journal.update(phase)
        assert needs_recovery(journal.data) is True, phase
    for phase in ("completed", "rolled_back", "recovered", "manual_intervention"):
        journal.update(phase)
        assert needs_recovery(journal.data) is False, phase


def test_manual_intervention_reports_code_zip_and_keeps_paths(tmp_path):
    journal = Journal(tmp_path)
    journal.update(
        "prepared",
        backup_id="20260101T000000Z-aaaa",
        backup_root=str(tmp_path / "backups"),
    )
    result = orchestrator._manual_intervention(
        journal, reason="旧版本健康复核失败", backup_id="20260101T000000Z-aaaa"
    )
    assert result["phase"] == "manual_intervention"
    assert result["code_zip"].endswith("code.zip")
    assert "需人工介入" in journal.data["message"]
    assert str(journal.path) in journal.data["message"]


# ---------------------------------------------------------------------------
# 等锁：必须松开 read(1) 且 pid 消失
# ---------------------------------------------------------------------------
def test_wait_for_lock_release_waits_for_the_holder_process(tmp_path):
    lock_dir = tmp_path
    code = (
        "import sys, time\n"
        f"sys.path.insert(0, {str(_repo_root())!r})\n"
        "from src.common.instance_lock import InstanceLock\n"
        f"lock = InstanceLock({str(lock_dir)!r}, name='instance')\n"
        "lock.acquire()\n"
        "print('held', flush=True)\n"
        "time.sleep(1.5)\n"
    )
    holder = subprocess.Popen(
        [sys.executable, "-c", code],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    # POSIX 上 holder 退出后会变僵尸，先起回收线程让 pid 判据能真正消失。
    stop = threading.Event()
    reaper = threading.Thread(
        target=_reap_process_while, args=(holder, stop), daemon=True
    )
    reaper.start()
    try:
        assert holder.stdout is not None
        assert holder.stdout.readline().strip() == "held"
        started = time.monotonic()
        result = wait_for_lock_release(tmp_path, pid=holder.pid, timeout=10.0)
        elapsed = time.monotonic() - started
        assert result["released"] is True, result
        assert result["attempts"] >= 2, "持有进程活着时不能提前判为已释放"
        assert elapsed >= 0.8, f"没等到锁真正释放就返回了（{elapsed:.2f}s）"
    finally:
        stop.set()
        reaper.join(timeout=5)
        holder.kill()
        holder.wait(timeout=10)


# ---------------------------------------------------------------------------
# 停进程：只认记录的 pid，绝不按镜像名批量杀
# ---------------------------------------------------------------------------
def test_stop_refuses_a_pid_that_is_not_the_gateway(tmp_path):
    (tmp_path / "run").mkdir()
    (tmp_path / "run" / "instance.json").write_text(
        json.dumps({"pid": os.getpid(), "port": 0}), encoding="utf-8"
    )
    result = stop_recorded_gateway(
        data_dir=tmp_path, app_root=tmp_path / "app", port=0
    )
    assert result["stopped"] is False
    assert "拒绝停止" in result["error"]
    # 自己还活着，说明它没有对这个 pid 发任何终止信号
    assert orchestrator._pid_alive(os.getpid())


def test_stop_kills_only_the_recorded_pid(tmp_path):
    target = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)", "src.gateway"]
    )
    bystander = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)", "src.gateway"]
    )
    # POSIX 上 target 被 SIGTERM 后会变僵尸，stop 的「等 pid 消失」需要回收
    # 线程配合；bystander 绝不能被回收或误杀。
    stop = threading.Event()
    reaper = threading.Thread(
        target=_reap_process_while, args=(target, stop), daemon=True
    )
    reaper.start()
    try:
        (tmp_path / "run").mkdir()
        (tmp_path / "run" / "instance.json").write_text(
            json.dumps({"pid": target.pid, "port": 0}), encoding="utf-8"
        )
        result = stop_recorded_gateway(data_dir=tmp_path, app_root=tmp_path)
        assert result["stopped"] is True, result
        assert result["process"]["pid"] == target.pid
        assert result["terminated"] is True
        assert target.poll() is not None
        assert bystander.poll() is None, "同名的旁观进程不能被杀"
    finally:
        stop.set()
        reaper.join(timeout=5)
        for proc in (target, bystander):
            if proc.poll() is None:
                proc.kill()
            proc.wait(timeout=10)


# ---------------------------------------------------------------------------
# 安装根之外运行 + 请求文件
# ---------------------------------------------------------------------------
def test_spawn_supervisor_runs_from_a_copy_outside_the_install_tree(
    tmp_path, monkeypatch
):
    data_dir = tmp_path / "data"
    app_root = tmp_path / "app"
    app_root.mkdir()
    captured: dict = {}

    class _FakeProc:
        pid = 4321

    def fake_popen(args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        return _FakeProc()

    monkeypatch.setattr(orchestrator.subprocess, "Popen", fake_popen)

    info = spawn_supervisor(data_dir=data_dir, app_root=app_root, port=8099)

    runtime_dir = Path(info["runtime_dir"])
    assert runtime_dir.parent == data_dir / "run" / "upgrade-agent"
    assert (runtime_dir / "src" / "upgrade" / "orchestrator.py").exists()
    assert captured["kwargs"]["cwd"] == str(runtime_dir)
    assert captured["kwargs"]["env"]["PYTHONPATH"] == str(runtime_dir)
    assert captured["kwargs"]["env"]["LIUHAO_APP_ROOT"] == str(app_root)
    assert captured["kwargs"]["env"]["LIUHAO_DATA_DIR"] == str(data_dir)
    assert Path(info["log"]) == runtime_dir / "supervisor.log"
    assert info["pid"] == 4321


def test_request_orchestrated_apply_writes_request_then_spawns(tmp_path, monkeypatch):
    seen: dict = {}

    def fake_spawn(**kwargs):
        request = json.loads(
            (tmp_path / "upgrade-state" / "orchestration-request.json").read_text(
                encoding="utf-8"
            )
        )
        seen.update(kwargs=kwargs, request=request)
        return {"pid": 999, "runtime_dir": "x", "log": "y"}

    monkeypatch.setattr(orchestrator, "spawn_supervisor", fake_spawn)
    plan = request_orchestrated_apply(
        data_dir=tmp_path, app_root=tmp_path, version="0.2.0", port=8099
    )
    assert plan["request"]["version"] == "0.2.0"
    assert seen["request"]["request_id"] == plan["request"]["request_id"]
    assert seen["request"]["port"] == 8099
    assert plan["supervisor"]["pid"] == 999


def test_request_orchestrated_apply_is_rejected_while_a_journal_is_active(
    tmp_path, monkeypatch
):
    Journal(tmp_path).update("applying", gravity="upgrade")
    monkeypatch.setattr(
        orchestrator,
        "spawn_supervisor",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("不应拉起 supervisor")),
    )
    with pytest.raises(OrchestratorBusy):
        request_orchestrated_apply(
            data_dir=tmp_path, app_root=tmp_path, version="0.2.0"
        )
    assert not (tmp_path / "upgrade-state" / "orchestration-request.json").exists()


def test_health_probe_only_accepts_json_with_status(tmp_path, monkeypatch):
    responses = [
        ("/v1/health", 200, {}, True),
        ("/v1/health", 200, {"status": "ok", "version": "0.2.0"}, False),
    ]

    def fake_probe(host, port, paths, timeout):
        assert paths == ("/v1/health",)
        return responses.pop(0)

    monkeypatch.setattr("src.upgrade.startup_probe._probe_health", fake_probe)
    monkeypatch.setattr(orchestrator.time, "sleep", lambda _s: None)

    result = orchestrator.probe_gateway_health(port=1234, timeout=5.0)
    assert result["ok"] is True
    assert result["path"] == "/v1/health"
    assert result["version"] == "0.2.0"


def test_lock_holder_publishes_metadata_and_releases_cleanly(tmp_path):
    # 保证 supervisor 的锁名不会和网关的 instance 锁互相影响。
    with InstanceLock(tmp_path, name="upgrade-agent") as lock:
        assert lock.held
        assert (tmp_path / "run" / "upgrade-agent.lock").exists()
    with InstanceLock(tmp_path, name="instance"):
        pass


def test_lock_timeout_does_not_apply_and_restarts_the_old_gateway(
    tmp_path, monkeypatch
):
    """等锁超时 = 停不下来：绝不应用，把旧网关拉回来并明确报错。"""
    from types import SimpleNamespace

    from src.upgrade import update_check
    from src.upgrade.models import CheckStatus

    applied: list = []
    restarted: list = []

    class FakeManager:
        config = SimpleNamespace(
            app_root=tmp_path / "app",
            data_dir=tmp_path,
            backup_root=tmp_path / "upgrade-backups",
        )

        def current_version(self):
            return "0.1.1", None

        def check(self, trigger=""):
            return SimpleNamespace(
                status=CheckStatus.UPDATE_AVAILABLE,
                release=SimpleNamespace(version="0.1.2"),
                message="",
            )

        def download(self, release):
            return SimpleNamespace(path=tmp_path / "pkg.zip")

        def prepare_backup(self, release):
            return "backup-1"

        def apply(self, *args, **kwargs):
            applied.append((args, kwargs))

    monkeypatch.setattr(update_check, "build_manager_from_env", lambda: FakeManager())
    monkeypatch.setattr(
        orchestrator,
        "stop_recorded_gateway",
        lambda **_kwargs: {"stopped": True, "pid": 4321, "port": 8099, "steps": []},
    )
    monkeypatch.setattr(
        orchestrator,
        "wait_for_lock_release",
        lambda *_a, **_k: {
            "released": False,
            "attempts": 180,
            "error": "等待实例锁释放超时（45s，最后一次：PermissionError(errno=13)）",
        },
    )
    monkeypatch.setattr(
        orchestrator,
        "_start_and_check",
        lambda **kwargs: restarted.append(kwargs)
        or {"proc": None, "health": {"ok": True}},
    )

    journal = Journal(tmp_path)
    result = orchestrator.orchestrate(
        app_root=tmp_path / "app",
        data_dir=tmp_path,
        request={"version": "0.1.2"},
        journal=journal,
        port=8099,
        python=sys.executable,
        stop_timeout=45.0,
    )

    assert result["phase"] == "failed_not_applied"
    assert applied == [], "等锁超时后绝不能应用"
    assert "PermissionError(errno=13)" in result["error"]
    assert "已重新启动旧版本" in result["error"]
    assert restarted, "必须把旧网关重新拉起来"
    assert journal.data["phase"] == "failed_not_applied"
    assert (tmp_path / "upgrade-state" / "orchestration.json").is_file()
