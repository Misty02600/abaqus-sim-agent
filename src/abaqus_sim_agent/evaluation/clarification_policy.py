from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
from collections.abc import Sequence
from datetime import UTC, datetime
from importlib.metadata import version
from pathlib import Path
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator
from pydantic_ai import ModelMessagesTypeAdapter
from pydantic_ai.messages import ModelResponse, ToolCallPart

from abaqus_sim_agent.coding.agent import create_abaqus_coding_agent
from abaqus_sim_agent.coding.clarification import ScriptedClarificationProvider
from abaqus_sim_agent.coding.contracts import (
    CLARIFICATION_POLICY_VERSION,
    ClarificationKind,
    CodingRunBudget,
    CodingRunRequest,
    ReadyForVerificationDecision,
    RuntimeProfile,
    RuntimeScriptPolicy,
)
from abaqus_sim_agent.coding.dependencies import CodingAgentDependencies
from abaqus_sim_agent.coding.harness_compat import (
    apply_harness_windows_newline_hash_patch,
    apply_harness_windows_shell_tree_patch,
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
from abaqus_sim_agent.coding.verification_port import VerificationAttempt
from abaqus_sim_agent.coding.workspace import (
    ResolvedWorkspace,
    WorkspaceResolver,
    WorkspaceSnapshot,
)
from abaqus_sim_agent.execution import (
    DEFAULT_ABAQUS_2024_LAUNCHER,
    Abaqus2024Runner,
    AbaqusExecutionResult,
)
from abaqus_sim_agent.skills import skill_library_path

DEFAULT_CASES_PATH = (
    Path(__file__).parents[3]
    / "benchmarks"
    / "coding"
    / "plate_clarification_policy_cases.jsonl"
)
EVALUATOR_VERSION = "clarification-policy-evaluator-v2"
ASSESSMENT_TASK_INSTRUCTIONS = """
Apply the request's own physical and artifact requirements. This run assesses
whether the request is blocked by missing or conflicting engineering intent.
Do not introduce a required field that the request does not imply.
""".strip()

_FORBIDDEN_TOPIC_TERMS: dict[str, tuple[str, ...]] = {
    "element_code": (
        "cps4r",
        "cpe4r",
        "which element code",
        "which element type",
        "choose an element code",
        "choose an element type",
        "请选择单元代码",
        "请选择单元类型",
        "选择哪种单元",
        "使用哪种单元",
    ),
    "abaqus_api": (
        "abaqus api",
        "method signature",
        "findat",
        "assembly.set",
        "part.set",
        "方法签名",
        "接口签名",
    ),
}


class _FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ClarificationPolicyCase(_FrozenModel):
    case_id: str = Field(min_length=1)
    policy_version: Literal["physics-materiality-clarification-v1"]
    goal: str = Field(min_length=1)
    expected_decision: Literal["proceed", "needs_input"]
    expected_kind: ClarificationKind | None
    expected_field_keywords: tuple[str, ...] = ()
    expected_element_type: str | None
    allowed_element_types: tuple[str, ...] = ()
    forbidden_question_topics: tuple[str, ...] = ()

    @model_validator(mode="after")
    def check_expected_clarification(self) -> ClarificationPolicyCase:
        if self.expected_decision == "needs_input":
            if self.expected_kind is None or not self.expected_field_keywords:
                raise ValueError(
                    "needs_input case requires expected kind and field keywords"
                )
        elif self.expected_kind is not None or self.expected_field_keywords:
            raise ValueError("proceed case must not declare a clarification")
        unknown_topics = set(self.forbidden_question_topics).difference(
            _FORBIDDEN_TOPIC_TERMS
        )
        if unknown_topics:
            raise ValueError(
                f"unknown forbidden question topics: {sorted(unknown_topics)}"
            )
        return self


class ClarificationPolicyAttempt(_FrozenModel):
    run_id: str
    case_id: str
    passed: bool
    actual_decision: str = "unavailable"
    actual_kind: str | None = None
    actual_field_id: str | None = None
    question: str | None = None
    impact: str | None = None
    pre_decision_tool_names: tuple[str, ...] = ()
    workspace_files: tuple[str, ...] = ()
    outcome_status: str | None = None
    failure_stage: str | None = None
    model_requests: int = Field(default=0, ge=0)
    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    error: str | None = None


class _AssessmentVerifier:
    async def verify(
        self,
        request: CodingRunRequest,
        runner: Abaqus2024Runner,
        initial_snapshot: WorkspaceSnapshot,
        executions: Sequence[AbaqusExecutionResult],
        decision: ReadyForVerificationDecision | None = None,
    ) -> VerificationAttempt:
        del request, runner, initial_snapshot, executions, decision
        return VerificationAttempt(
            passed=True,
            feedback="Assessment-only verifier accepted the non-clarification path.",
        )


def load_cases(path: Path = DEFAULT_CASES_PATH) -> tuple[ClarificationPolicyCase, ...]:
    cases = tuple(
        ClarificationPolicyCase.model_validate_json(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    )
    if not cases:
        raise ValueError(f"clarification policy case file is empty: {path}")
    if len({case.case_id for case in cases}) != len(cases):
        raise ValueError("clarification policy case ids must be unique")
    return cases


async def run_evaluation(
    run_root: Path,
    *,
    model_gateway: DeepSeekGateway = DeepSeekGateway.OPENCODE_GO,
    model_name: str = DEEPSEEK_V4_FLASH,
    http_retries: int = DEFAULT_MODEL_HTTP_RETRIES,
    repeat: int = 3,
    case_ids: tuple[str, ...] = (),
    cases_path: Path = DEFAULT_CASES_PATH,
) -> bool:
    if repeat < 1:
        raise ValueError("repeat must be positive")
    all_cases = load_cases(cases_path)
    selected = tuple(
        case
        for case in all_cases
        if case.expected_decision == "needs_input"
        and (not case_ids or case.case_id in case_ids)
    )
    if not selected:
        raise ValueError("no needs_input clarification cases were selected")
    missing_ids = set(case_ids).difference(case.case_id for case in selected)
    if missing_ids:
        raise ValueError(
            f"unknown or non-clarification case ids: {sorted(missing_ids)}"
        )

    await asyncio.to_thread(run_root.mkdir, parents=True, exist_ok=False)
    api_key = take_gateway_api_key_from_environment(model_gateway)
    metadata = {
        "schema_version": "1",
        "evaluator_version": EVALUATOR_VERSION,
        "policy_version": CLARIFICATION_POLICY_VERSION,
        "created_at": datetime.now(UTC).isoformat(),
        "model_gateway": model_gateway.value,
        "model": model_name,
        "base_url": gateway_base_url(model_gateway),
        "http_retries": http_retries,
        "http_timeout_seconds": DEFAULT_MODEL_HTTP_TIMEOUT_SECONDS,
        "repeat": repeat,
        "api_catalog_mode": "off",
        "concept_retrieval_mode": "off",
        "planning_enabled": False,
        "runtime_execution_allowed": False,
        "pydantic_ai_version": version("pydantic-ai-slim"),
        "pydantic_ai_harness_version": version("pydantic-ai-harness"),
        "harness_windows_newline_hash_patch": (
            apply_harness_windows_newline_hash_patch()
        ),
        "harness_windows_shell_tree_patch": apply_harness_windows_shell_tree_patch(),
    }
    await asyncio.to_thread(
        _write_json,
        run_root / "evaluation_metadata.json",
        metadata,
    )

    attempts: list[ClarificationPolicyAttempt] = []
    for case in selected:
        for index in range(1, repeat + 1):
            attempt = await _run_attempt(
                run_root,
                case,
                index=index,
                model_gateway=model_gateway,
                model_name=model_name,
                api_key=api_key,
                http_retries=http_retries,
            )
            attempts.append(attempt)
            print(
                json.dumps(
                    attempt.model_dump(mode="json"),
                    ensure_ascii=True,
                    sort_keys=True,
                )
            )

    case_summaries = []
    required_passes = min(2, repeat)
    for case in selected:
        case_attempts = [
            attempt for attempt in attempts if attempt.case_id == case.case_id
        ]
        passes = sum(attempt.passed for attempt in case_attempts)
        case_summaries.append(
            {
                "case_id": case.case_id,
                "attempts": len(case_attempts),
                "passes": passes,
                "required_passes": required_passes,
                "passed": len(case_attempts) == repeat and passes >= required_passes,
            }
        )
    passed = all(summary["passed"] for summary in case_summaries)
    summary = {
        "metadata": metadata,
        "passed": passed,
        "cases": case_summaries,
        "attempts": [attempt.model_dump(mode="json") for attempt in attempts],
    }
    await asyncio.to_thread(
        _write_json,
        run_root / "evaluation_summary.json",
        summary,
    )
    return passed


async def _run_attempt(
    run_root: Path,
    case: ClarificationPolicyCase,
    *,
    index: int,
    model_gateway: DeepSeekGateway,
    model_name: str,
    api_key: SecretStr,
    http_retries: int,
) -> ClarificationPolicyAttempt:
    run_id = f"{case.case_id}-{index}-{uuid4().hex[:8]}"
    resolved = await asyncio.to_thread(
        _create_assessment_workspace,
        run_root,
        run_id,
    )
    request = CodingRunRequest(
        run_id=run_id,
        goal=case.goal,
        workspace=resolved.workspace,
        state_dir=resolved.state_dir,
        runtime=RuntimeProfile(launcher_path=DEFAULT_ABAQUS_2024_LAUNCHER.resolve()),
        runtime_script_policy=RuntimeScriptPolicy(
            allowed_cae_scripts=(),
            allowed_abaqus_python_scripts=(),
            allowed_solver_inputs=(),
        ),
        budget=CodingRunBudget(
            max_clarifications=1,
            max_model_requests=8,
            max_api_lookup_calls=0,
            max_concept_lookup_calls=0,
            max_static_preflight_calls=0,
            max_abaqus_calls=0,
            max_solver_calls=0,
            wall_time_seconds=300.0,
        ),
    )
    runner = Abaqus2024Runner(request.workspace, request.state_dir)
    model, client = create_deepseek_chat_model(
        gateway=model_gateway,
        model_name=model_name,
        api_key=api_key,
        http_retries=http_retries,
        conversation_id=request.run_id
        if model_gateway is DeepSeekGateway.OPENCODE_GO
        else None,
    )
    error: str | None = None
    result = None
    try:
        dependencies = CodingAgentDependencies(request=request, runner=runner)
        service = CodingRunService(
            agent=create_abaqus_coding_agent(
                model,
                request,
                skill_library=skill_library_path(),
                api_catalog_mode="off",
                concept_retrieval_mode="off",
                planning_enabled=False,
                task_instructions=ASSESSMENT_TASK_INSTRUCTIONS,
            ),
            request=request,
            dependencies=dependencies,
            clarification_provider=ScriptedClarificationProvider({}),
            verifier=_AssessmentVerifier(),
            initial_snapshot=resolved.initial_snapshot,
        )
        result = await service.run()
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
    finally:
        await client.close()

    decision = _first_agent_decision(request.state_dir / "events.jsonl")
    pre_tools = _tools_before_first_decision(request.state_dir / "history.json")
    workspace_files = await asyncio.to_thread(
        _workspace_files,
        request.workspace,
    )
    passed = _decision_matches(case, decision, pre_tools)
    clarification = decision.get("clarification") if decision is not None else None
    clarification = clarification if isinstance(clarification, dict) else {}
    outcome = result.outcome if result is not None else None
    return ClarificationPolicyAttempt(
        run_id=run_id,
        case_id=case.case_id,
        passed=passed,
        actual_decision=(
            str(decision.get("status", "unavailable"))
            if decision is not None
            else "unavailable"
        ),
        actual_kind=_optional_string(clarification.get("kind")),
        actual_field_id=_optional_string(clarification.get("field_id")),
        question=_optional_string(clarification.get("question")),
        impact=_optional_string(clarification.get("impact")),
        pre_decision_tool_names=pre_tools,
        workspace_files=workspace_files,
        outcome_status=str(outcome.status) if outcome is not None else None,
        failure_stage=outcome.failure_stage if outcome is not None else None,
        model_requests=outcome.usage.model_requests if outcome is not None else 0,
        input_tokens=outcome.usage.input_tokens if outcome is not None else 0,
        output_tokens=outcome.usage.output_tokens if outcome is not None else 0,
        error=error,
    )


def _first_agent_decision(path: Path) -> dict[str, object] | None:
    if not path.is_file():
        return None
    for line in path.read_text(encoding="utf-8").splitlines():
        event = json.loads(line)
        if event.get("event") == "agent_decision" and isinstance(
            event.get("data"), dict
        ):
            return event["data"]
    return None


def _tools_before_first_decision(path: Path) -> tuple[str, ...]:
    if not path.is_file():
        return ()
    messages = ModelMessagesTypeAdapter.validate_json(path.read_bytes())
    tool_names: list[str] = []
    for message in messages:
        if not isinstance(message, ModelResponse):
            continue
        for part in message.parts:
            if not isinstance(part, ToolCallPart):
                continue
            if part.tool_name.startswith("final_result_"):
                return tuple(tool_names)
            tool_names.append(part.tool_name)
    return tuple(tool_names)


def _decision_matches(
    case: ClarificationPolicyCase,
    decision: dict[str, object] | None,
    pre_tools: tuple[str, ...],
) -> bool:
    if decision is None or decision.get("status") != case.expected_decision:
        return False
    clarification = decision.get("clarification")
    if not isinstance(clarification, dict):
        return False
    question = _optional_string(clarification.get("question")) or ""
    impact = _optional_string(clarification.get("impact")) or ""
    forbidden_terms = tuple(
        term
        for topic in case.forbidden_question_topics
        for term in _FORBIDDEN_TOPIC_TERMS[topic]
    )
    return (
        clarification.get("kind") == case.expected_kind
        and any(
            keyword in str(clarification.get("field_id", ""))
            for keyword in case.expected_field_keywords
        )
        and bool(question.strip())
        and bool(impact.strip())
        and not pre_tools
        and not any(term.casefold() in question.casefold() for term in forbidden_terms)
    )


def _optional_string(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _create_assessment_workspace(
    run_root: Path,
    run_id: str,
) -> ResolvedWorkspace:
    run_dir = run_root / run_id
    workspace = (run_dir / "workspace").resolve()
    state_dir = (run_dir / "state").resolve()
    workspace.mkdir(parents=True)
    return WorkspaceResolver(state_root=run_root).resolve_eval(
        run_id=run_id,
        workspace=workspace,
        state_dir=state_dir,
    )


def _workspace_files(workspace: Path) -> tuple[str, ...]:
    return tuple(
        sorted(
            path.relative_to(workspace).as_posix()
            for path in workspace.rglob("*")
            if path.is_file()
        )
    )


def _write_json(path: Path, value: object) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )


def replay_evaluation(
    run_root: Path,
    *,
    cases_path: Path = DEFAULT_CASES_PATH,
) -> bool:
    source_path = run_root / "evaluation_summary.json"
    source_bytes = source_path.read_bytes()
    source = json.loads(source_bytes)
    cases = {case.case_id: case for case in load_cases(cases_path)}
    replayed_attempts = []
    for attempt in source["attempts"]:
        case = cases[attempt["case_id"]]
        decision = {
            "status": attempt["actual_decision"],
            "clarification": {
                "field_id": attempt["actual_field_id"],
                "kind": attempt["actual_kind"],
                "question": attempt["question"],
                "impact": attempt["impact"],
            },
        }
        passed = _decision_matches(
            case,
            decision,
            tuple(attempt["pre_decision_tool_names"]),
        )
        replayed_attempts.append({**attempt, "passed": passed})

    case_summaries = []
    for source_case in source["cases"]:
        case_attempts = [
            attempt
            for attempt in replayed_attempts
            if attempt["case_id"] == source_case["case_id"]
        ]
        passes = sum(bool(attempt["passed"]) for attempt in case_attempts)
        required_passes = source_case["required_passes"]
        case_summaries.append(
            {
                "case_id": source_case["case_id"],
                "attempts": len(case_attempts),
                "passes": passes,
                "required_passes": required_passes,
                "passed": passes >= required_passes,
            }
        )
    passed = all(case["passed"] for case in case_summaries)
    replay = {
        "schema_version": "1",
        "evaluator_version": EVALUATOR_VERSION,
        "policy_version": CLARIFICATION_POLICY_VERSION,
        "source_summary": source_path.name,
        "source_summary_sha256": hashlib.sha256(source_bytes).hexdigest(),
        "passed": passed,
        "cases": case_summaries,
        "attempts": replayed_attempts,
    }
    _write_json(
        run_root / f"evaluation_replay_{EVALUATOR_VERSION}.json",
        replay,
    )
    return passed


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Evaluate needs_input decisions without allowing Abaqus runtime."
    )
    parser.add_argument("--run-root", type=Path, required=True)
    parser.add_argument(
        "--model-provider",
        type=DeepSeekGateway,
        choices=list(DeepSeekGateway),
        default=DeepSeekGateway.OPENCODE_GO,
    )
    parser.add_argument("--model", default=DEEPSEEK_V4_FLASH)
    parser.add_argument("--http-retries", type=int, default=DEFAULT_MODEL_HTTP_RETRIES)
    parser.add_argument("--repeat", type=int, default=3)
    parser.add_argument("--case-id", action="append", default=[])
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES_PATH)
    parser.add_argument(
        "--replay-existing",
        action="store_true",
        help="Re-score an existing evaluation_summary.json without model calls.",
    )
    args = parser.parse_args()
    if args.replay_existing:
        passed = replay_evaluation(
            args.run_root.resolve(),
            cases_path=args.cases.resolve(),
        )
        raise SystemExit(0 if passed else 1)
    passed = asyncio.run(
        run_evaluation(
            args.run_root.resolve(),
            model_gateway=args.model_provider,
            model_name=args.model,
            http_retries=args.http_retries,
            repeat=args.repeat,
            case_ids=tuple(args.case_id),
            cases_path=args.cases.resolve(),
        )
    )
    raise SystemExit(0 if passed else 1)


if __name__ == "__main__":
    main()
