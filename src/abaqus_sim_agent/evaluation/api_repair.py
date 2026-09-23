from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import shutil
from dataclasses import dataclass
from importlib.metadata import version
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, JsonValue, SecretStr, model_validator
from pydantic_evals import Case, Dataset
from pydantic_evals.dataset import increment_eval_metric, set_eval_attribute
from pydantic_evals.evaluators import EvaluationReason, Evaluator, EvaluatorContext
from pydantic_evals.reporting import EvaluationReport

from abaqus_sim_agent.api_catalog import ApiCatalog
from abaqus_sim_agent.coding.agent import create_abaqus_coding_agent
from abaqus_sim_agent.coding.api_lookup import ApiCatalogMode
from abaqus_sim_agent.coding.clarification import ScriptedClarificationProvider
from abaqus_sim_agent.coding.contracts import (
    ApiCatalogBinding,
    ApiLookupTrace,
    CodingRunBudget,
    CodingRunOutcome,
    CodingRunRequest,
    CodingRunStatus,
    RuntimeProfile,
)
from abaqus_sim_agent.coding.dependencies import CodingAgentDependencies
from abaqus_sim_agent.coding.harness_compat import (
    apply_harness_windows_newline_hash_patch,
    apply_harness_windows_shell_tree_patch,
)
from abaqus_sim_agent.coding.instructions import (
    CODING_DECISION_OUTPUT_POLICY_VERSION,
    EVIDENCE_REPAIR_CONVERGENCE_POLICY_VERSION,
)
from abaqus_sim_agent.coding.model import (
    DEEPSEEK_V4_FLASH,
    DEFAULT_MODEL_HTTP_RETRIES,
    DEFAULT_MODEL_HTTP_TIMEOUT_SECONDS,
    DeepSeekGateway,
    create_deepseek_chat_model,
    gateway_base_url,
    take_gateway_api_key_from_environment,
)
from abaqus_sim_agent.coding.service import CodingRunService
from abaqus_sim_agent.coding.static_preflight import (
    AbqpyBasedPyrightPreflight,
    PreparedAbqpySource,
    prepare_abqpy_source,
)
from abaqus_sim_agent.coding.workspace import ResolvedWorkspace, WorkspaceResolver
from abaqus_sim_agent.execution import (
    DEFAULT_ABAQUS_2024_LAUNCHER,
    Abaqus2024Runner,
    AbaqusInvocation,
)
from abaqus_sim_agent.skills import skill_library_path
from abaqus_sim_agent.verification.api_repair import (
    ApiRepairRunVerifier,
    ApiRepairVerificationAttempt,
)

PROMPT_VERSION = "api-repair-v2-output-decision"
StaticPreflightMode = Literal["off", "on"]
DEFAULT_MANIFEST = (
    Path(__file__).parents[3] / "benchmarks" / "coding" / "api_repair_cases.jsonl"
)


class _FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class SkillCoverage(_FrozenModel):
    level: Literal["none", "partial", "direct"]
    detail_zh: str = Field(min_length=1)


class ApiRepairCaseSpec(_FrozenModel):
    schema_version: Literal["1"] = "1"
    case_id: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    split: Literal["dev", "holdout"]
    goal_zh: str = Field(min_length=1)
    fixture_dir: str = Field(min_length=1)
    agent_visible_files: tuple[str, ...] = Field(min_length=1)
    entry_script: str = Field(min_length=1)
    result_file: str = Field(min_length=1)
    expected_result: dict[str, JsonValue]
    failing_api: str = Field(min_length=1)
    api_queries: tuple[str, ...] = Field(min_length=1)
    expected_error_terms: tuple[str, ...] = Field(min_length=1)
    source_category: str = Field(min_length=1)
    runtime_note_available: bool
    skill_coverage: SkillCoverage
    fixture_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    baseline_must_fail: bool
    baseline_must_not_create_result: bool
    repaired_solution_in_fixture: bool

    @model_validator(mode="after")
    def check_paths_and_fixture_contract(self) -> "ApiRepairCaseSpec":
        for value in (
            self.fixture_dir,
            self.entry_script,
            self.result_file,
            *self.agent_visible_files,
        ):
            _relative_path(value)
        if self.entry_script not in self.agent_visible_files:
            raise ValueError("entry_script must be included in agent_visible_files")
        if not self.baseline_must_fail or not self.baseline_must_not_create_result:
            raise ValueError("repair benchmark fixtures must start from a real failure")
        if self.repaired_solution_in_fixture:
            raise ValueError("repaired solutions must not be Agent-visible fixtures")
        return self


