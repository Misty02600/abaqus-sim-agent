from __future__ import annotations

import argparse
import asyncio
import json
from datetime import UTC, datetime
from importlib.metadata import version
from pathlib import Path
from typing import Literal

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
from abaqus_sim_agent.evaluation.behavior_measurement import CaseRubric, file_digest
from abaqus_sim_agent.evaluation.skill_routing_behavior import (
    CAPABILITY_IDS,
    DEFAULT_CASES,
    DEFAULT_SEAL,
    SkillRoutingCase,
    load_cases,
)
from abaqus_sim_agent.execution import DEFAULT_ABAQUS_2024_LAUNCHER, Abaqus2024Runner
from abaqus_sim_agent.skills import skill_library_path

REPO_ROOT = Path(__file__).parents[3]
DEFAULT_RUBRIC = (
    REPO_ROOT / "benchmarks/coding/foundation_skill_behavior_rubric_v1.json"
)
DEFAULT_PROTOCOL = (
    REPO_ROOT / "benchmarks/coding/foundation_skill_behavior_protocol_v1.md"
)
EVALUATOR_VERSION = "linear-structural-fea-foundation-behavior-v1"
Profile = Literal["control", "foundation"]
TASK_INSTRUCTIONS = """
Complete this offline engineering inspection task using the supplied workspace and
deferred capabilities only when they help resolve the task. Do not run Abaqus or
any other program, and do not use Catalog or document retrieval. Write
analysis.json with summary (string), findings (array of strings), and next_action
(string). Preserve facts already fixed by the user or workspace. If a required
user-specific physical input is missing, identify its effect and stop for
clarification without inventing a value. Otherwise inspect the requested artifact
and submit ready_for_verification.
""".strip()


def _write_new(path: Path, value: object) -> None:
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write("\n")


def _required_int(item: dict[str, object], key: str) -> int:
    value = item[key]
    if not isinstance(value, (str, int, float)):
        raise TypeError(f"{key} must be numeric")
    return int(value)


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


def load_rubrics(path: Path = DEFAULT_RUBRIC) -> dict[str, CaseRubric]:
    payload = json.loads(path.read_bytes())
    if payload.get("schema_version") != "foundation-skill-behavior-rubric-v1":
        raise ValueError("unsupported foundation behavior rubric")
    rubrics = {
        item["case_id"]: CaseRubric.model_validate_json(json.dumps(item))
        for item in payload["cases"]
    }
    cases = load_cases()
    expected = {case.case_id for case in cases}
    if set(rubrics) != expected or len(rubrics) != len(cases):
        raise ValueError("behavior rubric must cover every frozen routing case exactly")
    return rubrics


def _loaded_skill_body_bytes(load_ids: list[str]) -> int:
    root = skill_library_path()
    total = 0
    for capability_id in dict.fromkeys(load_ids):
        if capability_id not in CAPABILITY_IDS:
            continue
        path = root / capability_id / "SKILL.md"
        if path.is_file():
            total += path.stat().st_size
    return total


def observe_case(root: Path, case: SkillRoutingCase) -> dict[str, object]:
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
    forbidden = set(case.forbidden_loads)
    return {
        "case_id": case.case_id,
        "load_sequence": load_ids,
        "unique_loaded": sorted(loaded),
        "forbidden_load_count": len(loaded & forbidden),
        "unexpected_loads": sorted(loaded & forbidden),
        "load_call_count": len(load_calls),
        "duplicate_load_call_count": len(load_ids) - len(loaded),
        "loaded_skill_body_bytes": _loaded_skill_body_bytes(load_ids),
        "runtime_requested": any(
            call["tool"] in {"run_cae_script", "run_solver_job", "run_abaqus_python"}
            for call in calls
        ),
        "analysis": analysis,
        "outcome": outcome,
        "behavior_review_status": "pending_host_semantic_review",
    }


def _skill_hashes(profile: Profile) -> dict[str, str]:
    root = skill_library_path()
    ids = ["abaqus-2024-coding"]
    if profile == "foundation":
        ids.extend(
            capability
            for capability in CAPABILITY_IDS
            if capability != "abaqus-2024-coding"
        )
    return {
        capability: file_digest(root / capability / "SKILL.md") for capability in ids
    }


