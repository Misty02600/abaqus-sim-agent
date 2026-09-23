from __future__ import annotations

import asyncio
import hashlib
import logging
import threading
import time
from collections.abc import Awaitable, Callable
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, cast
from uuid import uuid4

from pydantic import SecretStr

from ..coding.application import (
    ConversationMessage,
    PreparedCodingRun,
    answer_question,
    prepare_new_run,
    prepare_resume,
)
from ..coding.clarification import ScriptedClarificationProvider
from ..coding.composition import ModelFactory, RunnerFactory, open_coding_run
from ..coding.contracts import (
    CodingRunBudget,
    CodingRunControlValues,
    CodingRunLimits,
    CodingRunRequest,
    EvaluationToolLimits,
    RuntimeExecutionPolicy,
    RuntimeProfile,
)
from ..coding.deferred_runtime import (
    DeferredSolverResolution,
    DeferredSolverRunner,
    load_deferred_tool_frontier,
)
from ..coding.domain_knowledge import (
    DomainKnowledgeInventory,
    discover_domain_knowledge_packs,
)
from ..coding.execution_verifier import ExecutionEvidenceVerifier
from ..coding.model import (
    DEEPSEEK_V4_FLASH,
    DeepSeekGateway,
    ModelExecutionConfig,
    create_deepseek_chat_model,
    new_run_execution_config,
)
from ..coding.progress import CodingProgress
from ..coding.read_model import (
    CodingRunView,
    project_checkpoint,
    read_coding_view,
)
from ..coding.response_stream import (
    ModelResponseControl,
    RunCancellationControl,
    UserStoppedError,
)
from ..coding.resume import (
    STEP_PERSISTENCE_DIRECTORY,
    CodingRunResumeError,
    load_effective_run_for_resume,
    load_resume_state,
)
from ..coding.run_lock import CodingRunAlreadyLocked, CodingRunFileLock
from ..coding.user_questions import UserAnswerInput, WaitingForUser
from ..execution import (
    DEFAULT_ABAQUS_2024_LAUNCHER,
    Abaqus2024Runner,
    AbaqusExecutionResult,
)
from .contracts import (
    AgentInvocation,
    AnswerCommand,
    CommandReceipt,
    CommandResponse,
    ContextUsage,
    CreateRun,
    DeleteRunResponse,
    DisplayMetadata,
    MessageCommand,
    Registration,
    RenameCommand,
    RunCommand,
    RunIssue,
    RunList,
    RunSnapshot,
    StopCommand,
    ThreadMessage,
    ToolExecution,
    ToolExecutionCancelCommand,
    ToolExecutionList,
    WebError,
)
from .deletion import RunDeletionStore
from .storage import (
    InvocationStore,
    RunRegistry,
    ThreadStore,
    ToolExecutionStore,
    digest,
    plain_path,
    save_model,
    selected_workspace,
)

DELETE_STOP_TIMEOUT_SECONDS = 20.0


@dataclass(frozen=True)
class ServerConfig:
    root: Path
    knowledge_packs_root: Path | None = None
    stream_model: bool = True
    execution: ModelExecutionConfig = field(
        default_factory=lambda: new_run_execution_config(
            model_provider=DeepSeekGateway.OFFICIAL,
            model_name=DEEPSEEK_V4_FLASH,
            planning_enabled=False,
        )
    )
    limits: CodingRunLimits = field(default_factory=CodingRunLimits)
    runtime_policy: RuntimeExecutionPolicy = field(
        default_factory=RuntimeExecutionPolicy
    )
    evaluation_tool_limits: EvaluationToolLimits | None = None
    budget: CodingRunBudget | None = None
    runtime: RuntimeProfile = field(
        default_factory=lambda: RuntimeProfile(
            launcher_path=DEFAULT_ABAQUS_2024_LAUNCHER.resolve()
        )
    )
    context_warning_threshold_tokens: int | None = 900_000

    def request_controls(self) -> CodingRunControlValues:
        if self.budget is not None:
            return self.budget.as_request_controls()
        return {
            "limits": self.limits,
            "runtime_policy": self.runtime_policy,
            "evaluation_tool_limits": self.evaluation_tool_limits,
        }


class _ToolExecutionRunner(DeferredSolverRunner):
    """投影 Solver 生命周期，并在 Agent 取消传播前保留 runtime 收尾错误。"""

    def __init__(
        self,
        base: Abaqus2024Runner,
        controller: "RunController",
        run_id: str,
        invocation_id: str,
    ) -> None:
        self._base = base
        self._controller = controller
        self._run_id = run_id
        self._invocation_id = invocation_id

    async def run_cae_script(self, *args, **kwargs):
        return await self._runtime_stage(self._base.run_cae_script(*args, **kwargs))

    async def run_abaqus_python(self, *args, **kwargs):
        return await self._runtime_stage(self._base.run_abaqus_python(*args, **kwargs))

    async def run_trusted_abaqus_python(self, *args, **kwargs):
        return await self._runtime_stage(
            self._base.run_trusted_abaqus_python(*args, **kwargs)
        )

    async def _runtime_stage(
        self, operation: Awaitable[AbaqusExecutionResult]
    ) -> AbaqusExecutionResult:
        try:
            return await operation
        except Exception as exc:
            # AgentRun.cancel() can surface RunCancelled even if a tool's cleanup
            # failed. Capture this fact at the runner boundary before that translation.
            with self._controller._mutex:
                control = self._controller._run_controls.get(self._run_id)
                if control is not None and control.requested:
                    self._controller._delete_errors[self._run_id] = (
                        f"Abaqus 执行收尾失败：{type(exc).__name__}"
                    )
            raise

    async def run_solver_job(
        self,
        job_name: str,
        *,
        input_file: Path | str | None = None,
        timeout_seconds: float | None = None,
    ) -> AbaqusExecutionResult:
        effective_input = str(
            input_file if input_file is not None else f"{job_name}.inp"
        )
        return await self._controller._run_solver_execution(
            self._run_id,
            self._invocation_id,
            self._base,
            job_name=job_name,
            input_file=effective_input,
            timeout_seconds=timeout_seconds,
        )

    def submit_solver_job(
        self,
        job_name: str,
        *,
        input_file: Path | str,
        timeout_seconds: float | None = None,
    ) -> str:
        execution, _ = self._controller._start_solver_execution(
            self._run_id,
            self._invocation_id,
            self._base,
            tool_call_id=None,
            tool_name="solver_job",
            job_name=job_name,
            input_file=str(input_file),
            timeout_seconds=timeout_seconds,
        )
        return execution.execution_id

    async def wait_solver_job(self, execution_id: str) -> AbaqusExecutionResult:
        try:
            return await self._controller._wait_solver_execution(
                self._run_id,
                execution_id,
            )
        except asyncio.CancelledError:
            resolution = self._controller._resolve_solver_execution(
                self._run_id,
                execution_id,
            )
            if resolution.status == "cancelled":
                raise RuntimeError(
                    resolution.error or "Solver execution was cancelled"
                ) from None
            raise

    def start_deferred_solver_job(
        self,
        *,
        tool_call_id: str,
        job_name: str,
        input_file: str,
        timeout_seconds: float | None,
    ) -> str:
        execution, _ = self._controller._start_solver_execution(
            self._run_id,
            self._invocation_id,
            self._base,
            tool_call_id=tool_call_id,
            job_name=job_name,
            input_file=input_file,
            timeout_seconds=timeout_seconds,
        )
        return execution.execution_id

    def resolve_deferred_solver_job(
        self, execution_id: str
    ) -> DeferredSolverResolution:
        return self._controller._resolve_solver_execution(self._run_id, execution_id)


