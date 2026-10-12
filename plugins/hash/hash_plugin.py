"""
Hash & Checksum Plugin for LiuHao AI OS

Provides MD5, SHA-1, SHA-256, SHA-512 hashing, file hashing,
HMAC generation, hash verification, and salted hashing.

Capabilities:
- md5: Compute MD5 hash
- sha1: Compute SHA-1 hash
- sha256: Compute SHA-256 hash
- sha512: Compute SHA-512 hash
- hash_file: Compute hash of a file
- hmac: Generate HMAC
- verify: Verify hash against expected value
- compare: Compare multiple hashes
- salted_hash: Generate salted hash
- hash_all: Compute all hash algorithms at once
- checksum: Generate file checksum
"""

import hashlib
import hmac
import os
import secrets
from typing import Dict, Any, Optional, List, Tuple, Union
from dataclasses import dataclass, field

from ..plugin_base import (
    BasePlugin,
    PluginMetadata,
    PluginStatus,
    PluginExecutionError,
)


@dataclass
class HashResult:
    """Result of a hash operation."""
    algorithm: str
    input_type: str  # text, bytes, file
    input_size: int
    hex_digest: str
    digest: bytes
    bit_length: int


@dataclass
class HmacResult:
    """Result of an HMAC operation."""
    algorithm: str
    hex_digest: str
    digest: bytes
    key_used: str  # 'provided' or 'generated'


@dataclass
class VerificationResult:
    """Result of hash verification."""
    is_match: bool
    algorithm: str
    expected: str
    actual: str
    time_taken_ms: float


@dataclass
class MultiHashResult:
    """Result of multi-algorithm hashing."""
    md5: str = ""
    sha1: str = ""
    sha256: str = ""
    sha512: str = ""
    sha224: str = ""
    sha384: str = ""
    input_size: int = 0


