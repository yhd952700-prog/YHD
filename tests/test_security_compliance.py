"""
Comprehensive tests for LiuHao AI OS security and compliance modules.

Covers:
- Encryption (symmetric, asymmetric, hashing, HMAC, KDF, key management)
- Authentication (API keys, JWT, sessions, MFA, login tracking)
- Authorization (RBAC, permissions, role hierarchy, ABAC)
- Input sanitization (SQLi, XSS, command injection, path traversal, validation)
- Rate limiting (token bucket, sliding window, fixed window, tiered)
- Audit trail (event logging, hash chain, suspicious activity detection)
- GDPR compliance (consent, DSR, DPIA, breach, privacy by design)
- HIPAA compliance (PHI access, minimum necessary, BAA, security rule)
- SOC 2 compliance (controls, evidence, testing, gap analysis)
- ISO 27001 compliance (Annex A, risk assessment, SoA, audits)
- PCI DSS compliance (CDE, controls, vulnerability, access review)
- Compliance registry (frameworks, controls, evidence, gaps, reports)
"""

import os
import time

import pytest

# Security module imports
from src.security.encryption import (
    AesGcmCipher,
    DataAtRestEncryption,
    EncryptedData,
    HashAlgorithm,
    HashUtils,
    HmacUtils,
    InMemoryKeyManager,
    KeyDerivation,
    KeyMaterial,
    KeyType,
    PasswordHasher,
    RsaCipher,
    SecureRandom,
)
from src.security.authentication import (
    ApiKeyManager,
    AuthMethod,
    AuthStatus,
    JwtManager,
    LoginAttemptTracker,
    MfaManager,
    MfaMethod,
    PasswordAuthenticator,
    SessionManager,
    TotpMfaProvider,
)
from src.security.authorization import (
    Action,
    Permission,
    PermissionEffect,
    RbacManager,
    ResourceType,
    RoleStatus,
    SimpleAbacPolicy,
    create_default_rbac,
)
from src.security.input_sanitizer import (
    CommandInjectionGuard,
    FileUploadSecurity,
    InputValidator,
    PathTraversalGuard,
    SanitizationLevel,
    SqlInjectionGuard,
    ValidationStatus,
    XssGuard,
)
from src.security.rate_limit import (
    MemoryRateLimitBackend,
    RateLimitManager,
    RateLimitStatus,
    RateLimitStrategy,
    RateLimitTier,
)
from src.security.audit_trail import (
    AuditEventType,
    AuditSeverity,
    AuditTrailManager,
    HashChainAuditor,
    InMemoryAuditStore,
    SuspiciousActivityDetector,
)

# Compliance module imports
from src.compliance.gdpr import (
    ConsentManager,
    ConsentStatus,
    DataBreachManager,
    DataSubjectRight,
    DataSubjectRightsManager,
    DpiaManager,
    DpiaStatus,
    PrivacyByDesignChecker,
    ProcessingActivityType,
    ProcessingRecordsManager,
)
from src.compliance.hipaa import (
    AccessType,
    BaaManager,
    BaaStatus,
    BreachClassification,
    HipaaBreachManager,
    MinimumNecessityEnforcer,
    PhiAccessManager,
    PhiType,
    SecurityRuleChecker,
)
from src.compliance.soc2 import (
    ControlStatus as Soc2ControlStatus,
    Soc2ComplianceManager,
    TrustPrinciple,
)
from src.compliance.iso27001 import (
    AnnexAFramework,
    ControlImplementationStatus,
    IsmsManager,
    RiskAssessmentManager,
    RiskLevel,
    RiskTreatment,
)
from src.compliance.pci_dss import (
    CdeComponentType,
    CdeManager,
    PciDssComplianceManager,
    PciDssControls,
    PciDssGoal,
    PciDssRequirement,
    SaqType,
)
from src.compliance.registry import (
    ComplianceFramework,
    ComplianceRegistry,
    ControlStatus as RegControlStatus,
    EvidenceStatus,
)


# =========================================================================
# Encryption Tests
# =========================================================================


class TestAesGcmCipher:
    """Tests for AES-256-GCM symmetric encryption."""

    def test_encrypt_decrypt_roundtrip(self):
        key = os.urandom(32)
        cipher = AesGcmCipher(key)
        plaintext = b"Hello, LiuHao AI OS!"
        encrypted = cipher.encrypt(plaintext)
        decrypted = cipher.decrypt(encrypted)
        assert decrypted == plaintext

    def test_encrypt_with_associated_data(self):
        key = os.urandom(32)
        cipher = AesGcmCipher(key)
        plaintext = b"Secret data"
        aad = b"metadata: user=test"
        encrypted = cipher.encrypt(plaintext, associated_data=aad)
        assert encrypted.associated_data == aad
        decrypted = cipher.decrypt(encrypted)
        assert decrypted == plaintext

    def test_tampered_ciphertext_fails(self):
        key = os.urandom(32)
        cipher = AesGcmCipher(key)
        encrypted = cipher.encrypt(b"test data")
        # Tamper with ciphertext
        tampered = EncryptedData(
            ciphertext=encrypted.ciphertext[:-1] + b"X",
            nonce=encrypted.nonce,
        )
        from cryptography.exceptions import InvalidTag
        with pytest.raises(InvalidTag):
            cipher.decrypt(tampered)

    def test_invalid_key_length(self):
        with pytest.raises(ValueError, match="Key must be 32 bytes"):
            AesGcmCipher(b"too_short")

    def test_encrypted_data_serialization(self):
        key = os.urandom(32)
        cipher = AesGcmCipher(key, key_id="test-key")
        encrypted = cipher.encrypt(b"test", associated_data=b"aad")
        d = encrypted.to_dict()
        restored = EncryptedData.from_dict(d)
        assert restored.ciphertext == encrypted.ciphertext
        assert restored.nonce == encrypted.nonce
        assert restored.key_id == encrypted.key_id
        assert restored.associated_data == encrypted.associated_data


