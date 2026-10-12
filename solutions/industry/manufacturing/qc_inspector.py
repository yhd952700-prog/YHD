"""
Manufacturing QC Inspector AI Solution Template.

A comprehensive AI-powered quality control solution for manufacturing.
Includes multi-agent roles for quality inspection, defect analysis,
root cause analysis, process improvement, and compliance auditing.

Typical workflow:
    inspection -> defect detection -> root cause analysis
    -> process improvement -> compliance check
"""

from typing import Optional, Dict, Any, List
from enum import Enum
from dataclasses import dataclass, field

from solutions.templates.base_solution import (
    BaseSolution,
    SolutionMetadata,
    SolutionMetrics,
)


class DefectSeverity(str, Enum):
    """Severity levels for manufacturing defects."""
    MINOR = "minor"
    MODERATE = "moderate"
    MAJOR = "major"
    CRITICAL = "critical"


class DefectCategory(str, Enum):
    """Categories of manufacturing defects."""
    DIMENSIONAL = "dimensional"
    SURFACE_FINISH = "surface_finish"
    MATERIAL = "material"
    ASSEMBLY = "assembly"
    FUNCTIONAL = "functional"
    COSMETIC = "cosmetic"
    PACKAGING = "packaging"


class InspectionStatus(str, Enum):
    """Status of an inspection batch."""
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    HELD = "held"
    REJECTED = "rejected"
    ACCEPTED = "accepted"


class ManufacturingRole(str, Enum):
    """Agent roles in the QC inspector solution."""
    QUALITY_INSPECTOR = "quality_inspector"
    DEFECT_ANALYZER = "defect_analyzer"
    PROCESS_ENGINEER = "process_engineer"
    COMPLIANCE_AUDITOR = "compliance_auditor"


@dataclass
class Defect:
    """Represents a detected defect.

    Attributes:
        defect_id: Unique defect identifier.
        category: Defect category.
        severity: Severity level.
        description: Detailed defect description.
        location: Location on the product/component.
        size_mm: Estimated defect size in millimeters.
        confidence: Detection confidence (0-1).
        image_ref: Reference to inspection image.
        production_step: Step where defect was introduced.
        root_cause: Identified root cause.
    """
    defect_id: str
    category: DefectCategory = DefectCategory.SURFACE_FINISH
    severity: DefectSeverity = DefectSeverity.MINOR
    description: str = ""
    location: str = ""
    size_mm: float = 0.0
    confidence: float = 0.0
    image_ref: str = ""
    production_step: str = ""
    root_cause: str = ""


@dataclass
class InspectionBatch:
    """Represents an inspection batch.

    Attributes:
        batch_id: Unique batch identifier.
        product: Product name/code.
        quantity: Total units in batch.
        inspected: Number of units inspected.
        defects_found: Total defects found.
        defect_rate: Defect rate (defects per unit).
        defects: List of detected defects.
        status: Inspection status.
        inspector: Inspector name/ID.
        production_line: Production line identifier.
        shift: Production shift.
        started_at: ISO 8601 start timestamp.
        completed_at: ISO 8601 completion timestamp.
    """
    batch_id: str
    product: str = ""
    quantity: int = 0
    inspected: int = 0
    defects_found: int = 0
    defect_rate: float = 0.0
    defects: List[Defect] = field(default_factory=list)
    status: InspectionStatus = InspectionStatus.PENDING
    inspector: str = ""
    production_line: str = ""
    shift: str = ""
    started_at: Optional[str] = None
    completed_at: Optional[str] = None