class ApiRepairEvalInput(_FrozenModel):
    case_id: str = Field(pattern=r"^[a-z][a-z0-9_]*$")


class ApiRepairEvalMetadata(_FrozenModel):
    split: Literal["dev", "holdout"]
    failing_api: str = Field(min_length=1)
    runtime_note_available: bool
    skill_coverage: Literal["none", "partial", "direct"]


class ApiRepairObservation(_FrozenModel):
    run_id: str
    case_id: str
    outcome: CodingRunOutcome
    verification: ApiRepairVerificationAttempt | None = None
    delivered_note_ids: tuple[str, ...] = ()
    abaqus_runtime_seconds: float = Field(ge=0)
    failed_cae_calls: int = Field(ge=0)
    static_preflight_mode: StaticPreflightMode = "off"
    static_diagnostic_count: int = Field(default=0, ge=0)
    static_unavailable_count: int = Field(default=0, ge=0)


class ApiRepairEvalGate(_FrozenModel):
    attempts: int = Field(ge=0)
    passes: int = Field(ge=0)
    dev_attempts: int = Field(ge=0)
    dev_passes: int = Field(ge=0)
    holdout_attempts: int = Field(ge=0)
    holdout_passes: int = Field(ge=0)
    passed: bool


@dataclass
class ApiRepairEvaluator(
    Evaluator[ApiRepairEvalInput, ApiRepairObservation, ApiRepairEvalMetadata]
):
    def evaluate(
        self,
        ctx: EvaluatorContext[
            ApiRepairEvalInput,
            ApiRepairObservation,
            ApiRepairEvalMetadata,
        ],
    ) -> dict[str, EvaluationReason | float | str]:
        output = ctx.output
        verification = output.verification
        passed = (
            output.outcome.status is CodingRunStatus.COMPLETED
            and verification is not None
            and verification.passed
        )
        return {
            "api_repair_pass": EvaluationReason(
                value=passed,
                reason=(
                    "modified script and CAE-created result passed host verification"
                    if passed
                    else "repair failed at "
                    + (output.outcome.failure_stage or "verification")
                ),
            ),
            "script_modified_score": (
                1.0
                if verification is not None and verification.script_modified
                else 0.0
            ),
            "cae_result_score": (
                1.0
                if verification is not None
                and verification.result_created_by_successful_cae
                else 0.0
            ),
            "failure_stage": output.outcome.failure_stage or "none",
            "split": ctx.metadata.split if ctx.metadata is not None else "unknown",
        }


def load_case_specs(path: Path = DEFAULT_MANIFEST) -> tuple[ApiRepairCaseSpec, ...]:
    manifest = path.resolve(strict=True)
    cases: list[ApiRepairCaseSpec] = []
    for line_number, line in enumerate(
        manifest.read_text(encoding="utf-8").splitlines(),
        start=1,
    ):
        if not line.strip():
            continue
        try:
            case = ApiRepairCaseSpec.model_validate_json(line)
        except ValueError as exc:
            raise ValueError(
                f"invalid API repair case at {manifest}:{line_number}"
            ) from exc
        cases.append(case)
    ids = [case.case_id for case in cases]
    if not cases or len(ids) != len(set(ids)):
        raise ValueError("API repair manifest must contain unique cases")
    return tuple(cases)


