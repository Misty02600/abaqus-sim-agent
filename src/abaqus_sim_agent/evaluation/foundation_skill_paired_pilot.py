from __future__ import annotations

import argparse
import asyncio
import json
from datetime import UTC, datetime
from importlib.metadata import version
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field
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
from abaqus_sim_agent.evaluation.foundation_public_mechanics_benchmark import (
    DEFAULT_CASES as PUBLIC_CASES,
)
from abaqus_sim_agent.evaluation.foundation_public_mechanics_benchmark import (
    DEFAULT_PROTOCOL as PUBLIC_PROTOCOL,
)
from abaqus_sim_agent.evaluation.foundation_public_mechanics_benchmark import (
    DEFAULT_RUBRIC as PUBLIC_RUBRIC,
)
from abaqus_sim_agent.evaluation.foundation_public_mechanics_benchmark import (
    DEFAULT_SEAL as PUBLIC_SEAL,
)
from abaqus_sim_agent.evaluation.foundation_public_mechanics_benchmark import (
    DEFAULT_SOURCES as PUBLIC_SOURCES,
)
from abaqus_sim_agent.evaluation.foundation_public_mechanics_benchmark import (
    PILOT_DIAGNOSTIC_CASE_IDS,
    PILOT_PUBLIC_CASE_IDS,
    load_benchmark,
)
from abaqus_sim_agent.evaluation.foundation_skill_behavior import (
    DEFAULT_PROTOCOL as DIAGNOSTIC_PROTOCOL,
)
from abaqus_sim_agent.evaluation.foundation_skill_behavior import (
    DEFAULT_RUBRIC as DIAGNOSTIC_RUBRIC,
)
from abaqus_sim_agent.evaluation.foundation_skill_behavior import (
    TASK_INSTRUCTIONS,
)
from abaqus_sim_agent.evaluation.foundation_skill_behavior import (
    load_rubrics as load_diagnostic_rubrics,
)
from abaqus_sim_agent.evaluation.skill_observation import required_int
from abaqus_sim_agent.evaluation.skill_routing_behavior import (
    CAPABILITY_IDS,
)
from abaqus_sim_agent.evaluation.skill_routing_behavior import (
    DEFAULT_CASES as DIAGNOSTIC_CASES,
)
from abaqus_sim_agent.evaluation.skill_routing_behavior import (
    DEFAULT_SEAL as DIAGNOSTIC_SEAL,
)
from abaqus_sim_agent.evaluation.skill_routing_behavior import (
    load_cases as load_diagnostic_cases,
)
from abaqus_sim_agent.execution import DEFAULT_ABAQUS_2024_LAUNCHER, Abaqus2024Runner
from abaqus_sim_agent.skills import skill_library_path

REPO_ROOT = Path(__file__).parents[3]
EVALUATOR_VERSION = "foundation-skill-paired-pilot-v1"
FOUNDATION_PROFILE = "linear_structural_fea_v1"
DEFAULT_REPETITIONS = 2

Profile = Literal["control", "foundation"]
CaseKind = Literal["public", "diagnostic"]


class StrictModel(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)


class PilotCase(StrictModel):
    case_id: str = Field(pattern=r"^[a-z][a-z0-9-]+$")
    case_kind: CaseKind
    goal: str = Field(min_length=1)
    seed_files: dict[str, str]


class PilotTrial(StrictModel):
    trial_id: str = Field(pattern=r"^[a-z0-9-]+$")
    case_id: str = Field(pattern=r"^[a-z][a-z0-9-]+$")
    case_kind: CaseKind
    repetition: int = Field(ge=1)
    profile: Profile


def _write_new(path: Path, value: object) -> None:
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write("\n")


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


def _skill_hashes() -> dict[str, str]:
    root = skill_library_path()
    return {
        capability_id: file_digest(root / capability_id / "SKILL.md")
        for capability_id in CAPABILITY_IDS
    }


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


