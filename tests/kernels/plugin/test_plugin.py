"""Plugin Kernel unit tests.

Uses an isolated ``registry_path`` (tmp_path) so the on-disk
``registry_index.json`` does not leak into the project tree or other
tests. Covers register/get, unregister, discovery filters, activation
failure when no module is present, deactivate guards, listing, and
capability compatibility checking.
"""
import pytest

from src.kernels.plugin import PluginRegistry, PluginStatus


@pytest.fixture
def reg(tmp_path) -> PluginRegistry:
    return PluginRegistry(registry_path=str(tmp_path / "plugins"))


def pid(name="p", kernel="memory", ver="1.0.0"):
    return f"{kernel}:{name}:{ver}"


# =====================================================================
# Register / get
# =====================================================================

class TestRegister:
    def test_register_returns_registered_status(self, reg):
        info = reg.register_plugin("myplug", "1.0.0", "memory", ["cap1"], "L3")
        assert info.status == PluginStatus.REGISTERED
        assert info.plugin_id == pid("myplug", "memory", "1.0.0")
        assert info.name == "myplug"

    def test_get_plugin(self, reg):
        reg.register_plugin("p", "1.0.0", "memory", ["c"], "L3")
        got = reg.get_plugin(pid())
        assert got is not None
        assert got.name == "p"

    def test_register_invalid_scope_rejected(self, reg):
        with pytest.raises(ValueError, match="Invalid plugin scope"):
            reg.register_plugin("p", "1.0.0", "memory", ["c"], "L99")


# =====================================================================
# Unregister
# =====================================================================

class TestUnregister:
    def test_unregister_sets_unloaded(self, reg):
        reg.register_plugin("p", "1.0.0", "memory", ["c"], "L3")
        assert reg.unregister_plugin(pid()) is True
        assert reg.get_plugin(pid()).status == PluginStatus.UNLOADED

    def test_unregister_unknown_returns_false(self, reg):
        assert reg.unregister_plugin("memory:ghost:1.0.0") is False


# =====================================================================
# Discovery
# =====================================================================

class TestDiscover:
    def test_discover_by_kernel_type(self, reg):
        reg.register_plugin("a", "1.0.0", "memory", ["c"], "L3")
        reg.register_plugin("b", "2.0.0", "network", ["c"], "L5")
        res = reg.discover_plugins(kernel_type="memory")
        assert len(res) == 1 and res[0].name == "a"

    def test_discover_by_scope(self, reg):
        reg.register_plugin("a", "1.0.0", "memory", ["c"], "L3")
        reg.register_plugin("b", "2.0.0", "network", ["c"], "L5")
        res = reg.discover_plugins(scope="L5")
        assert len(res) == 1 and res[0].name == "b"

    def test_discover_by_min_version(self, reg):
        reg.register_plugin("a", "1.0.0", "memory", ["c"], "L3")
        reg.register_plugin("b", "2.0.0", "network", ["c"], "L5")
        res = reg.discover_plugins(min_version="1.5.0")
        assert len(res) == 1 and res[0].name == "b"


# =====================================================================
# Version bounds are semantic (PEP 440), not lexicographic
# =====================================================================

class TestVersionBoundsAreSemantic:
    """String comparison put ``1.10.0`` *below* ``1.9.0``; bounds now use packaging."""

    def test_min_version_uses_semantic_order(self, reg):
        reg.register_plugin("old", "1.9.0", "memory", ["c"], "L3")
        reg.register_plugin("new", "1.10.0", "memory", ["c"], "L3")
        res = reg.discover_plugins(min_version="1.9.5")
        # Lexicographically "1.10.0" < "1.9.5", so the old code dropped BOTH.
        assert {p.name for p in res} == {"new"}

    def test_max_version_uses_semantic_order(self, reg):
        reg.register_plugin("old", "1.9.0", "memory", ["c"], "L3")
        reg.register_plugin("new", "1.10.0", "memory", ["c"], "L3")
        res = reg.discover_plugins(max_version="1.9.5")
        # Lexicographically "1.10.0" > "1.9.5", so the old code kept BOTH.
        assert {p.name for p in res} == {"old"}


# =====================================================================
# Import must be side-effect free (lazy singleton; no ./plugins mkdir)
# =====================================================================

