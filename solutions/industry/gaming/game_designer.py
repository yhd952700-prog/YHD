"""
Game Design Assistant AI Solution Template.

A comprehensive AI-powered game design assistant solution for
game studios and development teams. Includes multi-agent roles
for game design, narrative writing, level design, and balance
analysis with playtesting support.

Typical workflow:
    concept -> mechanics design -> narrative -> level design
    -> balancing -> playtesting
"""

from typing import Optional, Dict, Any, List
from enum import Enum
from dataclasses import dataclass, field

from solutions.templates.base_solution import (
    BaseSolution,
    SolutionMetadata,
    SolutionMetrics,
)


class GameGenre(str, Enum):
    """Video game genres."""
    RPG = "rpg"
    FPS = "fps"
    STRATEGY = "strategy"
    PUZZLE = "puzzle"
    PLATFORMER = "platformer"
    ACTION = "action"
    ADVENTURE = "adventure"
    SIMULATION = "simulation"
    ROGUELIKE = "roguelike"
    MOBA = "moba"
    MMO = "mmo"
    CASUAL = "casual"


class DifficultyCurve(str, Enum):
    """Difficulty curve types."""
    LINEAR = "linear"
    EXPONENTIAL = "exponential"
    STEPPED = "stepped"
    EASY_START = "easy_start"
    HARD_START = "hard_start"
    U_SHAPED = "u_shaped"


class GameRole(str, Enum):
    """Agent roles in the game designer solution."""
    GAME_DESIGNER = "game_designer"
    NARRATIVE_WRITER = "narrative_writer"
    LEVEL_DESIGNER = "level_designer"
    BALANCE_ANALYST = "balance_analyst"


@dataclass
class GameConcept:
    """Represents a game concept document.

    Attributes:
        concept_id: Unique concept ID.
        title: Working title.
        genre: Game genre.
        elevator_pitch: One-sentence summary.
        description: Full concept description.
        core_loop: Core gameplay loop.
        unique_selling_points: Key differentiators.
        target_audience: Target player demographic.
        platform: Target platforms.
        art_style: Art style direction.
        monetization: Monetization strategy.
        scope_estimate: Estimated development scope.
    """
    concept_id: str
    title: str = ""
    genre: GameGenre = GameGenre.RPG
    elevator_pitch: str = ""
    description: str = ""
    core_loop: List[str] = field(default_factory=list)
    unique_selling_points: List[str] = field(default_factory=list)
    target_audience: str = ""
    platform: List[str] = field(default_factory=list)
    art_style: str = ""
    monetization: str = "premium"
    scope_estimate: str = "medium"


@dataclass
class GameMechanic:
    """Represents a game mechanic design.

    Attributes:
        mechanic_id: Unique mechanic ID.
        name: Mechanic name.
        type: Mechanic type (combat, movement, progression, etc.).
        description: Detailed description.
        rules: Game rules for this mechanic.
        inputs: Player inputs required.
        feedback: Player feedback (visual, audio, haptic).
        balance_parameters: Parameters for balancing.
        synergies: Mechanics this interacts with.
        counters: Counters/weaknesses.
        difficulty_modifiers: How difficulty affects this.
    """
    mechanic_id: str
    name: str
    type: str = ""
    description: str = ""
    rules: List[str] = field(default_factory=list)
    inputs: List[str] = field(default_factory=list)
    feedback: Dict[str, str] = field(default_factory=dict)
    balance_parameters: Dict[str, Any] = field(default_factory=dict)
    synergies: List[str] = field(default_factory=list)
    counters: List[str] = field(default_factory=list)
    difficulty_modifiers: Dict[str, Any] = field(default_factory=dict)