def load_pilot_cases() -> tuple[tuple[PilotCase, ...], dict[str, CaseRubric]]:
    public_cases, public_rubrics, _ = load_benchmark()
    diagnostic_cases = load_diagnostic_cases(DIAGNOSTIC_CASES, DIAGNOSTIC_SEAL)
    diagnostic_rubrics = load_diagnostic_rubrics(DIAGNOSTIC_RUBRIC)

    public_by_id = {case.case_id: case for case in public_cases}
    public_rubric_by_id = {rubric.case_id: rubric for rubric in public_rubrics}
    diagnostic_by_id = {case.case_id: case for case in diagnostic_cases}

    missing_public = set(PILOT_PUBLIC_CASE_IDS) - set(public_by_id)
    missing_diagnostic = set(PILOT_DIAGNOSTIC_CASE_IDS) - set(diagnostic_by_id)
    if missing_public or missing_diagnostic:
        raise ValueError(
            f"pilot selection is missing frozen cases: public={sorted(missing_public)}, "
            f"diagnostic={sorted(missing_diagnostic)}"
        )

    cases: list[PilotCase] = []
    rubrics: dict[str, CaseRubric] = {}
    for case_id in PILOT_PUBLIC_CASE_IDS:
        source = public_by_id[case_id]
        cases.append(
            PilotCase(
                case_id=case_id,
                case_kind="public",
                goal=source.goal,
                seed_files=dict(source.seed_files),
            )
        )
        rubrics[case_id] = public_rubric_by_id[case_id]
    for case_id in PILOT_DIAGNOSTIC_CASE_IDS:
        source = diagnostic_by_id[case_id]
        cases.append(
            PilotCase(
                case_id=case_id,
                case_kind="diagnostic",
                goal=source.goal,
                seed_files=dict(source.seed_files),
            )
        )
        rubrics[case_id] = diagnostic_rubrics[case_id]

    if len({case.case_id for case in cases}) != len(cases):
        raise ValueError("pilot case identifiers must be unique")
    if set(rubrics) != {case.case_id for case in cases}:
        raise ValueError("pilot rubrics must cover the exact selected case set")
    return tuple(cases), rubrics


def build_trial_plan(
    cases: tuple[PilotCase, ...], repetitions: int = DEFAULT_REPETITIONS
) -> tuple[PilotTrial, ...]:
    if repetitions < 1:
        raise ValueError("repetitions must be positive")
    trials: list[PilotTrial] = []
    for repetition in range(1, repetitions + 1):
        profiles: tuple[Profile, Profile] = (
            ("control", "foundation") if repetition % 2 else ("foundation", "control")
        )
        for case in cases:
            for profile in profiles:
                trials.append(
                    PilotTrial(
                        trial_id=f"r{repetition}-{case.case_id}-{profile}",
                        case_id=case.case_id,
                        case_kind=case.case_kind,
                        repetition=repetition,
                        profile=profile,
                    )
                )
    return tuple(trials)


def agent_case_payload(case: PilotCase) -> dict[str, object]:
    """返回唯一允许进入 Agent 上下文的冻结题目内容。"""
    return {"goal": case.goal, "seed_files": dict(case.seed_files)}


def _source_bindings() -> dict[str, object]:
    return {
        "public": {
            "cases": file_digest(PUBLIC_CASES),
            "rubric": file_digest(PUBLIC_RUBRIC),
            "sources": file_digest(PUBLIC_SOURCES),
            "protocol": file_digest(PUBLIC_PROTOCOL),
            "seal": file_digest(PUBLIC_SEAL),
        },
        "diagnostic": {
            "cases": file_digest(DIAGNOSTIC_CASES),
            "rubric": file_digest(DIAGNOSTIC_RUBRIC),
            "protocol": file_digest(DIAGNOSTIC_PROTOCOL),
            "seal": file_digest(DIAGNOSTIC_SEAL),
        },
    }


def _observe_trial(trial_root: Path, trial: PilotTrial) -> dict[str, object]:
    state = trial_root / "state"
    workspace = trial_root / "workspace"
    calls = _tool_calls(state)
    load_calls = [call for call in calls if call["tool"] == "load_capability"]
    load_ids = [
        str(call["arguments"].get("id"))
        for call in load_calls
        if isinstance(call["arguments"], dict) and call["arguments"].get("id")
    ]
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
        "trial_id": trial.trial_id,
        "case_id": trial.case_id,
        "case_kind": trial.case_kind,
        "profile": trial.profile,
        "repetition": trial.repetition,
        "load_sequence": load_ids,
        "unique_loaded": sorted(set(load_ids)),
        "load_call_count": len(load_calls),
        "duplicate_load_call_count": len(load_ids) - len(set(load_ids)),
        "loaded_skill_body_bytes": _loaded_skill_body_bytes(load_ids),
        "runtime_requested": any(
            call["tool"] in {"run_cae_script", "run_solver_job", "run_abaqus_python"}
            for call in calls
        ),
        "analysis": analysis,
        "outcome": outcome,
        "review_status": "pending_host_semantic_review",
    }