class TestImportIsSideEffectFree:
    def test_import_does_not_instantiate_or_touch_cwd(self, tmp_path):
        import os
        import subprocess
        import sys
        from pathlib import Path

        repo_root = Path(__file__).resolve().parents[3]
        code = (
            "import src.kernels.plugin as p\n"
            "print('INSTANCE_IS_NONE', p._global_plugin_registry is None)\n"
            "import os\n"
            "print('PLUGINS_DIR_EXISTS', os.path.isdir('plugins'))\n"
        )
        env = dict(os.environ)
        env["PYTHONPATH"] = str(repo_root)
        out = subprocess.run(
            [sys.executable, "-c", code],
            cwd=str(tmp_path), env=env, capture_output=True, text=True,
        )
        assert out.returncode == 0, out.stderr
        assert "INSTANCE_IS_NONE True" in out.stdout
        assert "PLUGINS_DIR_EXISTS False" in out.stdout


# =====================================================================
# Activation / deactivation
# =====================================================================

class TestActivation:
    def test_activate_without_module_sets_failed(self, reg):
        reg.register_plugin("p", "1.0.0", "memory", ["c"], "L3")
        res = reg.activate_plugin(pid())
        assert res.status == PluginStatus.FAILED
        # 失败原因必须是中文，而不是英文异常名。
        assert "未登记可加载模块入口" in res.error

    def test_deactivate_not_active_returns_false(self, reg):
        reg.register_plugin("p", "1.0.0", "memory", ["c"], "L3")
        assert reg.deactivate_plugin(pid()) is False


