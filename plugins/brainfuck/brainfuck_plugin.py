"""
Brainfuck Interpreter Plugin for LiuHao AI OS

Provides Brainfuck code execution, text-to-brainfuck encoding,
brainfuck optimization, step-by-step execution, and memory visualization.

Capabilities:
- execute: Run Brainfuck code
- encode_text: Convert text to Brainfuck
- optimize: Optimize Brainfuck code
- step_execute: Step-by-step execution with state
- visualize_memory: Memory state visualization
- validate: Validate Brainfuck syntax
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
class BFExecutionResult:
    """Result of a Brainfuck program execution."""
    output: str
    memory: List[int]
    memory_pointer: int
    steps_executed: int
    success: bool
    error: Optional[str] = None


@dataclass
class BFStepState:
    """State of a single step in Brainfuck execution."""
    instruction: str
    instruction_pointer: int
    memory_pointer: int
    memory: List[int]
    output: str
    step_number: int


class BrainfuckPlugin(BasePlugin):
    """Brainfuck language interpreter and toolset.

    Supports full Brainfuck execution, text encoding, code optimization,
    step-by-step debugging, and memory visualization.

    Example:
        plugin = BrainfuckPlugin()
        plugin.initialize()
        plugin.activate()

        result = plugin.execute("++++++++[>++++[>++>+++>+++>+<<<<-]>+>+>->>+[<]<-]>>.>---.+++++++..+++.>>.<-.<.+++.------.--------.>>+.>++.")
        print(result.output)  # "Hello World!"
    """

    BF_COMMANDS = set('><+-.,[]')

    def __init__(self) -> None:
        """Initialize the Brainfuck plugin."""
        metadata = PluginMetadata(
            name="brainfuck",
            version="1.0.0",
            description="Brainfuck interpreter, encoder, optimizer, and visualizer",
            author="LiuHao AI OS Team",
            categories=["language", "esoteric", "utility"],
            keywords=["brainfuck", "esoteric", "interpreter", "encoder", "optimizer"],
            requires_internet=False,
            dependencies=[],
        )
        super().__init__(metadata)

        self._memory_size = 30000
        self._max_steps = 1_000_000

    # ==================== Lifecycle Methods ====================

    def initialize(self, config: Optional[Dict[str, Any]] = None) -> bool:
        """Initialize the Brainfuck plugin."""
        try:
            if config:
                self._memory_size = config.get("memory_size", 30000)
                self._max_steps = config.get("max_steps", 1_000_000)

            self._set_status(PluginStatus.INITIALIZED)
            return True
        except Exception as e:
            self._set_error(f"Initialization failed: {e}")
            return False

    def activate(self) -> bool:
        """Activate the Brainfuck plugin."""
        try:
            self._set_status(PluginStatus.ACTIVE)
            return True
        except Exception as e:
            self._set_error(f"Activation failed: {e}")
            return False

    def deactivate(self) -> bool:
        """Deactivate the Brainfuck plugin."""
        try:
            self._set_status(PluginStatus.INACTIVE)
            return True
        except Exception as e:
            self._set_error(str(e))
            return False

    def shutdown(self) -> bool:
        """Shut down the Brainfuck plugin."""
        try:
            self._set_status(PluginStatus.SHUTDOWN)
            return True
        except Exception as e:
            self._set_error(str(e))
            return False

    def get_capabilities(self) -> List[str]:
        """Return the list of capabilities."""
        return [
            "execute",
            "encode_text",
            "optimize",
            "step_execute",
            "visualize_memory",
            "validate",
        ]

    # ==================== Core Execution ====================

    def execute(
        self,
        code: str,
        input_data: str = "",
        memory_size: Optional[int] = None,
        max_steps: Optional[int] = None,
    ) -> BFExecutionResult:
        """Execute Brainfuck code.

        Args:
            code: Brainfuck source code.
            input_data: Input data for ',' commands.
            memory_size: Size of memory tape (default from config).
            max_steps: Maximum execution steps (default from config).

        Returns:
            BFExecutionResult with output, memory, and stats.
        """
        self._ensure_active()
        self._increment_call_count()

        mem_size = memory_size or self._memory_size
        max_st = max_steps or self._max_steps

        # Filter to valid commands only
        clean_code = ''.join(c for c in code if c in self.BF_COMMANDS)

        # Build bracket map
        try:
            bracket_map = self._build_bracket_map(clean_code)
        except ValueError as e:
            return BFExecutionResult(
                output="",
                memory=[],
                memory_pointer=0,
                steps_executed=0,
                success=False,
                error=str(e),
            )

        memory = [0] * mem_size
        ptr = 0
        ip = 0
        output: List[str] = []
        input_idx = 0
        steps = 0

        while ip < len(clean_code) and steps < max_st:
            cmd = clean_code[ip]
            steps += 1

            if cmd == '>':
                ptr = (ptr + 1) % mem_size
            elif cmd == '<':
                ptr = (ptr - 1) % mem_size
            elif cmd == '+':
                memory[ptr] = (memory[ptr] + 1) & 255
            elif cmd == '-':
                memory[ptr] = (memory[ptr] - 1) & 255
            elif cmd == '.':
                output.append(chr(memory[ptr]))
            elif cmd == ',':
                if input_idx < len(input_data):
                    memory[ptr] = ord(input_data[input_idx])
                    input_idx += 1
                else:
                    memory[ptr] = 0
            elif cmd == '[':
                if memory[ptr] == 0:
                    ip = bracket_map[ip]
            elif cmd == ']':
                if memory[ptr] != 0:
                    ip = bracket_map[ip]

            ip += 1

        success = steps < max_st
        error = None if success else f"Execution exceeded max steps ({max_st})"

        return BFExecutionResult(
            output=''.join(output),
            memory=memory,
            memory_pointer=ptr,
            steps_executed=steps,
            success=success,
            error=error,
        )

    def validate(self, code: str) -> Tuple[bool, str]:
        """Validate Brainfuck code syntax.

        Args:
            code: Brainfuck source code.

        Returns:
            Tuple of (is_valid, error_message).
        """
        self._ensure_active()
        self._increment_call_count()

        depth = 0
        for i, c in enumerate(code):
            if c == '[':
                depth += 1
            elif c == ']':
                depth -= 1
                if depth < 0:
                    return False, f"Unmatched ']' at position {i}"

        if depth != 0:
            return False, f"Unmatched '[' ({depth} open brackets)"

        return True, "Valid Brainfuck code"

    # ==================== Encoding ====================

    def encode_text(self, text: str) -> str:
        """Convert text to Brainfuck code that outputs the text.

        Uses a multi-cell approach for efficient encoding.

        Args:
            text: The text to encode.

        Returns:
            Brainfuck code that outputs the given text.
        """
        self._ensure_active()
        self._increment_call_count()

        if not text:
            return ""

        # Multiplier-based encoding: use cell 0 as counter, cell 1+ as output
        # Strategy: find common divisor for consecutive similar chars
        result: List[str] = []
        prev_val = 0

        for char in text:
            target = ord(char)
            diff = target - prev_val

            if diff > 0:
                # Use multiplication for large positive diffs
                if diff > 10:
                    # Find good factorization
                    best_factor = 1
                    for f in range(10, 1, -1):
                        if diff % f == 0:
                            best_factor = f
                            break
                    if best_factor > 1:
                        quotient = diff // best_factor
                        # Set up loop
                        result.append('+' * best_factor)
                        result.append('[')
                        result.append('>')
                        result.append('+' * quotient)
                        result.append('<')
                        result.append('-')
                        result.append(']')
                        result.append('>')
                        result.append('.')
                        prev_val = target
                        continue

                result.append('+' * diff)
                result.append('.')
            elif diff < 0:
                result.append('-' * (-diff))
                result.append('.')
            else:
                result.append('.')

            prev_val = target

        return ''.join(result)

    # ==================== Optimization ====================

    def optimize(self, code: str) -> str:
        """Optimize Brainfuck code by removing no-ops and combining operations.

        Optimizations:
        - Remove non-BF characters
        - Cancel adjacent +/- and >/<
        - Remove empty loops []
        - Remove leading no-ops

        Args:
            code: Brainfuck source code.

        Returns:
            Optimized Brainfuck code.
        """
        self._ensure_active()
        self._increment_call_count()

        # Filter to valid commands
        result = [c for c in code if c in self.BF_COMMANDS]

        # Pass 1: Cancel adjacent opposites (+/- and >/<)
        changed = True
        while changed:
            changed = False
            new_result: List[str] = []
            i = 0
            while i < len(result):
                if i + 1 < len(result):
                    pair = result[i] + result[i + 1]
                    if pair in ('+-', '-+', '><', '<>'):
                        i += 2
                        changed = True
                        continue
                new_result.append(result[i])
                i += 1
            result = new_result

        # Pass 2: Remove empty loops
        changed = True
        while changed:
            changed = False
            new_result: List[str] = []
            i = 0
            while i < len(result):
                if i + 1 < len(result) and result[i] == '[' and result[i + 1] == ']':
                    i += 2
                    changed = True
                    continue
                new_result.append(result[i])
                i += 1
            result = new_result

        return ''.join(result)

    # ==================== Step-by-Step Execution ====================

    def step_execute(
        self,
        code: str,
        num_steps: int = 10,
        input_data: str = "",
        memory_size: Optional[int] = None,
    ) -> List[BFStepState]:
        """Execute Brainfuck code step by step, returning state at each step.

        Args:
            code: Brainfuck source code.
            num_steps: Number of steps to execute.
            input_data: Input data for ',' commands.
            memory_size: Size of memory tape.

        Returns:
            List of BFStepState objects, one per step.
        """
        self._ensure_active()
        self._increment_call_count()

        mem_size = memory_size or self._memory_size
        clean_code = ''.join(c for c in code if c in self.BF_COMMANDS)

        try:
            bracket_map = self._build_bracket_map(clean_code)
        except ValueError:
            return []

        memory = [0] * mem_size
        ptr = 0
        ip = 0
        output: List[str] = []
        input_idx = 0
        states: List[BFStepState] = []
        step = 0

        while ip < len(clean_code) and step < num_steps:
            cmd = clean_code[ip]
            step += 1

            if cmd == '>':
                ptr = (ptr + 1) % mem_size
            elif cmd == '<':
                ptr = (ptr - 1) % mem_size
            elif cmd == '+':
                memory[ptr] = (memory[ptr] + 1) & 255
            elif cmd == '-':
                memory[ptr] = (memory[ptr] - 1) & 255
            elif cmd == '.':
                output.append(chr(memory[ptr]))
            elif cmd == ',':
                if input_idx < len(input_data):
                    memory[ptr] = ord(input_data[input_idx])
                    input_idx += 1
                else:
                    memory[ptr] = 0
            elif cmd == '[':
                if memory[ptr] == 0:
                    ip = bracket_map[ip]
            elif cmd == ']':
                if memory[ptr] != 0:
                    ip = bracket_map[ip]

            ip += 1

            states.append(BFStepState(
                instruction=cmd,
                instruction_pointer=ip,
                memory_pointer=ptr,
                memory=list(memory[:max(ptr + 1, 10)]),
                output=''.join(output),
                step_number=step,
            ))

        return states

    # ==================== Memory Visualization ====================

    def visualize_memory(
        self,
        memory: List[int],
        pointer: int = 0,
        width: int = 16,
        show_ascii: bool = True,
    ) -> str:
        """Visualize memory state in a hex-dump-like format.

        Args:
            memory: List of memory cell values.
            pointer: Current memory pointer position.
            width: Number of cells per row.
            show_ascii: Whether to show ASCII representation.

        Returns:
            Formatted memory visualization string.
        """
        self._ensure_active()
        self._increment_call_count()

        lines: List[str] = []
        lines.append(f"Memory ({len(memory)} cells, pointer at {pointer}):")
        lines.append("-" * (width * 4 + (width // 4) + 10))

        for row_start in range(0, len(memory), width):
            row_end = min(row_start + width, len(memory))
            row = memory[row_start:row_end]

            # Address
            addr_str = f"{row_start:04x}: "

            # Hex values with pointer marker
            hex_parts: List[str] = []
            for i, val in enumerate(row):
                cell_idx = row_start + i
                marker = ">" if cell_idx == pointer else " "
                hex_parts.append(f"{marker}{val:02x}")

            hex_str = " ".join(hex_parts)

            # Pad if short row
            if len(row) < width:
                padding = "   " * (width - len(row))
                hex_str += padding

            # ASCII representation
            if show_ascii:
                ascii_chars = []
                for i, val in enumerate(row):
                    cell_idx = row_start + i
                    if 32 <= val < 127:
                        ch = chr(val)
                    else:
                        ch = "."
                    if cell_idx == pointer:
                        ch = f"[{ch}]"
                    ascii_chars.append(ch)
                ascii_str = " " + "".join(ascii_chars)
            else:
                ascii_str = ""

            lines.append(f"{addr_str}{hex_str}{ascii_str}")

        return "\n".join(lines)

    # ==================== Internal Helpers ====================

    def _build_bracket_map(self, code: str) -> Dict[int, int]:
        """Build a map of bracket positions for fast jumps.

        Raises:
            ValueError: If brackets are unmatched.
        """
        bracket_map: Dict[int, int] = {}
        stack: List[int] = []

        for i, cmd in enumerate(code):
            if cmd == '[':
                stack.append(i)
            elif cmd == ']':
                if not stack:
                    raise ValueError(f"Unmatched ']' at position {i}")
                start = stack.pop()
                bracket_map[start] = i
                bracket_map[i] = start

        if stack:
            raise ValueError(f"Unmatched '[' at position(s): {stack}")

        return bracket_map

    # ==================== Statistics ====================

    def get_stats(self) -> Dict[str, Any]:
        """Get plugin statistics."""
        return {
            "memory_size": self._memory_size,
            "max_steps": self._max_steps,
            "commands": sorted(list(self.BF_COMMANDS)),
        }
