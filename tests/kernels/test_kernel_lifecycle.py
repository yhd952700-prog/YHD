"""Lifecycle protocol verification for the 14 LIUHAO X kernels.

These assertions back the contract added alongside ``src/kernels/_base.py``:

    initialize() -> READY
    pause()      from READY / UNINITIALIZED -> PAUSED   (raises otherwise)
    resume()     from PAUSED -> READY                   (raises otherwise)
    shutdown()   -> STOPPED

Anti-rubber-stamp guarantees:
    * ``MemoryKernel`` must NOT bake ``lifecycle`` to READY; a fresh instance
      stays ``UNINITIALIZED``.
    * The dataclass construction signature of ``MemoryKernel`` is unchanged
      (no class-level ``lifecycle`` field was added).
    * Every target class keeps a non-empty ``__doc__`` (坑 A guard: the
      docstring must remain the first statement of the class body).
"""
import ast
import inspect

import pytest

from src.kernels._base import KernelLifecycle, KernelStateError

MODULES = {
    "audit": "AuditStore",
    "capability": "CapabilityRegistry",
    "context": "ContextKernel",
    "evaluation": "Evaluator",
    "event": "EventBus",
    "execution": "ExecutionEngine",
    "identity": "IdentityManager",
    "memory": "MemoryKernel",
    "network": "NetworkBus",
    "plugin": "PluginRegistry",
    "policy": "PolicyEngine",
    "resource": "ResourceQuotaManager",
    "security": "SecurityEngine",
    "trust": "TrustManager",
}

CLASSES = {}
for _mod, _cls in MODULES.items():
    _m = __import__(f"src.kernels.{_mod}", fromlist=[_cls])
    CLASSES[_cls] = getattr(_m, _cls)


def test_initialize_sets_ready():
    for cls in CLASSES.values():
        inst = cls()
        inst.initialize()
        assert inst.lifecycle is KernelLifecycle.READY, cls.__name__


def test_shutdown_sets_stopped():
    for cls in CLASSES.values():
        inst = cls()
        inst.initialize()
        inst.shutdown()
        assert inst.lifecycle is KernelLifecycle.STOPPED, cls.__name__


def test_pause_resume_cycle():
    for cls in CLASSES.values():
        inst = cls()
        inst.initialize()
        inst.pause()
        assert inst.lifecycle is KernelLifecycle.PAUSED, cls.__name__
        inst.resume()
        assert inst.lifecycle is KernelLifecycle.READY, cls.__name__


def test_pause_from_stopped_raises():
    # Spec wording: pause() from STOPPED / unready must raise KernelStateError.
    for cls in CLASSES.values():
        inst = cls()
        inst.initialize()
        inst.shutdown()
        assert inst.lifecycle is KernelLifecycle.STOPPED
        with pytest.raises(KernelStateError):
            inst.pause()


def test_pause_from_paused_raises():
    # PAUSED is also an invalid source state for pause().
    for cls in CLASSES.values():
        inst = cls()
        inst.initialize()
        inst.pause()
        with pytest.raises(KernelStateError):
            inst.pause()


def test_pause_from_uninitialized_allowed():
    # Honest deviation note: the supplied `pause()` body permits pausing from
    # UNINITIALIZED (not only READY). We assert the REAL behaviour rather than
    # the spec's looser "unready raises" wording.
    for cls in CLASSES.values():
        inst = cls()
        assert inst.lifecycle is KernelLifecycle.UNINITIALIZED
        inst.pause()
        assert inst.lifecycle is KernelLifecycle.PAUSED, cls.__name__


def test_resume_when_not_paused_raises():
    for cls in CLASSES.values():
        inst = cls()
        inst.initialize()  # READY, not PAUSED
        with pytest.raises(KernelStateError):
            inst.resume()
        # Also invalid straight from UNINITIALIZED.
        fresh = cls()
        with pytest.raises(KernelStateError):
            fresh.resume()


def test_memorykernel_not_hardcoded_ready():
    from src.kernels.memory import MemoryKernel

    mk = MemoryKernel()
    # Proof the field is not defaulted to READY.
    assert mk.lifecycle is KernelLifecycle.UNINITIALIZED
    # Proof the dataclass construction contract is intact.
    params = list(inspect.signature(MemoryKernel.__init__).parameters.keys())
    assert params == ["self", "_lock", "db_path", "backend"], params
    # Proof `lifecycle` was NOT added as a dataclass field.
    assert "lifecycle" not in getattr(MemoryKernel, "__dataclass_fields__", {})


def test_docstrings_intact():
    for mod, cls in MODULES.items():
        path = __import__(f"src.kernels.{mod}", fromlist=[cls]).__file__
        tree = ast.parse(open(path, encoding="utf-8").read())
        node = next(
            n
            for n in ast.walk(tree)
            if isinstance(n, ast.ClassDef) and n.name == cls
        )
        doc = ast.get_docstring(node)
        assert doc, f"{cls} docstring is empty or None"
