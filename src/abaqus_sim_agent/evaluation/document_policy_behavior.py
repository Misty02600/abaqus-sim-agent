"""真实模型的有界查证行为评测；官方原文受控回放，不评测检索排名或物理结果。"""

import argparse
import asyncio
import hashlib
import json
import time
from datetime import UTC, datetime
from importlib.metadata import version
from pathlib import Path, PurePosixPath
from typing import Literal

from pydantic import Field, model_validator
from pydantic_ai import ModelMessagesTypeAdapter
from pydantic_ai.messages import ToolCallPart, ToolReturnPart
from pydantic_ai.models.openai import OpenAIChatModelSettings
from pydantic_ai.models.wrapper import WrapperModel

from abaqus_sim_agent.coding.agent import create_abaqus_coding_agent
from abaqus_sim_agent.coding.clarification import ScriptedClarificationProvider
from abaqus_sim_agent.coding.contracts import (
    CodingRunBudget,
    CodingRunRequest,
    RuntimeProfile,
)
from abaqus_sim_agent.coding.dependencies import CodingAgentDependencies
from abaqus_sim_agent.coding.document_policy_binding import (
    DeclaredDocumentNeed,
    load_document_policy_spec,
)
from abaqus_sim_agent.coding.document_policy_contracts import DocumentScope
from abaqus_sim_agent.coding.execution_verifier import ExecutionEvidenceVerifier
from abaqus_sim_agent.coding.model import (
    DEEPSEEK_V4_FLASH,
    DeepSeekGateway,
    create_deepseek_chat_model,
    take_gateway_api_key_from_environment,
)
from abaqus_sim_agent.coding.resume import create_step_store
from abaqus_sim_agent.coding.service import CodingRunService
from abaqus_sim_agent.coding.workspace import WorkspaceResolver
from abaqus_sim_agent.retrieval.evidence_contracts import (
    Contract,
    Cost,
    Evidence,
    Found,
    NotFound,
    QuestionQuery,
    Rejected,
    SearchRequest,
    Unavailable,
)
from abaqus_sim_agent.skills import skill_library_path

from .document_behavior_profile import (
    CALIBRATED_PROFILE,
    MeasurementProfile,
    model_settings,
)
from .document_behavior_spec import verify_runtime_manifest, verify_spec
from .document_policy_adapter import bind_document_policy, runtime_paths
from .document_policy_wiring_smoke import NeverRunAbaqus
from .evidence_receipt import (
    canonical,
    count_tokens,
    digest,
    load_units,
    read,
    validate_evidence,
    verify_snapshot,
)

PROFILE = "document-policy-live-behavior-v1"
UNITS = "c0cee566bb76c7f17644a9005c2715226ec5132f70b666bd040a43ab71ae6b50"
ANGLES = "6a6cc351556956924797ecf266f4812c9963d96a137b7806a45ca6ea5f8a17f9"
SETTINGS: OpenAIChatModelSettings = {
    "max_tokens": 2400,
    "extra_body": {"thinking": {"type": "disabled"}},
}
TASK_INSTRUCTIONS = """
Complete this document-only behavior task; do not run Abaqus or any other program.
All runtime budgets are zero and the runner is a test double, not a licensed environment.
The documentation tool uses a controlled replay of complete, source-verified official
paragraphs, or an explicitly injected failure; it is not a live retrieval ranking test.
The returned candidates can be relevant but incomplete. Do not invent citations or values.
Write analysis.json with summary (string), findings (array of strings), limitations (array
of strings), next_action (string), and evidence_refs (array of actual source/receipt/file
references). A partial analysis may be written when the task must stop, but do not mark a
critical unresolved task complete. Also create any separately requested independent file.
Keep the task's stated scope and do not create unrelated modeling or research subgoals.
""".strip()