class TestRsaCipher:
    """Tests for RSA asymmetric encryption."""

    def test_generate_and_encrypt_decrypt(self):
        cipher = RsaCipher.generate(2048)
        plaintext = b"Secret AES key data"
        ciphertext = cipher.encrypt(plaintext)
        decrypted = cipher.decrypt(ciphertext)
        assert decrypted == plaintext

    def test_key_size_2048(self):
        cipher = RsaCipher.generate(2048)
        assert cipher.key_size == 2048

    def test_key_size_4096(self):
        cipher = RsaCipher.generate(4096)
        assert cipher.key_size == 4096

    def test_invalid_key_size(self):
        with pytest.raises(ValueError, match="Key size must be 2048 or 4096"):
            RsaCipher.generate(1024)

    def test_export_import_keys(self):
        cipher = RsaCipher.generate(2048)
        pub_pem = cipher.export_public_key()
        priv_pem = cipher.export_private_key()
        # Create new cipher from exported keys
        cipher2 = RsaCipher(public_key_pem=pub_pem, private_key_pem=priv_pem)
        plaintext = b"test message"
        assert cipher2.decrypt(cipher2.encrypt(plaintext)) == plaintext


class TestHashingAndPassword:
    """Tests for hashing and password utilities."""

    def test_sha256_hex(self):
        result = HashUtils.sha256(b"hello world")
        assert isinstance(result, str)
        assert len(result) == 64  # SHA-256 hex is 64 chars

    def test_sha256_bytes(self):
        result = HashUtils.sha256(b"hello world", hex_digest=False)
        assert isinstance(result, bytes)
        assert len(result) == 32

    def test_sha512_hex(self):
        result = HashUtils.sha512(b"hello world")
        assert len(result) == 128  # SHA-512 hex is 128 chars

    def test_password_hasher_roundtrip_pbkdf2(self):
        # Use SHA256 (PBKDF2 fallback) which always works
        hasher = PasswordHasher(HashAlgorithm.SHA256)
        password = "MySecurePassword123!"
        hashed = hasher.hash(password)
        assert hasher.verify(password, hashed) is True

    def test_password_hasher_wrong_password(self):
        hasher = PasswordHasher(HashAlgorithm.SHA256)
        hashed = hasher.hash("correct_password")
        assert hasher.verify("wrong_password", hashed) is False


class TestHmacAndKeyDerivation:
    """Tests for HMAC and key derivation."""

    def test_hmac_sha256(self):
        key = b"secret_key"
        msg = b"message"
        result = HmacUtils.sha256(key, msg)
        assert isinstance(result, str)
        assert len(result) == 64

    def test_hmac_verify(self):
        key = b"secret_key"
        msg = b"message"
        mac = HmacUtils.sha256(key, msg)
        assert HmacUtils.verify_sha256(key, msg, mac) is True
        assert HmacUtils.verify_sha256(key, b"wrong message", mac) is False

    def test_pbkdf2(self):
        key = KeyDerivation.pbkdf2(b"password", b"salt", length=32, iterations=1000)
        assert len(key) == 32

    def test_hkdf(self):
        ikm = os.urandom(32)
        key = KeyDerivation.hkdf(ikm, length=32, salt=b"salt", info=b"context")
        assert len(key) == 32


class TestKeyManagerAndDataAtRest:
    """Tests for key management and data-at-rest encryption."""

    def test_generate_aes_key(self):
        km = InMemoryKeyManager()
        key = km.generate_key(KeyType.AES_256, "test-aes")
        assert key.key_type == KeyType.AES_256
        assert len(key.key_bytes) == 32
        assert key.is_active is True

    def test_generate_rsa_key(self):
        km = InMemoryKeyManager()
        key = km.generate_key(KeyType.RSA_2048, "test-rsa")
        assert key.key_type == KeyType.RSA_2048
        assert key.key_bytes.startswith(b"-----BEGIN PRIVATE KEY-----")

    def test_rotate_key(self):
        km = InMemoryKeyManager()
        km.generate_key(KeyType.AES_256, "mykey")
        new_key = km.rotate_key("mykey")
        assert new_key.is_active is True
        old_key = km.export_key("mykey")
        assert old_key.is_active is False

    def test_data_at_rest_encrypt_decrypt(self):
        km = InMemoryKeyManager()
        km.generate_key(KeyType.AES_256, "kek-1")
        dare = DataAtRestEncryption(km, "kek-1")
        plaintext = b"Sensitive data at rest"
        envelope = dare.encrypt(plaintext)
        decrypted = dare.decrypt(envelope)
        assert decrypted == plaintext


class TestSecureRandom:
    """Tests for secure random generation."""

    def test_token_hex(self):
        token = SecureRandom.token_hex(16)
        assert len(token) == 32  # 16 bytes = 32 hex chars

    def test_token_urlsafe(self):
        token = SecureRandom.token_urlsafe(32)
        assert len(token) > 30

    def test_compare_digest(self):
        assert SecureRandom.compare_digest("abc", "abc") is True
        assert SecureRandom.compare_digest("abc", "abd") is False


# =========================================================================
# Authentication Tests
# =========================================================================


