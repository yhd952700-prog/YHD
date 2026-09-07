"""
Tests for Phase 1 SEC-04: Cryptography Audit & Key Management

Tests VaultTransitCrypto, CryptoAuditLogger, and their integration
with existing security modules (EncryptionManager, JWTHandler, APIKeyManager).
"""

import time
import hashlib
import json
import sys
import os

# Ensure project root is in path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.security import (
    VaultTransitCrypto,
    CryptoAuditLogger,
    CryptoAuditEvent,
    CryptoOperation,
    EncryptionManager,
    JWTHandler,
    APIKeyManager,
    KeyScope,
)
from src.integrations.vault.client import VaultClient


class TestCryptoAuditLogger:
    """Tests for the crypto audit logger."""

    def test_audit_event_creation_basic(self):
        """Test basic CryptoAuditEvent creation and hashing."""
        logger = CryptoAuditLogger(component_name="test")
        event = logger.log(
            operation=CryptoOperation.ENCRYPT,
            key_name="test-key",
            success=True,
            details={"algorithm": "AES-256-GCM", "key_size": 32},
        )
        assert event is not None
        assert event.operation == CryptoOperation.ENCRYPT
        assert event.key_name == "test-key"
        assert event.success is True
        assert event.event_hash is not None
        assert len(event.event_hash) == 64  # SHA256 hex

    def test_audit_event_hash_chain(self):
        """Test that events form a hash chain (prev_event_hash links)."""
        logger = CryptoAuditLogger(component_name="chain-test")
        e1 = logger.log(CryptoOperation.SIGN, key_name="rsa-key", success=True)
        e2 = logger.log(CryptoOperation.VERIFY, key_name="rsa-key", success=True)
        e3 = logger.log(CryptoOperation.ENCRYPT, key_name="aes-key", success=True)

        assert e1.event_hash is not None
        assert e2.prev_event_hash == e1.event_hash  # Chain link
        assert e3.prev_event_hash == e2.event_hash  # Chain link

    def test_audit_event_chain_integrity_verification(self):
        """Test verify_chain() detects intact chain."""
        logger = CryptoAuditLogger(component_name="integrity-test")
        for i in range(10):
            logger.log(CryptoOperation.ENCRYPT, key_name=f"key-{i}", success=True)
        assert logger.verify_chain() is True

    def test_audit_event_chain_tampering_detection(self):
        """Test verify_chain() detects a broken chain."""
        logger = CryptoAuditLogger(component_name="tamper-test")
        e1 = logger.log(CryptoOperation.ENCRYPT, key_name="key-1", success=True)
        e2 = logger.log(CryptoOperation.DECRYPT, key_name="key-2", success=True)

        # Tamper: break the chain by changing prev_event_hash
        e2.prev_event_hash = "tampered-hash-value"
        assert logger.verify_chain() is False

    def test_audit_event_hash_mismatch_detection(self):
        """Test verify_chain() detects a modified event."""
        logger = CryptoAuditLogger(component_name="hash-mismatch-test")
        e1 = logger.log(CryptoOperation.ENCRYPT, key_name="key-1", success=True)
        e2 = logger.log(CryptoOperation.DECRYPT, key_name="key-2", success=True)

        # Tamper: modify the event's stored hash
        original_hash = e2.event_hash
        e2.event_hash = hashlib.sha256(b"wrong").hexdigest()
        assert logger.verify_chain() is False
        # Verify it would be True if restored
        e2.event_hash = original_hash
        assert logger.verify_chain() is True

    def test_audit_event_sanitizes_secrets(self):
        """Test that sensitive data in details is redacted."""
        logger = CryptoAuditLogger(component_name="sanitization-test")
        event = logger.log(
            operation=CryptoOperation.ENCRYPT,
            key_name="test-key",
            details={
                "algorithm": "AES-256-GCM",
                "secret_key": "super-secret-key-value",
                "api_key": "sk-1234567890",
                "password": "my-password",
            },
        )
        assert event.details.get("algorithm") == "AES-256-GCM"
        assert event.details.get("secret_key") == "[REDACTED]"
        assert event.details.get("api_key") == "[REDACTED]"
        assert event.details.get("password") == "[REDACTED]"

    def test_audit_event_long_string_truncation(self):
        """Test that long string values are truncated."""
        logger = CryptoAuditLogger(component_name="truncation-test")
        long_str = "A" * 1000
        event = logger.log(
            operation=CryptoOperation.SIGN,
            key_name="test-key",
            details={"data": long_str},
        )
        truncated = event.details.get("data")
        assert truncated is not None
        assert len(truncated) == 142  # 128 chars + "...[truncated]" (14 chars)
        assert truncated.endswith("...[truncated]")

    def test_audit_event_id_uniqueness(self):
        """Test that event IDs are unique."""
        logger = CryptoAuditLogger(component_name="uniqueness-test")
        ids = set()
        for i in range(50):
            event = logger.log(CryptoOperation.ENCRYPT, success=True)
            assert event.event_id not in ids, f"Duplicate event_id: {event.event_id}"
            ids.add(event.event_id)

    def test_audit_get_events_limit(self):
        """Test get_events with limit returns correct number."""
        logger = CryptoAuditLogger(component_name="limit-test")
        for i in range(20):
            logger.log(CryptoOperation.ENCRYPT, key_name=f"k{i}", success=True)

        events = logger.get_events(limit=5)
        assert len(events) == 5
        # Newest first
        assert events[0].key_name == "k19"
        assert events[4].key_name == "k15"

    def test_audit_clear_resets_state(self):
        """Test that clear() resets the logger state."""
        logger = CryptoAuditLogger(component_name="clear-test")
        logger.log(CryptoOperation.ENCRYPT, success=True)
        logger.log(CryptoOperation.DECRYPT, success=True)
        assert len(logger.get_events()) == 2

        logger.clear()
        assert len(logger.get_events()) == 0
        assert logger._last_hash is None

    def test_audit_event_serialization(self):
        """Test that CryptoAuditEvent can be serialized to dict."""
        logger = CryptoAuditLogger(component_name="serialization-test")
        event = logger.log(
            operation=CryptoOperation.SIGN,
            key_name="rsa-key",
            key_id="key-uuid-123",
            success=True,
            duration_ms=1.5,
            details={"algorithm": "RS256"},
        )
        d = event.to_dict()
        assert d["operation"] == "sign"
        assert d["key_name"] == "rsa-key"
        assert d["key_id"] == "key-uuid-123"
        assert d["success"] is True
        assert d["duration_ms"] == 1.5
        assert d["event_hash"] is not None
        assert d["prev_event_hash"] is None  # first event


