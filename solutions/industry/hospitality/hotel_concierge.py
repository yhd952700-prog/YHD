"""
Hotel Concierge AI Solution Template.

A comprehensive AI-powered hotel concierge solution for luxury
hotels, resorts, and boutique properties. Includes multi-agent
roles for concierge services, booking management, event planning,
and guest relations with personalized experience optimization.

Typical workflow:
    guest profile -> preferences analysis -> personalized recommendations
    -> booking -> stay experience -> feedback
"""

from typing import Optional, Dict, Any, List
from enum import Enum
from dataclasses import dataclass, field

from solutions.templates.base_solution import (
    BaseSolution,
    SolutionMetadata,
    SolutionMetrics,
)


class GuestTier(str, Enum):
    """Guest loyalty tier levels."""
    STANDARD = "standard"
    SILVER = "silver"
    GOLD = "gold"
    PLATINUM = "platinum"
    DIAMOND = "diamond"


class RoomType(str, Enum):
    """Hotel room categories."""
    STANDARD = "standard"
    DELUXE = "deluxe"
    SUITE = "suite"
    EXECUTIVE = "executive"
    PRESIDENTIAL = "presidential"


class ServiceCategory(str, Enum):
    """Categories of hotel services."""
    DINING = "dining"
    SPA = "spa"
    TOUR = "tour"
    TRANSPORTATION = "transportation"
    EVENT = "event"
    FITNESS = "fitness"
    BUSINESS = "business"
    CONCIERGE = "concierge"


class ConciergeRole(str, Enum):
    """Agent roles in the hotel concierge solution."""
    CONCIERGE = "concierge"
    BOOKING_AGENT = "booking_agent"
    EVENT_PLANNER = "event_planner"
    GUEST_RELATIONS = "guest_relations"


@dataclass
class GuestProfile:
    """Represents a hotel guest profile.

    Attributes:
        guest_id: Unique guest identifier.
        name: Guest name.
        email: Guest email address.
        nationality: Guest nationality.
        preferred_language: Preferred communication language.
        tier: Loyalty program tier.
        total_stays: Total number of past stays.
        preferences: Guest preferences dictionary.
        dietary_restrictions: Dietary restrictions or preferences.
        special_needs: Special accessibility or medical needs.
        birthday: Guest birthday for special recognition.
        anniversary: Guest anniversary for special recognition.
    """
    guest_id: str
    name: str = ""
    email: str = ""
    nationality: str = ""
    preferred_language: str = "en"
    tier: GuestTier = GuestTier.STANDARD
    total_stays: int = 0
    preferences: Dict[str, Any] = field(default_factory=dict)
    dietary_restrictions: List[str] = field(default_factory=list)
    special_needs: List[str] = field(default_factory=list)
    birthday: str = ""
    anniversary: str = ""


@dataclass
class StayDetails:
    """Represents a hotel stay booking.

    Attributes:
        booking_id: Unique booking identifier.
        guest_id: Associated guest ID.
        check_in_date: Check-in date string.
        check_out_date: Check-out date string.
        room_type: Type of room booked.
        room_number: Assigned room number.
        number_of_guests: Number of guests.
        special_requests: Special requests for the stay.
        purpose: Purpose of stay (leisure, business, etc.).
        amenities: In-room amenities requested.
        total_cost: Total cost of the stay.
        status: Booking status.
    """
    booking_id: str
    guest_id: str
    check_in_date: str = ""
    check_out_date: str = ""
    room_type: RoomType = RoomType.STANDARD
    room_number: str = ""
    number_of_guests: int = 1
    special_requests: List[str] = field(default_factory=list)
    purpose: str = "leisure"
    amenities: List[str] = field(default_factory=list)
    total_cost: float = 0.0
    status: str = "confirmed"