class TestApiKeyManager:
    """Tests for API key management."""

    def test_create_and_validate_key(self):
        mgr = ApiKeyManager()
        plaintext, key_info = mgr.create_key("user1", "Test Key")
        assert key_info.is_active is True
        validated = mgr.validate_key(plaintext)
        assert validated is not None
        assert validated.user_id == "user1"

    def test_invalid_key_rejected(self):
        mgr = ApiKeyManager()
        assert mgr.validate_key("invalid_key_here") is None

    def test_revoke_key(self):
        mgr = ApiKeyManager()
        plaintext, key_info = mgr.create_key("user1", "Key to revoke")
        assert mgr.revoke_key(key_info.key_id) is True
        assert mgr.validate_key(plaintext) is None


class TestJwtManager:
    """Tests for JWT token management."""

    def test_create_and_verify_access_token(self):
        jwt_mgr = JwtManager(secret_key="test_secret", access_token_ttl=3600)
        token, exp = jwt_mgr.create_access_token("user123", scopes=["read", "write"])
        payload = jwt_mgr.verify_token(token)
        assert payload is not None
        assert payload["sub"] == "user123"
        assert payload["type"] == "access"

    def test_expired_token_rejected(self):
        jwt_mgr = JwtManager(secret_key="test_secret", access_token_ttl=-1)  # Already expired
        token, _ = jwt_mgr.create_access_token("user123")
        assert jwt_mgr.verify_token(token) is None

    def test_revoke_token(self):
        jwt_mgr = JwtManager(secret_key="test_secret")
        token, _ = jwt_mgr.create_access_token("user123")
        assert jwt_mgr.verify_token(token) is not None
        jwt_mgr.revoke_token(token)
        assert jwt_mgr.verify_token(token) is None

    def test_refresh_token_rotation(self):
        jwt_mgr = JwtManager(secret_key="test_secret")
        refresh_token, _ = jwt_mgr.create_refresh_token("user123")
        result = jwt_mgr.refresh_token(refresh_token)
        assert result is not None
        new_access, new_refresh, exp = result
        # Old refresh token should be revoked
        assert jwt_mgr.verify_token(refresh_token) is None
        # New tokens should be valid
        assert jwt_mgr.verify_token(new_access) is not None
        assert jwt_mgr.verify_token(new_refresh) is not None


class TestSessionManager:
    """Tests for session management."""

    def test_create_and_get_session(self):
        sm = SessionManager()
        session = sm.create_session("user1", ip_address="127.0.0.1")
        assert session.is_active is True
        retrieved = sm.get_session(session.session_id)
        assert retrieved is not None
        assert retrieved.user_id == "user1"

    def test_end_session(self):
        sm = SessionManager()
        session = sm.create_session("user1")
        assert sm.end_session(session.session_id) is True
        assert sm.get_session(session.session_id) is None

    def test_end_all_user_sessions(self):
        sm = SessionManager()
        s1 = sm.create_session("user1")
        s2 = sm.create_session("user1")
        s3 = sm.create_session("user2")
        count = sm.end_all_user_sessions("user1")
        assert count == 2
        assert sm.get_session(s1.session_id) is None
        assert sm.get_session(s3.session_id) is not None


class TestLoginAttemptTracker:
    """Tests for login attempt tracking and lockout."""

    def test_successful_attempt(self):
        tracker = LoginAttemptTracker(max_attempts=3)
        result = tracker.record_attempt(True, user_id="user1", ip_address="1.2.3.4")
        assert result.success is True
        assert result.status == AuthStatus.SUCCESS

    def test_lockout_after_max_attempts(self):
        tracker = LoginAttemptTracker(max_attempts=3, lockout_duration=60)
        for i in range(3):
            tracker.record_attempt(False, user_id="user1", ip_address="1.2.3.4")
        # Fourth attempt should trigger lockout
        result = tracker.record_attempt(False, user_id="user1", ip_address="1.2.3.4")
        assert result.status == AuthStatus.LOCKED
        assert tracker.is_locked_out(user_id="user1") is True

    def test_remaining_attempts(self):
        tracker = LoginAttemptTracker(max_attempts=5)
        tracker.record_attempt(False, user_id="user1", ip_address="1.2.3.4")
        remaining = tracker.get_remaining_attempts(user_id="user1")
        assert remaining == 4


class TestMfaManager:
    """Tests for MFA management."""

    def test_enable_mfa(self):
        mfa = MfaManager()
        secret, uri = mfa.enable_mfa("user1")
        assert secret is not None
        assert uri.startswith("otpauth://totp/")
        assert mfa.is_enabled("user1") is True

    def test_backup_codes(self):
        mfa = MfaManager()
        mfa.enable_mfa("user1")
        codes = mfa.get_backup_codes("user1")
        assert len(codes) == 10

    def test_disable_mfa(self):
        mfa = MfaManager()
        mfa.enable_mfa("user1")
        assert mfa.disable_mfa("user1") is True
        assert mfa.is_enabled("user1") is False


# =========================================================================
# Authorization Tests
# =========================================================================