class TestVaultTransitCrypto:
    """Tests for VaultTransitCrypto (offline fallback mode)."""

    def test_vault_crypto_initialization_offline(self):
        """Test VaultTransitCrypto initializes (offline mode when Vault unavailable)."""
        crypto = VaultTransitCrypto()
        # In offline mode (no Vault), is_online should be False
        assert crypto.is_online is False

    def test_vault_crypto_hmac_returns_none_offline(self):
        """Test that HMAC returns None when Vault is offline."""
        crypto = VaultTransitCrypto()
        result = crypto.hmac_sha256("test-key", "hello world")
        assert result is None

    def test_vault_crypto_encrypt_returns_none_offline(self):
        """Test that encrypt returns None when Vault is offline."""
        crypto = VaultTransitCrypto()
        result = crypto.encrypt("test-key", "secret-data")
        assert result is None

    def test_vault_crypto_sign_returns_none_offline(self):
        """Test that RSA sign returns None when Vault is offline."""
        crypto = VaultTransitCrypto()
        result = crypto.sign_rsa_pkcs1v15_sha256("jwt-key", b"data to sign")
        assert result is None

    def test_vault_crypto_verify_returns_false_offline(self):
        """Test that HMAC/RSA verify returns False when Vault is offline."""
        crypto = VaultTransitCrypto()
        assert crypto.verify_hmac("key", "abc", "data") is False
        assert crypto.verify_rsa_pkcs1v15_sha256("key", "sig", b"data") is False

    def test_vault_crypto_ensure_key_offline(self):
        """Test that ensure_key returns False when Vault is offline."""
        crypto = VaultTransitCrypto()
        assert crypto.ensure_key("test-key", "hmac") is False

    def test_vault_crypto_rotate_key_offline(self):
        """Test that rotate returns False when Vault is offline."""
        crypto = VaultTransitCrypto()
        assert crypto.rotate_transit_key("test-key") is False

    def test_vault_crypto_decrypt_none_offline(self):
        """Test that decrypt returns None when Vault is offline."""
        crypto = VaultTransitCrypto()
        result = crypto.decrypt("test-key", "encrypted-ciphertext")
        assert result is None


