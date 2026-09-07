"""K02 JWT Handler kernel integration tests (Task 3).

Real API surface verified by reading src/security/jwt_handler.py.
Goal: 10+ passing tests for CP-A gate K02.

Author: 执行型 Hermes (Phase 2 Task 3)
"""

import time
import pytest
import jwt as pyjwt

from src.security.jwt_handler import (
    JWTHandler,
    TokenType,
    TokenPayload,
    get_jwt_handler,
    create_access_token,
    create_refresh_token,
    validate_token,
)


# ============================================================
# Section 1: Initialization
# ============================================================

def test_jwt_handler_construct():
    """Default construction should succeed and generate keys."""
    handler = JWTHandler()
    assert handler is not None
    assert handler.access_ttl > 0
    assert handler.refresh_ttl > 0


def test_jwt_handler_construct_with_hs256():
    """HS256 can be used with secret_key."""
    handler = JWTHandler(algorithm="HS256", secret_key="test-secret-32-bytes-long-string123")
    assert handler.algorithm == "HS256"
    token, payload = handler.create_token(subject="user-1")
    assert isinstance(token, str)
    assert isinstance(payload, TokenPayload)


def test_get_jwt_handler_singleton():
    """get_jwt_handler returns the same instance."""
    h1 = get_jwt_handler()
    h2 = get_jwt_handler()
    assert h1 is h2


# ============================================================
# Section 2: Token creation and validation
# ============================================================

def test_create_access_token_has_correct_type():
    """create_token with ACCESS produces ACCESS-typed payload."""
    handler = JWTHandler()
    token, payload = handler.create_token(subject="alice", token_type=TokenType.ACCESS)
    assert payload.token_type == TokenType.ACCESS
    assert payload.sub == "alice"
    assert len(token.split(".")) == 3  # JWT structure


def test_validate_access_token_roundtrip():
    """Create then validate returns same subject."""
    handler = JWTHandler()
    token, expected = handler.create_token(subject="bob")
    decoded = handler.validate_access_token(token)
    assert decoded.sub == "bob"
    assert decoded.jti == expected.jti


def test_create_token_pair_returns_access_and_refresh():
    """create_token_pair produces both access and refresh tokens."""
    handler = JWTHandler()
    pair = handler.create_token_pair(subject="carol")
    assert "access_token" in pair
    assert "refresh_token" in pair
    assert pair["token_type"] == "Bearer"
    assert pair["expires_in"] == handler.access_ttl

    # Both tokens should be valid
    a = handler.validate_access_token(pair["access_token"])
    r = handler.validate_refresh_token(pair["refresh_token"])
    assert a.sub == "carol"
    assert r.sub == "carol"


def test_token_payload_carries_custom_claims():
    """scopes/roles/permissions are encoded into payload."""
    handler = JWTHandler()
    _, payload = handler.create_token(
        subject="dave",
        scopes=["read:memory", "write:memory"],
        roles=["admin"],
        permissions=["memory:read"],
    )
    assert "read:memory" in payload.scopes
    assert "admin" in payload.roles
    assert "memory:read" in payload.permissions


# ============================================================
# Section 3: Revocation / blocklist
# ============================================================

def test_revoke_token_blocks_validation():
    """After revoke_token, validate_token raises InvalidTokenError."""
    handler = JWTHandler()
    token, _ = handler.create_token(subject="eve")
    assert handler.revoke_token(token) is True
    with pytest.raises(pyjwt.InvalidTokenError):
        handler.validate_access_token(token)


def test_revoke_token_by_jti_blocks():
    """revoke_token_by_jti adds JTI to blocklist."""
    handler = JWTHandler()
    token, payload = handler.create_token(subject="frank")
    handler.revoke_token_by_jti(payload.jti)
    with pytest.raises(pyjwt.InvalidTokenError):
        handler.validate_access_token(token)


def test_revoke_refresh_token_blocks_refresh():
    """revoke_refresh_token invalidates the refresh token."""
    handler = JWTHandler()
    pair = handler.create_token_pair(subject="grace")
    refresh_payload = handler.validate_refresh_token(pair["refresh_token"])
    handler.revoke_refresh_token(refresh_payload.jti)
    # refresh token should fail now
    with pytest.raises(pyjwt.InvalidTokenError):
        handler.validate_refresh_token(pair["refresh_token"])


def test_refresh_access_token_rotates_refresh():
    """refresh_access_token issues a new refresh token and revokes the old."""
    handler = JWTHandler()
    pair = handler.create_token_pair(subject="henry")
    old_refresh_payload = handler.validate_refresh_token(pair["refresh_token"])

    new_pair = handler.refresh_access_token(pair["refresh_token"])
    assert new_pair["access_token"] != pair["access_token"]
    assert new_pair["refresh_token"] != pair["refresh_token"]

    # Old refresh now revoked
    with pytest.raises(pyjwt.InvalidTokenError):
        handler.validate_refresh_token(pair["refresh_token"])