def build_dataset(
    cases: tuple[ApiRepairCaseSpec, ...],
) -> Dataset[ApiRepairEvalInput, ApiRepairObservation, ApiRepairEvalMetadata]:
    return Dataset(
        name="abaqus2024_api_repair_v1",
        cases=[
            Case(
                name=case.case_id,
                inputs=ApiRepairEvalInput(case_id=case.case_id),
                metadata=ApiRepairEvalMetadata(
                    split=case.split,
                    failing_api=case.failing_api,
                    runtime_note_available=case.runtime_note_available,
                    skill_coverage=case.skill_coverage.level,
                ),
            )
            for case in cases
        ],
        evaluators=[ApiRepairEvaluator()],
    )


def gate_report(
    report: EvaluationReport[
        ApiRepairEvalInput,
        ApiRepairObservation,
        ApiRepairEvalMetadata,
    ],
) -> ApiRepairEvalGate:
    cases = list(report.cases)
    passes = sum(_report_case_passed(case) for case in cases)
    dev_cases = [case for case in cases if _label_value(case, "split") == "dev"]
    holdout_cases = [case for case in cases if _label_value(case, "split") == "holdout"]
    attempts = len(cases) + len(report.failures)
    return ApiRepairEvalGate(
        attempts=attempts,
        passes=passes,
        dev_attempts=len(dev_cases),
        dev_passes=sum(_report_case_passed(case) for case in dev_cases),
        holdout_attempts=len(holdout_cases),
        holdout_passes=sum(_report_case_passed(case) for case in holdout_cases),
        passed=attempts > 0 and passes == attempts,
    )


