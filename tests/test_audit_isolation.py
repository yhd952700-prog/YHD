"""Regression guards for Companion-Wiring A1a / A1b (audit-channel isolation).

Root cause (pre-fix)
--------------------
The CRIT-1C Layer-2 mandatory-evidence gate (``src/kernels/_crosscutting.py:
850-878``) performs a *real* pre-execution audit write for every HIGH/CRITICAL
kernel action. That write is funnelled through ``AuditStore``'s SQLite
single-writer lease (``src/kernels/audit/fencing.py``). The default
``AUDIT_DB_PATH`` pointed at the **shared** ``<project_root>/audit_store.db``.

When a prior test run left a writer-lease row that was still within its TTL and
whose owning OS process was still "alive", a fresh run's ``lease.acquire`` raised
``StaleWriterError``. Because the gate calls ``_call_audit(..., reraise=True)``,
that exception surfaced and the gate converted *any* audit-backend failure into a
``PolicyDeniedError: ... rule=default_deny`` (``_crosscutting.py:878``). The
result was a whole class of HIGH/CRITICAL actions denied -- ``capability.register``,
``capability.retire``, ``trust.*``, ``security.set_abac_rule`` -- plus, downstream,
``Capability not found: kernel.network_bus/python_compute`` (builtin registration
goes through the HIGH ``capability.register`` gate and was being denied, leaving
the registry empty).

The fix (``tests/conftest.py::_isolate_audit_store``) redirects ``AUDIT_DB_PATH``
to an exclusive per-session temp file and drops any ``_audit_store`` singleton
built against the old path. These tests lock that invariant in so the suite can
never silently regress to the shared, lease-contended database.

These are test-infrastructure guards only: they do NOT touch the mandatory-
evidence gate, do NOT weaken fail-closed, and do NOT flip any production default.
"""

import os

from src.kernels.audit import get_audit_store
from src.kernels.capability import get_capability_registry


def test_audit_store_uses_isolated_temp_path():
    """conftest must redirect AUDIT_DB_PATH away from the shared project-root DB."""
    path = get_audit_store()._db_path
    assert path, "audit store resolved to an empty db path"

    # The env var conftest sets must be honoured (not silently fallen back to the
    # project-root default that carries a stale cross-run writer lease).
    env = os.environ.get("AUDIT_DB_PATH")
    assert env, "AUDIT_DB_PATH was not redirected by conftest"
    assert path == env, f"audit store path {path!r} != AUDIT_DB_PATH {env!r}"

    # It must live under the per-session pytest temp root, never the repo root.
    assert (
        "lh_audit" in path or "tmp" in path.lower() or "pytest" in path.lower()
    ), f"audit store not isolated from shared default: {path}"


def test_builtin_capabilities_registered_through_high_gate():
    """Builtins (incl. network_bus / python_compute) reach the registry.

    They are registered via the HIGH ``capability.register`` kernel action, which
    traverses the mandatory-evidence gate. Pre-fix the gate's pre-write failed on
    the shared DB, so registration was denied and the registry stayed empty --
    surfacing as ``Capability not found: kernel.network_bus/python_compute``.
    """
    reg = get_capability_registry()
    assert reg.lookup("network_bus", "kernel") is not None
    assert reg.lookup("python_compute", "kernel") is not None
    assert reg.lookup("capability_registry", "kernel") is not None