async def run_evaluation(
    run_root: Path,
    *,
    profile: Profile,
    repetition: int,
    gateway: DeepSeekGateway = DeepSeekGateway.OPENCODE_GO,
    model_name: str = DEEPSEEK_V4_FLASH,
    http_retries: int = DEFAULT_MODEL_HTTP_RETRIES,
) -> dict[str, object]:
    if repetition < 1:
        raise ValueError("repetition must be positive")
    cases = load_cases(DEFAULT_CASES, DEFAULT_SEAL)
    rubrics = load_rubrics()
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
        path.relative_to(REPO_ROOT).as_posix(): file_digest(path)
        for path in implementation_paths
    }
    skill_hashes = _skill_hashes(profile)
    root.mkdir(parents=True, exist_ok=False)
    _write_new(root / "cases.json", [case.model_dump(mode="json") for case in cases])
    _write_new(
        root / "rubrics.json",
        [rubrics[case.case_id].model_dump(mode="json") for case in cases],
    )
    manifest = {
        "evaluator_version": EVALUATOR_VERSION,
        "created_at": datetime.now(UTC).isoformat(),
        "profile": profile,
        "repetition": repetition,
        "foundation_skill_profile": "linear_structural_fea_v1"
        if profile == "foundation"
        else "off",
        "case_source": DEFAULT_CASES.relative_to(REPO_ROOT).as_posix(),
        "case_source_sha256": file_digest(DEFAULT_CASES),
        "case_seal_sha256": file_digest(DEFAULT_SEAL),
        "rubric_source": DEFAULT_RUBRIC.relative_to(REPO_ROOT).as_posix(),
        "rubric_sha256": file_digest(DEFAULT_RUBRIC),
        "protocol_sha256": file_digest(DEFAULT_PROTOCOL),
        "implementation_sha256": implementation_hashes,
        "skill_sha256": skill_hashes,
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
        "pydantic_ai_version": version("pydantic-ai-slim"),
        "dataset_role": "development_foundation_skill_behavior",
    }
    _write_new(root / "manifest.json", manifest)

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
            run_id=f"foundation-behavior-{profile}-r{repetition}-{case.case_id}",
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
                foundation_skill_profile=manifest["foundation_skill_profile"],
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
        observation["trial_id"] = f"{profile}-{case.case_id}-r{repetition}"
        observation["profile"] = profile
        observation["repetition"] = repetition
        observation["infrastructure_error_type"] = error
        observation["actual_abaqus_calls"] = dependencies.abaqus_calls
        observation["actual_solver_calls"] = dependencies.solver_calls
        _write_new(state / "foundation_behavior_observation.json", observation)
        observations.append(observation)
        print(
            f"{case.case_id}: loads={observation['load_sequence']}; review=pending",
            flush=True,
        )

    current_implementation = {
        path.relative_to(REPO_ROOT).as_posix(): file_digest(path)
        for path in implementation_paths
    }
    if implementation_hashes != current_implementation:
        raise RuntimeError(
            "implementation changed during foundation behavior evaluation"
        )
    if skill_hashes != _skill_hashes(profile):
        raise RuntimeError(
            "Skill content changed during foundation behavior evaluation"
        )
    load_cases(DEFAULT_CASES, DEFAULT_SEAL)
    if file_digest(DEFAULT_RUBRIC) != manifest["rubric_sha256"]:
        raise RuntimeError("behavior rubric changed during evaluation")
    report = {
        "manifest_sha256": file_digest(root / "manifest.json"),
        "profile": profile,
        "repetition": repetition,
        "case_count": len(observations),
        "pending_behavior_reviews": len(observations),
        "forbidden_loads_total": sum(
            _required_int(item, "forbidden_load_count") for item in observations
        ),
        "load_calls_total": sum(
            _required_int(item, "load_call_count") for item in observations
        ),
        "loaded_skill_body_bytes_total": sum(
            _required_int(item, "loaded_skill_body_bytes") for item in observations
        ),
        "cases": observations,
        "behavior_review_status": "pending_host_semantic_review",
        "environment_verified_after_run": True,
    }
    _write_new(root / "report.json", report)
    _write_new(
        root / "completion.json",
        {
            "manifest_sha256": file_digest(root / "manifest.json"),
            "report_sha256": file_digest(root / "report.json"),
            "scope": "raw behavior batch only; semantic engineering review is a separate host step",
        },
    )
    return report


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run one frozen foundation-Skill engineering-behavior batch."
    )
    parser.add_argument("--run-root", type=Path, required=True)
    parser.add_argument("--profile", choices=("control", "foundation"), required=True)
    parser.add_argument("--repetition", type=int, required=True)
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
            profile=args.profile,
            repetition=args.repetition,
            gateway=DeepSeekGateway(args.gateway),
            model_name=args.model,
        )
    )


if __name__ == "__main__":
    main()