@dataclass
class RootCauseAnalysis:
    """Represents results of root cause analysis.

    Attributes:
        analysis_id: Unique analysis identifier.
        defect_id: Associated defect ID.
        primary_cause: Primary root cause identified.
        contributing_factors: Contributing factors.
        fishbone_diagram: Fishbone/Ishikawa analysis data.
        verification_method: Method used to verify root cause.
        confidence: Confidence in root cause identification (0-1).
        recommended_fixes: Recommended corrective actions.
        estimated_impact: Estimated impact of fixing.
    """
    analysis_id: str
    defect_id: str
    primary_cause: str = ""
    contributing_factors: List[str] = field(default_factory=list)
    fishbone_diagram: Dict[str, Any] = field(default_factory=dict)
    verification_method: str = ""
    confidence: float = 0.0
    recommended_fixes: List[str] = field(default_factory=list)
    estimated_impact: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ProcessImprovement:
    """Represents a process improvement recommendation.

    Attributes:
        improvement_id: Unique improvement ID.
        title: Improvement title.
        description: Detailed description.
        target_process: Target process/step.
        current_defect_rate: Current defect rate.
        projected_defect_rate: Projected defect rate after improvement.
        estimated_cost: Estimated implementation cost.
        estimated_savings: Estimated annual savings.
        roi_months: Estimated ROI in months.
        priority: Implementation priority.
        status: Implementation status.
    """
    improvement_id: str
    title: str
    description: str = ""
    target_process: str = ""
    current_defect_rate: float = 0.0
    projected_defect_rate: float = 0.0
    estimated_cost: float = 0.0
    estimated_savings: float = 0.0
    roi_months: float = 0.0
    priority: str = "medium"
    status: str = "proposed"


@dataclass
class ManufacturingMetrics(SolutionMetrics):
    """Extended metrics for the QC inspector solution.

    Attributes:
        defect_rate: Overall defect rate.
        inspection_accuracy: Inspection accuracy rate.
        process_improvement_rate: Rate of process improvement.
        batches_inspected: Total batches inspected.
        total_defects_found: Total defects found.
        first_pass_yield: First pass yield rate.
        avg_inspection_time_sec: Average inspection time per unit.
        compliance_pass_rate: Compliance audit pass rate.
    """
    defect_rate: float = 0.0
    inspection_accuracy: float = 0.0
    process_improvement_rate: float = 0.0
    batches_inspected: int = 0
    total_defects_found: int = 0
    first_pass_yield: float = 0.0
    avg_inspection_time_sec: float = 0.0
    compliance_pass_rate: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        """Convert manufacturing metrics to a dictionary."""
        base = super().to_dict()
        base.update({
            "defect_rate": self.defect_rate,
            "inspection_accuracy": self.inspection_accuracy,
            "process_improvement_rate": self.process_improvement_rate,
            "batches_inspected": self.batches_inspected,
            "total_defects_found": self.total_defects_found,
            "first_pass_yield": self.first_pass_yield,
            "avg_inspection_time_sec": self.avg_inspection_time_sec,
            "compliance_pass_rate": self.compliance_pass_rate,
        })
        return base