async def run_evaluation(
    run_root: Path,
    *,
    manifest_path: Path = DEFAULT_MANIFEST,
    model_gateway: DeepSeekGateway = DeepSeekGateway.OFFICIAL,
    model_name: str = DEEPSEEK_V4_FLASH,
    http_retries: int = DEFAULT_MODEL_HTTP_RETRIES,
    api_catalog_mode: ApiCatalogMode = "off",
    api_catalog_dir: Path | None = None,
    case_ids: tuple[str, ...] | None = None,
    static_preflight_mode: StaticPreflightMode = "off",
    abqpy_static_wheel: Path | None = None,
    static_preflight_cache_dir: Path | None = None,
    repetition: int = 1,
    api_key: SecretStr | None = None,
) -> ApiRepairEvalGate:
    if repetition < 1:
        raise ValueError("repetition must be positive")
    harness_hash_patch = apply_harness_windows_newline_hash_patch()
    harness_shell_patch = apply_harness_windows_shell_tree_patch()
    cases = load_case_specs(manifest_path)
    if case_ids is not None:
        selected = set(case_ids)
        known = {case.case_id for case in cases}
        unknown = sorted(selected - known)
        if unknown:
            raise ValueError(f"unknown API repair cases: {', '.join(unknown)}")
        cases = tuple(case for case in cases if case.case_id in selected)
        if not cases:
            raise ValueError("at least one API repair case must be selected")
    project_root = Path(__file__).parents[3]
    _validate_case_fixtures(project_root, cases)
    static_source = await asyncio.to_thread(
        _prepare_static_source,
        static_preflight_mode,
        abqpy_static_wheel,
        static_preflight_cache_dir,
    )
    static_binding = static_source.binding() if static_source is not None else None
    if api_key is None:
        api_key = take_gateway_api_key_from_environment(model_gateway)
    api_catalog = await asyncio.to_thread(
        _load_api_catalog,
        run_root,
        api_catalog_mode,
        api_catalog_dir,
    )
    manifest_digest, skill_digest = await asyncio.gather(
        asyncio.to_thread(_sha256_existing, manifest_path),
        asyncio.to_thread(
            _sha256,
            skill_library_path() / "abaqus-2024-coding" / "SKILL.md",
        ),
    )
    case_by_id = {case.case_id: case for case in cases}
    experiment_metadata = {
        "model_gateway": model_gateway.value,
        "model": model_name,
        "base_url": gateway_base_url(model_gateway),
        "protocol": "openai-chat-completions",
        "profile_family": "pydantic-ai-deepseek",
        "thinking": "provider-default",
        "thinking_history_replay": (
            "disabled-go-compat"
            if model_gateway is DeepSeekGateway.OPENCODE_GO
            else "field"
        ),
        "http_retries": http_retries,
        "http_timeout_seconds": DEFAULT_MODEL_HTTP_TIMEOUT_SECONDS,
        "api_catalog_mode": api_catalog_mode,
        "api_catalog_cards_sha256": (
            api_catalog.manifest.cards_sha256
            if api_catalog is not None and api_catalog.manifest is not None
            else None
        ),
        "api_catalog_card_count": (
            len(api_catalog.cards) if api_catalog is not None else 0
        ),
        "case_manifest_sha256": manifest_digest,
        "skill_sha256": skill_digest,
        "pydantic_ai_version": version("pydantic-ai-slim"),
        "pydantic_ai_harness_version": version("pydantic-ai-harness"),
        "harness_windows_newline_hash_patch": harness_hash_patch,
        "harness_windows_shell_tree_patch": harness_shell_patch,
        "runtime": "Abaqus 2024",
        "prompt_version": PROMPT_VERSION,
        "static_preflight_mode": static_preflight_mode,
        "static_preflight_binding": (
            static_binding.model_dump(mode="json")
            if static_binding is not None
            else None
        ),
        "repetition": repetition,
        "coding_decision_output_policy": CODING_DECISION_OUTPUT_POLICY_VERSION,
        "evidence_repair_convergence_policy": (
            EVIDENCE_REPAIR_CONVERGENCE_POLICY_VERSION
        ),
    }

    async def run_case(inputs: ApiRepairEvalInput) -> ApiRepairObservation:
        case = case_by_id[inputs.case_id]
        run_id = (
            f"{case.case_id}-r{repetition}-{static_preflight_mode}-{uuid4().hex[:10]}"
        )
        resolved = await asyncio.to_thread(
            _create_case_workspace,
            run_root,
            run_id,
            project_root,
            case,
        )
        request = CodingRunRequest(
            run_id=run_id,
            goal=case.goal_zh,
            workspace=resolved.workspace,
            state_dir=resolved.state_dir,
            runtime=RuntimeProfile(
                launcher_path=DEFAULT_ABAQUS_2024_LAUNCHER.resolve()
            ),
            api_catalog=_api_catalog_binding(api_catalog),
            static_preflight=static_binding,
            **CodingRunBudget(
                max_clarifications=0,
                max_model_requests=18,
                max_api_lookup_calls=3,
                max_abaqus_calls=5,
                max_solver_calls=0,
                wall_time_seconds=600.0,
            ).as_request_controls(),
        )
        runner = Abaqus2024Runner(
            request.workspace,
            request.state_dir,
            launcher=request.runtime.launcher_path,
        )
        model, client = create_deepseek_chat_model(
            gateway=model_gateway,
            model_name=model_name,
            api_key=api_key,
            http_retries=http_retries,
            conversation_id=request.run_id
            if model_gateway is DeepSeekGateway.OPENCODE_GO
            else None,
        )
        try:
            static_preflight = (
                AbqpyBasedPyrightPreflight(static_source, request.state_dir)
                if static_source is not None
                else None
            )
            dependencies = CodingAgentDependencies(
                request=request,
                runner=runner,
                api_catalog=api_catalog,
                static_preflight=static_preflight,
            )
            agent = create_abaqus_coding_agent(
                model,
                request,
                skill_library=skill_library_path(),
                api_catalog_mode=api_catalog_mode,
                planning_enabled=False,
                task_instructions=_task_instructions(case),
            )
            service = CodingRunService[ApiRepairVerificationAttempt](
                agent=agent,
                request=request,
                dependencies=dependencies,
                clarification_provider=ScriptedClarificationProvider({}),
                verifier=ApiRepairRunVerifier(
                    entry_script=case.entry_script,
                    result_file=case.result_file,
                    expected_result=case.expected_result,
                ),
                initial_snapshot=resolved.initial_snapshot,
            )
            result = await service.run()
        finally:
            await client.close()

        delivered_note_ids = _delivered_note_ids(request.state_dir)
        failed_cae_calls = sum(
            1
            for execution in dependencies.executions
            if execution.invocation is AbaqusInvocation.CAE_NOGUI
            and (
                not execution.succeeded
                or "Abaqus Error:" in execution.stdout
                or "Traceback (most recent call last)" in execution.stderr
            )
        )
        usage = result.outcome.usage
        static_diagnostic_count, static_unavailable_count = _static_feedback_counts(
            request.state_dir
        )
        for name, value in {
            "model_requests": usage.model_requests,
            "input_tokens": usage.input_tokens,
            "output_tokens": usage.output_tokens,
            "tool_calls": usage.tool_calls,
            "api_lookup_calls": usage.api_lookup_calls,
            "delivered_runtime_notes": len(delivered_note_ids),
            "abaqus_calls": usage.abaqus_calls,
            "failed_cae_calls": failed_cae_calls,
            "static_preflight_calls": usage.static_preflight_calls,
            "static_diagnostic_count": static_diagnostic_count,
            "static_unavailable_count": static_unavailable_count,
            "abaqus_runtime_s": sum(
                execution.duration_seconds for execution in dependencies.executions
            ),
        }.items():
            increment_eval_metric(name, value)
        set_eval_attribute("run_id", run_id)
        set_eval_attribute("case_id", case.case_id)
        set_eval_attribute("split", case.split)
        set_eval_attribute("failing_api", case.failing_api)
        set_eval_attribute("api_catalog_mode", api_catalog_mode)
        set_eval_attribute("static_preflight_mode", static_preflight_mode)
        set_eval_attribute("repetition", repetition)
        return ApiRepairObservation(
            run_id=run_id,
            case_id=case.case_id,
            outcome=result.outcome,
            verification=result.verification,
            delivered_note_ids=delivered_note_ids,
            abaqus_runtime_seconds=sum(
                execution.duration_seconds for execution in dependencies.executions
            ),
            failed_cae_calls=failed_cae_calls,
            static_preflight_mode=static_preflight_mode,
            static_diagnostic_count=static_diagnostic_count,
            static_unavailable_count=static_unavailable_count,
        )

    dataset = build_dataset(cases)
    report = await dataset.evaluate(
        run_case,
        name=(
            f"{model_gateway.value}-{model_name}-abaqus2024-{PROMPT_VERSION}-"
            f"{api_catalog_mode}-{static_preflight_mode}-r{repetition}"
        ),
        max_concurrency=1,
        retry_task=None,
        retry_evaluators=None,
        metadata=experiment_metadata,
    )
    gate = gate_report(report)
    _write_evaluation_artifacts(run_root, report, gate)
    report.print(include_output=True, include_reasons=True)
    return gate