@dataclass
class Recommendation:
    """Represents a personalized guest recommendation.

    Attributes:
        id: Unique recommendation identifier.
        guest_id: Associated guest ID.
        category: Service category.
        title: Recommendation title.
        description: Detailed description.
        price: Estimated price.
        duration: Estimated duration.
        relevance_score: How well this matches guest preferences (0-1).
        reason: Why this was recommended.
        booking_required: Whether advance booking is needed.
    """
    id: str
    guest_id: str
    category: ServiceCategory = ServiceCategory.CONCIERGE
    title: str = ""
    description: str = ""
    price: float = 0.0
    duration: str = ""
    relevance_score: float = 0.0
    reason: str = ""
    booking_required: bool = False


@dataclass
class GuestFeedback:
    """Represents guest feedback on their stay.

    Attributes:
        id: Unique feedback identifier.
        guest_id: Associated guest ID.
        booking_id: Associated booking ID.
        overall_rating: Overall satisfaction rating (1-5).
        category_ratings: Ratings by category.
        comments: Detailed feedback comments.
        highlights: Positive highlights mentioned.
        concerns: Areas for improvement.
        would_return: Whether guest would return.
        submitted_at: ISO 8601 submission timestamp.
    """
    id: str
    guest_id: str
    booking_id: str
    overall_rating: float = 0.0
    category_ratings: Dict[str, float] = field(default_factory=dict)
    comments: str = ""
    highlights: List[str] = field(default_factory=list)
    concerns: List[str] = field(default_factory=list)
    would_return: bool = True
    submitted_at: Optional[str] = None


@dataclass
class HospitalityMetrics(SolutionMetrics):
    """Extended metrics for the hotel concierge solution.

    Attributes:
        guest_satisfaction: Average guest satisfaction score (0-5).
        booking_conversion_rate: Rate of recommendation bookings.
        upsell_rate: Rate of upsell recommendations accepted.
        guests_served: Total guests served.
        avg_stay_rating: Average stay rating.
        recommendation_count: Total recommendations generated.
        personalized_recommendation_rate: Rate of personalized vs generic recs.
        response_time_min: Average concierge response time in minutes.
        repeat_guest_rate: Rate of returning guests.
    """
    guest_satisfaction: float = 0.0
    booking_conversion_rate: float = 0.0
    upsell_rate: float = 0.0
    guests_served: int = 0
    avg_stay_rating: float = 0.0
    recommendation_count: int = 0
    personalized_recommendation_rate: float = 0.0
    response_time_min: float = 0.0
    repeat_guest_rate: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        """Convert hospitality metrics to a dictionary."""
        base = super().to_dict()
        base.update({
            "guest_satisfaction": self.guest_satisfaction,
            "booking_conversion_rate": self.booking_conversion_rate,
            "upsell_rate": self.upsell_rate,
            "guests_served": self.guests_served,
            "avg_stay_rating": self.avg_stay_rating,
            "recommendation_count": self.recommendation_count,
            "personalized_recommendation_rate": self.personalized_recommendation_rate,
            "response_time_min": self.response_time_min,
            "repeat_guest_rate": self.repeat_guest_rate,
        })
        return base


