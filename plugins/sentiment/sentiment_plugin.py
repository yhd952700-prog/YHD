"""
Sentiment Analysis Plugin for LiuHao AI OS

Provides sentiment analysis with positive/negative/neutral classification,
confidence scores, emotion detection, and aspect-based sentiment analysis.

Capabilities:
- analyze_sentiment: Classify text sentiment
- detect_emotions: Detect emotions in text
- analyze_aspect: Aspect-based sentiment analysis
- batch_analyze: Analyze multiple texts at once
"""

import re
from typing import Dict, Any, Optional, List, Tuple
from dataclasses import dataclass, field
from collections import defaultdict

from ..plugin_base import (
    BasePlugin,
    PluginMetadata,
    PluginStatus,
    PluginExecutionError,
)


@dataclass
class SentimentResult:
    """Result of a sentiment analysis operation."""
    text: str
    sentiment: str  # "positive", "negative", "neutral"
    confidence: float
    scores: Dict[str, float]  # positive, negative, neutral scores
    positive_words: List[str] = field(default_factory=list)
    negative_words: List[str] = field(default_factory=list)
    compound_score: float = 0.0


@dataclass
class EmotionResult:
    """Result of emotion detection."""
    text: str
    primary_emotion: str
    emotions: Dict[str, float]  # emotion -> score
    confidence: float


@dataclass
class AspectSentimentResult:
    """Result of aspect-based sentiment analysis."""
    text: str
    aspects: Dict[str, Dict[str, Any]]  # aspect -> {sentiment, score, phrases}
    overall_sentiment: str
    overall_confidence: float