class TestLoadableContract:
    """No entry point means no activatable button; persisted active is not real."""

    def test_plugin_without_module_is_not_activatable(self, reg):
        info = reg.register_plugin("identity-impl", "1.0.0", "identity", ["cap"], "L3")
        assert info.activatable is False

    def test_reload_backfills_module_path_from_metadata(self, tmp_path):
        import json

        plugins_dir = tmp_path / "plugins"
        plugins_dir.mkdir()
        (plugins_dir / "registry_index.json").write_text(
            json.dumps(
                {
                    "total_plugins": 1,
                    "active_plugins": 1,
                    "plugins": [
                        {
                            "plugin_id": "builtin.example_capability",
                            "name": "builtin.example_capability",
                            "version": "1.0.0",
                            "kernel_type": "builtin",
                            "status": "active",
                            "scope": "L1",
                            "capabilities": ["heartbeat"],
                            "metadata": {
                                "module_path": "src.plugins.builtin.example_capability_plugin"
                            },
                            "module_path": None,
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        reg = PluginRegistry(registry_path=str(plugins_dir))
        info = reg.get_plugin("builtin.example_capability")
        assert info is not None
        # 旧版只把入口写在 metadata，重载后必须回填到正式字段。
        assert info.module_path == "src.plugins.builtin.example_capability_plugin"
        assert info.activatable is True
        # 新进程里没有真实实例，落盘的 active 必须降级，不能假显示“已启用”。
        assert info.status == PluginStatus.REGISTERED

    def test_get_loaded_plugin_returns_none_before_activation(self, reg):
        reg.register_plugin("p", "1.0.0", "memory", ["c"], "L3")
        assert reg.get_loaded_plugin(pid()) is None


# =====================================================================
# Listing
# =====================================================================

class TestListing:
    def test_list_active_empty_initially(self, reg):
        reg.register_plugin("p", "1.0.0", "memory", ["c"], "L3")
        assert reg.list_active_plugins() == []

    def test_list_by_kernel(self, reg):
        reg.register_plugin("p", "1.0.0", "memory", ["c"], "L3")
        res = reg.list_plugins_by_kernel("memory")
        assert len(res) == 1


# =====================================================================
# Capability compatibility
# =====================================================================

class TestCompatibility:
    def test_compatibility_missing_reported(self, reg):
        reg.register_plugin("p", "1.0.0", "memory", ["c1", "c2"], "L3")
        ok, missing = reg.compatibility_check(pid(), ["c1", "c3"])
        assert ok is False
        assert missing == ["c3"]

    def test_compatibility_all_present(self, reg):
        reg.register_plugin("p", "1.0.0", "memory", ["c1", "c2"], "L3")
        ok, missing = reg.compatibility_check(pid(), ["c1", "c2"])
        assert ok is True
        assert missing == []

    def test_compatibility_unknown_plugin(self, reg):
        ok, missing = reg.compatibility_check("memory:ghost:1.0.0", ["c1"])
        assert ok is False
        assert missing == ["Plugin not found"]


# =====================================================================
# Retirement of placeholder rows (no module entry point)
# =====================================================================

class TestRegistryRetirement:
    def _write_index(self, plugins_dir, rows):
        import json

        plugins_dir.mkdir(parents=True, exist_ok=True)
        (plugins_dir / "registry_index.json").write_text(
            json.dumps({"total_plugins": len(rows), "plugins": rows}, ensure_ascii=False),
            encoding="utf-8",
        )

    @staticmethod
    def _rows():
        real = {
            "plugin_id": "builtin.example_capability",
            "name": "example_capability",
            "version": "1.0.0",
            "kernel_type": "builtin",
            "capabilities": ["heartbeat"],
            "scope": "L1",
            "status": "registered",
            "module_path": "src.plugins.builtin.example_capability_plugin",
            "metadata": {},
        }
        placeholder = {
            "plugin_id": "memory:ghostplugin:1.0.0",
            "name": "ghostplugin",
            "version": "1.0.0",
            "kernel_type": "memory",
            "capabilities": [],
            "scope": "L1",
            "status": "failed",
            "module_path": None,
            "metadata": {},
        }
        return real, placeholder

    def test_placeholder_rows_are_pruned_and_archived(self, tmp_path):
        import json

        plugins_dir = tmp_path / "plugins"
        real, placeholder = self._rows()
        self._write_index(plugins_dir, [real, placeholder])

        reg = PluginRegistry(registry_path=str(plugins_dir))

        assert reg.get_plugin("memory:ghostplugin:1.0.0") is None
        on_disk = json.loads((plugins_dir / "registry_index.json").read_text(encoding="utf-8"))
        assert [p["plugin_id"] for p in on_disk["plugins"]] == ["builtin.example_capability"]
        # 真插件行零影响：入口与能力字段原样保留。
        assert on_disk["plugins"][0]["module_path"] == real["module_path"]
        assert on_disk["plugins"][0]["capabilities"] == ["heartbeat"]
        archived = json.loads(
            (plugins_dir / "registry_index.retired.json").read_text(encoding="utf-8")
        )
        assert [e["plugin_id"] for e in archived["entries"]] == ["memory:ghostplugin:1.0.0"]

    def test_prune_is_idempotent(self, tmp_path):
        import json

        plugins_dir = tmp_path / "plugins"
        real, placeholder = self._rows()
        self._write_index(plugins_dir, [real, placeholder])

        PluginRegistry(registry_path=str(plugins_dir))
        index_path = plugins_dir / "registry_index.json"
        archive_path = plugins_dir / "registry_index.retired.json"
        first_index = index_path.read_bytes()
        first_archive = archive_path.read_bytes()

        # 第二次启动已无候选，两个文件都不再变化。
        PluginRegistry(registry_path=str(plugins_dir))
        assert index_path.read_bytes() == first_index
        assert archive_path.read_bytes() == first_archive
        assert len(json.loads(first_archive)["entries"]) == 1

    def test_retired_rollback_restores_placeholder(self, tmp_path, monkeypatch):
        import json

        plugins_dir = tmp_path / "plugins"
        real, placeholder = self._rows()
        self._write_index(plugins_dir, [real, placeholder])

        PluginRegistry(registry_path=str(plugins_dir))
        archived = json.loads(
            (plugins_dir / "registry_index.retired.json").read_text(encoding="utf-8")
        )
        index_path = plugins_dir / "registry_index.json"
        data = json.loads(index_path.read_text(encoding="utf-8"))
        data["plugins"].extend(archived["entries"])
        index_path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        before = index_path.read_bytes()

        # 回滚开关：保留占位行、且不再改写注册表。
        monkeypatch.setenv("LIUHAO_PLUGIN_PRUNE_PLACEHOLDERS", "0")
        restored = PluginRegistry(registry_path=str(plugins_dir))
        info = restored.get_plugin("memory:ghostplugin:1.0.0")
        assert info is not None
        # 回滚后行会重新载入，但仍不可激活（没有模块入口）。
        assert info.activatable is False
        assert index_path.read_bytes() == before