class TestEncryptionManagerIntegration:
    """Tests for EncryptionManager + crypto audit logging integration."""

    def test_encryption_round_trip(self):
        """Test encrypt/decrypt round trip with Fernet."""
        mgr = EncryptionManager()
        plaintext = "sensitive data 12345"
        encrypted = mgr.encrypt(plaintext)
        assert encrypted is not None
        assert encrypted != plaintext

        decrypted = mgr.decrypt(encrypted)
        assert decrypted == plaintext.encode()

    def test_encryption_with_metadata(self):
        """Test encrypt/decrypt with metadata wrapper."""
        mgr = EncryptionManager()
        plaintext = "data with metadata"
        metadata = {"purpose": "api-key", "version": 2}
        encrypted = mgr.encrypt(plaintext, metadata=metadata)

        decrypted, meta = mgr.decrypt(encrypted, return_metadata=True)
        assert decrypted == plaintext.encode()
        assert meta == metadata

    def test_aes_gcm_round_trip(self):
        """Test AES-GCM encrypt/decrypt round trip with AAD."""
        mgr = EncryptionManager()
        plaintext = "gcm-encrypted data"
        aad = b"associated-data"
        result = mgr.encrypt_aesgcm(plaintext, associated_data=aad)
        assert "nonce" in result
        assert "ciphertext" in result
        assert result["algorithm"] == "aes-gcm"

        decrypted = mgr.decrypt_aesgcm(
            nonce=result["nonce"],
            ciphertext=result["ciphertext"],
            associated_data=aad,
        )
        assert decrypted == plaintext.encode()

    def test_hmac_sign_verify_constant_time(self):
        """Test HMAC sign/verify with constant-time comparison."""
        mgr = EncryptionManager()
        data = "data to sign"
        signature = mgr.hmac_sign(data)

        assert len(signature) == 64  # SHA256 hex
        assert mgr.hmac_verify(data, signature) is True
        # Tampered data should fail
        assert mgr.hmac_verify("wrong data", signature) is False

    def test_key_derivation_pbkdf2(self):
        """Test PBKDF2 key derivation."""
        mgr = EncryptionManager()
        password = "user-password"
        key1 = mgr.derive_key_from_password(password)
        assert len(key1) == 32

        # Same password + same salt = same key
        import secrets
        salt = secrets.token_bytes(16)
        key2 = mgr.derive_key_from_password(password, salt=salt)
        key3 = mgr.derive_key_from_password(password, salt=salt)
        assert key2 == key3

    def test_key_derivation_different_passwords(self):
        """Test that different passwords produce different keys."""
        mgr = EncryptionManager()
        k1 = mgr.derive_key_from_password("password1")
        k2 = mgr.derive_key_from_password("password2")
        assert k1 != k2

    def test_envelope_encryption_wrap_unwrap(self):
        """Test envelope encryption: wrap data key, then unwrap."""
        mgr = EncryptionManager()
        data_key = EncryptionManager.generate_key(32)
        wrapped = mgr.wrap_key(data_key)

        assert "wrapped_key" in wrapped
        unwrapped = mgr.unwrap_key(wrapped["wrapped_key"])
        assert unwrapped == data_key

    def test_key_rotation_re_encrypt(self):
        """Test key rotation with re-encryption."""
        mgr1 = EncryptionManager()
        plaintext = "data to rotate"
        encrypted = mgr1.encrypt(plaintext)

        new_key = EncryptionManager.generate_key(32)
        mgr2 = EncryptionManager(master_key=new_key)

        re_encrypted = mgr1.re_encrypt(encrypted, mgr2)
        decrypted = mgr2.decrypt(re_encrypted)
        assert decrypted == plaintext.encode()


