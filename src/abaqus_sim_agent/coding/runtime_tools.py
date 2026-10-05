from __future__ import annotations

import hashlib
from collections.abc import Awaitable
from dataclasses import asdict
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field
from pydantic_ai import Agent, RunContext
from pydantic_ai_harness.step_persistence import annotate_tool_effect

from abaqus_sim_agent.coding.aci import PYTHON_SYNTAX_GATE_VERSION
from abaqus_sim_agent.coding.api_lookup import ApiCatalogMode, runtime_api_hint
from abaqus_sim_agent.coding.cae_script_policy import (
    find_cae_solver_lifecycle_calls,
)
from abaqus_sim_agent.coding.contracts import (
    ExecutionPurpose,
    ProbeConclusionStatus,
    ProbeResultInput,
)
from abaqus_sim_agent.coding.dependencies import (
    CodingAgentDependencies,
    RuntimeBudgetExceeded,
)
from abaqus_sim_agent.coding.diagnostic_retrieval import (
    DiagnosticRetrievalMode,
    maybe_add_solver_diagnostic_advisory,
)
from abaqus_sim_agent.coding.document_policy_tools import document_runtime_denial
from abaqus_sim_agent.coding.probe import ProbeProtocolMode, ProbeProtocolState
from abaqus_sim_agent.coding.progress import CodingProgress, notify
from abaqus_sim_agent.coding.resume import (
    append_execution_result,
)
from abaqus_sim_agent.coding.static_preflight import (
    static_preflight_payload,
)
from abaqus_sim_agent.execution.abaqus2024 import (
    AbaqusExecutionResult,
    AbaqusInvocation,
    SolverCpus,
    SolverMemory,
    solver_resource_arguments,
)

__all__ = ["SolverJobRequest", "finalize_runtime_result", "register_runtime_tools"]


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class SubmitSolverJobRequest(_StrictModel):
    action: Literal["submit"] = "submit"
    job_name: str = Field(min_length=1, max_length=80)
    input_file: str = Field(min_length=1)
    cpus: SolverCpus | None = None
    memory: SolverMemory | None = None


class WaitSolverJobRequest(_StrictModel):
    action: Literal["wait"] = "wait"
    execution_id: str = Field(pattern=r"^[0-9a-f]{32}$")


SolverJobRequest = Annotated[
    SubmitSolverJobRequest | WaitSolverJobRequest,
    Field(discriminator="action"),
]


