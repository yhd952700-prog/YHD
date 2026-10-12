"""
Classic Ciphers Collection Plugin for LiuHao AI OS

Provides multiple classic ciphers: Atbash, Rail Fence,
Columnar Transposition, Playfair, Hill cipher interface,
and One-Time Pad.

Capabilities:
- atbash: Atbash cipher encrypt/decrypt
- rail_fence: Rail fence cipher
- columnar_transposition: Columnar transposition cipher
- playfair: Playfair cipher
- hill_cipher: Hill cipher (interface)
- one_time_pad: One-time pad
"""

from typing import Dict, Any, Optional, List, Tuple
from dataclasses import dataclass, field

from ..plugin_base import (
    BasePlugin,
    PluginMetadata,
    PluginStatus,
    PluginExecutionError,
)


@dataclass
class CipherResult:
    """Result of a cipher operation."""
    input: str
    output: str
    algorithm: str
    key: str
    operation: str  # encrypt, decrypt


class EncryptionClassicPlugin(BasePlugin):
    """Classic ciphers collection plugin.

    Provides implementations of historical/classic ciphers:
    Atbash, Rail Fence, Columnar Transposition, Playfair,
    Hill cipher interface, and One-Time Pad.

    Example:
        plugin = EncryptionClassicPlugin()
        plugin.initialize()
        plugin.activate()

        result = plugin.atbash("Hello World")
        print(result.output)  # "Svool Dliow"
    """

    # Playfair 5x5 grid alphabet (I/J combined)
    PLAYFAIR_ALPHABET = "ABCDEFGHIKLMNOPQRSTUVWXYZ"

    def __init__(self) -> None:
        """Initialize the classic ciphers plugin."""
        metadata = PluginMetadata(
            name="encryption-classic",
            version="1.0.0",
            description="Collection of classic ciphers: Atbash, Rail Fence, Columnar, Playfair, Hill, One-Time Pad",
            author="LiuHao AI OS Team",
            categories=["cryptography", "cipher", "historical"],
            keywords=["cipher", "atbash", "rail fence", "playfair", "hill", "one-time pad", "transposition"],
            requires_internet=False,
            dependencies=[],
        )
        super().__init__(metadata)

    # ==================== Lifecycle Methods ====================

    def initialize(self, config: Optional[Dict[str, Any]] = None) -> bool:
        """Initialize the classic ciphers plugin."""
        try:
            self._set_status(PluginStatus.INITIALIZED)
            return True
        except Exception as e:
            self._set_error(f"Initialization failed: {e}")
            return False

    def activate(self) -> bool:
        """Activate the classic ciphers plugin."""
        try:
            self._set_status(PluginStatus.ACTIVE)
            return True
        except Exception as e:
            self._set_error(f"Activation failed: {e}")
            return False

    def deactivate(self) -> bool:
        """Deactivate the classic ciphers plugin."""
        try:
            self._set_status(PluginStatus.INACTIVE)
            return True
        except Exception as e:
            self._set_error(str(e))
            return False

    def shutdown(self) -> bool:
        """Shut down the classic ciphers plugin."""
        try:
            self._set_status(PluginStatus.SHUTDOWN)
            return True
        except Exception as e:
            self._set_error(str(e))
            return False

    def get_capabilities(self) -> List[str]:
        """Return the list of capabilities."""
        return [
            "atbash",
            "rail_fence",
            "columnar_transposition",
            "playfair",
            "hill_cipher",
            "one_time_pad",
            "list_ciphers",
        ]

    # ==================== Atbash Cipher ====================

    def atbash(self, text: str) -> CipherResult:
        """Apply Atbash cipher (encrypt and decrypt are the same).

        Atbash replaces each letter with its reverse in the alphabet:
        A <-> Z, B <-> Y, C <-> X, etc.

        Args:
            text: Text to transform.

        Returns:
            CipherResult with transformed text.
        """
        self._ensure_active()
        self._increment_call_count()

        result: List[str] = []
        for ch in text:
            if ch.isalpha():
                base = ord('A') if ch.isupper() else ord('a')
                # Reverse position in alphabet
                pos = ord(ch.upper()) - ord('A')
                reversed_pos = 25 - pos
                result_chr = chr(base + reversed_pos)
                result.append(result_chr)
            else:
                result.append(ch)

        return CipherResult(
            input=text,
            output=''.join(result),
            algorithm="atbash",
            key="",
            operation="encrypt/decrypt",
        )

    # ==================== Rail Fence Cipher ====================

    def rail_fence_encrypt(self, text: str, rails: int = 3) -> CipherResult:
        """Encrypt text using Rail Fence cipher.

        Args:
            text: Plaintext to encrypt.
            rails: Number of rails (fences).

        Returns:
            CipherResult with encrypted text.
        """
        self._ensure_active()
        self._increment_call_count()

        if rails < 2:
            raise ValueError("Number of rails must be at least 2")

        # Create fence pattern
        fence = [[] for _ in range(rails)]
        rail = 0
        direction = 1  # 1 = down, -1 = up

        for ch in text:
            fence[rail].append(ch)
            rail += direction
            if rail == 0 or rail == rails - 1:
                direction *= -1

        output = ''.join(''.join(rail) for rail in fence)

        return CipherResult(
            input=text,
            output=output,
            algorithm="rail_fence",
            key=str(rails),
            operation="encrypt",
        )

    def rail_fence_decrypt(self, ciphertext: str, rails: int = 3) -> CipherResult:
        """Decrypt Rail Fence cipher text.

        Args:
            ciphertext: Encrypted text.
            rails: Number of rails.

        Returns:
            CipherResult with decrypted text.
        """
        self._ensure_active()
        self._increment_call_count()

        if rails < 2:
            raise ValueError("Number of rails must be at least 2")

        n = len(ciphertext)

        # Calculate pattern of rail positions
        pattern: List[int] = []
        rail = 0
        direction = 1
        for i in range(n):
            pattern.append(rail)
            rail += direction
            if rail == 0 or rail == rails - 1:
                direction *= -1

        # Count characters per rail
        rail_counts = [0] * rails
        for r in pattern:
            rail_counts[r] += 1

        # Distribute ciphertext into rails
        rail_texts: List[str] = []
        pos = 0
        for count in rail_counts:
            rail_texts.append(ciphertext[pos:pos + count])
            pos += count

        # Read back in zig-zag order
        result: List[str] = []
        rail_indices = [0] * rails
        for r in pattern:
            result.append(rail_texts[r][rail_indices[r]])
            rail_indices[r] += 1

        return CipherResult(
            input=ciphertext,
            output=''.join(result),
            algorithm="rail_fence",
            key=str(rails),
            operation="decrypt",
        )

    # ==================== Columnar Transposition ====================

    def columnar_encrypt(self, text: str, key: str) -> CipherResult:
        """Encrypt text using Columnar Transposition cipher.

        Writes text in rows under the key, then reads columns
        in alphabetical order of key letters.

        Args:
            text: Plaintext to encrypt.
            key: Keyword for column ordering.

        Returns:
            CipherResult with encrypted text.
        """
        self._ensure_active()
        self._increment_call_count()

        if not key:
            raise ValueError("Key must not be empty")

        key = key.upper()
        key_len = len(key)

        # Pad text if needed
        padded = text
        padding_needed = (key_len - len(text) % key_len) % key_len
        if padding_needed > 0:
            padded += 'X' * padding_needed

        # Determine column order
        order = sorted(range(key_len), key=lambda i: key[i])

        # Write into columns
        columns: List[str] = [''] * key_len
        for i, ch in enumerate(padded):
            col = i % key_len
            columns[col] += ch

        # Read in sorted order
        output = ''.join(columns[i] for i in order)

        return CipherResult(
            input=text,
            output=output,
            algorithm="columnar_transposition",
            key=key,
            operation="encrypt",
        )

    def columnar_decrypt(self, ciphertext: str, key: str) -> CipherResult:
        """Decrypt Columnar Transposition cipher.

        Args:
            ciphertext: Encrypted text.
            key: Keyword for column ordering.

        Returns:
            CipherResult with decrypted text.
        """
        self._ensure_active()
        self._increment_call_count()

        if not key:
            raise ValueError("Key must not be empty")

        key = key.upper()
        key_len = len(key)
        n = len(ciphertext)

        if n % key_len != 0:
            raise ValueError("Ciphertext length must be divisible by key length")

        rows = n // key_len

        # Determine column order
        order = sorted(range(key_len), key=lambda i: key[i])

        # Split ciphertext into columns
        col_len = rows
        columns: Dict[int, str] = {}
        pos = 0
        for col_idx in order:
            columns[col_idx] = ciphertext[pos:pos + col_len]
            pos += col_len

        # Read row by row
        result: List[str] = []
        for row in range(rows):
            for col in range(key_len):
                result.append(columns[col][row])

        # Remove padding
        output = ''.join(result).rstrip('X')

        return CipherResult(
            input=ciphertext,
            output=output,
            algorithm="columnar_transposition",
            key=key,
            operation="decrypt",
        )

    # ==================== Playfair Cipher ====================

    def playfair_encrypt(self, text: str, key: str) -> CipherResult:
        """Encrypt text using Playfair cipher.

        Uses a 5x5 grid (I and J combined) based on keyword.

        Args:
            text: Plaintext to encrypt.
            key: Keyword to build the Playfair square.

        Returns:
            CipherResult with encrypted text.
        """
        self._ensure_active()
        self._increment_call_count()

        if not key:
            raise ValueError("Key must not be empty")

        # Build the Playfair square
        square = self._build_playfair_square(key)
        pos_map = {ch: (r, c) for r, row in enumerate(square) for c, ch in enumerate(row)}

        # Prepare text: uppercase, I->J, insert X between duplicates, pad if odd
        prepared = self._prepare_playfair_text(text)

        # Process digraphs
        result: List[str] = []
        for i in range(0, len(prepared), 2):
            a = prepared[i]
            b = prepared[i + 1] if i + 1 < len(prepared) else 'X'

            r1, c1 = pos_map[a]
            r2, c2 = pos_map[b]

            if r1 == r2:
                # Same row: shift right
                result.append(square[r1][(c1 + 1) % 5])
                result.append(square[r2][(c2 + 1) % 5])
            elif c1 == c2:
                # Same column: shift down
                result.append(square[(r1 + 1) % 5][c1])
                result.append(square[(r2 + 1) % 5][c2])
            else:
                # Rectangle: swap columns
                result.append(square[r1][c2])
                result.append(square[r2][c1])

        return CipherResult(
            input=text,
            output=''.join(result),
            algorithm="playfair",
            key=key,
            operation="encrypt",
        )

    def playfair_decrypt(self, ciphertext: str, key: str) -> CipherResult:
        """Decrypt Playfair cipher.

        Args:
            ciphertext: Encrypted text.
            key: Keyword for the Playfair square.

        Returns:
            CipherResult with decrypted text.
        """
        self._ensure_active()
        self._increment_call_count()

        if not key:
            raise ValueError("Key must not be empty")

        # Build the Playfair square
        square = self._build_playfair_square(key)
        pos_map = {ch: (r, c) for r, row in enumerate(square) for c, ch in enumerate(row)}

        # Prepare text: uppercase, only letters
        prepared = ''.join(c.upper() for c in ciphertext if c.isalpha())
        prepared = prepared.replace('J', 'I')

        # Ensure even length
        if len(prepared) % 2 != 0:
            prepared += 'X'

        # Process digraphs
        result: List[str] = []
        for i in range(0, len(prepared), 2):
            a = prepared[i]
            b = prepared[i + 1]

            r1, c1 = pos_map[a]
            r2, c2 = pos_map[b]

            if r1 == r2:
                # Same row: shift left
                result.append(square[r1][(c1 - 1) % 5])
                result.append(square[r2][(c2 - 1) % 5])
            elif c1 == c2:
                # Same column: shift up
                result.append(square[(r1 - 1) % 5][c1])
                result.append(square[(r2 - 1) % 5][c2])
            else:
                # Rectangle: swap columns
                result.append(square[r1][c2])
                result.append(square[r2][c1])

        return CipherResult(
            input=ciphertext,
            output=''.join(result),
            algorithm="playfair",
            key=key,
            operation="decrypt",
        )

    def _build_playfair_square(self, key: str) -> List[List[str]]:
        """Build a 5x5 Playfair square from a keyword."""
        key = key.upper().replace('J', 'I')
        seen = set()
        chars: List[str] = []

        for ch in key:
            if ch.isalpha() and ch not in seen:
                seen.add(ch)
                chars.append(ch)

        for ch in self.PLAYFAIR_ALPHABET:
            if ch not in seen:
                seen.add(ch)
                chars.append(ch)

        # Build 5x5 grid
        square = []
        for i in range(5):
            square.append(chars[i * 5:(i + 1) * 5])

        return square

    def _prepare_playfair_text(self, text: str) -> str:
        """Prepare text for Playfair cipher."""
        # Keep only letters, uppercase
        result = ''.join(c.upper() for c in text if c.isalpha())
        # Replace J with I
        result = result.replace('J', 'I')
        # Insert X between duplicate letters in a digraph
        processed: List[str] = []
        i = 0
        while i < len(result):
            processed.append(result[i])
            if i + 1 < len(result) and result[i] == result[i + 1]:
                processed.append('X')
            i += 1
        # Pad if odd length
        if len(processed) % 2 != 0:
            processed.append('X')

        return ''.join(processed)

    def playfair_square(self, key: str) -> List[List[str]]:
        """Get the Playfair square for a given key.

        Args:
            key: Keyword.

        Returns:
            5x5 Playfair square as list of lists.
        """
        self._ensure_active()
        self._increment_call_count()
        return self._build_playfair_square(key)

    # ==================== Hill Cipher (Interface) ====================

    def hill_cipher_encrypt(self, text: str, key_matrix: List[List[int]]) -> CipherResult:
        """Encrypt text using Hill cipher.

        Uses a key matrix to transform letter vectors.

        Args:
            text: Plaintext to encrypt.
            key_matrix: Encryption key matrix (2x2 or 3x3).

        Returns:
            CipherResult with encrypted text.
        """
        self._ensure_active()
        self._increment_call_count()

        n = len(key_matrix)
        if n < 2:
            raise ValueError("Key matrix must be at least 2x2")

        # Verify matrix is square
        for row in key_matrix:
            if len(row) != n:
                raise ValueError("Key matrix must be square")

        # Prepare text: uppercase letters only
        letters = [c.upper() for c in text if c.isalpha()]
        # Pad with X
        while len(letters) % n != 0:
            letters.append('X')

        result: List[str] = []

        for i in range(0, len(letters), n):
            block = [ord(letters[i + j]) - ord('A') for j in range(n)]
            encrypted = [0] * n
            for row in range(n):
                for col in range(n):
                    encrypted[row] += key_matrix[row][col] * block[col]
                encrypted[row] %= 26
            for val in encrypted:
                result.append(chr(val + ord('A')))

        return CipherResult(
            input=text,
            output=''.join(result),
            algorithm="hill",
            key=str(key_matrix),
            operation="encrypt",
        )

    def hill_cipher_validate_matrix(self, matrix: List[List[int]]) -> Tuple[bool, str]:
        """Validate a Hill cipher key matrix.

        Checks if the matrix is invertible modulo 26.

        Args:
            matrix: Key matrix.

        Returns:
            Tuple of (is_valid, message).
        """
        self._ensure_active()
        self._increment_call_count()

        n = len(matrix)
        if n < 2:
            return False, "Matrix must be at least 2x2"

        for row in matrix:
            if len(row) != n:
                return False, "Matrix must be square"

        # For 2x2: check determinant is coprime with 26
        if n == 2:
            det = matrix[0][0] * matrix[1][1] - matrix[0][1] * matrix[1][0]
            det = det % 26
            if det == 0:
                return False, "Determinant is zero, matrix not invertible"
            # Check gcd with 26
            from math import gcd
            if gcd(det, 26) != 1:
                return False, f"Determinant {det} not coprime with 26"
            return True, f"Valid 2x2 matrix, determinant = {det}"

        return True, f"{n}x{n} matrix (invertibility not checked for n>2)"

    # ==================== One-Time Pad ====================

    def one_time_pad_encrypt(self, text: str, key: str) -> CipherResult:
        """Encrypt using One-Time Pad.

        XOR-based one-time pad for letters only.
        Key must be at least as long as text.

        Args:
            text: Plaintext to encrypt.
            key: One-time pad key (letters only).

        Returns:
            CipherResult with encrypted text.
        """
        self._ensure_active()
        self._increment_call_count()

        if len(key) < len([c for c in text if c.isalpha()]):
            raise ValueError("Key must be at least as long as text letters")

        result: List[str] = []
        key_idx = 0

        for ch in text:
            if ch.isalpha():
                base = ord('A') if ch.isupper() else ord('a')
                key_char = key[key_idx].upper()
                shift = ord(key_char) - ord('A')

                val = (ord(ch.upper()) - ord('A') + shift) % 26
                result_chr = chr(base + val)
                result.append(result_chr)
                key_idx += 1
            else:
                result.append(ch)

        return CipherResult(
            input=text,
            output=''.join(result),
            algorithm="one_time_pad",
            key=key[:key_idx],
            operation="encrypt",
        )

    def one_time_pad_decrypt(self, ciphertext: str, key: str) -> CipherResult:
        """Decrypt One-Time Pad cipher.

        Args:
            ciphertext: Encrypted text.
            key: One-time pad key.

        Returns:
            CipherResult with decrypted text.
        """
        self._ensure_active()
        self._increment_call_count()

        result: List[str] = []
        key_idx = 0

        for ch in ciphertext:
            if ch.isalpha():
                base = ord('A') if ch.isupper() else ord('a')
                key_char = key[key_idx].upper()
                shift = ord(key_char) - ord('A')

                val = (ord(ch.upper()) - ord('A') - shift) % 26
                result_chr = chr(base + val)
                result.append(result_chr)
                key_idx += 1
            else:
                result.append(ch)

        return CipherResult(
            input=ciphertext,
            output=''.join(result),
            algorithm="one_time_pad",
            key=key[:key_idx],
            operation="decrypt",
        )

    def one_time_pad_generate_key(self, length: int) -> str:
        """Generate a random one-time pad key.

        Args:
            length: Length of key in characters.

        Returns:
            Random key string.
        """
        self._ensure_active()
        self._increment_call_count()

        import secrets
        return ''.join(secrets.choice('ABCDEFGHIJKLMNOPQRSTUVWXYZ') for _ in range(length))

    # ==================== List Ciphers ====================

    def list_ciphers(self) -> List[Dict[str, Any]]:
        """List all available ciphers.

        Returns:
            List of cipher info dicts.
        """
        self._ensure_active()
        self._increment_call_count()

        return [
            {
                "name": "Atbash",
                "type": "substitution",
                "key_required": False,
                "description": "Replaces each letter with its reverse in the alphabet",
                "strength": "Very weak (single substitution)",
            },
            {
                "name": "Rail Fence",
                "type": "transposition",
                "key_required": True,
                "key_type": "integer (number of rails)",
                "description": "Writes text in zig-zag pattern across rails",
                "strength": "Weak (simple transposition)",
            },
            {
                "name": "Columnar Transposition",
                "type": "transposition",
                "key_required": True,
                "key_type": "keyword",
                "description": "Rearranges columns based on keyword order",
                "strength": "Moderate",
            },
            {
                "name": "Playfair",
                "type": "digraph substitution",
                "key_required": True,
                "key_type": "keyword",
                "description": "Encrypts pairs of letters using 5x5 grid",
                "strength": "Moderate (better than monoalphabetic)",
            },
            {
                "name": "Hill Cipher",
                "type": "polygraphic substitution",
                "key_required": True,
                "key_type": "matrix",
                "description": "Uses linear algebra on letter blocks",
                "strength": "Moderate to strong",
            },
            {
                "name": "One-Time Pad",
                "type": "substitution",
                "key_required": True,
                "key_type": "random key (same length as text)",
                "description": "Unbreakable if key is truly random and used once",
                "strength": "Perfect secrecy (if used correctly)",
            },
        ]

    # ==================== Statistics ====================

    def get_stats(self) -> Dict[str, Any]:
        """Get plugin statistics."""
        return {
            "ciphers": 6,
            "cipher_types": [
                "substitution",
                "transposition",
                "digraph substitution",
                "polygraphic substitution",
            ],
            "playfair_square_size": "5x5",
            "hill_matrix_sizes": ["2x2", "3x3+"],
            "one_time_pad_perfect_secrecy": True,
        }