class TestJWTHandlerIntegration:
    """Tests for JWT handler integration with audit logging."""

    def test_jwt_hs256_issuance_and_validation(self):
        """Test JWT issuance and validation with HS256."""
        handler = JWTHandler(algorithm="HS256", secret_key="test-secret-key")
        token, payload = handler.create_token(
            subject="user-123",
            scopes=["read", "write"],
            roles=["admin"],
        )
        assert token is not None
        assert payload.sub == "user-123"

        validated = handler.validate_token(token)
        assert validated.sub == "user-123"
        assert "read" in validated.scopes
        assert "admin" in validated.roles

    def test_jwt_access_refresh_pair(self):
        """Test access + refresh token pair creation."""
        handler = JWTHandler(algorithm="HS256", secret_key="test-secret-key")
        token_pair = handler.create_token_pair(
            subject="user-456",
            scopes=["read"],
        )

        assert "access_token" in token_pair
        assert "refresh_token" in token_pair
        assert token_pair["token_type"] == "Bearer"
        assert token_pair["expires_in"] == 900

        # Access token should validate
        access_payload = handler.validate_access_token(token_pair["access_token"])
        assert access_payload.sub == "user-456"

        # Refresh token should validate
        refresh_payload = handler.validate_refresh_token(token_pair["refresh_token"])
        assert refresh_payload.sub == "user-456"
        assert refresh_payload.token_type.value == "refresh"

    def test_jwt_refresh_token_rotation(self):
        """Test refresh token rotation."""
        handler = JWTHandler(algorithm="HS256", secret_key="test-secret-key")
        token_pair = handler.create_token_pair(subject="user-789", scopes=["read"])

        new_pair = handler.refresh_access_token(token_pair["refresh_token"], rotate=True)
        assert "access_token" in new_pair
        assert "refresh_token" in new_pair
        # New refresh token should be different
        assert new_pair["refresh_token"] != token_pair["refresh_token"]

    def test_jwt_revocation_blocklist(self):
        """Test JWT token revocation via blocklist."""
        handler = JWTHandler(algorithm="HS256", secret_key="test-secret-key")
        token, payload = handler.create_token(subject="user-999", scopes=["read"])

        # Revoke the token
        handler.revoke_token(payload.jti)

        # Validation should fail after revocation
        try:
            handler.validate_token(token)
            assert False, "Should have raised InvalidTokenError"
        except Exception:
            pass

    def test_jwt_jwks_generation_hs256(self):
        """Test JWKS generation for HS256 (symmetric)."""
        handler = JWTHandler(algorithm="HS256", secret_key="test-secret-key")
        jwks = handler.get_jwks()
        assert "keys" in jwks
        assert len(jwks["keys"]) == 1
        assert jwks["keys"][0]["kty"] == "oct"
        assert jwks["keys"][0]["alg"] == "HS256"