# ============================================================
# Section 4: Token type enforcement
# ============================================================

def test_validate_access_rejects_refresh_token():
    """validate_access_token rejects a refresh token."""
    handler = JWTHandler()
    refresh_token, _ = handler.create_token(subject="ivy", token_type=TokenType.REFRESH)
    with pytest.raises(pyjwt.InvalidTokenError):
        handler.validate_access_token(refresh_token)


def test_validate_refresh_rejects_access_token():
    """validate_refresh_token rejects an access token."""
    handler = JWTHandler()
    access_token, _ = handler.create_token(subject="jack", token_type=TokenType.ACCESS)
    with pytest.raises(pyjwt.InvalidTokenError):
        handler.validate_refresh_token(access_token)


# ============================================================
# Section 5: Introspection and metadata
# ============================================================

def test_introspect_token_active():
    """introspect_token returns active=true for valid token."""
    handler = JWTHandler()
    token, _ = handler.create_token(subject="kim")
    info = handler.introspect_token(token)
    assert info.get("active") is True
    assert info.get("sub") == "kim"
    assert info.get("iss") == handler.issuer


def test_introspect_token_inactive_for_garbage():
    """introspect_token returns active=false for garbage input."""
    handler = JWTHandler()
    info = handler.introspect_token("not-a-jwt")
    assert info.get("active") is False


def test_token_payload_is_expired_logic():
    """TokenPayload.is_expired + is_valid_now behave correctly (deterministic)."""
    handler = JWTHandler()
    # Long-lived token: not expired and valid right after creation.
    token, payload = handler.create_token(subject="larry", ttl=3600)
    assert payload.is_expired() is False
    assert payload.is_valid_now() is True
    # Move exp into the past deterministically (no sleep -> no timing race).
    payload.exp = int(time.time()) - 1
    assert payload.is_expired() is True
    assert payload.is_valid_now() is False


# ============================================================
# Section 6: Key management
# ============================================================

def test_get_jwks_returns_keyset_dict():
    """get_jwks returns a dict in JWKS format (Y1 quirk: PyJWK.from_pem not in older pyjwt)."""
    handler = JWTHandler(algorithm="RS256")
    # Some pyjwt versions don't expose PyJWK.from_pem; ensure handler.get_jwks returns dict
    try:
        jwks = handler.get_jwks()
    except AttributeError:
        pytest.skip("PyJWT version too old for PyJWK.from_pem")
    assert isinstance(jwks, dict)
    assert "keys" in jwks
    assert len(jwks["keys"]) >= 1


def test_save_keys_to_directory(tmp_path):
    """save_keys writes private and public key files."""
    handler = JWTHandler()
    out_dir = tmp_path / "keys"
    handler.save_keys(str(out_dir))
    assert (out_dir / "private.pem").exists()
    assert (out_dir / "public.pem").exists()


# ============================================================
# Section 7: Convenience module-level functions
# ============================================================

def test_module_level_create_access_token():
    """create_access_token module function works."""
    token, payload = create_access_token(subject="mia")
    assert payload.token_type == TokenType.ACCESS
    assert payload.sub == "mia"


def test_module_level_create_refresh_token():
    """create_refresh_token module function works."""
    token, payload = create_refresh_token(subject="nick")
    assert payload.token_type == TokenType.REFRESH
    assert payload.sub == "nick"


def test_module_level_validate_token():
    """validate_token module function works."""
    token, payload = create_access_token(subject="olive")
    decoded = validate_token(token)
    assert decoded.sub == "olive"


# ============================================================
# Section 8: TokenType enum completeness
# ============================================================

def test_token_type_enum_members():
    """All expected token types exist."""
    types = {t.value for t in TokenType}
    assert "access" in types
    assert "refresh" in types
    assert "api_key" in types
    assert "invitation" in types
    assert "reset_password" in types


# ============================================================
# Section 9: Edge cases
# ============================================================

def test_token_uniqueness_via_jti():
    """Different tokens have different JTIs."""
    handler = JWTHandler()
    _, p1 = handler.create_token(subject="paul")
    _, p2 = handler.create_token(subject="paul")
    assert p1.jti != p2.jti


def test_token_iat_clock_consistent():
    """iat and nbf are similar within seconds (close to now)."""
    handler = JWTHandler()
    before = int(time.time())
    _, payload = handler.create_token(subject="quinn")
    after = int(time.time())
    assert before - 1 <= payload.iat <= after + 1
    assert before - 1 <= payload.nbf <= after + 1