class BehaviorCase(Contract):
    id: str = Field(pattern=r"^[a-z][a-z0-9-]+$")
    goal: str
    seed_files: dict[str, str]
    needs: tuple[str, ...] = Field(max_length=8)
    responses: tuple[tuple[Literal["units", "angles"], ...], ...] = Field(max_length=2)
    source_release: str = "2026 FD02"
    response_kind: Literal["evidence", "missing_release", "technical_failure"] = (
        "evidence"
    )
    physical_input: bool = False
    inject_resume: bool = False
    expected_lookups: int = Field(ge=0, le=2)
    expected_status: Literal["completed", "failed"]
    review_criteria: tuple[str, ...]

    @model_validator(mode="after")
    def safe_files(self):
        if any(
            PurePosixPath(name).name != name or "\\" in name or ":" in name
            for name in self.seed_files
        ):
            raise ValueError("only plain seed file names are allowed")
        return self


class BehaviorBatch(Contract):
    profile: Literal["document-policy-live-behavior-v1"] = PROFILE
    cases: tuple[BehaviorCase, ...] = Field(min_length=8, max_length=8)

    @model_validator(mode="after")
    def unique_cases(self):
        if len({c.id for c in self.cases}) != 8:
            raise ValueError("eight unique scenarios are required")
        return self


def write_new(path: Path, value):
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, default=str)
        stream.write("\n")


def phase(root: Path, event: str, **fields):
    with (root / "attempts.jsonl").open("a", encoding="utf-8") as stream:
        stream.write(
            canonical({"event": event, "at": datetime.now(UTC).isoformat(), **fields})
            + "\n"
        )


def records(path: Path) -> list[dict]:
    return (
        [json.loads(line) for line in path.read_bytes().splitlines()]
        if path.exists()
        else []
    )


def prepare_sources(binding, repo: Path):
    paths = runtime_paths(binding.spec)
    units = load_units(paths, {UNITS, ANGLES})
    original = read(
        repo
        / "var/audits/rag-evidence-baseline-v1/run-1/consistent-unit-system.result.json"
    )
    scope = next(
        item["applicability"] for item in original["evidence"] if item["id"] == UNITS
    )
    evidence = {}
    for alias, key in (("units", UNITS), ("angles", ANGLES)):
        unit = units[key]
        parent = unit["evidence"]
        evidence[alias] = Evidence.model_validate_json(
            canonical(
                {
                    "id": key,
                    **{
                        k: parent[k]
                        for k in (
                            "document_id",
                            "title",
                            "source",
                            "source_sha256",
                            "release",
                            "body",
                        )
                    },
                    "section": parent["heading_path"][-1],
                    "body_scope": "complete_stored_evidence_unit",
                    "body_sha256": hashlib.sha256(parent["body"].encode()).hexdigest(),
                    "applicability": scope,
                    "member_xpaths": unit["member_xpaths"],
                }
            )
        )
    return evidence, count_tokens(paths)


class ControlledEvidencePort:
    def __init__(self, case, binding, evidence, count, state):
        self.case, self.binding, self.evidence, self.count, self.state = (
            case,
            binding,
            evidence,
            count,
            state,
        )
        self.calls = 0

    async def search(self, request, *, search_id, deadline):
        self.calls += 1
        if time.monotonic() >= deadline:
            raise TimeoutError("controlled query deadline expired")
        common = {
            "search_id": search_id,
            "binding": self.binding.evidence_binding,
            "message": "Controlled official-source evidence replay, not live retrieval ranking; found does not establish sufficiency.",
        }
        if self.case.response_kind == "technical_failure":
            result = Unavailable(
                search_id=search_id,
                reason_code="worker_timeout",
                message="Injected technical timeout for this behavior scenario. No evidence was delivered; do not reword a retry.",
            )
        elif self.case.response_kind == "missing_release":
            result = NotFound(
                **common,
                reason_code="requested_release_not_indexed",
                cost=Cost(stage="delivered", evidence_tokens=self.count("[]")),
            )
        elif (
            self.count(request.query.question + "\n" + request.query.observed_context)
            > 256
        ):
            result = Rejected(**common, reason_code="query_too_long")
        else:
            aliases = (
                self.case.responses[min(self.calls - 1, len(self.case.responses) - 1)]
                if self.case.responses
                else ()
            )
            items = tuple(self.evidence[alias] for alias in aliases)
            cost = Cost(
                stage="delivered",
                body_tokens=tuple(self.count(e.body) for e in items),
                evidence_tokens=self.count(
                    canonical([e.model_dump(mode="json") for e in items])
                ),
            )
            result = (
                Found(**common, evidence=items, cost=cost)
                if items
                else NotFound(**common, reason_code="no_candidates", cost=cost)
            )
        with (self.state / "controlled_queries.jsonl").open(
            "a", encoding="utf-8"
        ) as stream:
            stream.write(
                canonical(
                    {
                        "mode": "controlled_official_replay",
                        "call": self.calls,
                        "request": request.model_dump(mode="json"),
                        "result": result.model_dump(mode="json"),
                    }
                )
                + "\n"
            )
        return result