def _task_instructions(case: ApiRepairCaseSpec) -> str:
    return f"""
This is an existing-script repair task; no physical user input is missing, so do
not return needs_input. Inspect and minimally edit `{case.entry_script}` rather
than replacing it with an INP file or another API dialect. Preserve the stated
modeling intent. Run it only with `run_cae_script`; do not call the Solver or
standalone Abaqus Python. The repair is complete only when a successful CAE run
creates `{case.result_file}` and you have inspected that JSON. Do not create or
edit the result file through the generic shell. Then return
ready_for_verification. If evidence is insufficient within budget, return failed.
""".strip()


def _create_case_workspace(
    run_root: Path,
    run_id: str,
    project_root: Path,
    case: ApiRepairCaseSpec,
) -> ResolvedWorkspace:
    run_dir = run_root / run_id
    workspace = run_dir / "workspace"
    state_dir = run_dir / "state"
    workspace.mkdir(parents=True, exist_ok=False)
    resolver = WorkspaceResolver(state_root=run_root)
    empty = resolver.resolve_eval(
        run_id=run_id,
        workspace=workspace,
        state_dir=state_dir,
    )
    fixture_root = (project_root / case.fixture_dir).resolve(strict=True)
    for relative in case.agent_visible_files:
        source = (fixture_root / relative).resolve(strict=True)
        if not source.is_file() or not source.is_relative_to(fixture_root):
            raise ValueError(f"invalid fixture file for {case.case_id}: {relative}")
        destination = empty.workspace / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
    return ResolvedWorkspace(
        workspace=empty.workspace,
        state_dir=empty.state_dir,
        initial_snapshot=resolver.snapshot(empty.workspace),
    )


