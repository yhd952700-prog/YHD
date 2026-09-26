"""U6 regression: ``revoke_all_user_tokens`` must actually revoke, not no-op.

This guards against the silent-no-op placeholder that returned ``0`` while
revoking nothing -- a security control that lied (violates the "no fake
success / no garbage code" boundary).
"""
from __future__ import annotations

import pytest

jwt = pytest.importorskip("jwt")

from src.security.jwt_handler import JWTHandler, TokenType


def _handler() -> JWTHandler:
    # HS256 + explicit secret avoids the RSA/cryptography dependency and is
    # deterministic for the test.
    return JWTHandler(
        algorithm="HS256",
        secret_key="test-secret-0123456789abcdef-32bytekey!!",
    )


def test_revoke_all_user_tokens_revokes_and_is_counted():
    h = _handler()
    t_alice1, _ = h.create_token("alice")
    t_alice2, _ = h.create_token("alice")
    t_bob, _ = h.create_token("bob")

    n = h.revoke_all_user_tokens("alice")
    assert n == 2

    # Alice's tokens are now rejected.
    with pytest.raises(jwt.InvalidTokenError):
        h.validate_token(t_alice1)
    with pytest.raises(jwt.InvalidTokenError):
        h.validate_token(t_alice2)

    # Bob's token is untouched.
    assert h.validate_token(t_bob).sub == "bob"


def test_revoke_all_user_tokens_is_honest_when_nothing_to_revoke():
    h = _handler()
    # No tokens minted for "ghost" -> returns 0 honestly, not a fake success.
    assert h.revoke_all_user_tokens("ghost") == 0


def test_revoke_all_user_tokens_repeat_returns_zero():
    h = _handler()
    h.create_token("carol")
    h.create_token("carol")
    assert h.revoke_all_user_tokens("carol") == 2
    # After revocation the subject has no tracked tokens left.
    assert h.revoke_all_user_tokens("carol") == 0


def test_revoke_all_user_tokens_covers_refresh_tokens():
    h = _handler()
    pair = h.create_token_pair("dave")
    refresh = pair["refresh_token"]
    assert h.revoke_all_user_tokens("dave") >= 1
    with pytest.raises(jwt.InvalidTokenError):
        h.validate_refresh_token(refresh)
