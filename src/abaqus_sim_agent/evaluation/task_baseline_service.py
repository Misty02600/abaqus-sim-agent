"""任务卡的离线接线与显式获批试点；外围评价不改写产品终态。"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import time
from collections.abc import Callable
from pathlib import Path
from typing import Literal

from pydantic import SecretStr, ValidationError
from pydantic_ai.exceptions import UnexpectedModelBehavior
from pydantic_ai.models import Model
from pydantic_ai.models.function import FunctionModel

from abaqus_sim_agent.coding.contracts import CodingRunRequest, RuntimeProfile
from abaqus_sim_agent.coding.deferred_runtime import WaitingForToolExecution
from abaqus_sim_agent.coding.dependencies import CodingAgentDependencies
from abaqus_sim_agent.coding.execution_verifier import ExecutionEvidenceVerifier
from abaqus_sim_agent.coding.launch import LaunchManifest
from abaqus_sim_agent.coding.model import (
    ModelExecutionConfig,
    create_deepseek_chat_model,
)
from abaqus_sim_agent.coding.persistence import write_bytes_atomic, write_text_atomic
from abaqus_sim_agent.coding.resume import (
    create_step_store,
    ensure_new_run_state_available,
    load_resume_state,
)
from abaqus_sim_agent.coding.run_lock import CodingRunFileLock
from abaqus_sim_agent.coding.service import (
    CodingRunInfrastructureError,
    CodingRunResult,
    CodingRunService,
)
from abaqus_sim_agent.coding.user_questions import (
    QuestionProtocol,
    UserAnswerInput,
    WaitingForUser,
    load_questions,
    submit_answer,
)
from abaqus_sim_agent.coding.workspace import (
    WorkspaceResolver,
    WorkspaceSnapshot,
    artifact_evidence_from_snapshots,
)
from abaqus_sim_agent.evaluation.response_diagnostic import (
    response_diagnostic,
    started_request_count,
)
from abaqus_sim_agent.evaluation.serial_runtime import SerialRuntime
from abaqus_sim_agent.evaluation.task_baseline import (
    CheckState,
    PreparedTask,
    TaskTrialReview,
    ThicknessClarificationFixture,
    baseline_execution_config,
    compose_baseline_agent,
    planned_slots,
    simple_slots,
)
from abaqus_sim_agent.evaluation.task_baseline_runner import TrialContext
from abaqus_sim_agent.execution import Abaqus2024Runner
from abaqus_sim_agent.execution.abaqus2024 import (
    AbaqusInvocation,
    ProcessStrategy,
    WindowsProcessStrategy,
)
from abaqus_sim_agent.skills import skill_library_path
from abaqus_sim_agent.verification.model_change_evidence import (
    model_change_execution_provenance,
    status_completed,
)
from abaqus_sim_agent.verification.plate_output_warning_repair_run import (
    _has_cae_solver_lineage,
    _has_target_warning,
)


def _prepare(
    context: TrialContext,
    prepared: PreparedTask,
    *,
    evidence_kind: Literal["fixture", "live"] = "fixture",
    runtime: RuntimeProfile | None = None,
) -> tuple[CodingRunRequest, WorkspaceSnapshot]:
    """在独立state锁下复制缓存输入，再捕获本次freshness基线。

    Note:
        非空工作区或已有CodingRun均拒绝覆盖；这里不执行seed脚本。
    """
    candidate = prepared.candidate
    if (
        context.slot not in (*planned_slots(), *simple_slots())
        or context.slot.split("/")[0] != candidate.task_id
    ):
        raise ValueError("prepared task does not match this slot")
    expected_files = (
        {"requirements.md"}
        if candidate.seed_kind == "none"
        else {"build_model.py", "extract_results.py"}
    )
    if set(prepared.workspace_files) != expected_files:
        raise ValueError("task seed must contain only the declared initial files")
    if any(not isinstance(data, bytes) for data in prepared.workspace_files.values()):
        raise ValueError("initial file cache must contain bytes")
    state = context.state_dir / "coding-run"
    run_id = (
        f"hybrid-{evidence_kind}-"
        + hashlib.sha256(str(context.workspace).encode()).hexdigest()[:16]
    )
    with CodingRunFileLock(state):
        ensure_new_run_state_available(state)
        resolved = WorkspaceResolver().resolve_eval(
            run_id=run_id, workspace=context.workspace, state_dir=state
        )
        for relative, data in prepared.workspace_files.items():
            write_bytes_atomic(resolved.workspace / relative, data)
        snapshot = WorkspaceResolver().snapshot(resolved.workspace)
        request = CodingRunRequest(
            run_id=run_id,
            goal=candidate.service_goal,
            workspace=resolved.workspace,
            state_dir=resolved.state_dir,
            runtime=runtime
            or RuntimeProfile(
                launcher_path=(
                    resolved.state_dir / "fixture-only-never-launch.bat"
                ).resolve()
            ),
            budget=candidate.candidate_budget,
        )
        binding = {
            "evidence_kind": evidence_kind,
            "service": "CodingRunService",
            "model": "FunctionModel"
            if evidence_kind == "fixture"
            else "configured Go model factory",
            "process": "fake ProcessStrategy"
            if evidence_kind == "fixture"
            else "WindowsProcessStrategy",
            "candidate": candidate.model_dump(mode="json"),
            "new_deliverables": candidate.new_deliverables,
            "preserved_inputs": candidate.preserved_inputs,
            "initial_snapshot": snapshot.model_dump(mode="json"),
            "seed_provenance": prepared.seed_provenance.model_dump(mode="json")
            if prepared.seed_provenance
            else None,
            "source_qualification": "fixture wiring only; not licensed evidence"
            if evidence_kind == "fixture"
            else "T1 from requirements; no model seed",
        }
        write_text_atomic(
            context.state_dir / "service-binding.json",
            json.dumps(binding, ensure_ascii=False, indent=2),
        )
    return request, snapshot


def _cause(error: BaseException) -> tuple[str, ...]:
    names = []
    seen = set()
    current: BaseException | None = error
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        names.append(type(current).__name__)
        current = current.__cause__ or current.__context__
    return tuple(names)


def _tool_argument_failure(error: BaseException) -> dict[str, object] | None:
    """仅识别框架的工具重试耗尽且直接原因为参数ValidationError的分支。"""
    seen = set()
    current: BaseException | None = error
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, UnexpectedModelBehavior) and isinstance(
            current.__cause__, ValidationError
        ):
            match = re.match(
                r"Tool '([A-Za-z0-9_]{1,100})' exceeded max retries count of ([0-9]+)\.",
                str(current),
            )
            if match:
                return {
                    "kind": "tool_argument_validation_exhausted",
                    "tool_name": match[1],
                    "retry_limit": int(match[2]),
                    "upstream_responsibility": "unknown",
                }
        current = current.__cause__ or current.__context__
    return None


def _collect(
    context: TrialContext,
    prepared: PreparedTask,
    request: CodingRunRequest,
    initial: WorkspaceSnapshot,
    deps: CodingAgentDependencies,
    clarification: ThicknessClarificationFixture,
    result: CodingRunResult | None,
    error: BaseException | None,
    *,
    evidence_kind: Literal["fixture", "live"] = "fixture",
    http_attempts: int = 0,
) -> TaskTrialReview:
    """读取服务终态并生成外围评测记录，不改变原outcome或调用物理oracle。

    Note:
        execution_evidence完成不保证任务交付完整。数值与权限语义保持待审，
        准备、运行及检查产生的原始证据由返回路径绑定；只写外围检查和快照。
    """
    resume = load_resume_state(request.state_dir)
    candidate = prepared.candidate
    current = WorkspaceResolver().snapshot(request.workspace)
    _, freshness = artifact_evidence_from_snapshots(initial, current)
    initial_files, current_files = initial.by_path(), current.by_path()
    missing_or_stale = [
        p for p in candidate.new_deliverables if not freshness.get(p, False)
    ]
    changed_inputs = [
        p
        for p in candidate.preserved_inputs
        if current_files.get(p) != initial_files.get(p)
    ]
    provenance = model_change_execution_provenance(
        deps.executions, current, artifact_stem=candidate.artifact_stem
    )
    last_stages = {execution.invocation: execution for execution in deps.executions}
    solver_completed = status_completed(
        request.workspace / f"{candidate.artifact_stem}.sta"
    )
    stages_ok = (
        provenance.passed
        and solver_completed
        and all(e.stage_succeeded for e in last_stages.values())
    )
    repair_checks = {}
    if candidate.seed_kind == "defective_plate":
        solvers = [
            i
            for i, e in enumerate(deps.executions)
            if e.invocation is AbaqusInvocation.SOLVER_JOB
        ]
        baseline, final = (solvers[0], solvers[-1]) if solvers else (None, None)
        build = current_files.get("build_model.py")
        repair_checks = {
            "original_seed_warning_reproduced": baseline is not None
            and _has_target_warning(deps.executions[baseline])
            and _has_cae_solver_lineage(
                deps.executions,
                build_sha256=hashlib.sha256(
                    prepared.workspace_files["build_model.py"]
                ).hexdigest(),
                solver_index=baseline,
                lower_bound=-1,
            ),
            "changed_build_clean_solver": len(solvers) >= 2
            and baseline is not None
            and final is not None
            and build is not None
            and not _has_target_warning(deps.executions[final])
            and _has_cae_solver_lineage(
                deps.executions,
                build_sha256=build.sha256,
                solver_index=final,
                lower_bound=baseline,
            ),
            "extract_after_clean_solver": final is not None
            and any(
                i > final
                and e.invocation is AbaqusInvocation.ABAQUS_PYTHON
                and "extract_results.py" in e.command
                and e.stage_succeeded
                for i, e in enumerate(deps.executions)
            ),
        }
        stages_ok = stages_ok and all(repair_checks.values())
    manifest_paths = (
        getattr(result.verification, "declared_deliverable_paths", ())
        if result and result.verification
        else ()
    )
    terminal = result.outcome.status.value if result else resume.status
    if terminal not in (
        "completed",
        "failed",
        "interrupted",
        "needs_reconcile",
        "waiting_for_user",
    ):
        raise ValueError("service did not persist a settled result")
    classification = "none"
    error_types = _cause(error) if error else ()
    failure_diagnostic = _tool_argument_failure(error) if error else None
    if error:
        classification = (
            "provider"
            if any(
                t in error_types
                for t in (
                    "ConnectionError",
                    "ModelAPIError",
                    "APITimeoutError",
                    "APIConnectionError",
                )
            )
            else "unknown"
        )
        if failure_diagnostic is not None:
            classification = "tool_arguments"
    elif result and terminal == "failed":
        classification = (
            "budget"
            if result.outcome.failure_stage
            in ("timeout", "model_budget", "runtime_budget")
            else "task"
        )
        if any(o.classification == "adapter_gap" for o in clarification.observations):
            classification = "adapter_gap"
    interaction: CheckState = "unknown"
    if candidate.task_id == "missing-input-plate-v1":
        answered_first = (
            len(resume.clarifications) == 1
            and resume.clarifications[0].answer == "1 mm"
            and resume.clarifications[0].abaqus_calls_before_answer == 0
        )
        if answered_first:
            interaction = "pass"
        elif resume.dependencies.abaqus_calls > 0 or terminal == "completed":
            interaction = "fail"
    elif resume.clarifications or any(
        o.classification == "unexpected_question" for o in clarification.observations
    ):
        interaction = "fail"
    elif terminal == "completed":
        interaction = "pass"
    questions = (
        load_questions(request.state_dir, request.run_id)
        if resume.question_protocol
        else None
    )
    if questions is not None:
        if candidate.task_id == "missing-input-plate-v1":
            answered_first = (
                len(questions.questions) == 1
                and questions.questions[0].answer == "1 mm"
                and questions.questions[0].abaqus_calls_before == 0
            )
            interaction = (
                "pass"
                if answered_first
                else "fail"
                if resume.dependencies.abaqus_calls or terminal == "completed"
                else "unknown"
            )
        else:
            interaction = (
                "fail"
                if questions.questions
                else "pass"
                if terminal == "completed"
                else "unknown"
            )
    if terminal == "waiting_for_user" and any(
        o.classification == "adapter_gap" for o in clarification.observations
    ):
        classification = "adapter_gap"
    requests_started = None
    http_journal = context.state_dir / "http-attempts.json"
    if evidence_kind == "live" and http_journal.is_file():
        try:
            entries = json.loads(http_journal.read_bytes())
        except (OSError, ValueError):
            entries = None
        if (
            isinstance(entries, list)
            and len(entries) == http_attempts
            and all(isinstance(e, dict) for e in entries)
        ):
            requests_started = started_request_count(entries)
    manifest_ok = set(candidate.new_deliverables) <= set(manifest_paths)
    checks = {
        "evidence_kind": evidence_kind,
        "verification_scope": "execution_evidence",
        "missing_or_stale_deliverables": missing_or_stale,
        "changed_preserved_inputs": changed_inputs,
        "stage_artifact_evidence": provenance.model_dump(mode="json"),
        "latest_stages_passed": stages_ok,
        "declared_deliverables": manifest_paths,
        "solver_status_completed": solver_completed,
        "repair_execution_checks": repair_checks,
        "required_manifest_complete": manifest_ok,
        "clarification_observations": [
            o.model_dump(mode="json") for o in clarification.observations
        ],
        "error_types": error_types,
        "interaction_acceptance": interaction,
        "failure_diagnostic": failure_diagnostic,
        "physics_checked": False,
        "human_review_pending": True,
        "user_questions": questions.model_dump(mode="json") if questions else None,
        "question_wait_seconds": questions.wait_seconds if questions else 0.0,
        "runtime_queue_wait_seconds": (
            deps.runner._process_strategy.wait_seconds
            if isinstance(deps.runner._process_strategy, SerialRuntime)
            else 0.0
        ),
    }
    write_text_atomic(
        context.state_dir / "service-checks.json",
        json.dumps(checks, ensure_ascii=False, indent=2),
    )
    write_text_atomic(
        context.state_dir / "final-workspace-snapshot.json",
        current.model_dump_json(indent=2),
    )
    # Bind original service records and workspace bytes without rewriting any of them.
    references = [
        f"trials/{context.slot}/state/{path.relative_to(context.state_dir).as_posix()}"
        for path in sorted(context.state_dir.rglob("*"))
        if path.is_file()
        and path.name
        not in {"coding_run.lock", "trial-result.json", "interrupted-observation.json"}
    ]
    references += [
        f"trials/{context.slot}/workspace/{item.relative_path}"
        for item in current.files
    ]
    values = {
        "slot": context.slot,
        "terminal": terminal,
        "cause": classification,
        "observation_kind": "service_fixture"
        if evidence_kind == "fixture"
        else "live_service",
        "service_run_id": request.run_id,
        "service_verification_scope": result.outcome.verification_scope
        if result
        else None,
        "artifact_acceptance": "unknown"
        if terminal == "waiting_for_user" and not changed_inputs
        else "fail"
        if missing_or_stale or changed_inputs
        else "pass",
        "execution_acceptance": "unknown"
        if terminal == "waiting_for_user"
        else "pass"
        if stages_ok and (terminal != "completed" or manifest_ok)
        else "fail",
        "numeric_acceptance": "unknown",
        "interaction_acceptance": interaction,
        "authority_acceptance": "unknown",
        "evidence_references": references,
        "logical_requests": resume.usage.requests,
        "model_requests_started": requests_started,
        "http_attempts": http_attempts,
        "input_tokens": resume.usage.input_tokens,
        "output_tokens": resume.usage.output_tokens,
        "runtime_calls": resume.dependencies.abaqus_calls,
        "solver_calls": resume.dependencies.solver_calls,
        "host_oracle_calls": 0,
        "wall_seconds": resume.active_wall_time_seconds,
        "service_wall_seconds": resume.active_wall_time_seconds,
        "error_type": error_types[-1] if error_types else None,
    }
    return TaskTrialReview.model_validate_json(json.dumps(values))


async def run_service_fixture(
    context: TrialContext,
    prepared: PreparedTask,
    *,
    model: FunctionModel,
    process_strategy: ProcessStrategy,
) -> TaskTrialReview:
    """用假模型与显式假进程驱动真实单Coder服务，结果留在原batch槽位。

    Args:
        context: 离线runner已经分配的空工作区与独立state。
        prepared: 已准备的任务卡及初始文件缓存；每次创建独立澄清provider。
        model: 仅接受本地FunctionModel，不构造HTTP客户端或读取密钥。
        process_strategy: 必须显式注入测试实现，拒绝默认Windows进程执行器。

    Note:
        注入的Python函数/策略是可信测试代码，不构成网络或进程沙箱。
        不调用reference_verifier，不自动resume，不把运行后检查反馈回Agent。
    """
    if (
        not isinstance(model, FunctionModel)
        or not process_strategy
        or isinstance(process_strategy, WindowsProcessStrategy)
    ):
        raise ValueError(
            "service fixture requires FunctionModel and a non-Windows fake process strategy"
        )
    request, initial = await asyncio.to_thread(_prepare, context, prepared)
    config = baseline_execution_config()
    return await _execute_service(
        context,
        prepared,
        request,
        initial,
        model=model,
        process_strategy=process_strategy,
        config=config,
        evidence_kind="fixture",
        http_count=lambda: 0,
    )


async def _execute_service(
    context: TrialContext,
    prepared: PreparedTask,
    request: CodingRunRequest,
    initial: WorkspaceSnapshot,
    *,
    model: Model,
    process_strategy: ProcessStrategy | None,
    config: ModelExecutionConfig,
    evidence_kind: Literal["fixture", "live"],
    http_count: Callable[[], int],
    question_protocol: QuestionProtocol = "legacy_decision",
    answer_mode: Literal["scripted_fixture", "human_confirmed"] = "scripted_fixture",
) -> TaskTrialReview:
    if answer_mode == "human_confirmed" and question_protocol != "ask_user_v1":
        raise ValueError("human-confirmed evaluation requires ask_user_v1")
    step_store = create_step_store(request.state_dir)
    runner = Abaqus2024Runner(
        request.workspace,
        request.state_dir,
        launcher=request.runtime.launcher_path,
        process_strategy=process_strategy,
    )
    deps = CodingAgentDependencies(
        request=request, runner=runner, step_store=step_store
    )
    clarification = ThicknessClarificationFixture(
        enabled=prepared.candidate.task_id == "missing-input-plate-v1"
    )
    launch = LaunchManifest(
        schema_version="coding-run-launch-v4"
        if question_protocol == "ask_user_v1"
        else "coding-run-launch-v3",
        **(
            {"question_protocol": "ask_user_v1"}
            if question_protocol == "ask_user_v1"
            else {}
        ),
        skill_library=skill_library_path().resolve(),
        **config.model_dump(),
    )
    service = CodingRunService(
        agent=compose_baseline_agent(
            model,
            request,
            configuration=config,
            immutable_workspace_paths=prepared.candidate.immutable_paths,
            step_store=step_store,
            question_protocol=question_protocol,
        ),
        request=request,
        dependencies=deps,
        clarification_provider=clarification,
        verifier=ExecutionEvidenceVerifier(),
        initial_snapshot=initial,
        step_store=step_store,
        verify_on_budget_exhaustion=False,
        resume=False,
        launch_manifest_json=launch.model_dump_json(indent=2),
        question_protocol=question_protocol,
    )
    try:
        result = (
            await service.run_step()
            if question_protocol == "ask_user_v1"
            else await service.run()
        )
        while isinstance(result, WaitingForUser):
            if answer_mode == "human_confirmed" or result.question.revision_had_runtime:
                # Human confirmation uses the existing question-ID answer interface
                # after this trial settles; never infer intent from question wording.
                return _collect(
                    context,
                    prepared,
                    request,
                    initial,
                    deps,
                    clarification,
                    None,
                    None,
                    evidence_kind=evidence_kind,
                    http_attempts=http_count(),
                )
            answer = clarification.get_question_answer(result.question.question)
            if answer is None:
                return _collect(
                    context,
                    prepared,
                    request,
                    initial,
                    deps,
                    clarification,
                    None,
                    None,
                    evidence_kind=evidence_kind,
                    http_attempts=http_count(),
                )
            await submit_answer(
                request.state_dir,
                UserAnswerInput(question_id=result.question.question_id, answer=answer),
                source="scripted",
            )
            service.resume = True
            result = await service.run_step()
        if isinstance(result, WaitingForToolExecution):
            raise CodingRunInfrastructureError(
                "deferred Solver execution requires the Web controller"
            )
    except asyncio.CancelledError as exc:
        observation = _collect(
            context,
            prepared,
            request,
            initial,
            deps,
            clarification,
            None,
            exc,
            evidence_kind=evidence_kind,
            http_attempts=http_count(),
        )
        write_text_atomic(
            context.state_dir / "interrupted-observation.json",
            observation.model_dump_json(indent=2, exclude_computed_fields=True),
        )
        raise
    except CodingRunInfrastructureError as exc:
        return _collect(
            context,
            prepared,
            request,
            initial,
            deps,
            clarification,
            None,
            exc,
            evidence_kind=evidence_kind,
            http_attempts=http_count(),
        )
    return _collect(
        context,
        prepared,
        request,
        initial,
        deps,
        clarification,
        result,
        None,
        evidence_kind=evidence_kind,
        http_attempts=http_count(),
    )


async def run_service_live(
    context: TrialContext,
    prepared: PreparedTask,
    *,
    launcher: Path,
    api_key: SecretStr,
    config: ModelExecutionConfig,
    runtime_gate: asyncio.Semaphore | None = None,
    question_protocol: QuestionProtocol = "legacy_decision",
    answer_mode: Literal["scripted_fixture", "human_confirmed"] = "scripted_fixture",
) -> TaskTrialReview:
    """仅供已获批准的T1或固定双任务调用；复用产品模型工厂与同一服务循环。

    Note:
        调用方必须在此之前校验试点spec/授权摘要。本函数不切换provider、不自动resume，
        只记录无凭据的HTTP尝试元数据，关闭客户端后不再发送请求。
    """
    if answer_mode == "human_confirmed" and question_protocol != "ask_user_v1":
        raise ValueError("human-confirmed evaluation requires ask_user_v1")
    if (
        prepared.candidate.task_id != "requirements-plate-v1"
        and (context.slot not in simple_slots() or runtime_gate is None)
    ) or config != baseline_execution_config():
        raise ValueError("live task must match T1 or a gated simple modeling slot")
    runtime = RuntimeProfile(
        launcher_path=await asyncio.to_thread(launcher.resolve, strict=True)
    )
    request, initial = await asyncio.to_thread(
        _prepare, context, prepared, evidence_kind="live", runtime=runtime
    )
    attempts = []

    def persist():
        write_text_atomic(
            context.state_dir / "http-attempts.json", json.dumps(attempts, indent=2)
        )

    async def on_request(http_request):
        body = json.loads(http_request.content)
        retry_header = http_request.headers.get("x-stainless-retry-count", "")
        retry_count = (
            int(retry_header)
            if retry_header.isascii()
            and retry_header.isdecimal()
            and len(retry_header) <= 6
            else None
        )
        entry = {
            "attempt": len(attempts) + 1,
            "sdk_retry_count": retry_count,
            "started": time.time(),
            "path": http_request.url.path,
            "body_sha256": hashlib.sha256(http_request.content).hexdigest(),
            "model": body.get("model"),
            "thinking": body.get("thinking"),
            "reasoning_effort": body.get("reasoning_effort"),
            "session_present": bool(http_request.headers.get("x-opencode-session")),
        }
        attempts.append(entry)
        http_request.extensions["pilot_attempt"] = entry
        http_request.extensions["pilot_tools"] = {
            t["function"]["name"]
            for t in body.get("tools", [])
            if t.get("type") == "function"
        }
        persist()

    async def on_response(response):
        entry = response.request.extensions["pilot_attempt"]
        entry.update(
            {"status_code": response.status_code, "headers_received": time.time()}
        )
        persist()
        # This service uses non-streaming requests: HTTPX would read the same body
        # immediately after this hook. Preserve read exceptions for SDK retries.
        try:
            raw = await response.aread()
        except BaseException as exc:
            entry["response_diagnostic"] = {
                "schema_version": "response-diagnostic-v1",
                "state": "read_error",
                "error_type": type(exc).__name__,
            }
            try:
                persist()
            except OSError:
                pass  # Do not mask the original transport error/cancellation.
            raise
        entry["response_diagnostic"] = response_diagnostic(
            raw, response.request.extensions["pilot_tools"]
        )
        persist()

    model, client = create_deepseek_chat_model(
        gateway=config.model_provider,
        model_name=config.model_name,
        api_key=api_key,
        timeout_seconds=config.http_timeout_seconds,
        http_retries=config.http_retries,
        conversation_id=request.run_id,
        execution_config=config,
    )
    client._client.event_hooks["request"].append(on_request)
    client._client.event_hooks["response"].append(on_response)
    try:
        return await _execute_service(
            context,
            prepared,
            request,
            initial,
            model=model,
            process_strategy=SerialRuntime(runtime_gate, WindowsProcessStrategy())
            if runtime_gate is not None
            else None,
            config=config,
            evidence_kind="live",
            http_count=lambda: len(attempts),
            question_protocol=question_protocol,
            answer_mode=answer_mode,
        )
    finally:
        await client.close()
