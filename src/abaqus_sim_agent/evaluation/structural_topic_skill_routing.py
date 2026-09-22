from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
from datetime import UTC, datetime
from importlib.metadata import version
from pathlib import Path, PurePosixPath

from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic_ai import ModelMessagesTypeAdapter
from pydantic_ai.messages import ModelResponse, ToolCallPart

from abaqus_sim_agent.coding.agent import create_abaqus_coding_agent
from abaqus_sim_agent.coding.clarification import ScriptedClarificationProvider
from abaqus_sim_agent.coding.contracts import (
    CodingRunBudget,
    CodingRunRequest,
    RuntimeProfile,
)
from abaqus_sim_agent.coding.dependencies import CodingAgentDependencies
from abaqus_sim_agent.coding.execution_verifier import ExecutionEvidenceVerifier
from abaqus_sim_agent.coding.model import (
    DEEPSEEK_V4_FLASH,
    DEFAULT_MODEL_HTTP_RETRIES,
    DeepSeekGateway,
    create_deepseek_chat_model,
    take_gateway_api_key_from_environment,
)
from abaqus_sim_agent.coding.service import CodingRunService
from abaqus_sim_agent.coding.workspace import WorkspaceResolver
from abaqus_sim_agent.execution import DEFAULT_ABAQUS_2024_LAUNCHER, Abaqus2024Runner
from abaqus_sim_agent.skills import skill_library_path

REPO_ROOT = Path(__file__).parents[3]
DEFAULT_CASES = (
    REPO_ROOT / "benchmarks/coding/structural_topic_skill_routing_cases_v1.jsonl"
)
DEFAULT_SEAL = (
    REPO_ROOT / "benchmarks/coding/structural_topic_skill_routing_cases_v1.seal.json"
)
EVALUATOR_VERSION = "structural-mechanics-topic-skill-routing-v1"
STRUCTURAL_TOPIC_PROFILE = "structural_mechanics_v1"
CAPABILITY_IDS = (
    "abaqus-2024-coding",
    "hyperelastic-fea",
    "hyperelastic-calibration",
    "plasticity-fea",
    "geometric-nonlinear-fea",
    "structural-dynamics-fea",
    "modal-dynamics-fea",
    "explicit-dynamics-fea",
    "quasi-static-explicit-fea",
    "contact-fea",
    "contact-diagnostics-fea",
    "interface-cohesive-fea",
)
TASK_INSTRUCTIONS = """
Complete this offline engineering inspection task using the supplied workspace and
deferred capabilities only when they are useful for the unresolved decision. Do
not run Abaqus or any other program, and do not use Catalog or document retrieval.
Write analysis.json with summary (string), findings (array of strings), and
next_action (string). If a required user-specific physical input is missing,
record that fact without inventing a value and submit needs_input. Otherwise
inspect the requested artifact and submit ready_for_verification. This experiment
measures capability loading behavior, not simulation correctness.
""".strip()


def _required_int(item: dict[str, object], key: str) -> int:
    value = item[key]
    if not isinstance(value, (str, int, float)):
        raise TypeError(f"{key} must be numeric")
    return int(value)