class HashPlugin(BasePlugin):
    """Hash and checksum plugin.

    Provides multiple hash algorithms, HMAC, file hashing,
    verification, and salted hashing.

    Example:
        plugin = HashPlugin()
        plugin.initialize()
        plugin.activate()

        result = plugin.sha256("Hello, World!")
        print(result.hex_digest)
    """

    # Available algorithms and their bit lengths
    ALGORITHMS = {
        "md5": 128,
        "sha1": 160,
        "sha224": 224,
        "sha256": 256,
        "sha384": 384,
        "sha512": 512,
        "sha3_256": 256,
        "sha3_512": 512,
    }

    # File read chunk size
    CHUNK_SIZE = 8192 * 1024  # 8MB

    def __init__(self) -> None:
        """Initialize the hash plugin."""
        metadata = PluginMetadata(
            name="hash",
            version="1.0.0",
            description="Hash functions, checksums, HMAC, and file hashing",
            author="LiuHao AI OS Team",
            categories=["security", "utility", "cryptography"],
            keywords=["hash", "md5", "sha256", "sha512", "hmac", "checksum"],
            requires_internet=False,
            dependencies=[],
        )
        super().__init__(metadata)

        self._default_algorithm: str = "sha256"
        self._default_salt_length: int = 16

    # ==================== Lifecycle Methods ====================

    def initialize(self, config: Optional[Dict[str, Any]] = None) -> bool:
        """Initialize the hash plugin."""
        try:
            if config:
                self._default_algorithm = config.get("default_algorithm", "sha256")
                self._default_salt_length = config.get("default_salt_length", 16)

            self._set_status(PluginStatus.INITIALIZED)
            return True
        except Exception as e:
            self._set_error(f"Initialization failed: {e}")
            return False

    def activate(self) -> bool:
        """Activate the hash plugin."""
        try:
            self._set_status(PluginStatus.ACTIVE)
            return True
        except Exception as e:
            self._set_error(f"Activation failed: {e}")
            return False

    def deactivate(self) -> bool:
        """Deactivate the hash plugin."""
        try:
            self._set_status(PluginStatus.INACTIVE)
            return True
        except Exception as e:
            self._set_error(str(e))
            return False

    def shutdown(self) -> bool:
        """Shut down the hash plugin."""
        try:
            self._set_status(PluginStatus.SHUTDOWN)
            return True
        except Exception as e:
            self._set_error(str(e))
            return False

    def get_capabilities(self) -> List[str]:
        """Return the list of capabilities."""
        return [
            "md5",
            "sha1",
            "sha256",
            "sha512",
            "hash",
            "hash_file",
            "hmac",
            "verify",
            "compare",
            "salted_hash",
            "hash_all",
            "checksum",
            "generate_salt",
            "pbkdf2",
        ]

    # ==================== Hash Functions ====================

    def md5(self, text: str, *, encoding: str = "utf-8") -> HashResult:
        """Compute MD5 hash of a string.

        Args:
            text: Input text.
            encoding: Text encoding.

        Returns:
            HashResult with MD5 digest.
        """
        self._ensure_active()
        self._increment_call_count()
        return self._hash_text(text, "md5", encoding)

    def sha1(self, text: str, *, encoding: str = "utf-8") -> HashResult:
        """Compute SHA-1 hash of a string.

        Args:
            text: Input text.
            encoding: Text encoding.

        Returns:
            HashResult with SHA-1 digest.
        """
        self._ensure_active()
        self._increment_call_count()
        return self._hash_text(text, "sha1", encoding)

    def sha256(self, text: str, *, encoding: str = "utf-8") -> HashResult:
        """Compute SHA-256 hash of a string.

        Args:
            text: Input text.
            encoding: Text encoding.

        Returns:
            HashResult with SHA-256 digest.
        """
        self._ensure_active()
        self._increment_call_count()
        return self._hash_text(text, "sha256", encoding)

    def sha512(self, text: str, *, encoding: str = "utf-8") -> HashResult:
        """Compute SHA-512 hash of a string.

        Args:
            text: Input text.
            encoding: Text encoding.

        Returns:
            HashResult with SHA-512 digest.
        """
        self._ensure_active()
        self._increment_call_count()
        return self._hash_text(text, "sha512", encoding)

    def hash(
        self,
        data: Union[str, bytes],
        algorithm: Optional[str] = None,
        *,
        encoding: str = "utf-8",
    ) -> HashResult:
        """Compute hash using specified algorithm.

        Args:
            data: Input text or bytes.
            algorithm: Hash algorithm name.
            encoding: Text encoding if data is string.

        Returns:
            HashResult with digest.
        """
        self._ensure_active()
        self._increment_call_count()

        algorithm = algorithm or self._default_algorithm

        if isinstance(data, str):
            return self._hash_text(data, algorithm, encoding)
        else:
            return self._hash_bytes(data, algorithm)

    def _hash_text(self, text: str, algorithm: str, encoding: str) -> HashResult:
        """Hash a text string."""
        data = text.encode(encoding)
        return self._hash_bytes(data, algorithm)

    def _hash_bytes(self, data: bytes, algorithm: str) -> HashResult:
        """Hash raw bytes."""
        algo = algorithm.lower().replace("-", "_")

        if algo not in self.ALGORITHMS:
            raise ValueError(f"Unsupported algorithm: {algorithm}. Available: {list(self.ALGORITHMS.keys())}")

        try:
            h = hashlib.new(algo.replace("_", "-") if algo.startswith("sha3") else algo)
            h.update(data)

            return HashResult(
                algorithm=algo,
                input_type="bytes",
                input_size=len(data),
                hex_digest=h.hexdigest(),
                digest=h.digest(),
                bit_length=self.ALGORITHMS.get(algo, 0),
            )
        except ValueError as e:
            raise ValueError(f"Hash error: {e}")

    # ==================== File Hashing ====================

    def hash_file(
        self,
        filepath: str,
        algorithm: Optional[str] = None,
    ) -> HashResult:
        """Compute hash of a file.

        Args:
            filepath: Path to the file.
            algorithm: Hash algorithm.

        Returns:
            HashResult with file hash.
        """
        self._ensure_active()
        self._increment_call_count()

        algorithm = algorithm or self._default_algorithm
        algo = algorithm.lower().replace("-", "_")

        if algo not in self.ALGORITHMS:
            raise ValueError(f"Unsupported algorithm: {algorithm}")

        if not os.path.exists(filepath):
            raise FileNotFoundError(f"File not found: {filepath}")

        try:
            h = hashlib.new(algo.replace("_", "-") if algo.startswith("sha3") else algo)
            file_size = 0

            with open(filepath, "rb") as f:
                while True:
                    chunk = f.read(self.CHUNK_SIZE)
                    if not chunk:
                        break
                    file_size += len(chunk)
                    h.update(chunk)

            return HashResult(
                algorithm=algo,
                input_type="file",
                input_size=file_size,
                hex_digest=h.hexdigest(),
                digest=h.digest(),
                bit_length=self.ALGORITHMS.get(algo, 0),
            )
        except PermissionError as e:
            raise PluginExecutionError(f"Permission denied: {e}")

    # ==================== HMAC ====================

    def hmac(
        self,
        message: str,
        key: Optional[str] = None,
        algorithm: Optional[str] = None,
        *,
        encoding: str = "utf-8",
        key_bytes: Optional[bytes] = None,
    ) -> HmacResult:
        """Generate an HMAC (Hash-based Message Authentication Code).

        Args:
            message: The message to authenticate.
            key: Secret key (string). Generated if not provided.
            algorithm: Hash algorithm for HMAC.
            encoding: Text encoding.
            key_bytes: Secret key as bytes (overrides key if provided).

        Returns:
            HmacResult with the HMAC digest.
        """
        self._ensure_active()
        self._increment_call_count()

        algorithm = algorithm or self._default_algorithm
        algo = algorithm.lower().replace("-", "_")

        if key_bytes is not None:
            key_data = key_bytes
            key_used = "provided"
        elif key is not None:
            key_data = key.encode(encoding)
            key_used = "provided"
        else:
            key_data = secrets.token_bytes(32)
            key_used = "generated"

        try:
            h = hmac.new(key_data, message.encode(encoding), algo.replace("_", "-") if algo.startswith("sha3") else algo)

            return HmacResult(
                algorithm=algo,
                hex_digest=h.hexdigest(),
                digest=h.digest(),
                key_used=key_used,
            )
        except Exception as e:
            raise PluginExecutionError(f"HMAC error: {e}")

    def hmac_verify(
        self,
        message: str,
        key: str,
        expected_hmac: str,
        algorithm: Optional[str] = None,
        *,
        encoding: str = "utf-8",
    ) -> bool:
        """Verify an HMAC against an expected value.

        Uses constant-time comparison to prevent timing attacks.

        Args:
            message: The message.
            key: The secret key.
            expected_hmac: Expected HMAC hex string.
            algorithm: Hash algorithm.
            encoding: Text encoding.

        Returns:
            True if HMAC matches.
        """
        self._ensure_active()
        self._increment_call_count()

        result = self.hmac(message, key=key, algorithm=algorithm, encoding=encoding)
        return hmac.compare_digest(result.hex_digest, expected_hmac.lower())

    # ==================== Verification ====================

    def verify(
        self,
        data: Union[str, bytes],
        expected_hash: str,
        algorithm: Optional[str] = None,
        *,
        encoding: str = "utf-8",
    ) -> VerificationResult:
        """Verify data against an expected hash.

        Args:
            data: Input text or bytes.
            expected_hash: Expected hash hex string.
            algorithm: Hash algorithm (auto-detected if None).
            encoding: Text encoding.

        Returns:
            VerificationResult with match status.
        """
        self._ensure_active()
        self._increment_call_count()

        import time
        start = time.time()

        # Auto-detect algorithm from hash length
        if algorithm is None:
            algorithm = self._detect_algorithm(expected_hash)

        if isinstance(data, str):
            result = self._hash_text(data, algorithm, encoding)
        else:
            result = self._hash_bytes(data, algorithm)

        is_match = hmac.compare_digest(result.hex_digest, expected_hash.lower())
        elapsed = (time.time() - start) * 1000

        return VerificationResult(
            is_match=is_match,
            algorithm=algorithm,
            expected=expected_hash.lower(),
            actual=result.hex_digest,
            time_taken_ms=round(elapsed, 4),
        )

    def verify_file(
        self,
        filepath: str,
        expected_hash: str,
        algorithm: Optional[str] = None,
    ) -> VerificationResult:
        """Verify a file against an expected hash.

        Args:
            filepath: Path to the file.
            expected_hash: Expected hash hex string.
            algorithm: Hash algorithm (auto-detected if None).

        Returns:
            VerificationResult with match status.
        """
        self._ensure_active()
        self._increment_call_count()

        import time
        start = time.time()

        if algorithm is None:
            algorithm = self._detect_algorithm(expected_hash)

        result = self.hash_file(filepath, algorithm)
        is_match = hmac.compare_digest(result.hex_digest, expected_hash.lower())
        elapsed = (time.time() - start) * 1000

        return VerificationResult(
            is_match=is_match,
            algorithm=algorithm,
            expected=expected_hash.lower(),
            actual=result.hex_digest,
            time_taken_ms=round(elapsed, 4),
        )

    def _detect_algorithm(self, hash_str: str) -> str:
        """Detect hash algorithm from hex string length."""
        hex_len = len(hash_str.strip())

        length_to_algo = {
            32: "md5",
            40: "sha1",
            56: "sha224",
            64: "sha256",
            96: "sha384",
            128: "sha512",
        }

        return length_to_algo.get(hex_len, self._default_algorithm)

    # ==================== Salted Hashing ====================

    def salted_hash(
        self,
        text: str,
        algorithm: Optional[str] = None,
        *,
        salt: Optional[str] = None,
        salt_length: Optional[int] = None,
        iterations: int = 1,
        encoding: str = "utf-8",
    ) -> Dict[str, str]:
        """Generate a salted hash.

        Args:
            text: Input text.
            algorithm: Hash algorithm.
            salt: Salt string. Generated if not provided.
            salt_length: Length of generated salt in bytes.
            iterations: Number of hash iterations.
            encoding: Text encoding.

        Returns:
            Dict with 'hash', 'salt', 'algorithm', 'iterations'.
        """
        self._ensure_active()
        self._increment_call_count()

        algorithm = algorithm or self._default_algorithm
        algo = algorithm.lower().replace("-", "_")
        salt_len = salt_length or self._default_salt_length

        if salt is None:
            salt_bytes = secrets.token_bytes(salt_len)
            salt_hex = salt_bytes.hex()
        else:
            salt_bytes = salt.encode(encoding)
            salt_hex = salt

        data = text.encode(encoding) + salt_bytes

        try:
            h = hashlib.new(algo.replace("_", "-") if algo.startswith("sha3") else algo)
            h.update(data)

            # Additional iterations
            for _ in range(iterations - 1):
                h = hashlib.new(algo.replace("_", "-") if algo.startswith("sha3") else algo)
                h.update(data)
                data = h.digest()

            return {
                "hash": h.hexdigest(),
                "salt": salt_hex,
                "algorithm": algo,
                "iterations": str(iterations),
            }
        except Exception as e:
            raise PluginExecutionError(f"Salted hash error: {e}")

    def pbkdf2(
        self,
        password: str,
        salt: Optional[str] = None,
        algorithm: str = "sha256",
        *,
        iterations: int = 100000,
        dk_length: int = 32,
        encoding: str = "utf-8",
    ) -> Dict[str, str]:
        """Generate a PBKDF2 hash (Password-Based Key Derivation Function 2).

        Args:
            password: The password to hash.
            salt: Salt string. Generated if not provided.
            algorithm: Hash algorithm (sha256, sha512).
            iterations: Number of iterations.
            dk_length: Desired key length in bytes.
            encoding: Text encoding.

        Returns:
            Dict with 'hash', 'salt', 'algorithm', 'iterations', 'dk_length'.
        """
        self._ensure_active()
        self._increment_call_count()

        if salt is None:
            salt_bytes = secrets.token_bytes(16)
            salt_hex = salt_bytes.hex()
        else:
            salt_bytes = salt.encode(encoding) if isinstance(salt, str) else salt
            salt_hex = salt_bytes.hex() if isinstance(salt_bytes, bytes) else salt

        password_bytes = password.encode(encoding)

        dk = hashlib.pbkdf2_hmac(
            algorithm,
            password_bytes,
            salt_bytes,
            iterations,
            dklen=dk_length,
        )

        return {
            "hash": dk.hex(),
            "salt": salt_hex,
            "algorithm": algorithm,
            "iterations": str(iterations),
            "dk_length": str(dk_length),
        }

    # ==================== Multi-Hash ====================

    def hash_all(self, data: Union[str, bytes], *, encoding: str = "utf-8") -> MultiHashResult:
        """Compute all common hash algorithms at once.

        Args:
            data: Input text or bytes.
            encoding: Text encoding if data is string.

        Returns:
            MultiHashResult with all hashes.
        """
        self._ensure_active()
        self._increment_call_count()

        if isinstance(data, str):
            data_bytes = data.encode(encoding)
        else:
            data_bytes = data

        result = MultiHashResult(input_size=len(data_bytes))

        for algo in ["md5", "sha1", "sha224", "sha256", "sha384", "sha512"]:
            try:
                h = hashlib.new(algo)
                h.update(data_bytes)
                setattr(result, algo, h.hexdigest())
            except Exception:
                pass

        return result

    def hash_file_all(self, filepath: str) -> MultiHashResult:
        """Compute all common hash algorithms for a file.

        Args:
            filepath: Path to the file.

        Returns:
            MultiHashResult with all hashes.
        """
        self._ensure_active()
        self._increment_call_count()

        if not os.path.exists(filepath):
            raise FileNotFoundError(f"File not found: {filepath}")

        hashers: Dict[str, Any] = {
            algo: hashlib.new(algo)
            for algo in ["md5", "sha1", "sha224", "sha256", "sha384", "sha512"]
        }

        file_size = 0
        with open(filepath, "rb") as f:
            while True:
                chunk = f.read(self.CHUNK_SIZE)
                if not chunk:
                    break
                file_size += len(chunk)
                for h in hashers.values():
                    h.update(chunk)

        result = MultiHashResult(input_size=file_size)
        for algo, h in hashers.items():
            setattr(result, algo, h.hexdigest())

        return result

    # ==================== Comparison ====================

    def compare(
        self,
        data1: Union[str, bytes],
        data2: Union[str, bytes],
        algorithm: Optional[str] = None,
        *,
        encoding: str = "utf-8",
    ) -> Dict[str, Any]:
        """Compare two pieces of data by their hashes.

        Args:
            data1: First data (text or bytes).
            data2: Second data (text or bytes).
            algorithm: Hash algorithm.
            encoding: Text encoding.

        Returns:
            Dict with 'are_equal', 'hash1', 'hash2', 'algorithm'.
        """
        self._ensure_active()
        self._increment_call_count()

        algorithm = algorithm or self._default_algorithm

        if isinstance(data1, str):
            r1 = self._hash_text(data1, algorithm, encoding)
        else:
            r1 = self._hash_bytes(data1, algorithm)

        if isinstance(data2, str):
            r2 = self._hash_text(data2, algorithm, encoding)
        else:
            r2 = self._hash_bytes(data2, algorithm)

        return {
            "are_equal": r1.hex_digest == r2.hex_digest,
            "hash1": r1.hex_digest,
            "hash2": r2.hex_digest,
            "algorithm": algorithm,
        }

    def compare_files(
        self,
        filepath1: str,
        filepath2: str,
        algorithm: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Compare two files by their hashes.

        Args:
            filepath1: Path to first file.
            filepath2: Path to second file.
            algorithm: Hash algorithm.

        Returns:
            Dict with 'are_equal', 'hash1', 'hash2', 'algorithm'.
        """
        self._ensure_active()
        self._increment_call_count()

        algorithm = algorithm or self._default_algorithm

        r1 = self.hash_file(filepath1, algorithm)
        r2 = self.hash_file(filepath2, algorithm)

        return {
            "are_equal": r1.hex_digest == r2.hex_digest,
            "hash1": r1.hex_digest,
            "hash2": r2.hex_digest,
            "algorithm": algorithm,
            "file1_size": r1.input_size,
            "file2_size": r2.input_size,
        }

    # ==================== Salt Generation ====================

    def generate_salt(self, length: Optional[int] = None) -> str:
        """Generate a cryptographically random salt.

        Args:
            length: Salt length in bytes.

        Returns:
            Hex-encoded salt string.
        """
        self._ensure_active()
        self._increment_call_count()

        length = length or self._default_salt_length
        return secrets.token_hex(length)

    # ==================== Checksum (CRC-like utilities) ====================

    def checksum(
        self,
        data: Union[str, bytes],
        algorithm: str = "crc32",
        *,
        encoding: str = "utf-8",
    ) -> Dict[str, Any]:
        """Generate a checksum for data.

        Args:
            data: Input text or bytes.
            algorithm: Checksum algorithm (crc32, adler32).
            encoding: Text encoding.

        Returns:
            Dict with 'checksum', 'algorithm', 'hex'.
        """
        self._ensure_active()
        self._increment_call_count()

        import zlib

        if isinstance(data, str):
            data_bytes = data.encode(encoding)
        else:
            data_bytes = data

        algo = algorithm.lower()

        if algo == "crc32":
            value = zlib.crc32(data_bytes) & 0xFFFFFFFF
        elif algo == "adler32":
            value = zlib.adler32(data_bytes) & 0xFFFFFFFF
        else:
            raise ValueError(f"Unsupported checksum algorithm: {algorithm}")

        return {
            "checksum": value,
            "algorithm": algo,
            "hex": format(value, "08x"),
            "input_size": len(data_bytes),
        }

    # ==================== Algorithm Info ====================

    def get_algorithms(self) -> List[Dict[str, Any]]:
        """Get list of available hash algorithms.

        Returns:
            List of algorithm info dicts.
        """
        algorithms = []
        for name, bits in sorted(self.ALGORITHMS.items()):
            algorithms.append({
                "name": name,
                "bit_length": bits,
                "hex_length": bits // 4,
                "byte_length": bits // 8,
            })
        return algorithms

    def get_algorithm_info(self, algorithm: str) -> Optional[Dict[str, Any]]:
        """Get information about a specific algorithm.

        Args:
            algorithm: Algorithm name.

        Returns:
            Algorithm info dict, or None if not found.
        """
        algo = algorithm.lower().replace("-", "_")
        if algo in self.ALGORITHMS:
            bits = self.ALGORITHMS[algo]
            return {
                "name": algo,
                "bit_length": bits,
                "hex_length": bits // 4,
                "byte_length": bits // 8,
            }
        return None

    # ==================== Statistics ====================

    def get_stats(self) -> Dict[str, Any]:
        """Get plugin statistics."""
        return {
            "default_algorithm": self._default_algorithm,
            "default_salt_length": self._default_salt_length,
            "supported_algorithms": len(self.ALGORITHMS),
            "algorithms": list(self.ALGORITHMS.keys()),
            "chunk_size": self.CHUNK_SIZE,
        }
