"""
Sports Coaching AI Solution Template.

A comprehensive AI-powered sports coaching assistant for
professional teams, athletic organizations, and individual
athletes. Includes multi-agent roles for head coach, tactical
analyst, fitness trainer, and scout.

Typical workflow:
    opponent analysis -> game plan -> training design
    -> performance tracking -> in-game adjustments
"""

from typing import Optional, Dict, Any, List
from enum import Enum
from dataclasses import dataclass, field

from solutions.templates.base_solution import (
    BaseSolution,
    SolutionMetadata,
    SolutionMetrics,
)


class SportType(str, Enum):
    """Types of sports."""
    SOCCER = "soccer"
    BASKETBALL = "basketball"
    FOOTBALL = "football"
    BASEBALL = "baseball"
    TENNIS = "tennis"
    SWIMMING = "swimming"
    TRACK_FIELD = "track_and_field"
    BASKETBALL_COLLEGE = "college_basketball"
    HOCKEY = "hockey"
    VOLLEYBALL = "volleyball"
    RUGBY = "rugby"
    GOLF = "golf"


class GamePhase(str, Enum):
    """Phases of a game/competition."""
    PRE_GAME = "pre_game"
    FIRST_HALF = "first_half"
    HALFTIME = "halftime"
    SECOND_HALF = "second_half"
    POST_GAME = "post_game"
    OFFSEASON = "offseason"
    PRESEASON = "preseason"
    REGULAR_SEASON = "regular_season"
    PLAYOFFS = "playoffs"


class TrainingIntensity(str, Enum):
    """Training intensity levels."""
    LIGHT = "light"
    MODERATE = "moderate"
    HIGH = "high"
    MAXIMAL = "maximal"
    RECOVERY = "recovery"


class PlayerPosition(str, Enum):
    """Generic player position categories."""
    OFFENSE = "offense"
    DEFENSE = "defense"
    MIDFIELD = "midfield"
    GOALKEEPER = "goalkeeper"
    FORWARD = "forward"
    UTILITY = "utility"
    PITCHER = "pitcher"
    CATCHER = "catcher"
    QUARTERBACK = "quarterback"
    LINEMAN = "lineman"
    GUARD = "guard"
    CENTER = "center"
    WING = "wing"
    SETTER = "setter"
    LIBERO = "libero"


class CoachRole(str, Enum):
    """Agent roles in the sports coaching solution."""
    HEAD_COACH = "head_coach"
    TACTICAL_ANALYST = "tactical_analyst"
    FITNESS_TRAINER = "fitness_trainer"
    SCOUT = "scout"


@dataclass
class TeamInfo:
    """Represents team information.

    Attributes:
        team_id: Unique team identifier.
        name: Team name.
        sport: Sport type.
        league: League/competition.
        season: Current season.
        current_phase: Current season phase.
        win_loss_record: Win-loss record as [wins, losses].
        ranking: Current league ranking/position.
        home_venue: Home venue name.
        roster_size: Number of players on roster.
        coaching_staff: Number of coaching staff.
        team_budget: Annual team budget.
    """
    team_id: str
    name: str = ""
    sport: SportType = SportType.SOCCER
    league: str = ""
    season: str = ""
    current_phase: GamePhase = GamePhase.REGULAR_SEASON
    win_loss_record: List[int] = field(default_factory=lambda: [0, 0])
    ranking: int = 0
    home_venue: str = ""
    roster_size: int = 0
    coaching_staff: int = 0
    team_budget: float = 0.0


@dataclass
class Player:
    """Represents a player/athlete.

    Attributes:
        player_id: Unique player identifier.
        name: Player name.
        number: Jersey number.
        position: Primary position.
        age: Player age.
        height_cm: Height in centimeters.
        weight_kg: Weight in kilograms.
        overall_rating: Overall skill rating (0-100).
        condition: Physical condition/fitness (0-100).
        fatigue: Fatigue level (0-100, higher = more fatigued).
        injury_status: Injury status.
        stats: Key performance statistics.
        strengths: Player strengths.
        weaknesses: Player weaknesses.
    """
    player_id: str
    name: str = ""
    number: int = 0
    position: PlayerPosition = PlayerPosition.UTILITY
    age: int = 25
    height_cm: int = 180
    weight_kg: float = 75.0
    overall_rating: float = 0.0
    condition: float = 0.0
    fatigue: float = 0.0
    injury_status: str = "healthy"
    stats: Dict[str, float] = field(default_factory=dict)
    strengths: List[str] = field(default_factory=list)
    weaknesses: List[str] = field(default_factory=list)


