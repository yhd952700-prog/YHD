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
        assert res.error is not None

    def test_deactivate_not_active_returns_false(self, reg):
        reg.register_plugin("p", "1.0.0", "memory", ["c"], "L3")
        assert reg.deactivate_plugin(pid()) is False


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
