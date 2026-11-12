"""
Emoji Toolkit Plugin for LiuHao AI OS

Provides text to emoji, emoji to text, emoji search, categories,
skin tone variations, and emoji extraction from text.

Capabilities:
- text_to_emoji: Replace text keywords with emojis
- emoji_to_text: Replace emojis with text descriptions
- search: Search emojis by keyword
- categories: List emoji categories
- skin_tones: Apply skin tone variations
- extract: Extract emojis from text
- count: Count emojis in text
- random_emoji: Get a random emoji
- get_emoji_info: Get detailed info about an emoji
"""

import re
import random
from typing import Dict, Any, Optional, List, Set, Tuple, Union
from dataclasses import dataclass, field

from ..plugin_base import (
    BasePlugin,
    PluginMetadata,
    PluginStatus,
    PluginExecutionError,
)


@dataclass
class EmojiInfo:
    """Information about an emoji."""
    emoji: str
    name: str
    category: str
    keywords: List[str]
    unicode: str
    has_skin_tone: bool


@dataclass
class EmojiExtractionResult:
    """Result of emoji extraction."""
    emojis: List[str]
    count: int
    unique_count: int
    positions: List[Tuple[int, str]]


class EmojiPlugin(BasePlugin):
    """Emoji toolkit plugin.

    Provides emoji search, conversion, extraction, and manipulation.

    Example:
        plugin = EmojiPlugin()
        plugin.initialize()
        plugin.activate()

        result = plugin.search("heart")
        print(result)
    """

    # Emoji database: name -> (emoji, category, keywords, has_skin_tone)
    EMOJIS: Dict[str, Dict[str, Any]] = {
        # Smileys & Emotion
        "grinning_face": {"emoji": "😀", "category": "smileys", "keywords": ["grin", "happy", "smile", "face"]},
        "grinning_face_with_big_eyes": {"emoji": "😃", "category": "smileys", "keywords": ["happy", "excited", "joy"]},
        "grinning_face_with_smiling_eyes": {"emoji": "😄", "category": "smileys", "keywords": ["happy", "smile", "laugh"]},
        "beaming_face_with_smiling_eyes": {"emoji": "😁", "category": "smileys", "keywords": ["beam", "happy", "grin"]},
        "grinning_squinting_face": {"emoji": "😆", "category": "smileys", "keywords": ["laugh", "happy", "XD"]},
        "grinning_face_with_sweat": {"emoji": "😅", "category": "smileys", "keywords": ["nervous", "sweat", "embarrassed"]},
        "rolling_on_the_floor_laughing": {"emoji": "🤣", "category": "smileys", "keywords": ["rofl", "laugh", "hilarious"]},
        "face_with_tears_of_joy": {"emoji": "😂", "category": "smileys", "keywords": ["lol", "cry", "laugh", "tears"]},
        "slightly_smiling_face": {"emoji": "🙂", "category": "smileys", "keywords": ["smile", "happy", "nice"]},
        "upside_down_face": {"emoji": "🙃", "category": "smileys", "keywords": ["upside down", "silly", "crazy"]},
        "winking_face": {"emoji": "😉", "category": "smileys", "keywords": ["wink", "flirt", "playful"]},
        "smiling_face_with_smiling_eyes": {"emoji": "😊", "category": "smileys", "keywords": ["smile", "happy", "blush"]},
        "smiling_face_with_halo": {"emoji": "😇", "category": "smileys", "keywords": ["angel", "innocent", "holy"]},
        "smiling_face_with_heart_eyes": {"emoji": "😍", "category": "smileys", "keywords": ["love", "heart", "crush"]},
        "smiling_face_with_hearts": {"emoji": "🥰", "category": "smileys", "keywords": ["love", "hearts", "affection"]},
        "star_struck": {"emoji": "🤩", "category": "smileys", "keywords": ["star", "amazed", "impressed"]},
        "face_blowing_a_kiss": {"emoji": "😘", "category": "smileys", "keywords": ["kiss", "love", "blow"]},
        "kissing_face": {"emoji": "😗", "category": "smileys", "keywords": ["kiss", "love"]},
        "kissing_face_with_smiling_eyes": {"emoji": "😙", "category": "smileys", "keywords": ["kiss", "happy", "love"]},
        "kissing_face_with_closed_eyes": {"emoji": "😚", "category": "smileys", "keywords": ["kiss", "love"]},
        "smiling_face": {"emoji": "☺️", "category": "smileys", "keywords": ["smile", "happy"]},
        "face_with_tongue": {"emoji": "😋", "category": "smileys", "keywords": ["tongue", "yummy", "delicious"]},
        "face_savoring_food": {"emoji": "😋", "category": "smileys", "keywords": ["food", "yummy", "delicious"]},
        "face_with_raised_eyebrow": {"emoji": "🤨", "category": "smileys", "keywords": ["skeptical", "suspicious", "hmm"]},
        "neutral_face": {"emoji": "😐", "category": "smileys", "keywords": ["neutral", "meh", "indifferent"]},
        "expressionless_face": {"emoji": "😑", "category": "smileys", "keywords": ["expressionless", "bored", "meh"]},
        "face_without_mouth": {"emoji": "😶", "category": "smileys", "keywords": ["speechless", "silent", "no mouth"]},
        "face_in_clouds": {"emoji": "😶‍🌫️", "category": "smileys", "keywords": ["fog", "clouds", "hidden"]},
        "smirking_face": {"emoji": "😏", "category": "smileys", "keywords": ["smirk", "sly", "cocky"]},
        "persevering_face": {"emoji": "😣", "category": "smileys", "keywords": ["struggle", "effort", "determined"]},
        "face_with_open_eyes_and_hand_over_mouth": {"emoji": "🫢", "category": "smileys", "keywords": ["surprised", "shocked"]},
        "shushing_face": {"emoji": "🤫", "category": "smileys", "keywords": ["shush", "quiet", "secret"]},
        "thinking_face": {"emoji": "🤔", "category": "smileys", "keywords": ["think", "wonder", "hmm"]},
        "saluting_face": {"emoji": "🫡", "category": "smileys", "keywords": ["salute", "respect", "yes sir"]},
        "zipper_mouth_face": {"emoji": "🤐", "category": "smileys", "keywords": ["zip", "secret", "silent"]},
        "face_with_raised_hand": {"emoji": "🙋", "category": "smileys", "keywords": ["hand", "question", "me"], "has_skin_tone": True},
        "call_me_hand": {"emoji": "🤙", "category": "people", "keywords": ["call", "phone", "hang loose"], "has_skin_tone": True},
        "face_with_rolling_eyes": {"emoji": "🙄", "category": "smileys", "keywords": ["eyeroll", "annoyed", "bored"]},
        "grimacing_face": {"emoji": "😬", "category": "smileys", "keywords": ["grimace", "nervous", "awkward"]},
        "lying_face": {"emoji": "🤥", "category": "smileys", "keywords": ["lie", "pinocchio", "liar"]},
        "shaking_face": {"emoji": "🫨", "category": "smileys", "keywords": ["shake", "shock", "vibrate"]},
        "head_shaking_horizontally": {"emoji": "🙂‍↔️", "category": "smileys", "keywords": ["no", "disagree"]},
        "head_shaking_vertically": {"emoji": "🙂‍↕️", "category": "smileys", "keywords": ["yes", "nod", "agree"]},
        "flushed_face": {"emoji": "😳", "category": "smileys", "keywords": ["flushed", "embarrassed", "blush"]},
        "angry_face": {"emoji": "😠", "category": "smileys", "keywords": ["angry", "mad", "furious"]},
        "pouting_face": {"emoji": "😡", "category": "smileys", "keywords": ["pout", "angry", "mad"]},
        "crying_face": {"emoji": "😢", "category": "smileys", "keywords": ["cry", "sad", "tears"]},
        "loudly_crying_face": {"emoji": "😭", "category": "smileys", "keywords": ["cry", "sad", "sob"]},
        "sleeping_face": {"emoji": "😴", "category": "smileys", "keywords": ["sleep", "zzz", "tired"]},
        # People & Body
        "thumbs_up": {"emoji": "👍", "category": "people", "keywords": ["like", "good", "approve"], "has_skin_tone": True},
        "thumbs_down": {"emoji": "👎", "category": "people", "keywords": ["dislike", "bad", "reject"], "has_skin_tone": True},
        "clapping_hands": {"emoji": "👏", "category": "people", "keywords": ["clap", "applause", "bravo"], "has_skin_tone": True},
        "raising_hands": {"emoji": "🙌", "category": "people", "keywords": ["hands", "celebrate", "hooray"], "has_skin_tone": True},
        "folded_hands": {"emoji": "🙏", "category": "people", "keywords": ["pray", "please", "thank you"], "has_skin_tone": True},
        "victory_hand": {"emoji": "✌️", "category": "people", "keywords": ["peace", "victory", "V"], "has_skin_tone": True},
        "ok_hand": {"emoji": "👌", "category": "people", "keywords": ["OK", "okay", "perfect"], "has_skin_tone": True},
        "raised_fist": {"emoji": "✊", "category": "people", "keywords": ["fist", "power", "fight"], "has_skin_tone": True},
        "oncoming_fist": {"emoji": "👊", "category": "people", "keywords": ["punch", "fist", "bump"], "has_skin_tone": True},
        "wave": {"emoji": "👋", "category": "people", "keywords": ["wave", "hello", "hi", "bye"], "has_skin_tone": True},
        "muscle": {"emoji": "💪", "category": "people", "keywords": ["muscle", "strong", "flex", "gym"], "has_skin_tone": True},
        "ear": {"emoji": "👂", "category": "people", "keywords": ["ear", "listen", "hear"], "has_skin_tone": True},
        "eyes": {"emoji": "👀", "category": "people", "keywords": ["eyes", "look", "see", "watch"]},
        "brain": {"emoji": "🧠", "category": "people", "keywords": ["brain", "mind", "smart", "think"]},
        # Nature
        "sun": {"emoji": "☀️", "category": "nature", "keywords": ["sun", "sunny", "weather"]},
        "moon": {"emoji": "🌙", "category": "nature", "keywords": ["moon", "night", "lunar"]},
        "star": {"emoji": "⭐", "category": "nature", "keywords": ["star", "shine", "sparkle"]},
        "cloud": {"emoji": "☁️", "category": "nature", "keywords": ["cloud", "weather", "sky"]},
        "rainbow": {"emoji": "🌈", "category": "nature", "keywords": ["rainbow", "color", "pride"]},
        "fire": {"emoji": "🔥", "category": "nature", "keywords": ["fire", "hot", "lit", "flame"]},
        "water_wave": {"emoji": "🌊", "category": "nature", "keywords": ["wave", "water", "ocean", "sea"]},
        "leaf": {"emoji": "🍃", "category": "nature", "keywords": ["leaf", "nature", "green", "wind"]},
        "tree": {"emoji": "🌳", "category": "nature", "keywords": ["tree", "nature", "forest"]},
        "flower": {"emoji": "🌸", "category": "nature", "keywords": ["flower", "blossom", "spring", "sakura"]},
        "rose": {"emoji": "🌹", "category": "nature", "keywords": ["rose", "flower", "love", "red"]},
        "heart": {"emoji": "❤️", "category": "symbols", "keywords": ["heart", "love", "red"]},
        "sparkling_heart": {"emoji": "💖", "category": "symbols", "keywords": ["heart", "sparkle", "love"]},
        "broken_heart": {"emoji": "💔", "category": "symbols", "keywords": ["heart", "broken", "sad"]},
        "two_hearts": {"emoji": "💕", "category": "symbols", "keywords": ["heart", "love", "two"]},
        # Food & Drink
        "pizza": {"emoji": "🍕", "category": "food", "keywords": ["pizza", "food", "italian"]},
        "burger": {"emoji": "🍔", "category": "food", "keywords": ["burger", "hamburger", "food"]},
        "fries": {"emoji": "🍟", "category": "food", "keywords": ["fries", "french fries", "potato"]},
        "hot_dog": {"emoji": "🌭", "category": "food", "keywords": ["hotdog", "sausage", "food"]},
        "taco": {"emoji": "🌮", "category": "food", "keywords": ["taco", "mexican", "food"]},
        "sushi": {"emoji": "🍣", "category": "food", "keywords": ["sushi", "japanese", "food", "fish"]},
        "ramen": {"emoji": "🍜", "category": "food", "keywords": ["ramen", "noodles", "japanese", "soup"]},
        "apple": {"emoji": "🍎", "category": "food", "keywords": ["apple", "fruit", "red", "healthy"]},
        "banana": {"emoji": "🍌", "category": "food", "keywords": ["banana", "fruit", "yellow"]},
        "ice_cream": {"emoji": "🍦", "category": "food", "keywords": ["ice cream", "dessert", "sweet", "cold"]},
        "cake": {"emoji": "🎂", "category": "food", "keywords": ["cake", "birthday", "dessert"]},
        "coffee": {"emoji": "☕", "category": "food", "keywords": ["coffee", "drink", "cafe", "hot"]},
        "beer": {"emoji": "🍺", "category": "food", "keywords": ["beer", "drink", "alcohol"]},
        "wine_glass": {"emoji": "🍷", "category": "food", "keywords": ["wine", "drink", "alcohol", "glass"]},
        # Activities
        "soccer_ball": {"emoji": "⚽", "category": "activities", "keywords": ["soccer", "football", "sport", "ball"]},
        "basketball": {"emoji": "🏀", "category": "activities", "keywords": ["basketball", "sport", "ball"]},
        "baseball": {"emoji": "⚾", "category": "activities", "keywords": ["baseball", "sport", "ball"]},
        "trophy": {"emoji": "🏆", "category": "activities", "keywords": ["trophy", "win", "award", "champion"]},
        "video_game": {"emoji": "🎮", "category": "activities", "keywords": ["game", "gaming", "controller"]},
        "movie_camera": {"emoji": "🎥", "category": "activities", "keywords": ["movie", "film", "camera", "cinema"]},
        "microphone": {"emoji": "🎤", "category": "activities", "keywords": ["mic", "sing", "karaoke", "music"]},
        "musical_note": {"emoji": "🎵", "category": "activities", "keywords": ["music", "note", "song"]},
        # Travel & Places
        "globe": {"emoji": "🌍", "category": "travel", "keywords": ["globe", "earth", "world", "travel"]},
        "airplane": {"emoji": "✈️", "category": "travel", "keywords": ["airplane", "plane", "flight", "travel"]},
        "rocket": {"emoji": "🚀", "category": "travel", "keywords": ["rocket", "space", "launch", "fast"]},
        "car": {"emoji": "🚗", "category": "travel", "keywords": ["car", "drive", "vehicle"]},
        "bus": {"emoji": "🚌", "category": "travel", "keywords": ["bus", "transport"]},
        "train": {"emoji": "🚆", "category": "travel", "keywords": ["train", "railway", "transport"]},
        "house": {"emoji": "🏠", "category": "travel", "keywords": ["house", "home", "building"]},
        "hospital": {"emoji": "🏥", "category": "travel", "keywords": ["hospital", "medical", "health"]},
        "school": {"emoji": "🏫", "category": "travel", "keywords": ["school", "education", "learning"]},
        # Objects
        "mobile_phone": {"emoji": "📱", "category": "objects", "keywords": ["phone", "mobile", "cell", "smartphone"]},
        "computer": {"emoji": "💻", "category": "objects", "keywords": ["computer", "laptop", "pc", "tech"]},
        "keyboard": {"emoji": "⌨️", "category": "objects", "keywords": ["keyboard", "typing", "computer"]},
        "envelope": {"emoji": "✉️", "category": "objects", "keywords": ["email", "mail", "letter", "message"]},
        "gift": {"emoji": "🎁", "category": "objects", "keywords": ["gift", "present", "birthday"]},
        "camera": {"emoji": "📷", "category": "objects", "keywords": ["camera", "photo", "picture"]},
        "book": {"emoji": "📚", "category": "objects", "keywords": ["book", "read", "library", "study"]},
        "pencil": {"emoji": "✏️", "category": "objects", "keywords": ["pencil", "write", "draw"]},
        "light_bulb": {"emoji": "💡", "category": "objects", "keywords": ["idea", "light", "bulb", "invention"]},
        "lock": {"emoji": "🔒", "category": "objects", "keywords": ["lock", "secure", "private"]},
        "key": {"emoji": "🔑", "category": "objects", "keywords": ["key", "access", "password"]},
        "magnifying_glass": {"emoji": "🔍", "category": "objects", "keywords": ["search", "magnify", "find"]},
        # Symbols
        "check_mark": {"emoji": "✅", "category": "symbols", "keywords": ["check", "yes", "correct", "done"]},
        "cross_mark": {"emoji": "❌", "category": "symbols", "keywords": ["cross", "no", "wrong", "x"]},
        "warning": {"emoji": "⚠️", "category": "symbols", "keywords": ["warning", "alert", "caution"]},
        "question_mark": {"emoji": "❓", "category": "symbols", "keywords": ["question", "help", "what", "why"]},
        "exclamation_mark": {"emoji": "❗", "category": "symbols", "keywords": ["exclamation", "important", "!"],},
        "hundred_points": {"emoji": "💯", "category": "symbols", "keywords": ["100", "hundred", "perfect", "score"]},
        "sparkles": {"emoji": "✨", "category": "symbols", "keywords": ["sparkle", "magic", "shine", "special"]},
        "tada": {"emoji": "🎉", "category": "symbols", "keywords": ["party", "celebration", "popper", "tada"]},
        "balloon": {"emoji": "🎈", "category": "symbols", "keywords": ["balloon", "party", "birthday"]},
        "flag": {"emoji": "🚩", "category": "symbols", "keywords": ["flag", "mark", "goal"]},
        "recycle": {"emoji": "♻️", "category": "symbols", "keywords": ["recycle", "eco", "environment", "green"]},
        "infinity": {"emoji": "♾️", "category": "symbols", "keywords": ["infinity", "forever", "endless"]},
        "arrow_right": {"emoji": "➡️", "category": "symbols", "keywords": ["arrow", "right", "direction"]},
        "arrow_left": {"emoji": "⬅️", "category": "symbols", "keywords": ["arrow", "left", "direction"]},
        "arrow_up": {"emoji": "⬆️", "category": "symbols", "keywords": ["arrow", "up", "direction"]},
        "arrow_down": {"emoji": "⬇️", "category": "symbols", "keywords": ["arrow", "down", "direction"]},
        "red_circle": {"emoji": "🔴", "category": "symbols", "keywords": ["circle", "red", "dot"]},
        "green_circle": {"emoji": "🟢", "category": "symbols", "keywords": ["circle", "green", "dot"]},
        "blue_circle": {"emoji": "🔵", "category": "symbols", "keywords": ["circle", "blue", "dot"]},
        "yellow_circle": {"emoji": "🟡", "category": "symbols", "keywords": ["circle", "yellow", "dot"]},
    }

    # Skin tone modifiers
    SKIN_TONES = {
        "light": "🏻",
        "medium_light": "🏼",
        "medium": "🏽",
        "medium_dark": "🏾",
        "dark": "🏿",
    }

    # Regex for detecting emoji-like characters (simplified)
    EMOJI_PATTERN = re.compile(
        "["
        "\U0001F300-\U0001FAFF"  # Miscellaneous Symbols, Emoticons, etc.
        "\U00002600-\U000027BF"  # Misc symbols & dingbats
        "\U0001F000-\U0001F02F"  # Mahjong & dominoes
        "\U0001F0A0-\U0001F0FF"  # Playing cards
        "\u2702-\u27B0"  # Dingbats
        "\u2600-\u26FF"  # Misc symbols
        "\U0001F100-\U0001F1FF"  # Enclosed chars
        "]+",
        flags=re.UNICODE,
    )

    def __init__(self) -> None:
        """Initialize the emoji plugin."""
        metadata = PluginMetadata(
            name="emoji",
            version="1.0.0",
            description="Emoji search, conversion, extraction, and manipulation",
            author="LiuHao AI OS Team",
            categories=["text", "utility", "fun"],
            keywords=["emoji", "emoticon", "smiley", "search", "reaction"],
            requires_internet=False,
            dependencies=[],
        )
        super().__init__(metadata)

        self._default_skin_tone: Optional[str] = None

    # ==================== Lifecycle Methods ====================

    def initialize(self, config: Optional[Dict[str, Any]] = None) -> bool:
        """Initialize the emoji plugin."""
        try:
            if config:
                self._default_skin_tone = config.get("default_skin_tone")

            self._set_status(PluginStatus.INITIALIZED)
            return True
        except Exception as e:
            self._set_error(f"Initialization failed: {e}")
            return False

    def activate(self) -> bool:
        """Activate the emoji plugin."""
        try:
            self._set_status(PluginStatus.ACTIVE)
            return True
        except Exception as e:
            self._set_error(f"Activation failed: {e}")
            return False

    def deactivate(self) -> bool:
        """Deactivate the emoji plugin."""
        try:
            self._set_status(PluginStatus.INACTIVE)
            return True
        except Exception as e:
            self._set_error(str(e))
            return False

    def shutdown(self) -> bool:
        """Shut down the emoji plugin."""
        try:
            self._set_status(PluginStatus.SHUTDOWN)
            return True
        except Exception as e:
            self._set_error(str(e))
            return False

    def get_capabilities(self) -> List[str]:
        """Return the list of capabilities."""
        return [
            "text_to_emoji",
            "emoji_to_text",
            "search",
            "categories",
            "skin_tones",
            "extract",
            "count",
            "random_emoji",
            "get_emoji_info",
            "list_all",
        ]

    # ==================== Text <-> Emoji ====================

    def text_to_emoji(self, text: str, *, language: str = "en") -> str:
        """Replace text keywords with emojis.

        Replaces common text patterns with corresponding emojis.

        Args:
            text: Input text.
            language: Language for keyword matching.

        Returns:
            Text with emojis inserted.
        """
        self._ensure_active()
        self._increment_call_count()

        result = text

        # Simple keyword replacement (word boundary matching)
        # Sort by keyword length (longer first) to avoid partial matches
        replacements: List[Tuple[str, str]] = []
        for name, data in self.EMOJIS.items():
            emoji = data["emoji"]
            # Add name and keywords
            all_keywords = [name.replace("_", " ")] + data.get("keywords", [])
            for kw in all_keywords:
                if len(kw) >= 3:  # Only use longer keywords
                    replacements.append((kw, emoji))

        # Sort by length descending
        replacements.sort(key=lambda x: len(x[0]), reverse=True)

        for keyword, emoji in replacements:
            pattern = re.compile(re.escape(keyword), re.IGNORECASE)
            result = pattern.sub(emoji, result)

        return result

    def emoji_to_text(self, text: str, *, format_style: str = "colon") -> str:
        """Replace emojis with text descriptions.

        Args:
            text: Input text with emojis.
            format_style: 'colon' (:emoji_name:), 'brackets' ([emoji name]), 'description'

        Returns:
            Text with emojis replaced by text.
        """
        self._ensure_active()
        self._increment_call_count()

        result = text

        # Build emoji -> name mapping
        emoji_to_name: Dict[str, str] = {}
        for name, data in self.EMOJIS.items():
            emoji = data["emoji"]
            if emoji not in emoji_to_name:  # First one wins
                emoji_to_name[emoji] = name

        # Replace known emojis
        for emoji, name in sorted(emoji_to_name.items(), key=lambda x: -len(x[0])):
            display_name = name.replace("_", " ")
            if format_style == "colon":
                replacement = f":{name}:"
            elif format_style == "brackets":
                replacement = f"[{display_name}]"
            elif format_style == "description":
                replacement = f"({display_name})"
            else:
                replacement = f":{name}:"

            result = result.replace(emoji, replacement)

        return result

    # ==================== Search ====================

    def search(
        self,
        query: str,
        *,
        category: Optional[str] = None,
        limit: int = 20,
    ) -> List[Dict[str, Any]]:
        """Search emojis by keyword.

        Args:
            query: Search query.
            category: Optional category filter.
            limit: Maximum number of results.

        Returns:
            List of matching emoji info dicts.
        """
        self._ensure_active()
        self._increment_call_count()

        query_lower = query.lower()
        results: List[Dict[str, Any]] = []
        scored: List[Tuple[int, Dict[str, Any]]] = []

        for name, data in self.EMOJIS.items():
            if category and data["category"] != category:
                continue

            score = 0
            name_lower = name.lower()
            keywords = data.get("keywords", [])

            # Exact name match
            if name_lower == query_lower:
                score += 100
            elif name_lower.startswith(query_lower):
                score += 50
            elif query_lower in name_lower:
                score += 30

            # Keyword matches
            for kw in keywords:
                kw_lower = kw.lower()
                if kw_lower == query_lower:
                    score += 80
                elif kw_lower.startswith(query_lower):
                    score += 40
                elif query_lower in kw_lower:
                    score += 20

            if score > 0:
                emoji_info = {
                    "emoji": data["emoji"],
                    "name": name,
                    "category": data["category"],
                    "keywords": keywords,
                    "score": score,
                }
                scored.append((score, emoji_info))

        # Sort by score descending
        scored.sort(key=lambda x: x[0], reverse=True)
        results = [item[1] for item in scored[:limit]]

        return results

    def get_emoji_info(self, emoji_char: str) -> Optional[Dict[str, Any]]:
        """Get detailed information about an emoji.

        Args:
            emoji_char: The emoji character.

        Returns:
            Emoji info dict, or None if not found.
        """
        self._ensure_active()
        self._increment_call_count()

        for name, data in self.EMOJIS.items():
            if data["emoji"] == emoji_char:
                return {
                    "emoji": emoji_char,
                    "name": name,
                    "category": data["category"],
                    "keywords": data.get("keywords", []),
                    "has_skin_tone": data.get("has_skin_tone", False),
                    "unicode": f"U+{ord(emoji_char[0]):04X}",
                }
        return None

    # ==================== Categories ====================

    def categories(self) -> List[Dict[str, Any]]:
        """Get list of emoji categories with counts.

        Returns:
            List of category info dicts.
        """
        self._ensure_active()
        self._increment_call_count()

        cat_counts: Dict[str, int] = {}
        for data in self.EMOJIS.values():
            cat = data["category"]
            cat_counts[cat] = cat_counts.get(cat, 0) + 1

        return [
            {"name": cat, "count": count}
            for cat, count in sorted(cat_counts.items(), key=lambda x: -x[1])
        ]

    def list_by_category(self, category: str) -> List[Dict[str, Any]]:
        """List all emojis in a category.

        Args:
            category: Category name.

        Returns:
            List of emoji info dicts.
        """
        self._ensure_active()
        self._increment_call_count()

        results = []
        for name, data in self.EMOJIS.items():
            if data["category"] == category:
                results.append({
                    "emoji": data["emoji"],
                    "name": name,
                    "keywords": data.get("keywords", []),
                })
        return results

    # ==================== Skin Tones ====================

    def apply_skin_tone(
        self,
        emoji_char: str,
        skin_tone: str,
    ) -> str:
        """Apply a skin tone modifier to an emoji.

        Args:
            emoji_char: The base emoji.
            skin_tone: Skin tone name (light, medium_light, medium, medium_dark, dark).

        Returns:
            Emoji with skin tone applied.
        """
        self._ensure_active()
        self._increment_call_count()

        if skin_tone not in self.SKIN_TONES:
            raise ValueError(
                f"Unknown skin tone: {skin_tone}. "
                f"Use: {list(self.SKIN_TONES.keys())}"
            )

        # Check if emoji supports skin tones
        supports_skin_tone = False
        for data in self.EMOJIS.values():
            if data["emoji"] == emoji_char and data.get("has_skin_tone", False):
                supports_skin_tone = True
                break

        if not supports_skin_tone:
            # Try anyway - many emojis support skin tones
            pass

        modifier = self.SKIN_TONES[skin_tone]
        return emoji_char + modifier

    def get_skin_tones(self) -> Dict[str, str]:
        """Get available skin tone modifiers.

        Returns:
            Dict mapping skin tone name to modifier character.
        """
        return dict(self.SKIN_TONES)

    # ==================== Extraction & Counting ====================

    def extract(self, text: str) -> EmojiExtractionResult:
        """Extract all emojis from text.

        Args:
            text: Input text.

        Returns:
            EmojiExtractionResult with extracted emojis.
        """
        self._ensure_active()
        self._increment_call_count()

        emojis: List[str] = []
        positions: List[Tuple[int, str]] = []

        # Find all emoji-like sequences
        for match in self.EMOJI_PATTERN.finditer(text):
            emoji_char = match.group()
            emojis.append(emoji_char)
            positions.append((match.start(), emoji_char))

        unique = list(set(emojis))

        return EmojiExtractionResult(
            emojis=emojis,
            count=len(emojis),
            unique_count=len(unique),
            positions=positions,
        )

    def count(self, text: str) -> Dict[str, Any]:
        """Count emojis in text with detailed stats.

        Args:
            text: Input text.

        Returns:
            Dict with emoji counts and stats.
        """
        self._ensure_active()
        self._increment_call_count()

        result = self.extract(text)
        emoji_counts: Dict[str, int] = {}

        for emoji in result.emojis:
            emoji_counts[emoji] = emoji_counts.get(emoji, 0) + 1

        # Sort by frequency
        sorted_emojis = sorted(
            emoji_counts.items(), key=lambda x: x[1], reverse=True
        )

        return {
            "total_count": result.count,
            "unique_count": result.unique_count,
            "emoji_counts": dict(sorted_emojis),
            "top_emoji": sorted_emojis[0][0] if sorted_emojis else None,
            "top_count": sorted_emojis[0][1] if sorted_emojis else 0,
            "text_length": len(text),
            "emoji_density": round(result.count / len(text), 4) if text else 0,
        }

    # ==================== Random ====================

    def random_emoji(
        self,
        *,
        category: Optional[str] = None,
        count: int = 1,
    ) -> Union[str, List[str]]:
        """Get random emoji(s).

        Args:
            category: Optional category filter.
            count: Number of random emojis.

        Returns:
            Single emoji string or list of emoji strings.
        """
        self._ensure_active()
        self._increment_call_count()

        pool: List[str] = []
        for data in self.EMOJIS.values():
            if category and data["category"] != category:
                continue
            pool.append(data["emoji"])

        if not pool:
            raise ValueError(f"No emojis found for category: {category}")

        if count == 1:
            return random.choice(pool)
        else:
            return random.choices(pool, k=count)

    # ==================== List All ====================

    def list_all(self) -> List[Dict[str, Any]]:
        """List all available emojis.

        Returns:
            List of all emoji info dicts.
        """
        self._ensure_active()
        self._increment_call_count()

        return [
            {
                "emoji": data["emoji"],
                "name": name,
                "category": data["category"],
                "keywords": data.get("keywords", []),
            }
            for name, data in self.EMOJIS.items()
        ]

    # ==================== Statistics ====================

    def get_stats(self) -> Dict[str, Any]:
        """Get plugin statistics."""
        categories = {}
        for data in self.EMOJIS.values():
            cat = data["category"]
            categories[cat] = categories.get(cat, 0) + 1

        with_skin_tone = sum(
            1 for data in self.EMOJIS.values()
            if data.get("has_skin_tone", False)
        )

        return {
            "total_emojis": len(self.EMOJIS),
            "categories": categories,
            "skin_tone_count": with_skin_tone,
            "skin_tones": list(self.SKIN_TONES.keys()),
        }