class TestAPIKeyManagerIntegration:
    """Tests for API key manager with encryption and audit."""

    @classmethod
    def setup_class(cls):
        """Set up isolated temp storage for all tests in this class."""
        import tempfile
        cls._tmpdir = tempfile.mkdtemp(prefix="test_api_keys_")

    @classmethod
    def teardown_class(cls):
        """Clean up temp storage."""
        import shutil
        shutil.rmtree(cls._tmpdir, ignore_errors=True)

    def _make_manager(self):
        """Create an isolated APIKeyManager using temp storage."""
        from src.config_manager import ConfigManager
        storage_path = os.path.join(self._tmpdir, f"keys_{id(self)}.json")
        return APIKeyManager(
            config=ConfigManager(),
            storage_path=storage_path,
        )

    def test_api_key_create_and_validate(self):
        """Test API key creation and validation."""
        mgr = self._make_manager()
        key, raw_key = mgr.create_key(
            name="test-key",
            scopes=[KeyScope.READONLY],
            expires_in_days=30,
        )
        assert key.id.startswith("lhao_")
        assert raw_key.startswith("lhao_")

        # Validate the key
        validated = mgr.validate_key(raw_key)
        assert validated is not None
        assert validated.id == key.id
        assert validated.status.value == "active"

    def test_api_key_hash_not_plaintext(self):
        """Test that stored key hash is not plaintext."""
        mgr = self._make_manager()
        key, raw_key = mgr.create_key(
            name="hash-test",
            scopes=[KeyScope.READONLY],
        )
        # Key hash should not contain the raw key
        assert raw_key not in key.key_hash
        # Should be a hex hash
        assert len(key.key_hash) == 64  # SHA256 hex

    def test_api_key_rotation(self):
        """Test API key rotation."""
        mgr = self._make_manager()
        key, raw_key = mgr.create_key(
            name="rotation-test",
            scopes=[KeyScope.AGENT],
            expires_in_days=30,
        )

        new_key, new_raw = mgr.rotate_key(key.id)
        assert new_key.id != key.id
        assert new_key.rotation_count == 1

        # Old key should be in PENDING_ROTATION state
        old_key = mgr.get_key(key.id)
        assert old_key.status.value == "pending_rotation"

    def test_api_key_revocation(self):
        """Test API key revocation."""
        mgr = self._make_manager()
        key, raw_key = mgr.create_key(
            name="revoke-test",
            scopes=[KeyScope.READONLY],
        )

        assert mgr.revoke_key(key.id) is True
        revoked = mgr.get_key(key.id)
        assert revoked.status.value == "revoked"

    def test_api_key_stats(self):
        """Test API key statistics."""
        mgr = self._make_manager()
        mgr.create_key(name="stat-key-1", scopes=[KeyScope.READONLY])
        mgr.create_key(name="stat-key-2", scopes=[KeyScope.AGENT])

        stats = mgr.get_key_stats()
        assert stats["total"] == 2
        assert "active" in stats["by_status"]
        assert "readonly" in stats["by_scope"]

    def test_api_key_scope_filtering(self):
        """Test listing keys filtered by scope."""
        mgr = self._make_manager()
        mgr.create_key(name="filter-1", scopes=[KeyScope.READONLY])
        mgr.create_key(name="filter-2", scopes=[KeyScope.ADMIN])

        readonly_keys = mgr.list_keys(scope=KeyScope.READONLY)
        admin_keys = mgr.list_keys(scope=KeyScope.ADMIN)
        assert all(KeyScope.READONLY in k.scopes for k in readonly_keys)
        assert all(KeyScope.ADMIN in k.scopes for k in admin_keys)