def register_runtime_tools[AgentOutputT](
    agent: Agent[CodingAgentDependencies, AgentOutputT],
    *,
    probe_protocol_mode: ProbeProtocolMode,
    probe_target_scripts: tuple[str, ...] | None,
    api_catalog_mode: ApiCatalogMode,
    diagnostic_retrieval_mode: DiagnosticRetrievalMode,
    background_solver_enabled: bool = False,
) -> None:
    if probe_protocol_mode == "typed":

        @agent.tool(name="run_cae_script", sequential=True)
        async def run_cae_script_typed(
            ctx: RunContext[CodingAgentDependencies],
            script_path: str,
            purpose: ExecutionPurpose,
            probe_result: ProbeResultInput | None = None,
        ) -> dict[str, object]:
            """Run a production or diagnostic script in Abaqus/CAE 2024.

            Diagnostic runs must be followed by ``record_probe_result``.
            Production authoring may create CAE/INP but must not submit a Job.
            """
            await _annotate_runtime_tool(ctx, "cae_nogui", script_path)
            return await _run_cae_tool(
                ctx.deps,
                script_path,
                purpose=purpose,
                probe_protocol_enabled=True,
                probe_result=probe_result,
                probe_target_scripts=probe_target_scripts,
                api_catalog_mode=api_catalog_mode,
            )

        @agent.tool(name="run_abaqus_python", sequential=True)
        async def run_abaqus_python_typed(
            ctx: RunContext[CodingAgentDependencies],
            script_path: str,
            purpose: ExecutionPurpose,
            probe_result: ProbeResultInput | None = None,
        ) -> dict[str, object]:
            """Run production extraction or a diagnostic in Abaqus Python.

            Use production for required odbAccess/result scripts. Diagnostic
            runs must be followed by ``record_probe_result``.
            """
            await _annotate_runtime_tool(ctx, "abaqus_python", script_path)
            return await _run_abaqus_python_tool(
                ctx.deps,
                script_path,
                purpose=purpose,
                probe_protocol_enabled=True,
                probe_result=probe_result,
                probe_target_scripts=probe_target_scripts,
                api_catalog_mode=api_catalog_mode,
            )

        @agent.tool
        async def record_probe_result(
            ctx: RunContext[CodingAgentDependencies],
            status: ProbeConclusionStatus,
            claim: str,
            evidence: str,
            target_script: str | None = None,
        ) -> dict[str, object]:
            """Record the typed conclusion of the latest diagnostic run.

            Use ``validated`` only for an adoptable candidate and provide the
            production ``target_script``. Rejected/inconclusive results must
            omit the target.
            """
            async with ctx.deps.probe_runtime_lock:
                return ctx.deps.ensure_probe_protocol(
                    probe_target_scripts
                ).record_result(
                    status=status,
                    claim=claim,
                    evidence=evidence,
                    target_script=target_script,
                )

        @agent.tool(name="run_solver_job", sequential=True)
        async def run_solver_job_typed(
            ctx: RunContext[CodingAgentDependencies],
            job_name: str,
            input_file: str,
            cpus: SolverCpus | None = None,
            memory: SolverMemory | None = None,
            probe_result: ProbeResultInput | None = None,
        ) -> dict[str, object]:
            """Run Abaqus/Standard after typed probe obligations are resolved."""
            await _annotate_runtime_tool(
                ctx,
                "solver_job",
                input_file,
                job_name=job_name,
                cpus=cpus,
                memory=memory,
            )
            return await _run_solver_tool(
                ctx,
                job_name,
                input_file,
                cpus=cpus,
                memory=memory,
                probe_protocol_enabled=True,
                probe_result=probe_result,
                probe_target_scripts=probe_target_scripts,
                api_catalog_mode=api_catalog_mode,
                diagnostic_retrieval_mode=diagnostic_retrieval_mode,
            )

    else:

        @agent.tool(name="run_cae_script", sequential=True)
        async def run_cae_script_untyped(
            ctx: RunContext[CodingAgentDependencies],
            script_path: str,
        ) -> dict[str, object]:
            """Run a workspace Python script in the Abaqus/CAE 2024 kernel.

            This authoring stage may create a CAE database and input deck but
            must not submit a solver job.
            """
            await _annotate_runtime_tool(ctx, "cae_nogui", script_path)
            return await _run_cae_tool(
                ctx.deps,
                script_path,
                purpose="production",
                probe_protocol_enabled=False,
                probe_result=None,
                probe_target_scripts=None,
                api_catalog_mode=api_catalog_mode,
            )

        @agent.tool(name="run_abaqus_python", sequential=True)
        async def run_abaqus_python_untyped(
            ctx: RunContext[CodingAgentDependencies],
            script_path: str,
        ) -> dict[str, object]:
            """Run a workspace script with Abaqus 2024 standalone Python.

            Use this for odbAccess-based numerical extraction, not CAE modeling.
            """
            await _annotate_runtime_tool(ctx, "abaqus_python", script_path)
            return await _run_abaqus_python_tool(
                ctx.deps,
                script_path,
                purpose="production",
                probe_protocol_enabled=False,
                probe_result=None,
                probe_target_scripts=None,
                api_catalog_mode=api_catalog_mode,
            )

        if background_solver_enabled:

            @agent.tool(name="solver_job", sequential=True)
            async def solver_job_untyped(
                ctx: RunContext[CodingAgentDependencies],
                request: SolverJobRequest,
            ) -> dict[str, object]:
                """Submit a Solver job or wait for a previously submitted execution.

                Use ``submit`` to start Abaqus/Standard and immediately receive an
                execution ID. Continue independent work, then use ``wait`` before
                reading Solver outputs or requesting verification.
                """
                if isinstance(request, SubmitSolverJobRequest):
                    await _annotate_runtime_tool(
                        ctx,
                        "solver_job",
                        request.input_file,
                        job_name=request.job_name,
                        cpus=request.cpus,
                        memory=request.memory,
                    )
                    return await _submit_solver_tool(
                        ctx.deps,
                        request.job_name,
                        request.input_file,
                        cpus=request.cpus,
                        memory=request.memory,
                    )
                return await _wait_solver_tool(
                    ctx.deps,
                    request.execution_id,
                    api_catalog_mode=api_catalog_mode,
                    diagnostic_retrieval_mode=diagnostic_retrieval_mode,
                )

        else:

            @agent.tool(name="run_solver_job", sequential=True)
            async def run_solver_job_untyped(
                ctx: RunContext[CodingAgentDependencies],
                job_name: str,
                input_file: str,
                cpus: SolverCpus | None = None,
                memory: SolverMemory | None = None,
            ) -> dict[str, object]:
                """Run one Abaqus/Standard 2024 job from a workspace INP."""
                await _annotate_runtime_tool(
                    ctx,
                    "solver_job",
                    input_file,
                    job_name=job_name,
                    cpus=cpus,
                    memory=memory,
                )
                return await _run_solver_tool(
                    ctx,
                    job_name,
                    input_file,
                    cpus=cpus,
                    memory=memory,
                    probe_protocol_enabled=False,
                    probe_result=None,
                    probe_target_scripts=None,
                    api_catalog_mode=api_catalog_mode,
                    diagnostic_retrieval_mode=diagnostic_retrieval_mode,
                )