def _validate_case_fixtures(
    project_root: Path,
    cases: tuple[ApiRepairCaseSpec, ...],
) -> None:
    for case in cases:
        fixture_root = (project_root / case.fixture_dir).resolve(strict=True)
        entry = (fixture_root / case.entry_script).resolve(strict=True)
        if not entry.is_file() or not entry.is_relative_to(fixture_root):
            raise ValueError(f"entry script is missing for {case.case_id}")
        if _sha256(entry) != case.fixture_sha256:
            raise ValueError(f"fixture digest mismatch for {case.case_id}")


def _load_api_catalog(
    run_root: Path,
    mode: ApiCatalogMode,
    api_catalog_dir: Path | None,
) -> ApiCatalog | None:
    if mode == "off":
        if api_catalog_dir is not None:
            raise ValueError("off mode must not receive an API catalog")
        return None
    if api_catalog_dir is None:
        raise ValueError(f"{mode} mode requires --api-catalog-dir")
    catalog_path = api_catalog_dir.resolve(strict=True)
    resolved_run_root = run_root.resolve()
    if catalog_path == resolved_run_root or catalog_path.is_relative_to(
        resolved_run_root
    ):
        raise ValueError("API catalog must be outside the evaluation run root")
    return ApiCatalog.open(catalog_path)


def _prepare_static_source(
    mode: StaticPreflightMode,
    wheel: Path | None,
    cache_dir: Path | None,
) -> PreparedAbqpySource | None:
    if mode == "off":
        if wheel is not None or cache_dir is not None:
            raise ValueError("off static preflight must not receive source paths")
        return None
    if mode != "on":
        raise ValueError(f"unknown static preflight mode: {mode}")
    if wheel is None or cache_dir is None:
        raise ValueError("on static preflight requires an abqpy wheel and cache dir")
    return prepare_abqpy_source(wheel, cache_dir)


def _static_feedback_counts(state_dir: Path) -> tuple[int, int]:
    path = state_dir / "static_preflight.jsonl"
    if not path.is_file():
        return 0, 0
    diagnostics = 0
    unavailable = 0
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        payload = json.loads(line)
        if payload.get("status") in {"timeout", "unavailable"}:
            unavailable += 1
        values = payload.get("diagnostics")
        if isinstance(values, list):
            diagnostics += len(values)
    return diagnostics, unavailable


def _api_catalog_binding(catalog: ApiCatalog | None) -> ApiCatalogBinding | None:
    if catalog is None:
        return None
    manifest = catalog.manifest
    if manifest is None:
        raise ValueError("persisted API catalog is missing its manifest")
    return ApiCatalogBinding(
        schema_version=manifest.schema_version,
        cards_sha256=manifest.cards_sha256,
        card_count=manifest.card_count,
        member_count=manifest.member_count,
        members_sha256=manifest.members_sha256,
        runtime_release=manifest.runtime_release,
    )


def _delivered_note_ids(state_dir: Path) -> tuple[str, ...]:
    path = state_dir / "api_lookups.jsonl"
    if not path.is_file():
        return ()
    note_ids: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        trace = ApiLookupTrace.model_validate_json(line)
        note_ids.extend(trace.delivered_note_ids)
    return tuple(dict.fromkeys(note_ids))


