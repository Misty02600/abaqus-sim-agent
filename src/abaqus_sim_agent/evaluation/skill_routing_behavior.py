from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
from datetime import UTC, datetime
from importlib.metadata import version
from pathlib import Path, PurePosixPath

from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic_evals import Case, Dataset
from pydantic_evals.reporting import EvaluationReportAdapter, ScalarResult

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
from abaqus_sim_agent.evaluation.skill_observation import (
    read_skill_evidence,
    score_skill_route,
    skill_content_hashes,
    write_new_json,
)
from abaqus_sim_agent.evaluation.skill_routing_evals import (
    SkillRouteEvaluator,
    SkillRoutingSummary,
)
from abaqus_sim_agent.execution import DEFAULT_ABAQUS_2024_LAUNCHER, Abaqus2024Runner
from abaqus_sim_agent.skills import skill_library_path

REPO_ROOT = Path(__file__).parents[3]
DEFAULT_CASES = REPO_ROOT / "benchmarks/coding/skill_routing_cases_v1.jsonl"
DEFAULT_SEAL = REPO_ROOT / "benchmarks/coding/skill_routing_cases_v1.seal.json"
EVALUATOR_VERSION = "linear-structural-fea-skill-routing-v2-native-evals"
FOUNDATION_PROFILE = "linear_structural_fea_v1"
CAPABILITY_IDS = (
    "abaqus-2024-coding",
    "linear-structural-fea-formulation",
    "linear-structural-fea-modeling",
    "linear-structural-fea-discretization",
    "linear-structural-fea-postprocess",
    "linear-structural-fea-verification",
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


class SkillRoutingCase(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    benchmark_version: str = Field(pattern=r"^skill-routing-v1$")
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


def load_cases(
    path: Path = DEFAULT_CASES,
    seal_path: Path = DEFAULT_SEAL,
) -> tuple[SkillRoutingCase, ...]:
    raw = path.read_bytes()
    seal = json.loads(seal_path.read_text(encoding="utf-8"))
    if hashlib.sha256(raw).hexdigest() != seal["sha256"]:
        raise ValueError("skill routing case file no longer matches the frozen seal")
    cases = tuple(
        SkillRoutingCase.model_validate_json(line)
        for line in raw.decode("utf-8").splitlines()
        if line.strip()
    )
    if len(cases) != seal["case_count"]:
        raise ValueError("skill routing case count no longer matches the frozen seal")
    if len({case.case_id for case in cases}) != len(cases):
        raise ValueError("skill routing case identifiers must be unique")
    if tuple(seal["capability_ids"]) != CAPABILITY_IDS:
        raise ValueError("frozen capability set does not match evaluator")
    return cases


def observe_case(root: Path, case: SkillRoutingCase) -> dict[str, object]:
    evidence = read_skill_evidence(root / case.case_id)
    return {
        **evidence.observation_fields(case.case_id),
        **score_skill_route(
            evidence.load_ids,
            required_loads=case.required_loads,
            allowed_loads=case.allowed_loads,
            forbidden_loads=case.forbidden_loads,
        ),
        "expected_behavior": case.expected_behavior,
        "review_criteria": list(case.review_criteria),
        "review_status": "pending_manual_review",
    }


def _skill_bindings() -> dict[str, str]:
    return skill_content_hashes(skill_library_path(), CAPABILITY_IDS)


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
        Path(__file__).with_name("skill_observation.py"),
        Path(__file__).with_name("skill_routing_evals.py"),
        REPO_ROOT / "src/abaqus_sim_agent/coding/agent.py",
    ]
    implementation_hashes = {
        path.relative_to(REPO_ROOT).as_posix(): _sha256(path)
        for path in implementation_paths
    }
    skill_hashes = _skill_bindings()
    root.mkdir(parents=True, exist_ok=False)
    write_new_json(
        root / "cases.json", [case.model_dump(mode="json") for case in cases]
    )
    manifest = {
        "evaluator_version": EVALUATOR_VERSION,
        "created_at": datetime.now(UTC).isoformat(),
        "case_source": cases_path.relative_to(REPO_ROOT).as_posix(),
        "case_source_sha256": _sha256(cases_path),
        "seal_sha256": _sha256(seal_path),
        "implementation_sha256": implementation_hashes,
        "skill_sha256": skill_hashes,
        "foundation_skill_profile": FOUNDATION_PROFILE,
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
        "dataset_role": "development_skill_routing_smoke",
        "pydantic_ai_version": version("pydantic-ai-slim"),
        "pydantic_evals_version": version("pydantic-evals"),
    }
    write_new_json(root / "manifest.json", manifest)
    fatal_errors: list[Exception] = []
    batch_task = asyncio.current_task()
    cancel_requested = False

    async def run_case(case: SkillRoutingCase) -> dict[str, object]:
        nonlocal cancel_requested
        if cancel_requested:
            raise asyncio.CancelledError()
        if fatal_errors:
            raise RuntimeError(
                "Case not started after an earlier preparation/observation failure"
            )
        try:
            return await execute_case(case)
        except asyncio.CancelledError:
            cancel_requested = True
            # Evals 2.51 can collect a cancelled child as None; propagate cancellation to the batch owner.
            if batch_task is not None and not batch_task.cancelling():
                batch_task.cancel()
            raise
        except Exception as exc:
            fatal_errors.append(exc)
            raise

    async def execute_case(case: SkillRoutingCase) -> dict[str, object]:
        workspace = root / case.case_id / "workspace"
        state = root / case.case_id / "state"
        workspace.mkdir(parents=True)
        state.mkdir()
        for name, content in case.seed_files.items():
            with (workspace / name).open("x", encoding="utf-8", newline="\n") as stream:
                stream.write(content)
        request = CodingRunRequest(
            run_id=f"skill-routing-{case.case_id}",
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
        try:
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
                    foundation_skill_profile=FOUNDATION_PROFILE,
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
        write_new_json(state / "skill_routing_observation.json", observation)
        print(
            f"{case.case_id}: loads={observation['load_sequence']}; "
            f"exact={observation['exact_route_pass']}",
            flush=True,
        )
        return observation

    dataset = Dataset[SkillRoutingCase, dict[str, object], None](
        name=EVALUATOR_VERSION,
        cases=[Case(name=case.case_id, inputs=case) for case in cases],
        evaluators=[SkillRouteEvaluator[SkillRoutingCase]()],
        report_evaluators=[SkillRoutingSummary[SkillRoutingCase]()],
    )
    native = await dataset.evaluate(
        run_case,
        name=root.name,
        metadata={
            **manifest,
            "manifest_sha256": _sha256(root / "manifest.json"),
            "environment_verified_after_run": False,
        },
        max_concurrency=1,
        repeat=1,
        retry_task=None,
        retry_evaluators=None,
        progress=False,
    )
    if (
        fatal_errors
        or native.failures
        or native.report_evaluator_failures
        or any(case.evaluator_failures for case in native.cases)
    ):
        write_new_json(
            root / "pydantic-evals-report.json",
            json.loads(EvaluationReportAdapter.dump_json(native)),
        )
        if fatal_errors:
            raise fatal_errors[0]
        raise RuntimeError(
            "Skill routing evaluation failed; per-case evidence was retained"
        )
    observations = [case.output for case in native.cases]
    summary = {
        item.title: item.value
        for item in native.analyses
        if isinstance(item, ScalarResult)
    }
    if implementation_hashes != {
        path.relative_to(REPO_ROOT).as_posix(): _sha256(path)
        for path in implementation_paths
    }:
        raise RuntimeError("implementation changed during skill routing evaluation")
    if skill_hashes != _skill_bindings():
        raise RuntimeError("Skill content changed during skill routing evaluation")
    load_cases(cases_path, seal_path)
    report = {
        "manifest_sha256": _sha256(root / "manifest.json"),
        "exact_route_passes": summary["exact_route_passes"],
        "case_count": summary["case_count"],
        "forbidden_loads_total": summary["forbidden_loads_total"],
        "load_calls_total": summary["load_calls_total"],
        "cases": observations,
        "review_status": "pending_manual_review",
        "environment_verified_after_run": True,
    }
    assert native.experiment_metadata is not None
    native.experiment_metadata["environment_verified_after_run"] = True
    with (root / "pydantic-evals-report.json").open("xb") as stream:
        stream.write(EvaluationReportAdapter.dump_json(native, indent=2))
    write_new_json(root / "report.json", report)
    write_new_json(
        root / "completion.json",
        {
            "manifest_sha256": _sha256(root / "manifest.json"),
            "report_sha256": _sha256(root / "report.json"),
            "scope": "skill routing behavior only; engineering answer review remains separate",
        },
    )
    return report


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run the frozen linear-structural FEA deferred-Skill routing smoke."
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