class ControlledSourceValidator:
    def __init__(self, binding, source_files):
        self.binding, self.source_files = binding, source_files

    async def validate(self, request, result, *, binding, deadline):
        if binding != self.binding.evidence_binding:
            raise ValueError("unexpected replay binding")
        verify_snapshot(self.source_files, deadline)
        validate_evidence(request, result, runtime_paths(self.binding.spec))


class InjectedResumeInterruption(RuntimeError):
    pass


class RecordingModel(WrapperModel):
    def __init__(self, model, state, inject_resume):
        super().__init__(model)
        self.state, self.inject_resume = state, inject_resume
        self.injected = False
        self.requests = 0
        (state / "model-io").mkdir()

    async def request(self, messages, model_settings, model_request_parameters):
        if (
            self.inject_resume
            and not self.injected
            and any(
                e["event"]["kind"] == "evidence_received"
                for e in records(self.state / "document_policy.jsonl")
            )
        ):
            self.injected = True
            write_new(
                self.state / "injected_resume.json",
                {
                    "kind": "after_first_receipt_before_next_provider_request",
                    "provider_requests_before": self.requests,
                },
            )
            raise InjectedResumeInterruption(
                "planned same-run interruption after a settled document receipt"
            )
        self.requests += 1
        stem = self.state / "model-io" / f"request-{self.requests:03d}"
        stem.with_suffix(".messages.json").write_bytes(
            ModelMessagesTypeAdapter.dump_json(messages)
        )
        response = await self.wrapped.request(
            messages, model_settings, model_request_parameters
        )
        stem.with_suffix(".response.json").write_bytes(
            ModelMessagesTypeAdapter.dump_json([response])
        )
        return response


def http_recorder(state: Path):
    directory = state / "http"
    directory.mkdir()
    sequence = 0

    async def record(response):
        nonlocal sequence
        sequence += 1
        raw = await response.aread()
        (directory / f"{sequence:03d}.request.json").write_bytes(
            response.request.content
        )
        (directory / f"{sequence:03d}.response.bin").write_bytes(raw)
        write_new(
            directory / f"{sequence:03d}.meta.json",
            {
                "status_code": response.status_code,
                "url_path": response.request.url.path,
            },
        )

    return record


def http_attempt_recorder(state: Path):
    """在发送前记录无凭据的请求体，覆盖没有 HTTP 响应的超时尝试。"""
    directory = state / "http-attempts"
    directory.mkdir()
    sequence = 0

    async def record(request):
        nonlocal sequence
        sequence += 1
        # 不记录 URL query、Authorization 或其他请求头。
        write_new(
            directory / f"{sequence:03d}.json",
            {
                "method": request.method,
                "url_path": request.url.path,
                "body": json.loads(request.content),
            },
        )

    return record


def caused_by(exc, name):
    while exc is not None:
        if type(exc).__name__ == name:
            return True
        exc = exc.__cause__
    return False


