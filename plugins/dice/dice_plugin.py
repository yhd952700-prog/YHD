"""
Dice Rolling Plugin for LiuHao AI OS

Provides dice rolling with notation support, multiple dice types,
statistics, advantage/disadvantage, custom dice, and roll history.

Capabilities:
- roll: Roll dice with standard notation (2d6, 1d20+5)
- roll_dice: Roll specific dice (count, sides, modifier)
- advantage: Roll with advantage (2d20, keep highest)
- disadvantage: Roll with disadvantage (2d20, keep lowest)
- statistics: Calculate dice statistics
- roll_history: Get roll history
"""

import random
import re
from typing import Dict, Any, Optional, List, Tuple
from dataclasses import dataclass, field
from collections import Counter

from ..plugin_base import (
    BasePlugin,
    PluginMetadata,
    PluginStatus,
    PluginExecutionError,
)


@dataclass
class DiceRollResult:
    """Result of a dice roll."""
    notation: str
    dice: List[Tuple[int, int]]  # List of (count, sides) pairs
    rolls: List[List[int]]  # Individual rolls for each dice group
    modifiers: int
    total: int
    min_possible: int
    max_possible: int
    kept_rolls: Optional[List[int]] = None  # For advantage/disadvantage


class DicePlugin(BasePlugin):
    """Dice rolling plugin with notation support.

    Supports standard dice notation (e.g., "2d6+5", "4d8-2"),
    advantage/disadvantage rolling, dice statistics, and
    roll history tracking.

    Example:
        plugin = DicePlugin()
        plugin.initialize()
        plugin.activate()

        result = plugin.roll("2d6+3")
        print(result.total)
    """

    # Standard dice types
    STANDARD_DICE = [4, 6, 8, 10, 12, 20, 100]

    def __init__(self) -> None:
        """Initialize the dice plugin."""
        metadata = PluginMetadata(
            name="dice",
            version="1.0.0",
            description="Dice rolling with notation, advantage/disadvantage, and statistics",
            author="LiuHao AI OS Team",
            categories=["games", "utility", "random"],
            keywords=["dice", "roll", "d20", "rpg", "dnd", "random"],
            requires_internet=False,
            dependencies=[],
        )
        super().__init__(metadata)

        self._rng = random.Random()
        self._history: List[DiceRollResult] = []
        self._max_history = 100

    # ==================== Lifecycle Methods ====================

    def initialize(self, config: Optional[Dict[str, Any]] = None) -> bool:
        """Initialize the dice plugin."""
        try:
            if config:
                seed = config.get("seed")
                if seed is not None:
                    self._rng.seed(seed)
                self._max_history = config.get("max_history", 100)

            self._set_status(PluginStatus.INITIALIZED)
            return True
        except Exception as e:
            self._set_error(f"Initialization failed: {e}")
            return False

    def activate(self) -> bool:
        """Activate the dice plugin."""
        try:
            self._set_status(PluginStatus.ACTIVE)
            return True
        except Exception as e:
            self._set_error(f"Activation failed: {e}")
            return False

    def deactivate(self) -> bool:
        """Deactivate the dice plugin."""
        try:
            self._set_status(PluginStatus.INACTIVE)
            return True
        except Exception as e:
            self._set_error(str(e))
            return False

    def shutdown(self) -> bool:
        """Shut down the dice plugin."""
        try:
            self._set_status(PluginStatus.SHUTDOWN)
            return True
        except Exception as e:
            self._set_error(str(e))
            return False

    def get_capabilities(self) -> List[str]:
        """Return the list of capabilities."""
        return [
            "roll",
            "roll_dice",
            "advantage",
            "disadvantage",
            "statistics",
            "roll_history",
            "parse_notation",
            "clear_history",
        ]

    # ==================== Dice Rolling ====================

    def roll(self, notation: str) -> DiceRollResult:
        """Roll dice using standard dice notation.

        Supported notation:
        - "2d6": Roll 2 six-sided dice
        - "1d20+5": Roll 1 d20, add 5
        - "4d8-2": Roll 4 d8, subtract 2
        - "2d6+1d8": Multiple dice types
        - "d20": Single die (count defaults to 1)

        Args:
            notation: Dice notation string.

        Returns:
            DiceRollResult with roll details.

        Raises:
            ValueError: If notation is invalid.
        """
        self._ensure_active()
        self._increment_call_count()

        dice_groups, modifier = self._parse_notation(notation)

        if not dice_groups:
            raise ValueError(f"Invalid dice notation: {notation}")

        rolls: List[List[int]] = []
        total = modifier
        min_total = modifier
        max_total = modifier

        for count, sides in dice_groups:
            group_rolls = [self._rng.randint(1, sides) for _ in range(count)]
            rolls.append(group_rolls)
            total += sum(group_rolls)
            min_total += count * 1
            max_total += count * sides

        result = DiceRollResult(
            notation=notation,
            dice=dice_groups,
            rolls=rolls,
            modifiers=modifier,
            total=total,
            min_possible=min_total,
            max_possible=max_total,
        )

        self._add_to_history(result)
        return result

    def roll_dice(
        self,
        count: int = 1,
        sides: int = 6,
        modifier: int = 0,
    ) -> DiceRollResult:
        """Roll a specific number of dice.

        Args:
            count: Number of dice to roll.
            sides: Number of sides per die.
            modifier: Value to add to the total.

        Returns:
            DiceRollResult with roll details.
        """
        self._ensure_active()
        self._increment_call_count()

        if count < 1:
            raise ValueError("Dice count must be at least 1")
        if sides < 2:
            raise ValueError("Dice must have at least 2 sides")

        rolls = [self._rng.randint(1, sides) for _ in range(count)]
        total = sum(rolls) + modifier

        notation = f"{count}d{sides}"
        if modifier > 0:
            notation += f"+{modifier}"
        elif modifier < 0:
            notation += str(modifier)

        result = DiceRollResult(
            notation=notation,
            dice=[(count, sides)],
            rolls=[rolls],
            modifiers=modifier,
            total=total,
            min_possible=count + modifier,
            max_possible=count * sides + modifier,
        )

        self._add_to_history(result)
        return result

    # ==================== Advantage / Disadvantage ====================

    def advantage(self, sides: int = 20, modifier: int = 0) -> DiceRollResult:
        """Roll with advantage (roll twice, keep higher).

        Args:
            sides: Number of sides (default 20 for d20).
            modifier: Value to add to the result.

        Returns:
            DiceRollResult with both rolls and kept value.
        """
        self._ensure_active()
        self._increment_call_count()

        rolls = [self._rng.randint(1, sides) for _ in range(2)]
        kept = max(rolls)
        total = kept + modifier

        result = DiceRollResult(
            notation=f"2d{sides} advantage",
            dice=[(2, sides)],
            rolls=[rolls],
            modifiers=modifier,
            total=total,
            min_possible=1 + modifier,
            max_possible=sides + modifier,
            kept_rolls=[kept],
        )

        self._add_to_history(result)
        return result

    def disadvantage(self, sides: int = 20, modifier: int = 0) -> DiceRollResult:
        """Roll with disadvantage (roll twice, keep lower).

        Args:
            sides: Number of sides (default 20 for d20).
            modifier: Value to add to the result.

        Returns:
            DiceRollResult with both rolls and kept value.
        """
        self._ensure_active()
        self._increment_call_count()

        rolls = [self._rng.randint(1, sides) for _ in range(2)]
        kept = min(rolls)
        total = kept + modifier

        result = DiceRollResult(
            notation=f"2d{sides} disadvantage",
            dice=[(2, sides)],
            rolls=[rolls],
            modifiers=modifier,
            total=total,
            min_possible=1 + modifier,
            max_possible=sides + modifier,
            kept_rolls=[kept],
        )

        self._add_to_history(result)
        return result

    # ==================== Statistics ====================

    def statistics(
        self,
        count: int = 1,
        sides: int = 6,
        modifier: int = 0,
    ) -> Dict[str, Any]:
        """Calculate dice statistics.

        Args:
            count: Number of dice.
            sides: Number of sides per die.
            modifier: Modifier value.

        Returns:
            Dict with statistics (mean, min, max, variance, etc.).
        """
        self._ensure_active()
        self._increment_call_count()

        if count < 1 or sides < 2:
            raise ValueError("Invalid dice parameters")

        min_val = count + modifier
        max_val = count * sides + modifier
        mean = count * (sides + 1) / 2 + modifier
        variance = count * (sides * sides - 1) / 12
        std_dev = variance ** 0.5

        # Probability distribution (for small dice counts)
        distribution: Dict[int, float] = {}
        if count * sides <= 200:
            # Use dynamic programming for exact distribution
            probs = [0.0] * (count * sides + 1)
            for i in range(1, sides + 1):
                probs[i] = 1.0 / sides

            for die in range(1, count):
                new_probs = [0.0] * (count * sides + 1)
                for total in range(die, die * sides + 1):
                    if probs[total] > 0:
                        for face in range(1, sides + 1):
                            new_probs[total + face] += probs[total] / sides
                probs = new_probs

            for total in range(count, count * sides + 1):
                distribution[total + modifier] = round(probs[total], 10)

        # Most common values
        if distribution:
            sorted_dist = sorted(distribution.items(), key=lambda x: -x[1])
            most_common = sorted_dist[:5]
        else:
            most_common = []

        return {
            "dice": f"{count}d{sides}",
            "modifier": modifier,
            "min": min_val,
            "max": max_val,
            "range": max_val - min_val + 1,
            "mean": round(mean, 4),
            "median": mean,  # Symmetric distribution
            "variance": round(variance, 4),
            "standard_deviation": round(std_dev, 4),
            "expected_value": round(mean, 4),
            "distribution": distribution if distribution else "too_large",
            "most_common": most_common,
            "prob_at_least": self._prob_at_least(distribution, min_val, max_val)
            if distribution else {},
        }

    def _prob_at_least(
        self, distribution: Dict[int, float], min_val: int, max_val: int
    ) -> Dict[int, float]:
        """Calculate probability of rolling at least each value."""
        result: Dict[int, float] = {}
        for target in range(min_val, max_val + 1):
            prob = sum(p for v, p in distribution.items() if v >= target)
            result[target] = round(prob, 6)
        return result

    # ==================== Notation Parsing ====================

    def parse_notation(self, notation: str) -> Dict[str, Any]:
        """Parse dice notation without rolling.

        Args:
            notation: Dice notation string.

        Returns:
            Dict with parsed components.
        """
        self._ensure_active()
        self._increment_call_count()

        dice_groups, modifier = self._parse_notation(notation)

        return {
            "notation": notation,
            "dice_groups": [
                {"count": c, "sides": s} for c, s in dice_groups
            ],
            "modifier": modifier,
            "min_possible": sum(c for c, _ in dice_groups) + modifier,
            "max_possible": sum(c * s for c, s in dice_groups) + modifier,
            "total_dice": sum(c for c, _ in dice_groups),
            "is_valid": len(dice_groups) > 0,
        }

    def _parse_notation(self, notation: str) -> Tuple[List[Tuple[int, int]], int]:
        """Parse dice notation into dice groups and modifier.

        Returns:
            Tuple of (dice_groups, modifier).
        """
        notation = notation.strip().lower()
        dice_groups: List[Tuple[int, int]] = []
        modifier = 0

        if not notation:
            return dice_groups, modifier

        # Find all dice patterns and modifiers
        # Pattern: optional sign, optional count, 'd', sides
        pattern = r'([+-]?)(\d*)d(\d+)'
        matches = list(re.finditer(pattern, notation))

        if not matches:
            raise ValueError(f"Invalid dice notation: {notation}")

        last_end = 0
        for match in matches:
            sign = match.group(1) or '+'
            count_str = match.group(2)
            sides_str = match.group(3)

            count = int(count_str) if count_str else 1
            sides = int(sides_str)

            if sign == '-':
                count = -count

            if sides < 2:
                raise ValueError(f"Invalid die sides: d{sides}")

            dice_groups.append((abs(count), sides))

            if count < 0:
                # Negative dice count: subtract from total
                # We'll handle this by storing negative count
                dice_groups[-1] = (count, sides)

            last_end = match.end()

        # Parse flat modifier from the end
        remaining = notation[last_end:].strip()
        if remaining:
            try:
                modifier = int(remaining)
            except ValueError:
                # Check for remaining +/- patterns
                mod_match = re.match(r'([+-]\d+)', remaining)
                if mod_match:
                    modifier = int(mod_match.group(1))

        # Handle negative dice (subtractive dice groups)
        positive_groups = [(c, s) for c, s in dice_groups if c > 0]
        # For simplicity, treat negative count as modifier adjustment
        neg_adjustment = sum(c * s for c, s in dice_groups if c < 0)
        # Actually, negative dice don't make sense mathematically the same way
        # Let's just use positive groups
        dice_groups = [(abs(c), s) for c, s in dice_groups if c > 0]
        modifier += neg_adjustment  # This is a simplification

        return dice_groups, modifier

    # ==================== History ====================

    def roll_history(self, limit: int = 10) -> List[Dict[str, Any]]:
        """Get recent roll history.

        Args:
            limit: Maximum number of rolls to return.

        Returns:
            List of roll result summaries.
        """
        self._ensure_active()
        self._increment_call_count()

        recent = self._history[-limit:] if limit > 0 else []
        return [
            {
                "notation": r.notation,
                "total": r.total,
                "rolls": r.rolls,
                "modifier": r.modifiers,
            }
            for r in reversed(recent)
        ]

    def clear_history(self) -> int:
        """Clear the roll history.

        Returns:
            Number of rolls cleared.
        """
        count = len(self._history)
        self._history.clear()
        return count

    def _add_to_history(self, result: DiceRollResult) -> None:
        """Add a roll result to history."""
        self._history.append(result)
        if len(self._history) > self._max_history:
            self._history.pop(0)

    # ==================== Statistics ====================

    def get_stats(self) -> Dict[str, Any]:
        """Get plugin statistics."""
        return {
            "standard_dice": self.STANDARD_DICE,
            "history_size": len(self._history),
            "max_history": self._max_history,
            "total_rolls": self._call_count,
        }