async def run_pilot(
    run_root: Path,
    *,
    repetitions: int = DEFAULT_REPETITIONS,
    gateway: DeepSeekGateway = DeepSeekGateway.OPENCODE_GO,
    model_name: str = DEEPSEEK_V4_FLASH,
    http_retries: int = DEFAULT_MODEL_HTTP_RETRIES,
) -> dict[str, object]:
    cases, rubrics = load_pilot_cases()
    trials = build_trial_plan(cases, repetitions)
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
        Path(__file__).with_name("skill_observation.py"),
        REPO_ROOT / "src/abaqus_sim_agent/coding/agent.py",
    ]
    implementation_hashes = {
        path.relative_to(REPO_ROOT).as_posix(): file_digest(path)
        for path in implementation_paths
    }
    skill_hashes = _skill_hashes()
    source_bindings = _source_bindings()

    root.mkdir(parents=True, exist_ok=False)
    _write_new(
        root / "agent_cases.json",
        [
            {
                "case_id": case.case_id,
                "case_kind": case.case_kind,
                **agent_case_payload(case),
            }
            for case in cases
        ],
    )
    _write_new(
        root / "rubrics.json",
        [rubrics[case.case_id].model_dump(mode="json") for case in cases],
    )
    manifest = {
        "evaluator_version": EVALUATOR_VERSION,
        "created_at": datetime.now(UTC).isoformat(),
        "repetitions": repetitions,
        "case_ids": [case.case_id for case in cases],
        "trial_plan": [trial.model_dump(mode="json") for trial in trials],
        "source_sha256": source_bindings,
        "implementation_sha256": implementation_hashes,
        "skill_sha256": skill_hashes,
        "treatment": {
            "control": "off",
            "foundation": FOUNDATION_PROFILE,
            "only_intended_difference": "foundation_skill_profile",
        },
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
        "runtime_expected": False,
        "pydantic_ai_version": version("pydantic-ai-slim"),
        "dataset_role": "development_foundation_skill_paired_pilot",
    }
    _write_new(root / "manifest.json", manifest)

    by_case = {case.case_id: case for case in cases}
    observations: list[dict[str, object]] = []
    for index, trial in enumerate(trials, start=1):
        case = by_case[trial.case_id]
        trial_root = root / "trials" / trial.trial_id
        workspace = trial_root / "workspace"
        state = trial_root / "state"
        workspace.mkdir(parents=True)
        state.mkdir()
        for name, content in case.seed_files.items():
            with (workspace / name).open("x", encoding="utf-8", newline="\n") as stream:
                stream.write(content)

        request = CodingRunRequest(
            run_id=f"foundation-pilot-{trial.trial_id}",
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
                foundation_skill_profile=(
                    FOUNDATION_PROFILE if trial.profile == "foundation" else "off"
                ),
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

        observation = _observe_trial(trial_root, trial)
        observation["infrastructure_error_type"] = error
        observation["actual_abaqus_calls"] = dependencies.abaqus_calls
        observation["actual_solver_calls"] = dependencies.solver_calls
        _write_new(state / "foundation_paired_pilot_observation.json", observation)
        observations.append(observation)
        print(
            f"[{index}/{len(trials)}] {trial.trial_id}: "
            f"loads={observation['load_sequence']}; review=pending",
            flush=True,
        )

    if implementation_hashes != {
        path.relative_to(REPO_ROOT).as_posix(): file_digest(path)
        for path in implementation_paths
    }:
        raise RuntimeError("implementation changed during paired pilot")
    if skill_hashes != _skill_hashes():
        raise RuntimeError("Skill content changed during paired pilot")
    if source_bindings != _source_bindings():
        raise RuntimeError("frozen benchmark sources changed during paired pilot")
    load_pilot_cases()

    report = {
        "manifest_sha256": file_digest(root / "manifest.json"),
        "trial_count": len(observations),
        "pending_behavior_reviews": len(observations),
        "loaded_skill_body_bytes_total": sum(
            required_int(item, "loaded_skill_body_bytes") for item in observations
        ),
        "trials": observations,
        "review_status": "pending_host_semantic_review",
        "environment_verified_after_run": True,
    }
    _write_new(root / "report.json", report)
    _write_new(
        root / "completion.json",
        {
            "manifest_sha256": file_digest(root / "manifest.json"),
            "report_sha256": file_digest(root / "report.json"),
            "scope": "raw paired pilot only; semantic engineering review is a separate host step",
        },
    )
    return report


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run the frozen Foundation Skill paired mechanics pilot."
    )
    parser.add_argument("--run-root", type=Path, required=True)
    parser.add_argument("--repetitions", type=int, default=DEFAULT_REPETITIONS)
    parser.add_argument(
        "--gateway",
        choices=[gateway.value for gateway in DeepSeekGateway],
        default=DeepSeekGateway.OPENCODE_GO.value,
    )
    parser.add_argument("--model", default=DEEPSEEK_V4_FLASH)
    parser.add_argument("--http-retries", type=int, default=DEFAULT_MODEL_HTTP_RETRIES)
    args = parser.parse_args()
    asyncio.run(
        run_pilot(
            args.run_root,
            repetitions=args.repetitions,
            gateway=DeepSeekGateway(args.gateway),
            model_name=args.model,
            http_retries=args.http_retries,
        )
    )


if __name__ == "__main__":
    main()