async def _annotate_runtime_tool(
    ctx: RunContext[CodingAgentDependencies],
    invocation: Literal["cae_nogui", "abaqus_python", "solver_job"],
    raw_path: str,
    *,
    job_name: str | None = None,
    cpus: SolverCpus | None = None,
    memory: SolverMemory | None = None,
) -> None:
    """在外部副作用开始前写入可对账的 Harness tool-effect 元数据。"""
    store = ctx.deps.step_store
    if store is None:
        return
    normalized = raw_path.replace("\\", "/")
    path = PurePosixPath(normalized)
    windows_path = PureWindowsPath(normalized)
    digest = "unresolved"
    if (
        normalized
        and not path.is_absolute()
        and not windows_path.is_absolute()
        and not windows_path.drive
        and ".." not in path.parts
    ):
        candidate = ctx.deps.request.workspace / normalized
        if candidate.is_file() and not candidate.is_symlink():
            digest = _sha256_file(candidate)
    identity_parts = [
        ctx.deps.request.run_id,
        invocation,
        job_name or "-",
        normalized,
        digest,
    ]
    resource_args = solver_resource_arguments(cpus, memory)
    identity_parts.extend(resource_args)
    await annotate_tool_effect(
        store,
        ctx,
        idempotency_key="::".join(identity_parts),
        effect_summary=(
            f"{invocation} path={normalized}"
            + (f" job={job_name}" if job_name is not None else "")
            + (" " + " ".join(resource_args) if resource_args else "")
        ),
    )


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