def observe(
    case: BehaviorCase,
    state: Path,
    port: ControlledEvidencePort,
    model: RecordingModel,
    error,
):
    history = (
        ModelMessagesTypeAdapter.validate_json((state / "history.json").read_bytes())
        if (state / "history.json").exists()
        else []
    )
    calls, returns = [], []
    for message_index, message in enumerate(history):
        for part_index, part in enumerate(message.parts):
            if isinstance(part, ToolCallPart):
                try:
                    arguments = part.args_as_dict()
                except (ValueError, TypeError):
                    arguments = {"invalid_json_arguments": part.args}
                calls.append(
                    {
                        "name": part.tool_name,
                        "args": arguments,
                        "id": part.tool_call_id,
                        "message_index": message_index,
                        "part_index": part_index,
                    }
                )
            elif isinstance(part, ToolReturnPart):
                returns.append(
                    {
                        "name": part.tool_name,
                        "content": part.content,
                        "id": part.tool_call_id,
                        "message_index": message_index,
                        "part_index": part_index,
                    }
                )
    journal = records(state / "document_policy.jsonl")
    assessments = [
        e["event"]["assessment"]
        for e in journal
        if e["event"]["kind"] == "assessment_recorded"
    ]
    outcome = (
        read(state / "outcome.json") if (state / "outcome.json").exists() else None
    )
    provider_failure = bool(
        error
        and any(
            caused_by(error, n)
            for n in (
                "ModelAPIError",
                "ModelHTTPError",
                "APIConnectionError",
                "APITimeoutError",
                "RateLimitError",
                "InternalServerError",
                "AuthenticationError",
            )
        )
    )
    files = {
        p.name: p.read_text(encoding="utf-8", errors="replace")
        for p in sorted(state.parent.joinpath("workspace").iterdir())
        if p.is_file()
    }
    return {
        "case_id": case.id,
        "provider_requests": model.requests,
        "controlled_lookup_calls": port.calls,
        "outcome": outcome,
        "provider_failure": provider_failure,
        "exception": type(error).__name__ if error else None,
        "tool_calls": calls,
        "tool_returns": returns,
        "assessments": assessments,
        "cache_hits": sum(
            isinstance(r["content"], dict) and r["content"].get("cache_hit") is True
            for r in returns
        ),
        "resume_injected": model.injected,
        "workspace_files": files,
        "count_matches": port.calls == case.expected_lookups,
        "terminal_matches": bool(outcome and outcome["status"] == case.expected_status),
        "runtime_tool_attempts": sum(
            c["name"] in {"run_cae_script", "run_solver_job", "run_abaqus_python"}
            for c in calls
        ),
        "review_criteria": case.review_criteria,
        "behavior_review": "pending_host_review_not_model_self_grade",
    }