class TestRbacManager:
    """Tests for RBAC authorization."""

    def test_create_role(self):
        rbac = RbacManager()
        role = rbac.create_role("editor", "Editor", "Can edit content")
        assert role.role_id == "editor"
        assert role.name == "Editor"

    def test_add_permission_to_role(self):
        rbac = RbacManager()
        role = rbac.create_role("editor", "Editor")
        perm = Permission(PermissionEffect.ALLOW, "edit", "document")
        role.add_permission(perm)
        assert len(role.permissions) == 1

    def test_assign_and_check_permission(self):
        rbac = RbacManager()
        rbac.create_role("editor", "Editor")
        role = rbac.get_role("editor")
        role.add_permission(Permission(PermissionEffect.ALLOW, "edit", "document"))
        rbac.assign_role("user1", "editor")
        assert rbac.can_user_do("user1", "edit", "document") is True

    def test_deny_permission_not_assigned(self):
        rbac = RbacManager()
        rbac.create_role("viewer", "Viewer")
        rbac.assign_role("user1", "viewer")
        assert rbac.can_user_do("user1", "delete", "document") is False

    def test_role_inheritance(self):
        rbac = RbacManager()
        viewer = rbac.create_role("viewer", "Viewer")
        viewer.add_permission(Permission(PermissionEffect.ALLOW, "read", "document"))
        editor = rbac.create_role("editor", "Editor", parent_roles=["viewer"])
        editor.add_permission(Permission(PermissionEffect.ALLOW, "edit", "document"))
        rbac.assign_role("user1", "editor")
        # Should have both read (inherited) and edit (own)
        assert rbac.can_user_do("user1", "read", "document") is True
        assert rbac.can_user_do("user1", "edit", "document") is True

    def test_cycle_detection(self):
        rbac = RbacManager()
        rbac.create_role("role_a", "Role A")
        rbac.create_role("role_b", "Role B")
        rbac.add_parent_role("role_b", "role_a")
        with pytest.raises(ValueError, match="cycle"):
            rbac.add_parent_role("role_a", "role_b")

    def test_default_roles(self):
        rbac = create_default_rbac()
        roles = rbac.list_roles()
        assert len(roles) >= 5
        assert any(r.role_id == "super_admin" for r in roles)
        assert any(r.role_id == "admin" for r in roles)


class TestAbacPolicy:
    """Tests for ABAC policy evaluation."""

    def test_simple_abac_allow(self):
        policy = SimpleAbacPolicy(
            policy_id="test_policy",
            description="Allow access during business hours",
            effect=PermissionEffect.ALLOW,
            context_conditions={"time_of_day": "business_hours"},
        )
        result = policy.evaluate(
            user_attributes={"role": "user"},
            resource_attributes={"type": "document"},
            action="read",
            context={"time_of_day": "business_hours"},
        )
        assert result == PermissionEffect.ALLOW

    def test_simple_abac_no_match(self):
        policy = SimpleAbacPolicy(
            policy_id="test_policy",
            description="Test policy",
            effect=PermissionEffect.ALLOW,
            user_conditions={"department": "engineering"},
        )
        result = policy.evaluate(
            user_attributes={"department": "sales"},
            resource_attributes={},
            action="read",
            context={},
        )
        assert result is None  # Policy doesn't apply

    def test_rbac_with_abac(self):
        rbac = RbacManager()
        rbac.create_role("employee", "Employee")
        role = rbac.get_role("employee")
        role.add_permission(Permission(PermissionEffect.ALLOW, "read", "document"))
        rbac.assign_role("user1", "employee")

        # Add ABAC deny policy
        policy = SimpleAbacPolicy(
            policy_id="deny_outside_hours",
            description="Deny access outside business hours",
            effect=PermissionEffect.DENY,
            context_conditions={"time_of_day": "outside_hours"},
            action="read",
        )
        rbac.add_abac_policy(policy)

        # Should be denied by ABAC even though RBAC allows
        result = rbac.evaluate_policy(
            "user1", "read", "document",
            context={"time_of_day": "outside_hours"},
        )
        assert result.allowed is False


# =========================================================================
# Input Sanitizer Tests
# =========================================================================


class TestSqlInjectionGuard:
    """Tests for SQL injection prevention."""

    def test_detect_union_select(self):
        guard = SqlInjectionGuard()
        detected = guard.detect("1' UNION SELECT * FROM users--")
        assert len(detected) > 0

    def test_detect_drop_table(self):
        guard = SqlInjectionGuard()
        assert guard.is_suspicious("; DROP TABLE users;") is True

    def test_safe_input(self):
        guard = SqlInjectionGuard()
        assert guard.is_suspicious("Hello World") is False

    def test_validate_identifier_valid(self):
        guard = SqlInjectionGuard()
        result = guard.validate_identifier("user_name")
        assert result.is_valid is True

    def test_validate_identifier_invalid(self):
        guard = SqlInjectionGuard()
        result = guard.validate_identifier("user'; DROP--")
        assert result.is_valid is False


class TestXssGuard:
    """Tests for XSS prevention."""

    def test_detect_script_tag(self):
        guard = XssGuard()
        assert guard.is_suspicious("<script>alert('xss')</script>") is True

    def test_detect_javascript_uri(self):
        guard = XssGuard()
        assert guard.is_suspicious('<a href="javascript:alert(1)">') is True

    def test_encode_for_html(self):
        guard = XssGuard()
        encoded = guard.encode_for_html("<script>")
        assert "<script>" not in encoded
        assert "&lt;script&gt;" in encoded

    def test_strip_all_tags(self):
        guard = XssGuard()
        result = guard.sanitize_html("<p>Hello <b>World</b></p>", SanitizationLevel.STRICT)
        assert result == "Hello World"


class TestCommandInjectionGuard:
    """Tests for command injection prevention."""

    def test_detect_semicolon(self):
        guard = CommandInjectionGuard()
        assert guard.is_suspicious("test; rm -rf /") is True

    def test_detect_backtick(self):
        guard = CommandInjectionGuard()
        assert guard.is_suspicious("`cat /etc/passwd`") is True

    def test_safe_filename(self):
        guard = CommandInjectionGuard()
        assert guard.is_safe_filename("document.pdf") is True

    def test_unsafe_filename(self):
        guard = CommandInjectionGuard()
        assert guard.is_safe_filename("../etc/passwd") is False


