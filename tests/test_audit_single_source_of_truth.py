"""Single-source-of-truth guardrail for the audit subsystem.

Background
----------
Project LiuHao AI OS had TWO audit implementations:

* ``src/audit``  — legacy compatibility layer. Writes plain JSON
  (``data/audit/events.json``). Events here are NOT part of the
  tamper-evident chain.
* ``src/kernels/audit`` — the AUTHORITATIVE audit implementation
  (SQLite + hash chain + ``chain_state`` anchor). This is the single
  source of truth for audit evidence (L10K gate, dashboard
  ``audit.total_events``, reliability gate).

This test enforces that convergence: the legacy layer stays importable but
is explicitly deprecated, and nothing in ``src/`` (outside ``src/audit``
itself) may import the legacy layer — preventing it from silently becoming
a runtime dependency again.

Hard constraints honoured:
* Never touches the real ``audit_store.db`` (all kernel use goes through
  a per-test temp db via ``tmp_path``).
* The guard does NOT degrade into a rubber stamp — see the anti-false
  test.
"""
import ast
import os
import sys
import warnings

from src.kernels.audit import AuditStore, AuditEventType, AuditScope
import src.kernels.audit as _kernel_audit


# =====================================================================
# Shared scanner: detect any absolute import of the legacy ``src.audit``
# package from OUTSIDE ``src/audit`` itself.
# =====================================================================

def _scan_sources(sources):
    """Scan (path, text) pairs for imports of the legacy ``src.audit`` package.

    Returns a list of human-readable violation strings (empty == clean).
    Detects:
      * ``import src.audit`` / ``import src.audit.foo``
      * ``from src.audit import ...`` / ``from src.audit.foo import ...``
      * ``from src import audit``
    """
    violations = []
    for path, text in sources:
        try:
            tree = ast.parse(text)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name == "src.audit" or alias.name.startswith("src.audit."):
                        violations.append(f"{path}: import {alias.name}")
            elif isinstance(node, ast.ImportFrom):
                mod = node.module or ""
                if mod == "src.audit" or mod.startswith("src.audit."):
                    violations.append(f"{path}: from {mod} import ...")
                # ``from src import audit`` also binds the legacy package.
                if mod == "src" and any(a.name == "audit" for a in node.names):
                    violations.append(f"{path}: from src import audit")
    return violations


def _collect_src_sources():
    """Yield (relpath, text) for every .py under ``src/`` except ``src/audit``."""
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    src_root = os.path.join(repo_root, "src")
    sources = []
    for dirpath, _dirs, files in os.walk(src_root):
        for fname in files:
            if not fname.endswith(".py"):
                continue
            full = os.path.join(dirpath, fname)
            rel = os.path.relpath(full, src_root)
            # Skip the legacy package itself — it is allowed to import itself.
            if rel.replace(os.sep, "/").startswith("audit/"):
                continue
            with open(full, "r", encoding="utf-8") as fh:
                sources.append((rel, fh.read()))
    return sources


# =====================================================================
# (i) Authoritative audit kernel is importable and integrity works on a
#      TEMP db (never the real audit_store.db).
# =====================================================================

def test_kernel_audit_importable_and_integrity_on_temp_db(tmp_path):
    # The module exposes verify_audit_integrity (bound to the real store, so
    # we only assert it exists; we exercise integrity on a temp db below).
    assert hasattr(_kernel_audit, "verify_audit_integrity")
    assert callable(_kernel_audit.verify_audit_integrity)

    store = AuditStore(db_path=str(tmp_path / "guard_audit.db"))
    store.log_event(AuditEventType.ACCESS_CHECK, "p1", AuditScope.L1, "allow")
    store.log_event(AuditEventType.ACCESS_DENIED, "p1", AuditScope.L1, "deny")

    ok, total = store.verify_integrity()
    assert ok is True, "fresh temp-db chain should verify clean"
    assert total == 2, f"expected 2 events, got {total}"


# =====================================================================
# (ii) Importing the legacy ``src.audit`` emits a DeprecationWarning that
#      names the authoritative ``src.kernels.audit``.
# =====================================================================

def test_import_src_audit_emits_deprecation_warning():
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        # Force a fresh re-execution of the module body so the
        # module-level warning actually fires.
        sys.modules.pop("src.audit", None)
        sys.modules.pop("src.audit.models", None)
        sys.modules.pop("src.audit.store", None)
        import src.audit  # noqa: F401

    deprecations = [w for w in caught
                    if issubclass(w.category, DeprecationWarning)]
    assert deprecations, f"no DeprecationWarning emitted on import src.audit: {caught}"
    assert any("src.kernels.audit" in str(w.message) for w in deprecations), \
        f"warning does not point at src.kernels.audit: " \
        f"{[str(w.message) for w in deprecations]}"


# =====================================================================
# (iii) Single-source guard: nothing in src/ (outside src/audit itself)
#       imports the legacy package.
# =====================================================================

def test_no_src_module_imports_legacy_audit():
    violations = _scan_sources(_collect_src_sources())
    assert violations == [], \
        "legacy src.audit is imported by the following src/ modules " \
        "(it must only be a deprecated compat shim):\n" + "\n".join(violations)


# =====================================================================
# (iv) Anti-false-positive: the same checker MUST catch a leaking import.
#      If this passes while (iii) also passes, the guard is real, not a
#      rubber stamp.
# =====================================================================

def test_guard_actually_detects_a_leaking_import():
    leak_sources = [
        ("src/leaky_module.py", "from src.audit import AuditStore\n"),
        ("src/sub/another_leak.py", "import src.audit.store\n"),
        ("src/third.py", "from src import audit\n"),
    ]
    violations = _scan_sources(leak_sources)
    assert violations, \
        "GUARD IS A RUBBER STAMP: it failed to detect explicit imports of " \
        "src.audit. The single-source guard (iii) cannot be trusted."
    assert len(violations) == 3, \
        f"expected 3 detected leaks, got {len(violations)}: {violations}"


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v", "--tb=short", "-p", "no:cacheprovider"])
