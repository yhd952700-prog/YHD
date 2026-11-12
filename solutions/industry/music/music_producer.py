"""
Music Production AI Solution Template.

A comprehensive AI-powered music production assistant for
music studios, producers, and artists. Includes multi-agent
roles for production, composition, mixing, and mastering.

Typical workflow:
    concept -> composition -> arrangement -> mixing -> mastering -> release
"""

from typing import Optional, Dict, Any, List
from enum import Enum
from dataclasses import dataclass, field

from solutions.templates.base_solution import (
    BaseSolution,
    SolutionMetadata,
    SolutionMetrics,
)


class Genre(str, Enum):
    """Music genres."""
    POP = "pop"
    ROCK = "rock"
    HIP_HOP = "hip_hop"
    ELECTRONIC = "electronic"
    RNB = "rnb"
    JAZZ = "jazz"
    CLASSICAL = "classical"
    COUNTRY = "country"
    FOLK = "folk"
    REGGAE = "reggae"
    METAL = "metal"
    INDIE = "indie"


class TrackRole(str, Enum):
    """Roles of tracks in a production."""
    LEAD_VOCAL = "lead_vocal"
    BACKING_VOCAL = "backing_vocal"
    DRUMS = "drums"
    BASS = "bass"
    GUITAR = "guitar"
    KEYS = "keys"
    SYNTH = "synth"
    STRINGS = "strings"
    BRASS = "brass"
    WOODWIND = "woodwind"
    PERCUSSION = "percussion"
    FX = "fx"
    AMBIENCE = "ambience"


class ProductionPhase(str, Enum):
    """Phases of music production."""
    PRE_PRODUCTION = "pre_production"
    RECORDING = "recording"
    EDITING = "editing"
    MIXING = "mixing"
    MASTERING = "mastering"
    RELEASE = "release"


class QualityLevel(str, Enum):
    """Audio quality levels."""
    DEMO = "demo"
    ROUGH_MIX = "rough_mix"
    RADIO_READY = "radio_ready"
    STUDIO_QUALITY = "studio_quality"
    MASTERED = "mastered"


class MusicRole(str, Enum):
    """Agent roles in the music production solution."""
    PRODUCER = "producer"
    COMPOSER = "composer"
    MIXING_ENGINEER = "mixing_engineer"
    MASTERING_ENGINEER = "mastering_engineer"


@dataclass
class SongConcept:
    """Represents a song/production concept.

    Attributes:
        id: Unique concept identifier.
        title: Song title / working title.
        genre: Primary genre.
        sub_genres: Sub-genre descriptors.
        mood: Overall mood/feeling.
        tempo_bpm: Tempo in BPM.
        key: Musical key.
        time_signature: Time signature.
        structure: Song structure (verse-chorus-etc.).
        reference_tracks: Reference tracks for style guidance.
        lyrical_theme: Lyrical theme/topic.
        target_duration_sec: Target duration in seconds.
    """
    id: str
    title: str = ""
    genre: Genre = Genre.POP
    sub_genres: List[str] = field(default_factory=list)
    mood: str = ""
    tempo_bpm: int = 120
    key: str = "C major"
    time_signature: str = "4/4"
    structure: str = "verse-chorus-verse-chorus-bridge-chorus-outro"
    reference_tracks: List[str] = field(default_factory=list)
    lyrical_theme: str = ""
    target_duration_sec: int = 210


@dataclass
class TrackElement:
    """Represents an individual track in the arrangement.

    Attributes:
        track_id: Unique track identifier.
        name: Track name.
        role: Track role/type.
        instrument: Instrument/sound source.
        plugin_chain: List of plugins/effects used.
        pan: Pan position (-100 to 100, L to R).
        volume_db: Volume in decibels.
        muted: Whether track is muted.
        soloed: Whether track is soloed.
        automation: Automation types used.
        notes: Production notes for this track.
    """
    track_id: str
    name: str = ""
    role: TrackRole = TrackRole.KEYS
    instrument: str = ""
    plugin_chain: List[str] = field(default_factory=list)
    pan: int = 0
    volume_db: float = 0.0
    muted: bool = False
    soloed: bool = False
    automation: List[str] = field(default_factory=list)
    notes: str = ""


@dataclass
class Composition:
    """Represents a musical composition.

    Attributes:
        id: Unique composition identifier.
        song_id: Associated song ID.
        melody: Melody description.
        harmony: Harmony/chord progression.
        rhythm: Rhythm/groove description.
        chord_progression: Chord progression notation.
        sections: Section-by-section breakdown.
        melodic_contour: Melodic shape description.
        counterpoint: Counterpoint elements.
        modulation_keys: Key changes/modulations.
        motifs: Recurring musical motifs.
        creativity_score: Originality/creativity score (0-10).
    """
    id: str
    song_id: str = ""
    melody: str = ""
    harmony: str = ""
    rhythm: str = ""
    chord_progression: str = ""
    sections: List[Dict[str, Any]] = field(default_factory=list)
    melodic_contour: str = ""
    counterpoint: str = ""
    modulation_keys: List[str] = field(default_factory=list)
    motifs: List[str] = field(default_factory=list)
    creativity_score: float = 0.0