class TestPathTraversalGuard:
    """Tests for path traversal prevention."""

    def test_detect_dotdot(self):
        guard = PathTraversalGuard()
        assert guard.is_suspicious("../../../etc/passwd") is True

    def test_sanitize_path(self):
        guard = PathTraversalGuard()
        result = guard.sanitize("../../../etc/passwd")
        assert ".." not in result

    def test_safe_join(self):
        guard = PathTraversalGuard()
        result = guard.safe_join("/var/data", "user", "file.txt")
        assert result is not None
        import os
        assert result.startswith(os.path.normpath("/var/data"))

    def test_unsafe_join(self):
        guard = PathTraversalGuard()
        result = guard.safe_join("/var/data", "../../etc/passwd")
        assert result is None


class TestInputValidator:
    """Tests for input validation."""

    def test_validate_email_valid(self):
        validator = InputValidator()
        result = validator.validate_email("user@example.com")
        assert result.is_valid is True

    def test_validate_email_invalid(self):
        validator = InputValidator()
        result = validator.validate_email("not-an-email")
        assert result.is_valid is False

    def test_validate_url_valid(self):
        validator = InputValidator()
        result = validator.validate_url("https://example.com/path")
        assert result.is_valid is True

    def test_validate_url_javascript(self):
        validator = InputValidator()
        result = validator.validate_url("javascript:alert(1)")
        assert result.is_valid is False

    def test_validate_username_valid(self):
        validator = InputValidator()
        result = validator.validate_username("user_123")
        assert result.is_valid is True

    def test_validate_ipv4_valid(self):
        validator = InputValidator()
        result = validator.validate_ip_address("192.168.1.1")
        assert result.is_valid is True

    def test_validate_integer(self):
        validator = InputValidator()
        result = validator.validate_integer(5, min_value=1, max_value=10)
        assert result.is_valid is True

    def test_validate_integer_out_of_range(self):
        validator = InputValidator()
        result = validator.validate_integer(15, min_value=1, max_value=10)
        assert result.is_valid is False


class TestFileUploadSecurity:
    """Tests for file upload security checks."""

    def test_safe_text_file(self):
        security = FileUploadSecurity(max_file_size=1000)
        result = security.check_upload("test.txt", b"Hello world")
        assert result.is_safe is True

    def test_file_too_large(self):
        security = FileUploadSecurity(max_file_size=10)
        result = security.check_upload("test.txt", b"x" * 100)
        assert result.is_safe is False
        assert any("size" in e.lower() for e in result.errors)

    def test_dangerous_extension(self):
        security = FileUploadSecurity()
        result = security.check_upload("malware.exe", b"\x90\x90\x90")
        assert result.is_safe is False

    def test_path_traversal_filename(self):
        security = FileUploadSecurity()
        result = security.check_upload("../../../etc/passwd", b"test")
        assert any("traversal" in e.lower() for e in result.errors)


# =========================================================================
# Rate Limiting Tests
# =========================================================================


class TestRateLimiting:
    """Tests for rate limiting strategies."""

    def test_token_bucket_allows(self):
        rlm = RateLimitManager()
        result = rlm.check("test_key", 10, 60, RateLimitStrategy.TOKEN_BUCKET, burst=0)
        assert result.allowed is True
        assert result.status == RateLimitStatus.ALLOWED

    def test_token_bucket_limits(self):
        rlm = RateLimitManager()
        key = "limit_test"
        # Consume all tokens
        for _ in range(5):
            rlm.check(key, 5, 60, RateLimitStrategy.TOKEN_BUCKET)
        # Next request should be limited
        result = rlm.check(key, 5, 60, RateLimitStrategy.TOKEN_BUCKET)
        assert result.allowed is False
        assert result.status == RateLimitStatus.LIMITED

    def test_sliding_window(self):
        rlm = RateLimitManager()
        key = "sliding_test"
        for i in range(3):
            result = rlm.check(key, 3, 60, RateLimitStrategy.SLIDING_WINDOW)
            assert result.allowed is True
        # Fourth request should be limited
        result = rlm.check(key, 3, 60, RateLimitStrategy.SLIDING_WINDOW)
        assert result.allowed is False

    def test_fixed_window(self):
        rlm = RateLimitManager()
        key = "fixed_test"
        for i in range(2):
            result = rlm.check(key, 2, 60, RateLimitStrategy.FIXED_WINDOW)
            assert result.allowed is True
        result = rlm.check(key, 2, 60, RateLimitStrategy.FIXED_WINDOW)
        assert result.allowed is False

    def test_tiered_rate_limits(self):
        rlm = RateLimitManager()
        free_result = rlm.check_user("user1", "api_requests", RateLimitTier.FREE)
        pro_result = rlm.check_user("user2", "api_requests", RateLimitTier.PRO)
        assert pro_result.limit > free_result.limit

    def test_rate_limit_headers(self):
        rlm = RateLimitManager()
        result = rlm.check("header_test", 10, 60)
        headers = RateLimitManager.make_headers(result)
        hd = headers.to_dict()
        assert "X-RateLimit-Limit" in hd
        assert "X-RateLimit-Remaining" in hd
        assert "X-RateLimit-Reset" in hd


# =========================================================================
# Audit Trail Tests
# =========================================================================


