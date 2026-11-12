"""
Morse Code Plugin for LiuHao AI OS

Provides Morse code encoding/decoding, timing information,
validation, and audio generation interface.

Capabilities:
- encode: Text to Morse code
- decode: Morse code to text
- validate: Validate Morse code
- get_timing: Get timing info for dit/dah
- generate_audio_params: Parameters for audio generation
- get_code_table: Get the full Morse code table
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
class MorseTiming:
    """Morse code timing parameters."""
    dit_duration_ms: int  # Duration of a dot
    dah_duration_ms: int  # Duration of a dash (3x dit)
    intra_char_gap_ms: int  # Gap between elements of a char (1x dit)
    inter_char_gap_ms: int  # Gap between characters (3x dit)
    word_gap_ms: int  # Gap between words (7x dit)
    frequency_hz: int  # Tone frequency


@dataclass
class MorseResult:
    """Result of a Morse encode/decode operation."""
    input: str
    output: str
    success: bool
    errors: List[str]
    timing: Optional[MorseTiming] = None


class MorsePlugin(BasePlugin):
    """International Morse code encoder/decoder and utilities.

    Supports standard International Morse Code with ITU-R M.1677-1
    recommendations, including letters, numbers, punctuation, and
    procedural signals.

    Example:
        plugin = MorsePlugin()
        plugin.initialize()
        plugin.activate()

        morse = plugin.encode("SOS")
        print(morse.output)  # "... --- ..."

        text = plugin.decode("... --- ...")
        print(text.output)  # "SOS"
    """

    # International Morse Code (ITU-R M.1677-1)
    MORSE_CODE: Dict[str, str] = {
        # Letters
        'A': '.-', 'B': '-...', 'C': '-.-.', 'D': '-..', 'E': '.',
        'F': '..-.', 'G': '--.', 'H': '....', 'I': '..', 'J': '.---',
        'K': '-.-', 'L': '.-..', 'M': '--', 'N': '-.', 'O': '---',
        'P': '.--.', 'Q': '--.-', 'R': '.-.', 'S': '...', 'T': '-',
        'U': '..-', 'V': '...-', 'W': '.--', 'X': '-..-', 'Y': '-.--',
        'Z': '--..',
        # Numbers
        '0': '-----', '1': '.----', '2': '..---', '3': '...--',
        '4': '....-', '5': '.....', '6': '-....', '7': '--...',
        '8': '---..', '9': '----.',
        # Punctuation
        '.': '.-.-.-',  # Period
        ',': '--..--',  # Comma
        '?': '..--..',  # Question mark
        "'": '.----.',  # Apostrophe
        '!': '-.-.--',  # Exclamation mark
        '/': '-..-.',   # Slash
        '(': '-.--.',   # Open parenthesis
        ')': '-.--.-',  # Close parenthesis
        '&': '.-...',   # Ampersand
        ':': '---...',  # Colon
        ';': '-.-.-.',  # Semicolon
        '=': '-...-',   # Equals
        '+': '.-.-.',   # Plus
        '-': '-....-',  # Hyphen
        '_': '..--.-',  # Underscore
        '"': '.-..-.',  # Quotation marks
        '$': '...-..-', # Dollar sign
        '@': '.--.-.',  # At sign
        # Procedural signals
        '<SK>': '...-.-',  # End of contact
        '<BT>': '-...-',   # Break (pause)
        '<AR>': '.-.-.',   # End of message
        '<KA>': '-.-.-',   # Start of transmission
        '<KN>': '-.--.',   # Invite specific station
        '<VE>': '...-.',   # Verified
    }

    def __init__(self) -> None:
        """Initialize the Morse code plugin."""
        metadata = PluginMetadata(
            name="morse",
            version="1.0.0",
            description="Morse code encoding, decoding, validation, and timing",
            author="LiuHao AI OS Team",
            categories=["encoding", "utility", "communication"],
            keywords=["morse", "code", "encode", "decode", "radio", "telegraph"],
            requires_internet=False,
            dependencies=[],
        )
        super().__init__(metadata)

        self._default_timing = MorseTiming(
            dit_duration_ms=100,
            dah_duration_ms=300,
            intra_char_gap_ms=100,
            inter_char_gap_ms=300,
            word_gap_ms=700,
            frequency_hz=700,
        )
        self._reverse_map: Dict[str, str] = {}

    # ==================== Lifecycle Methods ====================

    def initialize(self, config: Optional[Dict[str, Any]] = None) -> bool:
        """Initialize the Morse code plugin."""
        try:
            if config:
                dit = config.get("dit_duration_ms", 100)
                freq = config.get("frequency_hz", 700)
                self._default_timing = MorseTiming(
                    dit_duration_ms=dit,
                    dah_duration_ms=dit * 3,
                    intra_char_gap_ms=dit,
                    inter_char_gap_ms=dit * 3,
                    word_gap_ms=dit * 7,
                    frequency_hz=freq,
                )

            # Build reverse map for decoding
            self._reverse_map = {v: k for k, v in self.MORSE_CODE.items()}

            self._set_status(PluginStatus.INITIALIZED)
            return True
        except Exception as e:
            self._set_error(f"Initialization failed: {e}")
            return False

    def activate(self) -> bool:
        """Activate the Morse code plugin."""
        try:
            self._set_status(PluginStatus.ACTIVE)
            return True
        except Exception as e:
            self._set_error(f"Activation failed: {e}")
            return False

    def deactivate(self) -> bool:
        """Deactivate the Morse code plugin."""
        try:
            self._set_status(PluginStatus.INACTIVE)
            return True
        except Exception as e:
            self._set_error(str(e))
            return False

    def shutdown(self) -> bool:
        """Shut down the Morse code plugin."""
        try:
            self._set_status(PluginStatus.SHUTDOWN)
            return True
        except Exception as e:
            self._set_error(str(e))
            return False

    def get_capabilities(self) -> List[str]:
        """Return the list of capabilities."""
        return [
            "encode",
            "decode",
            "validate",
            "get_timing",
            "generate_audio_params",
            "get_code_table",
            "calculate_duration",
        ]

    # ==================== Encoding ====================

    def encode(
        self,
        text: str,
        dit_symbol: str = ".",
        dah_symbol: str = "-",
        char_separator: str = " ",
        word_separator: str = "   ",
    ) -> MorseResult:
        """Encode text to Morse code.

        Args:
            text: The text to encode.
            dit_symbol: Symbol to use for dots.
            dah_symbol: Symbol to use for dashes.
            char_separator: Separator between characters.
            word_separator: Separator between words.

        Returns:
            MorseResult with encoded Morse code.
        """
        self._ensure_active()
        self._increment_call_count()

        errors: List[str] = []
        words = text.upper().split()
        morse_words: List[str] = []

        for word in words:
            chars: List[str] = []
            for ch in word:
                if ch in self.MORSE_CODE:
                    code = self.MORSE_CODE[ch]
                    # Replace symbols
                    code = code.replace('.', dit_symbol).replace('-', dah_symbol)
                    chars.append(code)
                else:
                    errors.append(f"Unsupported character: '{ch}'")
                    chars.append('?')

            morse_words.append(char_separator.join(chars))

        output = word_separator.join(morse_words)

        return MorseResult(
            input=text,
            output=output,
            success=len(errors) == 0,
            errors=errors,
            timing=self._default_timing,
        )

    # ==================== Decoding ====================

    def decode(
        self,
        morse: str,
        dit_symbol: str = ".",
        dah_symbol: str = "-",
        char_separator: str = " ",
        word_separator: str = "   ",
    ) -> MorseResult:
        """Decode Morse code to text.

        Args:
            morse: The Morse code to decode.
            dit_symbol: Symbol used for dots.
            dah_symbol: Symbol used for dashes.
            char_separator: Separator between characters.
            word_separator: Separator between words.

        Returns:
            MorseResult with decoded text.
        """
        self._ensure_active()
        self._increment_call_count()

        errors: List[str] = []

        # Normalize symbols to standard . and -
        normalized = morse.replace(dit_symbol, '.').replace(dah_symbol, '-')

        # Split into words
        words = normalized.split(word_separator)
        decoded_words: List[str] = []

        for word in words:
            chars = word.split(char_separator)
            decoded_chars: List[str] = []

            for code in chars:
                code = code.strip()
                if not code:
                    continue
                if code in self._reverse_map:
                    decoded_chars.append(self._reverse_map[code])
                elif code == '?':
                    decoded_chars.append('?')
                else:
                    errors.append(f"Unknown Morse code: '{code}'")
                    decoded_chars.append('?')

            decoded_words.append(''.join(decoded_chars))

        output = ' '.join(decoded_words)

        return MorseResult(
            input=morse,
            output=output,
            success=len(errors) == 0,
            errors=errors,
        )

    # ==================== Validation ====================

    def validate(self, morse: str) -> Tuple[bool, List[str]]:
        """Validate Morse code syntax.

        Args:
            morse: Morse code string to validate.

        Returns:
            Tuple of (is_valid, list_of_errors).
        """
        self._ensure_active()
        self._increment_call_count()

        errors: List[str] = []

        # Check characters
        valid_chars = set('.- /')
        for i, ch in enumerate(morse):
            if ch not in valid_chars:
                errors.append(f"Invalid character '{ch}' at position {i}")

        # Check each code
        words = morse.split(' / ')
        if len(words) == 1:
            words = morse.split('   ')

        for word_idx, word in enumerate(words):
            chars = word.split()
            for char_idx, code in enumerate(chars):
                if code and code not in self._reverse_map:
                    errors.append(
                        f"Unknown code '{code}' at word {word_idx + 1}, "
                        f"char {char_idx + 1}"
                    )

        return len(errors) == 0, errors

    # ==================== Timing ====================

    def get_timing(self, wpm: Optional[int] = None) -> MorseTiming:
        """Get Morse code timing parameters.

        Args:
            wpm: Words per minute (standard PARIS method).
                 If None, uses default timing.

        Returns:
            MorseTiming with all timing parameters.
        """
        self._ensure_active()
        self._increment_call_count()

        if wpm is None:
            return self._default_timing

        # PARIS method: "PARIS" is 50 dit durations
        # 1 WPM = 50 dits per minute = 1200ms per dit
        dit_ms = int(1200 / wpm) if wpm > 0 else 100

        return MorseTiming(
            dit_duration_ms=dit_ms,
            dah_duration_ms=dit_ms * 3,
            intra_char_gap_ms=dit_ms,
            inter_char_gap_ms=dit_ms * 3,
            word_gap_ms=dit_ms * 7,
            frequency_hz=self._default_timing.frequency_hz,
        )

    def calculate_duration(
        self,
        morse: str,
        wpm: Optional[int] = None,
    ) -> float:
        """Calculate total duration of Morse code in seconds.

        Args:
            morse: Morse code string.
            wpm: Words per minute. Uses default if None.

        Returns:
            Total duration in seconds.
        """
        self._ensure_active()
        self._increment_call_count()

        timing = self.get_timing(wpm)
        total_ms = 0

        words = morse.split('   ')
        for word_idx, word in enumerate(words):
            if word_idx > 0:
                total_ms += timing.word_gap_ms

            chars = word.split()
            for char_idx, code in enumerate(chars):
                if char_idx > 0:
                    total_ms += timing.inter_char_gap_ms

                for elem_idx, elem in enumerate(code):
                    if elem_idx > 0:
                        total_ms += timing.intra_char_gap_ms
                    if elem == '.':
                        total_ms += timing.dit_duration_ms
                    elif elem == '-':
                        total_ms += timing.dah_duration_ms

        return total_ms / 1000.0

    # ==================== Audio Generation Interface ====================

    def generate_audio_params(
        self,
        morse: str,
        wpm: Optional[int] = None,
        frequency: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Generate parameters for Morse code audio.

        This provides the timing and frequency data needed to
        generate actual audio (e.g., using a wave generator).

        Args:
            morse: Morse code string.
            wpm: Words per minute.
            frequency: Tone frequency in Hz.

        Returns:
            Dict with tone list, total_duration, frequency, wpm.
        """
        self._ensure_active()
        self._increment_call_count()

        timing = self.get_timing(wpm)
        freq = frequency or timing.frequency_hz

        tones: List[Dict[str, Any]] = []
        current_time = 0.0

        words = morse.split('   ')
        for word_idx, word in enumerate(words):
            if word_idx > 0:
                current_time += timing.word_gap_ms / 1000.0

            chars = word.split()
            for char_idx, code in enumerate(chars):
                if char_idx > 0:
                    current_time += timing.inter_char_gap_ms / 1000.0

                for elem_idx, elem in enumerate(code):
                    if elem_idx > 0:
                        current_time += timing.intra_char_gap_ms / 1000.0

                    if elem == '.':
                        duration = timing.dit_duration_ms / 1000.0
                    elif elem == '-':
                        duration = timing.dah_duration_ms / 1000.0
                    else:
                        continue

                    tones.append({
                        "start": round(current_time, 4),
                        "duration": round(duration, 4),
                        "frequency": freq,
                        "type": "dit" if elem == '.' else "dah",
                    })
                    current_time += duration

        return {
            "tones": tones,
            "total_duration": round(current_time, 4),
            "frequency": freq,
            "wpm": wpm or self._wpm_from_timing(timing),
            "tone_count": len(tones),
        }

    # ==================== Code Table ====================

    def get_code_table(self, category: str = "all") -> Dict[str, str]:
        """Get the Morse code table, optionally filtered by category.

        Args:
            category: One of 'all', 'letters', 'numbers', 'punctuation',
                      'procedural'.

        Returns:
            Dict mapping characters to Morse code.
        """
        self._ensure_active()
        self._increment_call_count()

        if category == "letters":
            return {k: v for k, v in self.MORSE_CODE.items()
                    if len(k) == 1 and k.isalpha()}
        elif category == "numbers":
            return {k: v for k, v in self.MORSE_CODE.items()
                    if len(k) == 1 and k.isdigit()}
        elif category == "punctuation":
            return {k: v for k, v in self.MORSE_CODE.items()
                    if len(k) == 1 and not k.isalnum()}
        elif category == "procedural":
            return {k: v for k, v in self.MORSE_CODE.items()
                    if k.startswith('<')}
        else:
            return dict(self.MORSE_CODE)

    # ==================== Internal Helpers ====================

    def _wpm_from_timing(self, timing: MorseTiming) -> int:
        """Calculate WPM from timing (PARIS method)."""
        # PARIS = 50 dit durations
        # WPM = 60000ms / (50 * dit_ms)
        return int(60000 / (50 * timing.dit_duration_ms))

    # ==================== Statistics ====================

    def get_stats(self) -> Dict[str, Any]:
        """Get plugin statistics."""
        return {
            "code_table_size": len(self.MORSE_CODE),
            "default_wpm": self._wpm_from_timing(self._default_timing),
            "default_frequency_hz": self._default_timing.frequency_hz,
            "letters": sum(1 for k in self.MORSE_CODE if len(k) == 1 and k.isalpha()),
            "numbers": sum(1 for k in self.MORSE_CODE if len(k) == 1 and k.isdigit()),
            "punctuation": sum(1 for k in self.MORSE_CODE if len(k) == 1 and not k.isalnum()),
            "procedural_signals": sum(1 for k in self.MORSE_CODE if k.startswith('<')),
        }