class QCInspectorSolution(BaseSolution):
    """Manufacturing Quality Control AI solution template.

    Provides a complete, pre-configured quality control solution
    with multi-agent collaboration for inspection, defect analysis,
    root cause analysis, process improvement, and compliance auditing.

    Pre-configured agent roles:
        - Quality Inspector: Performs automated visual and dimensional inspection
        - Defect Analyzer: Classifies and analyzes detected defects
        - Process Engineer: Identifies root causes and proposes improvements
        - Compliance Auditor: Verifies compliance with quality standards

    Workflow:
        1. Inspection - Automated quality inspection of products
        2. Defect Detection - Identify and classify defects
        3. Root Cause Analysis - Determine underlying causes of defects
        4. Process Improvement - Recommend process optimizations
        5. Compliance Check - Verify adherence to quality standards

    Configuration options:
        - product_type: Type of product being manufactured
        - inspection_method: Primary inspection method
        - quality_standard: Quality standard to comply with
        - defect_threshold: Acceptable defect rate threshold
        - inspection_speed: Speed vs accuracy trade-off
    """

    def _build_metadata(self) -> SolutionMetadata:
        """Build metadata for the QC inspector solution."""
        return SolutionMetadata(
            name="qc-inspector",
            version="1.0.0",
            industry="manufacturing",
            description=(
                "AI-powered manufacturing quality control solution with "
                "defect detection, root cause analysis, process improvement, "
                "and compliance auditing."
            ),
            author="LiuHao AI OS",
            tags=["manufacturing", "quality-control", "defect-detection", "qc", "six-sigma"],
            min_liuhao_version="1.0.0",
        )

    def _build_config_schema(self) -> None:
        """Define configuration fields for the QC inspector solution."""
        self.config_schema.add_field(
            name="product_type",
            field_type="string",
            required=True,
            description="Type of product being manufactured",
        )
        self.config_schema.add_field(
            name="inspection_method",
            field_type="string",
            required=False,
            default="visual",
            description="Primary inspection method",
            valid_values=["visual", "dimensional", "functional", "destructive", "multimodal"],
        )
        self.config_schema.add_field(
            name="quality_standard",
            field_type="string",
            required=False,
            default="iso_9001",
            description="Quality standard to comply with",
            valid_values=["iso_9001", "iso_13485", "iatf_16949", "as9100", "gmp", "six_sigma"],
        )
        self.config_schema.add_field(
            name="defect_threshold",
            field_type="float",
            required=False,
            default=0.02,
            description="Acceptable defect rate threshold (0-1)",
        )
        self.config_schema.add_field(
            name="inspection_speed",
            field_type="string",
            required=False,
            default="balanced",
            description="Speed vs accuracy trade-off",
            valid_values=["fast", "balanced", "accurate", "ultra_accurate"],
        )
        self.config_schema.add_field(
            name="sample_size",
            field_type="string",
            required=False,
            default="standard",
            description="Inspection sample size strategy",
            valid_values=["100_percent", "standard", "reduced", "tightened"],
        )
        self.config_schema.add_field(
            name="auto_hold_on_critical",
            field_type="bool",
            required=False,
            default=True,
            description="Whether to auto-hold batches with critical defects",
        )

    def _on_initialize(self) -> None:
        """Initialize QC inspector solution state."""
        self._agents: Dict[str, Dict[str, Any]] = {}
        self._batches: Dict[str, InspectionBatch] = {}
        self._rca_records: Dict[str, RootCauseAnalysis] = {}
        self._improvements: Dict[str, ProcessImprovement] = {}
        self._batch_count = 0
        self._defect_count = 0
        self._improvement_count = 0

        # Initialize manufacturing-specific metrics
        self.manufacturing_metrics = ManufacturingMetrics()

    def _on_deploy(self) -> None:
        """Deploy QC inspector agents and workflows."""
        agent_configs = [
            (ManufacturingRole.QUALITY_INSPECTOR, {
                "name": "Quality Inspector",
                "description": "Performs automated quality inspection",
                "system_prompt": self._build_quality_inspector_prompt(),
                "tools": ["vision_inspection", "dimensional_check", "surface_scan"],
            }),
            (ManufacturingRole.DEFECT_ANALYZER, {
                "name": "Defect Analyzer",
                "description": "Classifies and analyzes detected defects",
                "system_prompt": self._build_defect_analyzer_prompt(),
                "tools": ["defect_classifier", "pattern_recognition", "trend_analysis"],
            }),
            (ManufacturingRole.PROCESS_ENGINEER, {
                "name": "Process Engineer",
                "description": "Identifies root causes and proposes improvements",
                "system_prompt": self._build_process_engineer_prompt(),
                "tools": ["root_cause_analysis", "process_modeling", "simulation"],
            }),
            (ManufacturingRole.COMPLIANCE_AUDITOR, {
                "name": "Compliance Auditor",
                "description": "Verifies compliance with quality standards",
                "system_prompt": self._build_compliance_auditor_prompt(),
                "tools": ["audit_checklist", "standard_library", "documentation_review"],
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
        """Process a quality control inspection batch through the workflow.

        Args:
            input_data: Dictionary containing batch data with at least
                ``product`` and ``quantity``.

        Returns:
            Dictionary with QC inspection results including defects,
            root cause analysis, improvement recommendations, and compliance.
        """
        # Step 1: Inspection
        batch = self._perform_inspection(input_data)

        # Step 2: Defect detection and analysis
        defects = self._detect_and_analyze_defects(batch)
        batch.defects = defects
        batch.defects_found = len(defects)
        batch.defect_rate = len(defects) / max(1, batch.inspected)

        # Step 3: Root cause analysis (for significant defects)
        rca_results = []
        if batch.defect_rate > self.config.get("defect_threshold", 0.02):
            rca_results = self._perform_root_cause_analysis(batch)

        # Step 4: Process improvement recommendations
        improvements = self._generate_process_improvements(batch, rca_results)

        # Step 5: Compliance check
        compliance = self._check_compliance(batch)

        # Determine batch disposition
        if self.config.get("auto_hold_on_critical", True):
            has_critical = any(d.severity == DefectSeverity.CRITICAL for d in defects)
            if has_critical:
                batch.status = InspectionStatus.HELD
            elif batch.defect_rate > self.config.get("defect_threshold", 0.02) * 2:
                batch.status = InspectionStatus.REJECTED
            else:
                batch.status = InspectionStatus.ACCEPTED

        # Update metrics
        self.manufacturing_metrics.batches_inspected += 1
        self.manufacturing_metrics.total_defects_found += len(defects)
        self._update_manufacturing_metrics(batch)

        return {
            "batch_id": batch.batch_id,
            "batch": {
                "product": batch.product,
                "quantity": batch.quantity,
                "inspected": batch.inspected,
                "defects_found": batch.defects_found,
                "defect_rate": round(batch.defect_rate * 100, 3),
                "status": batch.status.value,
                "production_line": batch.production_line,
            },
            "defects": [
                {
                    "id": d.defect_id,
                    "category": d.category.value,
                    "severity": d.severity.value,
                    "description": d.description,
                    "location": d.location,
                    "confidence": d.confidence,
                }
                for d in defects[:10]
            ],
            "root_cause_analysis": [
                {
                    "defect_id": rca.defect_id,
                    "primary_cause": rca.primary_cause,
                    "confidence": rca.confidence,
                    "recommended_fixes": rca.recommended_fixes,
                }
                for rca in rca_results
            ],
            "process_improvements": [
                {
                    "id": imp.improvement_id,
                    "title": imp.title,
                    "target_process": imp.target_process,
                    "current_rate": imp.current_defect_rate,
                    "projected_rate": imp.projected_defect_rate,
                    "roi_months": imp.roi_months,
                    "priority": imp.priority,
                }
                for imp in improvements
            ],
            "compliance": compliance,
        }

    def _on_teardown(self) -> None:
        """Clean up QC inspector solution resources."""
        self._agents.clear()
        self._batches.clear()
        self._rca_records.clear()
        self._improvements.clear()
        self._batch_count = 0
        self._defect_count = 0
        self._improvement_count = 0

    # ------------------------------------------------------------------
    # Workflow steps
    # ------------------------------------------------------------------

    def _perform_inspection(self, data: Dict[str, Any]) -> InspectionBatch:
        """Step 1: Perform quality inspection on the batch."""
        self._batch_count += 1
        batch_id = data.get("batch_id", f"batch-{self._batch_count}")
        quantity = data.get("quantity", 100)

        # Determine sample size
        sample_strategy = self.config.get("sample_size", "standard")
        sample_ratios = {
            "100_percent": 1.0,
            "tightened": 0.5,
            "standard": 0.2,
            "reduced": 0.05,
        }
        sample_ratio = sample_ratios.get(sample_strategy, 0.2)
        inspected = max(1, int(quantity * sample_ratio))

        batch = InspectionBatch(
            batch_id=batch_id,
            product=data.get("product", self.config.get("product_type", "unknown")),
            quantity=quantity,
            inspected=inspected,
            status=InspectionStatus.IN_PROGRESS,
            production_line=data.get("production_line", "Line 1"),
            shift=data.get("shift", "Day"),
        )
        self._batches[batch_id] = batch
        return batch

    def _detect_and_analyze_defects(self, batch: InspectionBatch) -> List[Defect]:
        """Step 2: Detect and classify defects in the batch."""
        # Simulate defect detection based on product type and speed
        speed = self.config.get("inspection_speed", "balanced")
        base_defect_rate = 0.015  # 1.5% baseline

        # Speed affects detection sensitivity
        sensitivity = {
            "fast": 0.7,
            "balanced": 0.85,
            "accurate": 0.95,
            "ultra_accurate": 0.99,
        }.get(speed, 0.85)

        actual_defects = int(batch.inspected * base_defect_rate)
        detected_defects = max(0, int(actual_defects * sensitivity + (hash(batch.batch_id) % 3)))

        defects = []
        defect_types = list(DefectCategory)
        severity_levels = list(DefectSeverity)

        for i in range(detected_defects):
            self._defect_count += 1
            category = defect_types[i % len(defect_types)]
            severity_idx = min(len(severity_levels) - 1, i % 4)
            severity = severity_levels[severity_idx]

            defect = Defect(
                defect_id=f"defect-{self._defect_count}",
                category=category,
                severity=severity,
                description=f"{severity.value} {category.value} defect detected on unit {i+1}",
                location=f"Area {(i % 6) + 1}",
                size_mm=round(0.5 + (i * 0.3), 2),
                confidence=round(0.7 + (sensitivity * 0.25) + (hash(f"d{i}") % 10) / 100, 3),
                production_step=["stamping", "welding", "assembly", "painting"][i % 4],
            )
            defects.append(defect)

        return defects

    def _perform_root_cause_analysis(
        self, batch: InspectionBatch
    ) -> List[RootCauseAnalysis]:
        """Step 3: Perform root cause analysis on significant defects."""
        rca_results = []

        for defect in batch.defects[:5]:
            analysis_id = f"rca-{defect.defect_id}"

            # Determine causes by defect category
            causes_by_category = {
                DefectCategory.DIMENSIONAL: {
                    "primary": "Tool wear in machining process",
                    "factors": ["Inadequate tool change schedule", "Material hardness variation", "Temperature fluctuation"],
                },
                DefectCategory.SURFACE_FINISH: {
                    "primary": "Contaminated polishing media",
                    "factors": ["Insufficient media replacement", "Environmental dust", "Operator technique variation"],
                },
                DefectCategory.MATERIAL: {
                    "primary": "Incoming material quality variation",
                    "factors": ["Supplier quality inconsistency", "Improper storage conditions", "Material fatigue"],
                },
                DefectCategory.ASSEMBLY: {
                    "primary": "Assembly fixture misalignment",
                    "factors": ["Fixture wear", "Operator training gap", "Part tolerance stack-up"],
                },
                DefectCategory.FUNCTIONAL: {
                    "primary": "Process parameter drift",
                    "factors": ["Equipment calibration issue", "Environmental condition change", "Raw material batch variation"],
                },
                DefectCategory.COSMETIC: {
                    "primary": "Handling damage during transfer",
                    "factors": ["Inadequate packaging", "Rough handling", "Conveyor system issues"],
                },
            }

            cause_info = causes_by_category.get(
                defect.category,
                {"primary": "Unknown root cause - requires further investigation", "factors": []}
            )

            rca = RootCauseAnalysis(
                analysis_id=analysis_id,
                defect_id=defect.defect_id,
                primary_cause=cause_info["primary"],
                contributing_factors=cause_info["factors"],
                fishbone_diagram={
                    "man": ["Training", "Fatigue"],
                    "machine": ["Wear", "Calibration"],
                    "material": ["Quality", "Variation"],
                    "method": ["Procedure", "Standards"],
                    "measurement": ["Accuracy", "Precision"],
                    "environment": ["Temperature", "Cleanliness"],
                },
                verification_method="Experimental verification recommended",
                confidence=0.75,
                recommended_fixes=[
                    f"Address {cause_info['primary'].lower()}",
                    "Implement preventive maintenance schedule",
                    "Add in-process check for this defect type",
                ],
                estimated_impact={
                    "defect_reduction_percent": 60.0,
                    "cost_savings_annual": 15000.0,
                },
            )
            rca_results.append(rca)
            self._rca_records[analysis_id] = rca

        return rca_results

    def _generate_process_improvements(
        self, batch: InspectionBatch, rca_results: List[RootCauseAnalysis]
    ) -> List[ProcessImprovement]:
        """Step 4: Generate process improvement recommendations."""
        improvements = []

        if not rca_results:
            return improvements

        unique_processes = set()
        for rca in rca_results:
            process = rca.primary_cause.split(" ")[0].lower()
            unique_processes.add(process)

        for i, process in enumerate(unique_processes):
            self._improvement_count += 1
            imp_id = f"improvement-{self._improvement_count}"

            current_rate = batch.defect_rate
            projected_rate = current_rate * 0.4  # 60% reduction

            cost = 5000.0 + (i * 2000)
            savings = current_rate * batch.quantity * 50 * 250  # Annual savings estimate
            roi = cost / max(1, savings / 12) if savings > 0 else 12.0

            improvement = ProcessImprovement(
                improvement_id=imp_id,
                title=f"Improve {process} process to reduce defects",
                description=(
                    f"Process improvement targeting {process} operations to reduce "
                    f"defect rates from {current_rate*100:.2f}% to {projected_rate*100:.2f}%"
                ),
                target_process=process,
                current_defect_rate=current_rate,
                projected_defect_rate=projected_rate,
                estimated_cost=cost,
                estimated_savings=savings,
                roi_months=round(roi, 1),
                priority="high" if current_rate > 0.05 else "medium",
            )
            improvements.append(improvement)
            self._improvements[imp_id] = improvement

        return improvements

    def _check_compliance(self, batch: InspectionBatch) -> Dict[str, Any]:
        """Step 5: Verify compliance with quality standards."""
        standard = self.config.get("quality_standard", "iso_9001")
        threshold = self.config.get("defect_threshold", 0.02)

        issues = []
        if batch.defect_rate > threshold:
            issues.append(f"Defect rate ({batch.defect_rate*100:.2f}%) exceeds threshold ({threshold*100}%)")

        has_critical = any(d.severity == DefectSeverity.CRITICAL for d in batch.defects)
        if has_critical:
            issues.append("Critical defects detected - requires immediate corrective action")

        compliant = len(issues) == 0

        return {
            "standard": standard,
            "compliant": compliant,
            "issues": issues,
            "actions_required": [
                "Document all findings in quality management system",
                "Initiate corrective action for non-conformances",
                "Review and update inspection criteria as needed",
            ] if not compliant else ["Continue monitoring"],
        }

    # ------------------------------------------------------------------
    # Agent prompts
    # ------------------------------------------------------------------

    def _build_quality_inspector_prompt(self) -> str:
        """Build the system prompt for the quality inspector role."""
        method = self.config.get("inspection_method", "visual")
        return (
            f"You are a quality control inspector specializing in {method} inspection. "
            f"Perform thorough, accurate inspections following established standards. "
            f"Identify all defects regardless of size and document them precisely. "
            f"Measure and record defect characteristics systematically. "
            f"Maintain inspection consistency and follow defined procedures exactly."
        )

    def _build_defect_analyzer_prompt(self) -> str:
        """Build the system prompt for the defect analyzer role."""
        return (
            "You are a defect analysis specialist for manufacturing. "
            "Classify defects accurately by type and severity. "
            "Identify patterns and trends in defect data. "
            "Correlate defects with production parameters and processes. "
            "Provide clear defect categorization and detailed descriptions."
        )

    def _build_process_engineer_prompt(self) -> str:
        """Build the system prompt for the process engineer role."""
        return (
            "You are a manufacturing process engineer focused on quality improvement. "
            "Use root cause analysis methodologies (5 Whys, Fishbone, FMEA) to identify "
            "the true causes of defects. Design process improvements that are practical, "
            "cost-effective, and measurable. Calculate ROI and prioritize improvements "
            "based on impact and feasibility."
        )

    def _build_compliance_auditor_prompt(self) -> str:
        """Build the system prompt for the compliance auditor role."""
        standard = self.config.get("quality_standard", "iso_9001")
        return (
            f"You are a quality compliance auditor with expertise in {standard}. "
            f"Verify adherence to quality standards, procedures, and documentation "
            f"requirements. Identify non-conformances and opportunities for improvement. "
            f"Ensure all quality records are complete and accurate. Make objective, "
            f"evidence-based audit conclusions."
        )

    # ------------------------------------------------------------------
    # Metrics
    # ------------------------------------------------------------------

    def _update_manufacturing_metrics(self, latest_batch: InspectionBatch) -> None:
        """Update manufacturing-specific metrics."""
        if self._batches:
            total_defects = sum(b.defects_found for b in self._batches.values())
            total_inspected = sum(b.inspected for b in self._batches.values())
            self.manufacturing_metrics.defect_rate = (
                total_defects / max(1, total_inspected)
            )
            self.manufacturing_metrics.first_pass_yield = (
                1 - self.manufacturing_metrics.defect_rate
            )

        speed_accuracy = {
            "fast": 0.92,
            "balanced": 0.96,
            "accurate": 0.985,
            "ultra_accurate": 0.995,
        }
        self.manufacturing_metrics.inspection_accuracy = speed_accuracy.get(
            self.config.get("inspection_speed", "balanced"), 0.96
        )

        self.manufacturing_metrics.compliance_pass_rate = 0.92
        self.manufacturing_metrics.process_improvement_rate = (
            len(self._improvements) / max(1, self.manufacturing_metrics.batches_inspected)
        )

    def get_manufacturing_metrics(self) -> Dict[str, Any]:
        """Get the current manufacturing-specific metrics.

        Returns:
            Dictionary with manufacturing metrics including defect rate,
            inspection accuracy, first pass yield, and compliance rate.
        """
        return self.manufacturing_metrics.to_dict()