async def _run_cae_tool(
    dependencies: CodingAgentDependencies,
    script_path: str,
    *,
    purpose: ExecutionPurpose,
    probe_protocol_enabled: bool,
    probe_result: ProbeResultInput | None,
    probe_target_scripts: tuple[str, ...] | None,
    api_catalog_mode: ApiCatalogMode,
) -> dict[str, object]:
    if permission_denied := dependencies.runtime_permission_denial(solver=False):
        raise RuntimeBudgetExceeded(permission_denied)
    denied = dependencies.runtime_script_policy_denial(
        kind="cae",
        path=script_path,
    )
    if denied is not None:
        return denied
    if document_denied := await document_runtime_denial(
        dependencies, kind="cae", path=script_path
    ):
        return document_denied
    lifecycle_calls = find_cae_solver_lifecycle_calls(
        dependencies.request.workspace,
        script_path,
    )
    if lifecycle_calls:
        return {
            "succeeded": False,
            "stage_succeeded": False,
            "blocked": True,
            "runtime_executed": False,
            "budget_consumed": False,
            "error_type": "CaeSolverLifecycleDenied",
            "error": (
                "CAE authoring scripts must not call submit() or "
                "waitForCompletion(); generate the input deck and use "
                "run_solver_job instead."
            ),
            "script_path": script_path.replace("\\", "/"),
            "calls": [
                {
                    "method": call.method,
                    "line": call.line,
                    "column": call.column,
                }
                for call in lifecycle_calls
            ],
        }
    preflight_block, preflight_annotation = await _python_checks_before_runtime(
        dependencies,
        kind="cae",
        script_path=script_path,
    )
    if preflight_block is not None:
        return preflight_block
    async with dependencies.probe_runtime_lock:
        protocol = (
            dependencies.ensure_probe_protocol(probe_target_scripts)
            if probe_protocol_enabled
            else None
        )
        recorded: dict[str, object] | None = None
        if protocol is not None:
            if probe_result is not None:
                recorded = protocol.record_input(probe_result)
                if recorded["status"] == "invalid":
                    return _invalid_probe_result(recorded)
            blocked = protocol.before_script_execution(
                purpose=purpose,
                script_path=script_path,
            )
            if blocked is not None:
                if recorded is not None:
                    blocked["recorded_probe_result"] = recorded["probe"]
                return blocked
        exhausted = dependencies.reserve_runtime_call(solver=False)
        if exhausted:
            raise RuntimeBudgetExceeded(exhausted)
        notify(
            dependencies.observer,
            CodingProgress(
                "runtime_started",
                "cae",
                dependencies.current_turn,
                dependencies.current_task_revision,
            ),
        )
        payload = await _runtime_tool_result(
            dependencies,
            dependencies.runner.run_cae_script(
                script_path,
                timeout_seconds=dependencies.request.runtime_policy.timeout_seconds(
                    "cae"
                ),
            ),
            api_catalog_mode=api_catalog_mode,
            probe_protocol=protocol,
            purpose=purpose,
            script_path=script_path,
        )
        if recorded is not None:
            payload["recorded_probe_result"] = recorded["probe"]
        if preflight_annotation is not None:
            payload["static_preflight"] = preflight_annotation
        return payload


async def _run_abaqus_python_tool(
    dependencies: CodingAgentDependencies,
    script_path: str,
    *,
    purpose: ExecutionPurpose,
    probe_protocol_enabled: bool,
    probe_result: ProbeResultInput | None,
    probe_target_scripts: tuple[str, ...] | None,
    api_catalog_mode: ApiCatalogMode,
) -> dict[str, object]:
    if permission_denied := dependencies.runtime_permission_denial(solver=False):
        raise RuntimeBudgetExceeded(permission_denied)
    denied = dependencies.runtime_script_policy_denial(
        kind="abaqus_python",
        path=script_path,
    )
    if denied is not None:
        return denied
    if document_denied := await document_runtime_denial(
        dependencies, kind="abaqus_python", path=script_path
    ):
        return document_denied
    preflight_block, preflight_annotation = await _python_checks_before_runtime(
        dependencies,
        kind="abaqus_python",
        script_path=script_path,
    )
    if preflight_block is not None:
        return preflight_block
    async with dependencies.probe_runtime_lock:
        protocol = (
            dependencies.ensure_probe_protocol(probe_target_scripts)
            if probe_protocol_enabled
            else None
        )
        recorded: dict[str, object] | None = None
        if protocol is not None:
            if probe_result is not None:
                recorded = protocol.record_input(probe_result)
                if recorded["status"] == "invalid":
                    return _invalid_probe_result(recorded)
            blocked = protocol.before_script_execution(
                purpose=purpose,
                script_path=script_path,
            )
            if blocked is not None:
                if recorded is not None:
                    blocked["recorded_probe_result"] = recorded["probe"]
                return blocked
        exhausted = dependencies.reserve_runtime_call(solver=False)
        if exhausted:
            raise RuntimeBudgetExceeded(exhausted)
        notify(
            dependencies.observer,
            CodingProgress(
                "runtime_started",
                "extract",
                dependencies.current_turn,
                dependencies.current_task_revision,
            ),
        )
        payload = await _runtime_tool_result(
            dependencies,
            dependencies.runner.run_abaqus_python(
                script_path,
                timeout_seconds=dependencies.request.runtime_policy.timeout_seconds(
                    "abaqus_python"
                ),
            ),
            api_catalog_mode=api_catalog_mode,
            probe_protocol=protocol,
            purpose=purpose,
            script_path=script_path,
        )
        if recorded is not None:
            payload["recorded_probe_result"] = recorded["probe"]
        if preflight_annotation is not None:
            payload["static_preflight"] = preflight_annotation
        return payload