class SentimentPlugin(BasePlugin):
    """Sentiment analysis plugin using lexicon-based approach.

    Supports:
    - Positive/negative/neutral classification
    - Confidence scoring
    - Multi-emotion detection (joy, anger, sadness, fear, surprise, disgust)
    - Aspect-based sentiment analysis

    Example:
        plugin = SentimentPlugin()
        plugin.initialize()
        plugin.activate()

        result = plugin.analyze_sentiment("I love this product!")
        print(result.sentiment)  # "positive"
    """

    # Positive sentiment words
    POSITIVE_WORDS = {
        # General positive
        "good": 2.0, "great": 3.0, "excellent": 3.5, "amazing": 3.5,
        "wonderful": 3.0, "fantastic": 3.5, "awesome": 3.0, "best": 3.0,
        "love": 3.5, "loved": 3.0, "loving": 2.5, "like": 2.0, "liked": 2.0,
        "happy": 3.0, "glad": 2.5, "pleased": 2.5, "satisfied": 2.5,
        "beautiful": 2.5, "nice": 2.0, "perfect": 3.5, "brilliant": 3.0,
        "superb": 3.0, "outstanding": 3.0, "impressive": 2.5, "remarkable": 2.5,
        "enjoy": 2.0, "enjoyed": 2.0, "enjoyable": 2.5, "delightful": 3.0,
        "charming": 2.0, "cute": 2.0, "lovely": 2.5, "gorgeous": 2.5,
        "successful": 2.5, "success": 2.0, "win": 2.0, "won": 2.5,
        "helpful": 2.0, "useful": 2.0, "valuable": 2.0, "important": 1.5,
        "recommend": 2.5, "recommended": 2.5, "highly": 1.5,
        "thank": 2.0, "thanks": 2.0, "thankful": 2.5, "grateful": 2.5,
        "excited": 3.0, "exciting": 2.5, "thrilled": 3.5, "ecstatic": 3.5,
        "fun": 2.0, "cool": 1.5, "wow": 2.0, "yes": 1.0, "agree": 1.5,
    }

    # Negative sentiment words
    NEGATIVE_WORDS = {
        # General negative
        "bad": 2.0, "terrible": 3.5, "awful": 3.5, "horrible": 3.5,
        "worst": 3.5, "poor": 2.0, "disappointing": 2.5, "disappointed": 2.5,
        "sad": 2.5, "unhappy": 2.5, "angry": 3.0, "upset": 2.5,
        "hate": 3.5, "hated": 3.0, "hateful": 3.0, "dislike": 2.0,
        "ugly": 2.0, "boring": 2.0, "dull": 1.5, "annoying": 2.5,
        "frustrating": 2.5, "frustrated": 2.5, "annoyed": 2.0,
        "fail": 2.5, "failed": 2.5, "failure": 2.5, "lose": 2.0,
        "lost": 2.5, "broken": 2.0, "broken": 2.0, "wrong": 1.5,
        "problem": 1.5, "issue": 1.0, "trouble": 1.5, "difficult": 1.5,
        "hard": 1.0, "expensive": 1.0, "cheap": 1.0, "waste": 2.0,
        "useless": 2.5, "worthless": 3.0, "stupid": 2.5, "dumb": 2.0,
        "terrible": 3.0, "pathetic": 2.5, "shameful": 2.5,
        "scared": 2.0, "afraid": 2.0, "fear": 2.0, "worried": 2.0,
        "nervous": 1.5, "anxious": 2.0, "depressed": 3.0, "miserable": 3.0,
        "no": 1.0, "disagree": 1.5, "never": 1.5, "cannot": 1.0,
        "sorry": 1.5, "apologize": 1.5, "regret": 2.0,
    }

    # Emotion word dictionaries
    EMOTION_WORDS: Dict[str, Dict[str, float]] = {
        "joy": {
            "happy": 3.0, "joy": 3.0, "joyful": 3.0, "delight": 3.0,
            "delighted": 3.0, "elated": 3.5, "ecstatic": 3.5, "thrilled": 3.5,
            "excited": 3.0, "excitement": 3.0, "glad": 2.5, "cheerful": 2.5,
            "smile": 2.0, "laugh": 2.5, "love": 3.0, "wonderful": 2.5,
            "fantastic": 3.0, "amazing": 3.0, "great": 2.5, "fun": 2.0,
        },
        "anger": {
            "angry": 3.0, "anger": 3.0, "furious": 3.5, "rage": 3.5,
            "mad": 2.5, "annoyed": 2.0, "annoying": 2.0, "frustrated": 2.5,
            "frustrating": 2.5, "irritated": 2.0, "hate": 3.0, "hateful": 3.0,
            "outrage": 3.0, "outraged": 3.0, "resentful": 2.5,
            "disgusted": 2.5, "disgust": 2.5,
        },
        "sadness": {
            "sad": 2.5, "sadness": 2.5, "unhappy": 2.5, "depressed": 3.0,
            "depression": 3.0, "miserable": 3.0, "sorrow": 3.0, "sorrowful": 3.0,
            "grief": 3.0, "grieving": 3.0, "heartbroken": 3.5, "lonely": 2.5,
            "tears": 2.0, "crying": 2.0, "cry": 2.0, "disappointed": 2.0,
            "disappointment": 2.0, "regret": 2.0, "regretful": 2.0,
        },
        "fear": {
            "fear": 2.5, "afraid": 2.5, "scared": 2.5, "fearful": 2.5,
            "terrified": 3.5, "horrified": 3.0, "panic": 3.0, "panicked": 3.0,
            "anxious": 2.5, "anxiety": 2.5, "worried": 2.0, "worry": 2.0,
            "nervous": 2.0, "dread": 2.5, "threat": 2.0, "threatened": 2.5,
            "dangerous": 2.0, "danger": 2.5,
        },
        "surprise": {
            "surprise": 2.5, "surprised": 2.5, "surprising": 2.5,
            "amazed": 3.0, "amazing": 3.0, "astonished": 3.0, "shocked": 3.0,
            "shocking": 3.0, "unexpected": 2.0, "wow": 2.0, "whoa": 2.0,
            "incredible": 2.5, "unbelievable": 2.5, "remarkable": 2.0,
        },
        "disgust": {
            "disgust": 2.5, "disgusted": 2.5, "disgusting": 3.0,
            "gross": 2.0, "nasty": 2.0, "revolting": 3.0, "sick": 1.5,
            "sickening": 2.5, "repulsive": 3.0, "vile": 2.5, "filthy": 2.0,
        },
    }

    # Negation words that flip sentiment
    NEGATION_WORDS = {
        "not", "no", "never", "neither", "nor", "none", "nobody",
        "nothing", "nowhere", "hardly", "barely", "scarcely",
        "doesn't", "don't", "didn't", "won't", "wouldn't", "shouldn't",
        "couldn't", "can't", "cannot", "isn't", "aren't", "wasn't",
        "weren't", "haven't", "hasn't", "hadn't",
    }

    # Intensifiers that multiply sentiment
    INTENSIFIERS = {
        "very": 1.5, "really": 1.5, "extremely": 2.0, "incredibly": 2.0,
        "absolutely": 1.8, "totally": 1.5, "completely": 1.5, "utterly": 1.8,
        "so": 1.3, "too": 1.3, "quite": 1.3, "rather": 1.2, "fairly": 1.1,
        "pretty": 1.2, "super": 1.5, "highly": 1.5, "deeply": 1.3,
        "greatly": 1.3, "strongly": 1.4,
    }

    # Diminishers that reduce sentiment
    DIMINISHERS = {
        "slightly": 0.7, "somewhat": 0.7, "kind of": 0.8, "kinda": 0.8,
        "sort of": 0.8, "bit": 0.8, "little": 0.8, "barely": 0.5,
        "hardly": 0.5, "scarcely": 0.5,
    }

    # Common aspects for aspect-based analysis
    COMMON_ASPECTS = {
        "quality": ["quality", "well", "poorly", "made", "build", "material"],
        "price": ["price", "cost", "expensive", "cheap", "value", "money", "worth"],
        "service": ["service", "staff", "support", "help", "customer", "delivery"],
        "design": ["design", "look", "style", "appearance", "beautiful", "ugly"],
        "performance": ["performance", "speed", "fast", "slow", "efficient", "lag"],
        "usability": ["easy", "simple", "intuitive", "difficult", "hard", "use", "useful"],
    }

    def __init__(self) -> None:
        """Initialize the sentiment plugin."""
        metadata = PluginMetadata(
            name="sentiment",
            version="1.0.0",
            description="Sentiment analysis plugin with emotion detection",
            author="LiuHao AI OS Team",
            categories=["nlp", "analysis", "text"],
            keywords=["sentiment", "emotion", "analysis", "nlp", "text"],
            requires_internet=False,
            dependencies=[],
        )
        super().__init__(metadata)

        self._analysis_count: int = 0

    # ==================== Lifecycle Methods ====================

    def initialize(self, config: Optional[Dict[str, Any]] = None) -> bool:
        """Initialize the sentiment plugin."""
        try:
            self._set_status(PluginStatus.INITIALIZED)
            return True
        except Exception as e:
            self._set_error(f"Initialization failed: {e}")
            return False

    def activate(self) -> bool:
        """Activate the sentiment plugin."""
        try:
            self._set_status(PluginStatus.ACTIVE)
            return True
        except Exception as e:
            self._set_error(f"Activation failed: {e}")
            return False

    def deactivate(self) -> bool:
        """Deactivate the sentiment plugin."""
        try:
            self._set_status(PluginStatus.INACTIVE)
            return True
        except Exception as e:
            self._set_error(str(e))
            return False

    def shutdown(self) -> bool:
        """Shut down the sentiment plugin."""
        try:
            self._set_status(PluginStatus.SHUTDOWN)
            return True
        except Exception as e:
            self._set_error(str(e))
            return False

    def get_capabilities(self) -> List[str]:
        """Return the list of capabilities."""
        return [
            "analyze_sentiment",
            "detect_emotions",
            "analyze_aspect",
            "batch_analyze",
            "batch_emotions",
        ]

    # ==================== Sentiment Analysis Methods ====================

    def analyze_sentiment(self, text: str) -> SentimentResult:
        """Analyze the sentiment of a text.

        Args:
            text: The text to analyze.

        Returns:
            SentimentResult with classification and confidence.
        """
        self._ensure_active()
        self._increment_call_count()
        self._analysis_count += 1

        if not text or not text.strip():
            return SentimentResult(
                text=text,
                sentiment="neutral",
                confidence=0.5,
                scores={"positive": 0.0, "negative": 0.0, "neutral": 1.0},
            )

        words = self._tokenize(text)
        pos_score = 0.0
        neg_score = 0.0
        positive_words = []
        negative_words = []

        for i, word in enumerate(words):
            word_lower = word.lower()

            # Check for negation context (within 3 words before)
            multiplier = 1.0
            is_negated = self._is_negated(words, i)

            # Check for intensifiers/diminishers
            if i > 0:
                prev = words[i - 1].lower()
                if prev in self.INTENSIFIERS:
                    multiplier *= self.INTENSIFIERS[prev]
                elif prev in self.DIMINISHERS:
                    multiplier *= self.DIMINISHERS[prev]

            # Positive words
            if word_lower in self.POSITIVE_WORDS:
                score = self.POSITIVE_WORDS[word_lower] * multiplier
                if is_negated:
                    neg_score += score
                    negative_words.append(word)
                else:
                    pos_score += score
                    positive_words.append(word)

            # Negative words
            elif word_lower in self.NEGATIVE_WORDS:
                score = self.NEGATIVE_WORDS[word_lower] * multiplier
                if is_negated:
                    pos_score += score * 0.7  # Not negative = mild positive
                    positive_words.append(word)
                else:
                    neg_score += score
                    negative_words.append(word)

        # Normalize scores
        word_count = max(len(words), 1)
        pos_score = min(1.0, pos_score / (word_count * 0.5))
        neg_score = min(1.0, neg_score / (word_count * 0.5))

        # Calculate neutral score
        total = pos_score + neg_score
        if total < 0.1:
            neutral_score = 0.8
        else:
            neutral_score = max(0.0, 1.0 - total)

        # Normalize to sum ~1
        total_all = pos_score + neg_score + neutral_score
        pos_norm = pos_score / total_all
        neg_norm = neg_score / total_all
        neu_norm = neutral_score / total_all

        # Determine sentiment
        if pos_norm > neg_norm and pos_norm > neu_norm:
            sentiment = "positive"
            confidence = pos_norm
        elif neg_norm > pos_norm and neg_norm > neu_norm:
            sentiment = "negative"
            confidence = neg_norm
        else:
            sentiment = "neutral"
            confidence = neu_norm

        # Compound score (-1 to 1)
        compound = pos_score - neg_score
        compound = max(-1.0, min(1.0, compound))

        return SentimentResult(
            text=text,
            sentiment=sentiment,
            confidence=round(confidence, 3),
            scores={
                "positive": round(pos_norm, 3),
                "negative": round(neg_norm, 3),
                "neutral": round(neu_norm, 3),
            },
            positive_words=positive_words,
            negative_words=negative_words,
            compound_score=round(compound, 3),
        )

    def detect_emotions(self, text: str) -> EmotionResult:
        """Detect emotions in a text.

        Args:
            text: The text to analyze.

        Returns:
            EmotionResult with detected emotions and scores.
        """
        self._ensure_active()
        self._increment_call_count()

        if not text or not text.strip():
            return EmotionResult(
                text=text,
                primary_emotion="neutral",
                emotions={"neutral": 1.0},
                confidence=0.5,
            )

        words = self._tokenize(text)
        emotion_scores: Dict[str, float] = defaultdict(float)

        for i, word in enumerate(words):
            word_lower = word.lower()
            multiplier = 1.0

            # Check for intensifiers
            if i > 0:
                prev = words[i - 1].lower()
                if prev in self.INTENSIFIERS:
                    multiplier = self.INTENSIFIERS[prev]

            # Check against each emotion's word list
            for emotion, word_dict in self.EMOTION_WORDS.items():
                if word_lower in word_dict:
                    is_negated = self._is_negated(words, i)
                    score = word_dict[word_lower] * multiplier

                    if is_negated:
                        # Add to opposite emotion or reduce
                        opposite = self._get_opposite_emotion(emotion)
                        emotion_scores[opposite] += score * 0.5
                    else:
                        emotion_scores[emotion] += score

        if not emotion_scores:
            return EmotionResult(
                text=text,
                primary_emotion="neutral",
                emotions={"neutral": 1.0},
                confidence=0.5,
            )

        # Normalize scores
        total = sum(emotion_scores.values())
        normalized = {
            emotion: round(score / total, 3)
            for emotion, score in emotion_scores.items()
        }

        # Find primary emotion
        primary = max(normalized, key=normalized.get)
        confidence = normalized[primary]

        # Ensure all 6 emotions are present
        all_emotions = {e: 0.0 for e in self.EMOTION_WORDS}
        all_emotions.update(normalized)
        all_emotions = {k: round(v, 3) for k, v in all_emotions.items()}

        return EmotionResult(
            text=text,
            primary_emotion=primary,
            emotions=all_emotions,
            confidence=round(confidence, 3),
        )

    def analyze_aspect(self, text: str) -> AspectSentimentResult:
        """Perform aspect-based sentiment analysis.

        Args:
            text: The text to analyze.

        Returns:
            AspectSentimentResult with per-aspect sentiment.
        """
        self._ensure_active()
        self._increment_call_count()

        if not text or not text.strip():
            return AspectSentimentResult(
                text=text,
                aspects={},
                overall_sentiment="neutral",
                overall_confidence=0.5,
            )

        sentences = self._split_sentences(text)
        aspects: Dict[str, Dict[str, Any]] = {}

        for aspect, keywords in self.COMMON_ASPECTS.items():
            aspect_sentences = []
            aspect_phrases = []
            pos_score = 0.0
            neg_score = 0.0

            for sent in sentences:
                sent_lower = sent.lower()
                if any(kw in sent_lower for kw in keywords):
                    aspect_sentences.append(sent)
                    # Analyze sentiment of this sentence
                    sent_result = self.analyze_sentiment(sent)
                    pos_score += sent_result.scores["positive"]
                    neg_score += sent_result.scores["negative"]

                    # Extract relevant phrases
                    words = sent.split()
                    for i, word in enumerate(words):
                        if word.lower() in keywords:
                            start = max(0, i - 3)
                            end = min(len(words), i + 4)
                            phrase = " ".join(words[start:end])
                            aspect_phrases.append(phrase)
                            break

            if aspect_sentences:
                n = len(aspect_sentences)
                pos_avg = pos_score / n
                neg_avg = neg_score / n

                if pos_avg > neg_avg:
                    sentiment = "positive"
                    conf = pos_avg
                elif neg_avg > pos_avg:
                    sentiment = "negative"
                    conf = neg_avg
                else:
                    sentiment = "neutral"
                    conf = 0.5

                aspects[aspect] = {
                    "sentiment": sentiment,
                    "confidence": round(conf, 3),
                    "scores": {
                        "positive": round(pos_avg, 3),
                        "negative": round(neg_avg, 3),
                    },
                    "mentions": n,
                    "phrases": aspect_phrases[:3],
                }

        # Overall sentiment
        overall = self.analyze_sentiment(text)

        return AspectSentimentResult(
            text=text,
            aspects=aspects,
            overall_sentiment=overall.sentiment,
            overall_confidence=overall.confidence,
        )

    def batch_analyze(self, texts: List[str]) -> List[SentimentResult]:
        """Analyze sentiment of multiple texts.

        Args:
            texts: List of text strings.

        Returns:
            List of SentimentResult objects.
        """
        self._ensure_active()
        return [self.analyze_sentiment(t) for t in texts]

    def batch_emotions(self, texts: List[str]) -> List[EmotionResult]:
        """Detect emotions in multiple texts.

        Args:
            texts: List of text strings.

        Returns:
            List of EmotionResult objects.
        """
        self._ensure_active()
        return [self.detect_emotions(t) for t in texts]

    # ==================== Internal Helpers ====================

    def _tokenize(self, text: str) -> List[str]:
        """Simple word tokenization."""
        words = re.findall(r"[a-zA-Z']+", text.lower())
        return words

    def _split_sentences(self, text: str) -> List[str]:
        """Split text into sentences."""
        sentences = re.split(r'(?<=[.!?])\s+', text.strip())
        return [s.strip() for s in sentences if s.strip()]

    def _is_negated(self, words: List[str], index: int, window: int = 3) -> bool:
        """Check if a word is in a negation context.

        Looks up to `window` words before the target for negation words.
        """
        start = max(0, index - window)
        for i in range(start, index):
            if words[i].lower() in self.NEGATION_WORDS:
                return True
        return False

    def _get_opposite_emotion(self, emotion: str) -> str:
        """Get the approximate opposite emotion."""
        opposites = {
            "joy": "sadness",
            "sadness": "joy",
            "anger": "joy",
            "fear": "surprise",
            "surprise": "fear",
            "disgust": "joy",
        }
        return opposites.get(emotion, "neutral")
