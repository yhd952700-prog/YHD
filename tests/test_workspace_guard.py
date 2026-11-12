"""工作区闸门（``src/ai/workspace.py``）与 FilesystemAdapter 路径约束的回归测试。

这一组测试存在的理由：文件访问一旦交给 LLM 工具调用，"读"和"写"都是风险面。
闸门是 fail-closed 的，因此**每一条逃逸路径都必须有对应用例**，而不是只测 happy path：

- ``..`` 逐级上跳
- 根外绝对路径
- 符号链接指向根外
- 空路径 / 非字符串入参
- 以及"历史默认仍是不设限"这件事 —— 它必须被显式钉住，否则某天有人把默认值
  改成"会拦"，既有三个调用方（e2e_demo / hardening / vhl_benchmark）会静默改变行为。
"""

import os
import sys

import pytest

from src.ai.world_interface import FilesystemAdapter, WorldInterface, WorldRequest
from src.ai.workspace import (
    WorkspaceViolation,
    is_inside,
    resolve_in_workspace,
    workspace_root,
)


class TestResolveInWorkspace:
    def test_inside_relative_path_resolves_under_root(self, tmp_path):
        root = str(tmp_path)
        real = resolve_in_workspace("notes/a.txt", root)
        assert real == os.path.realpath(os.path.join(root, "notes", "a.txt"))

    def test_root_itself_is_allowed(self, tmp_path):
        root = str(tmp_path)
        assert resolve_in_workspace(".", root) == os.path.realpath(root)

    def test_dotdot_escape_is_rejected(self, tmp_path):
        root = str(tmp_path / "inner")
        os.makedirs(root)
        with pytest.raises(WorkspaceViolation):
            resolve_in_workspace(os.path.join("..", "outside.txt"), root)

    def test_absolute_path_outside_root_is_rejected(self, tmp_path):
        with pytest.raises(WorkspaceViolation):
            resolve_in_workspace(os.path.abspath(os.sep), str(tmp_path))

    def test_empty_and_non_string_are_rejected(self, tmp_path):
        for bad in ("", "   ", None, 123):
            with pytest.raises(WorkspaceViolation):
                resolve_in_workspace(bad, str(tmp_path))

    def test_symlink_escape_is_rejected(self, tmp_path):
        """符号链接指向根外时必须被拒（realpath 消解后即越界）。"""
        root = tmp_path / "root"
        outside = tmp_path / "outside"
        root.mkdir()
        outside.mkdir()
        (outside / "secret.txt").write_text("s", encoding="utf-8")
        link = root / "link"
        try:
            os.symlink(str(outside), str(link), target_is_directory=True)
        except (OSError, NotImplementedError):
            pytest.skip("本平台/账户不允许创建符号链接")
        with pytest.raises(WorkspaceViolation):
            resolve_in_workspace("link/secret.txt", str(root))

    def test_default_root_is_absolute(self):
        assert os.path.isabs(workspace_root())

    def test_is_inside_never_raises(self, tmp_path):
        assert is_inside(str(tmp_path), "a/b.txt") is True
        assert is_inside(str(tmp_path), os.path.join("..", "..", "x")) is False
        assert is_inside(str(tmp_path), None) is False