def _solver_validation_error(
    dependencies: CodingAgentDependencies,
    job_name: str,
    input_file: str,
    *,
    cpus: SolverCpus | None,
    memory: SolverMemory | None,
) -> dict[str, object] | None:
    try:
        dependencies.runner.validate_solver_job(
            job_name,
            input_file=input_file,
            cpus=cpus,
            memory=memory,
            timeout_seconds=dependencies.request.runtime_policy.timeout_seconds(
                "solver"
            ),
        )
    except (OSError, ValueError) as exc:
        return {
            "succeeded": False,
            "error_type": type(exc).__name__,
            "error": str(exc),
        }
    return None


async def _run_solver_tool(
    ctx: RunContext[CodingAgentDependencies],
    job_name: str,
    input_file: str,
    *,
    cpus: SolverCpus | None = None,
    memory: SolverMemory | None = None,
    probe_protocol_enabled: bool,
    probe_result: ProbeResultInput | None,
    probe_target_scripts: tuple[str, ...] | None,
    api_catalog_mode: ApiCatalogMode,
    diagnostic_retrieval_mode: DiagnosticRetrievalMode,
) -> dict[str, object]:
    dependencies = ctx.deps
    if permission_denied := dependencies.runtime_permission_denial(solver=True):
        raise RuntimeBudgetExceeded(permission_denied)
    denied = dependencies.runtime_script_policy_denial(
        kind="solver_input",
        path=input_file,
    )
    if denied is not None:
        return denied
    if document_denied := await document_runtime_denial(
        dependencies, kind="solver_input", path=input_file
    ):
        return document_denied
    async with dependencies.probe_runtime_lock:
        if probe_protocol_enabled:
            protocol = dependencies.ensure_probe_protocol(probe_target_scripts)
            recorded: dict[str, object] | None = None
            if probe_result is not None:
                recorded = protocol.record_input(probe_result)
                if recorded["status"] == "invalid":
                    return _invalid_probe_result(recorded)
            blocked = protocol.before_solver_execution()
            if blocked is not None:
                if recorded is not None:
                    blocked["recorded_probe_result"] = recorded["probe"]
                return blocked
        else:
            recorded = None
        validation_error = _solver_validation_error(
            dependencies, job_name, input_file, cpus=cpus, memory=memory
        )
        if validation_error is not None:
            if recorded is not None:
                validation_error["recorded_probe_result"] = recorded["probe"]
            return validation_error
        exhausted = dependencies.reserve_runtime_call(solver=True)
        if exhausted:
            raise RuntimeBudgetExceeded(exhausted)
        notify(
            dependencies.observer,
            CodingProgress(
                "runtime_started",
                "solver",
                dependencies.current_turn,
                dependencies.current_task_revision,
            ),
        )
        payload = await _runtime_tool_result(
            dependencies,
            dependencies.runner.run_solver_job(
                job_name,
                input_file=input_file,
                cpus=cpus,
                memory=memory,
                timeout_seconds=dependencies.request.runtime_policy.timeout_seconds(
                    "solver"
                ),
            ),
            api_catalog_mode=api_catalog_mode,
            diagnostic_retrieval_mode=diagnostic_retrieval_mode,
        )
        if recorded is not None:
            payload["recorded_probe_result"] = recorded["probe"]
        return payload


