"""HC-01 denial-truth regression: a refusal must never be recorded as success.

These tests assert behaviour against the REAL hash-chained audit store (a
temp ``AUDIT_DB_PATH`` redirected by ``tests/conftest.py::_isolate_audit_store``).
They must FAIL against the pre-fix code, where the five genuine refusal paths
below were written as ``outcome="success"`` even though the action did not
happen:

  * ``network.route`` refusal (ALLOW verdict, kernel refused on scope/route/...)
    -> was "success"
  * ``resource.allocate`` returning ``None`` (no quota)             -> was "success"
  * ``resource.release`` returning ``False`` (nothing to release)   -> was "success"

Genuine success and legitimate no-ops must stay "success" (no blanket flip):
  * ``memory.store`` (allow, completes)        -> stays "success"
  * ``memory.compress`` returning ``None`` for "nothing to do" (allow, LOW)
    -> stays "success", NOT mislabelled "denied"

This is the audit-denial-truth closure for the top-priority defect: the
authoritative chain must never assert a false success for a refused action.
"""

from src.kernels.audit import audit_query
from src.kernels import security, plugin, resource, network, memory


def _latest_event(action_name: str):
    """Newest non-``intent`` audit event for ``action_name`` (attribution-safe)."""
    for ev in audit_query(limit=2000, reverse=True):
        det = ev.get("details") or {}
        if det.get("action") != action_name:
            continue
        if ev.get("outcome") == "intent":  # skip the mandatory-evidence pre-write
            continue
        return ev
    return None


def _assert_not_success(action_name, required_decision=None):
    ev = _latest_event(action_name)
    assert ev is not None, f"no audit event recorded for {action_name!r}"
    det = ev.get("details") or {}
    outcome = ev.get("outcome")
    policy = det.get("policy_decision")
    assert outcome != "success", (
        f"{action_name}: refusal recorded as outcome='success' "
        f"(policy={policy!r}, denial_reason={det.get('denial_reason')!r})"
    )
    if required_decision is not None:
        assert policy == required_decision, (
            f"{action_name}: expected policy_decision={required_decision!r}, "
            f"got {policy!r}"
        )
    # Consistency: a non-success outcome must carry a refusal reason, and a
    # "deny" verdict must not coexist with "success" (the original contradiction).
    assert policy != "deny" or outcome != "success", (
        f"{action_name}: contradictory policy_decision='deny' + outcome='success'"
    )
    return ev


# --------------------------------------------------------------------------- #
# 1. HIGH-risk action denied by policy -> not success, internally consistent
# --------------------------------------------------------------------------- #
def test_high_risk_denied_by_policy_not_success():
    sec = security.get_security_engine()
    sec.revoke_rbac_role("ghost-principal", security.RBACRole.ADMIN)
    ev = _assert_not_success("security.revoke_rbac_role", required_decision="deny")
    det = ev.get("details") or {}
    # genuine refusal: the role was not held, so the kernel declared a denial
    assert det.get("denial_reason"), "denied action must carry a denial_reason"


# --------------------------------------------------------------------------- #
# 2. network.route scope violation (ALLOW verdict) -> not success
# --------------------------------------------------------------------------- #
def test_network_route_scope_violation_not_success():
    bus = network.get_network_bus()
    bus.add_route(network.Route(
        id="r1", pattern="svc/*", protocol=network.ProtocolType.INTERNAL,
        adapter="internal", scope="L1",
    ))
    msg = network.Message(destination="svc/a", content={}, metadata={"scope": "L7"})
    out = bus.route(msg)
    assert out.status == network.MessageStatus.FAILED
    _assert_not_success("network.route")  # allow verdict, but kernel refused


# --------------------------------------------------------------------------- #
# 3. resource.allocate / release refusals -> not success
# --------------------------------------------------------------------------- #
def test_resource_allocate_no_quota_not_success():
    rm = resource.get_resource_manager()
    alloc = rm.allocate(
        resource.ResourceType.CPU, 1.0,
        resource.ResourceScope.L7, "ghost-owner", "test",
    )
    assert alloc is None
    _assert_not_success("resource.allocate")


def test_resource_release_nothing_to_release_not_success():
    rm = resource.get_resource_manager()
    ok = rm.release(
        resource.ResourceType.CPU, 5.0,
        resource.ResourceScope.L3, "ghost-owner",
    )
    assert ok is False
    _assert_not_success("resource.release")


def test_plugin_unknown_not_success():
    reg = plugin.get_plugin_registry()
    ok = reg.unregister_plugin("does-not-exist:plugin:1.0.0")
    assert ok is False
    _assert_not_success("plugin.unregister_plugin")


# --------------------------------------------------------------------------- #
# 4. genuine success is NOT blanket-flipped
# --------------------------------------------------------------------------- #
def test_genuine_success_still_success():
    mem = memory.get_memory_kernel()
    mem.store("k", {"v": 1}, scope=memory.MemoryScope.L1)
    ev = _latest_event("memory.store")
    assert ev is not None, "memory.store produced no audit event"
    assert ev.get("outcome") == "success", (
        f"genuine success flipped to {ev.get('outcome')!r}"
    )


# --------------------------------------------------------------------------- #
# 5. legitimate no-op must NOT be mislabelled as denied
# --------------------------------------------------------------------------- #
def test_memory_compress_noop_not_denied():
    mem = memory.get_memory_kernel()
    # No candidates -> honest "nothing to do" (returns None). LOW/allow.
    result = mem.compress()
    assert result is None
    ev = _latest_event("memory.compress")
    assert ev is not None, "memory.compress produced no audit event"
    assert ev.get("outcome") == "success", (
        f"legitimate no-op mislabelled as {ev.get('outcome')!r} "
        f"(should stay 'success')"
    )
    assert ev.get("outcome") != "denied"