async def execute_batch(
    root: Path,
    cases_path: Path,
    spec_path: Path,
    *,
    measurement_profile: MeasurementProfile = "legacy",
    experiment_spec: Path | None = None,
    repetition: int | None = None,
):
    settings = model_settings(measurement_profile)
    repo = Path(__file__).parents[3]
    experiment = None
    if measurement_profile == "calibrated":
        if (
            experiment_spec is None
            or type(repetition) is not int
            or repetition not in (1, 2, 3)
        ):
            raise ValueError("calibrated requires experiment_spec and repetition 1..3")
        experiment = verify_spec(repo, experiment_spec)
        bound = experiment["implementation_and_inputs"]
        for path, key in (
            (cases_path, "benchmarks/coding/document_policy_behavior_cases_v1.json"),
            (spec_path, "benchmarks/rag/document_policy_spec_v1.json"),
        ):
            if digest(path) != bound[key]:
                raise ValueError("runtime input differs from experiment seal: " + key)
    elif experiment_spec is not None or repetition is not None:
        raise ValueError("legacy does not accept calibrated experiment arguments")
    batch = BehaviorBatch.model_validate_json(cases_path.read_bytes())  # noqa: ASYNC240 -- explicit local experiment input
    api_key = take_gateway_api_key_from_environment(DeepSeekGateway.OPENCODE_GO)
    if not api_key.get_secret_value():
        raise ValueError("OpenCode Go credentials are unavailable")
    binding = bind_document_policy(load_document_policy_spec(spec_path))
    evidence, count = prepare_sources(binding, repo)
    source_root = Path(read(binding.spec.candidate / "manifest.json")["source_root"])
    source = source_root / "SIMACAEMODRefMap/simamod-c-conventions.htm"
    source_files = {
        str(source): digest(source),
        str(binding.spec.candidate / "units.jsonl"): digest(
            binding.spec.candidate / "units.jsonl"
        ),
        str(binding.spec.scope_rules): digest(binding.spec.scope_rules),
    }
    source_files.update(
        {
            name: value
            for name, value in read(binding.spec.candidate / "manifest.json")[
                "encoder_files"
            ].items()
            if Path(name).name == "tokenizer.json"
        }
    )
    fixture_items = tuple(evidence.values())
    fixture = Found(
        search_id="fixture-check",
        binding=binding.evidence_binding,
        evidence=fixture_items,
        cost=Cost(
            stage="delivered",
            body_tokens=tuple(count(e.body) for e in fixture_items),
            evidence_tokens=count(
                canonical([e.model_dump(mode="json") for e in fixture_items])
            ),
        ),
    )
    validate_evidence(
        SearchRequest(
            query=QuestionQuery(
                question="Check fixture integrity", source_release="2026 FD02"
            ),
            constraints_origin="host_typed",
        ),
        fixture,
        runtime_paths(binding.spec),
    )
    root.mkdir(parents=True, exist_ok=False)  # noqa: ASYNC240 -- explicit sequential experiment
    write_new(
        root / "evidence.json",
        {key: value.model_dump(mode="json") for key, value in evidence.items()},
    )
    write_new(root / "cases.json", batch.model_dump(mode="json"))
    implementations = {
        str(p.relative_to(repo)): digest(p)
        for p in sorted((repo / "src/abaqus_sim_agent").rglob("*.py"))
    }
    budget = CodingRunBudget(
        max_model_requests=12,
        max_api_lookup_calls=0,
        max_concept_lookup_calls=2,
        max_diagnostic_retrieval_calls=0,
        max_abaqus_calls=0,
        max_solver_calls=0,
        wall_time_seconds=360.0,
    )
    manifest = {
        "profile": PROFILE if measurement_profile == "legacy" else CALIBRATED_PROFILE,
        "base_backend_binding": binding.model_dump(mode="json"),
        "spec_sha256": digest(spec_path),
        "cases_sha256": digest(cases_path),
        "evidence_sha256": digest(root / "evidence.json"),
        "implementation_sha256": implementations,
        "source_files": source_files,
        "skill_sha256": digest(skill_library_path() / "abaqus-2024-coding/SKILL.md"),
        "uv_lock_sha256": digest(repo / "uv.lock"),
        "budget": budget.model_dump(mode="json"),
        "model": {
            "provider": "opencode-go",
            "name": DEEPSEEK_V4_FLASH,
            "http_retries": 0,
            "http_timeout_seconds": 60,
            "settings": settings,
        },
        "pydantic_ai_version": version("pydantic-ai-slim"),
        "harness_version": version("pydantic-ai-harness"),
        "task_instructions": TASK_INSTRUCTIONS,
        "planning": False,
        "api_catalog": "off",
        "real_ranking_calls": 0,
        "selection": "fixed per-scenario complete source units; not responsive retrieval rankings",
        "one_new_run_per_case": True,
        "provider_failures": "invalid sample, no retry",
        "behavior_gate": "all eight scenarios must have valid provider evidence and pass both mechanical facts and predeclared host semantic review; invalid samples do not count as passes",
        "wall_timeout": "record separately as incomplete under the fixed wall budget; never raise limits or replace the sample",
        "review": "host review against predeclared criteria, not model self-grading",
    }
    if measurement_profile == "calibrated":
        assert experiment is not None
        assert experiment_spec is not None
        verify_runtime_manifest(experiment, manifest)
        manifest["experiment_spec_sha256"] = digest(experiment_spec)
        manifest["repetition"] = repetition
        manifest["rubrics"] = experiment["rubrics"]
        manifest["planned_trial_ids"] = [
            t["trial_id"]
            for t in experiment["planned_trials"]
            if t["repetition"] == repetition
        ]
        manifest["behavior_gate"] = (
            "Separate task acceptance, necessary constraints, protocol and measurement validity; host semantic review required. No pass inferred from tool counts or host outcome alone."
        )
        manifest["provider_failures"] = (
            "Retain every started trial in completion denominator; conditional capability analysis labels exclusions. No replacement or automatic provider resume."
        )
        manifest["output_limit_policy"] = (
            "No evaluator-added single-response cap; provider/model native limits still apply. Thinking disabled is retained as an explicit experiment setting, not claimed as the universal product default."
        )
    write_new(root / "seal.json", manifest)
    frozen_files = (
        {str(repo / key): value for key, value in implementations.items()}
        | source_files
        | {
            str(cases_path): manifest["cases_sha256"],
            str(spec_path): manifest["spec_sha256"],
            str(root / "evidence.json"): manifest["evidence_sha256"],
            str(repo / "uv.lock"): manifest["uv_lock_sha256"],
            str(skill_library_path() / "abaqus-2024-coding/SKILL.md"): manifest[
                "skill_sha256"
            ],
        }
    )
    observations = []
    if experiment_spec is not None:
        frozen_files[str(experiment_spec)] = manifest["experiment_spec_sha256"]
    for case in batch.cases:
        if experiment_spec is not None:
            verify_spec(repo, experiment_spec)
        verify_snapshot(frozen_files, time.monotonic() + 60)
        if digest(cases_path) != manifest["cases_sha256"]:
            raise ValueError("cases changed after seal")
        workspace, state = root / case.id / "workspace", root / case.id / "state"
        workspace.mkdir(parents=True)
        state.mkdir()
        for name, value in case.seed_files.items():
            (workspace / name).write_text(value, encoding="utf-8")
        scope = DocumentScope(
            product="Abaqus/Standard",
            source_release=case.source_release,
            constraints_origin="user_confirmed",
        )
        needs = tuple(
            DeclaredDocumentNeed(
                condition=n,
                origin="task_contract",
                affects_step="main document conclusion",
            )
            for n in case.needs
        )
        if case.physical_input:
            needs = (
                DeclaredDocumentNeed(
                    condition="plate_thickness",
                    origin="task_contract",
                    affects_step="all modeling",
                    kind="physical_input",
                    clarification_field_id="plate_thickness",
                ),
            )
        case_binding = binding.model_copy(
            update={
                "spec": binding.spec.model_copy(
                    update={"scope": scope, "host_needs": needs}
                )
            }
        )
        request = CodingRunRequest(
            run_id=(
                "document-live-" + case.id
                if measurement_profile == "legacy"
                else "document-v2-"
                + hashlib.sha256(str(root).encode()).hexdigest()[:16]
                + "-"
                + case.id
            ),
            goal=case.goal,
            workspace=workspace,
            state_dir=state,
            runtime=RuntimeProfile(launcher_path=root / "never-run.bat"),
            document_policy=case_binding,
            budget=budget,
        )
        snapshot = WorkspaceResolver(captured_cwd=workspace).snapshot(workspace)
        port = ControlledEvidencePort(case, case_binding, evidence, count, state)
        validator = ControlledSourceValidator(case_binding, source_files)
        raw_model, client = create_deepseek_chat_model(
            gateway=DeepSeekGateway.OPENCODE_GO,
            model_name=DEEPSEEK_V4_FLASH,
            api_key=api_key,
            timeout_seconds=60,
            http_retries=0,
            conversation_id=request.run_id,
        )
        client._client.event_hooks["response"].append(http_recorder(state))
        if measurement_profile == "calibrated":
            client._client.event_hooks["request"].append(http_attempt_recorder(state))
        model = RecordingModel(raw_model, state, case.inject_resume)

        def service(
            resume=False,
            *,
            state=state,
            request=request,
            workspace=workspace,
            port=port,
            validator=validator,
            model=model,
            snapshot=snapshot,
        ):
            steps = create_step_store(state)
            deps = CodingAgentDependencies(
                request=request,
                runner=NeverRunAbaqus(
                    workspace, state, launcher=request.runtime.launcher_path
                ),
                document_port=port,
                document_validator=validator,
            )
            agent = create_abaqus_coding_agent(
                model,
                request,
                skill_library=skill_library_path(),
                concept_retrieval_mode="tool",
                planning_enabled=False,
                task_instructions=TASK_INSTRUCTIONS,
                step_store=steps,
            )
            agent.model_settings = settings
            return CodingRunService(
                agent=agent,
                request=request,
                dependencies=deps,
                clarification_provider=ScriptedClarificationProvider({}),
                verifier=ExecutionEvidenceVerifier(),
                initial_snapshot=snapshot,
                step_store=steps,
                resume=resume,
            )

        phase(
            root,
            "case_started",
            case_id=case.id,
            seal_sha256=digest(root / "seal.json"),
        )
        print("START", case.id, flush=True)
        error = None
        try:
            try:
                await service().run()
            except Exception as exc:
                if case.inject_resume and caused_by(exc, "InjectedResumeInterruption"):
                    phase(root, "planned_same_run_resume", case_id=case.id)
                    await service(resume=True).run()
                else:
                    raise
        except Exception as exc:
            error = exc
        finally:
            await client.close()
        observation = observe(case, state, port, model, error)
        if measurement_profile == "calibrated":
            observation["trial_id"] = f"{case.id}-r{repetition}"
            observation["http_attempts"] = len(
                list((state / "http-attempts").glob("*.json"))
            )
            observation["http_responses"] = len(
                list((state / "http").glob("*.response.bin"))
            )
        write_new(state / "observation.json", observation)
        observations.append(observation)
        phase(
            root,
            "case_recorded",
            case_id=case.id,
            provider_requests=model.requests,
            lookups=port.calls,
            error=type(error).__name__ if error else None,
        )
        print(
            "DONE",
            case.id,
            "requests=",
            model.requests,
            "lookups=",
            port.calls,
            "error=",
            type(error).__name__ if error else None,
            flush=True,
        )
    verify_snapshot(frozen_files, time.monotonic() + 60)
    if experiment_spec is not None:
        verify_spec(repo, experiment_spec)
    write_new(
        root / "report.json",
        {
            "seal_sha256": digest(root / "seal.json"),
            "observations": observations,
            "review_status": "pending_host_review",
        },
    )
    write_new(
        root / "completion.json",
        {
            "completed_cases": len(observations),
            "source_and_implementation_unchanged": True,
            "seal_sha256": digest(root / "seal.json"),
            "report_sha256": digest(root / "report.json"),
            "scope": "controlled evidence behavior, no licensed execution or live ranking",
        },
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--cases",
        type=Path,
        default=Path("benchmarks/coding/document_policy_behavior_cases_v1.json"),
    )
    parser.add_argument(
        "--spec", type=Path, default=Path("benchmarks/rag/document_policy_spec_v1.json")
    )
    parser.add_argument(
        "--measurement-profile",
        choices=("legacy", "calibrated"),
        default="legacy",
        help="Explicit evaluation profile; calibrated removes only the extra response cap and separates scoring metadata.",
    )
    parser.add_argument("--experiment-spec", type=Path)
    parser.add_argument("--repetition", type=int, choices=(1, 2, 3))
    args = parser.parse_args()
    asyncio.run(
        execute_batch(
            args.output.resolve(),
            args.cases.resolve(),
            args.spec.resolve(),
            measurement_profile=args.measurement_profile,
            experiment_spec=args.experiment_spec.resolve()
            if args.experiment_spec
            else None,
            repetition=args.repetition,
        )
    )


if __name__ == "__main__":
    main()