async def _submit_solver_tool(
    dependencies: CodingAgentDependencies,
    job_name: str,
    input_file: str,
    *,
    cpus: SolverCpus | None = None,
    memory: SolverMemory | None = None,
) -> dict[str, object]:
    if permission_denied := dependencies.runtime_permission_denial(solver=True):
        raise RuntimeBudgetExceeded(permission_denied)
    denied = dependencies.runtime_script_policy_denial(
        kind="solver_input",
        path=input_file,
    )
    if denied is not None:
        return denied
    if document_denied := await document_runtime_denial(
        dependencies, kind="solver_input", path=input_file
    ):
        return document_denied
    validation_error = _solver_validation_error(
        dependencies, job_name, input_file, cpus=cpus, memory=memory
    )
    if validation_error is not None:
        return validation_error
    exhausted = dependencies.reserve_runtime_call(solver=True)
    if exhausted:
        raise RuntimeBudgetExceeded(exhausted)
    notify(
        dependencies.observer,
        CodingProgress(
            "runtime_started",
            "solver",
            dependencies.current_turn,
            dependencies.current_task_revision,
        ),
    )
    try:
        execution_id = dependencies.runner.submit_solver_job(
            job_name,
            input_file=input_file,
            cpus=cpus,
            memory=memory,
            timeout_seconds=dependencies.request.runtime_policy.timeout_seconds(
                "solver"
            ),
        )
    except (OSError, RuntimeError, ValueError) as exc:
        notify(
            dependencies.observer,
            CodingProgress(
                "runtime_finished",
                "model",
                dependencies.current_turn,
                dependencies.current_task_revision,
            ),
        )
        return {
            "succeeded": False,
            "error_type": type(exc).__name__,
            "error": str(exc),
        }
    dependencies.pending_solver_executions.add(execution_id)
    return {
        "succeeded": True,
        "submitted": True,
        "execution_id": execution_id,
        "job_name": job_name,
        "input_file": input_file.replace("\\", "/"),
        "guidance": (
            "Continue independent work if useful, then call solver_job with "
            "action='wait' before reading Solver outputs or requesting verification."
        ),
    }


async def _wait_solver_tool(
    dependencies: CodingAgentDependencies,
    execution_id: str,
    *,
    api_catalog_mode: ApiCatalogMode,
    diagnostic_retrieval_mode: DiagnosticRetrievalMode,
) -> dict[str, object]:
    try:
        async with dependencies.active_wall.pause():
            result = await dependencies.runner.wait_solver_job(execution_id)
    except (OSError, RuntimeError, ValueError) as exc:
        dependencies.pending_solver_executions.discard(execution_id)
        notify(
            dependencies.observer,
            CodingProgress(
                "runtime_finished",
                "model",
                dependencies.current_turn,
                dependencies.current_task_revision,
            ),
        )
        return {
            "succeeded": False,
            "error_type": type(exc).__name__,
            "error": str(exc),
        }
    dependencies.pending_solver_executions.discard(execution_id)
    return await finalize_runtime_result(
        dependencies,
        result,
        api_catalog_mode=api_catalog_mode,
        diagnostic_retrieval_mode=diagnostic_retrieval_mode,
    )


def _invalid_probe_result(recorded: dict[str, object]) -> dict[str, object]:
    return {
        "succeeded": False,
        "stage_succeeded": False,
        "blocked": True,
        "error_type": "ProbeProtocolInvalidConclusion",
        "error": recorded["error"],
        "probe_result": recorded,
    }


