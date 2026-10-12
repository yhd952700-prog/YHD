"""
Enigma Machine Plugin for LiuHao AI OS

Provides Enigma M3 machine simulation with rotor configuration,
plugboard settings, reflector configuration, ring settings,
and encrypt/decrypt functionality.

Capabilities:
- encrypt: Encrypt text (same as decrypt for Enigma)
- configure: Configure rotor, reflector, plugboard settings
- get_rotor_wiring: Get rotor wiring information
- step_simulation: Step-by-step encryption with state
- validate_config: Validate Enigma configuration
- get_presets: Get standard rotor/reflector presets
"""

from typing import Dict, Any, Optional, List, Tuple
from dataclasses import dataclass, field
import copy

from ..plugin_base import (
    BasePlugin,
    PluginMetadata,
    PluginStatus,
    PluginExecutionError,
)


@dataclass
class EnigmaConfig:
    """Enigma machine configuration."""
    rotors: List[str]  # Rotor names, left to right (e.g., ["I", "II", "III"])
    reflector: str  # Reflector name (e.g., "B")
    ring_settings: List[int]  # Ring settings, 0-25 (A=0)
    initial_positions: List[int]  # Initial rotor positions, 0-25
    plugboard: Dict[str, str]  # Plugboard connections


@dataclass
class EnigmaResult:
    """Result of Enigma encryption/decryption."""
    input: str
    output: str
    config: EnigmaConfig
    final_positions: List[int]


@dataclass
class EnigmaStepState:
    """State of a single Enigma step."""
    step: int
    input_char: str
    output_char: str
    positions: List[int]  # Rotor positions after step
    path: List[str]  # Signal path: plugboard -> rotors -> reflector -> rotors -> plugboard


