"""在预先封存的代码快照中执行 12 条离线 Foundation 对照。"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sys
from datetime import UTC, datetime
from importlib.metadata import distributions
from pathlib import Path
from typing import Any

from pydantic_ai.models.function import FunctionModel

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
    DeepSeekGateway,
    ModelExecutionConfig,
    create_deepseek_chat_model,
    take_gateway_api_key_from_environment,
)
from abaqus_sim_agent.coding.service import CodingRunService
from abaqus_sim_agent.coding.workspace import WorkspaceResolver
from abaqus_sim_agent.evaluation.behavior_measurement import CaseRubric, file_digest
from abaqus_sim_agent.evaluation.foundation_skill_paired_pilot import (
    FOUNDATION_PROFILE,
    PilotCase,
    _observe_trial,
    build_trial_plan,
)
from abaqus_sim_agent.execution import DEFAULT_ABAQUS_2024_LAUNCHER, Abaqus2024Runner
from abaqus_sim_agent.skills import skill_library_path

VERSION = "foundation-followup-v2"
TASK_INSTRUCTIONS = """Complete this offline engineering inspection task using the supplied workspace and
deferred capabilities only when they help resolve the task. Do not run Abaqus or
any other program, and do not use Catalog or document retrieval. Write
analysis.json with summary (string), findings (array of strings), and next_action
(string). Preserve facts already fixed by the user or workspace. If a required
user-specific physical input is missing, identify its effect and stop for
clarification without inventing a value. Otherwise inspect the requested artifact
and submit ready_for_verification."""
CASE_IDS = (
    "missing-user-physical-input",
    "stress-result-semantics",
    "plate-with-hole-evidence-boundary-v2",
)
SNAPSHOT_ROOT = Path(__file__).parents[3]


def write_new(path: Path, value: object) -> None:
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write("\n")


def read(path: Path) -> Any:
    return json.loads(path.read_bytes())


def execution_config() -> ModelExecutionConfig:
    return ModelExecutionConfig(
        model_provider=DeepSeekGateway.OPENCODE_GO,
        model_name=DEEPSEEK_V4_FLASH,
        http_retries=10,
        http_timeout_seconds=60.0,
        reasoning_policy="provider_default",
        planning_enabled=False,
        interaction_policy="legacy",
    )


def budget() -> CodingRunBudget:
    return CodingRunBudget(
        max_model_requests=10,
        max_api_lookup_calls=0,
        max_concept_lookup_calls=0,
        max_diagnostic_retrieval_calls=0,
        max_abaqus_calls=0,
        max_solver_calls=0,
        wall_time_seconds=360.0,
    )


def load_inputs(root: Path) -> tuple[tuple[PilotCase, ...], dict[str, CaseRubric]]:
    cases = tuple(
        PilotCase.model_validate_json(json.dumps(row))
        for row in read(root / "agent_cases.json")
    )
    rubrics = {
        row["case_id"]: CaseRubric.model_validate_json(json.dumps(row))
        for row in read(root / "rubrics.json")
    }
    if tuple(c.case_id for c in cases) != CASE_IDS or set(rubrics) != set(CASE_IDS):
        raise ValueError("follow-up must contain exactly the three declared cases")
    if any(c.seed_files for c in cases):
        raise ValueError("this follow-up has no seed files")
    return cases, rubrics


def verify_seal(root: Path) -> dict:
    seal = read(root / "seal.json")
    for name, sha in seal["snapshot_files"].items():
        if file_digest(root / name) != sha:
            raise ValueError(f"snapshot binding changed: {name}")
    for name, sha in seal["external_runtime_files"].items():
        if file_digest(Path(name)) != sha:
            raise ValueError(f"installed runtime binding changed: {name}")
    versions = {d.metadata["Name"]: d.version for d in distributions()}
    if versions != seal["installed_versions"]:
        raise ValueError("installed package inventory changed")
    if str(SNAPSHOT_ROOT.resolve()) != str((root / "snapshot").resolve()):
        raise ValueError("worker must import the frozen source snapshot")
    if sys.version != seal["python_version"]:
        raise ValueError("Python version changed")
    return seal


def request_for(root: Path, trial, case: PilotCase) -> CodingRunRequest:
    workspace = root / "trials" / trial.trial_id / "workspace"
    state = workspace.parent / "state"
    return CodingRunRequest(
        run_id=f"foundation-followup-v2-{trial.trial_id}",
        goal=case.goal,
        workspace=workspace,
        state_dir=state,
        runtime=RuntimeProfile(launcher_path=DEFAULT_ABAQUS_2024_LAUNCHER.resolve()),
        **budget().as_request_controls(),
    )


def make_agent(model, request: CodingRunRequest, profile: str):
    return create_abaqus_coding_agent(
        model,
        request,
        skill_library=skill_library_path(),
        planning_enabled=False,
        execution_config=execution_config(),
        question_protocol="legacy_decision",
        task_instructions=TASK_INSTRUCTIONS,
        foundation_skill_profile=FOUNDATION_PROFILE
        if profile == "foundation"
        else "off",
    )


async def preflight(root: Path) -> None:
    """运行本地 FunctionModel 预检；不创建网关客户端或消耗真实请求。"""
    from pydantic_ai.messages import ModelResponse, ToolCallPart

    verify_seal(root)
    cases, _ = load_inputs(root)
    plan = build_trial_plan(cases, 2)
    assert len(plan) == 12
    observations = {}
    for profile in ("control", "foundation"):
        trial = next(t for t in plan if t.profile == profile)
        test_root = root / "preflight" / profile
        request = request_for(test_root, trial, cases[0])
        request.workspace.mkdir(parents=True)
        request.state_dir.mkdir()
        seen = {}

        def fake(messages, info, seen=seen, request=request):
            seen["deferred_capability_ids"] = sorted(
                info.model_request_parameters.deferred_capability_ids or []
            )
            seen["function_tools"] = sorted(t.name for t in info.function_tools)
            seen["tool_schemas"] = {
                t.name: t.parameters_json_schema for t in info.function_tools
            }
            seen["instructions"] = [
                str(getattr(m, "instructions", "")).replace(
                    str(request.workspace), "<workspace>"
                )
                for m in messages
                if getattr(m, "instructions", None)
            ]
            tool = next(
                t for t in info.output_tools if t.name.endswith("NeedsInputDecision")
            )
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        tool.name,
                        {
                            "status": "needs_input",
                            "clarification": {
                                "kind": "missing_physical_input",
                                "question": "What is the plate thickness?",
                                "field_id": "plate_thickness",
                                "impact": "Thickness changes the reaction.",
                            },
                            "summary": "Missing thickness",
                        },
                    )
                ]
            )

        agent = make_agent(FunctionModel(fake), request, profile)
        deps = CodingAgentDependencies(
            request=request,
            runner=Abaqus2024Runner(request.workspace, request.state_dir),
        )
        await agent.run(request.goal, deps=deps)
        if "run_command" in seen["function_tools"] or any(
            "shell" in name.lower() for name in seen["function_tools"]
        ):
            raise ValueError("general shell must not be exposed")
        observations[profile] = seen
    if (
        observations["control"]["tool_schemas"]
        != observations["foundation"]["tool_schemas"]
    ):
        raise ValueError("ordinary tool schemas differ across profiles")

    def without_foundation_metadata(instructions):
        return [
            "\n".join(
                line
                for line in text.splitlines()
                if not line.startswith("- linear-structural-fea-")
            )
            for text in instructions
        ]

    if without_foundation_metadata(
        observations["control"]["instructions"]
    ) != without_foundation_metadata(observations["foundation"]["instructions"]):
        raise ValueError("non-capability instructions differ across profiles")
    expected = {
        "abaqus-2024-coding",
        "linear-structural-fea-formulation",
        "linear-structural-fea-modeling",
        "linear-structural-fea-discretization",
        "linear-structural-fea-postprocess",
        "linear-structural-fea-verification",
    }
    if set(
        observations["foundation"]["deferred_capability_ids"]
    ) != expected or observations["control"]["deferred_capability_ids"] != [
        "abaqus-2024-coding"
    ]:
        raise ValueError("unexpected capability set")
    write_new(
        root / "preflight.json",
        {
            "passed": True,
            "real_model_calls": 0,
            "abaqus_calls": 0,
            "profiles": observations,
            "seal_sha256": file_digest(root / "seal.json"),
        },
    )
    print("preflight passed", flush=True)


async def run(root: Path) -> None:
    seal = verify_seal(root)
    checked = read(root / "preflight.json")
    if not checked["passed"] or checked["seal_sha256"] != file_digest(
        root / "seal.json"
    ):
        raise ValueError("preflight not bound to this seal")
    if (root / "started.json").exists():
        raise FileExistsError("batch already started; no implicit rerun")
    api_key = take_gateway_api_key_from_environment(DeepSeekGateway.OPENCODE_GO)
    if not api_key.get_secret_value():
        raise ValueError("OPENCODE_API_KEY unavailable")
    cases, _ = load_inputs(root)
    plan = build_trial_plan(cases, 2)
    manifest = {
        "evaluator_version": VERSION,
        "created_at": datetime.now(UTC).isoformat(),
        "case_ids": list(CASE_IDS),
        "repetitions": 2,
        "trial_plan": [t.model_dump(mode="json") for t in plan],
        "seal_sha256": file_digest(root / "seal.json"),
        "source_sha256": {
            name: sha
            for name, sha in seal["snapshot_files"].items()
            if name in {"agent_cases.json", "rubrics.json"}
        },
        "execution_config": execution_config().model_dump(mode="json"),
        "budget": budget().model_dump(mode="json"),
        "task_instructions": TASK_INSTRUCTIONS,
        "question_protocol": "legacy_decision",
        "planning_enabled": False,
        "api_catalog_mode": "off",
        "concept_retrieval_mode": "off",
        "diagnostic_retrieval_mode": "off",
        "runtime_expected": False,
        "dataset_role": "development_followup_not_heldout",
        "treatment": {
            "control": "off",
            "foundation": FOUNDATION_PROFILE,
            "only_intended_difference": "foundation_skill_profile",
        },
    }
    write_new(root / "manifest.json", manifest)
    write_new(
        root / "started.json",
        {
            "at": datetime.now(UTC).isoformat(),
            "manifest_sha256": file_digest(root / "manifest.json"),
        },
    )
    observations = []
    for index, trial in enumerate(plan, 1):
        verify_seal(root)
        case = next(c for c in cases if c.case_id == trial.case_id)
        request = request_for(root, trial, case)
        request.workspace.mkdir(parents=True)
        request.state_dir.mkdir()
        transport_path = request.state_dir / "transport_events.jsonl"

        def event(kind: str, path=transport_path, **values):
            with path.open("a", encoding="utf-8", newline="\n") as stream:
                stream.write(
                    json.dumps(
                        {"event": kind, "at": datetime.now(UTC).isoformat(), **values}
                    )
                    + "\n"
                )

        async def on_request(http_request):
            event(
                "http_attempt_started",
                retry_count=http_request.headers.get("x-stainless-retry-count"),
                body_sha256=hashlib.sha256(http_request.content).hexdigest(),
            )

        async def on_response(response):
            event(
                "http_response_headers",
                status_code=response.status_code,
                retry_count=response.request.headers.get("x-stainless-retry-count"),
            )

        model, client = create_deepseek_chat_model(
            gateway=DeepSeekGateway.OPENCODE_GO,
            model_name=DEEPSEEK_V4_FLASH,
            api_key=api_key,
            timeout_seconds=60.0,
            http_retries=10,
            conversation_id=request.run_id,
            execution_config=execution_config(),
        )
        client._client.event_hooks["request"].append(on_request)
        client._client.event_hooks["response"].append(on_response)
        deps = CodingAgentDependencies(
            request=request,
            runner=Abaqus2024Runner(request.workspace, request.state_dir),
        )
        service = CodingRunService(
            agent=make_agent(model, request, trial.profile),
            request=request,
            dependencies=deps,
            clarification_provider=ScriptedClarificationProvider({}),
            verifier=ExecutionEvidenceVerifier(),
            initial_snapshot=WorkspaceResolver(captured_cwd=request.workspace).snapshot(
                request.workspace
            ),
        )
        error = None
        event("trial_started", trial_id=trial.trial_id)
        print(f"[{index}/12] START {trial.trial_id}", flush=True)
        try:
            await service.run()
        except Exception as exc:
            error = type(exc).__name__
        finally:
            await client.close()
        observation = _observe_trial(request.workspace.parent, trial)
        observation.update(
            infrastructure_error_type=error,
            actual_abaqus_calls=deps.abaqus_calls,
            actual_solver_calls=deps.solver_calls,
        )
        write_new(
            request.state_dir / "foundation_paired_pilot_observation.json", observation
        )
        observations.append(observation)
        outcome = observation.get("outcome")
        failure_stage = (
            outcome.get("failure_stage") if isinstance(outcome, dict) else None
        )
        event(
            "trial_finished",
            error_type=error,
            failure_stage=failure_stage,
        )
        print(
            f"[{index}/12] END {trial.trial_id} failure={failure_stage} loads={observation['load_sequence']}",
            flush=True,
        )
        if deps.abaqus_calls or deps.solver_calls:
            raise RuntimeError("zero-runtime constraint violated")
    verify_seal(root)
    write_new(
        root / "report.json",
        {
            "trials": observations,
            "trial_count": 12,
            "environment_verified_after_run": True,
            "review_status": "pending_host_semantic_review",
        },
    )
    write_new(
        root / "completion.json",
        {
            "manifest_sha256": file_digest(root / "manifest.json"),
            "report_sha256": file_digest(root / "report.json"),
            "seal_sha256": file_digest(root / "seal.json"),
        },
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("preflight", "run"))
    parser.add_argument("--root", type=Path, required=True)
    args = parser.parse_args()
    asyncio.run(
        preflight(args.root.resolve())
        if args.mode == "preflight"
        else run(args.root.resolve())
    )


if __name__ == "__main__":
    main()