@dataclass
class MixSettings:
    """Represents mixing settings and approach.

    Attributes:
        id: Unique mix identifier.
        song_id: Associated song ID.
        mix_style: Mixing style/approach.
        track_count: Total number of tracks.
        bus_structure: Mix bus structure.
        eq_strategy: Overall EQ strategy.
        compression_strategy: Compression approach.
        reverb_choices: Reverb types and settings.
        stereo_width: Stereo width strategy.
        loudness_target: Target loudness in LUFS.
        automation_passes: Automation passes needed.
        reference_mix: Reference mix for comparison.
        mix_quality: Mix quality rating (0-10).
    """
    id: str
    song_id: str = ""
    mix_style: str = ""
    track_count: int = 0
    bus_structure: List[str] = field(default_factory=list)
    eq_strategy: str = ""
    compression_strategy: str = ""
    reverb_choices: List[str] = field(default_factory=list)
    stereo_width: str = ""
    loudness_target: float = 0.0
    automation_passes: List[str] = field(default_factory=list)
    reference_mix: str = ""
    mix_quality: float = 0.0


@dataclass
class MasterSettings:
    """Represents mastering settings.

    Attributes:
        id: Unique master identifier.
        song_id: Associated song ID.
        master_style: Mastering style.
        target_lufs: Target integrated loudness (LUFS).
        true_peak_limit: True peak ceiling (dBTP).
        dynamic_range: Target dynamic range (dB).
        eq_curve: Master EQ adjustments.
        compression: Master compression settings.
        limiting: Limiter settings.
        stereo_imaging: Stereo imaging adjustments.
        reference: Reference track for mastering.
        formats: Output formats and resolutions.
        master_quality: Master quality rating (0-10).
    """
    id: str
    song_id: str = ""
    master_style: str = ""
    target_lufs: float = 0.0
    true_peak_limit: float = 0.0
    dynamic_range: float = 0.0
    eq_curve: str = ""
    compression: str = ""
    limiting: str = ""
    stereo_imaging: str = ""
    reference: str = ""
    formats: List[str] = field(default_factory=list)
    master_quality: float = 0.0


@dataclass
class MusicMetrics(SolutionMetrics):
    """Extended metrics for the music production solution.

    Attributes:
        track_quality: Average track quality score (0-10).
        production_time: Average production time per track (hours).
        creative_output: Creative output score (0-100).
        tracks_produced: Total tracks produced.
        avg_track_count: Average number of tracks per song.
        mix_quality: Average mix quality score.
        master_quality: Average master quality score.
        genre_versatility: Number of genres produced.
        creativity_score: Average creativity score.
        client_rating: Average client/artists satisfaction rating.
    """
    track_quality: float = 0.0
    production_time: float = 0.0
    creative_output: float = 0.0
    tracks_produced: int = 0
    avg_track_count: float = 0.0
    mix_quality: float = 0.0
    master_quality: float = 0.0
    genre_versatility: int = 0
    creativity_score: float = 0.0
    client_rating: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        """Convert music metrics to a dictionary."""
        base = super().to_dict()
        base.update({
            "track_quality": self.track_quality,
            "production_time": self.production_time,
            "creative_output": self.creative_output,
            "tracks_produced": self.tracks_produced,
            "avg_track_count": self.avg_track_count,
            "mix_quality": self.mix_quality,
            "master_quality": self.master_quality,
            "genre_versatility": self.genre_versatility,
            "creativity_score": self.creativity_score,
            "client_rating": self.client_rating,
        })
        return base


