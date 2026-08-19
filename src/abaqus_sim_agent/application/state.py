import operator
from typing import Annotated, Any, TypedDict


class SimulationState(TypedDict, total=False):
    task_id: str
    user_request: str
    phase: str
    spec: dict[str, Any]
    modeling_plan: dict[str, Any]
    artifact_manifest: dict[str, Any]
    job: dict[str, Any]
    diagnosis: dict[str, Any]
    verification: dict[str, Any]
    clarification_history: Annotated[list[dict[str, str]], operator.add]
    events: Annotated[list[dict[str, str]], operator.add]
    repair_attempts: int
    terminal_reason: str
