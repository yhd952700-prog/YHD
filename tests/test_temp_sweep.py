"""启动时有界清扫的行为护栏。

三条核心承诺：
1. **只清自己人**：命名模式之外的任何文件都不碰（含"看起来很像但没有 pid"的）。
2. **只清死掉的**：pid 仍存活、或无法证明已退出的，一律保留。
3. **只清够旧的**：未到保留期的一律保留；单次处理数量有上限。
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

from src.common import temp_sweep

REPO_ROOT = Path(__file__).resolve().parents[1]

#: 构造"明显过期"的 mtime（30 天前）。
OLD_SECONDS = 30 * 24 * 3600


def _exited_pid() -> int:
    """拿一个确定已经退出的 pid（起个子进程等它结束）。"""
    process = subprocess.Popen([sys.executable, "-c", "pass"])
    process.wait(timeout=30)
    return process.pid


def _age(path, seconds: float = OLD_SECONDS) -> None:
    stamp = time.time() - seconds
    os.utime(path, (stamp, stamp))


def _make_dir(root, name: str) -> None:
    target = root / name
    (target / "payload").mkdir(parents=True)
    (target / "payload" / "artifact.bin").write_text("x", encoding="utf-8")
    _age(target)


def _make_file(root, name: str) -> None:
    target = root / name
    target.write_text("payload", encoding="utf-8")
    _age(target)


def test_only_dead_and_old_product_artifacts_are_removed(tmp_path, monkeypatch):
    """第一组（死的旧残留）清掉；第二组（活的/太新）与第三组（别人家的）都不动。"""
    monkeypatch.setenv(temp_sweep.MIN_AGE_ENV, "3600")
    temp_dir = tmp_path / "temp"
    temp_dir.mkdir()
    dead_pid = _exited_pid()
    live_pid = os.getpid()

    # 第一组：死掉的旧残留 —— 唯一应当被清的。
    removed_names = [
        f"liuhao-upgrade-stage-{dead_pid}-dead1",
        f"liuhao_reminder_balloon_{dead_pid}.ps1",
        f"liuhao_reminder_{dead_pid}_payload.json",
        f"liuhao-selfmod-test-{dead_pid}-run.log",
    ]
    _make_dir(temp_dir, removed_names[0])
    for name in removed_names[1:]:
        _make_file(temp_dir, name)

    # 第二组：pid 仍存活（即使 mtime 很旧）、或已死但还很新 —— 都保留。
    live_name = f"liuhao-selfcheck-{live_pid}-live1"
    _make_dir(temp_dir, live_name)
    fresh_name = f"liuhao-restore-stage-{dead_pid}-fresh1"
    fresh_dir = temp_dir / fresh_name
    fresh_dir.mkdir()

    # 第三组：别人家的文件 / 名字像但没有 pid —— 一律不碰。
    foreign_dir = temp_dir / "other-app-cache"
    foreign_dir.mkdir()
    _age(foreign_dir)
    _make_file(temp_dir, "notes.txt")
    _make_dir(temp_dir, "liuhao-upgrade-stage-nopid")
    _make_file(temp_dir, "liuhao_reminder_backup.json")
    # 目录模式的文件：名字像目录但没有 pid 段，也不该被当成目录删掉。
    _make_file(temp_dir, f"liuhao-restore-stage-{dead_pid}-looks-like-dir.txt")

    stats = temp_sweep.sweep_stale_temp_artifacts(directory=str(temp_dir))

    for name in removed_names:
        assert not (temp_dir / name).exists(), f"死掉的旧残留应被清理：{name}"
    assert (temp_dir / live_name).is_dir(), "pid 仍存活的产物必须保留"
    assert (temp_dir / fresh_name).is_dir(), "未到保留期的产物必须保留"
    assert foreign_dir.is_dir()
    assert (temp_dir / "notes.txt").is_file()
    assert (temp_dir / "liuhao-upgrade-stage-nopid").is_dir()
    assert (temp_dir / "liuhao_reminder_backup.json").is_file()
    assert (temp_dir / f"liuhao-restore-stage-{dead_pid}-looks-like-dir.txt").is_file()

    assert stats["removed"] == len(removed_names)
    assert stats["kept"] >= 2


def test_scan_is_bounded_by_candidate_limit(tmp_path, monkeypatch):
    monkeypatch.setenv(temp_sweep.MIN_AGE_ENV, "3600")
    temp_dir = tmp_path / "temp"
    temp_dir.mkdir()
    dead_pid = _exited_pid()
    for index in range(6):
        _make_dir(temp_dir, f"liuhao-restore-stage-{dead_pid}-bounded{index}")

    stats = temp_sweep.sweep_stale_temp_artifacts(
        directory=str(temp_dir), max_candidates=2
    )

    assert stats["matched"] == 2, "扫描到的数量必须被上限截住"
    assert stats["removed"] == 2
    assert len(list(temp_dir.iterdir())) == 4, "超出的候选必须留到下次启动"


def test_unchanged_temp_dir_skips_the_scan(tmp_path, monkeypatch):
    """临时目录没变化时不再整轮遍历；一旦变化立刻恢复扫描。

    这是启动开销的护栏：真实 ``%TEMP%`` 三万条时裸 ``os.scandir`` 就要 ~100ms，
    每个 CLI/网关启动都付一次是不可接受的回退。
    """
    monkeypatch.setenv(temp_sweep.MIN_AGE_ENV, "3600")
    temp_dir = tmp_path / "temp"
    temp_dir.mkdir()

    calls = {"count": 0}
    real_iterator = temp_sweep._iter_candidates

    def counting(directory):
        calls["count"] += 1
        return real_iterator(directory)

    monkeypatch.setattr(temp_sweep, "_iter_candidates", counting)

    temp_sweep.sweep_stale_temp_artifacts(directory=str(temp_dir))
    second = temp_sweep.sweep_stale_temp_artifacts(directory=str(temp_dir))
    assert calls["count"] == 1, "临时目录没变化时不应重复遍历"
    assert second == {"matched": 0, "removed": 0, "kept": 0}

    # 目录里出现新条目（任何 app 写临时文件都这样）→ 必须重新扫。
    (temp_dir / "some-new-entry").mkdir()
    temp_sweep.sweep_stale_temp_artifacts(directory=str(temp_dir))
    assert calls["count"] == 2, "临时目录变化后必须重新扫描"


def test_stamp_expires_so_daily_sweep_still_happens(tmp_path, monkeypatch):
    """缓存不是永久有效：过期后必须完整扫一次。"""
    monkeypatch.setenv(temp_sweep.MIN_AGE_ENV, "3600")
    temp_dir = tmp_path / "temp"
    temp_dir.mkdir()

    calls = {"count": 0}
    real_iterator = temp_sweep._iter_candidates

    def counting(directory):
        calls["count"] += 1
        return real_iterator(directory)

    monkeypatch.setattr(temp_sweep, "_iter_candidates", counting)

    temp_sweep.sweep_stale_temp_artifacts(directory=str(temp_dir))
    monkeypatch.setattr(temp_sweep, "_STAMP_MAX_AGE_SECONDS", 0)
    temp_sweep.sweep_stale_temp_artifacts(directory=str(temp_dir))
    assert calls["count"] == 2, "缓存过期后必须重新扫描"


def test_min_age_env_override_and_invalid_fallback(monkeypatch, caplog):
    monkeypatch.setenv(temp_sweep.MIN_AGE_ENV, "120")
    assert temp_sweep.min_age_seconds() == 120

    monkeypatch.setenv(temp_sweep.MIN_AGE_ENV, "not-a-number")
    assert temp_sweep.min_age_seconds() == temp_sweep.DEFAULT_MIN_AGE_SECONDS
    assert any("不是合法秒数" in record.getMessage() for record in caplog.records)

    monkeypatch.setenv(temp_sweep.MIN_AGE_ENV, "-1")
    assert temp_sweep.min_age_seconds() == temp_sweep.DEFAULT_MIN_AGE_SECONDS


def test_startup_sweep_runs_once_per_process(monkeypatch):
    calls: list[int] = []
    monkeypatch.setattr(temp_sweep, "_swept", False)
    monkeypatch.setattr(
        temp_sweep,
        "sweep_stale_temp_artifacts",
        lambda **_kwargs: calls.append(1) or {"matched": 0, "removed": 0, "kept": 0},
    )

    temp_sweep.run_startup_temp_sweep()
    temp_sweep.run_startup_temp_sweep()

    assert calls == [1], "同一进程内启动清扫只跑一次"


def test_startup_sweep_never_breaks_startup(monkeypatch):
    monkeypatch.setattr(temp_sweep, "_swept", False)

    def _boom(**_kwargs):
        raise OSError("simulated sweep failure")

    monkeypatch.setattr(temp_sweep, "sweep_stale_temp_artifacts", _boom)

    assert temp_sweep.run_startup_temp_sweep() == {
        "matched": 0,
        "removed": 0,
        "kept": 0,
    }


def test_killed_cli_residue_is_swept_by_next_cli_run(tmp_path):
    """模拟"CLI 被强杀 → 下次 CLI 运行清理"。

    子进程的 TEMP/TMP 指向独立目录，所以这里造的是**真实**的残留现场，不是
    mock：死 pid 的升级暂存目录 + 两个别人家的文件，然后真的跑一次
    ``python -m src.cli backup list``。
    """
    scratch = tmp_path / "temp"
    scratch.mkdir()
    dead_pid = _exited_pid()

    residue = scratch / f"liuhao-upgrade-stage-{dead_pid}-killed"
    (residue / "payload").mkdir(parents=True)
    (residue / "payload" / "artifact.bin").write_text("x", encoding="utf-8")
    _age(residue)

    foreign_dir = scratch / "other-app-cache"
    foreign_dir.mkdir()
    (foreign_dir / "keep.bin").write_text("keep", encoding="utf-8")
    _age(foreign_dir)
    foreign_file = scratch / "notes.txt"
    foreign_file.write_text("keep", encoding="utf-8")
    _age(foreign_file)

    env = os.environ.copy()
    env["TEMP"] = str(scratch)
    env["TMP"] = str(scratch)
    env["TMPDIR"] = str(scratch)
    env["LIUHAO_TEMP_SWEEP_MIN_AGE_SECONDS"] = "3600"
    env["LIUHAO_DATA_DIR"] = str(tmp_path / "data")

    completed = subprocess.run(
        [sys.executable, "-m", "src.cli", "backup", "list"],
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert completed.returncode == 0, completed.stderr

    assert not residue.exists(), "下次 CLI 运行必须清掉被强杀进程的残留"
    assert foreign_dir.is_dir(), "别人家的目录一根不动"
    assert (foreign_dir / "keep.bin").is_file()
    assert foreign_file.is_file(), "别人家的文件一根不动"


# ── 取证沙箱（liuhao-maintenance-evidence）────────────────────────────────
#
# 它没有 pid，判据必须更严：完全同名 + 本产品内容指纹 + 30 天保留期 +
# 人工保留标记 + 删除前改名隔离。以下逐条验证"宁可不删"。


def _age_tree(root, seconds: float) -> None:
    """把整棵树（含子目录）的 mtime 拨到 ``seconds`` 秒之前。"""
    stamp = time.time() - seconds
    for current, _dirs, files in os.walk(root, topdown=False):
        for name in files:
            os.utime(os.path.join(current, name), (stamp, stamp))
        os.utime(current, (stamp, stamp))


def _make_evidence_sandbox(root, *, name: str | None = None):
    """造一个带内容指纹的取证沙箱：``data/master.key`` + 一个取证脚本。"""
    base = root / (name or temp_sweep.EVIDENCE_DIR_NAME)
    data = base / "data"
    data.mkdir(parents=True)
    (data / "master.key").write_bytes(b"not-a-real-key")
    (base / "capture_evidence.js").write_text("// capture", encoding="utf-8")
    return base


def _loosen_acl(path) -> None:
    """把文件还原成继承 ACL（Windows）/ 0644（POSIX）。"""
    if os.name == "nt":
        subprocess.run(
            ["icacls", str(path), "/inheritance:e", "/grant", "*S-1-5-32-545:(RX)"],
            check=True,
            capture_output=True,
            text=True,
        )
    else:
        os.chmod(path, 0o644)


def test_young_evidence_sandbox_is_kept_but_key_acl_is_tightened(tmp_path):
    from src.common.fs_permissions import is_restricted_to_current_user

    temp_dir = tmp_path / "temp"
    temp_dir.mkdir()
    sandbox = _make_evidence_sandbox(temp_dir)
    key = sandbox / "data" / "master.key"
    _loosen_acl(key)
    assert not is_restricted_to_current_user(key), "前置条件：密钥 ACL 应处于过宽状态"

    stats = temp_sweep.sweep_stale_evidence_sandboxes(directory=str(temp_dir))

    assert stats == {"matched": 1, "removed": 0, "kept": 1}
    assert sandbox.is_dir(), "保留期内不得删除取证沙箱"
    assert is_restricted_to_current_user(key), "保留期内也必须收紧密钥 ACL"


def test_expired_evidence_sandbox_with_fingerprint_is_removed(tmp_path):
    temp_dir = tmp_path / "temp"
    temp_dir.mkdir()
    sandbox = _make_evidence_sandbox(temp_dir)
    _age_tree(sandbox, 40 * 24 * 3600)

    stats = temp_sweep.sweep_stale_evidence_sandboxes(directory=str(temp_dir))

    assert stats == {"matched": 1, "removed": 1, "kept": 0}
    assert not sandbox.exists()
    assert not list(temp_dir.glob(f"{temp_sweep.EVIDENCE_DIR_NAME}.deleting-*"))


def test_keep_marker_prevents_removal_even_when_expired(tmp_path):
    temp_dir = tmp_path / "temp"
    temp_dir.mkdir()
    sandbox = _make_evidence_sandbox(temp_dir)
    (sandbox / "KEEP").write_text("manual", encoding="utf-8")
    _age_tree(sandbox, 40 * 24 * 3600)

    stats = temp_sweep.sweep_stale_evidence_sandboxes(directory=str(temp_dir))

    assert stats == {"matched": 1, "removed": 0, "kept": 1}
    assert sandbox.is_dir(), "人工保留标记优先于保留期"


def test_same_named_dir_without_fingerprint_is_never_touched(tmp_path):
    temp_dir = tmp_path / "temp"
    temp_dir.mkdir()
    foreign = temp_dir / temp_sweep.EVIDENCE_DIR_NAME
    foreign.mkdir()
    (foreign / "notes.txt").write_text("someone else's", encoding="utf-8")
    _age_tree(foreign, 40 * 24 * 3600)

    stats = temp_sweep.sweep_stale_evidence_sandboxes(directory=str(temp_dir))

    assert stats == {"matched": 0, "removed": 0, "kept": 0}
    assert foreign.is_dir(), "同名但没有本产品指纹的目录一根不动"


def test_only_direct_child_of_temp_dir_is_considered(tmp_path):
    temp_dir = tmp_path / "temp"
    temp_dir.mkdir()
    nested = _make_evidence_sandbox(temp_dir / "someone", name=temp_sweep.EVIDENCE_DIR_NAME)
    _age_tree(nested, 40 * 24 * 3600)

    stats = temp_sweep.sweep_stale_evidence_sandboxes(directory=str(temp_dir))

    assert stats == {"matched": 0, "removed": 0, "kept": 0}
    assert nested.is_dir(), "只认临时目录的直接子项，不递归到别人家目录里"


def test_fresh_content_inside_old_sandbox_is_kept(tmp_path):
    temp_dir = tmp_path / "temp"
    temp_dir.mkdir()
    sandbox = _make_evidence_sandbox(temp_dir)
    _age_tree(sandbox, 40 * 24 * 3600)
    # 目录整体很旧，但里面刚写了一份新证据 → 仍在活跃使用。
    (sandbox / "data" / "lottery_20261109.json").write_text("{}", encoding="utf-8")

    stats = temp_sweep.sweep_stale_evidence_sandboxes(directory=str(temp_dir))

    assert stats == {"matched": 1, "removed": 0, "kept": 1}
    assert sandbox.is_dir()


def test_busy_sandbox_is_kept_when_rename_fails(tmp_path, monkeypatch):
    temp_dir = tmp_path / "temp"
    temp_dir.mkdir()
    sandbox = _make_evidence_sandbox(temp_dir)
    _age_tree(sandbox, 40 * 24 * 3600)

    real_rename = os.rename

    def deny_quarantine(source, destination):
        if os.path.normcase(str(source)) == os.path.normcase(str(sandbox)):
            raise PermissionError("directory is in use")
        return real_rename(source, destination)

    monkeypatch.setattr(temp_sweep.os, "rename", deny_quarantine)

    stats = temp_sweep.sweep_stale_evidence_sandboxes(directory=str(temp_dir))

    assert stats == {"matched": 1, "removed": 0, "kept": 1}
    assert sandbox.is_dir(), "改名隔离失败 = 仍被占用，必须保留"


def test_evidence_sweep_can_be_disabled(tmp_path, monkeypatch):
    monkeypatch.setenv(temp_sweep.EVIDENCE_SWEEP_ENV, "off")
    temp_dir = tmp_path / "temp"
    temp_dir.mkdir()
    sandbox = _make_evidence_sandbox(temp_dir)
    _age_tree(sandbox, 40 * 24 * 3600)

    stats = temp_sweep.sweep_stale_evidence_sandboxes(directory=str(temp_dir))

    assert stats == {"matched": 0, "removed": 0, "kept": 0}
    assert sandbox.is_dir()


def test_full_sweep_includes_evidence_sandboxes(tmp_path, monkeypatch):
    """启动清扫（``sweep_stale_temp_artifacts``）必须把取证沙箱一并纳入。"""
    monkeypatch.setenv(temp_sweep.MIN_AGE_ENV, "3600")
    monkeypatch.setenv(temp_sweep.EVIDENCE_MIN_AGE_ENV, "3600")
    monkeypatch.setenv("LIUHAO_DATA_DIR", str(tmp_path / "data"))
    temp_dir = tmp_path / "temp"
    temp_dir.mkdir()
    sandbox = _make_evidence_sandbox(temp_dir)
    _age_tree(sandbox, OLD_SECONDS)

    stats = temp_sweep.sweep_stale_temp_artifacts(directory=str(temp_dir))

    assert stats["matched"] == 1
    assert stats["removed"] == 1
    assert not sandbox.exists()


def test_cached_sweep_still_tightens_evidence_key(tmp_path, monkeypatch):
    """临时目录没变化（命中缓存）时，取证沙箱的 ACL 收紧仍要执行。"""
    from src.common.fs_permissions import is_restricted_to_current_user

    monkeypatch.setenv("LIUHAO_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(temp_sweep, "_sweep_is_redundant", lambda *_args: True)
    temp_dir = tmp_path / "temp"
    temp_dir.mkdir()
    sandbox = _make_evidence_sandbox(temp_dir)
    key = sandbox / "data" / "master.key"
    _loosen_acl(key)
    assert not is_restricted_to_current_user(key)

    stats = temp_sweep.sweep_stale_temp_artifacts(directory=str(temp_dir))

    assert stats == {"matched": 1, "removed": 0, "kept": 1}
    assert is_restricted_to_current_user(key)


def test_evidence_roots_cover_both_user_temp_locations(tmp_path, monkeypatch):
    process_temp = tmp_path / "process-temp"
    process_temp.mkdir()
    localappdata = tmp_path / "LocalAppData"
    (localappdata / "Temp").mkdir(parents=True)
    monkeypatch.setattr(temp_sweep.tempfile, "gettempdir", lambda: str(process_temp))
    monkeypatch.setenv("LOCALAPPDATA", str(localappdata))

    roots = temp_sweep._evidence_roots(None)

    assert str(process_temp) in roots
    assert str(localappdata / "Temp") in roots
    assert temp_sweep._evidence_roots(str(process_temp)) == (str(process_temp),), (
        "显式传入目录时只看它，避免测试/调用方被环境干扰"
    )
