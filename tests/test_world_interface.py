"""World Interface — MASTER-SPEC Phase 15 tests."""
import os
import sys

import pytest

from src.ai.world_interface import (
    WorldRequest,
    FilesystemAdapter,
    ShellAdapter,
    WorldInterface,
)


def _world(**kwargs):
    return WorldInterface(
        adapters=[FilesystemAdapter(), ShellAdapter()],
        **kwargs,
    )


class TestFilesystemAdapter:
    def test_read_and_write_roundtrip(self, tmp_path):
        fs = FilesystemAdapter()
        path = str(tmp_path / "note.txt")

        fs.execute(WorldRequest(adapter="filesystem", action="write",
                                params={"path": path, "content": "hello world"}))
        data = fs.observe(WorldRequest(adapter="filesystem", action="read",
                                       params={"path": path}))
        assert data == "hello world"

    def test_list_directory(self, tmp_path):
        (tmp_path / "a.txt").write_text("a")
        (tmp_path / "b.txt").write_text("b")
        fs = FilesystemAdapter()
        data = fs.observe(WorldRequest(adapter="filesystem", action="list",
                                       params={"path": str(tmp_path)}))
        assert sorted(data) == ["a.txt", "b.txt"]


class TestShellAdapter:
    def test_run_echo(self):
        sh = ShellAdapter()
        # 用 sys.executable 而非 `echo`：`echo` 在 Windows 上不是可执行文件，
        # 只在 shell 内建存在 —— argv 模式（shell=False）下会 FileNotFoundError。
        # 本适配器自 2026-09-13 起默认 argv 模式，故测试也须跨平台。
        out = sh.execute(WorldRequest(adapter="shell", action="run",
                                      params={"command": f'"{sys.executable}" -c "print(42)"'}))
        assert out["stdout"].strip() == "42"
        assert out["returncode"] == 0

    def test_shell_metacharacters_are_not_interpreted(self, tmp_path, monkeypatch):
        """默认路径必须关掉命令注入面。

        若仍走 ``shell=True``，分号会开启第二个命令并真的在 cwd 落下文件；
        argv 模式下整串只是 python 的普通实参，副作用不可能发生。
        """
        monkeypatch.chdir(tmp_path)
        sh = ShellAdapter()
        marker = "pwned_from_injection.txt"
        out = sh.execute(WorldRequest(
            adapter="shell", action="run",
            params={"command": f'"{sys.executable}" -c "print(42)"; touch {marker}'}))
        assert out["returncode"] == 0
        # 正面证据：注入命令没有被 shell 解释执行
        assert not (tmp_path / marker).exists()

    def test_shell_true_requires_explicit_opt_in(self):
        """`shell=True` 这条危险路径必须显式开启，不能是默认行为。"""
        sh = ShellAdapter()
        # 不传 shell -> argv；传 shell=True -> 走 shell（此处只验证两者都能跑出结果）
        out = sh.execute(WorldRequest(adapter="shell", action="run",
                                      params={"command": f'"{sys.executable}" -c "print(43)"',
                                              "shell": True}))
        assert out["stdout"].strip() == "43"


class TestWorldInterface:
    def test_validate_rejects_unknown_adapter(self):
        w = _world()
        assert w.validate(WorldRequest(adapter="nope", action="read")) is False

    def test_validate_rejects_unsupported_action(self):
        w = _world()
        # filesystem has no "run" action
        assert w.validate(WorldRequest(adapter="filesystem", action="run")) is False

    def test_observe_full_chain(self, tmp_path):
        w = _world()
        path = str(tmp_path / "f.txt")
        (tmp_path / "f.txt").write_text("data")

        out = w.observe(WorldRequest(adapter="filesystem", action="read",
                                     params={"path": path}))
        assert out["status"] == "observed"
        assert out["data"] == "data"

    def test_observe_invalid_request(self):
        w = _world()
        out = w.observe(WorldRequest(adapter="nope", action="read"))
        assert out["status"] == "invalid"

    def test_execute_full_chain_returns_action_result(self, tmp_path):
        w = _world()
        path = str(tmp_path / "out.txt")
        result = w.execute(WorldRequest(adapter="filesystem", action="write",
                                        params={"path": path, "content": "x"}))
        assert result.success is True
        assert result.output == {"written": path}
        assert os.path.exists(path)

    def test_authorize_denies(self, tmp_path):
        w = _world(authorize=lambda req: False)
        path = str(tmp_path / "out.txt")
        result = w.execute(WorldRequest(adapter="filesystem", action="write",
                                        params={"path": path, "content": "x"}))
        assert result.success is False
        assert "denied" in result.error
        assert not os.path.exists(path)

    def test_verify_defaults_to_success(self):
        w = _world()
        from src.kernels.execution import ActionResult
        assert w.verify(ActionResult(action_id="a", success=True)) is True
        assert w.verify(ActionResult(action_id="a", success=False)) is False

    def test_verify_injected(self):
        seen = []
        w = _world(verify=lambda r: seen.append(r) or r.output == "ok")
        from src.kernels.execution import ActionResult
        assert w.verify(ActionResult(action_id="a", success=True, output="ok")) is True
        assert seen  # injected verifier actually ran
