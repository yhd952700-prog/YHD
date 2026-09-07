"""
Encryption Utilities for LiuHao AI OS

Provides:
- Symmetric encryption (AES-GCM via Fernet)
- Key derivation (PBKDF2, Argon2)
- Secure key generation
- Envelope encryption for key wrapping
"""

import base64
import secrets
import hashlib
import hmac
from typing import Optional, Tuple, Union
from dataclasses import dataclass
from pathlib import Path

try:
    from cryptography.fernet import Fernet
    from cryptography.hazmat.primitives import hashes, serialization  # noqa: F401
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
    from cryptography.hazmat.primitives.kdf.argon2 import Argon2id
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from cryptography.hazmat.primitives.asymmetric import rsa, padding
    CRYPTO_AVAILABLE = True
except ImportError:
    CRYPTO_AVAILABLE = False
    Fernet = None
    PBKDF2HMAC = None
    Argon2id = None
    AESGCM = None
    rsa = None
    padding = None


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
        if not CRYPTO_AVAILABLE:
            raise RuntimeError(
                "cryptography library required. Install: pip install cryptography"
            )

        self.config = config or EncryptionConfig()

        # Load or generate master key
        if master_key:
            self._master_key = master_key
        elif key_file:
            self._master_key = self._load_key_file(key_file)
        else:
            # Try to load from default location
            default_key_file = Path.home() / ".liuhao" / "master.key"
            if default_key_file.exists():
                self._master_key = self._load_key_file(str(default_key_file))
            else:
                self._master_key = self.generate_key()
                # Optionally save to default location
                # self._save_key_file(str(default_key_file), self._master_key)

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
        """Save master key to file"""
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_bytes(base64.urlsafe_b64encode(key))

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

        if self.config.key_derivation == "argon2" and CRYPTO_AVAILABLE:
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