class TestAuditTrail:
    """Tests for security audit trail."""

    def test_log_event(self):
        atm = AuditTrailManager()
        event = atm.log_event(
            AuditEventType.LOGIN,
            AuditSeverity.LOW,
            actor_id="user1",
            ip_address="127.0.0.1",
            status="success",
        )
        assert event.event_type == AuditEventType.LOGIN
        assert event.event_hash is not None

    def test_hash_chain_integrity(self):
        atm = AuditTrailManager()
        for i in range(5):
            atm.log_event(
                AuditEventType.DATA_ACCESS,
                AuditSeverity.LOW,
                actor_id=f"user{i}",
            )
        is_valid, count, errors = atm.verify_integrity()
        assert is_valid is True
        assert count == 5
        assert len(errors) == 0

    def test_search_events(self):
        atm = AuditTrailManager()
        atm.log_event(AuditEventType.LOGIN, AuditSeverity.LOW, actor_id="user1")
        atm.log_event(AuditEventType.LOGIN_FAILED, AuditSeverity.MEDIUM, actor_id="user2")
        atm.log_event(AuditEventType.LOGOUT, AuditSeverity.LOW, actor_id="user1")

        login_events = atm.search(event_type=AuditEventType.LOGIN)
        assert len(login_events) == 1

        user1_events = atm.search(actor_id="user1")
        assert len(user1_events) == 2

    def test_compliance_report(self):
        atm = AuditTrailManager()
        atm.log_event(AuditEventType.LOGIN, AuditSeverity.LOW, actor_id="u1")
        atm.log_event(AuditEventType.LOGIN_FAILED, AuditSeverity.MEDIUM, actor_id="u2")
        atm.log_event(AuditEventType.PERMISSION_CHANGE, AuditSeverity.HIGH, actor_id="admin")

        report = atm.generate_compliance_report()
        assert report.total_events == 3
        assert report.failed_logins == 1
        assert report.permission_changes == 1

    def test_suspicious_activity_detection(self):
        detector = SuspiciousActivityDetector(failed_login_threshold=3, failed_login_window=300)
        events = []
        for i in range(5):
            from src.security.audit_trail import AuditEvent
            events.append(AuditEvent(
                event_id=f"evt_{i}",
                event_type=AuditEventType.LOGIN_FAILED,
                severity=AuditSeverity.MEDIUM,
                timestamp=time.time(),
                ip_address="10.0.0.1",
            ))
        alerts = detector.analyze_events(events)
        assert len(alerts) >= 1
        assert any(a["type"] == "brute_force_login" for a in alerts)


# =========================================================================
# GDPR Compliance Tests
# =========================================================================


class TestGdprConsent:
    """Tests for GDPR consent management."""

    def test_grant_consent(self):
        cm = ConsentManager()
        record = cm.grant_consent("user1", "marketing")
        assert record.status == ConsentStatus.GRANTED

    def test_withdraw_consent(self):
        cm = ConsentManager()
        cm.grant_consent("user1", "marketing")
        assert cm.has_consent("user1", "marketing") is True
        cm.withdraw_consent("user1", "marketing")
        assert cm.has_consent("user1", "marketing") is False

    def test_consent_history(self):
        cm = ConsentManager()
        cm.grant_consent("user1", "marketing", version="1.0")
        cm.withdraw_consent("user1", "marketing")
        cm.grant_consent("user1", "marketing", version="2.0")
        history = cm.get_consent_history("user1", "marketing")
        assert len(history) >= 2
        statuses = [r.status for r in history]
        assert ConsentStatus.GRANTED in statuses


class TestGdprDataSubjectRights:
    """Tests for GDPR data subject rights."""

    def test_submit_access_request(self):
        dsrm = DataSubjectRightsManager()
        req = dsrm.submit_request("user1", DataSubjectRight.ACCESS)
        assert req.right == DataSubjectRight.ACCESS
        assert req.status == "pending"

    def test_complete_request(self):
        dsrm = DataSubjectRightsManager()
        req = dsrm.submit_request("user1", DataSubjectRight.ACCESS)
        assert dsrm.complete_request(req.request_id, "Data provided") is True
        updated = dsrm.get_request(req.request_id)
        assert updated.status == "completed"

    def test_erase_data(self):
        dsrm = DataSubjectRightsManager()
        dsrm.register_user_data("user1", {"email": "user@test.com", "name": "Test User"})
        erased = dsrm.erase_data("user1")
        assert len(erased) > 0


class TestGdprDpia:
    """Tests for GDPR DPIA management."""

    def test_create_dpia(self):
        dm = DpiaManager()
        dpia = dm.create_dpia("New AI Feature", "Processing user data for AI training")
        assert dpia.status == DpiaStatus.DRAFT

    def test_dpia_required_check(self):
        dm = DpiaManager()
        # Should be required when special categories + profiling
        from src.compliance.gdpr import DataCategory, ProcessingActivityType
        required = dm.is_dpia_required(
            data_categories=[DataCategory.HEALTH],
            processing_types=[ProcessingActivityType.PROFILING],
            at_scale=True,
        )
        assert required is True


class TestGdprBreach:
    """Tests for GDPR data breach management."""

    def test_record_breach(self):
        bm = DataBreachManager()
        breach = bm.record_breach("Data exposure", "User emails")
        assert breach.breach_id is not None

    def test_72h_window(self):
        bm = DataBreachManager()
        breach = bm.record_breach("Test breach", "test")
        assert bm.is_within_72h_window(breach.breach_id) is True


class TestPrivacyByDesign:
    """Tests for privacy by design checker."""

    def test_create_checklist(self):
        checker = PrivacyByDesignChecker()
        checklist = checker.create_checklist("New Feature")
        assert len(checklist.items) > 10

    def test_completion_percentage(self):
        checker = PrivacyByDesignChecker()
        checklist = checker.create_checklist("Test")
        pct = checker.get_completion_percentage(checklist)
        assert pct == 0.0

        items = checker.list_all_items()
        first_key = list(items.keys())[0]
        checker.mark_item(checklist, first_key, True)
        assert checker.get_completion_percentage(checklist) > 0


