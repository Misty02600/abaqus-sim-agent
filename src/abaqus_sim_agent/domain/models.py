from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator


class AnalysisType(StrEnum):
    STATIC_GENERAL = "static_general"


class WorkflowPhase(StrEnum):
    RECEIVED = "received"
    NEEDS_CLARIFICATION = "needs_clarification"
    READY_FOR_DESIGN = "ready_for_design"
    READY_FOR_BUILD = "ready_for_build"
    AWAITING_APPROVAL = "awaiting_approval"
    RUNNING = "running"
    NEEDS_REVIEW = "needs_review"
    READY_FOR_VERIFICATION = "ready_for_verification"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class JobPhase(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class MaterialSpec(StrictModel):
    name: str = Field(min_length=1)
    elastic_modulus: float = Field(gt=0)
    poisson_ratio: float = Field(gt=-1, lt=0.5)
    density: float | None = Field(default=None, gt=0)


class SimulationSpec(StrictModel):
    problem_name: str = Field(min_length=1)
    analysis_type: AnalysisType = AnalysisType.STATIC_GENERAL
    unit_system: str = Field(min_length=1)
    geometry_description: str = Field(min_length=1)
    material: MaterialSpec
    boundary_conditions: list[str] = Field(min_length=1)
    loads: list[str] = Field(min_length=1)
    requested_outputs: list[str] = Field(min_length=1)
    assumptions: list[str] = Field(default_factory=list)


class RequirementResult(StrictModel):
    spec: SimulationSpec | None = None
    missing_fields: list[str] = Field(default_factory=list)
    questions: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def check_completion_contract(self) -> "RequirementResult":
        if self.spec is None and not self.missing_fields:
            raise ValueError("missing_fields must explain why spec is unavailable")
        if self.spec is not None and self.missing_fields:
            raise ValueError("a complete spec cannot contain missing_fields")
        return self


class ModelingPlan(StrictModel):
    dimensionality: str = Field(pattern=r"^(2D|3D)$")
    element_type: str = Field(min_length=1)
    analysis_step: str = Field(min_length=1)
    mesh_strategy: str = Field(min_length=1)
    output_variables: list[str] = Field(min_length=1)
    rationale: list[str] = Field(min_length=1)


class ArtifactManifest(StrictModel):
    generation_id: str = Field(min_length=1)
    input_file: str = Field(min_length=1)
    script_file: str | None = None
    content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class JobHandle(StrictModel):
    job_id: str = Field(min_length=1)
    idempotency_key: str = Field(min_length=1)


class JobResult(StrictModel):
    job_id: str = Field(min_length=1)
    phase: JobPhase
    artifact_paths: dict[str, str] = Field(default_factory=dict)
    summary: str = ""


class DiagnosisReport(StrictModel):
    category: str = Field(min_length=1)
    repairable: bool
    evidence: list[str] = Field(min_length=1)
    patch_instructions: list[str] = Field(default_factory=list)


class VerificationReport(StrictModel):
    passed: bool
    checks: dict[str, bool] = Field(min_length=1)
    findings: list[str] = Field(default_factory=list)
