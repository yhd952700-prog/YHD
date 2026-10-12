"""
Poker Hand Evaluator Plugin for LiuHao AI OS

Provides poker hand ranking, best hand calculation, hand comparison,
deck management, card dealing, and odds calculation interface.

Capabilities:
- evaluate_hand: Evaluate a 5-card poker hand
- best_hand: Find best 5-card hand from 5+ cards
- compare_hands: Compare two poker hands
- create_deck: Create a standard deck
- shuffle_deck: Shuffle a deck
- deal_cards: Deal cards from a deck
- hand_rank_name: Get name of a hand rank
"""

import random
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
class Card:
    """A playing card."""
    rank: int  # 2-14 (2=2, 11=J, 12=Q, 13=K, 14=A)
    suit: str  # 'S', 'H', 'D', 'C'

    def __str__(self) -> str:
        rank_names = {11: 'J', 12: 'Q', 13: 'K', 14: 'A'}
        r = rank_names.get(self.rank, str(self.rank))
        return f"{r}{self.suit}"

    def __repr__(self) -> str:
        return self.__str__()


@dataclass
class PokerHand:
    """Result of a poker hand evaluation."""
    cards: List[Card]
    rank: int  # 0-9 (higher is better)
    rank_name: str
    tie_breakers: List[int]  # Values for tie-breaking
    description: str