@dataclass
class LevelDesign:
    """Represents a level design specification.

    Attributes:
        level_id: Unique level ID.
        name: Level name.
        level_number: Sequence number.
        theme: Level theme/setting.
        objectives: Level objectives.
        layout_description: Description of level layout.
        enemies: Enemies/waves in this level.
        puzzles: Puzzles or challenges.
        collectibles: Collectible items.
        difficulty: Difficulty rating (1-10).
        playtime_minutes: Estimated playtime.
        secrets: Secret areas/items.
        boss: Boss encounter description (if any).
    """
    level_id: str
    name: str = ""
    level_number: int = 1
    theme: str = ""
    objectives: List[str] = field(default_factory=list)
    layout_description: str = ""
    enemies: List[Dict[str, Any]] = field(default_factory=list)
    puzzles: List[str] = field(default_factory=list)
    collectibles: List[str] = field(default_factory=list)
    difficulty: int = 3
    playtime_minutes: int = 20
    secrets: List[str] = field(default_factory=list)
    boss: str = ""


@dataclass
class BalanceReport:
    """Represents a game balance analysis report.

    Attributes:
        report_id: Unique report ID.
        system: System being balanced (combat, economy, progression, etc.).
        overall_balance_score: Overall balance score (0-100).
        overpowered_elements: Elements that are too strong.
        underpowered_elements: Elements that are too weak.
        difficulty_curve: Assessed difficulty curve type.
        recommended_adjustments: Suggested balance changes.
        player_skill_impact: How much skill affects outcomes.
        rng_impact: How much RNG affects outcomes.
        progression_pacing: Pacing of player progression.
        economy_balance: In-game economy balance score.
        playtest_feedback: Aggregated playtest feedback.
    """
    report_id: str
    system: str = "combat"
    overall_balance_score: float = 70.0
    overpowered_elements: List[str] = field(default_factory=list)
    underpowered_elements: List[str] = field(default_factory=list)
    difficulty_curve: DifficultyCurve = DifficultyCurve.LINEAR
    recommended_adjustments: List[str] = field(default_factory=list)
    player_skill_impact: float = 0.0
    rng_impact: float = 0.0
    progression_pacing: str = "balanced"
    economy_balance: float = 0.0
    playtest_feedback: List[str] = field(default_factory=list)


@dataclass
class GameMetrics(SolutionMetrics):
    """Extended metrics for the game designer solution.

    Attributes:
        player_engagement: Average player engagement score.
        retention_rate: Player retention rate.
        monetization_efficiency: Monetization efficiency score.
        concepts_generated: Total game concepts generated.
        levels_designer: Total levels designed.
        mechanics_balanced: Total mechanics balanced.
        playtest_satisfaction: Average playtest satisfaction.
        avg_playtime_hours: Average estimated playtime.
    """
    player_engagement: float = 0.0
    retention_rate: float = 0.0
    monetization_efficiency: float = 0.0
    concepts_generated: int = 0
    levels_designer: int = 0
    mechanics_balanced: int = 0
    playtest_satisfaction: float = 0.0
    avg_playtime_hours: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        """Convert game metrics to a dictionary."""
        base = super().to_dict()
        base.update({
            "player_engagement": self.player_engagement,
            "retention_rate": self.retention_rate,
            "monetization_efficiency": self.monetization_efficiency,
            "concepts_generated": self.concepts_generated,
            "levels_designer": self.levels_designer,
            "mechanics_balanced": self.mechanics_balanced,
            "playtest_satisfaction": self.playtest_satisfaction,
            "avg_playtime_hours": self.avg_playtime_hours,
        })
        return base