class HotelConciergeSolution(BaseSolution):
    """Hotel Concierge AI solution template.

    Provides a complete, pre-configured hotel concierge solution
    with multi-agent collaboration for guest profiling, preference
    analysis, personalized recommendations, booking management,
    and guest relations.

    Pre-configured agent roles:
        - Concierge: Provides personalized guest services and recommendations
        - Booking Agent: Manages room and service reservations
        - Event Planner: Organizes events, dining, and special experiences
        - Guest Relations: Handles guest feedback and issue resolution

    Workflow:
        1. Guest Profile - Build comprehensive guest profile and preferences
        2. Preferences Analysis - Analyze preferences and past behavior
        3. Personalized Recommendations - Generate tailored recommendations
        4. Booking - Manage reservations and confirmations
        5. Stay Experience - Enhance in-stay experience
        6. Feedback - Collect and act on guest feedback

    Configuration options:
        - hotel_name: Name of the hotel property
        - hotel_class: Hotel star rating/class
        - service_hours: Concierge service hours
        - max_recommendations: Maximum recommendations per guest
        - personalization_level: Level of personalization detail
    """

    def _build_metadata(self) -> SolutionMetadata:
        """Build metadata for the hotel concierge solution."""
        return SolutionMetadata(
            name="hotel-concierge",
            version="1.0.0",
            industry="hospitality",
            description=(
                "AI-powered hotel concierge solution with personalized guest "
                "recommendations, booking management, event planning, and "
                "guest relations management."
            ),
            author="LiuHao AI OS",
            tags=["hospitality", "hotel", "concierge", "guest-experience", "personalization"],
            min_liuhao_version="1.0.0",
        )

    def _build_config_schema(self) -> None:
        """Define configuration fields for the hotel concierge solution."""
        self.config_schema.add_field(
            name="hotel_name",
            field_type="string",
            required=True,
            description="Name of the hotel property",
        )
        self.config_schema.add_field(
            name="hotel_class",
            field_type="string",
            required=False,
            default="5-star",
            description="Hotel star rating or class",
            valid_values=["3-star", "4-star", "5-star", "luxury", "boutique", "resort"],
        )
        self.config_schema.add_field(
            name="service_hours",
            field_type="string",
            required=False,
            default="24/7",
            description="Concierge service availability hours",
        )
        self.config_schema.add_field(
            name="max_recommendations",
            field_type="int",
            required=False,
            default=5,
            description="Maximum number of recommendations per guest request",
        )
        self.config_schema.add_field(
            name="personalization_level",
            field_type="string",
            required=False,
            default="high",
            description="Level of personalization for recommendations",
            valid_values=["basic", "medium", "high", "ultra"],
        )
        self.config_schema.add_field(
            name="location",
            field_type="string",
            required=False,
            default="",
            description="Hotel location (city, region)",
        )
        self.config_schema.add_field(
            name="upsell_enabled",
            field_type="bool",
            required=False,
            default=True,
            description="Whether to include upsell recommendations",
        )

    def _on_initialize(self) -> None:
        """Initialize hotel concierge solution state."""
        self._agents: Dict[str, Dict[str, Any]] = {}
        self._guests: Dict[str, GuestProfile] = {}
        self._bookings: Dict[str, StayDetails] = {}
        self._recommendations: Dict[str, Recommendation] = {}
        self._feedback: Dict[str, GuestFeedback] = {}
        self._guest_count = 0
        self._booking_count = 0
        self._recommendation_count = 0
        self._feedback_count = 0

        # Initialize hospitality-specific metrics
        self.hospitality_metrics = HospitalityMetrics()

        # Service catalog
        self._service_catalog = self._build_service_catalog()

    def _build_service_catalog(self) -> Dict[str, Any]:
        """Build the hotel service catalog."""
        return {
            "dining": [
                {"name": "Fine Dining Restaurant", "price": 150, "duration": "2-3 hours", "booking": True},
                {"name": "Room Service Breakfast", "price": 35, "duration": "30 min", "booking": False},
                {"name": "Rooftop Bar", "price": 50, "duration": "1-2 hours", "booking": True},
                {"name": "Private Chef Experience", "price": 500, "duration": "3 hours", "booking": True},
            ],
            "spa": [
                {"name": "Swedish Massage", "price": 120, "duration": "60 min", "booking": True},
                {"name": "Deep Tissue Massage", "price": 140, "duration": "60 min", "booking": True},
                {"name": "Facial Treatment", "price": 150, "duration": "75 min", "booking": True},
                {"name": "Spa Day Package", "price": 350, "duration": "4 hours", "booking": True},
            ],
            "tour": [
                {"name": "City Walking Tour", "price": 75, "duration": "3 hours", "booking": True},
                {"name": "Museum Visit", "price": 50, "duration": "2 hours", "booking": True},
                {"name": "Sunset Cruise", "price": 120, "duration": "2 hours", "booking": True},
                {"name": "Helicopter Tour", "price": 350, "duration": "45 min", "booking": True},
            ],
            "transportation": [
                {"name": "Airport Transfer", "price": 85, "duration": "45 min", "booking": True},
                {"name": "Private Car Service", "price": 150, "duration": "per day", "booking": True},
                {"name": "Luxury Car Rental", "price": 300, "duration": "per day", "booking": True},
            ],
            "event": [
                {"name": "Business Meeting Setup", "price": 200, "duration": "half day", "booking": True},
                {"name": "Wedding Coordination", "price": 5000, "duration": "full day", "booking": True},
                {"name": "Private Party", "price": 1500, "duration": "evening", "booking": True},
            ],
        }

    def _on_deploy(self) -> None:
        """Deploy hotel concierge agents and workflows."""
        agent_configs = [
            (ConciergeRole.CONCIERGE, {
                "name": "Concierge",
                "description": "Provides personalized guest services and local recommendations",
                "system_prompt": self._build_concierge_prompt(),
                "tools": ["local_knowledge", "guest_preferences", "service_booking"],
            }),
            (ConciergeRole.BOOKING_AGENT, {
                "name": "Booking Agent",
                "description": "Manages room reservations and service bookings",
                "system_prompt": self._build_booking_agent_prompt(),
                "tools": ["reservation_system", "availability_check", "confirmation"],
            }),
            (ConciergeRole.EVENT_PLANNER, {
                "name": "Event Planner",
                "description": "Organizes special events and custom experiences",
                "system_prompt": self._build_event_planner_prompt(),
                "tools": ["event_design", "vendor_coordination", "budget_tracking"],
            }),
            (ConciergeRole.GUEST_RELATIONS, {
                "name": "Guest Relations",
                "description": "Handles guest feedback and resolves issues",
                "system_prompt": self._build_guest_relations_prompt(),
                "tools": ["feedback_analysis", "issue_resolution", "loyalty_program"],
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
        """Process a guest request through the concierge workflow.

        Args:
            input_data: Dictionary containing guest data with at least
                ``guest_id`` and request type.

        Returns:
            Dictionary with concierge results including recommendations,
            booking details, or guest experience enhancements.
        """
        request_type = input_data.get("request_type", "recommendations")

        if request_type == "booking":
            return self._process_booking(input_data)
        elif request_type == "feedback":
            return self._process_feedback(input_data)
        else:
            return self._process_recommendations(input_data)

    def _process_recommendations(self, input_data: Dict[str, Any]) -> Dict[str, Any]:
        """Generate personalized recommendations for a guest."""
        # Step 1: Guest profile
        guest = self._build_guest_profile(input_data)

        # Step 2: Preferences analysis
        preferences = self._analyze_preferences(guest, input_data)

        # Step 3: Generate recommendations
        recommendations = self._generate_recommendations(guest, preferences)

        # Update metrics
        self.hospitality_metrics.recommendation_count += len(recommendations)
        self.hospitality_metrics.guests_served += 1
        self._update_hospitality_metrics()

        return {
            "guest_id": guest.guest_id,
            "guest": {
                "name": guest.name,
                "tier": guest.tier.value,
                "preferred_language": guest.preferred_language,
            },
            "preferences": preferences,
            "recommendations": [
                {
                    "id": r.id,
                    "category": r.category.value,
                    "title": r.title,
                    "description": r.description,
                    "price": r.price,
                    "duration": r.duration,
                    "relevance_score": r.relevance_score,
                    "reason": r.reason,
                    "booking_required": r.booking_required,
                }
                for r in recommendations
            ],
            "personalization": {
                "level": self.config.get("personalization_level", "high"),
                "based_on": preferences["factors"],
            },
        }

    def _process_booking(self, input_data: Dict[str, Any]) -> Dict[str, Any]:
        """Process a hotel booking request."""
        # Step 1: Guest profile
        guest = self._build_guest_profile(input_data)

        # Step 2: Create booking
        booking = self._create_booking(guest, input_data)

        # Step 3: Generate stay recommendations
        stay_recommendations = self._generate_stay_recommendations(guest, booking)

        return {
            "guest_id": guest.guest_id,
            "booking": {
                "id": booking.booking_id,
                "check_in": booking.check_in_date,
                "check_out": booking.check_out_date,
                "room_type": booking.room_type.value,
                "number_of_guests": booking.number_of_guests,
                "total_cost": booking.total_cost,
                "status": booking.status,
            },
            "welcome_amenities": self._generate_welcome_amenities(guest),
            "stay_recommendations": stay_recommendations,
        }

    def _process_feedback(self, input_data: Dict[str, Any]) -> Dict[str, Any]:
        """Process guest feedback."""
        guest_id = input_data.get("guest_id", "unknown")
        booking_id = input_data.get("booking_id", "unknown")

        feedback = self._collect_feedback(guest_id, booking_id, input_data)
        response = self._generate_feedback_response(feedback)

        return {
            "feedback_id": feedback.id,
            "guest_id": guest_id,
            "booking_id": booking_id,
            "rating": feedback.overall_rating,
            "response": response,
            "actions_taken": self._generate_improvement_actions(feedback),
        }

    def _on_teardown(self) -> None:
        """Clean up hotel concierge solution resources."""
        self._agents.clear()
        self._guests.clear()
        self._bookings.clear()
        self._recommendations.clear()
        self._feedback.clear()
        self._guest_count = 0
        self._booking_count = 0
        self._recommendation_count = 0
        self._feedback_count = 0

    # ------------------------------------------------------------------
    # Workflow steps
    # ------------------------------------------------------------------

    def _build_guest_profile(self, data: Dict[str, Any]) -> GuestProfile:
        """Step 1: Build or retrieve guest profile."""
        self._guest_count += 1
        guest_id = data.get("guest_id", f"guest-{self._guest_count}")

        # Parse tier
        tier_value = data.get("tier", "standard")
        try:
            tier = GuestTier(tier_value)
        except ValueError:
            tier = GuestTier.STANDARD

        guest = GuestProfile(
            guest_id=guest_id,
            name=data.get("name", ""),
            email=data.get("email", ""),
            nationality=data.get("nationality", ""),
            preferred_language=data.get("preferred_language", "en"),
            tier=tier,
            total_stays=data.get("total_stays", 0),
            preferences=data.get("preferences", {}),
            dietary_restrictions=data.get("dietary_restrictions", []),
            special_needs=data.get("special_needs", []),
            birthday=data.get("birthday", ""),
            anniversary=data.get("anniversary", ""),
        )
        self._guests[guest_id] = guest
        return guest

    def _analyze_preferences(
        self, guest: GuestProfile, data: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Step 2: Analyze guest preferences and interests."""
        explicit_prefs = guest.preferences
        stay_purpose = data.get("stay_purpose", "leisure")
        interests = data.get("interests", [])

        # Derive preference scores
        scores = {
            "dining": 0.5,
            "spa": 0.3,
            "tour": 0.5,
            "transportation": 0.3,
            "event": 0.2,
            "fitness": 0.3,
            "business": 0.2,
        }

        factors = []

        # Adjust based on tier
        tier_boost = {
            GuestTier.DIAMOND: 0.3,
            GuestTier.PLATINUM: 0.2,
            GuestTier.GOLD: 0.15,
            GuestTier.SILVER: 0.1,
            GuestTier.STANDARD: 0.0,
        }[guest.tier]
        factors.append(f"Loyalty tier: {guest.tier.value}")

        # Adjust based on interests
        interest_mapping = {
            "food": "dining",
            "cuisine": "dining",
            "relaxation": "spa",
            "wellness": "spa",
            "sightseeing": "tour",
            "culture": "tour",
            "business": "business",
            "meetings": "event",
            "fitness": "fitness",
            "gym": "fitness",
        }
        for interest in interests:
            interest_lower = interest.lower()
            for key, category in interest_mapping.items():
                if key in interest_lower:
                    scores[category] += 0.2
                    factors.append(f"Interest in {interest}")

        # Adjust based on stay purpose
        if stay_purpose == "business":
            scores["business"] += 0.3
            scores["event"] += 0.2
            factors.append("Business travel")
        elif stay_purpose == "romantic":
            scores["dining"] += 0.2
            scores["spa"] += 0.2
            factors.append("Romantic getaway")
        elif stay_purpose == "family":
            scores["tour"] += 0.2
            factors.append("Family vacation")

        # Dietary considerations
        if guest.dietary_restrictions:
            scores["dining"] += 0.1
            factors.append("Dietary preferences considered")

        # Normalize scores
        max_score = max(scores.values()) if scores else 1.0
        if max_score > 0:
            scores = {k: min(1.0, v + tier_boost) for k, v in scores.items()}

        return {
            "scores": scores,
            "factors": factors,
            "stay_purpose": stay_purpose,
        }

    def _generate_recommendations(
        self, guest: GuestProfile, preferences: Dict[str, Any]
    ) -> List[Recommendation]:
        """Step 3: Generate personalized recommendations."""
        max_recs = self.config.get("max_recommendations", 5)
        scores = preferences["scores"]
        recommendations = []
        rec_id_counter = 0

        # Sort categories by preference score
        sorted_categories = sorted(
            scores.items(), key=lambda x: x[1], reverse=True
        )

        for category, score in sorted_categories:
            if score < 0.3:
                continue
            services = self._service_catalog.get(category, [])
            for service in services[:2]:  # Top 2 per category
                rec_id_counter += 1
                rec = Recommendation(
                    id=f"rec-{rec_id_counter}",
                    guest_id=guest.guest_id,
                    category=ServiceCategory(category),
                    title=service["name"],
                    description=f"Experience our {service['name']} during your stay.",
                    price=service["price"],
                    duration=service["duration"],
                    relevance_score=round(score, 2),
                    reason=f"Based on your preference for {category} experiences",
                    booking_required=service["booking"],
                )
                recommendations.append(rec)
                self._recommendations[rec.id] = rec

                if len(recommendations) >= max_recs:
                    break
            if len(recommendations) >= max_recs:
                break

        # Add tier-specific perk
        if guest.tier in (GuestTier.GOLD, GuestTier.PLATINUM, GuestTier.DIAMOND):
            rec_id_counter += 1
            perk = Recommendation(
                id=f"rec-{rec_id_counter}",
                guest_id=guest.guest_id,
                category=ServiceCategory.CONCIERGE,
                title=f"{guest.tier.value.capitalize()} Member Welcome Amenity",
                description="Complimentary welcome amenity upon arrival, personalized to your preferences.",
                price=0.0,
                duration="on arrival",
                relevance_score=1.0,
                reason=f"Exclusive benefit for {guest.tier.value} members",
                booking_required=False,
            )
            recommendations.insert(0, perk)
            self._recommendations[perk.id] = perk

        return recommendations[:max_recs]

    def _create_booking(
        self, guest: GuestProfile, data: Dict[str, Any]
    ) -> StayDetails:
        """Step 2 (booking): Create a new booking."""
        self._booking_count += 1
        booking_id = f"booking-{self._booking_count}"

        try:
            room_type = RoomType(data.get("room_type", "standard"))
        except ValueError:
            room_type = RoomType.STANDARD

        # Calculate pricing
        base_rates = {
            RoomType.STANDARD: 200,
            RoomType.DELUXE: 350,
            RoomType.SUITE: 600,
            RoomType.EXECUTIVE: 800,
            RoomType.PRESIDENTIAL: 2000,
        }
        nightly_rate = base_rates.get(room_type, 200)

        # Tier discount
        tier_discount = {
            GuestTier.DIAMOND: 0.15,
            GuestTier.PLATINUM: 0.10,
            GuestTier.GOLD: 0.05,
            GuestTier.SILVER: 0.02,
            GuestTier.STANDARD: 0.0,
        }[guest.tier]

        nights = data.get("nights", 3)
        subtotal = nightly_rate * nights
        discount = subtotal * tier_discount
        total = subtotal - discount

        booking = StayDetails(
            booking_id=booking_id,
            guest_id=guest.guest_id,
            check_in_date=data.get("check_in_date", ""),
            check_out_date=data.get("check_out_date", ""),
            room_type=room_type,
            number_of_guests=data.get("number_of_guests", 1),
            special_requests=data.get("special_requests", []),
            purpose=data.get("stay_purpose", "leisure"),
            total_cost=round(total, 2),
            status="confirmed",
        )
        self._bookings[booking_id] = booking
        return booking

    def _generate_stay_recommendations(
        self, guest: GuestProfile, booking: StayDetails
    ) -> List[str]:
        """Generate in-stay experience recommendations."""
        recs = [
            f"Complimentary {self.config.get('hotel_class', '5-star')} welcome amenities upon arrival",
            "Daily housekeeping service with turndown service in the evening",
            "Access to fitness center and swimming pool 24/7",
        ]

        if guest.tier in (GuestTier.GOLD, GuestTier.PLATINUM, GuestTier.DIAMOND):
            recs.append("Executive lounge access with complimentary breakfast and evening cocktails")
            recs.append("Late checkout available upon request")

        if booking.purpose == "business":
            recs.append("Business center access with meeting room booking privileges")
            recs.append("High-speed WiFi and dedicated work desk in room")

        return recs

    def _generate_welcome_amenities(self, guest: GuestProfile) -> List[str]:
        """Generate personalized welcome amenities."""
        amenities = []

        if guest.tier in (GuestTier.PLATINUM, GuestTier.DIAMOND):
            amenities.append("Champagne and fresh fruit basket")
        elif guest.tier == GuestTier.GOLD:
            amenities.append("Wine and cheese platter")
        elif guest.tier == GuestTier.SILVER:
            amenities.append("Fresh fruit basket")
        else:
            amenities.append("Welcome chocolates and bottled water")

        # Dietary consideration
        if "vegan" in [d.lower() for d in guest.dietary_restrictions]:
            amenities = [a + " (vegan options available)" for a in amenities]
        if "gluten-free" in [d.lower() for d in guest.dietary_restrictions]:
            amenities.append("Gluten-free options available on request")

        return amenities

    def _collect_feedback(
        self, guest_id: str, booking_id: str, data: Dict[str, Any]
    ) -> GuestFeedback:
        """Step 1 (feedback): Collect and store guest feedback."""
        self._feedback_count += 1
        feedback_id = f"fb-{self._feedback_count}"

        feedback = GuestFeedback(
            id=feedback_id,
            guest_id=guest_id,
            booking_id=booking_id,
            overall_rating=data.get("overall_rating", 4.0),
            category_ratings=data.get("category_ratings", {}),
            comments=data.get("comments", ""),
            highlights=data.get("highlights", []),
            concerns=data.get("concerns", []),
            would_return=data.get("would_return", True),
        )
        self._feedback[feedback_id] = feedback
        return feedback

    def _generate_feedback_response(self, feedback: GuestFeedback) -> str:
        """Generate personalized response to guest feedback."""
        if feedback.overall_rating >= 4.5:
            return (
                "Thank you for your wonderful feedback! We are thrilled you "
                "enjoyed your stay. Your satisfaction is our highest priority, "
                "and we look forward to welcoming you back soon for another "
                "memorable experience."
            )
        elif feedback.overall_rating >= 3.5:
            return (
                "Thank you for your positive feedback. We are glad you enjoyed "
                "your stay and appreciate your suggestions for improvement. "
                "Our team is committed to continuously enhancing the guest "
                "experience."
            )
        else:
            return (
                "We sincerely apologize that your stay did not meet your "
                "expectations. Your feedback is invaluable to us, and our "
                "guest relations team would like to speak with you directly "
                "to address your concerns and make things right."
            )

    def _generate_improvement_actions(self, feedback: GuestFeedback) -> List[str]:
        """Generate improvement actions based on feedback concerns."""
        actions = []
        for concern in feedback.concerns:
            concern_lower = concern.lower()
            if "room" in concern_lower:
                actions.append(f"Room issue flagged for housekeeping review: {concern}")
            elif "service" in concern_lower or "staff" in concern_lower:
                actions.append(f"Service issue escalated to department head: {concern}")
            elif "food" in concern_lower or "dining" in concern_lower:
                actions.append(f"Dining concern forwarded to F&B manager: {concern}")
            else:
                actions.append(f"Concern logged for management review: {concern}")
        return actions

    # ------------------------------------------------------------------
    # Agent prompts
    # ------------------------------------------------------------------

    def _build_concierge_prompt(self) -> str:
        """Build the system prompt for the concierge role."""
        hotel_name = self.config.get("hotel_name", "our hotel")
        return (
            f"You are the chief concierge at {hotel_name}, a luxury hotel. "
            "Your role is to anticipate guest needs, provide personalized "
            "recommendations, and create unforgettable experiences. You have "
            "extensive knowledge of the local area, restaurants, attractions, "
            "and hidden gems. Always address guests by name, remember their "
            "preferences, and go above and beyond to exceed expectations. "
            "Be warm, professional, and resourceful."
        )

    def _build_booking_agent_prompt(self) -> str:
        """Build the system prompt for the booking agent role."""
        return (
            "You are a hotel booking agent responsible for managing room "
            "reservations and service bookings. Ensure all bookings are "
            "accurate, confirm availability promptly, and provide clear "
            "confirmation details. Upsell appropriate room upgrades and "
            "services when they genuinely enhance the guest experience. "
            "Be efficient, detail-oriented, and customer-focused."
        )

    def _build_event_planner_prompt(self) -> str:
        """Build the system prompt for the event planner role."""
        return (
            "You are an experienced hotel event planner specializing in "
            "creating memorable experiences for guests. From business "
            "meetings to weddings to private celebrations, you coordinate "
            "every detail with precision and creativity. Work closely with "
            "guests to understand their vision, manage budgets effectively, "
            "and deliver events that exceed expectations."
        )

    def _build_guest_relations_prompt(self) -> str:
        """Build the system prompt for the guest relations role."""
        return (
            "You are the guest relations manager at a luxury hotel. Your "
            "primary responsibility is ensuring every guest has an "
            "outstanding experience. Proactively check in with guests, "
            "address concerns promptly and empathetically, and turn "
            "potentially negative experiences into positive ones. "
            "You have the authority to make service recovery decisions "
            "and ensure guest loyalty."
        )

    # ------------------------------------------------------------------
    # Metrics
    # ------------------------------------------------------------------

    def _update_hospitality_metrics(self) -> None:
        """Update hospitality-specific metrics."""
        if self._feedback:
            total = len(self._feedback)
            avg_rating = sum(
                f.overall_rating for f in self._feedback.values()
            ) / total
            self.hospitality_metrics.avg_stay_rating = avg_rating
            self.hospitality_metrics.guest_satisfaction = avg_rating

            return_rate = sum(
                1 for f in self._feedback.values() if f.would_return
            ) / total
            self.hospitality_metrics.repeat_guest_rate = return_rate

        if self._recommendations:
            personalized = sum(
                1 for r in self._recommendations.values()
                if r.relevance_score >= 0.7
            )
            total = len(self._recommendations)
            self.hospitality_metrics.personalized_recommendation_rate = (
                personalized / total if total > 0 else 0.0
            )

    def get_hospitality_metrics(self) -> Dict[str, Any]:
        """Get the current hospitality-specific metrics.

        Returns:
            Dictionary with hospitality metrics including guest satisfaction,
            booking conversion, upsell rate, and recommendation stats.
        """
        return self.hospitality_metrics.to_dict()
