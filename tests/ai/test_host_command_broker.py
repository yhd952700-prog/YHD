"""U1 — host-command broker tests (DENY default + enablement gate + pipeline)."""

import pytest

from src.ai.host_command.broker import HostCommandBroker
from src.ai.host_command.capability import CapabilityCatalog, HostCommandCapability
from src.ai.host_command.enablement import ENV_VAR, is_enabled, reload
from src.ai.host_command.approval import ApprovalInterface
from src.ai.host_command.harness import FakeExecutor, build_test_broker
from src.ai.host_command.models import (
    DecisionOutcome,
    HostCommandDecision,
    HostCommandRequest,
    SandboxSpec,
)

_NOOP_SINK = lambda e, d: None


@pytest.fixture(autouse=True)
def _gate(monkeypatch):
    monkeypatch.delenv(ENV_VAR, raising=False)
    reload()
    yield
    reload()


def _git_cap():
    return HostCommandCapability("git", command_glob="git *")


def test_gate_disabled_denies_even_with_capability():
    # Gate OFF is authoritative: a registered capability does not bypass it.
    broker = build_test_broker(
        enabled=False, capabilities=(_git_cap(),), event_sink=_NOOP_SINK
    )
    dec = broker.submit(HostCommandRequest(command="git status"))
    assert dec.outcome is DecisionOutcome.DENY
    assert ENV_VAR in dec.reason


def test_gate_enabled_empty_catalog_still_denies():
    # Enabling the gate grants nothing; the empty catalog keeps default-DENY.
    import os

    os.environ[ENV_VAR] = "true"
    reload()
    broker = build_test_broker(enabled=True, capabilities=(), event_sink=_NOOP_SINK)
    dec = broker.submit(HostCommandRequest(command="git status"))
    assert dec.outcome is DecisionOutcome.DENY
    assert "no matching capability" in dec.reason


def test_allow_with_capability_executes():
    import os

    os.environ[ENV_VAR] = "true"
    reload()
    exec_fn = FakeExecutor()
    broker = build_test_broker(
        enabled=True, capabilities=(_git_cap(),), executor=exec_fn, event_sink=_NOOP_SINK
    )
    dec = broker.submit(HostCommandRequest(command="git pull"))
    assert dec.outcome is DecisionOutcome.ALLOW
    assert len(exec_fn.calls) == 1


def test_deny_does_not_execute():
    import os

    os.environ[ENV_VAR] = "true"
    reload()
    exec_fn = FakeExecutor()
    broker = build_test_broker(
        enabled=True, capabilities=(_git_cap(),), executor=exec_fn, event_sink=_NOOP_SINK
    )
    dec = broker.submit(HostCommandRequest(command="rm -rf /"))  # no matching cap
    assert dec.outcome is DecisionOutcome.DENY
    assert len(exec_fn.calls) == 0


def test_requires_approval_defers_then_allows():
    import os

    os.environ[ENV_VAR] = "true"
    reload()
    cap = HostCommandCapability(
        "destructive", command_glob="rm *", requires_approval=True
    )
    approvals = ApprovalInterface()
    exec_fn = FakeExecutor()
    broker = build_test_broker(
        enabled=True, capabilities=(cap,),
        approvals=approvals, executor=exec_fn, event_sink=_NOOP_SINK,
    )
    req = HostCommandRequest(command="rm -rf /data", use_shell=True)
    dec = broker.submit(req)
    assert dec.outcome is DecisionOutcome.DEFER
    # Grant and re-submit.
    ar = approvals.pending()
    approval_id = next(iter(ar))
    approvals.grant(approval_id)
    dec2 = broker.submit(req)
    assert dec2.outcome is DecisionOutcome.ALLOW
    assert len(exec_fn.calls) == 1


def test_simulation_mode_does_not_execute():
    import os

    os.environ[ENV_VAR] = "true"
    reload()
    exec_fn = FakeExecutor()
    broker = build_test_broker(
        enabled=True, capabilities=(_git_cap(),), executor=exec_fn,
        simulate=True, event_sink=_NOOP_SINK,
    )
    dec = broker.submit(HostCommandRequest(command="git push"))
    assert dec.outcome is DecisionOutcome.ALLOW
    # Simulation: decision computed but executor never invoked.
    assert len(exec_fn.calls) == 0


def test_no_executor_errors():
    import os

    os.environ[ENV_VAR] = "true"
    reload()
    broker = build_test_broker(
        enabled=True, capabilities=(_git_cap(),), executor=None, event_sink=_NOOP_SINK
    )
    dec = broker.submit(HostCommandRequest(command="git status"))
    assert dec.outcome is DecisionOutcome.ERROR
    assert "no executor" in dec.reason


def test_sandbox_propagated_to_decision():
    import os

    os.environ[ENV_VAR] = "true"
    reload()
    sb = SandboxSpec(cwd="/workspace", network=False)
    cap = HostCommandCapability("git", command_glob="git *", sandbox=sb)
    broker = build_test_broker(
        enabled=True, capabilities=(cap,), executor=FakeExecutor(), event_sink=_NOOP_SINK
    )
    dec = broker.submit(HostCommandRequest(command="git status"))
    assert dec.outcome is DecisionOutcome.ALLOW
    assert dec.sandbox is sb


def test_direct_broker_respects_gate_without_env_helper():
    # Construct a broker directly while the gate is OFF -> DenyAllPolicy forced.
    assert is_enabled() is False
    broker = HostCommandBroker(
        catalog=CapabilityCatalog(),
        executor=FakeExecutor(),
        event_sink=_NOOP_SINK,
    )
    # Even if a policy were passed, the OFF gate forces DenyAll; sanity: it's denied.
    dec = broker.submit(HostCommandRequest(command="git status"))
    assert dec.outcome is DecisionOutcome.DENY


def test_decision_is_host_command_decision():
    import os

    os.environ[ENV_VAR] = "true"
    reload()
    broker = build_test_broker(enabled=True, capabilities=(), event_sink=_NOOP_SINK)
    dec = broker.submit(HostCommandRequest(command="git status"))
    assert isinstance(dec, HostCommandDecision)
    assert isinstance(dec.as_dict(), dict)