class StructuralTopicSkillRoutingCase(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    benchmark_version: str = Field(pattern=r"^structural-topic-skill-routing-v1$")
    case_id: str = Field(pattern=r"^[a-z][a-z0-9-]+$")
    goal: str = Field(min_length=1)
    seed_files: dict[str, str]
    required_loads: tuple[str, ...]
    allowed_loads: tuple[str, ...]
    forbidden_loads: tuple[str, ...]
    expected_behavior: str = Field(min_length=1)
    review_criteria: tuple[str, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def valid_routing_sets(self):
        known = set(CAPABILITY_IDS)
        required = set(self.required_loads)
        allowed = set(self.allowed_loads)
        forbidden = set(self.forbidden_loads)
        if len(required) != len(self.required_loads):
            raise ValueError("required_loads contains duplicates")
        if len(allowed) != len(self.allowed_loads):
            raise ValueError("allowed_loads contains duplicates")
        if len(forbidden) != len(self.forbidden_loads):
            raise ValueError("forbidden_loads contains duplicates")
        if not required <= allowed:
            raise ValueError("required_loads must be a subset of allowed_loads")
        if not allowed.isdisjoint(forbidden) or allowed | forbidden != known:
            raise ValueError("allowed and forbidden loads must partition capabilities")
        for name in self.seed_files:
            path = PurePosixPath(name)
            if path.name != name or "\\" in name or ":" in name:
                raise ValueError("seed files must be plain workspace file names")
        return self


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_new(path: Path, value: object) -> None:
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write("\n")


def load_cases(
    path: Path = DEFAULT_CASES,
    seal_path: Path = DEFAULT_SEAL,
) -> tuple[StructuralTopicSkillRoutingCase, ...]:
    raw = path.read_bytes()
    seal = json.loads(seal_path.read_text(encoding="utf-8"))
    if hashlib.sha256(raw).hexdigest() != seal["sha256"]:
        raise ValueError(
            "structural topic routing case file no longer matches the frozen seal"
        )
    cases = tuple(
        StructuralTopicSkillRoutingCase.model_validate_json(line)
        for line in raw.decode("utf-8").splitlines()
        if line.strip()
    )
    if len(cases) != seal["case_count"]:
        raise ValueError(
            "structural topic routing case count no longer matches the frozen seal"
        )
    if len({case.case_id for case in cases}) != len(cases):
        raise ValueError("structural topic routing case identifiers must be unique")
    if tuple(seal["capability_ids"]) != CAPABILITY_IDS:
        raise ValueError("frozen capability set does not match evaluator")
    return cases


def _tool_calls(state: Path) -> list[dict[str, object]]:
    history = state / "history.json"
    if not history.is_file():
        return []
    messages = ModelMessagesTypeAdapter.validate_json(history.read_bytes())
    calls: list[dict[str, object]] = []
    for message in messages:
        if not isinstance(message, ModelResponse):
            continue
        calls.extend(
            {
                "tool": part.tool_name,
                "arguments": part.args_as_dict(),
                "tool_call_id": part.tool_call_id,
            }
            for part in message.parts
            if isinstance(part, ToolCallPart)
        )
    return calls


def _loaded_skill_body_bytes(loaded: set[str]) -> int:
    root = skill_library_path()
    return sum(
        (root / capability_id / "SKILL.md").stat().st_size
        for capability_id in loaded
        if capability_id in CAPABILITY_IDS
        and (root / capability_id / "SKILL.md").is_file()
    )


def observe_case(
    root: Path, case: StructuralTopicSkillRoutingCase
) -> dict[str, object]:
    state = root / case.case_id / "state"
    workspace = state.parent / "workspace"
    calls = _tool_calls(state)
    load_calls = [call for call in calls if call["tool"] == "load_capability"]
    load_ids = [
        str(call["arguments"].get("id"))
        for call in load_calls
        if isinstance(call["arguments"], dict) and call["arguments"].get("id")
    ]
    loaded = set(load_ids)
    required = set(case.required_loads)
    allowed = set(case.allowed_loads)
    forbidden = set(case.forbidden_loads)
    required_recall = (
        len(loaded & required) / len(required) if required else float(not loaded)
    )
    analysis_path = workspace / "analysis.json"
    analysis: object = None
    if analysis_path.is_file():
        try:
            analysis = json.loads(analysis_path.read_text(encoding="utf-8"))
        except (UnicodeError, ValueError):
            analysis = {"invalid_json": True}
    outcome_path = state / "outcome.json"
    outcome = (
        json.loads(outcome_path.read_text(encoding="utf-8"))
        if outcome_path.is_file()
        else None
    )
    return {
        "case_id": case.case_id,
        "load_sequence": load_ids,
        "unique_loaded": sorted(loaded),
        "required_loads": list(case.required_loads),
        "allowed_loads": list(case.allowed_loads),
        "forbidden_loads": list(case.forbidden_loads),
        "required_load_recall": required_recall,
        "forbidden_load_count": len(loaded & forbidden),
        "unexpected_loads": sorted(loaded - allowed),
        "load_call_count": len(load_calls),
        "duplicate_load_call_count": len(load_ids) - len(loaded),
        "loaded_skill_body_bytes": _loaded_skill_body_bytes(loaded),
        "exact_route_pass": required <= loaded and loaded <= allowed,
        "runtime_requested": any(
            call["tool"] in {"run_cae_script", "run_solver_job", "run_abaqus_python"}
            for call in calls
        ),
        "analysis": analysis,
        "outcome": outcome,
        "expected_behavior": case.expected_behavior,
        "review_criteria": list(case.review_criteria),
        "review_status": "pending_manual_review",
    }


def _skill_bindings() -> dict[str, str]:
    root = skill_library_path()
    return {
        capability_id: _sha256(root / capability_id / "SKILL.md")
        for capability_id in CAPABILITY_IDS
    }


async def run_evaluation(
    run_root: Path,
    *,
    cases_path: Path = DEFAULT_CASES,
    seal_path: Path = DEFAULT_SEAL,
    gateway: DeepSeekGateway = DeepSeekGateway.OPENCODE_GO,
    model_name: str = DEEPSEEK_V4_FLASH,
    http_retries: int = DEFAULT_MODEL_HTTP_RETRIES,
) -> dict[str, object]:
    cases = load_cases(cases_path, seal_path)
    root = await asyncio.to_thread(run_root.resolve)
    if root.exists():
        raise FileExistsError(root)
    api_key = take_gateway_api_key_from_environment(gateway)
    if not api_key.get_secret_value():
        raise ValueError("the selected model gateway has no configured API key")
    budget = CodingRunBudget(
        max_model_requests=10,
        max_api_lookup_calls=0,
        max_concept_lookup_calls=0,
        max_diagnostic_retrieval_calls=0,
        max_abaqus_calls=0,
        max_solver_calls=0,
        wall_time_seconds=360.0,
    )
    implementation_paths = [
        Path(__file__),
        REPO_ROOT / "src/abaqus_sim_agent/coding/agent.py",
    ]
    implementation_hashes = {
        path.relative_to(REPO_ROOT).as_posix(): _sha256(path)
        for path in implementation_paths
    }
    skill_hashes = _skill_bindings()
    root.mkdir(parents=True, exist_ok=False)
    _write_new(root / "cases.json", [case.model_dump(mode="json") for case in cases])
    _write_new(
        root / "manifest.json",
        {
            "evaluator_version": EVALUATOR_VERSION,
            "created_at": datetime.now(UTC).isoformat(),
            "case_source": cases_path.relative_to(REPO_ROOT).as_posix(),
            "case_source_sha256": _sha256(cases_path),
            "seal_sha256": _sha256(seal_path),
            "implementation_sha256": implementation_hashes,
            "skill_sha256": skill_hashes,
            "foundation_skill_profile": "off",
            "structural_topic_skill_profile": STRUCTURAL_TOPIC_PROFILE,
            "model": {
                "gateway": gateway.value,
                "name": model_name,
                "http_retries": http_retries,
            },
            "budget": budget.model_dump(mode="json"),
            "task_instructions": TASK_INSTRUCTIONS,
            "planning_enabled": False,
            "api_catalog_mode": "off",
            "concept_retrieval_mode": "off",
            "diagnostic_retrieval_mode": "off",
            "repeats": 1,
            "dataset_role": "development_structural_topic_skill_routing_smoke",
            "pydantic_ai_version": version("pydantic-ai-slim"),
        },
    )
    observations: list[dict[str, object]] = []
    for case in cases:
        workspace = root / case.case_id / "workspace"
        state = root / case.case_id / "state"
        workspace.mkdir(parents=True)
        state.mkdir()
        for name, content in case.seed_files.items():
            with (workspace / name).open("x", encoding="utf-8", newline="\n") as stream:
                stream.write(content)
        request = CodingRunRequest(
            run_id=f"structural-topic-skill-routing-{case.case_id}",
            goal=case.goal,
            workspace=workspace,
            state_dir=state,
            runtime=RuntimeProfile(
                launcher_path=DEFAULT_ABAQUS_2024_LAUNCHER.resolve()
            ),
            **budget.as_request_controls(),
        )
        initial = WorkspaceResolver(captured_cwd=workspace).snapshot(workspace)
        model, client = create_deepseek_chat_model(
            gateway=gateway,
            model_name=model_name,
            api_key=api_key,
            http_retries=http_retries,
            conversation_id=request.run_id
            if gateway is DeepSeekGateway.OPENCODE_GO
            else None,
        )
        dependencies = CodingAgentDependencies(
            request=request,
            runner=Abaqus2024Runner(workspace, state),
        )
        service = CodingRunService(
            agent=create_abaqus_coding_agent(
                model,
                request,
                skill_library=skill_library_path(),
                planning_enabled=False,
                task_instructions=TASK_INSTRUCTIONS,
                foundation_skill_profile="off",
                structural_topic_skill_profile=STRUCTURAL_TOPIC_PROFILE,
            ),
            request=request,
            dependencies=dependencies,
            clarification_provider=ScriptedClarificationProvider({}),
            verifier=ExecutionEvidenceVerifier(),
            initial_snapshot=initial,
        )
        error: str | None = None
        try:
            await service.run()
        except Exception as exc:
            error = type(exc).__name__
        finally:
            await client.close()
        observation = observe_case(root, case)
        observation["infrastructure_error_type"] = error
        observation["actual_abaqus_calls"] = dependencies.abaqus_calls
        observation["actual_solver_calls"] = dependencies.solver_calls
        _write_new(
            state / "structural_topic_skill_routing_observation.json", observation
        )
        observations.append(observation)
        print(
            f"{case.case_id}: loads={observation['load_sequence']}; "
            f"exact={observation['exact_route_pass']}",
            flush=True,
        )
    if implementation_hashes != {
        path.relative_to(REPO_ROOT).as_posix(): _sha256(path)
        for path in implementation_paths
    }:
        raise RuntimeError(
            "implementation changed during structural topic routing evaluation"
        )
    if skill_hashes != _skill_bindings():
        raise RuntimeError(
            "Skill content changed during structural topic routing evaluation"
        )
    load_cases(cases_path, seal_path)
    report = {
        "manifest_sha256": _sha256(root / "manifest.json"),
        "exact_route_passes": sum(
            bool(item["exact_route_pass"]) for item in observations
        ),
        "case_count": len(observations),
        "forbidden_loads_total": sum(
            _required_int(item, "forbidden_load_count") for item in observations
        ),
        "load_calls_total": sum(
            _required_int(item, "load_call_count") for item in observations
        ),
        "duplicate_load_calls_total": sum(
            _required_int(item, "duplicate_load_call_count") for item in observations
        ),
        "loaded_skill_body_bytes_total": sum(
            _required_int(item, "loaded_skill_body_bytes") for item in observations
        ),
        "runtime_requested_cases": sum(
            bool(item["runtime_requested"]) for item in observations
        ),
        "cases": observations,
        "review_status": "pending_manual_review",
        "environment_verified_after_run": True,
    }
    _write_new(root / "report.json", report)
    _write_new(
        root / "completion.json",
        {
            "manifest_sha256": _sha256(root / "manifest.json"),
            "report_sha256": _sha256(root / "report.json"),
            "scope": "structural topic Skill routing only; engineering answer review remains separate",
        },
    )
    return report


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run the frozen structural-mechanics topic Skill routing smoke."
    )
    parser.add_argument("--run-root", type=Path, required=True)
    parser.add_argument(
        "--gateway",
        choices=[gateway.value for gateway in DeepSeekGateway],
        default=DeepSeekGateway.OPENCODE_GO.value,
    )
    parser.add_argument("--model", default=DEEPSEEK_V4_FLASH)
    args = parser.parse_args()
    asyncio.run(
        run_evaluation(
            args.run_root,
            gateway=DeepSeekGateway(args.gateway),
            model_name=args.model,
        )
    )


if __name__ == "__main__":
    main()
