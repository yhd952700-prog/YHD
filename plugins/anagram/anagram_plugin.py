"""
Anagram Solver Plugin for LiuHao AI OS

Provides anagram solving, generation, phrase anagrams,
anagram checking, Scrabble-style word finding, and word validation.

Capabilities:
- find_anagrams: Find anagrams of a word
- is_anagram: Check if two words are anagrams
- generate_anagrams: Generate all possible anagrams
- phrase_anagrams: Find anagram phrases
- scrabble_words: Scrabble-style word finder
- word_validation: Validate against word list
"""

from typing import Dict, Any, Optional, List, Tuple, Set
from collections import Counter
from itertools import permutations
from dataclasses import dataclass, field

from ..plugin_base import (
    BasePlugin,
    PluginMetadata,
    PluginStatus,
    PluginExecutionError,
)


@dataclass
class AnagramResult:
    """Result of an anagram search."""
    input: str
    anagrams: List[str]
    count: int
    by_length: Dict[int, List[str]]


class AnagramPlugin(BasePlugin):
    """Anagram solver and generator plugin.

    Supports finding anagrams from a built-in word list,
    checking if words are anagrams, generating all permutations,
    phrase anagrams, and Scrabble-style word finding.

    Example:
        plugin = AnagramPlugin()
        plugin.initialize()
        plugin.activate()

        result = plugin.find_anagrams("listen")
        print(result.anagrams)  # ["silent", "enlist", ...]
    """

    # Built-in word list (common English words)
    WORD_LIST: List[str] = [
        # 3-letter words
        "the", "and", "for", "are", "but", "not", "you", "all", "can", "had",
        "her", "was", "one", "our", "out", "day", "get", "has", "him", "his",
        "how", "its", "let", "may", "new", "now", "old", "see", "two", "way",
        "who", "boy", "did", "own", "say", "she", "too", "use", "cat", "dog",
        "red", "top", "run", "sit", "sun", "car", "bed", "cup", "egg", "fan",
        "hat", "jar", "key", "map", "net", "pen", "pot", "rat", "tea", "van",
        # 4-letter words
        "that", "with", "have", "this", "will", "your", "from", "they", "know",
        "want", "been", "good", "much", "some", "time", "very", "when", "come",
        "just", "like", "long", "look", "make", "many", "more", "only", "over",
        "such", "take", "than", "them", "then", "well", "were", "what", "work",
        "also", "back", "call", "came", "down", "each", "find", "first", "give",
        "hand", "here", "into", "last", "life", "line", "name", "need", "part",
        "place", "read", "same", "tell", "word", "world", "year", "acid", "aide",
        "angel", "anger", "apple", "armed", "avoid", "beach", "blank", "blast",
        "board", "bonus", "brain", "brand", "bread", "break", "brick", "bring",
        "broad", "brush", "build", "bunch", "burst", "cabin", "cable", "camel",
        "candy", "carry", "catch", "cause", "chain", "chair", "chalk", "champ",
        "cheap", "check", "chest", "chief", "child", "chill", "china", "chose",
        "civil", "claim", "class", "clean", "clear", "clerk", "cliff", "climb",
        "clock", "close", "cloth", "cloud", "coach", "coast", "could", "count",
        "court", "cover", "crash", "cream", "crime", "cross", "crowd", "crown",
        "curve", "cycle", "daily", "dance", "dated", "dealt", "death", "debug",
        "decay", "delay", "depth", "diary", "dirty", "doubt", "dozen", "draft",
        "drain", "drama", "drawn", "dream", "dress", "drill", "drink", "drive",
        "earth", "eight", "elder", "elect", "elite", "empty", "enemy", "enjoy",
        "enter", "entry", "equal", "error", "event", "every", "exact", "exile",
        "exist", "extra", "faith", "false", "fault", "favor", "feast", "fence",
        "fiber", "field", "fifth", "fifty", "fight", "final", "first", "fixed",
        "flame", "flash", "fleet", "flesh", "float", "flood", "floor", "flour",
        "fluid", "focus", "force", "forge", "forth", "forty", "forum", "found",
        "frame", "frank", "fraud", "fresh", "front", "fruit", "fully", "funny",
        "giant", "given", "glass", "globe", "grace", "grade", "grain", "grand",
        "grant", "grass", "grave", "great", "green", "grief", "grind", "gross",
        "group", "grown", "guard", "guess", "guest", "guide", "guild", "guilt",
        "habit", "hairy", "hands", "happy", "harsh", "haste", "heart", "heavy",
        "hence", "horse", "hotel", "human", "humor", "hurry", "ideal", "image",
        "imply", "index", "inner", "input", "issue", "jeans", "joint", "jones",
        "judge", "juice", "known", "label", "labor", "large", "laser", "later",
        "laugh", "layer", "learn", "lease", "least", "leave", "legal", "level",
        "lever", "light", "limit", "links", "lives", "local", "logic", "loose",
        "lower", "lucky", "lunch", "lying", "magic", "major", "maker", "march",
        "maria", "match", "maybe", "mayor", "meant", "media", "metal", "might",
        "minor", "minus", "mixed", "model", "money", "month", "moral", "motor",
        "mount", "mouse", "mouth", "movie", "music", "needs", "never", "night",
        "noise", "north", "noted", "novel", "nurse", "occur", "ocean", "offer",
        "often", "order", "other", "ought", "paint", "panel", "paper", "party",
        "peace", "peter", "phase", "phone", "photo", "piece", "pilot", "pitch",
        "place", "plain", "plane", "plant", "plate", "point", "pound", "power",
        "press", "price", "pride", "prime", "print", "prior", "prize", "proof",
        "proud", "prove", "queen", "quick", "quiet", "quite", "radio", "raise",
        "range", "rapid", "ratio", "reach", "ready", "realty", "rebel", "refer",
        "relax", "reply", "right", "rival", "river", "robin", "robot", "rocky",
        "rogue", "roman", "rough", "round", "route", "royal", "rural", "scale",
        "scene", "scope", "score", "sense", "serve", "seven", "shade", "shake",
        "shall", "shape", "share", "sharp", "sheep", "sheer", "sheet", "shelf",
        "shell", "shift", "shirt", "shock", "shoot", "short", "shown", "sight",
        "sigma", "since", "sixth", "sixty", "sized", "skill", "sleep", "slide",
        "slope", "small", "smart", "smell", "smile", "smoke", "solid", "solve",
        "sorry", "sound", "south", "space", "spare", "speak", "speed", "spell",
        "spend", "spent", "split", "spoke", "sport", "staff", "stage", "stake",
        "stand", "start", "state", "steam", "steel", "stick", "still", "stock",
        "stone", "stood", "store", "storm", "story", "strip", "stuck", "study",
        "stuff", "style", "sugar", "suite", "super", "sweet", "sword", "table",
        "taken", "taste", "taxes", "teach", "teeth", "terry", "texas", "thank",
        "theft", "their", "theme", "there", "thick", "thing", "think", "third",
        "those", "three", "threw", "throw", "tight", "times", "tired", "title",
        "today", "token", "topic", "total", "touch", "tough", "tower", "track",
        "trade", "train", "treat", "trend", "trial", "tribe", "trick", "tried",
        "tries", "truck", "truly", "trust", "truth", "twice", "under", "undue",
        "union", "unity", "until", "upper", "upset", "urban", "usage", "usual",
        "valid", "value", "video", "virus", "visit", "vital", "voice", "waste",
        "watch", "water", "wheel", "where", "which", "while", "white", "whole",
        "whose", "woman", "women", "world", "worry", "worse", "worst", "worth",
        "would", "wound", "write", "wrong", "wrote", "yield", "young", "youth",
        # 5-7 letter words commonly used
        "listen", "silent", "enlist", "tinsel", "inlets", "stencil",
        "earth", "heart", "hater", "rathe", "hearth",
        "angel", "angle", "galen", "glean", "lange",
        "cider", "cried", "dicers",
        "below", "bowel", "elbow", "bowle",
        "study", "dusty", "dutys",
        "bread", "beard", "bared", "debar", "badger",
        "saint", "stain", "satin", "stain",
        "pale", "leap", "plea", "peal",
        "lake", "kale", "leak", "bake",
        "item", "time", "mite", "emit",
        "taste", "state", "teats", "east",
        "melon", "lemon", "menlo",
        "steak", "skate", "stake", "takes",
        "actor", "catar", "croat",
        "cabin", "basic",
        "cheap", "peach", "cache",
        "table", "bleat", "balet",
        "night", "thing",
        "panel", "plane", "penal", "nepal",
        "piano", "piano", "opina",
        "point", "pinto", "piton",
        "quiet", "quite", "quilt",
        "resort", "roster",
        "singer", "resign", "signer",
        "smile", "slime", "miles",
        "snake", "sneak", "sanek",
        "spare", "parse", "spear", "pears", "rapes",
        "speak", "skape", "peaks",
        "steal", "slate", "stale", "tales", "teals", "least",
        "stone", "tones", "notes", "onset", "steno",
        "store", "rote", "tores",
        "table", "bleat", "belta",
        "trail", "trial", "liart",
        "tread", "trade", "rated", "derat",
        "voter", "vetor", "rotev",
        "water", "wreat", "tawer",
    ]

    def __init__(self) -> None:
        """Initialize the anagram plugin."""
        metadata = PluginMetadata(
            name="anagram",
            version="1.0.0",
            description="Anagram solver, generator, phrase anagrams, and Scrabble word finder",
            author="LiuHao AI OS Team",
            categories=["word", "game", "utility"],
            keywords=["anagram", "word", "scrabble", "puzzle", "permutation", "solver"],
            requires_internet=False,
            dependencies=[],
        )
        super().__init__(metadata)

        self._word_set: Set[str] = set()
        self._anagram_index: Dict[str, List[str]] = {}

    # ==================== Lifecycle Methods ====================

    def initialize(self, config: Optional[Dict[str, Any]] = None) -> bool:
        """Initialize the anagram plugin."""
        try:
            # Build word set and anagram index
            self._word_set = set(w.lower() for w in self.WORD_LIST)

            # Build index: sorted_letters -> list of words
            self._anagram_index = {}
            for word in self._word_set:
                key = ''.join(sorted(word))
                if key not in self._anagram_index:
                    self._anagram_index[key] = []
                self._anagram_index[key].append(word)

            self._set_status(PluginStatus.INITIALIZED)
            return True
        except Exception as e:
            self._set_error(f"Initialization failed: {e}")
            return False

    def activate(self) -> bool:
        """Activate the anagram plugin."""
        try:
            self._set_status(PluginStatus.ACTIVE)
            return True
        except Exception as e:
            self._set_error(f"Activation failed: {e}")
            return False

    def deactivate(self) -> bool:
        """Deactivate the anagram plugin."""
        try:
            self._set_status(PluginStatus.INACTIVE)
            return True
        except Exception as e:
            self._set_error(str(e))
            return False

    def shutdown(self) -> bool:
        """Shut down the anagram plugin."""
        try:
            self._set_status(PluginStatus.SHUTDOWN)
            return True
        except Exception as e:
            self._set_error(str(e))
            return False

    def get_capabilities(self) -> List[str]:
        """Return the list of capabilities."""
        return [
            "find_anagrams",
            "is_anagram",
            "generate_anagrams",
            "phrase_anagrams",
            "scrabble_words",
            "validate_word",
            "get_word_list",
        ]

    # ==================== Anagram Finding ====================

    def find_anagrams(
        self,
        word: str,
        case_sensitive: bool = False,
        include_self: bool = False,
    ) -> AnagramResult:
        """Find anagrams of a word from the built-in word list.

        Args:
            word: Word to find anagrams of.
            case_sensitive: Whether to preserve case.
            include_self: Include the input word in results.

        Returns:
            AnagramResult with matching anagrams.
        """
        self._ensure_active()
        self._increment_call_count()

        clean_word = word.strip()
        key = ''.join(sorted(clean_word.lower()))

        anagrams = self._anagram_index.get(key, [])

        if not include_self:
            anagrams = [a for a in anagrams if a != clean_word.lower()]

        # Group by length
        by_length: Dict[int, List[str]] = {}
        for a in anagrams:
            ln = len(a)
            if ln not in by_length:
                by_length[ln] = []
            by_length[ln].append(a)

        # Sort
        anagrams.sort()
        for ln in by_length:
            by_length[ln].sort()

        # Handle case
        if case_sensitive and word and word[0].isupper():
            anagrams = [a.capitalize() for a in anagrams]
            by_length = {
                k: [w.capitalize() for w in v]
                for k, v in by_length.items()
            }

        return AnagramResult(
            input=word,
            anagrams=anagrams,
            count=len(anagrams),
            by_length=by_length,
        )

    def is_anagram(
        self,
        word1: str,
        word2: str,
        case_sensitive: bool = False,
        ignore_spaces: bool = True,
    ) -> bool:
        """Check if two words are anagrams of each other.

        Args:
            word1: First word.
            word2: Second word.
            case_sensitive: Whether to consider case.
            ignore_spaces: Whether to ignore spaces.

        Returns:
            True if the words are anagrams.
        """
        self._ensure_active()
        self._increment_call_count()

        w1 = word1
        w2 = word2

        if ignore_spaces:
            w1 = w1.replace(' ', '')
            w2 = w2.replace(' ', '')

        if not case_sensitive:
            w1 = w1.lower()
            w2 = w2.lower()

        if len(w1) != len(w2):
            return False

        return sorted(w1) == sorted(w2)

    # ==================== Anagram Generation ====================

    def generate_anagrams(
        self,
        word: str,
        max_results: int = 100,
        unique_only: bool = True,
    ) -> List[str]:
        """Generate all possible anagram permutations of a word.

        Note: For words with duplicate letters, unique permutations
        will be fewer than n!.

        Args:
            word: Word to generate anagrams from.
            max_results: Maximum number of results.
            unique_only: Return only unique permutations.

        Returns:
            List of anagram strings.
        """
        self._ensure_active()
        self._increment_call_count()

        if len(word) > 10:
            raise ValueError(
                f"Word too long ({len(word)} chars). "
                f"Generation may be slow for words over 10 characters."
            )

        perms = set() if unique_only else []

        for i, perm in enumerate(permutations(word)):
            if i >= max_results:
                break
            anagram = ''.join(perm)
            if unique_only:
                perms.add(anagram)
            else:
                perms.append(anagram)

        result = list(perms) if unique_only else perms
        result.sort()
        return result[:max_results]

    # ==================== Phrase Anagrams ====================

    def phrase_anagrams(
        self,
        phrase: str,
        max_words: int = 3,
        max_results: int = 20,
    ) -> List[str]:
        """Find multi-word anagram phrases.

        Finds combinations of words from the word list that
        use all letters of the input phrase.

        Args:
            phrase: Phrase to find anagrams of.
            max_words: Maximum number of words in result.
            max_results: Maximum number of results.

        Returns:
            List of anagram phrases.
        """
        self._ensure_active()
        self._increment_call_count()

        # Normalize input
        letters = phrase.lower().replace(' ', '')
        target_counter = Counter(letters)

        results: List[str] = []

        # Find all words that can be made from the letters
        usable_words = []
        for word in self._word_set:
            word_counter = Counter(word)
            if all(word_counter[c] <= target_counter[c] for c in word_counter):
                usable_words.append(word)

        # Sort by length descending for better results
        usable_words.sort(key=len, reverse=True)

        # Simple recursive search for phrase anagrams
        def search(
            remaining: Counter,
            current: List[str],
            start_idx: int,
        ) -> None:
            if len(results) >= max_results:
                return

            if sum(remaining.values()) == 0:
                results.append(' '.join(sorted(current)))
                return

            if len(current) >= max_words:
                return

            for i in range(start_idx, len(usable_words)):
                word = usable_words[i]
                word_counter = Counter(word)

                # Check if word fits
                if all(
                    remaining.get(c, 0) >= word_counter[c]
                    for c in word_counter
                ):
                    new_remaining = remaining - word_counter
                    current.append(word)
                    search(new_remaining, current, i)
                    current.pop()

        search(target_counter, [], 0)

        # Remove duplicates and sort
        results = list(set(results))
        results.sort(key=lambda x: (len(x.split()), x))

        return results[:max_results]

    # ==================== Scrabble Word Finder ====================

    def scrabble_words(
        self,
        letters: str,
        min_length: int = 2,
        max_length: Optional[int] = None,
        must_include: str = "",
    ) -> List[Dict[str, Any]]:
        """Find all words that can be made from given letters.

        Args:
            letters: Available letters (use '?' for wildcards).
            min_length: Minimum word length.
            max_length: Maximum word length.
            must_include: Letters that must be in the word.

        Returns:
            List of dicts with word, length, and score.
        """
        self._ensure_active()
        self._increment_call_count()

        letters_lower = letters.lower()
        wildcards = letters_lower.count('?')
        letter_counter = Counter(letters_lower.replace('?', ''))

        must = must_include.lower()
        must_counter = Counter(must)

        results: List[Dict[str, Any]] = []

        for word in self._word_set:
            word_len = len(word)

            if word_len < min_length:
                continue
            if max_length and word_len > max_length:
                continue

            word_counter = Counter(word)

            # Check must-include letters
            if not all(word_counter.get(c, 0) >= must_counter[c] for c in must_counter):
                continue

            # Check if word can be formed with available letters + wildcards
            deficit = 0
            for ch, count in word_counter.items():
                have = letter_counter.get(ch, 0)
                if count > have:
                    deficit += count - have

            if deficit <= wildcards:
                score = self._scrabble_score(word)
                results.append({
                    "word": word,
                    "length": word_len,
                    "score": score,
                    "wildcards_used": deficit,
                })

        # Sort by score descending
        results.sort(key=lambda x: (-x["score"], x["word"]))

        return results

    def _scrabble_score(self, word: str) -> int:
        """Calculate Scrabble score for a word."""
        scores = {
            'a': 1, 'b': 3, 'c': 3, 'd': 2, 'e': 1, 'f': 4, 'g': 2,
            'h': 4, 'i': 1, 'j': 8, 'k': 5, 'l': 1, 'm': 3, 'n': 1,
            'o': 1, 'p': 3, 'q': 10, 'r': 1, 's': 1, 't': 1, 'u': 1,
            'v': 4, 'w': 4, 'x': 8, 'y': 4, 'z': 10,
        }
        return sum(scores.get(ch, 0) for ch in word.lower())

    # ==================== Word Validation ====================

    def validate_word(self, word: str) -> Tuple[bool, str]:
        """Check if a word is in the word list.

        Args:
            word: Word to validate.

        Returns:
            Tuple of (is_valid, message).
        """
        self._ensure_active()
        self._increment_call_count()

        if word.lower() in self._word_set:
            return True, f"'{word}' is a valid word"
        else:
            return False, f"'{word}' is not in the word list"

    def get_word_list(self) -> List[str]:
        """Get the full word list."""
        self._ensure_active()
        self._increment_call_count()
        return sorted(list(self._word_set))

    # ==================== Statistics ====================

    def get_stats(self) -> Dict[str, Any]:
        """Get plugin statistics."""
        word_lengths = Counter(len(w) for w in self._word_set)
        return {
            "total_words": len(self._word_set),
            "anagram_groups": len(self._anagram_index),
            "word_lengths": dict(sorted(word_lengths.items())),
            "longest_word": max(len(w) for w in self._word_set),
            "shortest_word": min(len(w) for w in self._word_set),
        }