# =========================================================================
# HIPAA Compliance Tests
# =========================================================================


class TestHipaaPhiAccess:
    """Tests for HIPAA PHI access management."""

    def test_create_phi_record(self):
        pam = PhiAccessManager()
        record = pam.create_phi_record(PhiType.MEDICAL_RECORD, "patient1", "doctor1")
        assert record.phi_type == PhiType.MEDICAL_RECORD
        assert record.patient_id == "patient1"

    def test_authorized_access(self):
        pam = PhiAccessManager()
        record = pam.create_phi_record(PhiType.MEDICAL_RECORD, "patient1", "doctor1")
        pam.authorize_user("doctor1", record.record_id, "admin")
        allowed, _ = pam.check_access("doctor1", record.record_id, AccessType.VIEW)
        assert allowed is True

    def test_unauthorized_access(self):
        pam = PhiAccessManager()
        record = pam.create_phi_record(PhiType.MEDICAL_RECORD, "patient1", "doctor1")
        allowed, reason = pam.check_access("stranger", record.record_id, AccessType.VIEW)
        assert allowed is False

    def test_access_logging(self):
        pam = PhiAccessManager()
        record = pam.create_phi_record(PhiType.MEDICAL_RECORD, "p1", "d1")
        pam.authorize_user("d1", record.record_id, "admin")
        pam.record_access("d1", record.record_id, AccessType.VIEW, "treatment")
        logs = pam.get_access_logs(phi_id=record.record_id)
        assert len(logs) == 1


class TestHipaaMinimumNecessary:
    """Tests for HIPAA minimum necessary standard."""

    def test_evaluate_allowed(self):
        mne = MinimumNecessityEnforcer()
        result = mne.evaluate_request(
            "doc1", "physician", PhiType.MEDICAL_RECORD, "treatment", ["diagnosis"]
        )
        assert result.is_compliant is True

    def test_evaluate_denied(self):
        mne = MinimumNecessityEnforcer()
        result = mne.evaluate_request(
            "receptionist1", "receptionist", PhiType.GENETIC, "payment", []
        )
        assert result.is_compliant is False


class TestHipaaBaa:
    """Tests for HIPAA BAA management."""

    def test_create_baa(self):
        bm = BaaManager()
        baa = bm.create_baa(
            "CloudProvider Inc.",
            "Business Associate",
            [PhiType.MEDICAL_RECORD],
            ["Cloud storage"],
        )
        assert baa.status == BaaStatus.PENDING

    def test_sign_baa(self):
        bm = BaaManager()
        baa = bm.create_baa("Test Co", "BA", [PhiType.NAME], ["Hosting"])
        now = time.time()
        bm.sign_baa(baa.baa_id, now, now + 365 * 86400)
        assert bm.has_baa("Test Co") is True


class TestHipaaSecurityRule:
    """Tests for HIPAA Security Rule checker."""

    def test_initial_status(self):
        checker = SecurityRuleChecker()
        status = checker.get_compliance_status()
        assert status["total_controls"] > 10
        assert status["implemented_controls"] == 0

    def test_mark_implemented(self):
        checker = SecurityRuleChecker()
        checker.mark_control_implemented("access_control", "Role-based access implemented")
        status = checker.get_compliance_status()
        assert status["implemented_controls"] >= 1


# =========================================================================
# SOC 2 Compliance Tests
# =========================================================================


class TestSoc2:
    """Tests for SOC 2 compliance."""

    def test_controls_framework(self):
        mgr = Soc2ComplianceManager()
        controls = mgr.framework.list_controls()
        assert len(controls) > 20

    def test_controls_by_principle(self):
        mgr = Soc2ComplianceManager()
        security_controls = mgr.framework.list_controls(principle=TrustPrinciple.SECURITY)
        assert len(security_controls) > 10

    def test_update_control_status(self):
        mgr = Soc2ComplianceManager()
        assert mgr.framework.update_control_status("CC6.1", Soc2ControlStatus.IMPLEMENTED, "Implemented RBAC") is True
        control = mgr.framework.get_control("CC6.1")
        assert control.status == Soc2ControlStatus.IMPLEMENTED

    def test_evidence_collection(self):
        mgr = Soc2ComplianceManager()
        evidence = mgr.evidence.add_evidence(
            "CC6.1",
            "policy",
            "Access Control Policy",
            "Documented access control policy",
            "/docs/access-policy.pdf",
            "security_officer",
        )
        assert evidence.evidence_id is not None
        assert evidence.title == "Access Control Policy"

    def test_gap_analysis(self):
        mgr = Soc2ComplianceManager()
        gaps = mgr.identify_gaps()
        assert len(gaps) > 0

    def test_compliance_report(self):
        mgr = Soc2ComplianceManager()
        report = mgr.generate_report([TrustPrinciple.SECURITY])
        assert report.total_controls > 0
        assert "compliance report" in report.summary.lower()


# =========================================================================
# ISO 27001 Compliance Tests
# =========================================================================


