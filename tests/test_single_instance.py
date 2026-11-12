"""单实例锁：同一数据目录只允许一个网关进程。

这些用例用**真实子进程**验证互斥（``msvcrt`` / ``fcntl`` 的语义只在跨进程时
才成立，同进程内伪造的锁测不出任何东西）。
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]

_HOLDER = r"""
import sys, time
sys.path.insert(0, r"{repo}")
from src.common.instance_lock import InstanceLock
lock = InstanceLock(r"{root}")
lock.acquire()
lock.publish(pid=__import__("os").getpid(), url="http://127.0.0.1:18080",
             started_at="2026-01-01T00:00:00+08:00")
print("HELD", flush=True)
time.sleep(30)
"""

_SECOND = r"""
import sys
sys.path.insert(0, r"{repo}")
from src.common.instance_lock import InstanceLock, InstanceLockBusy
try:
    InstanceLock(r"{root}").acquire()
except InstanceLockBusy as busy:
    print(busy.describe())
    raise SystemExit(3)
print("ACQUIRED")
"""


def _env(root: Path) -> dict:
    env = dict(os.environ)
    env["LIUHAO_INSTANCE_DIR"] = str(root)
    env["PYTHONPATH"] = str(REPO_ROOT)
    return env


def _wait_for_holder(proc: subprocess.Popen, timeout: float = 10.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise AssertionError(f"holder exited early: {proc.returncode}")
        if (proc.stdout and proc.stdout.readline().strip() == "HELD"):
            return
    raise AssertionError("holder did not report HELD in time")


@pytest.mark.timeout(60)
def test_second_process_is_refused_with_readable_message(tmp_path: Path) -> None:
    holder = subprocess.Popen(
        [sys.executable, "-c", _HOLDER.format(repo=REPO_ROOT, root=tmp_path)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=_env(tmp_path),
    )
    try:
        _wait_for_holder(holder)
        second = subprocess.run(
            [sys.executable, "-c", _SECOND.format(repo=REPO_ROOT, root=tmp_path)],
            capture_output=True,
            text=True,
            env=_env(tmp_path),
            timeout=30,
        )
    finally:
        holder.kill()
        holder.wait(timeout=10)

    assert second.returncode == 3, second.stderr
    assert "已经在运行" in second.stdout
    assert "18080" in second.stdout  # 元信息里带着正在运行的地址，用户能直接打开
    assert "为避免两个进程同时写同一份数据" in second.stdout


@pytest.mark.timeout(60)
def test_lock_is_released_when_holder_dies(tmp_path: Path) -> None:
    holder = subprocess.Popen(
        [sys.executable, "-c", _HOLDER.format(repo=REPO_ROOT, root=tmp_path)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=_env(tmp_path),
    )
    try:
        _wait_for_holder(holder)
        holder.kill()
        holder.wait(timeout=10)
    finally:
        if holder.poll() is None:
            holder.kill()

    # 进程死亡后内核自动释放锁：不需要"清理僵尸锁"这一步。
    again = subprocess.run(
        [sys.executable, "-c", _SECOND.format(repo=REPO_ROOT, root=tmp_path)],
        capture_output=True,
        text=True,
        env=_env(tmp_path),
        timeout=30,
    )
    assert again.returncode == 0, again.stdout + again.stderr
    assert "ACQUIRED" in again.stdout


@pytest.mark.timeout(60)
def test_gateway_leaves_when_launcher_is_killed() -> None:
    """启动器被强杀时，网关不能变成占着端口的孤儿（父进程看护）。"""
    import threading

    from src.common.parent_watch import watch_parent

    parent = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    fired = threading.Event()
    thread = watch_parent(None, parent_pid=parent.pid, interval=0.2, on_exit=fired.set)
    assert thread is not None
    try:
        assert not fired.wait(1.0)  # 父进程还活着，不能提前退出
    finally:
        parent.kill()
        parent.wait(timeout=10)
    assert fired.wait(10), "父进程退出后网关没有收到停机信号"


def test_stale_metadata_without_lock_is_not_treated_as_running(tmp_path: Path) -> None:
    """锁空闲时，残留的 instance.json 不能让探测误报"已在运行"。"""
    from src.common.instance_lock import InstanceLock

    (tmp_path / "run").mkdir(parents=True)
    (tmp_path / "run" / "instance.json").write_text(
        '{"pid": 999999, "url": "http://127.0.0.1:18080"}', encoding="utf-8"
    )
    assert InstanceLock(tmp_path).probe() is None


@pytest.mark.timeout(60)
def test_tray_refuses_when_cli_instance_holds_lock(tmp_path: Path, monkeypatch) -> None:
    """托盘与命令行各起一个：托盘要在拉起子进程之前给出可读提示，不空跑。"""
    from src.common.instance_lock import InstanceLock
    from src.desktop.service import GatewayService

    monkeypatch.setenv("LIUHAO_INSTANCE_ROOT", str(tmp_path))
    holder = InstanceLock(tmp_path)
    holder.acquire()
    holder.publish(
        pid=os.getpid(),
        url="http://127.0.0.1:18080",
        started_at="2026-01-01T00:00:00+08:00",
    )
    try:
        service = GatewayService(root=tmp_path)
        assert service.start() is False
        assert service.last_error and "已经在运行" in service.last_error
        assert service._proc is None, "托盘不应拉起一个注定被拒绝的子进程"
    finally:
        holder.clear_metadata()
        holder.release()