@dataclass
class TacticalAnalysis:
    """Represents a tactical analysis report.

    Attributes:
        id: Unique analysis identifier.
        team_id: Associated team ID.
        opponent: Opponent team name.
        formation: Recommended formation/system.
        style_of_play: Style of play recommendation.
        key_strengths: Key tactical strengths.
        key_weaknesses: Key tactical weaknesses.
        opponent_strengths: Opponent's strengths to exploit.
        opponent_weaknesses: Opponent's weaknesses to target.
        set_piece_plans: Set piece strategies.
        pressing_strategy: Pressing strategy.
        defensive_organization: Defensive organization plan.
        attacking_patterns: Attacking patterns to use.
        confidence: Analysis confidence (0-1).
    """
    id: str
    team_id: str
    opponent: str = ""
    formation: str = ""
    style_of_play: str = ""
    key_strengths: List[str] = field(default_factory=list)
    key_weaknesses: List[str] = field(default_factory=list)
    opponent_strengths: List[str] = field(default_factory=list)
    opponent_weaknesses: List[str] = field(default_factory=list)
    set_piece_plans: List[str] = field(default_factory=list)
    pressing_strategy: str = ""
    defensive_organization: str = ""
    attacking_patterns: List[str] = field(default_factory=list)
    confidence: float = 0.0


@dataclass
class TrainingPlan:
    """Represents a training plan.

    Attributes:
        id: Unique plan identifier.
        team_id: Associated team ID.
        period: Planning period (e.g., "weekly", "daily").
        sessions: List of training sessions.
        total_training_hours: Total training hours.
        intensity_distribution: Distribution by intensity level.
        focus_areas: Key focus areas for training.
        recovery_schedule: Recovery and rest schedule.
        nutrition_guidance: Nutrition recommendations.
        injury_prevention: Injury prevention exercises.
        individual_plans: Individual player plans.
        expected_improvements: Expected areas of improvement.
    """
    id: str
    team_id: str
    period: str = "weekly"
    sessions: List[Dict[str, Any]] = field(default_factory=list)
    total_training_hours: float = 0.0
    intensity_distribution: Dict[str, float] = field(default_factory=dict)
    focus_areas: List[str] = field(default_factory=list)
    recovery_schedule: List[str] = field(default_factory=list)
    nutrition_guidance: List[str] = field(default_factory=list)
    injury_prevention: List[str] = field(default_factory=list)
    individual_plans: Dict[str, Any] = field(default_factory=dict)
    expected_improvements: List[str] = field(default_factory=list)


@dataclass
class ScoutingReport:
    """Represents a scouting report.

    Attributes:
        id: Unique report identifier.
        target_team: Team being scouted.
        scout: Scout name/ID.
        formation: Opponent's typical formation.
        key_players: Key players to watch.
        style_of_play: Opponent's playing style.
        strengths: Opponent's strengths.
        weaknesses: Opponent's weaknesses.
        recent_form: Recent form/results.
        top_scorers: Top goal/point scorers.
        set_piece_danger: Set piece threat level.
        injury_concerns: Injury concerns for opponent.
        tactical_tendencies: Tactical patterns and tendencies.
        game_plan_suggestions: Suggested game plan approach.
    """
    id: str
    target_team: str = ""
    scout: str = ""
    formation: str = ""
    key_players: List[str] = field(default_factory=list)
    style_of_play: str = ""
    strengths: List[str] = field(default_factory=list)
    weaknesses: List[str] = field(default_factory=list)
    recent_form: List[str] = field(default_factory=list)
    top_scorers: List[str] = field(default_factory=list)
    set_piece_danger: str = "medium"
    injury_concerns: List[str] = field(default_factory=list)
    tactical_tendencies: List[str] = field(default_factory=list)
    game_plan_suggestions: List[str] = field(default_factory=list)