async def _python_checks_before_runtime(
    dependencies: CodingAgentDependencies,
    *,
    kind: Literal["cae", "abaqus_python"],
    script_path: str,
) -> tuple[dict[str, object] | None, dict[str, object] | None]:
    """在已有权限检查后验证语法，收集非阻断诊断，不启动 vendor runtime。

    Returns:
        第一项是语法或源码变化的拒绝信息；第二项是随实际运行结果返回的静态反馈。
        分析器不可用时允许运行，但不缓存该次未完成的检查。
    """
    gate = dependencies.syntax_gate
    assert gate is not None
    syntax = gate.check_runtime(script_path)
    if syntax.status != "clean":
        return {
            "succeeded": False,
            "stage_succeeded": False,
            "blocked": True,
            "budget_consumed": False,
            "runtime_executed": False,
            "error_type": "PythonSyntaxRuntimeDenied"
            if syntax.status == "denied"
            else "PythonSyntaxGateUnavailable",
            "syntax_gate_version": PYTHON_SYNTAX_GATE_VERSION,
            **asdict(syntax),
        }, None
    preflight = dependencies.static_preflight
    if preflight is None:
        return None, None
    assert syntax.script_sha256 is not None
    key = (kind, syntax.script_path, syntax.script_sha256)
    report = dependencies.static_preflight_reports.get(key)
    annotation: dict[str, object] = {
        "status": "unavailable",
        "error": "Static analysis did not complete",
        "non_blocking": True,
    }
    if report is None:
        exhausted = dependencies.reserve_static_preflight()
        if exhausted is not None:
            annotation = {
                "status": "unavailable",
                "error": exhausted,
                "non_blocking": True,
            }
        else:
            try:
                async with dependencies.active_wall.pause():
                    report = await preflight.inspect(
                        dependencies.request.workspace, script_path
                    )
            except (OSError, RuntimeError, ValueError) as exc:
                annotation = {
                    "status": "unavailable",
                    "error": f"{type(exc).__name__}: {exc}",
                    "non_blocking": True,
                }
            else:
                if report.status in {"clean", "diagnostics"}:
                    dependencies.static_preflight_reports[key] = report
    if report is not None:
        if (
            report.script_path != syntax.script_path
            or report.script_sha256 != syntax.script_sha256
            or report.status == "changed"
        ):
            return _script_changed_during_checks(), None
        annotation = static_preflight_payload(report)
    # A slow analyzer must not allow a newly changed, unchecked script to execute.
    try:
        current = _sha256_file(dependencies.request.workspace / script_path)
    except OSError:
        return _script_changed_during_checks(), None
    if current != syntax.script_sha256:
        return _script_changed_during_checks(), None
    return None, annotation


def _script_changed_during_checks() -> dict[str, object]:
    return {
        "succeeded": False,
        "stage_succeeded": False,
        "blocked": True,
        "budget_consumed": False,
        "runtime_executed": False,
        "error_type": "StaticPreflightScriptChanged",
        "error": "Script changed during checks; request execution of its current version.",
    }


def _tool_payload(result: AbaqusExecutionResult) -> dict[str, object]:
    abaqus_error_detected = "Abaqus Error:" in result.stdout
    python_traceback_detected = (
        result.invocation
        in {AbaqusInvocation.CAE_NOGUI, AbaqusInvocation.ABAQUS_PYTHON}
        and "Traceback (most recent call last)" in result.stderr
    )
    stage_succeeded = result.stage_succeeded
    payload: dict[str, object] = {
        "succeeded": stage_succeeded,
        "process_succeeded": result.succeeded,
        "abaqus_error_detected": abaqus_error_detected,
        "python_traceback_detected": python_traceback_detected,
        "python_fatal_error_detected": result.python_fatal_error_detected,
        "stage_succeeded": stage_succeeded,
        "invocation": result.invocation.value,
        "exit_code": result.exit_code,
        "timed_out": result.timed_out,
        "duration_seconds": result.duration_seconds,
        "stdout_tail": _tail(result.stdout),
        "stderr_tail": _tail(result.stderr),
        "stdout_encoding": result.stdout_encoding,
        "stderr_encoding": result.stderr_encoding,
        "artifact_changes": [
            change.model_dump(mode="json") for change in result.artifact_changes
        ],
    }
    if result.python_fatal_error_detected:
        payload["error_type"] = "PythonFatalError"
        payload["error"] = (
            "The Abaqus Python interpreter reported a fatal error. "
            "Inspect stderr for the failing call; a zero launcher exit code does not mean the script completed."
        )
    if result.invocation is AbaqusInvocation.SOLVER_JOB:
        payload["diagnostics"] = [
            diagnostic.model_dump(mode="json") for diagnostic in result.diagnostics
        ]
    return payload


