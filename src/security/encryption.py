"""
Encryption utilities for LiuHao AI OS.

Provides symmetric encryption (AES-256-GCM), asymmetric encryption (RSA),
hashing (SHA-256, SHA-512, bcrypt, Argon2 interface), HMAC, key derivation,
key management, and secure random generation.

All cryptographic operations use the `cryptography` library which is already
a project dependency. Interfaces are designed to be swappable for future
backends (e.g. HSM, KMS).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Optional, Tuple, Union

from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.argon2 import Argon2id
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class HashAlgorithm(str, Enum):
    """Supported hashing algorithms."""

    SHA256 = "sha256"
    SHA512 = "sha512"
    BCRYPT = "bcrypt"
    ARGON2 = "argon2"


class KeyType(str, Enum):
    """Types of cryptographic keys."""

    AES_256 = "aes256"
    RSA_2048 = "rsa2048"
    RSA_4096 = "rsa4096"
    HMAC_SHA256 = "hmac_sha256"
    HMAC_SHA512 = "hmac_sha512"


class EncryptionMode(str, Enum):
    """Encryption operation modes."""

    ENCRYPT = "encrypt"
    DECRYPT = "decrypt"


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass
class EncryptedData:
    """Container for encrypted data with associated metadata."""

    ciphertext: bytes
    nonce: bytes
    algorithm: str = "AES-256-GCM"
    key_id: Optional[str] = None
    associated_data: Optional[bytes] = None

    def to_dict(self) -> dict:
        """Serialize to a dictionary with base64-encoded binary fields."""
        return {
            "ciphertext": base64.b64encode(self.ciphertext).decode("ascii"),
            "nonce": base64.b64encode(self.nonce).decode("ascii"),
            "algorithm": self.algorithm,
            "key_id": self.key_id,
            "associated_data": (
                base64.b64encode(self.associated_data).decode("ascii")
                if self.associated_data
                else None
            ),
        }

    @classmethod
    def from_dict(cls, data: dict) -> "EncryptedData":
        """Deserialize from a dictionary with base64-encoded binary fields."""
        return cls(
            ciphertext=base64.b64decode(data["ciphertext"]),
            nonce=base64.b64decode(data["nonce"]),
            algorithm=data.get("algorithm", "AES-256-GCM"),
            key_id=data.get("key_id"),
            associated_data=(
                base64.b64decode(data["associated_data"])
                if data.get("associated_data")
                else None
            ),
        )


@dataclass
class KeyMaterial:
    """Represents a cryptographic key with metadata."""

    key_id: str
    key_type: KeyType
    key_bytes: bytes
    created_at: float = field(default_factory=lambda: __import__("time").time())
    rotated_at: Optional[float] = None
    is_active: bool = True
    metadata: dict = field(default_factory=dict)

    def export_public(self) -> Optional[bytes]:
        """Export the public portion of an asymmetric key, if applicable."""
        if self.key_type in (KeyType.RSA_2048, KeyType.RSA_4096):
            # key_bytes holds the private key; derive public key
            private_key = serialization.load_pem_private_key(self.key_bytes, password=None)
            public_key = private_key.public_key()  # type: ignore[union-attr]
            return public_key.public_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PublicFormat.SubjectPublicKeyInfo,
            )
        return None


# ---------------------------------------------------------------------------
# Symmetric Encryption (AES-256-GCM)
# ---------------------------------------------------------------------------


class AesGcmCipher:
    """AES-256-GCM symmetric encryption with authenticated data support.

    Uses 96-bit (12-byte) nonces as recommended by NIST for GCM.
    Each encryption produces a unique nonce; authentication tag is appended
    to the ciphertext by the AESGCM primitive.
    """

    KEY_SIZE = 32  # 256 bits
    NONCE_SIZE = 12  # 96 bits, NIST recommended for GCM

    def __init__(self, key: bytes, key_id: Optional[str] = None) -> None:
        """Initialize cipher with a 256-bit key.

        Args:
            key: 32-byte AES key.
            key_id: Optional identifier for key management/rotation.
        """
        if len(key) != self.KEY_SIZE:
            raise ValueError(f"Key must be {self.KEY_SIZE} bytes for AES-256")
        self._key = key
        self._aesgcm = AESGCM(key)
        self.key_id = key_id or "default"

    def encrypt(
        self,
        plaintext: bytes,
        associated_data: Optional[bytes] = None,
        nonce: Optional[bytes] = None,
    ) -> EncryptedData:
        """Encrypt plaintext with optional associated authenticated data.

        Args:
            plaintext: Data to encrypt.
            associated_data: Data authenticated but not encrypted (AAD).
            nonce: Optional nonce; if None, a random 12-byte nonce is used.

        Returns:
            EncryptedData containing ciphertext, nonce, and metadata.
        """
        if nonce is None:
            nonce = os.urandom(self.NONCE_SIZE)
        if len(nonce) != self.NONCE_SIZE:
            raise ValueError(f"Nonce must be {self.NONCE_SIZE} bytes")

        ciphertext = self._aesgcm.encrypt(nonce, plaintext, associated_data)
        return EncryptedData(
            ciphertext=ciphertext,
            nonce=nonce,
            algorithm="AES-256-GCM",
            key_id=self.key_id,
            associated_data=associated_data,
        )

    def decrypt(self, encrypted: EncryptedData) -> bytes:
        """Decrypt an EncryptedData object.

        Args:
            encrypted: The encrypted data container.

        Returns:
            Decrypted plaintext bytes.

        Raises:
            InvalidTag: If authentication fails (tampered ciphertext or AAD).
        """
        return self._aesgcm.decrypt(
            encrypted.nonce,
            encrypted.ciphertext,
            encrypted.associated_data,
        )


# ---------------------------------------------------------------------------
# Asymmetric Encryption (RSA)
# ---------------------------------------------------------------------------


class RsaCipher:
    """RSA asymmetric encryption with OAEP padding.

    Supports RSA-2048 and RSA-4096 key sizes. Uses SHA-256 for OAEP hashing.
    Suitable for encrypting small data (e.g. symmetric keys, credentials).
    For large data, use hybrid encryption (RSA + AES).
    """

    def __init__(
        self,
        public_key_pem: Optional[bytes] = None,
        private_key_pem: Optional[bytes] = None,
        key_size: int = 2048,
    ) -> None:
        """Initialize RSA cipher.

        Args:
            public_key_pem: PEM-encoded public key for encryption.
            private_key_pem: PEM-encoded private key for decryption.
            key_size: Key size in bits for generation (default 2048).
        """
        self._public_key = None
        self._private_key = None
        self._key_size = key_size

        if public_key_pem:
            self._public_key = serialization.load_pem_public_key(public_key_pem)
        if private_key_pem:
            self._private_key = serialization.load_pem_private_key(
                private_key_pem, password=None
            )

    @classmethod
    def generate(cls, key_size: int = 2048) -> "RsaCipher":
        """Generate a new RSA key pair.

        Args:
            key_size: Key size in bits (2048 or 4096).

        Returns:
            RsaCipher instance with generated key pair.
        """
        if key_size not in (2048, 4096):
            raise ValueError("Key size must be 2048 or 4096 bits")
        private_key = rsa.generate_private_key(
            public_exponent=65537,
            key_size=key_size,
        )
        public_pem = private_key.public_key().public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        private_pem = private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
        cipher = cls(public_pem, private_pem, key_size)
        return cipher

    @property
    def key_size(self) -> int:
        """Return the RSA key size in bits."""
        if self._private_key:
            return self._private_key.key_size  # type: ignore[return-value]
        if self._public_key:
            return self._public_key.key_size  # type: ignore[return-value]
        return self._key_size

    def encrypt(self, plaintext: bytes) -> bytes:
        """Encrypt data using the public key with OAEP padding.

        Args:
            plaintext: Data to encrypt (must be smaller than key size - padding).

        Returns:
            Ciphertext bytes.

        Raises:
            ValueError: If no public key is available.
        """
        if self._public_key is None:
            raise ValueError("Public key required for encryption")
        return self._public_key.encrypt(  # type: ignore[call-arg]
            plaintext,
            padding.OAEP(
                mgf=padding.MGF1(algorithm=hashes.SHA256()),
                algorithm=hashes.SHA256(),
                label=None,
            ),
        )

    def decrypt(self, ciphertext: bytes) -> bytes:
        """Decrypt data using the private key with OAEP padding.

        Args:
            ciphertext: Encrypted data.

        Returns:
            Plaintext bytes.

        Raises:
            ValueError: If no private key is available.
        """
        if self._private_key is None:
            raise ValueError("Private key required for decryption")
        return self._private_key.decrypt(  # type: ignore[call-arg]
            ciphertext,
            padding.OAEP(
                mgf=padding.MGF1(algorithm=hashes.SHA256()),
                algorithm=hashes.SHA256(),
                label=None,
            ),
        )

    def export_public_key(self) -> bytes:
        """Export the public key in PEM format."""
        if self._public_key is None:
            raise ValueError("No public key available")
        return self._public_key.public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )

    def export_private_key(self) -> bytes:
        """Export the private key in PEM format (unencrypted)."""
        if self._private_key is None:
            raise ValueError("No private key available")
        return self._private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )


# ---------------------------------------------------------------------------
# Hashing
# ---------------------------------------------------------------------------


class HashUtils:
    """Hashing utilities: SHA-256, SHA-512, bcrypt interface, Argon2 interface.

    For password hashing, use the dedicated PasswordHasher class which provides
    salted, slow hashes suitable for credentials.
    """

    @staticmethod
    def sha256(data: bytes, hex_digest: bool = True) -> Union[str, bytes]:
        """Compute SHA-256 hash.

        Args:
            data: Input bytes.
            hex_digest: If True, return hex string; otherwise return bytes.

        Returns:
            Hex digest string or raw bytes.
        """
        h = hashlib.sha256(data)
        return h.hexdigest() if hex_digest else h.digest()

    @staticmethod
    def sha512(data: bytes, hex_digest: bool = True) -> Union[str, bytes]:
        """Compute SHA-512 hash."""
        h = hashlib.sha512(data)
        return h.hexdigest() if hex_digest else h.digest()

    @staticmethod
    def bcrypt_hash(password: str, rounds: int = 12) -> str:
        """Hash a password using bcrypt.

        This is an interface method that uses the bcrypt library when available.
        Falls back to PBKDF2-SHA256 if bcrypt is not installed.

        Args:
            password: Plaintext password.
            rounds: Cost factor (log2 of iterations).

        Returns:
            Bcrypt hash string.
        """
        try:
            import bcrypt  # type: ignore

            salt = bcrypt.gensalt(rounds=rounds)
            return bcrypt.hashpw(password.encode("utf-8"), salt).decode("utf-8")
        except ImportError:
            # Fallback: PBKDF2 with bcrypt-like prefix for compatibility
            salt = os.urandom(16)
            iterations = 2**rounds
            dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
            return f"$pbkdf2-sha256${rounds}${base64.b64encode(salt).decode()}${base64.b64encode(dk).decode()}"

    @staticmethod
    def bcrypt_verify(password: str, hashed: str) -> bool:
        """Verify a bcrypt password hash."""
        try:
            import bcrypt  # type: ignore

            return bcrypt.checkpw(password.encode("utf-8"), hashed.encode("utf-8"))
        except ImportError:
            # Fallback for PBKDF2-based hashes
            if hashed.startswith("$pbkdf2-sha256$"):
                parts = hashed.split("$")
                rounds = int(parts[2])
                salt = base64.b64decode(parts[3])
                expected = base64.b64decode(parts[4])
                iterations = 2**rounds
                dk = hashlib.pbkdf2_hmac(
                    "sha256", password.encode("utf-8"), salt, iterations
                )
                return hmac.compare_digest(dk, expected)
            raise ValueError("Unsupported hash format")

    @staticmethod
    def argon2_hash(password: str, time_cost: int = 3, memory_cost: int = 65536) -> str:
        """Hash a password using Argon2id (interface).

        Requires the argon2-cffi package. Falls back to bcrypt if unavailable.

        Args:
            password: Plaintext password.
            time_cost: Number of iterations.
            memory_cost: Memory usage in KiB.

        Returns:
            Argon2 hash string.
        """
        try:
            from argon2 import PasswordHasher  # type: ignore
            from argon2.profiles import RFC_9106_LOW_MEMORY  # type: ignore

            ph = PasswordHasher(
                time_cost=time_cost,
                memory_cost=memory_cost,
            )
            return ph.hash(password)
        except ImportError:
            return HashUtils.bcrypt_hash(password)

    @staticmethod
    def argon2_verify(password: str, hashed: str) -> bool:
        """Verify an Argon2 password hash."""
        try:
            from argon2 import PasswordHasher  # type: ignore

            ph = PasswordHasher()
            ph.verify(hashed, password)
            return True
        except ImportError:
            return HashUtils.bcrypt_verify(password, hashed)
        except Exception:
            return False


class PasswordHasher:
    """Secure password hashing with configurable algorithm.

    Defaults to Argon2id if available, falls back to bcrypt, then PBKDF2.
    Uses constant-time comparison for verification.
    """

    def __init__(self, algorithm: HashAlgorithm = HashAlgorithm.ARGON2) -> None:
        """Initialize password hasher.

        Args:
            algorithm: Preferred hashing algorithm.
        """
        self.algorithm = algorithm

    def hash(self, password: str) -> str:
        """Hash a password.

        Args:
            password: Plaintext password.

        Returns:
            Hash string with algorithm prefix.
        """
        if self.algorithm == HashAlgorithm.ARGON2:
            return HashUtils.argon2_hash(password)
        if self.algorithm == HashAlgorithm.BCRYPT:
            return HashUtils.bcrypt_hash(password)
        # PBKDF2-SHA256 fallback
        salt = os.urandom(16)
        iterations = 100_000
        dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
        return (
            f"$pbkdf2-sha256${iterations}$"
            f"{base64.b64encode(salt).decode()}${base64.b64encode(dk).decode()}"
        )

    def verify(self, password: str, hashed: str) -> bool:
        """Verify a password against a hash.

        Args:
            password: Plaintext password.
            hashed: Hash string.

        Returns:
            True if password matches.
        """
        if hashed.startswith("$argon2"):
            return HashUtils.argon2_verify(password, hashed)
        if hashed.startswith("$2b$") or hashed.startswith("$2a$") or hashed.startswith("$2y$"):
            return HashUtils.bcrypt_verify(password, hashed)
        if hashed.startswith("$pbkdf2-sha256$"):
            parts = hashed.split("$")
            iterations = int(parts[2])
            salt = base64.b64decode(parts[3])
            expected = base64.b64decode(parts[4])
            dk = hashlib.pbkdf2_hmac(
                "sha256", password.encode("utf-8"), salt, iterations
            )
            return hmac.compare_digest(dk, expected)
        return False

    def needs_rehash(self, hashed: str) -> bool:
        """Check if a hash needs to be rehashed with current parameters."""
        if self.algorithm == HashAlgorithm.ARGON2 and not hashed.startswith("$argon2"):
            return True
        if self.algorithm == HashAlgorithm.BCRYPT and not (
            hashed.startswith("$2b$")
            or hashed.startswith("$2a$")
            or hashed.startswith("$2y$")
        ):
            return True
        return False


# ---------------------------------------------------------------------------
# HMAC
# ---------------------------------------------------------------------------


class HmacUtils:
    """HMAC utilities for message authentication."""

    @staticmethod
    def sha256(key: bytes, message: bytes, hex_digest: bool = True) -> Union[str, bytes]:
        """Compute HMAC-SHA256.

        Args:
            key: Secret key.
            message: Message to authenticate.
            hex_digest: Return hex string if True, bytes otherwise.

        Returns:
            HMAC digest.
        """
        h = hmac.new(key, message, hashlib.sha256)
        return h.hexdigest() if hex_digest else h.digest()

    @staticmethod
    def sha512(key: bytes, message: bytes, hex_digest: bool = True) -> Union[str, bytes]:
        """Compute HMAC-SHA512."""
        h = hmac.new(key, message, hashlib.sha512)
        return h.hexdigest() if hex_digest else h.digest()

    @staticmethod
    def verify_sha256(key: bytes, message: bytes, expected: Union[str, bytes]) -> bool:
        """Verify HMAC-SHA256 using constant-time comparison."""
        actual = HmacUtils.sha256(key, message, hex_digest=isinstance(expected, str))
        if isinstance(expected, str):
            return hmac.compare_digest(actual, expected)
        return hmac.compare_digest(actual, expected)  # type: ignore[arg-type]

    @staticmethod
    def verify_sha512(key: bytes, message: bytes, expected: Union[str, bytes]) -> bool:
        """Verify HMAC-SHA512 using constant-time comparison."""
        actual = HmacUtils.sha512(key, message, hex_digest=isinstance(expected, str))
        if isinstance(expected, str):
            return hmac.compare_digest(actual, expected)
        return hmac.compare_digest(actual, expected)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Key Derivation
# ---------------------------------------------------------------------------


class KeyDerivation:
    """Key derivation functions: PBKDF2 and HKDF."""

    @staticmethod
    def pbkdf2(
        password: bytes,
        salt: bytes,
        length: int = 32,
        iterations: int = 100_000,
        hash_algorithm: str = "sha256",
    ) -> bytes:
        """Derive a key using PBKDF2-HMAC.

        Args:
            password: Input password/passphrase.
            salt: Cryptographic salt.
            length: Desired key length in bytes.
            iterations: Number of iterations.
            hash_algorithm: Hash algorithm name (sha256, sha512).

        Returns:
            Derived key bytes.
        """
        kdf = PBKDF2HMAC(
            algorithm=getattr(hashes, hash_algorithm.upper())(),
            length=length,
            salt=salt,
            iterations=iterations,
        )
        return kdf.derive(password)

    @staticmethod
    def hkdf(
        input_key_material: bytes,
        length: int = 32,
        salt: Optional[bytes] = None,
        info: Optional[bytes] = None,
        hash_algorithm: str = "sha256",
    ) -> bytes:
        """Derive a key using HKDF (HMAC-based Extract-and-Expand KDF).

        Suitable for deriving multiple keys from a single master key or
        converting non-uniform randomness into uniform key material.

        Args:
            input_key_material: Source key material.
            length: Desired output length in bytes.
            salt: Optional salt (recommended for security).
            info: Optional context/application-specific info.
            hash_algorithm: Hash algorithm name.

        Returns:
            Derived key bytes.
        """
        hkdf = HKDF(
            algorithm=getattr(hashes, hash_algorithm.upper())(),
            length=length,
            salt=salt,
            info=info,
        )
        return hkdf.derive(input_key_material)


# ---------------------------------------------------------------------------
# Key Management Interface
# ---------------------------------------------------------------------------


class KeyManager(ABC):
    """Abstract base class for key management backends.

    Implementations may include in-memory, file-based, vault, or KMS backends.
    """

    @abstractmethod
    def generate_key(self, key_type: KeyType, key_id: Optional[str] = None) -> KeyMaterial:
        """Generate a new cryptographic key."""
        ...

    @abstractmethod
    def get_key(self, key_id: str) -> Optional[KeyMaterial]:
        """Retrieve a key by ID."""
        ...

    @abstractmethod
    def rotate_key(self, key_id: str) -> KeyMaterial:
        """Rotate a key, generating a new version."""
        ...

    @abstractmethod
    def import_key(self, key_id: str, key_material: KeyMaterial) -> None:
        """Import an externally generated key."""
        ...

    @abstractmethod
    def export_key(self, key_id: str) -> Optional[KeyMaterial]:
        """Export key material (may be restricted by policy)."""
        ...

    @abstractmethod
    def list_keys(self) -> list[str]:
        """List all key IDs."""
        ...

    @abstractmethod
    def deactivate_key(self, key_id: str) -> None:
        """Deactivate a key (no longer used for encryption)."""
        ...


class InMemoryKeyManager(KeyManager):
    """In-memory key manager for testing and development.

    NOT suitable for production use — keys are stored in plaintext in memory.
    """

    def __init__(self) -> None:
        self._keys: dict[str, KeyMaterial] = {}

    def generate_key(self, key_type: KeyType, key_id: Optional[str] = None) -> KeyMaterial:
        import time

        kid = key_id or f"key_{secrets.token_hex(8)}"
        if key_type == KeyType.AES_256:
            key_bytes = os.urandom(32)
        elif key_type in (KeyType.RSA_2048, KeyType.RSA_4096):
            size = 2048 if key_type == KeyType.RSA_2048 else 4096
            rsa_cipher = RsaCipher.generate(size)
            key_bytes = rsa_cipher.export_private_key()
        elif key_type == KeyType.HMAC_SHA256:
            key_bytes = os.urandom(32)
        elif key_type == KeyType.HMAC_SHA512:
            key_bytes = os.urandom(64)
        else:
            raise ValueError(f"Unsupported key type: {key_type}")

        material = KeyMaterial(
            key_id=kid,
            key_type=key_type,
            key_bytes=key_bytes,
            created_at=time.time(),
            is_active=True,
        )
        self._keys[kid] = material
        return material

    def get_key(self, key_id: str) -> Optional[KeyMaterial]:
        key = self._keys.get(key_id)
        if key and key.is_active:
            return key
        return None

    def rotate_key(self, key_id: str) -> KeyMaterial:
        import time

        old_key = self._keys.get(key_id)
        if old_key is None:
            raise KeyError(f"Key not found: {key_id}")
        # Generate new key with same type but new ID
        new_id = f"{key_id}_v{int(time.time())}"
        new_key = self.generate_key(old_key.key_type, new_id)
        # Deactivate old key
        old_key.is_active = False
        old_key.rotated_at = time.time()
        return new_key

    def import_key(self, key_id: str, key_material: KeyMaterial) -> None:
        self._keys[key_id] = key_material

    def export_key(self, key_id: str) -> Optional[KeyMaterial]:
        return self._keys.get(key_id)

    def list_keys(self) -> list[str]:
        return list(self._keys.keys())

    def deactivate_key(self, key_id: str) -> None:
        if key_id in self._keys:
            self._keys[key_id].is_active = False


# ---------------------------------------------------------------------------
# Secure Random Generation
# ---------------------------------------------------------------------------


class SecureRandom:
    """Cryptographically secure random number generation."""

    @staticmethod
    def bytes(n: int) -> bytes:
        """Generate n cryptographically secure random bytes."""
        return os.urandom(n)

    @staticmethod
    def token_hex(nbytes: int = 32) -> str:
        """Generate a hex-encoded random token."""
        return secrets.token_hex(nbytes)

    @staticmethod
    def token_urlsafe(nbytes: int = 32) -> str:
        """Generate a URL-safe random token."""
        return secrets.token_urlsafe(nbytes)

    @staticmethod
    def randbelow(n: int) -> int:
        """Generate a random integer in [0, n)."""
        return secrets.randbelow(n)

    @staticmethod
    def choice(seq: list) -> object:
        """Choose a random element from a non-empty sequence."""
        return secrets.choice(seq)

    @staticmethod
    def compare_digest(a: Union[str, bytes], b: Union[str, bytes]) -> bool:
        """Constant-time string comparison."""
        return hmac.compare_digest(a, b)


# ---------------------------------------------------------------------------
# Data-at-Rest Encryption Interface
# ---------------------------------------------------------------------------


class DataAtRestEncryption:
    """Interface for encrypting data at rest.

    Uses envelope encryption: a data encryption key (DEK) encrypts the data,
    and the DEK is encrypted with a key encryption key (KEK) managed by
    the KeyManager.
    """

    def __init__(self, key_manager: KeyManager, kek_id: str) -> None:
        """Initialize with a key manager and KEK identifier.

        Args:
            key_manager: Key manager backend.
            kek_id: ID of the key encryption key (must be AES-256).
        """
        self._key_manager = key_manager
        self._kek_id = kek_id

    def _get_kek_cipher(self) -> AesGcmCipher:
        kek = self._key_manager.get_key(self._kek_id)
        if kek is None:
            raise ValueError(f"KEK not found: {self._kek_id}")
        return AesGcmCipher(kek.key_bytes, key_id=self._kek_id)

    def encrypt(self, plaintext: bytes, associated_data: Optional[bytes] = None) -> dict:
        """Encrypt data at rest using envelope encryption.

        Args:
            plaintext: Data to encrypt.
            associated_data: Optional authenticated metadata.

        Returns:
            Dictionary with encrypted data, encrypted DEK, and metadata.
        """
        # Generate a random DEK
        dek_bytes = os.urandom(32)
        dek = AesGcmCipher(dek_bytes, key_id="data_dek")

        # Encrypt the data with DEK
        encrypted_data = dek.encrypt(plaintext, associated_data)

        # Encrypt the DEK with KEK
        kek_cipher = self._get_kek_cipher()
        encrypted_dek = kek_cipher.encrypt(dek_bytes)

        return {
            "encrypted_data": encrypted_data.to_dict(),
            "encrypted_dek": encrypted_dek.to_dict(),
            "kek_id": self._kek_id,
            "algorithm": "AES-256-GCM-envelope",
        }

    def decrypt(self, envelope: dict) -> bytes:
        """Decrypt envelope-encrypted data.

        Args:
            envelope: Dictionary from encrypt().

        Returns:
            Decrypted plaintext bytes.
        """
        # Decrypt the DEK with KEK
        kek_cipher = self._get_kek_cipher()
        encrypted_dek = EncryptedData.from_dict(envelope["encrypted_dek"])
        dek_bytes = kek_cipher.decrypt(encrypted_dek)

        # Decrypt the data with DEK
        dek = AesGcmCipher(dek_bytes)
        encrypted_data = EncryptedData.from_dict(envelope["encrypted_data"])
        return dek.decrypt(encrypted_data)


# ---------------------------------------------------------------------------
# Legacy EncryptionManager (compatibility layer)
# ---------------------------------------------------------------------------
#
# This class predates the cipher/manager split above. ``jwt_handler``,
# ``vault_crypto`` and the gateway health probe still depend on it
# (notably the ``master_key`` property and the module-level
# ``get_encryption_manager`` singleton), so it is kept intact here.


@dataclass
class EncryptionConfig:
    """Encryption configuration"""

    algorithm: str = "fernet"  # fernet, aes-gcm, chacha20-poly1305
    key_derivation: str = "pbkdf2"  # pbkdf2, argon2
    pbkdf2_iterations: int = 600000
    argon2_time_cost: int = 3
    argon2_memory_cost: int = 65536
    argon2_parallelism: int = 4


class EncryptionManager:
    """
    Unified encryption manager supporting multiple algorithms.

    Features:
    - Fernet (AES-128-CBC + HMAC) - simple, safe default
    - AES-GCM - authenticated encryption with associated data
    - Key derivation from passwords
    - Envelope encryption for key wrapping
    - Key rotation support
    """

    def __init__(
        self,
        master_key: Optional[bytes] = None,
        config: Optional[EncryptionConfig] = None,
        key_file: Optional[str] = None,
    ):
        """
        Initialize encryption manager.

        Args:
            master_key: 32-byte master key (generated if not provided)
            config: EncryptionConfig instance
            key_file: Path to master key file
        """
        self.config = config or EncryptionConfig()

        # Load or generate master key.
        if master_key:
            self._master_key = master_key
        elif key_file:
            self._master_key = self._load_key_file(key_file)
        else:
            default_key_file = Path(
                os.environ.get(
                    "LIUHAO_MASTER_KEY_FILE",
                    str(Path.home() / ".liuhao" / "master.key"),
                )
            )
            if default_key_file.exists():
                # Real persistence: load the previously generated master key so
                # it is NOT regenerated on every boot.
                self._master_key = self._load_key_file(str(default_key_file))
            elif os.environ.get("LIUHAO_SECRET_DEV_EPHEMERAL"):
                # Explicit dev-only ephemeral mode: the key is kept in memory
                # ONLY and is intentionally never persisted. Production must
                # never reach this branch.
                self._master_key = self.generate_key()
            else:
                # Production: generate a real master key and PERSIST it (chmod
                # 600) so it survives restarts instead of being regenerated.
                self._master_key = self.generate_key()
                self._save_key_file(str(default_key_file), self._master_key)

        # Initialize Fernet with master key
        self._fernet = Fernet(base64.urlsafe_b64encode(self._master_key[:32]))

        # AES-GCM instance
        self._aesgcm = AESGCM(self._master_key[:32])

    def _load_key_file(self, path: str) -> bytes:
        """Load master key from file"""
        key_data = Path(path).read_bytes()
        # Support both raw 32-byte and base64 encoded
        if len(key_data) == 32:
            return key_data
        elif len(key_data) == 44:  # base64 encoded 32 bytes
            return base64.urlsafe_b64decode(key_data)
        else:
            # Try to derive from password
            return self.derive_key_from_password(key_data.decode())

    def _save_key_file(self, path: str, key: bytes) -> None:
        """Save master key to file (chmod 0600)."""
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_bytes(base64.urlsafe_b64encode(key))
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass

    def save(self, path: Optional[str] = None) -> None:
        """Persist the master key (chmod 0600).

        Useful after rotation so the new key survives restart instead of being
        silently lost. Falls back to ``LIUHAO_MASTER_KEY_FILE`` / the default
        location when ``path`` is not given.
        """
        if path is None:
            path = os.environ.get(
                "LIUHAO_MASTER_KEY_FILE",
                str(Path.home() / ".liuhao" / "master.key"),
            )
        self._save_key_file(path, self._master_key)

    @property
    def master_key(self) -> bytes:
        """Read-only raw master key (32 bytes)."""
        return self._master_key

    @staticmethod
    def generate_key(length: int = 32) -> bytes:
        """Generate cryptographically secure random key"""
        return secrets.token_bytes(length)

    @staticmethod
    def generate_key_base64(length: int = 32) -> str:
        """Generate key as base64 string"""
        return base64.urlsafe_b64encode(EncryptionManager.generate_key(length)).decode()

    def derive_key_from_password(
        self,
        password: Union[str, bytes],
        salt: Optional[bytes] = None,
        length: int = 32,
    ) -> bytes:
        """Derive encryption key from password using PBKDF2 or Argon2"""
        if isinstance(password, str):
            password = password.encode()

        if salt is None:
            salt = secrets.token_bytes(16)

        if self.config.key_derivation == "argon2":
            kdf = Argon2id(
                salt=salt,
                length=length,
                time_cost=self.config.argon2_time_cost,
                memory_cost=self.config.argon2_memory_cost,
                parallelism=self.config.argon2_parallelism,
            )
        else:
            kdf = PBKDF2HMAC(
                algorithm=hashes.SHA256(),
                length=length,
                salt=salt,
                iterations=self.config.pbkdf2_iterations,
            )

        return kdf.derive(password)

    # ==================== Fernet (High-level) ====================

    def encrypt(self, data: Union[str, bytes], metadata: Optional[dict] = None) -> str:
        """
        Encrypt data using Fernet (AES-128-CBC + HMAC).

        Returns base64-encoded token containing:
        - Version byte
        - Timestamp
        - IV
        - Ciphertext
        - HMAC
        - Optional metadata (JSON)
        """
        if isinstance(data, str):
            data = data.encode()

        token = self._fernet.encrypt(data)

        if metadata:
            # Wrap with metadata
            import json
            wrapper = {
                "v": 1,
                "data": token.decode(),
                "meta": metadata,
            }
            return base64.urlsafe_b64encode(json.dumps(wrapper).encode()).decode()

        return token.decode()

    def decrypt(self, token: str, return_metadata: bool = False) -> Union[bytes, Tuple[bytes, dict]]:
        """
        Decrypt Fernet token.

        Returns bytes, or (bytes, metadata) if return_metadata=True
        """
        # Check if wrapped with metadata
        try:
            import json
            decoded = base64.urlsafe_b64decode(token.encode())
            wrapper = json.loads(decoded)
            if isinstance(wrapper, dict) and "data" in wrapper:
                token = wrapper["data"]
                metadata = wrapper.get("meta", {})
                data = self._fernet.decrypt(token.encode())
                if return_metadata:
                    return data, metadata
                return data
        except Exception:
            pass

        # Standard Fernet token
        data = self._fernet.decrypt(token.encode())
        if return_metadata:
            return data, {}
        return data

    def encrypt_file(self, input_path: str, output_path: str) -> None:
        """Encrypt file"""
        data = Path(input_path).read_bytes()
        encrypted = self.encrypt(data)
        Path(output_path).write_text(encrypted)

    def decrypt_file(self, input_path: str, output_path: str) -> None:
        """Decrypt file"""
        token = Path(input_path).read_text()
        decrypted = self.decrypt(token)
        Path(output_path).write_bytes(decrypted)

    # ==================== AES-GCM (Low-level, with AAD) ====================

    def encrypt_aesgcm(
        self,
        data: Union[str, bytes],
        associated_data: Optional[bytes] = None,
        nonce: Optional[bytes] = None,
    ) -> dict:
        """
        Encrypt using AES-GCM with optional associated data.

        Returns dict with: nonce, ciphertext, tag (combined in ciphertext for AES-GCM)
        """
        if isinstance(data, str):
            data = data.encode()

        if nonce is None:
            nonce = secrets.token_bytes(12)  # 96-bit nonce for GCM

        ciphertext = self._aesgcm.encrypt(nonce, data, associated_data)

        return {
            "nonce": base64.urlsafe_b64encode(nonce).decode(),
            "ciphertext": base64.urlsafe_b64encode(ciphertext).decode(),
            "algorithm": "aes-gcm",
        }

    def decrypt_aesgcm(
        self,
        nonce: Union[str, bytes],
        ciphertext: Union[str, bytes],
        associated_data: Optional[bytes] = None,
    ) -> bytes:
        """Decrypt AES-GCM"""
        if isinstance(nonce, str):
            nonce = base64.urlsafe_b64decode(nonce)
        if isinstance(ciphertext, str):
            ciphertext = base64.urlsafe_b64decode(ciphertext)

        return self._aesgcm.decrypt(nonce, ciphertext, associated_data)

    # ==================== Envelope Encryption ====================

    def wrap_key(self, data_key: bytes, context: Optional[dict] = None) -> dict:
        """
        Encrypt a data encryption key (DEK) with master key (KEK).
        This is envelope encryption - the DEK encrypts data, KEK encrypts DEK.
        """
        # Use Fernet for key wrapping
        wrapped = self._fernet.encrypt(data_key)

        return {
            "wrapped_key": base64.urlsafe_b64encode(wrapped).decode(),
            "algorithm": "fernet",
            "context": context or {},
        }

    def unwrap_key(self, wrapped_key: Union[str, bytes]) -> bytes:
        """Decrypt a wrapped data encryption key"""
        if isinstance(wrapped_key, str):
            wrapped_key = base64.urlsafe_b64decode(wrapped_key)
        return self._fernet.decrypt(wrapped_key)

    # ==================== Key Management ====================

    def rotate_master_key(self, new_master_key: bytes) -> "EncryptionManager":
        """
        Create new EncryptionManager with rotated master key.
        Old keys can still be decrypted with old manager.
        """
        return EncryptionManager(master_key=new_master_key, config=self.config)

    def re_encrypt(self, token: str, new_manager: "EncryptionManager") -> str:
        """Re-encrypt token with new master key"""
        data = self.decrypt(token)
        return new_manager.encrypt(data)

    # ==================== Utilities ====================

    def hash_data(self, data: Union[str, bytes], algorithm: str = "sha256") -> str:
        """Hash data with specified algorithm"""
        if isinstance(data, str):
            data = data.encode()

        if algorithm == "sha256":
            return hashlib.sha256(data).hexdigest()
        elif algorithm == "sha512":
            return hashlib.sha512(data).hexdigest()
        elif algorithm == "blake2b":
            return hashlib.blake2b(data).hexdigest()
        else:
            raise ValueError(f"Unsupported algorithm: {algorithm}")

    def hmac_sign(self, data: Union[str, bytes], key: Optional[bytes] = None) -> str:
        """Generate HMAC signature"""
        if isinstance(data, str):
            data = data.encode()
        if key is None:
            key = self._master_key[:32]
        return hmac.new(key, data, hashlib.sha256).hexdigest()

    def hmac_verify(self, data: Union[str, bytes], signature: str, key: Optional[bytes] = None) -> bool:
        """Verify HMAC signature (constant-time)"""
        expected = self.hmac_sign(data, key)
        return hmac.compare_digest(expected, signature)


# Module-level convenience functions (use default manager)
_default_manager: Optional[EncryptionManager] = None


def get_encryption_manager() -> EncryptionManager:
    """Get default encryption manager"""
    global _default_manager
    if _default_manager is None:
        _default_manager = EncryptionManager()
    return _default_manager


def encrypt_data(data: Union[str, bytes], metadata: Optional[dict] = None) -> str:
    """Encrypt data using default manager"""
    return get_encryption_manager().encrypt(data, metadata)


def decrypt_data(token: str, return_metadata: bool = False) -> Union[bytes, Tuple[bytes, dict]]:
    """Decrypt data using default manager"""
    return get_encryption_manager().decrypt(token, return_metadata)


def generate_key(length: int = 32) -> bytes:
    """Generate random key"""
    return EncryptionManager.generate_key(length)