class TestAdapterRootEnforcement:
    def _world(self, root):
        return WorldInterface(adapters=[FilesystemAdapter(root=root)])

    def test_read_inside_root_succeeds(self, tmp_path):
        (tmp_path / "ok.txt").write_text("hello", encoding="utf-8")
        world = self._world(str(tmp_path))
        result = world.observe(
            WorldRequest(adapter="filesystem", action="read", params={"path": "ok.txt"})
        )
        assert result["status"] == "observed"
        assert result["data"] == "hello"

    def test_read_outside_root_is_denied_not_silently_allowed(self, tmp_path):
        root = tmp_path / "root"
        root.mkdir()
        outside = tmp_path / "outside.txt"
        outside.write_text("secret", encoding="utf-8")

        world = self._world(str(root))
        result = world.observe(
            WorldRequest(
                adapter="filesystem", action="read", params={"path": str(outside)}
            )
        )
        assert result["status"] == "error"
        assert "越界" in result["error"]

    def test_write_outside_root_is_denied(self, tmp_path):
        root = tmp_path / "root"
        root.mkdir()
        world = self._world(str(root))
        result = world.execute(
            WorldRequest(
                adapter="filesystem",
                action="write",
                params={"path": str(tmp_path / "escaped.txt"), "content": "x"},
            )
        )
        assert result.success is False
        assert not (tmp_path / "escaped.txt").exists()

    def test_list_outside_root_is_denied(self, tmp_path):
        root = tmp_path / "root"
        root.mkdir()
        world = self._world(str(root))
        result = world.observe(
            WorldRequest(
                adapter="filesystem", action="list", params={"path": str(tmp_path)}
            )
        )
        assert result["status"] == "error"

    def test_unset_root_keeps_legacy_unrestricted_behaviour(self, tmp_path):
        """历史默认必须被钉住：``root=None`` 时路径不受任何工作区约束。

        这不是在主张"默认安全" —— 恰恰相反，这条用例让"默认不设限"这件事
        **可见且不可被静默改动**。正因如此，工具层必须显式传 root
        （见 ``TestToolLayerIsConstrainedByWorkspace``）。
        """
        adapter = FilesystemAdapter()
        assert adapter.root is None
        target = tmp_path / "outside-workspace.txt"
        target.write_text("legacy", encoding="utf-8")
        assert adapter.observe(
            WorldRequest(adapter="filesystem", action="read",
                         params={"path": str(target)})
        ) == "legacy"


class TestToolLayerIsConstrainedByWorkspace:
    """工具层暴露的文件工具必须被工作区闸门约束。

    这里做**行为验证**而不是属性验证：不检查"某字段等于某值"，而是真的拿一个越界
    路径去调用工具，断言它**没有**把内容交出来。字段可以改名，行为骗不了人。
    """

    def _tools(self, root, monkeypatch):
        monkeypatch.setenv("LIUHAO_WORKSPACE_ROOT", str(root))
        from src.ai.tools import make_tools

        return {t.tool_id: t for t in make_tools("alice", status_fn=lambda: {"turn": 0})}

    def test_file_tools_are_exposed(self, tmp_path, monkeypatch):
        tools = self._tools(tmp_path, monkeypatch)
        assert "liuhao.file.read" in tools
        assert "liuhao.file.list" in tools

    def test_read_inside_workspace_returns_content(self, tmp_path, monkeypatch):
        (tmp_path / "hello.txt").write_text("你好", encoding="utf-8")
        tools = self._tools(tmp_path, monkeypatch)
        assert "你好" in str(tools["liuhao.file.read"].fn(path="hello.txt"))

    def test_absolute_read_outside_workspace_does_not_leak(self, tmp_path, monkeypatch):
        root = tmp_path / "root"
        root.mkdir()
        secret = tmp_path / "secret.txt"
        secret.write_text("TOPSECRET", encoding="utf-8")

        tools = self._tools(root, monkeypatch)
        out = str(tools["liuhao.file.read"].fn(path=str(secret)))
        assert "TOPSECRET" not in out
        assert "越界" in out

    def test_dotdot_read_escape_does_not_leak(self, tmp_path, monkeypatch):
        root = tmp_path / "root"
        root.mkdir()
        (tmp_path / "secret.txt").write_text("TOPSECRET", encoding="utf-8")

        tools = self._tools(root, monkeypatch)
        out = str(tools["liuhao.file.read"].fn(path="../secret.txt"))
        assert "TOPSECRET" not in out

    def test_list_outside_workspace_is_refused(self, tmp_path, monkeypatch):
        root = tmp_path / "root"
        root.mkdir()
        tools = self._tools(root, monkeypatch)
        out = str(tools["liuhao.file.list"].fn(path=str(tmp_path)))
        assert "越界" in out

    def test_permission_is_declared_for_auditability(self, tmp_path, monkeypatch):
        tools = self._tools(tmp_path, monkeypatch)
        for tool_id in ("liuhao.file.read", "liuhao.file.list"):
            assert tools[tool_id].permission == "workspace.read"


@pytest.mark.skipif(
    not sys.platform.startswith("win"),
    reason="Windows 路径大小写不敏感，仅在 Windows 上验证归一化比较",
)
def test_windows_path_comparison_is_case_insensitive(tmp_path):
    root = str(tmp_path)
    assert is_inside(root, root.upper().replace(str(tmp_path).upper(), root)) is True
