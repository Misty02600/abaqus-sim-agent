import pytest
from pydantic import ValidationError

from abaqus_sim_agent.domain.models import (
    MaterialSpec,
    RequirementResult,
    SimulationSpec,
)


def make_spec() -> SimulationSpec:
    return SimulationSpec(
        problem_name="cantilever",
        unit_system="mm-N-MPa",
        geometry_description="A rectangular cantilever beam",
        material=MaterialSpec(
            name="steel",
            elastic_modulus=210_000,
            poisson_ratio=0.3,
        ),
        boundary_conditions=["fixed at x=0"],
        loads=["downward point load at the free end"],
        requested_outputs=["U", "S"],
    )


def test_requirement_result_accepts_complete_spec() -> None:
    result = RequirementResult(spec=make_spec())

    assert result.missing_fields == []


def test_requirement_result_requires_missing_field_explanation() -> None:
    with pytest.raises(ValidationError, match="missing_fields"):
        RequirementResult()


def test_material_rejects_incompressible_poisson_ratio() -> None:
    with pytest.raises(ValidationError):
        MaterialSpec(name="invalid", elastic_modulus=1.0, poisson_ratio=0.5)