class RunController:
    def __init__(
        self,
        config: ServerConfig,
        api_key: SecretStr,
        *,
        model_factory: ModelFactory = create_deepseek_chat_model,
        runner_factory: RunnerFactory = Abaqus2024Runner,
    ):
        self.config, self.api_key = config, api_key
        self.registry = RunRegistry(config.root)
        self.threads = ThreadStore(self.registry)
        self.invocations = InvocationStore(self.registry)
        self.tool_executions = ToolExecutionStore(self.registry)
        self.deletions = RunDeletionStore(self.registry)
        self._deleting: set[str] = set()
        self._work_tasks: dict[
            str, tuple[asyncio.AbstractEventLoop, asyncio.Task[None]]
        ] = {}
        self._solver_tasks: dict[str, asyncio.Task[AbaqusExecutionResult]] = {}
        self._delete_errors: dict[str, str] = {}
        self._run_controls: dict[str, RunCancellationControl] = {}
        self._cancelled_for_delete: set[str] = set()
        self.model_factory, self.runner_factory = model_factory, runner_factory
        self._mutex = threading.RLock()
        self._active: str | None = None
        self._future: Future[None] | None = None
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="coding-run")
        self._views: dict[str, CodingRunView] = {}
        self._effective: dict[str, PreparedCodingRun] = {}
        self._closing = False
        self._response_control: ModelResponseControl | None = None
        self._tool_tasks: dict[str, Future[AbaqusExecutionResult]] = {}
        self._deferred_resume_queue: dict[str, tuple[str, str]] = {}
        self._tool_loop: asyncio.AbstractEventLoop | None = None
        self._tool_loop_thread: threading.Thread | None = None
        self._tool_loop_ready = threading.Event()
        self._lock: CodingRunFileLock | None = None
        self.changed: Callable[[str], None] = lambda _: None

    def start(self) -> None:
        self.registry.initialize()
        plain_path(self.registry.root / "control" / "coding_run.lock", directory=False)
        lock = CodingRunFileLock(self.registry.root / "control")
        lock.__enter__()
        self._lock = lock
        self.tool_executions.mark_interrupted_as_needs_reconcile()
        self._start_tool_loop()
        self._recover_deferred_resumes()

    def close(self) -> None:
        with self._mutex:
            self._closing = True
        self._pool.shutdown(wait=True)
        self._stop_tool_loop()
        if self._lock:
            self._lock.__exit__(None, None, None)
            self._lock = None

    def _start_tool_loop(self) -> None:
        with self._mutex:
            if self._tool_loop_thread is not None and self._tool_loop_thread.is_alive():
                return
            self._tool_loop_ready.clear()

        def run_loop() -> None:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            with self._mutex:
                self._tool_loop = loop
            self._tool_loop_ready.set()
            try:
                loop.run_forever()
            finally:
                pending = tuple(asyncio.all_tasks(loop))
                for task in pending:
                    task.cancel()
                if pending:
                    loop.run_until_complete(
                        asyncio.gather(*pending, return_exceptions=True)
                    )
                loop.close()
                with self._mutex:
                    self._tool_loop = None

        thread = threading.Thread(
            target=run_loop,
            name="abaqus-tool-runtime",
            daemon=True,
        )
        with self._mutex:
            self._tool_loop_thread = thread
        thread.start()
        if not self._tool_loop_ready.wait(5.0):
            raise RuntimeError("Abaqus tool runtime did not start")

    def _stop_tool_loop(self) -> None:
        with self._mutex:
            active = tuple(self._tool_tasks.items())
            loop = self._tool_loop
            thread = self._tool_loop_thread
            for execution_id, future in active:
                try:
                    execution = next(
                        item
                        for run_id in self.registry.ids()
                        for item in self.tool_executions.list(run_id)
                        if item.execution_id == execution_id
                    )
                except StopIteration:
                    execution = None
                if execution is not None and execution.status in {
                    "starting",
                    "running",
                }:
                    self.tool_executions.update(
                        execution,
                        status="needs_reconcile",
                        error="宿主在 Solver 执行期间关闭；执行状态需要核查。",
                    )
                future.cancel()
        if loop is not None:
            loop.call_soon_threadsafe(loop.stop)
        if thread is not None:
            thread.join(timeout=10.0)
        with self._mutex:
            self._tool_loop_thread = None
            self._tool_tasks.clear()

    def _tool_execution_snapshot(self, run_id: str) -> ToolExecution | None:
        execution = self.tool_executions.latest(run_id)
        if execution is None:
            return execution
        return self._tool_execution_view(execution)

    @staticmethod
    def _tool_execution_view(execution: ToolExecution) -> ToolExecution:
        if execution.status not in {"starting", "running"}:
            return execution
        started = execution.started_at or execution.created_at
        elapsed = max(
            execution.elapsed_seconds,
            (datetime.now(UTC) - started).total_seconds(),
        )
        return execution.model_copy(update={"elapsed_seconds": elapsed})

    async def _run_solver_execution(
        self,
        run_id: str,
        invocation_id: str,
        runner: Abaqus2024Runner,
        *,
        job_name: str,
        input_file: str,
        timeout_seconds: float | None,
    ) -> AbaqusExecutionResult:
        _, future = self._start_solver_execution(
            run_id,
            invocation_id,
            runner,
            tool_call_id=None,
            job_name=job_name,
            input_file=input_file,
            timeout_seconds=timeout_seconds,
        )
        return await asyncio.wrap_future(future)

    async def _wait_solver_execution(
        self,
        run_id: str,
        execution_id: str,
    ) -> AbaqusExecutionResult:
        with self._mutex:
            future = self._tool_tasks.get(execution_id)
        if future is not None:
            return await asyncio.wrap_future(future)
        resolution = self._resolve_solver_execution(run_id, execution_id)
        if resolution.result is not None:
            return resolution.result
        raise RuntimeError(
            resolution.error or f"Solver execution is {resolution.status}"
        )

    def _start_solver_execution(
        self,
        run_id: str,
        invocation_id: str,
        runner: Abaqus2024Runner,
        *,
        tool_call_id: str | None,
        tool_name: Literal["run_solver_job", "solver_job"] = "run_solver_job",
        job_name: str,
        input_file: str,
        timeout_seconds: float | None,
    ) -> tuple[ToolExecution, Future[AbaqusExecutionResult]]:
        normalized_input = input_file.replace("\\", "/")
        with self._mutex:
            if run_id in self._deleting:
                raise WebError("run_deleting", "正在删除会话。")
            if self._closing:
                raise WebError("closing", "服务正在退出", 503)
            loop = self._tool_loop
            if loop is None or not loop.is_running():
                raise WebError(
                    "tool_runtime_unavailable", "Abaqus 工具运行时未启动", 503
                )
            existing = (
                self.tool_executions.find_for_call(run_id, invocation_id, tool_call_id)
                if tool_call_id is not None
                else None
            )
            if existing is not None:
                if (
                    existing.job_name != job_name
                    or existing.input_file != normalized_input
                    or existing.timeout_seconds != timeout_seconds
                ):
                    raise WebError(
                        "tool_execution_conflict",
                        "同一工具调用不能以不同 Solver 参数重新提交。",
                    )
                active = self._tool_tasks.get(existing.execution_id)
                if active is not None:
                    return existing, active
                result = self.tool_executions.load_result(run_id, existing.execution_id)
                terminal: Future[AbaqusExecutionResult] = Future()
                if result is not None:
                    terminal.set_result(result)
                    return existing, terminal
                if existing.status in {
                    "failed",
                    "cancelled",
                    "timed_out",
                    "needs_reconcile",
                }:
                    terminal.set_exception(
                        RuntimeError(
                            existing.error or f"Solver status is {existing.status}"
                        )
                    )
                    return existing, terminal
                existing = self.tool_executions.update(
                    existing,
                    status="needs_reconcile",
                    error="持久记录仍为活动状态，但宿主控制句柄不存在。",
                )
                terminal.set_exception(
                    RuntimeError(existing.error or "needs reconcile")
                )
                return existing, terminal

        now = datetime.now(UTC)
        execution = ToolExecution(
            execution_id=uuid4().hex,
            thread_id=run_id,
            invocation_id=invocation_id,
            tool_name=tool_name,
            tool_call_id=tool_call_id,
            job_name=job_name,
            input_file=normalized_input,
            timeout_seconds=timeout_seconds,
            created_at=now,
            updated_at=now,
        )
        with self._mutex:
            execution = self.tool_executions.create(execution)
            loop = self._tool_loop
            assert loop is not None
            future = asyncio.run_coroutine_threadsafe(
                self._tracked_solver_execution(execution, runner),
                loop,
            )
            self._tool_tasks[execution.execution_id] = future
            future.add_done_callback(
                lambda completed, run_id=run_id, execution_id=execution.execution_id: (
                    self._forget_tool_task(run_id, execution_id, completed)
                )
            )
        self.changed(run_id)
        return execution, future

    def _forget_tool_task(
        self,
        run_id: str,
        execution_id: str,
        completed: Future[AbaqusExecutionResult],
    ) -> None:
        should_resume = False
        with self._mutex:
            if self._tool_tasks.get(execution_id) is completed:
                self._tool_tasks.pop(execution_id, None)
            try:
                execution = self.tool_executions.load(run_id, execution_id)
            except WebError:
                execution = None
            should_resume = bool(
                execution is not None
                and execution.tool_call_id is not None
                and execution.status
                in {"completed", "failed", "cancelled", "timed_out"}
                and not self._closing
            )
        if should_resume:
            self._queue_deferred_resume(run_id, execution_id)

    def _recover_deferred_resumes(self) -> None:
        """服务启动时恢复已经到终态、但模型尚未消费结果的 Solver frontier。"""
        for run_id in self.registry.ids():
            try:
                registration = self.registry.load(run_id)
                with CodingRunFileLock(registration.request.state_dir):
                    state = load_resume_state(registration.request.state_dir)
                    frontier = load_deferred_tool_frontier(
                        registration.request.state_dir
                    )
                if (
                    state.status != "waiting_for_tool_execution"
                    or frontier is None
                    or frontier.state == "resolved"
                ):
                    continue
                execution = self.tool_executions.load(run_id, frontier.execution_id)
            except (
                OSError,
                ValueError,
                WebError,
                CodingRunResumeError,
                CodingRunAlreadyLocked,
            ):
                continue
            if execution.tool_call_id == frontier.tool_call_id and execution.status in {
                "completed",
                "failed",
                "cancelled",
                "timed_out",
            }:
                self._queue_deferred_resume(run_id, execution.execution_id)

    def _queue_deferred_resume(self, run_id: str, execution_id: str) -> None:
        with self._mutex:
            if self._closing or run_id in self._deleting:
                return
            execution = self.tool_executions.load(run_id, execution_id)
            if execution.tool_call_id is None or execution.status not in {
                "completed",
                "failed",
                "cancelled",
                "timed_out",
            }:
                return
            self._deferred_resume_queue.setdefault(
                execution_id,
                (run_id, execution.invocation_id),
            )
            self._launch_next_deferred_resume_locked()
        self.changed(run_id)

    def _launch_next_deferred_resume_locked(self) -> None:
        if self._closing or self._active is not None or not self._deferred_resume_queue:
            return
        execution_id, (run_id, invocation_id) = next(
            iter(self._deferred_resume_queue.items())
        )
        self._active = run_id
        self._response_control = ModelResponseControl()
        try:
            self._future = self._pool.submit(
                self._resume_deferred_tool_work,
                run_id,
                execution_id,
                invocation_id,
            )
        except Exception:
            self._active = None
            self._response_control = None
            raise
        self._deferred_resume_queue.pop(execution_id, None)

    def _resume_deferred_tool_work(
        self,
        run_id: str,
        execution_id: str,
        invocation_id: str,
    ) -> None:
        registration = self.registry.load(run_id)
        invocation: AgentInvocation | None = None

        async def execute() -> None:
            nonlocal invocation
            with CodingRunFileLock(registration.request.state_dir):
                prepared = self._prepared(registration)
                persisted_before = load_resume_state(registration.request.state_dir)
                frontier = load_deferred_tool_frontier(registration.request.state_dir)

            if persisted_before.status != "waiting_for_tool_execution":
                return
            if frontier is None or frontier.state == "resolved":
                raise WebError(
                    "deferred_resume_invalid",
                    "Solver 等待状态缺少可恢复的工具边界。",
                )

            execution_record = self.tool_executions.load(run_id, execution_id)
            if (
                frontier.run_id != run_id
                or frontier.execution_id != execution_id
                or execution_record.thread_id != run_id
                or execution_record.invocation_id != invocation_id
                or execution_record.tool_call_id != frontier.tool_call_id
            ):
                raise WebError(
                    "deferred_resume_mismatch",
                    "Solver 执行记录与待恢复的 Agent 工具调用不一致。",
                )
            if execution_record.status not in {
                "completed",
                "failed",
                "cancelled",
                "timed_out",
            }:
                return

            invocation = self.invocations.load(run_id, invocation_id)
            if invocation.status in {"completed", "failed", "stopped"}:
                raise WebError(
                    "invocation_conflict",
                    "Solver 已完成，但对应 Agent 执行已处于终态。",
                )
            if invocation.status not in {
                "running",
                "waiting_for_tool_execution",
                "interrupted",
            }:
                raise WebError(
                    "invocation_conflict",
                    "Solver 已完成，但对应 Agent 执行状态不能自动恢复。",
                )

            invocation = self.invocations.update(
                invocation,
                status="running",
                started_at=invocation.started_at or datetime.now(UTC),
                resume_count=invocation.resume_count + 1,
                last_error=None,
            )

            async with open_coding_run(
                prepared.request,
                prepared.initial_snapshot,
                prepared.launch,
                api_key=self.api_key,
                verifier=ExecutionEvidenceVerifier(),
                clarification_provider=ScriptedClarificationProvider({}),
                resume=True,
                expected_amendment_sequence=prepared.amendment_sequence,
                observer=lambda event: self._publish(run_id, prepared, event),
                stream_model=self.config.stream_model,
                response_control=self._response_control,
                run_control=self._run_controls.get(run_id),
                model_factory=self.model_factory,
                runner_factory=self._runner_factory_for_invocation(
                    run_id, invocation.invocation_id
                ),
            ) as service:
                await service.run_step()

            with CodingRunFileLock(registration.request.state_dir):
                persisted = load_resume_state(registration.request.state_dir)
            with self._mutex:
                current_view = self._views.get(run_id)
            invocation = self.invocations.update(
                invocation,
                status=persisted.status,
                execution_task_revision=persisted.task_revision,
                execution_amendment_sequence=persisted.amendment_sequence,
                usage_after=current_view.usage if current_view is not None else None,
                finished_at=(
                    datetime.now(UTC)
                    if persisted.status in {"completed", "failed"}
                    else None
                ),
            )

        try:
            asyncio.run(self._cancellable_work(run_id, execute))
        except UserStoppedError:
            if invocation is not None and invocation.status not in {
                "completed",
                "failed",
                "stopped",
            }:
                with self._mutex:
                    current_view = self._views.get(run_id)
                invocation = self.invocations.update(
                    invocation,
                    status="stopped",
                    finished_at=datetime.now(UTC),
                    usage_after=current_view.usage
                    if current_view is not None
                    else None,
                )
        except Exception as exc:
            if invocation is not None and invocation.status not in {
                "completed",
                "failed",
                "stopped",
            }:
                status = "interrupted"
                execution_task_revision = invocation.execution_task_revision
                execution_amendment_sequence = invocation.execution_amendment_sequence
                try:
                    with CodingRunFileLock(registration.request.state_dir):
                        persisted = load_resume_state(registration.request.state_dir)
                    status = persisted.status
                    execution_task_revision = persisted.task_revision
                    execution_amendment_sequence = persisted.amendment_sequence
                except (
                    OSError,
                    ValueError,
                    CodingRunResumeError,
                    CodingRunAlreadyLocked,
                ):
                    pass
                with self._mutex:
                    current_view = self._views.get(run_id)
                invocation = self.invocations.update(
                    invocation,
                    status=status,
                    execution_task_revision=execution_task_revision,
                    execution_amendment_sequence=execution_amendment_sequence,
                    usage_after=current_view.usage
                    if current_view is not None
                    else None,
                    finished_at=(
                        datetime.now(UTC) if status in {"completed", "failed"} else None
                    ),
                    last_error=f"{type(exc).__name__}: {exc}",
                )
            logging.getLogger(__name__).exception(
                "Deferred Solver resume interrupted: %s", run_id
            )
        finally:
            with self._mutex:
                self._active = None
                self._response_control = None
                self.snapshot(run_id)
                self._launch_next_deferred_resume_locked()
            self.changed(run_id)

    async def _execute_solver_execution(
        self,
        execution: ToolExecution,
        runner: Abaqus2024Runner,
    ) -> AbaqusExecutionResult:
        run_id = execution.thread_id
        with self._mutex:
            current = self.tool_executions.load(run_id, execution.execution_id)
            if current.status == "starting":
                execution = self.tool_executions.update(
                    current,
                    status="running",
                    started_at=datetime.now(UTC),
                )
            elif current.status == "running":
                execution = current
            else:
                raise RuntimeError(
                    f"Solver execution cannot start from status {current.status}"
                )
        self.changed(run_id)

        process_started = getattr(runner, "set_solver_process_started_callback", None)
        if callable(process_started):
            process_started(
                lambda process_id: self._record_tool_process_started(
                    run_id, execution.execution_id, process_id
                )
            )
        try:
            result = await runner.run_solver_job(
                execution.job_name,
                input_file=execution.input_file,
                timeout_seconds=execution.timeout_seconds,
            )
        except asyncio.CancelledError:
            finished = datetime.now(UTC)
            cancelled_execution: ToolExecution | None = None
            with self._mutex:
                current = self.tool_executions.load(run_id, execution.execution_id)
                if current.status != "needs_reconcile":
                    cancelled_execution = self.tool_executions.update(
                        current,
                        status="needs_reconcile" if self._closing else "cancelled",
                        cancel_requested=current.cancel_requested or not self._closing,
                        finished_at=None if self._closing else finished,
                        elapsed_seconds=max(
                            current.elapsed_seconds,
                            (
                                finished - (current.started_at or current.created_at)
                            ).total_seconds(),
                        ),
                        error=(
                            "宿主关闭时终止了本地控制句柄；Solver 状态需要核查。"
                            if self._closing
                            else "Solver 执行已由宿主取消。"
                        ),
                    )
            self.changed(run_id)
            if (
                cancelled_execution is not None
                and cancelled_execution.tool_call_id is not None
                and cancelled_execution.status == "cancelled"
            ):
                self._queue_deferred_resume(run_id, execution.execution_id)
            raise
        except Exception as exc:
            finished = datetime.now(UTC)
            with self._mutex:
                current = self.tool_executions.load(run_id, execution.execution_id)
                self.tool_executions.update(
                    current,
                    status="failed",
                    finished_at=finished,
                    elapsed_seconds=max(
                        current.elapsed_seconds,
                        (
                            finished - (current.started_at or current.created_at)
                        ).total_seconds(),
                    ),
                    error=f"{type(exc).__name__}: {exc}",
                )
            self.changed(run_id)
            raise
        else:
            finished = datetime.now(UTC)
            result_json = result.model_dump_json()
            status = (
                "timed_out"
                if result.timed_out
                else "completed"
                if result.stage_succeeded
                else "failed"
            )
            with self._mutex:
                current = self.tool_executions.load(run_id, execution.execution_id)
                self.tool_executions.save_result(current, result)
                self.tool_executions.update(
                    current,
                    status=status,
                    finished_at=finished,
                    elapsed_seconds=max(
                        result.duration_seconds,
                        (
                            finished - (current.started_at or current.created_at)
                        ).total_seconds(),
                    ),
                    exit_code=result.exit_code,
                    result_succeeded=result.succeeded,
                    result_stage_succeeded=result.stage_succeeded,
                    result_sha256=hashlib.sha256(result_json.encode()).hexdigest(),
                    stdout_raw_path=str(result.stdout_raw_path),
                    stderr_raw_path=str(result.stderr_raw_path),
                    error=(
                        None
                        if status == "completed"
                        else "Solver 执行未通过当前阶段成功判定。"
                    ),
                )
            self.changed(run_id)
            return result
        finally:
            if callable(process_started):
                process_started(None)

    def _resolve_solver_execution(
        self,
        run_id: str,
        execution_id: str,
    ) -> DeferredSolverResolution:
        with self._mutex:
            execution = self.tool_executions.load(run_id, execution_id)
            result = self.tool_executions.load_result(run_id, execution_id)
        if execution.status in {"starting", "running"}:
            return DeferredSolverResolution(status=execution.status)
        if execution.status == "needs_reconcile":
            return DeferredSolverResolution(
                status="needs_reconcile", error=execution.error
            )
        if execution.status == "cancelled":
            return DeferredSolverResolution(status="cancelled", error=execution.error)
        if execution.status in {"completed", "failed", "timed_out"}:
            if result is None:
                return DeferredSolverResolution(
                    status="needs_reconcile",
                    error="Solver 已到终态，但缺少持久化执行结果。",
                )
            return DeferredSolverResolution(
                status=execution.status,
                result=result,
                error=execution.error,
            )
        raise AssertionError(f"unsupported ToolExecution status: {execution.status}")

    def _record_tool_process_started(
        self, run_id: str, execution_id: str, process_id: int
    ) -> None:
        with self._mutex:
            execution = self.tool_executions.load(run_id, execution_id)
            if execution.status not in {"starting", "running"}:
                raise WebError(
                    "tool_execution_not_active",
                    "Solver 进程启动时工具执行记录已不再活动。",
                    409,
                )
            self.tool_executions.update(execution, process_id=process_id)
        self.changed(run_id)

    def cancel_tool_execution(
        self,
        run_id: str,
        execution_id: str,
        command: ToolExecutionCancelCommand,
    ) -> ToolExecution:
        del command
        with self._mutex:
            self.registry.load(run_id)
            execution = self.tool_executions.load(run_id, execution_id)
            if execution.status in {
                "completed",
                "failed",
                "cancelled",
                "timed_out",
            }:
                return execution
            if execution.status == "needs_reconcile":
                raise WebError(
                    "tool_execution_needs_reconcile",
                    "Solver 执行状态未知，需要先核查外部进程和产物。",
                    409,
                )
            active = self._tool_tasks.get(execution_id)
            if active is None:
                execution = self.tool_executions.update(
                    execution,
                    status="needs_reconcile",
                    error="宿主找不到对应的活动任务；需要核查外部 Solver 状态。",
                )
                self.changed(run_id)
                raise WebError(
                    "tool_execution_control_unavailable",
                    "活动 Solver 控制句柄不存在，已转为待核查状态。",
                    409,
                )
            execution = self.tool_executions.update(
                execution,
                cancel_requested=True,
            )
            active.cancel()
        self.changed(run_id)
        return execution

    def list_tool_executions(self, run_id: str) -> ToolExecutionList:
        with self._mutex:
            self.registry.load(run_id)
            return ToolExecutionList(
                items=tuple(
                    self._tool_execution_view(execution)
                    for execution in self.tool_executions.list(run_id)
                )
            )

    def get_tool_execution(self, run_id: str, execution_id: str) -> ToolExecution:
        with self._mutex:
            self.registry.load(run_id)
            return self._tool_execution_view(
                self.tool_executions.load(run_id, execution_id)
            )

    def create(self, command: CreateRun) -> RunSnapshot:
        run_id = "web-" + command.idempotency_key.hex
        with self._mutex:
            if self.registry.deletion_pending(run_id):
                raise WebError("run_deleted", "此会话标识已删除，请新建会话。", 410)
            if self._closing:
                raise WebError("closing", "服务正在退出", 503)
            directory = self.registry.directory(run_id)
            if directory.exists():
                saved = self.registry.load(run_id)
                if saved.request_digest != digest(command):
                    raise WebError(
                        "idempotency_conflict", "相同创建标识不能提交不同目标"
                    )
                return self.snapshot(run_id)
            workspace = (
                selected_workspace(command.workspace, self.registry.root)
                if command.workspace is not None
                else plain_path(
                    self.registry.root / "workspaces" / run_id, directory=True
                )
            )
            conflict = self._workspace_conflict(workspace)
            if conflict:
                raise WebError("workspace_busy", conflict)
            directory.mkdir()
            state = self.registry.state_dir(run_id)
            if command.workspace is None:
                workspace.mkdir()
            state.mkdir()
            controls = self.config.request_controls()
            base_runtime_policy = controls["runtime_policy"]
            runtime_updates = {}
            if command.abaqus_permission is not None:
                runtime_updates["abaqus"] = command.abaqus_permission
            if command.solver_permission is not None:
                runtime_updates["solver"] = command.solver_permission
            if runtime_updates:
                controls["runtime_policy"] = base_runtime_policy.model_copy(
                    update=runtime_updates
                )
            prepared = prepare_new_run(
                run_id=run_id,
                goal=command.goal,
                workspace=workspace,
                state_dir=state,
                execution=self.config.execution,
                runtime=self.config.runtime,
                **controls,
                knowledge_pack_ids=command.knowledge_pack_ids,
                knowledge_packs_root=self.config.knowledge_packs_root,
            )
            with CodingRunFileLock(state):
                registration = Registration(
                    workspace_kind="selected"
                    if command.workspace is not None
                    else "managed",
                    request_digest=digest(command),
                    created_at=datetime.now(UTC),
                    request=prepared.request,
                    initial_snapshot=prepared.initial_snapshot,
                    launch=prepared.launch,
                )
                save_model(directory / "registration.json", registration)
                self.threads.initialize(run_id, created_at=registration.created_at)
        return self.snapshot(run_id)

    def _workspace_conflict(
        self, workspace: Path, run_id: str | None = None
    ) -> str | None:
        """在 Web 调度锁内检查重叠目录的其他会话，不提供跨进程工作区锁。"""
        if workspace.is_relative_to(self.registry.root):
            return None  # Managed run folders are disjoint; selected roots cannot overlap them.
        for peer_id in self.registry.ids():
            if peer_id == run_id:
                continue
            try:
                peer = self.registry.load(peer_id)
            except (ValueError, OSError, WebError):
                continue
            other = peer.request.workspace
            if not (
                workspace == other
                or workspace.is_relative_to(other)
                or other.is_relative_to(workspace)
            ):
                continue
            if self._active == peer_id:
                return "这个工作区的另一会话正在执行，请等待它结束。"
            if self.deletions.stop_pending(peer_id):
                return "这个工作区的另一会话尚未完成删除收尾。"
            try:
                executions = self.tool_executions.list(peer_id)
                resume_path = peer.request.state_dir / "resume_state.json"
                resume = (
                    load_resume_state(peer.request.state_dir)
                    if resume_path.exists()
                    else None
                )
            except (ValueError, OSError, WebError):
                return "这个工作区的另一会话记录不可用，请先核查。"
            if any(
                item.status in {"starting", "running", "needs_reconcile"}
                for item in executions
            ) or (resume is not None and resume.status == "needs_reconcile"):
                return "这个工作区的另一会话仍有未结束或待核查的执行。"
        return None

    def list_knowledge_packs(self) -> DomainKnowledgeInventory:
        return discover_domain_knowledge_packs(self.config.knowledge_packs_root)

    def _prepared(self, registration: Registration) -> PreparedCodingRun:
        if (registration.request.state_dir / "request.json").exists():
            base = CodingRunRequest.model_validate_json(
                (registration.request.state_dir / "request.json").read_bytes()
            )
            if base != registration.request:
                raise WebError("base_changed", "不可变任务请求与Web登记不一致")
            prepared = prepare_resume(registration.request.state_dir)
        else:
            prepared = PreparedCodingRun(
                registration.request, registration.initial_snapshot, registration.launch
            )
        thread = self.threads.load(registration.request.run_id)
        if thread is None:
            return prepared
        return replace(
            prepared,
            conversation_messages=tuple(
                ConversationMessage(
                    message_id=item.message_id,
                    text=item.text,
                    submitted_after_turn=item.submitted_after_turn,
                )
                for item in thread.messages
            ),
        )

    def _ensure_thread(self, registration: Registration) -> None:
        run_id = registration.request.run_id
        if self.threads.load(run_id) is not None:
            return
        self.threads.initialize(run_id, created_at=registration.created_at)
        if not (registration.request.state_dir / "request.json").is_file():
            return
        effective = load_effective_run_for_resume(registration.request.state_dir)
        for amendment in effective.ledger.amendments:
            if (
                amendment.message_id is None
                or amendment.goal is None
                or amendment.submitted_after_turn is None
            ):
                continue
            self.threads.append_message(
                run_id,
                message_id=amendment.message_id,
                text=amendment.goal,
                submitted_after_turn=amendment.submitted_after_turn,
                base_task_revision=amendment.task_revision - 1,
                base_amendment_sequence=amendment.sequence - 1,
                created_at=amendment.created_at,
            )
            self.threads.mark_execution(
                run_id,
                amendment.message_id,
                task_revision=amendment.task_revision,
                amendment_sequence=amendment.sequence,
            )

    def _cached(self, run_id: str, registration: Registration) -> CodingRunView:
        if run_id in self._views:
            return self._views[run_id]
        file = plain_path(
            self.registry.directory(run_id) / "view.json", directory=False
        )
        if file.exists():
            view = CodingRunView.model_validate_json(file.read_bytes())
            if view.run_id != run_id:
                raise WebError("view_corrupt", "展示记录与任务不一致")
            return view
        return CodingRunView(run_id=run_id, goal=registration.request.goal)

    def _refresh(self, run_id: str, registration: Registration) -> CodingRunView:
        state = registration.request.state_dir
        lock_path = plain_path(state / "coding_run.lock", directory=False)
        if not lock_path.is_file() or lock_path.stat().st_size == 0:
            raise WebError("state_corrupt", "任务锁记录缺失")
        with CodingRunFileLock(state) as lock:
            prepared = self._prepared(registration)
            self._effective[run_id] = prepared
            view = read_coding_view(prepared, lock)
        if view.status == "running":
            view = view.model_copy(
                update={
                    "status": "interrupted",
                    "phase": "execution_unknown",
                    "error": "原工作段已不在本服务运行，继续前将由宿主核对。",
                }
            )
        self._views[run_id] = view
        return view

    def snapshot(self, run_id: str) -> RunSnapshot:
        """活动段只读内存；空闲读取在state锁内，不触发恢复或改写产品记录。"""
        with self._mutex:
            registration = self.registry.load(run_id)
            active = self._active == run_id
            display = self._display(run_id, registration)
            external_busy = False
            try:
                view = (
                    self._cached(run_id, registration)
                    if active
                    else self._refresh(run_id, registration)
                )
            except CodingRunAlreadyLocked:
                view = self._cached(run_id, registration).model_copy(
                    update={
                        "phase": "external_execution",
                        "error": "任务被其他进程占用。",
                    }
                )
                external_busy = True
            except (OSError, ValueError, WebError, CodingRunResumeError):
                view = CodingRunView(
                    run_id=run_id,
                    goal=registration.request.goal,
                    status="unavailable",
                    phase="unavailable",
                    error="宿主记录缺失、损坏或与任务不一致，请检查本地记录。",
                )
            if active:
                view = view.model_copy(update={"status": "running"})
            pending_message = self.threads.pending(run_id)
            tool_execution = self._tool_execution_snapshot(run_id)
            tool_needs_reconcile = bool(
                tool_execution is not None
                and tool_execution.status == "needs_reconcile"
            )
            receipt_dir = plain_path(
                self.registry.directory(run_id) / "commands", directory=True
            )
            uncertain = False
            receipt_error = None
            if receipt_dir.exists() and not active:
                try:
                    receipts = [
                        CommandReceipt.model_validate_json(
                            plain_path(p, directory=False).read_bytes()
                        )
                        for p in receipt_dir.glob("*.json")
                    ]
                except (ValueError, OSError, WebError):
                    receipts = []
                    view = view.model_copy(
                        update={
                            "status": "unavailable",
                            "phase": "unavailable",
                            "error": "命令回执损坏，当前任务不能继续。",
                            "outcome": None,
                            "verification": None,
                            "files": (),
                        }
                    )
                uncertain = any(
                    r.state == "accepted"
                    and (
                        view.status not in {"completed", "failed"}
                        or (
                            r.message_revision is not None
                            and r.message_revision > view.task_revision
                        )
                    )
                    for r in receipts
                )
                receipt_error = next((r.error for r in receipts if r.error), None)
            if (
                receipt_error
                and view.status in {"registered", "interrupted"}
                and view.phase != "stopped"
            ):
                view = view.model_copy(update={"error": receipt_error})
            workspace_conflict = self._workspace_conflict(
                registration.request.workspace, run_id
            )
            deletion_stopping = self.deletions.stop_pending(run_id)
            free = (
                not active
                and not external_busy
                and self._active is None
                and not self._closing
                and workspace_conflict is None
                and run_id not in self._deleting
                and not deletion_stopping
            )
            usable = view.status != "unavailable"
            launch = (
                self._effective[run_id].launch
                if run_id in self._effective
                else registration.launch
            )
            can_start = (
                free and usable and view.status == "registered" and not uncertain
            )
            can_answer = (
                free
                and usable
                and not tool_needs_reconcile
                and view.status == "waiting_for_user"
                and bool(view.question and view.question.pending)
            )
            can_continue = (
                free
                and usable
                and not tool_needs_reconcile
                and (view.status == "interrupted" or pending_message is not None)
                and not (view.question and view.question.pending)
            )
            request = (
                self._effective[run_id].request
                if run_id in self._effective
                else registration.request
            )
            message_block = None
            if run_id in self._deleting or deletion_stopping:
                message_block = "会话删除尚未完成，请重试删除。"
            elif launch.schema_version != "coding-run-launch-v7":
                message_block = "旧会话不支持追加消息，请新建会话。"
            elif tool_needs_reconcile:
                message_block = "Solver 外部执行状态未知，请先核查运行状态与产物。"
            elif not usable or view.status == "needs_reconcile":
                message_block = "请先核查宿主记录或未决执行。"
            elif workspace_conflict:
                message_block = workspace_conflict
            elif not free:
                message_block = "正在处理，请等待当前回复结束。"
            elif view.question and view.question.pending:
                message_block = "请先回复当前问题。"
            elif view.status == "registered":
                message_block = "首条消息尚未开始处理。"
            elif pending_message is not None:
                message_block = "请先显式继续已保存的消息。"
            elif uncertain or view.phase in {"revision_pending", "execution_unknown"}:
                message_block = "请先显式继续已保存的消息或核查上次执行。"
            elif (
                request.limits.request_limit is not None
                and view.usage.model_requests >= request.limits.request_limit
            ) or (
                request.limits.active_wall_seconds is not None
                and view.usage.wall_time_seconds >= request.limits.active_wall_seconds
            ):
                message_block = "会话预算已用尽；可新建会话，或在CLI中显式调整预算。"
            value = RunSnapshot(
                view=view,
                version="",
                created_at=registration.created_at,
                model=f"{launch.model_provider.value}/{launch.model_name}",
                workspace=str(registration.request.workspace),
                title=display.title,
                title_version=display.version,
                reasoning_policy=launch.effective_execution().reasoning_policy,
                limits=request.limits,
                runtime_policy=request.runtime_policy,
                knowledge_packs=(
                    launch.domain_knowledge.packs
                    if launch.domain_knowledge is not None
                    else ()
                ),
                can_start=can_start,
                can_answer=can_answer,
                can_continue=can_continue,
                can_message=message_block is None
                and view.status in {"completed", "failed", "interrupted"},
                message_block_reason=message_block,
                active=active or external_busy,
                command_uncertain=uncertain,
                model_request_id=self._response_control.request_id
                if active and self._response_control
                else None,
                can_stop=bool(
                    active
                    and self._response_control
                    and self._response_control.request_id
                ),
                stop_requested=bool(
                    active
                    and self._response_control
                    and self._response_control.requested
                ),
                tool_execution=tool_execution,
                can_cancel_tool_execution=bool(
                    tool_execution is not None
                    and tool_execution.status in {"starting", "running"}
                    and tool_execution.execution_id in self._tool_tasks
                ),
                context_usage=ContextUsage(
                    latest_input_tokens=view.latest_model_input_tokens,
                    warning_threshold_tokens=self.config.context_warning_threshold_tokens,
                    warning=bool(
                        view.latest_model_input_tokens is not None
                        and self.config.context_warning_threshold_tokens is not None
                        and view.latest_model_input_tokens
                        >= self.config.context_warning_threshold_tokens
                    ),
                ),
            )
            return value.model_copy(
                update={
                    "version": hashlib.sha256(
                        value.model_dump_json().encode()
                    ).hexdigest()
                }
            )

    def list_runs(self) -> RunList:
        runs, unavailable = [], []
        for run_id in self.registry.ids():
            try:
                runs.append(self.snapshot(run_id))
            except (ValueError, OSError, WebError):
                unavailable.append(
                    RunIssue(run_id=run_id, message="任务登记不可用，请检查本地记录。")
                )
        unavailable.extend(
            RunIssue(
                run_id=item.run_id,
                message="会话删除尚未完成，可重试清理记录。",
                can_retry_delete=True,
            )
            for item in self.deletions.incomplete()
        )
        return RunList(
            runs=tuple(sorted(runs, key=lambda x: x.created_at, reverse=True)),
            unavailable=tuple(unavailable),
        )

    async def _cancellable_work(
        self, run_id: str, execute: Callable[[], Awaitable[None]]
    ) -> None:
        task = asyncio.current_task()
        assert task is not None
        with self._mutex:
            if run_id in self._deleting:
                raise asyncio.CancelledError("会话正在删除")
            self._work_tasks[run_id] = (asyncio.get_running_loop(), task)
            self._run_controls[run_id] = RunCancellationControl()
            self._cancelled_for_delete.discard(run_id)
            self._delete_errors.pop(run_id, None)
        try:
            await execute()
        except Exception as exc:
            with self._mutex:
                control = self._run_controls.get(run_id)
                if control is not None and control.requested:
                    self._delete_errors[run_id] = f"执行收尾失败：{type(exc).__name__}"
            raise
        finally:
            with self._mutex:
                control = self._run_controls.get(run_id)
                if (
                    control is not None
                    and control.requested
                    and run_id not in self._delete_errors
                ):
                    self._cancelled_for_delete.add(run_id)
                self._work_tasks.pop(run_id, None)
                self._run_controls.pop(run_id, None)

    async def _tracked_solver_execution(
        self, execution: ToolExecution, runner: Abaqus2024Runner
    ) -> AbaqusExecutionResult:
        task = asyncio.current_task()
        assert task is not None
        with self._mutex:
            self._solver_tasks[execution.execution_id] = task
        try:
            with self._mutex:
                if execution.thread_id in self._deleting:
                    self.tool_executions.update(
                        execution,
                        status="cancelled",
                        cancel_requested=True,
                        finished_at=datetime.now(UTC),
                    )
                    raise asyncio.CancelledError("会话正在删除")
            return await self._execute_solver_execution(execution, runner)
        except Exception as exc:
            with self._mutex:
                current = self.tool_executions.load(
                    execution.thread_id, execution.execution_id
                )
                if execution.thread_id in self._deleting or current.cancel_requested:
                    self._delete_errors[execution.thread_id] = (
                        f"Solver 收尾失败：{type(exc).__name__}"
                    )
            raise
        finally:
            with self._mutex:
                self._solver_tasks.pop(execution.execution_id, None)

    async def _finish_solvers_for_deletion(self, run_id: str) -> None:
        with self._mutex:
            tasks = []
            for item in self.tool_executions.list(run_id):
                if item.execution_id in self._solver_tasks:
                    self.tool_executions.update(item, cancel_requested=True)
                    tasks.append(self._solver_tasks[item.execution_id])
        for task in tasks:
            if not task.done() and not task.cancelling():
                task.cancel("删除会话")
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    def delete(self, run_id: str, command: RunCommand) -> DeleteRunResponse:
        """等待当前宿主的执行和清理结束，再永久删除会话记录；工程文件保留。"""
        del command  # DELETE is idempotent by immutable run identity.
        with self._mutex:
            if self._closing:
                raise WebError("closing", "服务正在退出，请稍后重试。", 503)
            receipt = self.deletions.load(run_id)
            if receipt is not None:
                if receipt.state != "deleted":
                    try:
                        receipt = self.deletions.purge(run_id, receipt.workspace)
                    except (OSError, CodingRunAlreadyLocked) as exc:
                        raise WebError(
                            "delete_incomplete", "会话记录尚未清理完成，请重试。"
                        ) from exc
                return DeleteRunResponse(run_id=run_id, workspace=receipt.workspace)
            if run_id in self._deleting:
                raise WebError("run_deleting", "会话正在删除，请稍后重试。")
            if run_id in self._delete_errors:
                raise WebError(
                    "delete_cleanup_failed",
                    self._delete_errors[run_id] + "；记录未删除。",
                )
            registration = self.registry.load(run_id)
            snapshot = self.snapshot(run_id)
            owned_work = self._active == run_id
            if (
                self.deletions.stop_pending(run_id)
                and not owned_work
                and run_id not in self._cancelled_for_delete
            ):
                raise WebError(
                    "delete_unresolved", "上次删除未能确认执行已结束，记录已保留。"
                )
            if snapshot.active and not owned_work:
                raise WebError("delete_busy", "会话正由其他宿主使用，暂时无法删除。")
            if not owned_work and snapshot.view.status in {
                "needs_reconcile",
                "unavailable",
            }:
                raise WebError(
                    "delete_unresolved", "无法确认会话的执行状态，记录未删除。"
                )
            if not owned_work and run_id not in self._cancelled_for_delete:
                if snapshot.command_uncertain:
                    raise WebError(
                        "delete_unresolved", "执行状态尚未确认，记录未删除。"
                    )
                self.deletions.check_inactive_execution(registration.request)
            for execution in self.tool_executions.list(run_id):
                if (
                    execution.status == "needs_reconcile"
                    or (
                        execution.status in {"starting", "running"}
                        and execution.execution_id not in self._tool_tasks
                        and execution.execution_id not in self._solver_tasks
                    )
                    or (
                        execution.status in {"failed", "timed_out"}
                        and execution.result_sha256 is None
                    )
                ):
                    raise WebError(
                        "delete_unresolved", "无法确认外部执行是否结束，记录未删除。"
                    )
            if owned_work or any(
                item.status in {"starting", "running"}
                for item in self.tool_executions.list(run_id)
            ):
                self.deletions.begin_stop(run_id)
            self._deleting.add(run_id)
            self._deferred_resume_queue = {
                key: value
                for key, value in self._deferred_resume_queue.items()
                if value[0] != run_id
            }
            work = self._future if owned_work else None
            current = self._work_tasks.get(run_id)
            control = self._run_controls.get(run_id)
            native_cancel = control.request_cancel() if control is not None else False
            if current is not None and not native_cancel:
                loop, task = current

                def cancel_work() -> None:
                    if not task.done() and not task.cancelling():
                        task.cancel("删除会话")

                loop.call_soon_threadsafe(cancel_work)
            solver_loop = self._tool_loop
            solver_cleanup = (
                asyncio.run_coroutine_threadsafe(
                    self._finish_solvers_for_deletion(run_id), solver_loop
                )
                if solver_loop is not None
                else None
            )
        self.changed(run_id)
        deadline = time.monotonic() + DELETE_STOP_TIMEOUT_SECONDS
        try:
            if solver_cleanup is not None:
                solver_cleanup.result(timeout=max(0.0, deadline - time.monotonic()))
            if work is not None:
                work.result(timeout=max(0.0, deadline - time.monotonic()))
            with self._mutex:
                if run_id in self._delete_errors:
                    raise WebError(
                        "delete_cleanup_failed",
                        self._delete_errors[run_id] + "；记录未删除，请检查后重试。",
                    )
                self.deletions.confirm_stopped(run_id)
                self.deletions.purge(run_id, str(registration.request.workspace))
                self._views.pop(run_id, None)
                self._effective.pop(run_id, None)
                self._cancelled_for_delete.discard(run_id)
            return DeleteRunResponse(
                run_id=run_id, workspace=str(registration.request.workspace)
            )
        except TimeoutError as exc:
            raise WebError(
                "delete_pending", "执行仍在结束中，记录未删除，请稍后重试。"
            ) from exc
        except (OSError, CodingRunAlreadyLocked) as exc:
            raise WebError(
                "delete_incomplete",
                "会话记录尚未清理完成，可能被其他进程占用，请重试。",
            ) from exc
        finally:
            with self._mutex:
                self._deleting.discard(run_id)
            self.changed(run_id)

    def _display(self, run_id: str, registration: Registration) -> DisplayMetadata:
        path = plain_path(
            self.registry.directory(run_id) / "display.json", directory=False
        )
        return (
            DisplayMetadata.model_validate_json(path.read_bytes())
            if path.exists()
            else DisplayMetadata(title=registration.request.goal)
        )

    def rename(self, run_id: str, command: RenameCommand) -> RunSnapshot:
        with self._mutex:
            registration = self.registry.load(run_id)
            display = self._display(run_id, registration)
            payload_hash = digest(command)
            key = command.command_id.hex
            if key in display.receipts:
                if display.receipts[key] != payload_hash:
                    raise WebError("command_conflict", "相同标识不能提交不同名称")
                return self.snapshot(run_id)
            if display.version != command.expected_title_version:
                raise WebError("title_conflict", "名称已在其他页面修改，请刷新后重试")
            updated = DisplayMetadata(
                title=command.title,
                version=display.version + 1,
                receipts={**display.receipts, key: payload_hash},
            )
            save_model(self.registry.directory(run_id) / "display.json", updated)
            self.changed(run_id)
            return self.snapshot(run_id)

    def stop(self, run_id: str, command: StopCommand) -> CommandResponse:
        with self._mutex:
            self.registry.load(run_id)
            path = self._receipt_path(run_id, command)
            payload_hash = digest(command)
            if path.exists():
                prior = CommandReceipt.model_validate_json(path.read_bytes())
                if prior.action != "stop" or prior.digest != payload_hash:
                    raise WebError("command_conflict", "停止标识与原命令不一致")
                return CommandResponse(
                    command_id=command.command_id,
                    execution_accepted=False,
                    answer_saved=False,
                    replayed=True,
                    error=prior.error,
                    snapshot=self.snapshot(run_id),
                )
            control = self._response_control
            if (
                self._active != run_id
                or control is None
                or (
                    control.request_id != command.expected_request_id
                    and not control.requested
                )
            ):
                raise WebError("stop_conflict", "当前不在可停止的模型回复阶段，请刷新")
            receipt = CommandReceipt(
                command_id=command.command_id,
                digest=payload_hash,
                action="stop",
                state="accepted",
            )
            save_model(path, receipt)
            if not control.stop(command.expected_request_id):
                save_model(
                    path,
                    receipt.model_copy(
                        update={
                            "state": "rejected",
                            "error": "模型阶段已改变，本次停止未执行。",
                        }
                    ),
                )
                raise WebError("stop_conflict", "模型阶段已改变，请刷新")
            save_model(path, receipt.model_copy(update={"state": "settled"}))
            self.changed(run_id)
            return CommandResponse(
                command_id=command.command_id,
                execution_accepted=False,
                answer_saved=False,
                snapshot=self.snapshot(run_id),
            )

    def _receipt_path(self, run_id: str, command: RunCommand) -> Path:
        return plain_path(
            self.registry.directory(run_id)
            / "commands"
            / f"{command.command_id.hex}.json",
            directory=False,
        )

    def command(
        self,
        run_id: str,
        action: Literal["start", "answer", "continue", "message"],
        command: RunCommand,
    ) -> CommandResponse:
        with self._mutex:
            registration = self.registry.load(run_id)
            path = self._receipt_path(run_id, command)
            request_digest = hashlib.sha256(
                (action + command.model_dump_json()).encode()
            ).hexdigest()
            if path.exists():
                receipt = CommandReceipt.model_validate_json(path.read_bytes())
                if receipt.digest != request_digest or receipt.action != action:
                    raise WebError("command_conflict", "相同命令标识不能用于不同操作")
                snapshot = self.snapshot(run_id)
                saved_message = self._saved_message(run_id, command)
                accepted = receipt.state == "settled" or (
                    receipt.state == "accepted"
                    and (
                        snapshot.active
                        or (
                            snapshot.view.status in {"completed", "failed"}
                            and receipt.message_revision is not None
                            and receipt.message_revision <= snapshot.view.task_revision
                        )
                    )
                )
                return CommandResponse(
                    command_id=command.command_id,
                    execution_accepted=accepted,
                    answer_saved=receipt.answer_saved,
                    message_saved=receipt.message_saved or saved_message is not None,
                    replayed=True,
                    error=receipt.error
                    or (
                        "执行回执尚未完成，请检查状态后显式继续。"
                        if not accepted and receipt.state == "accepted"
                        else None
                    ),
                    snapshot=snapshot,
                )
            snapshot = self.snapshot(run_id)
            saved_message = self._saved_message(run_id, command)
            if action == "message" and saved_message is not None:
                return CommandResponse(
                    command_id=command.command_id,
                    execution_accepted=bool(
                        saved_message.execution_task_revision is not None
                        and saved_message.execution_task_revision
                        <= snapshot.view.task_revision
                    ),
                    answer_saved=False,
                    message_saved=True,
                    replayed=True,
                    snapshot=snapshot,
                )
            allowed = {
                "start": snapshot.can_start,
                "answer": snapshot.can_answer,
                "continue": snapshot.can_continue,
                "message": snapshot.can_message,
            }[action]
            if action == "answer" and isinstance(command, AnswerCommand):
                q = snapshot.view.question
                if q is None or q.question_id != command.question_id:
                    raise WebError("question_conflict", "问题已改变，请刷新后回答")
                if q.answer is not None:
                    if q.answer != command.answer:
                        raise WebError("answer_conflict", "已保存的答案不能覆盖")
                    return CommandResponse(
                        command_id=command.command_id,
                        answer_saved=True,
                        execution_accepted=False,
                        replayed=True,
                        snapshot=snapshot,
                    )
                if snapshot.view.task_revision != command.expected_task_revision:
                    raise WebError("revision_conflict", "任务修订已改变，请刷新")
            if not allowed:
                raise WebError(
                    "state_conflict", "当前状态不允许此操作，或另一个任务正在运行"
                )
            if not self.api_key.get_secret_value():
                raise WebError(
                    "model_not_configured", "启动服务前请配置模型API凭据", 503
                )
            receipt = CommandReceipt(
                command_id=command.command_id,
                digest=request_digest,
                action=action,
                state="accepted",
            )
            save_model(path, receipt)
            if action == "message" and isinstance(command, MessageCommand):
                try:
                    with CodingRunFileLock(registration.request.state_dir):
                        state = load_resume_state(registration.request.state_dir)
                        if (
                            state.task_revision != command.expected_task_revision
                            or state.amendment_sequence
                            != command.expected_amendment_sequence
                            or state.status
                            not in {"completed", "failed", "interrupted"}
                        ):
                            raise ValueError("conversation changed before message save")
                        self._ensure_thread(registration)
                        self.threads.append_message(
                            run_id,
                            message_id=command.command_id.hex,
                            text=command.text,
                            submitted_after_turn=state.turn,
                            base_task_revision=command.expected_task_revision,
                            base_amendment_sequence=command.expected_amendment_sequence,
                        )
                except (
                    OSError,
                    ValueError,
                    CodingRunResumeError,
                    CodingRunAlreadyLocked,
                ) as exc:
                    save_model(
                        path,
                        receipt.model_copy(
                            update={
                                "state": "rejected",
                                "error": "会话状态已改变，消息未保存。请刷新后重试。",
                            }
                        ),
                    )
                    raise WebError(
                        "message_conflict", "会话状态已改变，消息未保存。请刷新后重试。"
                    ) from exc
                receipt = receipt.model_copy(update={"message_saved": True})
                save_model(path, receipt)
                snapshot = self.snapshot(run_id)
            if action == "answer" and isinstance(command, AnswerCommand):
                try:
                    asyncio.run(
                        answer_question(
                            registration.request.state_dir,
                            UserAnswerInput(
                                question_id=command.question_id, answer=command.answer
                            ),
                            expected_task_revision=command.expected_task_revision,
                        )
                    )
                except (
                    OSError,
                    ValueError,
                    CodingRunResumeError,
                    CodingRunAlreadyLocked,
                ) as exc:
                    save_model(
                        path,
                        receipt.model_copy(
                            update={
                                "state": "rejected",
                                "error": "答复未保存，请刷新后重试。",
                            }
                        ),
                    )
                    raise WebError(
                        "answer_conflict", "答复未保存，请刷新后重试。"
                    ) from exc
                receipt = receipt.model_copy(update={"answer_saved": True})
                save_model(path, receipt)
                snapshot = self.snapshot(run_id)
            self._views[run_id] = snapshot.view
            self._active = run_id
            self._response_control = ModelResponseControl()
            try:
                self._future = self._pool.submit(
                    self._work, run_id, registration, action, command, receipt
                )
            except Exception:
                self._active = None
                self._response_control = None
                save_model(
                    path,
                    receipt.model_copy(
                        update={
                            "state": "rejected",
                            "error": "任务调度失败，未启动执行",
                        }
                    ),
                )
                if receipt.message_saved or receipt.answer_saved:
                    return CommandResponse(
                        command_id=command.command_id,
                        execution_accepted=False,
                        answer_saved=receipt.answer_saved,
                        message_saved=receipt.message_saved,
                        error="输入已保存但未启动，请点击继续。",
                        snapshot=self.snapshot(run_id),
                    )
                raise
            return CommandResponse(
                command_id=command.command_id,
                execution_accepted=True,
                answer_saved=receipt.answer_saved,
                message_saved=receipt.message_saved,
                snapshot=self.snapshot(run_id),
            )

    def _saved_message(self, run_id: str, command: RunCommand) -> ThreadMessage | None:
        if not isinstance(command, MessageCommand):
            return None
        thread = self.threads.load(run_id)
        if thread is None:
            return None
        existing = next(
            (m for m in thread.messages if m.message_id == command.command_id.hex), None
        )
        if existing is not None and (
            existing.text != command.text
            or existing.base_task_revision != command.expected_task_revision
            or existing.base_amendment_sequence != command.expected_amendment_sequence
        ):
            raise WebError("command_conflict", "相同消息标识不能提交不同内容")
        return existing

    def _publish(
        self, run_id: str, prepared: PreparedCodingRun, event: CodingProgress
    ) -> None:
        with self._mutex:
            view = project_checkpoint(prepared, event, self._views.get(run_id))
            self._views[run_id] = view
            # This is a derived display cache outside the model workspace; it owns no outcome.
            if event.kind == "checkpoint":
                save_model(self.registry.directory(run_id) / "view.json", view)
        self.changed(run_id)

    def _new_invocation(
        self,
        run_id: str,
        registration: Registration,
        prepared: PreparedCodingRun,
        action: Literal["start", "answer", "continue", "message"],
        command: RunCommand,
        thread_message: ThreadMessage | None,
        *,
        base_task_revision: int,
        base_amendment_sequence: int,
    ) -> AgentInvocation:
        now = datetime.now(UTC)
        with self._mutex:
            current_view = self._views.get(run_id)
        invocation = AgentInvocation(
            invocation_id=uuid4().hex,
            thread_id=run_id,
            source_message_id=(
                thread_message.message_id if thread_message is not None else None
            ),
            source_message_sequence=(
                thread_message.sequence if thread_message is not None else None
            ),
            origin_action=action,
            origin_command_id=command.command_id.hex,
            created_at=now,
            updated_at=now,
            status="created",
            base_task_revision=base_task_revision,
            base_amendment_sequence=base_amendment_sequence,
            provider=prepared.launch.model_provider.value,
            model=prepared.launch.model_name,
            limits=prepared.request.limits,
            runtime_policy=prepared.request.runtime_policy,
            step_persistence_reference=STEP_PERSISTENCE_DIRECTORY,
            compatibility_continuation_id=(
                thread_message.message_id if thread_message is not None else None
            ),
            usage_before=(
                current_view.usage
                if current_view is not None
                else CodingRunView(run_id=run_id, goal=registration.request.goal).usage
            ),
        )
        return self.invocations.create(invocation)

    def _invocation_for_work(
        self,
        run_id: str,
        registration: Registration,
        prepared: PreparedCodingRun,
        action: Literal["start", "answer", "continue", "message"],
        command: RunCommand,
        thread_message: ThreadMessage | None,
        *,
        base_task_revision: int,
        base_amendment_sequence: int,
    ) -> AgentInvocation:
        existing = (
            self.invocations.latest_for_message(run_id, thread_message.message_id)
            if thread_message is not None
            else self.invocations.latest_resumable(run_id)
            if action in {"answer", "continue"}
            else None
        )
        if existing is None:
            created = self._new_invocation(
                run_id,
                registration,
                prepared,
                action,
                command,
                thread_message,
                base_task_revision=base_task_revision,
                base_amendment_sequence=base_amendment_sequence,
            )
            return self.invocations.update(
                created,
                status="running",
                started_at=datetime.now(UTC),
            )
        if existing.status in {"completed", "failed", "stopped"}:
            return existing
        return self.invocations.update(
            existing,
            status="running",
            started_at=existing.started_at or datetime.now(UTC),
            resume_count=existing.resume_count
            + (1 if existing.started_at is not None else 0),
            last_error=None,
        )

    def _runner_factory_for_invocation(
        self, run_id: str, invocation_id: str
    ) -> RunnerFactory:
        base_factory = self.runner_factory

        def factory(workspace: Path, state_dir: Path, **kwargs) -> Abaqus2024Runner:
            base = base_factory(workspace, state_dir, **kwargs)
            managed = _ToolExecutionRunner(base, self, run_id, invocation_id)
            return cast(Abaqus2024Runner, managed)

        return factory

    def _work(
        self,
        run_id: str,
        registration: Registration,
        action: Literal["start", "answer", "continue", "message"],
        command: RunCommand,
        receipt: CommandReceipt,
    ) -> None:
        path = self._receipt_path(run_id, command)
        invocation: AgentInvocation | None = None

        async def execute() -> None:
            nonlocal invocation, receipt
            thread_message = (
                self._saved_message(run_id, command)
                if action == "message"
                else self.threads.pending(run_id)
                if action == "continue"
                else None
            )
            # Restore preparation also runs under the same state lock; the service reacquires it and revalidates.
            with CodingRunFileLock(registration.request.state_dir):
                prepared = self._prepared(registration)
                resume_path = registration.request.state_dir / "resume_state.json"
                persisted_before = (
                    load_resume_state(registration.request.state_dir)
                    if resume_path.is_file()
                    else None
                )
            if action == "start" and prepared.resume:
                raise WebError("already_started", "已有运行记录不能作为新任务重启")
            if (
                prepared.launch.question_protocol != "ask_user_v1"
                or prepared.launch.model_provider
                != self.config.execution.model_provider
            ):
                raise WebError("profile_mismatch", "服务配置与已保存的运行不匹配")

            if (
                thread_message is not None
                and persisted_before is not None
                and persisted_before.continuation_id == thread_message.message_id
                and persisted_before.status in {"completed", "failed"}
            ):
                invocation = self.invocations.latest_for_message(
                    run_id, thread_message.message_id
                )
                if invocation is None:
                    invocation = self._new_invocation(
                        run_id,
                        registration,
                        prepared,
                        action,
                        command,
                        thread_message,
                        base_task_revision=thread_message.base_task_revision,
                        base_amendment_sequence=thread_message.base_amendment_sequence,
                    )
                if invocation.status not in {"completed", "failed"}:
                    with self._mutex:
                        current_view = self._views.get(run_id)
                    invocation = self.invocations.update(
                        invocation,
                        status=persisted_before.status,
                        started_at=invocation.started_at or invocation.created_at,
                        finished_at=datetime.now(UTC),
                        execution_task_revision=persisted_before.task_revision,
                        execution_amendment_sequence=persisted_before.amendment_sequence,
                        usage_after=(
                            current_view.usage if current_view is not None else None
                        ),
                    )
                with self._mutex:
                    self.threads.mark_execution(
                        run_id,
                        thread_message.message_id,
                        task_revision=persisted_before.task_revision,
                        amendment_sequence=persisted_before.amendment_sequence,
                        continuation_id=thread_message.message_id,
                        invocation_id=invocation.invocation_id,
                    )
                    receipt = receipt.model_copy(
                        update={
                            "message_saved": True,
                            "message_revision": persisted_before.task_revision,
                        }
                    )
                    save_model(path, receipt)
                return

            if thread_message is not None:
                base_task_revision = thread_message.base_task_revision
                base_amendment_sequence = thread_message.base_amendment_sequence
            elif isinstance(command, AnswerCommand):
                base_task_revision = command.expected_task_revision
                base_amendment_sequence = (
                    persisted_before.amendment_sequence
                    if persisted_before is not None
                    else prepared.amendment_sequence or 0
                )
            elif persisted_before is not None:
                base_task_revision = persisted_before.task_revision
                base_amendment_sequence = persisted_before.amendment_sequence
            else:
                base_task_revision = 0
                base_amendment_sequence = 0

            invocation = self._invocation_for_work(
                run_id,
                registration,
                prepared,
                action,
                command,
                thread_message,
                base_task_revision=base_task_revision,
                base_amendment_sequence=base_amendment_sequence,
            )
            if invocation.status in {"completed", "failed", "stopped"}:
                raise WebError(
                    "invocation_conflict",
                    "已有终态执行记录与当前恢复状态不一致，请检查本地记录。",
                )

            continuation_id = (
                thread_message.message_id if thread_message is not None else None
            )
            continuation_prompt = (
                thread_message.text if thread_message is not None else None
            )
            async with open_coding_run(
                prepared.request,
                prepared.initial_snapshot,
                prepared.launch,
                api_key=self.api_key,
                verifier=ExecutionEvidenceVerifier(),
                clarification_provider=ScriptedClarificationProvider({}),
                resume=prepared.resume,
                expected_amendment_sequence=prepared.amendment_sequence,
                continuation_id=continuation_id,
                continuation_prompt=continuation_prompt,
                observer=lambda event: self._publish(run_id, prepared, event),
                stream_model=self.config.stream_model,
                response_control=self._response_control,
                run_control=self._run_controls.get(run_id),
                model_factory=self.model_factory,
                runner_factory=self._runner_factory_for_invocation(
                    run_id, invocation.invocation_id
                ),
            ) as service:
                result = await service.run_step()
            with CodingRunFileLock(registration.request.state_dir):
                persisted = load_resume_state(registration.request.state_dir)
            with self._mutex:
                current_view = self._views.get(run_id)
            invocation = self.invocations.update(
                invocation,
                status=persisted.status,
                execution_task_revision=persisted.task_revision,
                execution_amendment_sequence=persisted.amendment_sequence,
                usage_after=current_view.usage if current_view is not None else None,
                finished_at=(
                    datetime.now(UTC)
                    if persisted.status in {"completed", "failed"}
                    else None
                ),
            )
            pending = self.threads.pending(run_id)
            if (
                pending is not None
                and persisted.continuation_id == pending.message_id
                and invocation.source_message_id == pending.message_id
            ):
                with self._mutex:
                    self.threads.mark_execution(
                        run_id,
                        pending.message_id,
                        task_revision=persisted.task_revision,
                        amendment_sequence=persisted.amendment_sequence,
                        continuation_id=pending.message_id,
                        invocation_id=invocation.invocation_id,
                    )
                    if command.command_id.hex == pending.message_id:
                        receipt = receipt.model_copy(
                            update={
                                "message_saved": True,
                                "message_revision": persisted.task_revision,
                            }
                        )
                        save_model(path, receipt)
            if isinstance(result, WaitingForUser):
                return

        try:
            asyncio.run(self._cancellable_work(run_id, execute))
            receipt = receipt.model_copy(update={"state": "settled"})
        except asyncio.CancelledError:
            if invocation is not None and invocation.status not in {
                "completed",
                "failed",
                "stopped",
            }:
                with self._mutex:
                    current_view = self._views.get(run_id)
                invocation = self.invocations.update(
                    invocation,
                    status="interrupted",
                    usage_after=current_view.usage
                    if current_view is not None
                    else None,
                    last_error="Execution cancelled by host",
                )
            receipt = receipt.model_copy(
                update={
                    "state": "rejected",
                    "error": "当前执行段已中断，可核查状态后继续。",
                }
            )
        except UserStoppedError:
            if invocation is not None and invocation.status not in {
                "completed",
                "failed",
                "stopped",
            }:
                with self._mutex:
                    current_view = self._views.get(run_id)
                invocation = self.invocations.update(
                    invocation,
                    status="stopped",
                    finished_at=datetime.now(UTC),
                    usage_after=current_view.usage
                    if current_view is not None
                    else None,
                )
            receipt = receipt.model_copy(update={"state": "settled", "error": None})
        except Exception as exc:
            if invocation is not None and invocation.status not in {
                "completed",
                "failed",
                "stopped",
            }:
                status = "interrupted"
                execution_task_revision = invocation.execution_task_revision
                execution_amendment_sequence = invocation.execution_amendment_sequence
                try:
                    with CodingRunFileLock(registration.request.state_dir):
                        persisted = load_resume_state(registration.request.state_dir)
                    status = persisted.status
                    execution_task_revision = persisted.task_revision
                    execution_amendment_sequence = persisted.amendment_sequence
                except (
                    OSError,
                    ValueError,
                    CodingRunResumeError,
                    CodingRunAlreadyLocked,
                ):
                    pass
                with self._mutex:
                    current_view = self._views.get(run_id)
                invocation = self.invocations.update(
                    invocation,
                    status=status,
                    execution_task_revision=execution_task_revision,
                    execution_amendment_sequence=execution_amendment_sequence,
                    usage_after=current_view.usage
                    if current_view is not None
                    else None,
                    finished_at=(
                        datetime.now(UTC) if status in {"completed", "failed"} else None
                    ),
                    last_error=f"{type(exc).__name__}: {exc}",
                )
            logging.getLogger(__name__).exception(
                "CodingRun work segment interrupted: %s", run_id
            )
            receipt = receipt.model_copy(
                update={
                    "state": "rejected",
                    "error": "工作段中断或未能启动；已保存答案和预算不会清零，请查看任务状态。",
                }
            )
        finally:
            try:
                with self._mutex:
                    save_model(path, receipt)
            finally:
                with self._mutex:
                    self._active = None
                    self._response_control = None
                    self.snapshot(run_id)
                    self._launch_next_deferred_resume_locked()
                self.changed(run_id)

    def with_idle_run(
        self, run_id: str, function: Callable[[Registration, RunSnapshot], object]
    ) -> object:
        with self._mutex:
            snapshot = self.snapshot(run_id)
            if snapshot.active or snapshot.view.status == "unavailable":
                raise WebError(
                    "file_busy", "运行期间不能读取可变产物，请等待当前工作段结束"
                )
            registration = self.registry.load(run_id)
            with CodingRunFileLock(registration.request.state_dir):
                return function(registration, snapshot)