class MusicProducerSolution(BaseSolution):
    """Music Production AI solution template.

    Provides a complete, pre-configured music production solution
    with multi-agent collaboration for concept development,
    composition, arrangement, mixing, and mastering.

    Pre-configured agent roles:
        - Producer: Oversees overall production and creative direction
        - Composer: Creates melodies, harmonies, and musical arrangements
        - Mixing Engineer: Balances and processes individual tracks
        - Mastering Engineer: Finalizes audio for distribution

    Workflow:
        1. Concept - Define song concept, genre, and reference tracks
        2. Composition - Create melody, harmony, and arrangement
        3. Arrangement - Build full track with instruments and sounds
        4. Mixing - Balance, process, and blend all tracks
        5. Mastering - Final polish and prepare for release
        6. Release - Format for distribution platforms

    Configuration options:
        - genre: Primary music genre
        - quality_target: Target quality level
        - tempo_range: Preferred tempo range (BPM)
        - production_style: Production approach/style
        - target_platforms: Distribution platforms
    """

    def _build_metadata(self) -> SolutionMetadata:
        """Build metadata for the music producer solution."""
        return SolutionMetadata(
            name="music-producer",
            version="1.0.0",
            industry="music",
            description=(
                "AI-powered music production assistant with composition, "
                "arrangement, mixing, and mastering capabilities for "
                "professional-quality audio production."
            ),
            author="LiuHao AI OS",
            tags=["music", "production", "composition", "mixing", "mastering", "audio"],
            min_liuhao_version="1.0.0",
        )

    def _build_config_schema(self) -> None:
        """Define configuration fields for the music producer solution."""
        self.config_schema.add_field(
            name="genre",
            field_type="string",
            required=False,
            default="pop",
            description="Primary music genre",
            valid_values=[
                "pop", "rock", "hip_hop", "electronic", "rnb", "jazz",
                "classical", "country", "folk", "reggae", "metal", "indie",
            ],
        )
        self.config_schema.add_field(
            name="quality_target",
            field_type="string",
            required=False,
            default="radio_ready",
            description="Target production quality level",
            valid_values=["demo", "rough_mix", "radio_ready", "studio_quality", "mastered"],
        )
        self.config_schema.add_field(
            name="tempo_range",
            field_type="list",
            required=False,
            default=[90, 130],
            description="Preferred tempo range in BPM [min, max]",
        )
        self.config_schema.add_field(
            name="production_style",
            field_type="string",
            required=False,
            default="modern",
            description="Production approach and style",
            valid_values=["vintage", "modern", "minimalist", "maximalist", "lofi", "polished"],
        )
        self.config_schema.add_field(
            name="target_platforms",
            field_type="list",
            required=False,
            default=["spotify", "apple_music", "youtube"],
            description="Target distribution platforms",
        )
        self.config_schema.add_field(
            name="track_count_min",
            field_type="int",
            required=False,
            default=8,
            description="Minimum number of tracks in arrangement",
        )
        self.config_schema.add_field(
            name="vocal_type",
            field_type="string",
            required=False,
            default="male",
            description="Vocal type/instrumentation focus",
            valid_values=["male", "female", "duo", "instrumental", "band"],
        )

    def _on_initialize(self) -> None:
        """Initialize music production solution state."""
        self._agents: Dict[str, Dict[str, Any]] = {}
        self._song_concepts: Dict[str, SongConcept] = {}
        self._compositions: Dict[str, Composition] = {}
        self._tracks: Dict[str, TrackElement] = {}
        self._mixes: Dict[str, MixSettings] = {}
        self._masters: Dict[str, MasterSettings] = {}
        self._song_count = 0
        self._composition_count = 0
        self._track_count = 0
        self._mix_count = 0
        self._master_count = 0
        self._genres_used: set = set()

        # Initialize music-specific metrics
        self.music_metrics = MusicMetrics()

        # Genre-specific characteristics
        self._genre_characteristics = self._build_genre_characteristics()

    def _build_genre_characteristics(self) -> Dict[str, Any]:
        """Build genre production characteristics."""
        return {
            Genre.POP: {
                "tempo_range": [90, 130],
                "common_keys": ["C major", "G major", "D major", "A minor"],
                "typical_structure": "verse-pre-chorus-chorus-verse-pre-chorus-chorus-bridge-chorus-outro",
                "core_tracks": ["lead_vocal", "backing_vocal", "drums", "bass", "keys", "guitar", "synth"],
                "mix_style": "polished, vocal-forward, wide stereo",
                "target_lufs": -9.0,
                "creativity_baseline": 6.0,
            },
            Genre.ROCK: {
                "tempo_range": [100, 160],
                "common_keys": ["E minor", "A minor", "G major", "D major"],
                "typical_structure": "verse-chorus-verse-chorus-solo-bridge-chorus-outro",
                "core_tracks": ["lead_vocal", "backing_vocal", "drums", "bass", "guitar", "guitar_lead", "keys"],
                "mix_style": "raw, powerful, guitar-driven",
                "target_lufs": -10.0,
                "creativity_baseline": 7.0,
            },
            Genre.HIP_HOP: {
                "tempo_range": [70, 100],
                "common_keys": ["C minor", "A minor", "D minor", "G minor"],
                "typical_structure": "intro-verse-chorus-verse-chorus-bridge-chorus-outro",
                "core_tracks": ["lead_vocal", "ad_libs", "drums", "bass", "keys", "sample", "fx"],
                "mix_style": "punchy, low-end heavy, in-your-face vocal",
                "target_lufs": -8.0,
                "creativity_baseline": 7.5,
            },
            Genre.ELECTRONIC: {
                "tempo_range": [120, 140],
                "common_keys": ["A minor", "E minor", "C major", "G major"],
                "typical_structure": "intro-build-drop-breakdown-drop-outro",
                "core_tracks": ["drums", "bass", "synth_lead", "synth_pad", "fx", "vocal_sample", "percussion"],
                "mix_style": "wide stereo, sub-heavy, dynamic buildups",
                "target_lufs": -7.5,
                "creativity_baseline": 8.0,
            },
            Genre.RNB: {
                "tempo_range": [60, 90],
                "common_keys": ["D minor", "A minor", "E minor", "B minor"],
                "typical_structure": "verse-chorus-verse-chorus-bridge-chorus-outro",
                "core_tracks": ["lead_vocal", "backing_vocal", "drums", "bass", "keys", "synth", "percussion"],
                "mix_style": "smooth, lush, vocal-forward",
                "target_lufs": -9.5,
                "creativity_baseline": 7.0,
            },
            Genre.JAZZ: {
                "tempo_range": [80, 200],
                "common_keys": ["F major", "Bb major", "Eb major", "D minor"],
                "typical_structure": "head-solos-head-outro",
                "core_tracks": ["drums", "bass", "piano", "horn", "guitar", "percussion"],
                "mix_style": "natural, dynamic, room sound",
                "target_lufs": -14.0,
                "creativity_baseline": 8.5,
            },
            Genre.CLASSICAL: {
                "tempo_range": [60, 120],
                "common_keys": ["C major", "D minor", "G minor", "A major"],
                "typical_structure": "movement-based (sonata, symphony, etc.)",
                "core_tracks": ["strings", "woodwinds", "brass", "percussion", "piano", "choir"],
                "mix_style": "orchestral, natural, wide dynamic range",
                "target_lufs": -16.0,
                "creativity_baseline": 9.0,
            },
        }

    def _on_deploy(self) -> None:
        """Deploy music production agents and workflows."""
        agent_configs = [
            (MusicRole.PRODUCER, {
                "name": "Music Producer",
                "description": "Oversees overall production and creative direction",
                "system_prompt": self._build_producer_prompt(),
                "tools": ["arrangement", "sound_design", "vocal_production"],
            }),
            (MusicRole.COMPOSER, {
                "name": "Composer",
                "description": "Creates melodies, harmonies, and musical arrangements",
                "system_prompt": self._build_composer_prompt(),
                "tools": ["melody_generation", "chord_progressions", "counterpoint"],
            }),
            (MusicRole.MIXING_ENGINEER, {
                "name": "Mixing Engineer",
                "description": "Balances and processes individual tracks",
                "system_prompt": self._build_mixing_engineer_prompt(),
                "tools": ["eq", "compression", "reverb", "automation"],
            }),
            (MusicRole.MASTERING_ENGINEER, {
                "name": "Mastering Engineer",
                "description": "Finalizes audio for distribution",
                "system_prompt": self._build_mastering_engineer_prompt(),
                "tools": ["mastering_eq", "limiting", "stereo_imaging", "loudness"],
            }),
        ]

        for role, config in agent_configs:
            self._agents[role.value] = {
                "role": role.value,
                "name": config["name"],
                "status": "deployed",
                **config,
            }

    def _on_run(self, input_data: Dict[str, Any]) -> Dict[str, Any]:
        """Process a music production request through the full workflow.

        Args:
            input_data: Dictionary containing song concept parameters.

        Returns:
            Dictionary with production results including concept,
            composition, arrangement, mix, and mastering details.
        """
        # Step 1: Concept development
        concept = self._develop_concept(input_data)

        # Step 2: Composition
        composition = self._compose_music(concept)

        # Step 3: Arrangement
        tracks = self._arrange_tracks(concept, composition)

        # Step 4: Mixing
        mix = self._mix_track(concept, tracks)

        # Step 5: Mastering
        master = self._master_track(concept, mix)

        # Store and update metrics
        self._song_concepts[concept.id] = concept
        self._compositions[composition.id] = composition
        self._mixes[mix.id] = mix
        self._masters[master.id] = master

        self._genres_used.add(concept.genre.value)
        self.music_metrics.tracks_produced += 1
        self._update_music_metrics()

        return {
            "concept": {
                "id": concept.id,
                "title": concept.title,
                "genre": concept.genre.value,
                "sub_genres": concept.sub_genres,
                "mood": concept.mood,
                "tempo_bpm": concept.tempo_bpm,
                "key": concept.key,
                "time_signature": concept.time_signature,
                "structure": concept.structure,
                "target_duration_sec": concept.target_duration_sec,
                "lyrical_theme": concept.lyrical_theme,
            },
            "composition": {
                "id": composition.id,
                "melody": composition.melody,
                "harmony": composition.harmony,
                "rhythm": composition.rhythm,
                "chord_progression": composition.chord_progression,
                "sections_count": len(composition.sections),
                "motifs": composition.motifs,
                "creativity_score": composition.creativity_score,
            },
            "arrangement": {
                "track_count": len(tracks),
                "tracks": [
                    {
                        "id": t.track_id,
                        "name": t.name,
                        "role": t.role.value,
                        "instrument": t.instrument,
                        "pan": t.pan,
                        "volume_db": t.volume_db,
                        "plugin_chain": t.plugin_chain,
                    }
                    for t in tracks
                ],
                "bus_structure": mix.bus_structure,
            },
            "mix": {
                "id": mix.id,
                "mix_style": mix.mix_style,
                "eq_strategy": mix.eq_strategy,
                "compression_strategy": mix.compression_strategy,
                "reverb_choices": mix.reverb_choices,
                "stereo_width": mix.stereo_width,
                "loudness_target": mix.loudness_target,
                "mix_quality": mix.mix_quality,
            },
            "master": {
                "id": master.id,
                "master_style": master.master_style,
                "target_lufs": master.target_lufs,
                "true_peak_limit": master.true_peak_limit,
                "dynamic_range": master.dynamic_range,
                "formats": master.formats,
                "master_quality": master.master_quality,
            },
        }

    def _on_teardown(self) -> None:
        """Clean up music production solution resources."""
        self._agents.clear()
        self._song_concepts.clear()
        self._compositions.clear()
        self._tracks.clear()
        self._mixes.clear()
        self._masters.clear()
        self._song_count = 0
        self._composition_count = 0
        self._track_count = 0
        self._mix_count = 0
        self._master_count = 0
        self._genres_used.clear()

    # ------------------------------------------------------------------
    # Workflow steps
    # ------------------------------------------------------------------

    def _develop_concept(self, data: Dict[str, Any]) -> SongConcept:
        """Step 1: Develop the song concept."""
        self._song_count += 1
        concept_id = f"song-{self._song_count}"

        try:
            genre = Genre(data.get("genre", self.config.get("genre", "pop")))
        except ValueError:
            genre = Genre.POP

        chars = self._genre_characteristics.get(genre, self._genre_characteristics[Genre.POP])

        # Tempo
        tempo_range = data.get("tempo_range", self.config.get("tempo_range", [90, 130]))
        if len(tempo_range) == 2:
            tempo = (tempo_range[0] + tempo_range[1]) // 2
        else:
            tempo = chars["tempo_range"][0] + (chars["tempo_range"][1] - chars["tempo_range"][0]) // 2

        # Key
        key = data.get("key", chars["common_keys"][0])

        concept = SongConcept(
            id=concept_id,
            title=data.get("title", f"Track {self._song_count:03d}"),
            genre=genre,
            sub_genres=data.get("sub_genres", []),
            mood=data.get("mood", "energetic and uplifting"),
            tempo_bpm=tempo,
            key=key,
            time_signature=data.get("time_signature", "4/4"),
            structure=chars["typical_structure"],
            reference_tracks=data.get("reference_tracks", []),
            lyrical_theme=data.get("lyrical_theme", ""),
            target_duration_sec=data.get("target_duration", 210),
        )
        self._song_concepts[concept_id] = concept
        return concept

    def _compose_music(self, concept: SongConcept) -> Composition:
        """Step 2: Compose the musical elements."""
        self._composition_count += 1
        comp_id = f"comp-{self._composition_count}"

        genre = concept.genre
        chars = self._genre_characteristics.get(genre, self._genre_characteristics[Genre.POP])

        # Chord progression by genre
        progressions = {
            Genre.POP: "I-V-vi-IV (or vi-IV-I-V for minor)",
            Genre.ROCK: "I-IV-V with power chords and variations",
            Genre.HIP_HOP: "i-iv-VII-VI (minor key variations)",
            Genre.ELECTRONIC: "i-VI-III-VII or i-iv-v-i",
            Genre.RNB: "ii-V-I with extended and altered chords",
            Genre.JAZZ: "ii-V-I with extensions and substitutions",
            Genre.CLASSICAL: "I-IV-V-I with modulations and development",
        }
        chord_prog = progressions.get(genre, "I-V-vi-IV")

        # Melody description
        melody_desc = {
            Genre.POP: "Stepwise motion with occasional leaps, memorable hook in chorus",
            Genre.ROCK: "Powerful and melodic with wide vocal range",
            Genre.HIP_HOP: "Rhythmic speech patterns with melodic choruses",
            Genre.ELECTRONIC: "Catchy synth melodies with repetitive motifs",
            Genre.RNB: "Smooth, soulful melodies with vocal runs and ad-libs",
            Genre.JAZZ: "Improvisational, chromatic, and extended melodic lines",
            Genre.CLASSICAL: "Thematic development through counterpoint and variation",
        }.get(genre, "Melodic with stepwise motion")

        # Harmony
        harmony = {
            Genre.POP: "Triadic with seventh chord colorations",
            Genre.ROCK: "Power chords and open voicings",
            Genre.HIP_HOP: "Sampled chords, synth pads, minimal harmony",
            Genre.ELECTRONIC: "Synth pads, arpeggios, modal interchange",
            Genre.RNB: "Extended chords (9ths, 11ths, 13ths)",
            Genre.JAZZ: "Extended chords, substitutions, modal interchange",
            Genre.CLASSICAL: "Functional harmony with voice leading",
        }.get(genre, "Standard tonal harmony")

        # Rhythm/groove
        rhythm = {
            Genre.POP: "Four-on-the-floor or backbeat-driven",
            Genre.ROCK: "Driving rock beat with syncopated fills",
            Genre.HIP_HOP: "Boom-bap or trap-style with swung hi-hats",
            Genre.ELECTRONIC: "Four-on-the-floor with sidechained elements",
            Genre.RNB: "Laid-back grooves with pocket drumming",
            Genre.JAZZ: "Swing feel with ride cymbal pattern",
            Genre.CLASSICAL: "Varied according to form and style period",
        }.get(genre, "Standard pop rhythm")

        # Sections
        structure_parts = concept.structure.split("-")
        sections = []
        for i, part in enumerate(structure_parts):
            sections.append({
                "name": part,
                "order": i + 1,
                "duration_bars": 8 if "chorus" in part or "verse" in part else 4,
                "energy_level": "high" if "chorus" in part or "drop" in part else "medium",
            })

        # Motifs
        motifs = [
            "Main vocal hook motif in chorus",
            "Instrumental riff/ostinato",
            "Rhythmic motif in percussion",
        ]

        # Creativity score
        creativity_baseline = chars.get("creativity_baseline", 6.0)
        prod_style = self.config.get("production_style", "modern")
        style_bonus = {
            "vintage": -0.5,
            "modern": 0,
            "minimalist": 0.5,
            "maximalist": 1.0,
            "lofi": 0.5,
            "polished": -0.3,
        }.get(prod_style, 0)
        creativity_score = min(10.0, creativity_baseline + style_bonus)

        composition = Composition(
            id=comp_id,
            song_id=concept.id,
            melody=melody_desc,
            harmony=harmony,
            rhythm=rhythm,
            chord_progression=chord_prog,
            sections=sections,
            melodic_contour="Arch-shaped with peak in chorus",
            counterpoint="Two-voice counterpoint in instrumental sections",
            modulation_keys=[chars["common_keys"][1]] if len(chars["common_keys"]) > 1 else [],
            motifs=motifs,
            creativity_score=round(creativity_score, 1),
        )
        self._compositions[comp_id] = composition
        return composition

    def _arrange_tracks(
        self, concept: SongConcept, composition: Composition
    ) -> List[TrackElement]:
        """Step 3: Build the full arrangement."""
        genre = concept.genre
        chars = self._genre_characteristics.get(genre, self._genre_characteristics[Genre.POP])
        core_tracks = chars["core_tracks"]

        min_tracks = self.config.get("track_count_min", 8)
        tracks = []

        # Pan positions
        pan_positions = [-30, -15, 0, 15, 30, -45, 45, -20, 20]
        # Volume levels (relative)
        volumes = [-3, -6, -10, -12, -8, -14, -16, -5, -11]

        for idx, track_role_str in enumerate(core_tracks):
            self._track_count += 1
            track_id = f"track-{self._track_count}"

            try:
                role = TrackRole(track_role_str)
            except ValueError:
                role = TrackRole.KEYS

            # Instrument by role and genre
            instruments = {
                TrackRole.LEAD_VOCAL: "Lead Vocal",
                TrackRole.BACKING_VOCAL: "Harmony/Background Vocals",
                TrackRole.DRUMS: "Acoustic Drum Kit" if genre in (Genre.ROCK, Genre.JAZZ) else "Electronic Drums",
                TrackRole.BASS: "Electric Bass" if genre in (Genre.ROCK, Genre.POP) else "Synth Bass",
                TrackRole.GUITAR: "Electric Guitar" if genre == Genre.ROCK else "Acoustic Guitar",
                TrackRole.KEYS: "Acoustic Piano" if genre == Genre.JAZZ else "Synthesizer",
                TrackRole.SYNTH: "Analog Synth Lead",
                TrackRole.STRINGS: "String Ensemble",
                TrackRole.BRASS: "Horn Section",
                TrackRole.PERCUSSION: "Percussion Layer",
                TrackRole.FX: "Sound Effects/Atmosphere",
                TrackRole.AMBIENCE: "Ambient Pad",
            }
            instrument = instruments.get(role, "Synthesizer")

            # Plugin chain
            plugins = {
                TrackRole.LEAD_VOCAL: ["EQ", "Compressor", "De-esser", "Reverb", "Delay"],
                TrackRole.DRUMS: ["EQ", "Compressor", "Gate", "Saturation", "Bus Compressor"],
                TrackRole.BASS: ["EQ", "Compressor", "Saturation", "Sidechain Comp"],
                TrackRole.KEYS: ["EQ", "Compressor", "Chorus", "Reverb"],
                TrackRole.GUITAR: ["EQ", "Amp Sim", "Compressor", "Reverb", "Delay"],
                TrackRole.SYNTH: ["EQ", "Filter", "Chorus", "Delay", "Reverb"],
            }
            plugin_chain = plugins.get(role, ["EQ", "Compressor", "Reverb"])

            track = TrackElement(
                track_id=track_id,
                name=instrument,
                role=role,
                instrument=instrument,
                plugin_chain=plugin_chain,
                pan=pan_positions[idx % len(pan_positions)],
                volume_db=volumes[idx % len(volumes)],
                automation=["volume", "pan"] if idx < 3 else ["volume"],
                notes=f"{role.value.replace('_', ' ').title()} track for {concept.genre.value} production",
            )
            tracks.append(track)
            self._tracks[track_id] = track

        # Add extra tracks if below minimum
        while len(tracks) < min_tracks:
            self._track_count += 1
            extra_id = f"track-{self._track_count}"
            extra_idx = len(tracks)
            track = TrackElement(
                track_id=extra_id,
                name=f"Additional Layer {extra_idx}",
                role=TrackRole.FX,
                instrument="Textural Layer",
                plugin_chain=["EQ", "Reverb", "Delay"],
                pan=pan_positions[extra_idx % len(pan_positions)],
                volume_db=-18,
                notes="Additional texture layer",
            )
            tracks.append(track)
            self._tracks[extra_id] = track

        return tracks

    def _mix_track(self, concept: SongConcept, tracks: List[TrackElement]) -> MixSettings:
        """Step 4: Mix the track."""
        self._mix_count += 1
        mix_id = f"mix-{self._mix_count}"

        genre = concept.genre
        chars = self._genre_characteristics.get(genre, self._genre_characteristics[Genre.POP])
        prod_style = self.config.get("production_style", "modern")

        mix_style = chars["mix_style"]
        if prod_style == "minimalist":
            mix_style += " - minimalist approach with fewer elements"
        elif prod_style == "maximalist":
            mix_style += " - dense, layered, full frequency spectrum"
        elif prod_style == "lofi":
            mix_style += " - warm, degraded, character-driven"

        # Bus structure
        bus_structure = [
            "Master Bus",
            "Drum Bus",
            "Instrument Bus",
            "Vocal Bus",
            "FX Bus",
            "Sidechain Bus",
        ]

        # EQ strategy
        eq_strategies = {
            Genre.POP: "Carve out space for vocals in midrange, boost air on top",
            Genre.ROCK: "Aggressive EQ on guitars, punchy low end",
            Genre.HIP_HOP: "Boost sub-bass, forward vocal presence",
            Genre.ELECTRONIC: "Sculpt low end, wide stereo image",
            Genre.RNB: "Smooth midrange, warm low end, airy highs",
            Genre.JAZZ: "Natural EQ, preserve dynamics",
            Genre.CLASSICAL: "Minimal EQ, natural balance",
        }
        eq_strategy = eq_strategies.get(genre, "Balanced EQ across spectrum")

        # Compression strategy
        comp_strategies = {
            Genre.POP: "Moderate compression on vocals, light on instruments",
            Genre.ROCK: "Heavy drum compression, punchy bass",
            Genre.HIP_HOP: "Heavy vocal compression, parallel compression on drums",
            Genre.ELECTRONIC: "Sidechain compression everywhere",
            Genre.RNB: "Smooth vocal compression, gentle bus compression",
            Genre.JAZZ: "Minimal compression, preserve dynamics",
            Genre.CLASSICAL: "Very minimal, transparent compression only",
        }
        comp_strategy = comp_strategies.get(genre, "Balanced compression")

        # Reverb choices
        reverbs = ["Hall Reverb", "Plate Reverb", "Room Reverb", "Spring Reverb"]
        if genre in (Genre.JAZZ, Genre.CLASSICAL):
            reverb_choices = ["Hall Reverb", "Room Reverb"]
        elif genre == Genre.ELECTRONIC:
            reverb_choices = ["Hall Reverb", "Plate Reverb", "Delay"]
        else:
            reverb_choices = reverbs[:3]

        # Loudness target
        target_lufs = chars["target_lufs"]
        quality_target = self.config.get("quality_target", "radio_ready")
        if quality_target == "demo":
            target_lufs -= 3
        elif quality_target == "mastered":
            target_lufs += 1

        # Mix quality
        quality_scores = {
            "demo": 5.0,
            "rough_mix": 6.5,
            "radio_ready": 8.0,
            "studio_quality": 9.0,
            "mastered": 9.5,
        }
        mix_quality = quality_scores.get(quality_target, 7.0)

        # Automation passes
        automation = [
            "Volume automation on lead vocal",
            "Panning automation on effects",
            "Send level automation for dynamics",
            "Filter automation for buildups",
        ]

        mix = MixSettings(
            id=mix_id,
            song_id=concept.id,
            mix_style=mix_style,
            track_count=len(tracks),
            bus_structure=bus_structure,
            eq_strategy=eq_strategy,
            compression_strategy=comp_strategy,
            reverb_choices=reverb_choices,
            stereo_width="Wide stereo image with centered low end" if genre == Genre.ELECTRONIC else "Balanced stereo field",
            loudness_target=target_lufs,
            automation_passes=automation,
            reference_mix=f"Reference mix in {genre.value} style",
            mix_quality=mix_quality,
        )
        self._mixes[mix_id] = mix
        return mix

    def _master_track(self, concept: SongConcept, mix: MixSettings) -> MasterSettings:
        """Step 5: Master the track."""
        self._master_count += 1
        master_id = f"master-{self._master_count}"

        genre = concept.genre
        target_lufs = mix.loudness_target
        true_peak = -1.0

        # Dynamic range by genre
        dynamic_ranges = {
            Genre.POP: 8,
            Genre.ROCK: 10,
            Genre.HIP_HOP: 7,
            Genre.ELECTRONIC: 6,
            Genre.RNB: 9,
            Genre.JAZZ: 14,
            Genre.CLASSICAL: 18,
        }
        dr = dynamic_ranges.get(genre, 10)

        # Master EQ
        master_eq = (
            "Gentle high shelf boost for air, subtle low end tightening, "
            "midrange balance adjustment"
        )

        # Compression
        master_comp = (
            "Very gentle bus compression (1.5:1 ratio, 2-3 dB gain reduction) "
            "for glue, multiband compression for dynamic control"
        )

        # Limiting
        limiting = (
            f"Transparent limiter set to {true_peak} dBTP ceiling, "
            "minimal gain reduction (2-3 dB) for loudness"
        )

        # Output formats
        platforms = self.config.get("target_platforms", ["spotify"])
        formats = [
            "WAV 24-bit/48kHz - Master file",
            "MP3 320kbps - Streaming",
            "AAC 256kbps - Apple Music",
            "WAV 16-bit/44.1kHz - CD",
        ]

        # Master quality
        quality_target = self.config.get("quality_target", "radio_ready")
        quality_map = {
            "demo": 5.0,
            "rough_mix": 6.0,
            "radio_ready": 7.5,
            "studio_quality": 8.5,
            "mastered": 9.0,
        }
        master_quality = quality_map.get(quality_target, 7.0)

        master = MasterSettings(
            id=master_id,
            song_id=concept.id,
            master_style=f"Professional {genre.value} mastering",
            target_lufs=target_lufs,
            true_peak_limit=true_peak,
            dynamic_range=float(dr),
            eq_curve=master_eq,
            compression=master_comp,
            limiting=limiting,
            stereo_imaging="Slight stereo width enhancement on high end",
            reference=f"Reference tracks in {genre.value} style",
            formats=formats,
            master_quality=master_quality,
        )
        self._masters[master_id] = master
        return master

    # ------------------------------------------------------------------
    # Agent prompts
    # ------------------------------------------------------------------

    def _build_producer_prompt(self) -> str:
        """Build the system prompt for the producer role."""
        genre = self.config.get("genre", "pop")
        style = self.config.get("production_style", "modern")
        return (
            f"You are a music producer specializing in {genre} with a "
            f"{style} production aesthetic. Shape the overall sound and "
            "direction of each track. Make creative decisions about "
            "arrangement, instrumentation, sound choices, and performance "
            "direction. Balance artistic vision with commercial appeal. "
            "Ensure every element serves the song and creates emotional impact."
        )

    def _build_composer_prompt(self) -> str:
        """Build the system prompt for the composer role."""
        genre = self.config.get("genre", "pop")
        return (
            f"You are a composer and songwriter working in {genre} music. "
            "Create memorable melodies, compelling chord progressions, "
            "and effective song structures. Develop musical motifs and "
            "variations that create interest and cohesion. Consider "
            "harmonic movement, rhythmic interest, and melodic contour. "
            "Write music that serves the emotional intent of the song."
        )

    def _build_mixing_engineer_prompt(self) -> str:
        """Build the system prompt for the mixing engineer role."""
        return (
            "You are a mixing engineer with a keen ear for balance, "
            "clarity, and impact. Create mixes where every element has "
            "its space and contributes to the whole. Use EQ, compression, "
            "reverb, delay, and automation to create depth, dimension, "
            "and excitement. Make the vocal/lead element shine while "
            "maintaining a solid foundation. Deliver mixes that translate "
            "across all listening systems."
        )

    def _build_mastering_engineer_prompt(self) -> str:
        """Build the system prompt for the mastering engineer role."""
        return (
            "You are a mastering engineer who adds the final polish to "
            "audio productions. Optimize loudness, frequency balance, "
            "stereo width, and dynamic range for the target format and "
            "platform. Make subtle, transparent adjustments that enhance "
            "the mix without changing its character. Ensure consistency "
            "across an album/EP and deliver masters that meet industry "
            "standards while preserving artistic intent."
        )

    # ------------------------------------------------------------------
    # Metrics
    # ------------------------------------------------------------------

    def _update_music_metrics(self) -> None:
        """Update music-specific metrics."""
        if self._mixes:
            total = len(self._mixes)
            avg_mix = sum(m.mix_quality for m in self._mixes.values()) / total
            self.music_metrics.mix_quality = avg_mix
            self.music_metrics.track_quality = avg_mix

        if self._masters:
            total = len(self._masters)
            avg_master = sum(m.master_quality for m in self._masters.values()) / total
            self.music_metrics.master_quality = avg_master

        if self._compositions:
            total = len(self._compositions)
            avg_creativity = sum(c.creativity_score for c in self._compositions.values()) / total
            self.music_metrics.creativity_score = avg_creativity

        if self._tracks:
            # Average tracks per song
            if self._song_concepts:
                self.music_metrics.avg_track_count = (
                    len(self._tracks) / len(self._song_concepts)
                )

        self.music_metrics.genre_versatility = len(self._genres_used)

        # Production time estimate (hours)
        quality_target = self.config.get("quality_target", "radio_ready")
        time_estimates = {
            "demo": 4,
            "rough_mix": 8,
            "radio_ready": 16,
            "studio_quality": 24,
            "mastered": 30,
        }
        self.music_metrics.production_time = float(time_estimates.get(quality_target, 16))

        # Creative output score
        self.music_metrics.creative_output = (
            self.music_metrics.creativity_score * 10
        )

    def get_music_metrics(self) -> Dict[str, Any]:
        """Get the current music production metrics.

        Returns:
            Dictionary with music metrics including track quality,
            production time, creativity score, and mix quality.
        """
        return self.music_metrics.to_dict()