def _write_evaluation_artifacts(
    run_root: Path,
    report: EvaluationReport[
        ApiRepairEvalInput,
        ApiRepairObservation,
        ApiRepairEvalMetadata,
    ],
    gate: ApiRepairEvalGate,
) -> None:
    run_root.mkdir(parents=True, exist_ok=True)
    (run_root / "evaluation_report.txt").write_text(
        report.render(include_output=True, include_reasons=True),
        encoding="utf-8",
    )
    summary = {
        "name": report.name,
        "experiment_metadata": report.experiment_metadata,
        **gate.model_dump(mode="json"),
        "cases": [
            {
                "name": case.name,
                "api_repair_pass": (
                    case.assertions["api_repair_pass"].value
                    if "api_repair_pass" in case.assertions
                    else False
                ),
                "metrics": case.metrics,
                "labels": {key: result.value for key, result in case.labels.items()},
                "scores": {key: result.value for key, result in case.scores.items()},
                "task_duration": case.task_duration,
            }
            for case in report.cases
        ],
        "failures": [failure.name for failure in report.failures],
    }
    (run_root / "evaluation_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    (run_root / "gate.json").write_text(
        gate.model_dump_json(indent=2),
        encoding="utf-8",
    )


def _report_case_passed(case: object) -> int:
    assertions = getattr(case, "assertions", {})
    assertion = assertions.get("api_repair_pass")
    evaluator_failures = getattr(case, "evaluator_failures", ())
    return int(
        assertion is not None and assertion.value is True and not evaluator_failures
    )


def _label_value(case: object, name: str) -> object:
    labels = getattr(case, "labels", {})
    result = labels.get(name)
    return result.value if result is not None else None


def _relative_path(value: str) -> str:
    normalized = value.replace("\\", "/")
    path = PurePosixPath(normalized)
    windows_path = PureWindowsPath(normalized)
    if (
        not normalized
        or normalized == "."
        or path.is_absolute()
        or bool(windows_path.drive)
        or ".." in path.parts
    ):
        raise ValueError("case paths must be relative")
    return path.as_posix()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sha256_existing(path: Path) -> str:
    return _sha256(path.resolve(strict=True))


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run the Abaqus 2024 multi-API repair benchmark."
    )
    parser.add_argument("--run-root", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument(
        "--model-provider",
        type=DeepSeekGateway,
        choices=list(DeepSeekGateway),
        default=DeepSeekGateway.OFFICIAL,
    )
    parser.add_argument("--model", default=DEEPSEEK_V4_FLASH)
    parser.add_argument("--http-retries", type=int, default=DEFAULT_MODEL_HTTP_RETRIES)
    parser.add_argument(
        "--api-catalog-mode",
        choices=(
            "off",
            "full",
            "automatic_verified_only",
            "automatic_exact_signature",
        ),
        default="off",
    )
    parser.add_argument("--api-catalog-dir", type=Path)
    parser.add_argument("--static-preflight-mode", choices=("off", "on"), default="off")
    parser.add_argument("--abqpy-static-wheel", type=Path)
    parser.add_argument("--static-preflight-cache-dir", type=Path)
    parser.add_argument("--repetition", type=int, default=1)
    parser.add_argument(
        "--case-id",
        action="append",
        dest="case_ids",
        help="Run only this case; may be repeated",
    )
    args = parser.parse_args()
    gate = asyncio.run(
        run_evaluation(
            args.run_root.resolve(),
            manifest_path=args.manifest,
            model_gateway=args.model_provider,
            model_name=args.model,
            http_retries=args.http_retries,
            api_catalog_mode=args.api_catalog_mode,
            api_catalog_dir=args.api_catalog_dir,
            case_ids=tuple(args.case_ids) if args.case_ids else None,
            static_preflight_mode=args.static_preflight_mode,
            abqpy_static_wheel=args.abqpy_static_wheel,
            static_preflight_cache_dir=args.static_preflight_cache_dir,
            repetition=args.repetition,
        )
    )
    raise SystemExit(0 if gate.passed else 1)


if __name__ == "__main__":
    main()