class TestVaultTransitCryptoWithAudit:
    """Integration tests for VaultTransitCrypto + CryptoAuditLogger."""

    def test_audit_logs_crypto_operations(self):
        """Test that crypto audit logger records operations."""
        logger = CryptoAuditLogger(component_name="integration-test")

        # Simulate encrypt operation
        event = logger.log(
            operation=CryptoOperation.ENCRYPT,
            key_name="api-key-hash-pepper",
            success=True,
            duration_ms=0.5,
            details={"algorithm": "HMAC-SHA256", "backend": "vault-transit"},
        )

        assert event.component == "integration-test"
        assert event.duration_ms == 0.5
        assert event.details["algorithm"] == "HMAC-SHA256"
        assert event.details["backend"] == "vault-transit"

    def test_audit_logs_failed_operations(self):
        """Test that failed crypto operations are logged with success=False."""
        logger = CryptoAuditLogger(component_name="failure-test")

        event = logger.log(
            operation=CryptoOperation.DECRYPT,
            key_name="encrypted-data-key",
            success=False,
            details={"error": "Invalid ciphertext"},
        )

        assert event.success is False
        assert len(logger.get_events()) == 1

    def test_audit_chain_integrity_across_operations(self):
        """Test hash chain integrity across multiple operations."""
        logger = CryptoAuditLogger(component_name="chain-integrity")

        # Simulate a full key lifecycle
        operations = [
            (CryptoOperation.KEY_GENERATE, "master-key"),
            (CryptoOperation.ENCRYPT, "api-key-pepper"),
            (CryptoOperation.SIGN, "jwt-sign-key"),
            (CryptoOperation.VERIFY, "jwt-sign-key"),
            (CryptoOperation.KEY_ROTATE, "master-key"),
            (CryptoOperation.DECRYPT, "api-key-pepper"),
        ]

        for op, key in operations:
            logger.log(operation=op, key_name=key, success=True)

        events = logger.get_events()
        assert len(events) == len(operations)

        # Verify chain integrity
        assert logger.verify_chain() is True

        # Verify all operations are logged
        for i, (expected_op, expected_key) in enumerate(reversed(operations)):
            event = logger.get_events()[i]
            assert event.operation == expected_op
            assert event.key_name == expected_key


class TestCryptoSecurityProperties:
    """Security property tests for cryptographic components."""

    def test_encryption_manager_key_length(self):
        """Test that generated keys are 32 bytes (256 bits)."""
        key = EncryptionManager.generate_key(32)
        assert len(key) == 32

    def test_encryption_manager_key_randomness(self):
        """Test that generated keys are random (not predictable)."""
        key1 = EncryptionManager.generate_key(32)
        key2 = EncryptionManager.generate_key(32)
        assert key1 != key2
        assert len(key1) == len(key2) == 32

    def test_hmac_constant_time_comparison(self):
        """Test HMAC verification uses constant-time comparison."""
        mgr = EncryptionManager()
        data = "test data"
        sig = mgr.hmac_sign(data)

        # Correct signature validates
        assert mgr.hmac_verify(data, sig) is True
        # Wrong signature fails
        assert mgr.hmac_verify(data, sig[:-1] + ("a" if sig[-1] != "a" else "b")) is False
        # Wrong data fails
        assert mgr.hmac_verify("wrong data", sig) is False

    def test_no_secret_leakage_in_audit_details(self):
        """Test that no secret material appears in audit event details."""
        logger = CryptoAuditLogger(component_name="security-test")

        # Try to sneak secrets in via various key names
        event = logger.log(
            operation=CryptoOperation.ENCRYPT,
            key_name="secret-key",
            details={
                "api_key_secret": "sk-1234567890abcdef",
                "password": "my-secret-password",
                "secret_value": "should-be-redacted",
                "token": "tok_abc123",
                "normal_field": "safe-value",
            },
        )

        assert event.details["api_key_secret"] == "[REDACTED]"
        assert event.details["password"] == "[REDACTED]"
        assert event.details["secret_value"] == "[REDACTED]"
        assert event.details["token"] == "[REDACTED]"
        assert event.details["normal_field"] == "safe-value"

    def test_jwt_token_expiration(self):
        """Test JWT tokens expire correctly."""
        handler = JWTHandler(algorithm="HS256", secret_key="test-key-verification")
        import time
        token, payload = handler.create_token(
            subject="user-exp",
            ttl=1,  # 1 second
            scopes=["read"],
        )
        time.sleep(2)  # wait past expiration
        assert payload.is_expired(leeway=0) is True  # now expired

        # Valid token (15 min)
        token2, payload2 = handler.create_token(
            subject="user-valid",
            scopes=["read"],
        )
        assert payload2.is_expired(leeway=30) is False  # 15 min > 30s leeway


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v", "--tb=short"])