class TestIso27001:
    """Tests for ISO 27001 compliance."""

    def test_annex_a_controls(self):
        framework = AnnexAFramework()
        controls = framework.list_controls()
        assert len(controls) > 20

    def test_risk_identification(self):
        rm = RiskAssessmentManager()
        risk = rm.identify_risk(
            "Data breach risk",
            "Customer database",
            "External attacker",
            "Weak password policy",
            likelihood=3,
            impact=4,
        )
        assert risk.risk_level == RiskLevel.HIGH

    def test_risk_treatment(self):
        rm = RiskAssessmentManager()
        risk = rm.identify_risk("Test risk", "Asset", "Threat", "Vuln", 2, 2)
        rm.set_treatment(risk.risk_id, RiskTreatment.MITIGATE, ["A.9.1", "A.9.2"])
        assert risk.treatment == RiskTreatment.MITIGATE

    def test_risk_summary(self):
        rm = RiskAssessmentManager()
        rm.identify_risk("Risk 1", "A", "T", "V", 1, 1)
        rm.identify_risk("Risk 2", "A", "T", "V", 4, 5)
        summary = rm.get_risk_summary()
        assert summary["total_risks"] == 2

    def test_soa_generation(self):
        isms = IsmsManager()
        soa = isms.generate_soa(approved_by="CISO")
        assert soa.total_controls > 0

    def test_internal_audit(self):
        isms = IsmsManager()
        audit = isms.schedule_audit(
            "Q1 Internal Audit",
            time.time(),
            time.time() + 86400,
            "lead_auditor",
        )
        assert audit.audit_id is not None

    def test_corrective_action(self):
        isms = IsmsManager()
        action = isms.create_corrective_action(
            "Fix access control gap",
            "Unauthorized access possible",
            source="audit",
        )
        assert action.action_id is not None


# =========================================================================
# PCI DSS Compliance Tests
# =========================================================================


class TestPciDss:
    """Tests for PCI DSS compliance."""

    def test_cde_component(self):
        cde = CdeManager()
        comp = cde.add_component(
            "web-server-01",
            CdeComponentType.SERVER,
            "10.0.1.10",
            in_cde=True,
            network_zone="cde",
        )
        assert comp.component_type == CdeComponentType.SERVER

    def test_cde_summary(self):
        cde = CdeManager()
        cde.add_component("s1", CdeComponentType.SERVER, "10.0.0.1", in_cde=True)
        cde.add_component("db1", CdeComponentType.DATABASE, "10.0.0.2", in_cde=True)
        cde.add_component("fw1", CdeComponentType.FIREWALL, "10.0.0.3", in_cde=True, network_zone="dmz")
        summary = cde.get_cde_summary()
        assert summary["in_cde"] == 3

    def test_pci_controls(self):
        controls = PciDssControls()
        all_controls = controls.list_controls()
        assert len(all_controls) == 12

    def test_controls_by_goal(self):
        controls = PciDssControls()
        goal1_controls = controls.get_requirements_for_goal(PciDssGoal.GOAL_1)
        assert len(goal1_controls) == 2  # Req 1 & 2

    def test_vulnerability_scan(self):
        pci = PciDssComplianceManager()
        scan = pci.vulnerability.record_scan(
            "server1",
            scanner="nessus",
            critical=1,
            high=3,
            medium=5,
        )
        assert scan.critical_count == 1

    def test_access_review(self):
        pci = PciDssComplianceManager()
        review = pci.access_control.record_access_review(
            "quarterly",
            "security_manager",
            total_users=50,
            access_revoked=2,
        )
        assert review.total_users_reviewed == 50

    def test_compliance_report(self):
        pci = PciDssComplianceManager()
        report = pci.generate_report()
        assert report.total_requirements == 12


# =========================================================================
# Compliance Registry Tests
# =========================================================================


class TestComplianceRegistry:
    """Tests for the compliance registry."""

    def test_register_framework(self):
        reg = ComplianceRegistry()
        reg.register_framework(ComplianceFramework.GDPR)
        assert reg.is_framework_active(ComplianceFramework.GDPR) is True

    def test_register_control(self):
        reg = ComplianceRegistry()
        control = reg.register_control(
            "ctrl-001",
            "Access Control",
            "Implement role-based access control",
            ComplianceFramework.SOC2,
            "CC6.1",
            category="access_control",
        )
        assert control.control_id == "ctrl-001"

    def test_update_control_status(self):
        reg = ComplianceRegistry()
        reg.register_control("c1", "Test", "Desc", ComplianceFramework.GDPR, "R1")
        reg.update_control_status("c1", RegControlStatus.IMPLEMENTED)
        control = reg.get_control("c1")
        assert control.status == RegControlStatus.IMPLEMENTED

    def test_evidence_collection(self):
        reg = ComplianceRegistry()
        reg.register_control("c1", "Test", "Desc", ComplianceFramework.GDPR, "R1")
        evidence = reg.add_evidence(
            "Access Policy Evidence",
            "Documented policy",
            "policy",
            control_ids=["c1"],
        )
        assert evidence.status == EvidenceStatus.COLLECTED

    def test_gap_analysis(self):
        reg = ComplianceRegistry()
        reg.register_framework(ComplianceFramework.GDPR)
        reg.register_control("c1", "Test", "Desc", ComplianceFramework.GDPR, "R1")
        gaps = reg.identify_gaps()
        assert len(gaps) >= 1

    def test_compliance_report(self):
        reg = ComplianceRegistry()
        reg.register_framework(ComplianceFramework.SOC2)
        reg.register_control("c1", "Ctrl1", "Desc1", ComplianceFramework.SOC2, "CC1")
        reg.register_control("c2", "Ctrl2", "Desc2", ComplianceFramework.SOC2, "CC2")
        reg.update_control_status("c1", RegControlStatus.IMPLEMENTED)
        report = reg.generate_report()
        assert report.total_controls == 2
        assert report.overall_compliance_score > 0

    def test_dashboard_data(self):
        reg = ComplianceRegistry()
        reg.register_framework(ComplianceFramework.GDPR)
        reg.register_control("c1", "Ctrl", "Desc", ComplianceFramework.GDPR, "R1")
        dashboard = reg.get_dashboard_data()
        assert dashboard.total_controls >= 1