@dataclass
class SportsMetrics(SolutionMetrics):
    """Extended metrics for the sports coaching solution.

    Attributes:
        win_rate: Team win rate percentage.
        player_development: Player development score (0-100).
        team_performance: Overall team performance rating (0-100).
        games_analyzed: Total games analyzed.
        training_plans_created: Number of training plans created.
        tactical_accuracy: Tactical analysis accuracy.
        fitness_level: Average team fitness level.
        injury_rate: Injury rate per 1000 training hours.
        scouting_accuracy: Scouting report accuracy.
        player_improvement_rate: Rate of player improvement.
    """
    win_rate: float = 0.0
    player_development: float = 0.0
    team_performance: float = 0.0
    games_analyzed: int = 0
    training_plans_created: int = 0
    tactical_accuracy: float = 0.0
    fitness_level: float = 0.0
    injury_rate: float = 0.0
    scouting_accuracy: float = 0.0
    player_improvement_rate: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        """Convert sports metrics to a dictionary."""
        base = super().to_dict()
        base.update({
            "win_rate": self.win_rate,
            "player_development": self.player_development,
            "team_performance": self.team_performance,
            "games_analyzed": self.games_analyzed,
            "training_plans_created": self.training_plans_created,
            "tactical_accuracy": self.tactical_accuracy,
            "fitness_level": self.fitness_level,
            "injury_rate": self.injury_rate,
            "scouting_accuracy": self.scouting_accuracy,
            "player_improvement_rate": self.player_improvement_rate,
        })
        return base