class GameDesignerSolution(BaseSolution):
    """Game Design Assistant AI solution template.

    Provides a complete, pre-configured game design assistant
    with multi-agent collaboration for game design, narrative,
    level design, balancing, and playtesting support.

    Pre-configured agent roles:
        - Game Designer: Creates game concepts and mechanics
        - Narrative Writer: Develops story, characters, and dialogue
        - Level Designer: Designs levels, puzzles, and encounters
        - Balance Analyst: Analyzes and tunes game balance

    Workflow:
        1. Concept - Create game concept and high-level design
        2. Mechanics Design - Design core gameplay systems
        3. Narrative - Develop story, characters, and world
        4. Level Design - Create level layouts and challenges
        5. Balancing - Tune difficulty, economy, and progression
        6. Playtesting - Generate playtest scenarios and feedback

    Configuration options:
        - genre: Primary game genre
        - target_platform: Target platform(s)
        - scope: Project scope (small, medium, large)
        - art_style: Art style direction
        - monetization_model: Monetization approach
    """

    def _build_metadata(self) -> SolutionMetadata:
        """Build metadata for the game designer solution."""
        return SolutionMetadata(
            name="game-designer",
            version="1.0.0",
            industry="gaming",
            description=(
                "AI-powered game design assistant solution with concept design, "
                "narrative, level design, balancing, and playtesting support."
            ),
            author="LiuHao AI OS",
            tags=["gaming", "game-design", "narrative", "level-design", "balance"],
            min_liuhao_version="1.0.0",
        )

    def _build_config_schema(self) -> None:
        """Define configuration fields for the game designer solution."""
        self.config_schema.add_field(
            name="genre",
            field_type="string",
            required=True,
            description="Primary game genre",
            valid_values=[g.value for g in GameGenre],
        )
        self.config_schema.add_field(
            name="target_platform",
            field_type="string",
            required=False,
            default="pc",
            description="Target platform",
            valid_values=["pc", "console", "mobile", "multiplatform", "vr", "web"],
        )
        self.config_schema.add_field(
            name="scope",
            field_type="string",
            required=False,
            default="medium",
            description="Project scope",
            valid_values=["small", "medium", "large", "aaa"],
        )
        self.config_schema.add_field(
            name="art_style",
            field_type="string",
            required=False,
            default="realistic",
            description="Art style direction",
            valid_values=["pixel_art", "cartoon", "anime", "realistic", "stylized", "retro", "low_poly"],
        )
        self.config_schema.add_field(
            name="monetization_model",
            field_type="string",
            required=False,
            default="premium",
            description="Monetization approach",
            valid_values=["premium", "free_to_play", "battle_pass", "subscription", "dlc", "early_access"],
        )
        self.config_schema.add_field(
            name="target_audience_age",
            field_type="string",
            required=False,
            default="teen",
            description="Target age rating",
            valid_values=["everyone", "everyone_10+", "teen", "mature", "adults_only"],
        )
        self.config_schema.add_field(
            name="difficult_curve_type",
            field_type="string",
            required=False,
            default="linear",
            description="Target difficulty curve",
            valid_values=[c.value for c in DifficultyCurve],
        )

    def _on_initialize(self) -> None:
        """Initialize game designer solution state."""
        self._agents: Dict[str, Dict[str, Any]] = {}
        self._concepts: Dict[str, GameConcept] = {}
        self._mechanics: Dict[str, GameMechanic] = {}
        self._levels: Dict[str, LevelDesign] = {}
        self._balance_reports: Dict[str, BalanceReport] = {}
        self._concept_count = 0
        self._mechanic_count = 0
        self._level_count = 0
        self._balance_count = 0

        # Initialize game-specific metrics
        self.game_metrics = GameMetrics()

    def _on_deploy(self) -> None:
        """Deploy game designer agents and workflows."""
        agent_configs = [
            (GameRole.GAME_DESIGNER, {
                "name": "Game Designer",
                "description": "Creates game concepts and mechanics",
                "system_prompt": self._build_game_designer_prompt(),
                "tools": ["concept_generator", "mechanics_designer", "systems_design"],
            }),
            (GameRole.NARRATIVE_WRITER, {
                "name": "Narrative Writer",
                "description": "Develops story, characters, and dialogue",
                "system_prompt": self._build_narrative_writer_prompt(),
                "tools": ["story_generator", "character_creator", "dialogue_writer"],
            }),
            (GameRole.LEVEL_DESIGNER, {
                "name": "Level Designer",
                "description": "Designs levels, puzzles, and encounters",
                "system_prompt": self._build_level_designer_prompt(),
                "tools": ["level_generator", "encounter_designer", "puzzle_creator"],
            }),
            (GameRole.BALANCE_ANALYST, {
                "name": "Balance Analyst",
                "description": "Analyzes and tunes game balance",
                "system_prompt": self._build_balance_analyst_prompt(),
                "tools": ["balance_simulator", "difficulty_tuner", "economy_analyzer"],
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
        """Process a game design request through the full workflow.

        Args:
            input_data: Dictionary containing game design request data.

        Returns:
            Dictionary with game design results including concept,
            mechanics, narrative, levels, and balance analysis.
        """
        # Step 1: Game concept
        concept = self._create_concept(input_data)

        # Step 2: Mechanics design
        mechanics = self._design_mechanics(concept)

        # Step 3: Narrative
        narrative = self._create_narrative(concept)

        # Step 4: Level design
        levels = self._design_levels(concept)

        # Step 5: Balancing
        balance = self._analyze_balance(concept, mechanics, levels)

        # Step 6: Playtesting
        playtest = self._prepare_playtesting(concept, levels)

        # Update metrics
        self.game_metrics.concepts_generated += 1
        self.game_metrics.levels_designer += len(levels)
        self.game_metrics.mechanics_balanced += len(mechanics)
        self._update_game_metrics(concept, levels)

        return {
            "concept": {
                "id": concept.concept_id,
                "title": concept.title,
                "genre": concept.genre.value,
                "elevator_pitch": concept.elevator_pitch,
                "core_loop": concept.core_loop,
                "unique_selling_points": concept.unique_selling_points,
                "target_audience": concept.target_audience,
                "platform": concept.platform,
                "art_style": concept.art_style,
                "monetization": concept.monetization,
                "scope": concept.scope_estimate,
            },
            "mechanics": [
                {
                    "id": m.mechanic_id,
                    "name": m.name,
                    "type": m.type,
                    "rules_count": len(m.rules),
                    "synergies": m.synergies,
                }
                for m in mechanics
            ],
            "narrative": narrative,
            "level_design": {
                "total_levels": len(levels),
                "levels": [
                    {
                        "id": l.level_id,
                        "name": l.name,
                        "number": l.level_number,
                        "theme": l.theme,
                        "difficulty": l.difficulty,
                        "playtime_minutes": l.playtime_minutes,
                        "objectives": l.objectives,
                    }
                    for l in levels[:5]
                ],
                "total_playtime_hours": sum(l.playtime_minutes for l in levels) / 60,
            },
            "balance": {
                "overall_score": balance.overall_balance_score,
                "system": balance.system,
                "difficulty_curve": balance.difficulty_curve.value,
                "overpowered": balance.overpowered_elements,
                "underpowered": balance.underpowered_elements,
                "adjustments": balance.recommended_adjustments,
                "skill_impact": balance.player_skill_impact,
                "rng_impact": balance.rng_impact,
                "economy_balance": balance.economy_balance,
            },
            "playtesting": playtest,
        }

    def _on_teardown(self) -> None:
        """Clean up game designer solution resources."""
        self._agents.clear()
        self._concepts.clear()
        self._mechanics.clear()
        self._levels.clear()
        self._balance_reports.clear()
        self._concept_count = 0
        self._mechanic_count = 0
        self._level_count = 0
        self._balance_count = 0

    # ------------------------------------------------------------------
    # Workflow steps
    # ------------------------------------------------------------------

    def _create_concept(self, data: Dict[str, Any]) -> GameConcept:
        """Step 1: Create game concept document."""
        self._concept_count += 1
        concept_id = f"concept-{self._concept_count}"

        genre = GameGenre(data.get("genre", self.config.get("genre", GameGenre.RPG.value)))
        title = data.get("title", f"Untitled {genre.value.title()} Project")

        # Core loop by genre
        core_loops = {
            GameGenre.RPG: ["Explore", "Fight", "Loot", "Level Up", "Repeat"],
            GameGenre.FPS: ["Move", "Aim", "Shoot", "Reload", "Advance"],
            GameGenre.STRATEGY: ["Gather Resources", "Build", "Train Units", "Attack", "Expand"],
            GameGenre.PUZZLE: ["Observe", "Analyze", "Test", "Solve", "Next Level"],
            GameGenre.PLATFORMER: ["Run", "Jump", "Avoid", "Collect", "Reach Goal"],
            GameGenre.ROGUELIKE: ["Enter Floor", "Fight", "Collect Items", "Die/Progress", "Run Again"],
        }
        core_loop = core_loops.get(genre, ["Play", "Win", "Repeat"])

        # USPs
        usps = data.get("unique_selling_points", [
            f"Innovative {genre.value} mechanic twist",
            "Unique art style with stunning visuals",
            "Deep and engaging story",
            "Replayable procedural content",
        ])

        concept = GameConcept(
            concept_id=concept_id,
            title=title,
            genre=genre,
            elevator_pitch=data.get("elevator_pitch", f"A {genre.value} game with innovative mechanics and stunning visuals."),
            description=data.get("description", f"A {genre.value} experience that pushes the boundaries of the genre with fresh mechanics and deep gameplay."),
            core_loop=core_loop,
            unique_selling_points=usps,
            target_audience=f"{self.config.get('target_audience_age', 'teen')} players who enjoy {genre.value} games",
            platform=[self.config.get("target_platform", "pc")],
            art_style=self.config.get("art_style", "stylized"),
            monetization=self.config.get("monetization_model", "premium"),
            scope_estimate=self.config.get("scope", "medium"),
        )
        self._concepts[concept_id] = concept
        return concept

    def _design_mechanics(self, concept: GameConcept) -> List[GameMechanic]:
        """Step 2: Design core game mechanics."""
        genre = concept.genre
        mechanics = []

        # Genre-specific mechanics
        if genre == GameGenre.RPG:
            mech_data = [
                ("Combat System", "combat", ["Turn-based battles", "Elemental weakness system", "Combo attacks"]),
                ("Progression System", "progression", ["XP and leveling", "Skill tree", "Equipment upgrades"]),
                ("Party System", "social", ["Character recruitment", "Relationship building", "Party composition"]),
                ("Crafting System", "crafting", ["Material gathering", "Recipe discovery", "Item upgrades"]),
            ]
        elif genre == GameGenre.FPS:
            mech_data = [
                ("Gunplay", "combat", ["Weapon recoil", "Aim down sights", "Weapon switching"]),
                ("Movement", "movement", ["Slide", "Wall run", "Mantle", "Dash"]),
                ("Ability System", "ability", ["Ultimate ability", "Passive perks", "Equipment mods"]),
            ]
        elif genre == GameGenre.STRATEGY:
            mech_data = [
                ("Resource Management", "economy", ["Multiple resource types", "Supply chains", "Trade system"]),
                ("Combat", "combat", ["Unit counters", "Formation system", "Terrain bonuses"]),
                ("Tech Tree", "progression", ["Research system", "Era progression", "Unit upgrades"]),
            ]
        elif genre == GameGenre.ROGUELIKE:
            mech_data = [
                ("Combat", "combat", ["Permadeath", "Procedural rooms", "Item synergies"]),
                ("Build System", "progression", ["Item combinations", "Character builds", "Rarity tiers"]),
                ("Meta-progression", "meta", ["Persistent upgrades", "Character unlocks", "Starting options"]),
            ]
        else:
            mech_data = [
                ("Core Gameplay", "core", ["Primary interaction", "Win condition", "Lose condition"]),
                ("Progression", "progression", ["Level system", "Unlocks", "Scoring"]),
                ("Special Ability", "ability", ["Power-up system", "Cooldown mechanics"]),
            ]

        for name, mtype, rules in mech_data:
            self._mechanic_count += 1
            mech = GameMechanic(
                mechanic_id=f"mech-{self._mechanic_count}",
                name=name,
                type=mtype,
                description=f"{name} mechanic for the {genre.value} game.",
                rules=rules,
                inputs=["Mouse", "Keyboard", "Controller"],
                feedback={
                    "visual": "VFX and screen effects",
                    "audio": "Sound effects and music",
                    "haptic": "Controller vibration",
                },
                balance_parameters={
                    "base_value": 1.0,
                    "scaling_factor": 1.1,
                    "cooldown": 5.0,
                    "duration": 3.0,
                },
                synergies=[f"Synergizes with {m[0]}" for m in mech_data[:2] if m[0] != name],
                counters=[f"Countered by specific conditions"],
                difficulty_modifiers={
                    "easy": 0.7,
                    "normal": 1.0,
                    "hard": 1.3,
                    "expert": 1.6,
                },
            )
            mechanics.append(mech)
            self._mechanics[mech.mechanic_id] = mech

        return mechanics

    def _create_narrative(self, concept: GameConcept) -> Dict[str, Any]:
        """Step 3: Create narrative elements."""
        genre = concept.genre

        # Narrative elements by genre
        if genre == GameGenre.RPG:
            story = "A chosen hero must rise against an ancient evil that threatens the realm, gathering allies and uncovering secrets along the way."
            themes = ["Hero's journey", "Friendship", "Sacrifice", "Redemption"]
        elif genre == GameGenre.FPS:
            story = "An elite soldier must stop a global threat behind enemy lines, uncovering a conspiracy that goes deeper than anyone imagined."
            themes = ["War", "Betrayal", "Heroism", "Technology"]
        elif genre == GameGenre.STRATEGY:
            story = "Build your civilization from humble beginnings to galactic empire, making choices that will shape the fate of your people."
            themes = ["Civilization", "Progress", "Conflict", "Diplomacy"]
        else:
            story = "Navigate challenges and discover what lies beyond in this unique gaming experience."
            themes = ["Discovery", "Challenge", "Growth"]

        characters = [
            {"name": "Protagonist", "role": "Player character", "archetype": "Hero"},
            {"name": "Mentor", "role": "Guide and teacher", "archetype": "Wise mentor"},
            {"name": "Antagonist", "role": "Primary villain", "archetype": "Dark lord/ruler"},
            {"name": "Companion", "role": "Ally and friend", "archetype": "Loyal sidekick"},
        ]

        return {
            "story_premise": story,
            "themes": themes,
            "characters": characters,
            "world_setting": f"A rich {genre.value} world with diverse environments and cultures.",
            "story_beats": [
                "Introduction and inciting incident",
                "Rising action and first major challenge",
                "Midpoint twist and revelation",
                "Climactic confrontation",
                "Resolution and aftermath",
            ],
            "dialogue_style": "Witty and natural with emotional depth",
        }

    def _design_levels(self, concept: GameConcept) -> List[LevelDesign]:
        """Step 4: Design game levels."""
        scope = self.config.get("scope", "medium")
        num_levels = {"small": 5, "medium": 10, "large": 20, "aaa": 30}.get(scope, 10)

        levels = []
        themes = [
            "Forest Entrance", "Crystal Caves", "Ancient Ruins", "Frozen Peaks",
            "Volcanic Depths", "Sky Temple", "Dark Fortress", "Final Showdown",
            "Hidden Gardens", "Forgotten Library",
        ]

        for i in range(num_levels):
            self._level_count += 1
            level_id = f"level-{self._level_count}"

            # Difficulty curve
            curve = self.config.get("difficult_curve_type", "linear")
            if curve == "linear":
                difficulty = min(10, 1 + i)
            elif curve == "easy_start":
                difficulty = min(10, 1 + (i * 0.8))
            elif curve == "exponential":
                difficulty = min(10, 1 + (i ** 1.2))
            elif curve == "stepped":
                difficulty = min(10, 1 + (i // 2) * 2)
            else:
                difficulty = min(10, 1 + i)

            # Playtime per level
            base_playtime = 15 + (i * 3)

            # Boss every 3-5 levels
            is_boss = (i + 1) % 5 == 0
            boss_name = f"Boss {i // 5 + 1}: {themes[i % len(themes)]} Guardian" if is_boss else ""

            level = LevelDesign(
                level_id=level_id,
                name=f"Level {i+1}: {themes[i % len(themes)]}",
                level_number=i + 1,
                theme=themes[i % len(themes)],
                objectives=[
                    f"Navigate the {themes[i % len(themes)].lower()}",
                    "Find the exit/goal",
                    "Collect secrets along the way",
                ] + (["Defeat the boss"] if is_boss else []),
                layout_description=(
                    f"A {themes[i % len(themes)].lower()}-themed level with "
                    f"multiple paths, hidden areas, and increasing challenge."
                ),
                enemies=[
                    {"type": f"Enemy Type A", "count": 3 + i},
                    {"type": f"Enemy Type B", "count": max(0, i - 2)},
                ],
                puzzles=[f"Environmental puzzle {i+1}"] if i > 2 else [],
                collectibles=["Health pickup", "Secret area", "Upgrade item"][:i % 3 + 1],
                difficulty=round(difficulty, 1),
                playtime_minutes=base_playtime + (10 if is_boss else 0),
                secrets=["Hidden room", "Alternate path"][:i % 2 + 1],
                boss=boss_name,
            )
            levels.append(level)
            self._levels[level_id] = level

        return levels

    def _analyze_balance(
        self, concept: GameConcept, mechanics: List[GameMechanic], levels: List[LevelDesign]
    ) -> BalanceReport:
        """Step 5: Analyze game balance."""
        self._balance_count += 1
        report_id = f"balance-{self._balance_count}"

        # Overall balance score
        balance_score = 72.0

        # Find potential balance issues
        overpowered = []
        underpowered = []

        for mech in mechanics:
            if "ultimate" in mech.name.lower() or "ultimate" in mech.description.lower():
                overpowered.append(f"{mech.name} - May need damage/cooldown tuning")
            if "passive" in mech.type:
                underpowered.append(f"{mech.name} - Impact may be too subtle")

        # Difficulty curve assessment
        curve_type = DifficultyCurve(self.config.get("difficult_curve_type", DifficultyCurve.LINEAR.value))

        # Skill vs RNG impact
        genre = concept.genre
        if genre in (GameGenre.ROGUELIKE, GameGenre.STRATEGY):
            rng_impact = 0.35
            skill_impact = 0.65
        elif genre in (GameGenre.FPS, GameGenre.PLATFORMER, GameGenre.ACTION):
            rng_impact = 0.15
            skill_impact = 0.85
        elif genre == GameGenre.RPG:
            rng_impact = 0.25
            skill_impact = 0.55
        else:
            rng_impact = 0.20
            skill_impact = 0.70

        report = BalanceReport(
            report_id=report_id,
            system="core_gameplay",
            overall_balance_score=balance_score,
            overpowered_elements=overpowered if overpowered else ["No major imbalances detected"],
            underpowered_elements=underpowered if underpowered else ["No major weak systems detected"],
            difficulty_curve=curve_type,
            recommended_adjustments=[
                "Tune early game difficulty to ease players in",
                "Test endgame balance with veteran players",
                "Adjust economy for both casual and hardcore players",
                "Balance around player skill, not just stats",
            ],
            player_skill_impact=skill_impact,
            rng_impact=rng_impact,
            progression_pacing="steady",
            economy_balance=75.0,
            playtest_feedback=[
                "First hour engagement is strong",
                "Mid-game pacing needs adjustment",
                "Boss encounters are highlights",
                "Progression feels rewarding",
            ],
        )
        self._balance_reports[report_id] = report
        return report

    def _prepare_playtesting(
        self, concept: GameConcept, levels: List[LevelDesign]
    ) -> Dict[str, Any]:
        """Step 6: Prepare playtesting scenarios and feedback framework."""
        return {
            "playtest_phases": [
                "Internal Alpha - Core mechanics testing",
                "Closed Beta - Balance and polish",
                "Open Beta - Stress testing and final polish",
            ],
            "test_scenarios": [
                "Tutorial/first 30 minutes experience",
                "Combat encounters difficulty",
                "Puzzle solutions and alternatives",
                "Economy and progression pacing",
                "Boss encounters",
                "Endgame content",
            ],
            "metrics_to_track": {
                "engagement": ["session_length", "return_rate", "completion_rate"],
                "difficulty": ["death_count", "time_spent", "hint_usage"],
                "economy": ["currency_flow", "item_usage", "shop_purchases"],
                "satisfaction": ["fun_rating", "recommend_score", "emotional_response"],
            },
            "target_playtime_hours": sum(l.playtime_minutes for l in levels) / 60,
            "estimated_replay_value": "high" if concept.genre in (GameGenre.ROGUELIKE, GameGenre.STRATEGY) else "medium",
            "feedback_categories": [
                "Fun/Enjoyment",
                "Difficulty",
                "Controls",
                "Visuals/Audio",
                "Bugs/Issues",
                "Overall Satisfaction",
            ],
        }

    # ------------------------------------------------------------------
    # Agent prompts
    # ------------------------------------------------------------------

    def _build_game_designer_prompt(self) -> str:
        """Build the system prompt for the game designer role."""
        genre = self.config.get("genre", "rpg")
        return (
            f"You are a creative game designer specializing in {genre} games. "
            f"Design innovative mechanics, compelling core loops, and engaging "
            f"game systems. Balance fun with challenge, accessibility with depth. "
            f"Consider player psychology, flow states, and reward structures. "
            f"Create experiences that are both intuitive and masterable."
        )

    def _build_narrative_writer_prompt(self) -> str:
        """Build the system prompt for the narrative writer role."""
        return (
            "You are a narrative designer and writer for video games. "
            "Create compelling stories, memorable characters, and "
            "meaningful choices. Write dialogue that reveals character "
            "and advances the plot. Design branching narratives where "
            "choices have real consequences. Build worlds players want to explore."
        )

    def _build_level_designer_prompt(self) -> str:
        """Build the system prompt for the level designer role."""
        return (
            "You are a level designer who creates engaging, well-paced "
            "levels. Design layouts that guide players naturally while "
            "rewarding exploration. Create encounters that teach and test "
            "skills. Balance difficulty with fairness. Use environmental "
            "storytelling to reinforce narrative and worldbuilding."
        )

    def _build_balance_analyst_prompt(self) -> str:
        """Build the system prompt for the balance analyst role."""
        return (
            "You are a game balance analyst. Analyze game systems for "
            "fairness, pacing, and fun factor. Identify overpowered and "
            "underpowered elements. Tune difficulty curves to provide "
            "appropriate challenge. Analyze economy systems, progression, "
            "and combat balance. Use data and playtesting feedback to "
            "make evidence-based adjustments."
        )

    # ------------------------------------------------------------------
    # Metrics
    # ------------------------------------------------------------------

    def _update_game_metrics(self, concept: GameConcept, levels: List[LevelDesign]) -> None:
        """Update game-specific metrics."""
        self.game_metrics.player_engagement = 0.82
        self.game_metrics.retention_rate = 0.65
        self.game_metrics.playtest_satisfaction = 4.1
        self.game_metrics.avg_playtime_hours = sum(l.playtime_minutes for l in levels) / 60

        monet_efficiency = {
            "premium": 0.85,
            "free_to_play": 0.65,
            "battle_pass": 0.75,
            "subscription": 0.80,
            "dlc": 0.70,
        }
        self.game_metrics.monetization_efficiency = monet_efficiency.get(
            self.config.get("monetization_model", "premium"), 0.7
        )

    def get_game_metrics(self) -> Dict[str, Any]:
        """Get the current game designer metrics.

        Returns:
            Dictionary with game metrics including engagement, retention,
            and playtest satisfaction.
        """
        return self.game_metrics.to_dict()