class EnigmaPlugin(BasePlugin):
    """Enigma M3 machine simulator.

    Simulates the German Enigma M3 machine with configurable
    rotors, reflector, plugboard, and ring settings. Encryption
    and decryption are the same operation.

    Example:
        plugin = EnigmaPlugin()
        plugin.initialize()
        plugin.activate()

        result = plugin.encrypt("HELLO", ["I", "II", "III"], "B",
                                [0, 0, 0], [0, 0, 0], {})
        print(result.output)
    """

    # Standard Enigma M3 rotor wirings (right to left mapping)
    # Each string represents the output for input position 0-25
    ROTORS: Dict[str, str] = {
        "I": "EKMFLGDQVZNTOWYHXUSPAIBRCJ",
        "II": "AJDKSIRUXBLHWTMCQGZNPYFVOE",
        "III": "BDFHJLCPRTXVZNYEIWGAKMUSQO",
        "IV": "ESOVPZJAYQUIRHXLNFTGKDCMWB",
        "V": "VZBRGITYUPSDNHLXAWMJQOFECK",
        "VI": "JPGVOUMFYQBENHZRDKASXLICTW",
        "VII": "NZJHGRCXMYSWBOUFAIVLPEKQDT",
        "VIII": "FKQHTLXOCBJSPDZRAMEWNIUYGV",
    }

    # Rotor turnover positions (where the next rotor advances)
    TURNOVERS: Dict[str, List[int]] = {
        "I": [16],    # Q
        "II": [4],    # E
        "III": [21],  # V
        "IV": [9],    # J
        "V": [25],    # Z
        "VI": [12, 25],   # M, Z
        "VII": [12, 25],  # M, Z
        "VIII": [12, 25], # M, Z
    }

    # Reflector wirings
    REFLECTORS: Dict[str, str] = {
        "A": "EJMZALYXVBWFCRQUONTSPIKHGD",
        "B": "YRUHQSLDPXNGOKMIEBFZCWVJAT",
        "C": "FVPJIAOYEDRZXWGCTKUQSBNMHL",
        "B_THIN": "ENKQAUYWJICOPBLMDXZVFTHRGS",
        "C_THIN": "RDOBJNTKVEHMLFCWZAXGYIPSUQ",
    }

    def __init__(self) -> None:
        """Initialize the Enigma plugin."""
        metadata = PluginMetadata(
            name="enigma",
            version="1.0.0",
            description="Enigma M3 machine simulator with configurable rotors, reflector, and plugboard",
            author="LiuHao AI OS Team",
            categories=["cryptography", "cipher", "historical"],
            keywords=["enigma", "cipher", "machine", "rotor", "world war", "crypto"],
            requires_internet=False,
            dependencies=[],
        )
        super().__init__(metadata)

        self._default_config = EnigmaConfig(
            rotors=["I", "II", "III"],
            reflector="B",
            ring_settings=[0, 0, 0],
            initial_positions=[0, 0, 0],
            plugboard={},
        )

    # ==================== Lifecycle Methods ====================

    def initialize(self, config: Optional[Dict[str, Any]] = None) -> bool:
        """Initialize the Enigma plugin."""
        try:
            if config:
                rotors = config.get("rotors", ["I", "II", "III"])
                reflector = config.get("reflector", "B")
                ring_settings = config.get("ring_settings", [0, 0, 0])
                positions = config.get("initial_positions", [0, 0, 0])
                plugboard = config.get("plugboard", {})
                self._default_config = EnigmaConfig(
                    rotors=rotors,
                    reflector=reflector,
                    ring_settings=ring_settings,
                    initial_positions=positions,
                    plugboard=plugboard,
                )

            self._set_status(PluginStatus.INITIALIZED)
            return True
        except Exception as e:
            self._set_error(f"Initialization failed: {e}")
            return False

    def activate(self) -> bool:
        """Activate the Enigma plugin."""
        try:
            self._set_status(PluginStatus.ACTIVE)
            return True
        except Exception as e:
            self._set_error(f"Activation failed: {e}")
            return False

    def deactivate(self) -> bool:
        """Deactivate the Enigma plugin."""
        try:
            self._set_status(PluginStatus.INACTIVE)
            return True
        except Exception as e:
            self._set_error(str(e))
            return False

    def shutdown(self) -> bool:
        """Shut down the Enigma plugin."""
        try:
            self._set_status(PluginStatus.SHUTDOWN)
            return True
        except Exception as e:
            self._set_error(str(e))
            return False

    def get_capabilities(self) -> List[str]:
        """Return the list of capabilities."""
        return [
            "encrypt",
            "decrypt",
            "configure",
            "get_rotor_wiring",
            "step_encrypt",
            "validate_config",
            "get_presets",
        ]
    # ==================== Encryption / Decryption ====================

    def encrypt(
        self,
        text: str,
        rotors: Optional[List[str]] = None,
        reflector: Optional[str] = None,
        ring_settings: Optional[List[int]] = None,
        initial_positions: Optional[List[int]] = None,
        plugboard: Optional[Dict[str, str]] = None,
    ) -> EnigmaResult:
        """Encrypt/decrypt text using Enigma machine.

        Encryption and decryption are the same operation for Enigma.

        Args:
            text: The text to process (letters only, others preserved).
            rotors: List of rotor names (left to right).
            reflector: Reflector name.
            ring_settings: Ring settings for each rotor (0-25).
            initial_positions: Initial rotor positions (0-25).
            plugboard: Plugboard connections dict.

        Returns:
            EnigmaResult with output and final state.
        """
        self._ensure_active()
        self._increment_call_count()

        config = self._build_config(
            rotors, reflector, ring_settings, initial_positions, plugboard
        )
        self._validate_config(config)

        positions = list(config.initial_positions)
        result_chars: List[str] = []

        for ch in text:
            if not ch.isalpha():
                result_chars.append(ch)
                continue

            ch_upper = ch.upper()

            # Step rotors
            self._step_rotors(positions, config.rotors)

            # Pass through plugboard
            plug_in = ch_upper
            plug_out = self._pass_plugboard(plug_in, config.plugboard)

            # Pass through rotors (right to left)
            current = ord(plug_out) - ord('A')
            path_rl: List[str] = []
            for i in range(len(config.rotors) - 1, -1, -1):
                rotor_name = config.rotors[i]
                ring = config.ring_settings[i]
                pos = positions[i]
                current = self._pass_rotor_forward(current, rotor_name, ring, pos)
                path_rl.append(chr(current + ord('A')))

            # Pass through reflector
            current = self._pass_reflector(current, config.reflector)
            reflected_char = chr(current + ord('A'))

            # Pass back through rotors (left to right)
            path_lr: List[str] = []
            for i in range(len(config.rotors)):
                rotor_name = config.rotors[i]
                ring = config.ring_settings[i]
                pos = positions[i]
                current = self._pass_rotor_backward(current, rotor_name, ring, pos)
                path_lr.append(chr(current + ord('A')))

            # Pass through plugboard again
            final_char = self._pass_plugboard(
                chr(current + ord('A')), config.plugboard
            )

            # Preserve case
            if ch.islower():
                result_chars.append(final_char.lower())
            else:
                result_chars.append(final_char)

        return EnigmaResult(
            input=text,
            output=''.join(result_chars),
            config=config,
            final_positions=positions,
        )

    def decrypt(
        self,
        text: str,
        rotors: Optional[List[str]] = None,
        reflector: Optional[str] = None,
        ring_settings: Optional[List[int]] = None,
        initial_positions: Optional[List[int]] = None,
        plugboard: Optional[Dict[str, str]] = None,
    ) -> EnigmaResult:
        """Decrypt text using Enigma machine (same as encrypt).

        Enigma is symmetric - encryption and decryption are identical.

        Args:
            text: The text to decrypt.
            rotors: List of rotor names.
            reflector: Reflector name.
            ring_settings: Ring settings.
            initial_positions: Initial positions.
            plugboard: Plugboard connections.

        Returns:
            EnigmaResult with decrypted text.
        """
        return self.encrypt(
            text, rotors, reflector, ring_settings,
            initial_positions, plugboard
        )

    # ==================== Step-by-step ====================

    def step_encrypt(
        self,
        text: str,
        rotors: Optional[List[str]] = None,
        reflector: Optional[str] = None,
        ring_settings: Optional[List[int]] = None,
        initial_positions: Optional[List[int]] = None,
        plugboard: Optional[Dict[str, str]] = None,
    ) -> List[EnigmaStepState]:
        """Encrypt text with step-by-step state tracking.

        Args:
            text: The text to encrypt.
            rotors: Rotor names.
            reflector: Reflector name.
            ring_settings: Ring settings.
            initial_positions: Initial positions.
            plugboard: Plugboard connections.

        Returns:
            List of EnigmaStepState for each letter processed.
        """
        self._ensure_active()
        self._increment_call_count()

        config = self._build_config(
            rotors, reflector, ring_settings, initial_positions, plugboard
        )
        self._validate_config(config)

        positions = list(config.initial_positions)
        states: List[EnigmaStepState] = []
        step = 0

        for ch in text:
            if not ch.isalpha():
                continue

            step += 1
            ch_upper = ch.upper()

            # Step rotors
            self._step_rotors(positions, config.rotors)
            step_positions = list(positions)

            # Build signal path
            path: List[str] = []

            # Plugboard in
            plug_out = self._pass_plugboard(ch_upper, config.plugboard)
            path.append(f"PB:{ch_upper}->{plug_out}")

            # Rotors right to left
            current = ord(plug_out) - ord('A')
            for i in range(len(config.rotors) - 1, -1, -1):
                rotor_name = config.rotors[i]
                ring = config.ring_settings[i]
                pos = positions[i]
                current = self._pass_rotor_forward(current, rotor_name, ring, pos)
                path.append(f"R{i}:{chr(current + ord('A'))}")

            # Reflector
            current = self._pass_reflector(current, config.reflector)
            path.append(f"REF:{chr(current + ord('A'))}")

            # Rotors left to right
            for i in range(len(config.rotors)):
                rotor_name = config.rotors[i]
                ring = config.ring_settings[i]
                pos = positions[i]
                current = self._pass_rotor_backward(current, rotor_name, ring, pos)
                path.append(f"R{i}':{chr(current + ord('A'))}")

            # Plugboard out
            final_char = self._pass_plugboard(
                chr(current + ord('A')), config.plugboard
            )
            path.append(f"PB':{final_char}")

            states.append(EnigmaStepState(
                step=step,
                input_char=ch_upper,
                output_char=final_char,
                positions=step_positions,
                path=path,
            ))

        return states

    # ==================== Configuration ====================

    def validate_config(
        self,
        rotors: List[str],
        reflector: str,
        ring_settings: List[int],
        positions: List[int],
        plugboard: Dict[str, str],
    ) -> Tuple[bool, List[str]]:
        """Validate an Enigma configuration.

        Args:
            rotors: List of rotor names.
            reflector: Reflector name.
            ring_settings: Ring settings.
            positions: Initial positions.
            plugboard: Plugboard connections.

        Returns:
            Tuple of (is_valid, list_of_errors).
        """
        self._ensure_active()
        self._increment_call_count()

        errors: List[str] = []

        if len(rotors) < 3:
            errors.append("At least 3 rotors required")

        for rotor in rotors:
            if rotor not in self.ROTORS:
                errors.append(f"Unknown rotor: {rotor}")

        if reflector not in self.REFLECTORS:
            errors.append(f"Unknown reflector: {reflector}")

        if len(ring_settings) != len(rotors):
            errors.append("Ring settings count must match rotor count")
        for i, rs in enumerate(ring_settings):
            if not isinstance(rs, int) or rs < 0 or rs > 25:
                errors.append(f"Invalid ring setting at position {i}: {rs}")

        if len(positions) != len(rotors):
            errors.append("Positions count must match rotor count")
        for i, pos in enumerate(positions):
            if not isinstance(pos, int) or pos < 0 or pos > 25:
                errors.append(f"Invalid position at position {i}: {pos}")

        # Validate plugboard
        seen: set = set()
        for a, b in plugboard.items():
            if not (a.isalpha() and len(a) == 1):
                errors.append(f"Invalid plugboard key: {a}")
                continue
            if not (b.isalpha() and len(b) == 1):
                errors.append(f"Invalid plugboard value for {a}: {b}")
                continue
            a_up = a.upper()
            b_up = b.upper()
            if a_up in seen:
                errors.append(f"Plugboard letter {a_up} used multiple times")
            if b_up in seen:
                errors.append(f"Plugboard letter {b_up} used multiple times")
            seen.add(a_up)
            seen.add(b_up)

        return len(errors) == 0, errors

    def get_rotor_wiring(self, rotor_name: str) -> Dict[str, Any]:
        """Get wiring information for a specific rotor.

        Args:
            rotor_name: Name of the rotor.

        Returns:
            Dict with wiring details.

        Raises:
            ValueError: If rotor name is unknown.
        """
        self._ensure_active()
        self._increment_call_count()

        if rotor_name not in self.ROTORS:
            raise ValueError(f"Unknown rotor: {rotor_name}")

        wiring = self.ROTORS[rotor_name]
        turnovers = self.TURNOVERS.get(rotor_name, [])

        # Build mapping
        forward_map: Dict[str, str] = {}
        reverse_map: Dict[str, str] = {}
        for i in range(26):
            input_char = chr(ord('A') + i)
            output_char = wiring[i]
            forward_map[input_char] = output_char
            reverse_map[output_char] = input_char

        return {
            "name": rotor_name,
            "wiring": wiring,
            "forward_map": forward_map,
            "reverse_map": reverse_map,
            "turnovers": [chr(ord('A') + t) for t in turnovers],
            "notches": len(turnovers),
        }

    def get_presets(self) -> Dict[str, Any]:
        """Get all available rotor and reflector presets.

        Returns:
            Dict with available rotors and reflectors.
        """
        self._ensure_active()
        self._increment_call_count()

        return {
            "rotors": {
                name: {
                    "wiring": self.ROTORS[name],
                    "turnovers": [chr(ord('A') + t) for t in self.TURNOVERS.get(name, [])],
                }
                for name in sorted(self.ROTORS.keys())
            },
            "reflectors": {
                name: self.REFLECTORS[name]
                for name in sorted(self.REFLECTORS.keys())
            },
            "default_config": {
                "rotors": self._default_config.rotors,
                "reflector": self._default_config.reflector,
                "ring_settings": self._default_config.ring_settings,
                "initial_positions": self._default_config.initial_positions,
                "plugboard": self._default_config.plugboard,
            },
        }

    # ==================== Internal Helpers ====================

    def _build_config(
        self,
        rotors: Optional[List[str]],
        reflector: Optional[str],
        ring_settings: Optional[List[int]],
        initial_positions: Optional[List[int]],
        plugboard: Optional[Dict[str, str]],
    ) -> EnigmaConfig:
        """Build a configuration using defaults where not specified."""
        return EnigmaConfig(
            rotors=rotors or self._default_config.rotors,
            reflector=reflector or self._default_config.reflector,
            ring_settings=(
                ring_settings
                if ring_settings is not None
                else self._default_config.ring_settings
            ),
            initial_positions=(
                initial_positions
                if initial_positions is not None
                else self._default_config.initial_positions
            ),
            plugboard=plugboard if plugboard is not None else self._default_config.plugboard,
        )

    def _validate_config(self, config: EnigmaConfig) -> None:
        """Validate config, raise ValueError if invalid."""
        valid, errors = self.validate_config(
            config.rotors, config.reflector,
            config.ring_settings, config.initial_positions,
            config.plugboard,
        )
        if not valid:
            raise ValueError("Invalid Enigma config: " + "; ".join(errors))

    def _step_rotors(self, positions: List[int], rotors: List[str]) -> None:
        """Step the rotors (rightmost first, with turnover).

        Modifies positions list in place.
        """
        num_rotors = len(rotors)
        if num_rotors == 0:
            return

        # Rightmost rotor always steps
        positions[-1] = (positions[-1] + 1) % 26

        # Check turnover for rightmost rotor (steps middle)
        right_idx = num_rotors - 1
        right_turnovers = self.TURNOVERS.get(rotors[right_idx], [])
        # The rotor just stepped to positions[-1], check if the PREVIOUS
        # position was a turnover position
        prev_right = (positions[-1] - 1) % 26
        if prev_right in right_turnovers:
            # Step middle rotor
            if num_rotors >= 2:
                mid_idx = num_rotors - 2
                positions[mid_idx] = (positions[mid_idx] + 1) % 26

                # Check middle turnover (steps left rotor)
                mid_turnovers = self.TURNOVERS.get(rotors[mid_idx], [])
                prev_mid = (positions[mid_idx] - 1) % 26
                if prev_mid in mid_turnovers and num_rotors >= 3:
                    left_idx = num_rotors - 3
                    positions[left_idx] = (positions[left_idx] + 1) % 26

        # Double step: if middle rotor is at turnover AND right rotor
        # caused it to step, middle steps again (Enigma anomaly)
        if num_rotors >= 3:
            mid_idx = num_rotors - 2
            mid_turnovers = self.TURNOVERS.get(rotors[mid_idx], [])
            if positions[mid_idx] in mid_turnovers:
                positions[mid_idx] = (positions[mid_idx] + 1) % 26
                left_idx = num_rotors - 3
                positions[left_idx] = (positions[left_idx] + 1) % 26

    def _pass_rotor_forward(
        self, input_val: int, rotor_name: str, ring_setting: int, position: int
    ) -> int:
        """Pass signal through rotor from right to left (forward)."""
        wiring = self.ROTORS[rotor_name]
        # Adjust for rotor position and ring setting
        offset = (input_val + position - ring_setting) % 26
        output_char = wiring[offset]
        output_val = ord(output_char) - ord('A')
        result = (output_val - position + ring_setting) % 26
        return result

    def _pass_rotor_backward(
        self, input_val: int, rotor_name: str, ring_setting: int, position: int
    ) -> int:
        """Pass signal through rotor from left to right (backward)."""
        wiring = self.ROTORS[rotor_name]
        # Find which position maps to input
        adjusted_input = (input_val + position - ring_setting) % 26
        input_char = chr(adjusted_input + ord('A'))
        # Find index of input_char in wiring
        output_idx = wiring.index(input_char)
        result = (output_idx - position + ring_setting) % 26
        return result

    def _pass_reflector(self, input_val: int, reflector_name: str) -> int:
        """Pass signal through reflector."""
        wiring = self.REFLECTORS[reflector_name]
        return ord(wiring[input_val]) - ord('A')

    def _pass_plugboard(self, char: str, plugboard: Dict[str, str]) -> str:
        """Pass character through plugboard."""
        char_up = char.upper()
        # Check both directions
        if char_up in plugboard:
            return plugboard[char_up].upper()
        for k, v in plugboard.items():
            if v.upper() == char_up:
                return k.upper()
        return char_up

    # ==================== Statistics ====================

    def get_stats(self) -> Dict[str, Any]:
        """Get plugin statistics."""
        return {
            "available_rotors": len(self.ROTORS),
            "available_reflectors": len(self.REFLECTORS),
            "rotor_names": sorted(list(self.ROTORS.keys())),
            "reflector_names": sorted(list(self.REFLECTORS.keys())),
            "default_rotors": self._default_config.rotors,
            "default_reflector": self._default_config.reflector,
        }