class SportsCoachSolution(BaseSolution):
    """Sports Coaching AI solution template.

    Provides a complete, pre-configured sports coaching solution
    with multi-agent collaboration for opponent analysis, game
    planning, training design, performance tracking, and in-game
    adjustments.

    Pre-configured agent roles:
        - Head Coach: Develops overall strategy and manages team
        - Tactical Analyst: Analyzes opponents and tactical approaches
        - Fitness Trainer: Designs training programs and monitors fitness
        - Scout: Provides opponent scouting reports and player evaluations

    Workflow:
        1. Opponent Analysis - Scout and analyze upcoming opponent
        2. Game Plan - Develop tactical approach and game strategy
        3. Training Design - Create training plan for the week
        4. Performance Tracking - Monitor player and team performance
        5. In-Game Adjustments - Provide real-time tactical adjustments

    Configuration options:
        - sport: Sport type
        - team_level: Competitive level of the team
        - season_phase: Current phase of the season
        - training_focus: Primary training focus area
        - development_priority: Priority on winning vs development
    """

    def _build_metadata(self) -> SolutionMetadata:
        """Build metadata for the sports coach solution."""
        return SolutionMetadata(
            name="sports-coach",
            version="1.0.0",
            industry="sports",
            description=(
                "AI-powered sports coaching assistant with tactical analysis, "
                "training design, opponent scouting, and performance tracking."
            ),
            author="LiuHao AI OS",
            tags=["sports", "coaching", "tactics", "training", "scouting", "performance"],
            min_liuhao_version="1.0.0",
        )

    def _build_config_schema(self) -> None:
        """Define configuration fields for the sports coaching solution."""
        self.config_schema.add_field(
            name="sport",
            field_type="string",
            required=False,
            default="soccer",
            description="Sport type for coaching",
            valid_values=[
                "soccer", "basketball", "football", "baseball", "tennis",
                "swimming", "track_and_field", "hockey", "volleyball",
                "rugby", "golf",
            ],
        )
        self.config_schema.add_field(
            name="team_level",
            field_type="string",
            required=False,
            default="professional",
            description="Competitive level of the team",
            valid_values=["youth", "amateur", "semi_pro", "professional", "elite", "college"],
        )
        self.config_schema.add_field(
            name="season_phase",
            field_type="string",
            required=False,
            default="regular_season",
            description="Current phase of the season",
            valid_values=[
                "offseason", "preseason", "regular_season",
                "playoffs", "post_game",
            ],
        )
        self.config_schema.add_field(
            name="training_focus",
            field_type="string",
            required=False,
            default="balanced",
            description="Primary training focus area",
            valid_values=["technical", "tactical", "physical", "mental", "balanced"],
        )
        self.config_schema.add_field(
            name="development_priority",
            field_type="float",
            required=False,
            default=0.5,
            description="Balance between winning and player development (0-1)",
        )
        self.config_schema.add_field(
            name="roster_size",
            field_type="int",
            required=False,
            default=25,
            description="Number of players on the roster",
        )
        self.config_schema.add_field(
            name="training_sessions_per_week",
            field_type="int",
            required=False,
            default=5,
            description="Number of training sessions per week",
        )

    def _on_initialize(self) -> None:
        """Initialize sports coaching solution state."""
        self._agents: Dict[str, Dict[str, Any]] = {}
        self._teams: Dict[str, TeamInfo] = {}
        self._players: Dict[str, Player] = {}
        self._tactical_analyses: Dict[str, TacticalAnalysis] = {}
        self._training_plans: Dict[str, TrainingPlan] = {}
        self._scouting_reports: Dict[str, ScoutingReport] = {}
        self._team_count = 0
        self._player_count = 0
        self._tactical_count = 0
        self._training_count = 0
        self._scouting_count = 0

        # Initialize sports-specific metrics
        self.sports_metrics = SportsMetrics()

        # Sport-specific formations and styles
        self._sport_tactics = self._build_sport_tactics()

    def _build_sport_tactics(self) -> Dict[str, Any]:
        """Build sport-specific tactical options."""
        return {
            SportType.SOCCER: {
                "formations": ["4-3-3", "4-4-2", "3-5-2", "4-2-3-1", "5-3-2"],
                "styles": ["possession", "counter_attack", "high_press", "direct", "tiki_taka"],
                "positions": ["goalkeeper", "defender", "midfielder", "forward"],
            },
            SportType.BASKETBALL: {
                "formations": ["starting_5", "10_man_rotation", "deep_bench"],
                "styles": ["pick_and_roll", "isolation", "transition", "three_point", "post_up"],
                "positions": ["point_guard", "shooting_guard", "small_forward", "power_forward", "center"],
            },
            SportType.FOOTBALL: {
                "formations": ["4-3_defense", "3-4_defense", "spread_offense", "pro_style", "west_coast"],
                "styles": ["balanaced", "pass_heavy", "run_heavy", "blitz_heavy", "bend_dont_break"],
                "positions": ["quarterback", "running_back", "wide_receiver", "offensive_lineman", "defensive_lineman", "linebacker", "defensive_back"],
            },
            SportType.BASEBALL: {
                "formations": ["9_man_field", "dh_rule", "national_league"],
                "styles": ["power_hitting", "small_ball", "starting_pitching", "bullpen_heavy", "analytics_driven"],
                "positions": ["pitcher", "catcher", "first_base", "second_base", "third_base", "shortstop", "outfielder"],
            },
            SportType.TENNIS: {
                "formations": ["singles", "doubles"],
                "styles": ["aggressive_baseline", "serve_and_volley", "counter_puncher", "all_court", "big_server"],
                "positions": ["singles_player", "doubles_team"],
            },
        }

    def _on_deploy(self) -> None:
        """Deploy sports coaching agents and workflows."""
        agent_configs = [
            (CoachRole.HEAD_COACH, {
                "name": "Head Coach",
                "description": "Develops overall strategy and manages team",
                "system_prompt": self._build_head_coach_prompt(),
                "tools": ["game_planning", "team_management", "strategy_design"],
            }),
            (CoachRole.TACTICAL_ANALYST, {
                "name": "Tactical Analyst",
                "description": "Analyzes opponents and tactical approaches",
                "system_prompt": self._build_tactical_analyst_prompt(),
                "tools": ["video_analysis", "opponent_scouting", "tactical_modeling"],
            }),
            (CoachRole.FITNESS_TRAINER, {
                "name": "Fitness Trainer",
                "description": "Designs training programs and monitors fitness",
                "system_prompt": self._build_fitness_trainer_prompt(),
                "tools": ["training_design", "fitness_testing", "recovery_planning"],
            }),
            (CoachRole.SCOUT, {
                "name": "Scout",
                "description": "Provides opponent scouting and player evaluations",
                "system_prompt": self._build_scout_prompt(),
                "tools": ["player_evaluation", "opponent_reconnaissance", "talent_identification"],
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
        """Process a sports coaching request through the full workflow.

        Args:
            input_data: Dictionary containing team and opponent information.

        Returns:
            Dictionary with coaching results including tactical analysis,
            game plan, training plan, and scouting report.
        """
        # Step 1: Team setup
        team = self._setup_team(input_data)

        # Step 2: Opponent analysis / scouting
        scouting = self._scout_opponent(team, input_data)

        # Step 3: Game plan / tactical analysis
        tactics = self._develop_game_plan(team, scouting)

        # Step 4: Training design
        training = self._design_training_plan(team, tactics)

        # Step 5: Performance tracking plan
        performance_plan = self._design_performance_tracking(team)

        # Store and update metrics
        self._teams[team.team_id] = team
        self._scouting_reports[scouting.id] = scouting
        self._tactical_analyses[tactics.id] = tactics
        self._training_plans[training.id] = training

        self.sports_metrics.games_analyzed += 1
        self.sports_metrics.training_plans_created += 1
        self._update_sports_metrics()

        return {
            "team": {
                "id": team.team_id,
                "name": team.name,
                "sport": team.sport.value,
                "league": team.league,
                "phase": team.current_phase.value,
                "win_loss": team.win_loss_record,
                "ranking": team.ranking,
                "roster_size": team.roster_size,
            },
            "scouting": {
                "id": scouting.id,
                "opponent": scouting.target_team,
                "formation": scouting.formation,
                "key_players": scouting.key_players,
                "style_of_play": scouting.style_of_play,
                "strengths": scouting.strengths,
                "weaknesses": scouting.weaknesses,
                "recent_form": scouting.recent_form,
                "set_piece_danger": scouting.set_piece_danger,
                "game_plan_suggestions": scouting.game_plan_suggestions,
            },
            "game_plan": {
                "id": tactics.id,
                "formation": tactics.formation,
                "style_of_play": tactics.style_of_play,
                "key_strengths": tactics.key_strengths,
                "key_weaknesses": tactics.key_weaknesses,
                "pressing_strategy": tactics.pressing_strategy,
                "defensive_organization": tactics.defensive_organization,
                "attacking_patterns": tactics.attacking_patterns,
                "set_piece_plans": tactics.set_piece_plans,
                "confidence": tactics.confidence,
            },
            "training_plan": {
                "id": training.id,
                "period": training.period,
                "total_training_hours": training.total_training_hours,
                "intensity_distribution": training.intensity_distribution,
                "focus_areas": training.focus_areas,
                "sessions_count": len(training.sessions),
                "recovery_schedule": training.recovery_schedule,
                "injury_prevention": training.injury_prevention,
                "expected_improvements": training.expected_improvements,
            },
            "performance_tracking": performance_plan,
        }

    def _on_teardown(self) -> None:
        """Clean up sports coaching solution resources."""
        self._agents.clear()
        self._teams.clear()
        self._players.clear()
        self._tactical_analyses.clear()
        self._training_plans.clear()
        self._scouting_reports.clear()
        self._team_count = 0
        self._player_count = 0
        self._tactical_count = 0
        self._training_count = 0
        self._scouting_count = 0

    # ------------------------------------------------------------------
    # Workflow steps
    # ------------------------------------------------------------------

    def _setup_team(self, data: Dict[str, Any]) -> TeamInfo:
        """Step 1: Set up team information."""
        self._team_count += 1
        team_id = f"team-{self._team_count}"

        try:
            sport = SportType(data.get("sport", self.config.get("sport", "soccer")))
        except ValueError:
            sport = SportType.SOCCER

        try:
            phase = GamePhase(data.get("season_phase", self.config.get("season_phase", "regular_season")))
        except ValueError:
            phase = GamePhase.REGULAR_SEASON

        roster_size = self.config.get("roster_size", 25)

        # Generate sample roster
        for i in range(roster_size):
            self._player_count += 1
            player_id = f"player-{self._player_count}"
            positions = [
                PlayerPosition.FORWARD, PlayerPosition.MIDFIELD,
                PlayerPosition.DEFENSE, PlayerPosition.GOALKEEPER,
            ]
            position = positions[i % len(positions)]

            player = Player(
                player_id=player_id,
                name=f"Player {i+1}",
                number=i + 1,
                position=position,
                age=22 + (i % 12),
                height_cm=175 + (i % 25),
                weight_kg=70 + (i % 20),
                overall_rating=round(60 + (i % 35), 1),
                condition=round(75 + (i % 20), 1),
                fatigue=round(10 + (i % 30), 1),
                stats={
                    "games_played": 10 + i,
                    "goals": i % 10,
                    "assists": i % 8,
                    "minutes_played": 500 + i * 50,
                },
                strengths=[f"Strength {i % 3 + 1}", f"Strength {i % 4 + 1}"],
                weaknesses=[f"Area to improve {i % 2 + 1}"],
            )
            self._players[player_id] = player

        team = TeamInfo(
            team_id=team_id,
            name=data.get("team_name", f"Team {self._team_count}"),
            sport=sport,
            league=data.get("league", ""),
            season=data.get("season", "2025-2026"),
            current_phase=phase,
            win_loss_record=data.get("win_loss_record", [10, 5]),
            ranking=data.get("ranking", 3),
            home_venue=data.get("home_venue", ""),
            roster_size=roster_size,
            coaching_staff=8,
            team_budget=data.get("team_budget", 50000000.0),
        )
        return team

    def _scout_opponent(self, team: TeamInfo, data: Dict[str, Any]) -> ScoutingReport:
        """Step 2: Scout the opponent."""
        self._scouting_count += 1
        scout_id = f"scout-{self._scouting_count}"

        opponent = data.get("opponent", "Opponent Team")
        sport = team.sport
        tactics = self._sport_tactics.get(sport, self._sport_tactics[SportType.SOCCER])

        # Opponent formation and style
        formation = tactics["formations"][self._scouting_count % len(tactics["formations"])]
        style = tactics["styles"][self._scouting_count % len(tactics["styles"])]

        # Key players
        key_players = [
            "Striker #9 - Clinical finisher, 15 goals this season",
            "Playmaker #10 - Creative hub, 12 assists",
            "Defensive Leader #4 - Organizes defense, aerial threat",
        ]

        # Recent form
        recent_form = ["W", "W", "D", "W", "L"]

        # Strengths and weaknesses
        strengths = [
            "Strong attacking transition",
            "Set piece threat",
            "High pressing intensity",
        ]
        weaknesses = [
            "Vulnerable on counter-attacks",
            "Defensive frailty on flanks",
            "Inconsistent away form",
        ]

        # Game plan suggestions
        suggestions = [
            "Exploit wide areas against their fullbacks",
            "Press high to disrupt their build-up play",
            "Target their left-sided defensive weakness",
            "Use set pieces as key scoring opportunities",
            "Maintain compact shape when defending transitions",
        ]

        report = ScoutingReport(
            id=scout_id,
            target_team=opponent,
            scout="AI Scout System",
            formation=formation,
            key_players=key_players,
            style_of_play=style.replace("_", " ").title(),
            strengths=strengths,
            weaknesses=weaknesses,
            recent_form=recent_form,
            top_scorers=["#9 - 15 goals", "#10 - 8 goals", "#7 - 6 goals"],
            set_piece_danger="high",
            injury_concerns=[
                "Starting defender doubtful",
                "Key midfielder carrying knock",
            ],
            tactical_tendencies=[
                "Prefers to build from the back",
                "High line of engagement",
                "Rotates fullbacks forward",
            ],
            game_plan_suggestions=suggestions,
        )
        return report

    def _develop_game_plan(
        self, team: TeamInfo, scouting: ScoutingReport
    ) -> TacticalAnalysis:
        """Step 3: Develop game plan and tactical approach."""
        self._tactical_count += 1
        tactics_id = f"tactics-{self._tactical_count}"

        sport = team.sport
        sport_tactics = self._sport_tactics.get(sport, self._sport_tactics[SportType.SOCCER])

        # Select formation based on opponent's weaknesses
        formation = sport_tactics["formations"][0]
        if "counter" in scouting.style_of_play.lower():
            formation = sport_tactics["formations"][2]  # More defensive

        # Style of play
        dev_priority = self.config.get("development_priority", 0.5)
        if team.current_phase == GamePhase.PLAYOFFS:
            style = "pragmatic counter-attacking approach"
        elif dev_priority > 0.6:
            style = "development-focused possession play"
        else:
            style = "balanced attacking approach"

        # Pressing strategy
        pressing = "High pressing in final third, drop into mid-block when deep"

        # Defensive organization
        defensive_org = "Compact 2 banks of 4, force play wide, central compactness"

        # Attacking patterns
        attacking = [
            "Build from back through midfield",
            "Exploit wide areas with overlapping fullbacks",
            "Quick combinations in final third",
            "Counter-press after losing possession",
        ]

        # Set piece plans
        set_pieces = [
            "Near-post runs on corner kicks",
            "Short corner variation for possession retention",
            "Free kick routines targeting central runs",
        ]

        # Team strengths/weaknesses
        key_strengths = [
            "Strong midfield ball retention",
            "Clinical finishing in box",
            "Set piece effectiveness",
        ]
        key_weaknesses = [
            "Defending crosses",
            "Transition defense",
            "Squad depth on bench",
        ]

        # Confidence
        confidence = 0.75
        if team.current_phase == GamePhase.PLAYOFFS:
            confidence = 0.82
        elif team.current_phase == GamePhase.PRESEASON:
            confidence = 0.6

        analysis = TacticalAnalysis(
            id=tactics_id,
            team_id=team.team_id,
            opponent=scouting.target_team,
            formation=formation,
            style_of_play=style,
            key_strengths=key_strengths,
            key_weaknesses=key_weaknesses,
            opponent_strengths=scouting.strengths,
            opponent_weaknesses=scouting.weaknesses,
            set_piece_plans=set_pieces,
            pressing_strategy=pressing,
            defensive_organization=defensive_org,
            attacking_patterns=attacking,
            confidence=confidence,
        )
        self._tactical_analyses[tactics_id] = analysis
        return analysis

    def _design_training_plan(
        self, team: TeamInfo, tactics: TacticalAnalysis
    ) -> TrainingPlan:
        """Step 4: Design training plan."""
        self._training_count += 1
        plan_id = f"train-{self._training_count}"

        sessions_per_week = self.config.get("training_sessions_per_week", 5)
        focus = self.config.get("training_focus", "balanced")

        # Intensity distribution
        intensity_dist = {
            "light": 1.0,
            "moderate": 2.0,
            "high": 1.5,
            "recovery": 0.5,
        }
        if focus == "physical":
            intensity_dist["high"] = 2.0
            intensity_dist["moderate"] = 2.0
        elif focus == "technical":
            intensity_dist["moderate"] = 2.5
            intensity_dist["light"] = 1.5
        elif focus == "tactical":
            intensity_dist["moderate"] = 2.5
            intensity_dist["high"] = 1.0

        # Training sessions
        sessions = []
        session_types = [
            {"name": "Tactical Session", "intensity": "moderate", "duration": 90, "focus": "team shape and patterns"},
            {"name": "Technical Drills", "intensity": "moderate", "duration": 75, "focus": "individual technique"},
            {"name": "Strength & Conditioning", "intensity": "high", "duration": 60, "focus": "power and endurance"},
            {"name": "Tactical + Scrimmage", "intensity": "high", "duration": 90, "focus": "game simulation"},
            {"name": "Recovery + Video", "intensity": "recovery", "duration": 45, "focus": "regeneration and analysis"},
        ]

        for i in range(min(sessions_per_week, len(session_types))):
            st = session_types[i]
            sessions.append({
                "day": f"Day {i+1}",
                "name": st["name"],
                "intensity": st["intensity"],
                "duration_min": st["duration"],
                "focus": st["focus"],
                "exercises": [
                    "Warm-up and activation (15 min)",
                    "Main session (45-60 min)",
                    "Cool-down and stretching (15 min)",
                ],
            })

        total_hours = sum(s["duration_min"] for s in sessions) / 60

        # Focus areas
        focus_areas = []
        if focus in ("tactical", "balanced"):
            focus_areas.append("Defensive organization")
            focus_areas.append("Attacking patterns")
        if focus in ("technical", "balanced"):
            focus_areas.append("Passing accuracy")
            focus_areas.append("Finishing technique")
        if focus in ("physical", "balanced"):
            focus_areas.append("Aerobic endurance")
            focus_areas.append("Strength and power")

        # Recovery schedule
        recovery = [
            "Active recovery day (light swimming or cycling)",
            "Foam rolling and mobility work",
            "Sleep optimization - 8-9 hours minimum",
            "Nutrition timing and hydration",
            "Cold water immersion after high-intensity days",
        ]

        # Injury prevention
        injury_prevention = [
            "Dynamic warm-up before every session",
            "Proprioception and balance training",
            "Eccentric strength training",
            "Load monitoring and management",
            "Regular mobility work for key joints",
        ]

        # Expected improvements
        expected = [
            "Improved tactical understanding of game model",
            "Enhanced physical conditioning",
            "Better team cohesion and communication",
            "Reduced injury risk through proper preparation",
        ]

        plan = TrainingPlan(
            id=plan_id,
            team_id=team.team_id,
            period="weekly",
            sessions=sessions,
            total_training_hours=round(total_hours, 1),
            intensity_distribution={k: v for k, v in intensity_dist.items()},
            focus_areas=focus_areas,
            recovery_schedule=recovery,
            nutrition_guidance=[
                "Pre-training: Carbs + protein 1-2 hours before",
                "During: Hydration + electrolytes for sessions >60 min",
                "Post-training: Protein + carbs within 30 min window",
                "Daily: Balanced macros, adequate calories for training load",
            ],
            injury_prevention=injury_prevention,
            expected_improvements=expected,
        )
        self._training_plans[plan_id] = plan
        return plan

    def _design_performance_tracking(self, team: TeamInfo) -> Dict[str, Any]:
        """Step 5: Design performance tracking framework."""
        return {
            "metrics_tracked": [
                "Physical: GPS tracking, heart rate, load monitoring",
                "Technical: Pass accuracy, shooting %, dribble success",
                "Tactical: Positional data, pressing intensity, shape discipline",
                "Mental: Wellbeing surveys, recovery scores, readiness ratings",
            ],
            "tracking_frequency": {
                "daily": ["wellness", "readiness", "training_load"],
                "weekly": ["technical_stats", "tactical_review", "fitness_tests"],
                "monthly": ["performance_summary", "development_review"],
            },
            "key_performance_indicators": [
                "Training availability rate (target: >90%)",
                "Match performance rating",
                "Physical output per match",
                "Technical efficiency metrics",
                "Team win rate",
            ],
            "review_cycle": "Weekly individual reviews + monthly full team review",
            "data_sources": [
                "GPS wearables",
                "Video analysis software",
                "Player self-reporting",
                "Coach assessments",
                "Medical staff input",
            ],
        }

    # ------------------------------------------------------------------
    # Agent prompts
    # ------------------------------------------------------------------

    def _build_head_coach_prompt(self) -> str:
        """Build the system prompt for the head coach role."""
        sport = self.config.get("sport", "soccer").replace("_", " ")
        level = self.config.get("team_level", "professional")
        return (
            f"You are a head coach at the {level} level in {sport}. "
            "Develop comprehensive game plans, manage the roster, make "
            "tactical decisions, and lead the team to success. Balance "
            "short-term results with long-term player development. Create "
            "a positive team culture, set clear expectations, and hold "
            "players accountable. Your decisions should be data-informed "
            "but ultimately guided by your experience and judgment."
        )

    def _build_tactical_analyst_prompt(self) -> str:
        """Build the system prompt for the tactical analyst role."""
        return (
            "You are a tactical analyst who studies opposition teams and "
            "identifies tactical patterns. Analyze formations, pressing "
            "schemes, attacking patterns, defensive organization, and "
            "set piece routines. Use video analysis, statistical data, "
            "and scouting reports to build comprehensive opponent profiles. "
            "Provide actionable insights that the coaching staff can use "
            "to prepare game plans."
        )

    def _build_fitness_trainer_prompt(self) -> str:
        """Build the system prompt for the fitness trainer role."""
        focus = self.config.get("training_focus", "balanced")
        return (
            f"You are a strength and conditioning coach with focus on "
            f"{focus} training. Design periodized training programs that "
            "develop physical qualities while managing fatigue and injury "
            "risk. Individualize training for player positions and needs. "
            "Monitor training load, track fitness metrics, and ensure "
            "players peak at the right times. Prioritize injury prevention "
            "and recovery alongside performance gains."
        )

    def _build_scout_prompt(self) -> str:
        """Build the system prompt for the scout role."""
        return (
            "You are an experienced talent scout and opposition analyst. "
            "Evaluate players and teams objectively, identifying strengths, "
            "weaknesses, tendencies, and potential. Watch games with an "
            "analytical eye, track tactical patterns, and provide detailed "
            "reports that coaches can act on. Consider context such as "
            "system fit, developmental trajectory, and intangible qualities."
        )

    # ------------------------------------------------------------------
    # Metrics
    # ------------------------------------------------------------------

    def _update_sports_metrics(self) -> None:
        """Update sports-specific metrics."""
        if self._teams:
            for team in self._teams.values():
                wins, losses = team.win_loss_record
                total = wins + losses
                if total > 0:
                    self.sports_metrics.win_rate = round(wins / total * 100, 1)
                    self.sports_metrics.team_performance = (
                        50 + (wins - losses) / total * 50
                    )

        if self._tactical_analyses:
            total = len(self._tactical_analyses)
            avg_conf = sum(
                t.confidence for t in self._tactical_analyses.values()
            ) / total
            self.sports_metrics.tactical_accuracy = avg_conf * 100

        if self._training_plans:
            # Fitness level estimate based on training focus
            focus = self.config.get("training_focus", "balanced")
            fitness_scores = {
                "physical": 85,
                "balanced": 75,
                "tactical": 70,
                "technical": 68,
                "mental": 65,
            }
            self.sports_metrics.fitness_level = float(fitness_scores.get(focus, 75))

        if self._players:
            total = len(self._players)
            avg_overall = sum(p.overall_rating for p in self._players.values()) / total
            avg_condition = sum(p.condition for p in self._players.values()) / total
            self.sports_metrics.player_development = avg_overall
            self.sports_metrics.fitness_level = avg_condition

            # Injury rate estimate
            injured = sum(
                1 for p in self._players.values()
                if p.injury_status != "healthy"
            )
            self.sports_metrics.injury_rate = injured / total * 100 if total > 0 else 0

        # Scouting accuracy
        if self._scouting_reports:
            # Simplified: base on number of weaknesses identified
            self.sports_metrics.scouting_accuracy = 78.5

        # Player improvement rate
        dev_priority = self.config.get("development_priority", 0.5)
        self.sports_metrics.player_improvement_rate = 60 + dev_priority * 30

    def get_sports_metrics(self) -> Dict[str, Any]:
        """Get the current sports coaching metrics.

        Returns:
            Dictionary with sports metrics including win rate, player
            development, team performance, and tactical accuracy.
        """
        return self.sports_metrics.to_dict()
