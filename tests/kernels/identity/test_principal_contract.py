"""Principal-must-be-a-non-empty-string contract on IdentityManager.create_identity.

Regression guard for the "principal 可空" contract gap: a non-string or
empty/whitespace principal used to be silently coerced through
``normalize_principal`` (which does ``str(principal)``), so ``create_identity(None)``
minted a phantom ``human:None`` that ``is_human_identity()`` would then accept.
That let the machine-mints-human primitive be reached with no principal at all.

The boundary now refuses anything that is not a non-empty string, while still
normalising real strings (``"  carol  "`` -> ``human:carol``).
"""

import os

import pytest

from src.kernels.identity import (
    IdentityManager,
    get_identity_manager,
    is_human_identity,
)


@pytest.fixture
def isolated_manager(tmp_path, monkeypatch):
    """A fresh IdentityManager bound to an isolated store + integrity key."""
    for name in (
        "AUDIT_DB_PATH",
        "LIUHAO_HUMAN_IDENTITIES_FILE",
        "LIUHAO_HUMAN_IDENTITIES_INTEGRITY_KEY",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("AUDIT_DB_PATH", str(tmp_path / "audit.sqlite3"))
    monkeypatch.setenv("LIUHAO_HUMAN_IDENTITIES_FILE", str(tmp_path / "humans.json"))
    monkeypatch.setenv("LIUHAO_HUMAN_IDENTITIES_INTEGRITY_KEY", "k" * 64)
    # The autouse conftest fixture already nulls _global_manager; reconstruct.
    return get_identity_manager()


@pytest.mark.parametrize(
    "bad_principal",
    [None, "", "   ", 0, False],
    ids=["none", "empty", "whitespace", "int-zero", "bool-false"],
)
def test_create_identity_refuses_non_string_principal(
    isolated_manager: IdentityManager, bad_principal
):
    ident = isolated_manager.create_identity(
        principal=bad_principal, metadata={"kind": "human"}
    )
    assert ident is None, (
        f"create_identity({bad_principal!r}) should be refused, "
        f"but minted {getattr(ident, 'id', None)!r}"
    )
    # Nothing nameable leaked into the registry.
    assert (
        isolated_manager.get_identity_by_principal(str(bad_principal)) is None
    )


def test_create_human_identity_refuses_none(isolated_manager: IdentityManager):
    assert isolated_manager.create_human_identity(None) is None


def test_create_identity_mints_valid_string(isolated_manager: IdentityManager):
    ident = isolated_manager.create_identity(
        principal="alice", metadata={"kind": "human"}
    )
    assert ident is not None
    assert ident.id == "human:alice"
    assert is_human_identity(ident) is True


def test_whitespace_principal_is_normalised(isolated_manager: IdentityManager):
    ident = isolated_manager.create_identity(
        principal="  carol  ", metadata={"kind": "human"}
    )
    assert ident is not None
    assert ident.id == "human:carol"


def test_no_phantom_human_after_null_principal(isolated_manager: IdentityManager):
    """The core of the original gap: a null principal must not yield a human."""
    isolated_manager.create_identity(principal=None, metadata={"kind": "human"})
    assert isolated_manager.get_identity_by_principal("None") is None
    humans = [
        i for i in isolated_manager._identities.values() if is_human_identity(i)
    ]
    assert humans == []