class PokerPlugin(BasePlugin):
    """Poker hand evaluation and deck management plugin.

    Supports standard 5-card poker hand evaluation, best hand
    calculation from 5+ cards (Texas Hold'em style), hand comparison,
    deck management, and card dealing.

    Example:
        plugin = PokerPlugin()
        plugin.initialize()
        plugin.activate()

        deck = plugin.create_deck()
        hand = plugin.deal_cards(deck, 5)
        result = plugin.evaluate_hand(hand)
        print(result.rank_name)
    """

    # Hand ranks (higher = better)
    HIGH_CARD = 0
    ONE_PAIR = 1
    TWO_PAIR = 2
    THREE_OF_A_KIND = 3
    STRAIGHT = 4
    FLUSH = 5
    FULL_HOUSE = 6
    FOUR_OF_A_KIND = 7
    STRAIGHT_FLUSH = 8
    ROYAL_FLUSH = 9

    RANK_NAMES = {
        0: "High Card",
        1: "One Pair",
        2: "Two Pair",
        3: "Three of a Kind",
        4: "Straight",
        5: "Flush",
        6: "Full House",
        7: "Four of a Kind",
        8: "Straight Flush",
        9: "Royal Flush",
    }

    SUITS = ['S', 'H', 'D', 'C']
    RANKS = list(range(2, 15))  # 2 through Ace

    def __init__(self) -> None:
        """Initialize the poker plugin."""
        metadata = PluginMetadata(
            name="poker",
            version="1.0.0",
            description="Poker hand evaluation, deck management, and odds calculation",
            author="LiuHao AI OS Team",
            categories=["games", "cards", "utility"],
            keywords=["poker", "cards", "hand", "deck", "texas holdem", "casino"],
            requires_internet=False,
            dependencies=[],
        )
        super().__init__(metadata)

        self._rng = random.Random()

    # ==================== Lifecycle Methods ====================

    def initialize(self, config: Optional[Dict[str, Any]] = None) -> bool:
        """Initialize the poker plugin."""
        try:
            if config:
                seed = config.get("seed")
                if seed is not None:
                    self._rng.seed(seed)

            self._set_status(PluginStatus.INITIALIZED)
            return True
        except Exception as e:
            self._set_error(f"Initialization failed: {e}")
            return False

    def activate(self) -> bool:
        """Activate the poker plugin."""
        try:
            self._set_status(PluginStatus.ACTIVE)
            return True
        except Exception as e:
            self._set_error(f"Activation failed: {e}")
            return False

    def deactivate(self) -> bool:
        """Deactivate the poker plugin."""
        try:
            self._set_status(PluginStatus.INACTIVE)
            return True
        except Exception as e:
            self._set_error(str(e))
            return False

    def shutdown(self) -> bool:
        """Shut down the poker plugin."""
        try:
            self._set_status(PluginStatus.SHUTDOWN)
            return True
        except Exception as e:
            self._set_error(str(e))
            return False

    def get_capabilities(self) -> List[str]:
        """Return the list of capabilities."""
        return [
            "evaluate_hand",
            "best_hand",
            "compare_hands",
            "create_deck",
            "shuffle_deck",
            "deal_cards",
            "hand_rank_name",
            "parse_card",
        ]

    # ==================== Card Parsing ====================

    def parse_card(self, card_str: str) -> Card:
        """Parse a card string into a Card object.

        Args:
            card_str: Card string like 'As', '10h', 'Kd', '2c'.

        Returns:
            Card object.

        Raises:
            ValueError: If card string is invalid.
        """
        self._ensure_active()
        self._increment_call_count()

        card_str = card_str.strip()
        if len(card_str) < 2:
            raise ValueError(f"Invalid card: {card_str}")

        suit = card_str[-1].upper()
        rank_str = card_str[:-1].upper()

        if suit not in self.SUITS:
            raise ValueError(f"Invalid suit: {suit}")

        rank_map = {'J': 11, 'Q': 12, 'K': 13, 'A': 14, 'T': 10}
        if rank_str in rank_map:
            rank = rank_map[rank_str]
        else:
            try:
                rank = int(rank_str)
            except ValueError:
                raise ValueError(f"Invalid rank: {rank_str}")

        if rank < 2 or rank > 14:
            raise ValueError(f"Rank out of range: {rank}")

        return Card(rank=rank, suit=suit)

    # ==================== Deck Management ====================

    def create_deck(self) -> List[Card]:
        """Create a standard 52-card deck.

        Returns:
            List of Card objects in order.
        """
        self._ensure_active()
        self._increment_call_count()

        deck = []
        for suit in self.SUITS:
            for rank in self.RANKS:
                deck.append(Card(rank=rank, suit=suit))
        return deck

    def shuffle_deck(self, deck: List[Card]) -> List[Card]:
        """Shuffle a deck of cards.

        Args:
            deck: List of Card objects.

        Returns:
            Shuffled list of cards (new list).
        """
        self._ensure_active()
        self._increment_call_count()

        shuffled = list(deck)
        self._rng.shuffle(shuffled)
        return shuffled

    def deal_cards(self, deck: List[Card], count: int) -> List[Card]:
        """Deal cards from the top of the deck.

        Removes the dealt cards from the deck.

        Args:
            deck: Deck to deal from (modified in place).
            count: Number of cards to deal.

        Returns:
            List of dealt Card objects.
        """
        self._ensure_active()
        self._increment_call_count()

        if count > len(deck):
            raise ValueError(
                f"Cannot deal {count} cards from deck of {len(deck)}"
            )

        dealt = deck[:count]
        del deck[:count]
        return dealt

    # ==================== Hand Evaluation ====================

    def evaluate_hand(self, cards: List[Card]) -> PokerHand:
        """Evaluate a 5-card poker hand.

        Args:
            cards: List of exactly 5 Card objects.

        Returns:
            PokerHand with rank, name, and tie-breakers.

        Raises:
            ValueError: If not exactly 5 cards.
        """
        self._ensure_active()
        self._increment_call_count()

        if len(cards) != 5:
            raise ValueError("Poker hand must have exactly 5 cards")

        ranks = sorted([c.rank for c in cards], reverse=True)
        suits = [c.suit for c in cards]
        rank_counts = Counter(ranks)
        suit_counts = Counter(suits)

        # Check for flush
        is_flush = len(suit_counts) == 1

        # Check for straight
        is_straight, high_card = self._check_straight(ranks)

        # Royal Flush
        if is_flush and is_straight and high_card == 14:
            return PokerHand(
                cards=cards,
                rank=self.ROYAL_FLUSH,
                rank_name=self.RANK_NAMES[self.ROYAL_FLUSH],
                tie_breakers=[14],
                description="Royal Flush",
            )

        # Straight Flush
        if is_flush and is_straight:
            return PokerHand(
                cards=cards,
                rank=self.STRAIGHT_FLUSH,
                rank_name=self.RANK_NAMES[self.STRAIGHT_FLUSH],
                tie_breakers=[high_card],
                description=f"Straight Flush, {self._rank_name(high_card)} high",
            )

        # Four of a Kind
        fours = [r for r, c in rank_counts.items() if c == 4]
        if fours:
            four_rank = fours[0]
            kicker = [r for r in ranks if r != four_rank][0]
            return PokerHand(
                cards=cards,
                rank=self.FOUR_OF_A_KIND,
                rank_name=self.RANK_NAMES[self.FOUR_OF_A_KIND],
                tie_breakers=[four_rank, kicker],
                description=f"Four of a Kind, {self._rank_name(four_rank)}s",
            )

        # Full House
        threes = [r for r, c in rank_counts.items() if c == 3]
        pairs = [r for r, c in rank_counts.items() if c == 2]
        if threes and pairs:
            three_rank = threes[0]
            pair_rank = pairs[0]
            return PokerHand(
                cards=cards,
                rank=self.FULL_HOUSE,
                rank_name=self.RANK_NAMES[self.FULL_HOUSE],
                tie_breakers=[three_rank, pair_rank],
                description=(
                    f"Full House, {self._rank_name(three_rank)}s "
                    f"full of {self._rank_name(pair_rank)}s"
                ),
            )

        # Flush
        if is_flush:
            return PokerHand(
                cards=cards,
                rank=self.FLUSH,
                rank_name=self.RANK_NAMES[self.FLUSH],
                tie_breakers=list(ranks),
                description=f"Flush, {self._rank_name(ranks[0])} high",
            )

        # Straight
        if is_straight:
            return PokerHand(
                cards=cards,
                rank=self.STRAIGHT,
                rank_name=self.RANK_NAMES[self.STRAIGHT],
                tie_breakers=[high_card],
                description=f"Straight, {self._rank_name(high_card)} high",
            )

        # Three of a Kind
        if threes:
            three_rank = threes[0]
            kickers = sorted([r for r in ranks if r != three_rank], reverse=True)
            return PokerHand(
                cards=cards,
                rank=self.THREE_OF_A_KIND,
                rank_name=self.RANK_NAMES[self.THREE_OF_A_KIND],
                tie_breakers=[three_rank] + kickers,
                description=f"Three of a Kind, {self._rank_name(three_rank)}s",
            )

        # Two Pair
        if len(pairs) >= 2:
            pair_ranks = sorted(pairs, reverse=True)
            kicker = [r for r in ranks if r not in pair_ranks][0]
            return PokerHand(
                cards=cards,
                rank=self.TWO_PAIR,
                rank_name=self.RANK_NAMES[self.TWO_PAIR],
                tie_breakers=pair_ranks + [kicker],
                description=(
                    f"Two Pair, {self._rank_name(pair_ranks[0])}s and "
                    f"{self._rank_name(pair_ranks[1])}s"
                ),
            )

        # One Pair
        if pairs:
            pair_rank = pairs[0]
            kickers = sorted([r for r in ranks if r != pair_rank], reverse=True)
            return PokerHand(
                cards=cards,
                rank=self.ONE_PAIR,
                rank_name=self.RANK_NAMES[self.ONE_PAIR],
                tie_breakers=[pair_rank] + kickers,
                description=f"One Pair, {self._rank_name(pair_rank)}s",
            )

        # High Card
        return PokerHand(
            cards=cards,
            rank=self.HIGH_CARD,
            rank_name=self.RANK_NAMES[self.HIGH_CARD],
            tie_breakers=list(ranks),
            description=f"High Card, {self._rank_name(ranks[0])}",
        )

    def _check_straight(self, ranks: List[int]) -> Tuple[bool, int]:
        """Check if ranks form a straight.

        Returns:
            Tuple of (is_straight, high_card).
        """
        unique_ranks = sorted(set(ranks), reverse=True)

        if len(unique_ranks) != 5:
            return False, 0

        # Normal straight
        if unique_ranks[0] - unique_ranks[4] == 4:
            return True, unique_ranks[0]

        # Wheel straight: A-2-3-4-5 (Ace low)
        if unique_ranks == [14, 5, 4, 3, 2]:
            return True, 5

        return False, 0

    def _rank_name(self, rank: int) -> str:
        """Get display name for a rank."""
        names = {11: 'Jack', 12: 'Queen', 13: 'King', 14: 'Ace'}
        return names.get(rank, str(rank))

    # ==================== Best Hand ====================

    def best_hand(self, cards: List[Card]) -> PokerHand:
        """Find the best 5-card hand from 5+ cards.

        Uses combinatorial evaluation for Texas Hold'em style
        (7 cards, best 5).

        Args:
            cards: List of 5+ Card objects.

        Returns:
            Best PokerHand.
        """
        self._ensure_active()
        self._increment_call_count()

        if len(cards) < 5:
            raise ValueError("Need at least 5 cards to find best hand")

        if len(cards) == 5:
            return self.evaluate_hand(cards)

        best = None
        n = len(cards)

        # Generate all 5-card combinations
        from itertools import combinations
        for combo in combinations(range(n), 5):
            hand_cards = [cards[i] for i in combo]
            result = self.evaluate_hand(hand_cards)

            if best is None or self._compare_hand_results(result, best) > 0:
                best = result

        return best

    # ==================== Hand Comparison ====================

    def compare_hands(self, hand1: List[Card], hand2: List[Card]) -> int:
        """Compare two poker hands.

        Args:
            hand1: First 5-card hand.
            hand2: Second 5-card hand.

        Returns:
            1 if hand1 wins, -1 if hand2 wins, 0 if tie.
        """
        self._ensure_active()
        self._increment_call_count()

        eval1 = self.evaluate_hand(hand1)
        eval2 = self.evaluate_hand(hand2)

        return self._compare_hand_results(eval1, eval2)

    def _compare_hand_results(self, h1: PokerHand, h2: PokerHand) -> int:
        """Compare two evaluated poker hands."""
        if h1.rank > h2.rank:
            return 1
        elif h1.rank < h2.rank:
            return -1

        # Same rank, compare tie-breakers
        for tb1, tb2 in zip(h1.tie_breakers, h2.tie_breakers):
            if tb1 > tb2:
                return 1
            elif tb1 < tb2:
                return -1

        return 0

    def hand_rank_name(self, rank: int) -> str:
        """Get the name of a hand rank.

        Args:
            rank: Rank value (0-9).

        Returns:
            Name of the hand rank.
        """
        self._ensure_active()
        self._increment_call_count()

        return self.RANK_NAMES.get(rank, "Unknown")

    # ==================== Odds Calculation Interface ====================

    def calculate_odds(
        self,
        hole_cards: List[Card],
        community_cards: List[Card],
        num_players: int = 2,
    ) -> Dict[str, Any]:
        """Calculate approximate winning odds (simplified).

        Provides a rough estimate based on hand strength evaluation.
        Full Monte Carlo simulation would be more accurate.

        Args:
            hole_cards: Player's hole cards.
            community_cards: Community cards (0-5).
            num_players: Number of players in the hand.

        Returns:
            Dict with odds estimation.
        """
        self._ensure_active()
        self._increment_call_count()

        all_cards = hole_cards + community_cards
        known_ranks = [c.rank for c in all_cards]
        known_suits = [c.suit for c in all_cards]

        result = {
            "current_best": None,
            "draw_potential": {
                "flush_draw": False,
                "straight_draw": False,
                "open_ended_straight_draw": False,
            },
            "estimated_win_rate": 0.0,
            "num_players": num_players,
        }

        # Evaluate current best hand
        if len(all_cards) >= 5:
            best = self.best_hand(all_cards)
            result["current_best"] = {
                "rank": best.rank,
                "rank_name": best.rank_name,
                "description": best.description,
            }

        # Check flush draw
        suit_counts = Counter(known_suits)
        for suit, count in suit_counts.items():
            if count >= 4:
                result["draw_potential"]["flush_draw"] = True
                result["draw_potential"]["flush_suit"] = suit
                break

        # Check straight draw potential
        if len(known_ranks) >= 4:
            unique_ranks = sorted(set(known_ranks))
            # Simple check for 4 consecutive ranks
            for i in range(len(unique_ranks) - 3):
                if unique_ranks[i + 3] - unique_ranks[i] == 3:
                    result["draw_potential"]["straight_draw"] = True
                    result["draw_potential"]["open_ended_straight_draw"] = True
                    break

        # Rough win rate estimate
        if len(all_cards) >= 5:
            best = self.best_hand(all_cards)
            # Base on hand rank
            rank_scores = {
                0: 0.10,  # High card
                1: 0.25,  # One pair
                2: 0.45,  # Two pair
                3: 0.60,  # Three of a kind
                4: 0.70,  # Straight
                5: 0.75,  # Flush
                6: 0.85,  # Full house
                7: 0.92,  # Four of a kind
                8: 0.98,  # Straight flush
                9: 0.999,  # Royal flush
            }
            base = rank_scores.get(best.rank, 0.1)
            # Adjust for number of players
            result["estimated_win_rate"] = round(
                base ** (num_players - 1), 4
            )

        return result

    # ==================== Statistics ====================

    def get_stats(self) -> Dict[str, Any]:
        """Get plugin statistics."""
        return {
            "deck_size": 52,
            "num_suits": 4,
            "num_ranks": 13,
            "hand_ranks": len(self.RANK_NAMES),
            "rank_names": self.RANK_NAMES,
            "suits": self.SUITS,
        }