async def _runtime_tool_result(
    dependencies: CodingAgentDependencies,
    execution: Awaitable[AbaqusExecutionResult],
    *,
    api_catalog_mode: ApiCatalogMode = "full",
    probe_protocol: ProbeProtocolState | None = None,
    purpose: ExecutionPurpose = "production",
    script_path: str | None = None,
    diagnostic_retrieval_mode: DiagnosticRetrievalMode = "off",
) -> dict[str, object]:
    try:
        async with dependencies.active_wall.pause():
            result = await execution
    except (OSError, ValueError) as exc:
        notify(
            dependencies.observer,
            CodingProgress(
                "runtime_finished",
                "model",
                dependencies.current_turn,
                dependencies.current_task_revision,
            ),
        )
        return {
            "succeeded": False,
            "error_type": type(exc).__name__,
            "error": str(exc),
        }
    return await finalize_runtime_result(
        dependencies,
        result,
        api_catalog_mode=api_catalog_mode,
        probe_protocol=probe_protocol,
        purpose=purpose,
        script_path=script_path,
        diagnostic_retrieval_mode=diagnostic_retrieval_mode,
    )


async def finalize_runtime_result(
    dependencies: CodingAgentDependencies,
    result: AbaqusExecutionResult,
    *,
    api_catalog_mode: ApiCatalogMode = "full",
    probe_protocol: ProbeProtocolState | None = None,
    purpose: ExecutionPurpose = "production",
    script_path: str | None = None,
    diagnostic_retrieval_mode: DiagnosticRetrievalMode = "off",
    record_execution: bool = True,
) -> dict[str, object]:
    """把一个已完成的 Abaqus 结果接回原有证据、诊断和工具返回语义。"""
    if record_execution:
        append_execution_result(dependencies.request.state_dir, result)
        dependencies.record_runtime_execution(result)
    notify(
        dependencies.observer,
        CodingProgress(
            "runtime_finished",
            "model",
            dependencies.current_turn,
            dependencies.current_task_revision,
        ),
    )
    payload = _tool_payload(result)
    if payload["stage_succeeded"] is False and api_catalog_mode != "off":
        hint = runtime_api_hint(
            dependencies,
            result,
            mode=api_catalog_mode,
        )
        if hint is not None:
            payload["api_lookup"] = hint
    if diagnostic_retrieval_mode == "advisory":
        try:
            advisory = await maybe_add_solver_diagnostic_advisory(
                dependencies,
                result,
            )
        except Exception as exc:
            payload["diagnostic_retrieval"] = {
                "status": "error",
                "error_type": type(exc).__name__,
                "trace_status": "not_recorded",
            }
        else:
            if advisory is not None:
                payload["diagnostic_retrieval"] = advisory
    if probe_protocol is not None:
        if script_path is None:
            raise ValueError("probe protocol requires script_path")
        stage_succeeded = payload["stage_succeeded"] is True
        if purpose == "diagnostic":
            payload["probe_protocol"] = probe_protocol.register_diagnostic_execution(
                script_path,
                result,
                runtime_stage_succeeded=stage_succeeded,
            )
        else:
            adoption = probe_protocol.register_production_execution(
                script_path,
                result,
                runtime_stage_succeeded=stage_succeeded,
            )
            if adoption is not None:
                payload["probe_adoption"] = adoption
    return payload


def _tail(value: str, limit: int = 8_000) -> str:
    return value[-limit:]
