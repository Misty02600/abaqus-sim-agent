import asyncio
import hashlib
import json
import os
import time

import pytest
from pydantic import ValidationError
from pydantic_ai.messages import ModelResponse, ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import FunctionModel
from tests.test_coding_resume import _execution
from tests.test_coding_service import _request
from tests.test_document_policy import BINDING, SCOPE, FakePort, FakeValidator, first

from abaqus_sim_agent.api_catalog import ApiCatalog
from abaqus_sim_agent.coding.agent import create_abaqus_coding_agent
from abaqus_sim_agent.coding.amendments import (
    CodingRunConfigPatch,
    RuntimeExecutionPolicyPatch,
    append_operational_amendment,
    append_task_revision,
)
from abaqus_sim_agent.coding.clarification import ScriptedClarificationProvider
from abaqus_sim_agent.coding.cli import (
    _LaunchManifest,
    _validate_resume_arguments,
    build_parser,
)
from abaqus_sim_agent.coding.context_policy import CONTEXT_POLICY_VERSION
from abaqus_sim_agent.coding.contracts import (
    CapabilityDefinitionBinding,
    CodingRunBudget,
)
from abaqus_sim_agent.coding.dependencies import CodingAgentDependencies
from abaqus_sim_agent.coding.document_policy_binding import (
    DeclaredDocumentNeed,
    DocumentPolicyBinding,
    DocumentPolicySpec,
    load_document_policy_spec,
)
from abaqus_sim_agent.coding.model import DeepSeekGateway, new_run_execution_config
from abaqus_sim_agent.coding.resume import (
    create_step_store,
    load_request_for_resume,
    load_resume_state,
)
from abaqus_sim_agent.coding.service import (
    CodingRunInfrastructureError,
    CodingRunService,
)
from abaqus_sim_agent.coding.verification_port import VerificationAttempt
from abaqus_sim_agent.execution import AbaqusInvocation


def bound(tmp_path, *, needs=()):
    return DocumentPolicyBinding(
        spec=DocumentPolicySpec(
            candidate=tmp_path / "candidate",
            symbol_index=tmp_path / "symbol",
            scope_rules=tmp_path / "scope.json",
            ranker_directory=tmp_path / "ranker",
            scope=SCOPE,
            host_needs=needs,
        ),
        evidence_binding=BINDING,
    )


def tool(name, args):
    # FunctionModel must represent the provider JSON wire path for strict tuple fields.
    return ModelResponse(parts=[ToolCallPart(name, json.dumps(args))])


def ready():
    return tool(
        "final_result_ReadyForVerificationDecision",
        {
            "status": "ready_for_verification",
            "summary": "Deterministic fixture ready",
            "deliverables": [{"relative_path": "report.txt"}],
        },
    )


def returned(messages, name):
    for message in reversed(messages):
        for part in reversed(message.parts):
            if isinstance(part, ToolReturnPart) and part.tool_name == name:
                return (
                    part.content
                    if isinstance(part.content, dict)
                    else json.loads(part.content)
                )
    raise AssertionError("no tool return: " + name)


def assessment_payload(reply, *, status="supported", next_action="continue"):
    wrapped = {
        "assessment": {
            "search_id": reply["result"]["search_id"],
            "scope": SCOPE.model_dump(mode="json"),
            "needs": [
                {
                    "need_id": n["id"],
                    "status": status,
                    "explanation": "Deterministic protocol fixture only.",
                    "citations": []
                    if status == "missing"
                    else [
                        {
                            "search_id": reply["result"]["search_id"],
                            "evidence_id": "doc-one",
                            "quote": "Rotations are expressed in radians.",
                            "applicability": "unknown",
                        }
                    ],
                }
                for n in reply["needs"]
            ],
            "next_action": next_action,
        }
    }
    return wrapped | {"protocol": "document-assessment-v1"}


class FakeRunner:
    def __init__(self, request):
        self.request, self.calls = request, []

    async def run_cae_script(self, path, **kwargs):
        self.calls.append("cae")
        return _execution(self.request).model_copy(
            update={"invocation": AbaqusInvocation.CAE_NOGUI}
        )

    async def run_abaqus_python(self, path, **kwargs):
        self.calls.append("python")
        return _execution(self.request).model_copy(
            update={"invocation": AbaqusInvocation.ABAQUS_PYTHON}
        )

    async def run_solver_job(self, name, **kwargs):
        self.calls.append("solver")
        return _execution(self.request)


class FakeVerifier:
    def __init__(self):
        self.calls = 0

    async def verify(self, *args):
        self.calls += 1
        return VerificationAttempt(
            passed=True,
            scope="execution_evidence",
            feedback="Fake verifier for control-flow testing only",
        )


def compose(
    request,
    snapshot,
    model,
    *,
    port=None,
    verifier=None,
    answers=None,
    resume=False,
    sequence=None,
    finalize=False,
):
    step_store = create_step_store(request.state_dir)
    runner = FakeRunner(request)
    deps = CodingAgentDependencies(
        request=request,
        runner=runner,
        document_port=port or FakePort(),
        document_validator=FakeValidator(),
    )
    agent = create_abaqus_coding_agent(
        FunctionModel(model),
        request,
        concept_retrieval_mode="tool",
        planning_enabled=False,
        step_store=step_store,
        task_instructions="Deterministic document-policy integration test; no real Abaqus.",
    )
    service = CodingRunService(
        agent=agent,
        request=request,
        dependencies=deps,
        clarification_provider=ScriptedClarificationProvider(answers or {}),
        verifier=verifier or FakeVerifier(),
        initial_snapshot=snapshot,
        step_store=step_store,
        resume=resume,
        expected_amendment_sequence=sequence,
        verify_on_budget_exhaustion=finalize,
    )
    return service, deps, runner


def test_harness_blocks_all_runtime_tools_and_ready_until_assessment(tmp_path):
    request, snapshot = _request(tmp_path)
    (request.workspace / "model.py").write_text("pass\n", encoding="utf-8")
    request = request.model_copy(update={"document_policy": bound(tmp_path)})
    verifier = FakeVerifier()
    count = 0

    def model(messages, info):
        nonlocal count
        count += 1
        names = [t.name for t in info.function_tools]
        assert names.count("search_abaqus_docs") == 1
        assert "record_document_assessment" in names
        assert "run_command" not in names
        if count == 1:
            return tool(
                "search_abaqus_docs", {"query": first().model_dump(mode="json")}
            )
        if count == 2:
            return tool("run_cae_script", {"script_path": "model.py"})
        if count == 3:
            assert returned(messages, "run_cae_script")["budget_consumed"] is False
            return tool("run_solver_job", {"job_name": "job", "input_file": "job.inp"})
        if count == 4:
            assert returned(messages, "run_solver_job")["blocked"]
            return tool("run_abaqus_python", {"script_path": "extract.py"})
        if count == 5:
            assert returned(messages, "run_abaqus_python")["blocked"]
            return ready()
        if count == 6:
            assert verifier.calls == 0
            return tool(
                "record_document_assessment",
                assessment_payload(returned(messages, "search_abaqus_docs")),
            )
        if count == 7:
            assert (
                returned(messages, "record_document_assessment")["status"] == "recorded"
            )
            return tool("run_cae_script", {"script_path": "model.py"})
        return ready()

    service, deps, runner = compose(request, snapshot, model, verifier=verifier)
    result = asyncio.run(service.run())
    assert result.outcome.status.value == "completed"
    assert runner.calls == ["cae"]
    assert deps.abaqus_calls == 1
    assert verifier.calls == 1
    assert deps.concept_lookup_calls == 1
    state = load_resume_state(request.state_dir)
    assert state.schema_version == "coding-run-resume-v7"
    assert state.document_policy_checkpoint.cumulative_lookup_calls == 1


def test_budget_exhaustion_cannot_bypass_document_gate(tmp_path):
    request, snapshot = _request(tmp_path, budget=CodingRunBudget(max_model_requests=1))
    need = DeclaredDocumentNeed(
        condition="required evidence",
        affects_step="all runtime",
        origin="task_contract",
    )
    request = request.model_copy(
        update={"document_policy": bound(tmp_path, needs=(need,))}
    )
    verifier = FakeVerifier()
    service, deps, runner = compose(
        request, snapshot, lambda m, i: ready(), verifier=verifier, finalize=True
    )
    outcome = asyncio.run(service.run()).outcome
    assert outcome.status.value == "failed"
    assert outcome.failure_stage == "knowledge_evidence"
    assert verifier.calls == 0
    assert runner.calls == []
    assert deps.abaqus_calls == 0


def test_host_physical_input_is_resolved_only_by_real_clarification_path(tmp_path):
    request, snapshot = _request(tmp_path)
    (request.workspace / "model.py").write_text("pass\n", encoding="utf-8")
    need = DeclaredDocumentNeed(
        condition="plate thickness",
        affects_step="all runtime",
        origin="task_contract",
        kind="physical_input",
        clarification_field_id="plate_thickness",
    )
    request = request.model_copy(
        update={"document_policy": bound(tmp_path, needs=(need,))}
    )
    calls = 0

    def model(messages, info):
        nonlocal calls
        calls += 1
        if calls == 1:
            return tool("run_cae_script", {"script_path": "model.py"})
        if calls == 2:
            assert returned(messages, "run_cae_script")["blocked"]
            return tool(
                "final_result_NeedsInputDecision",
                {
                    "status": "needs_input",
                    "summary": "Need thickness",
                    "clarification": {
                        "field_id": "plate_thickness",
                        "kind": "missing_physical_input",
                        "question": "Thickness?",
                        "impact": "Changes section stiffness",
                    },
                },
            )
        if calls == 3:
            return tool("run_cae_script", {"script_path": "model.py"})
        return ready()

    service, deps, runner = compose(
        request, snapshot, model, answers={"plate_thickness": "1 mm"}
    )
    result = asyncio.run(service.run())
    assert result.outcome.status.value == "completed"
    assert runner.calls == ["cae"]
    assert deps.concept_lookup_calls == 0
    assert result.outcome.clarifications[0].abaqus_calls_before_answer == 0


def test_real_service_resume_reuses_receipt_across_runtime_amendment(tmp_path):
    request, snapshot = _request(tmp_path)
    request = request.model_copy(update={"document_policy": bound(tmp_path)})
    port = FakePort()
    calls = 0

    def interrupted(messages, info):
        nonlocal calls
        calls += 1
        if calls == 1:
            return tool(
                "search_abaqus_docs", {"query": first().model_dump(mode="json")}
            )
        raise ConnectionError("injected provider interruption")

    service, _, _ = compose(request, snapshot, interrupted, port=port)
    with pytest.raises(CodingRunInfrastructureError):
        asyncio.run(service.run())
    original_request = (request.state_dir / "request.json").read_bytes()
    append_operational_amendment(
        request.state_dir,
        patch=CodingRunConfigPatch(
            runtime_policy=RuntimeExecutionPolicyPatch.model_validate(
                {"solver": {"mode": "allow"}}
            )
        ),
        reason="test unrelated runtime policy change",
    )
    effective = load_request_for_resume(request.state_dir)
    resumed_calls = 0

    def recovered(messages, info):
        nonlocal resumed_calls
        resumed_calls += 1
        if resumed_calls == 1:
            return tool(
                "search_abaqus_docs", {"query": first().model_dump(mode="json")}
            )
        if resumed_calls == 2:
            assert returned(messages, "search_abaqus_docs")["cache_hit"]
            return tool(
                "record_document_assessment",
                assessment_payload(returned(messages, "search_abaqus_docs")),
            )
        return ready()

    service, deps, _ = compose(
        effective, snapshot, recovered, port=port, resume=True, sequence=1
    )
    outcome = asyncio.run(service.run()).outcome
    assert outcome.status.value == "completed"
    assert len(port.calls) == 1
    assert deps.concept_lookup_calls == 1
    assert deps.document_policy.journal.state.limit == 2
    assert (request.state_dir / "request.json").read_bytes() == original_request
    append_task_revision(
        request.state_dir,
        goal="New independent documentation task",
        reason="test revision",
    )
    revised = load_request_for_resume(request.state_dir)
    service, deps, _ = compose(
        revised, snapshot, lambda m, i: ready(), port=port, resume=True, sequence=2
    )
    assert asyncio.run(service.run()).outcome.status.value == "completed"
    assert deps.document_policy.journal.state.revision == 1
    assert deps.document_policy.journal.state.needs == {}
    with pytest.raises(RuntimeError, match="must hold"):
        _ = deps.document_policy.needs
    assert deps.concept_lookup_calls == 1


@pytest.mark.parametrize(
    "damaged", ["document_policy.jsonl", "document_policy.head.json"]
)
def test_resume_with_damaged_policy_never_calls_model(tmp_path, damaged):
    request, snapshot = _request(tmp_path)
    request = request.model_copy(update={"document_policy": bound(tmp_path)})

    def fail(m, i):
        raise ConnectionError("interruption")

    service, _, _ = compose(request, snapshot, fail)
    with pytest.raises(CodingRunInfrastructureError):
        asyncio.run(service.run())
    (request.state_dir / damaged).write_text("bad data")
    called = []
    service, _, _ = compose(
        request, snapshot, lambda m, i: called.append(1), resume=True
    )
    with pytest.raises(RuntimeError):
        asyncio.run(service.run())
    assert called == []


def test_explicit_binding_current_launch_roundtrip(tmp_path):
    request, _ = _request(tmp_path)
    assert "document_policy" not in request.model_dump()
    config = new_run_execution_config(
        model_provider=DeepSeekGateway.OFFICIAL,
        model_name="fake",
        http_retries=0,
    )
    launch = _LaunchManifest(
        schema_version="coding-run-launch-v9",
        response_enabled=False,
        capability_definitions=(
            CapabilityDefinitionBinding(
                capability_id="fixture",
                definition_sha256="a" * 64,
            ),
        ),
        context_policy_version=CONTEXT_POLICY_VERSION,
        skill_library=tmp_path,
        **config.model_dump(),
    )
    assert "document_policy" not in launch.model_dump()
    value = launch.model_dump() | {
        "document_policy": bound(tmp_path),
        "concept_retrieval_mode": "tool",
    }
    updated = _LaunchManifest.model_validate(value)
    assert _LaunchManifest.model_validate_json(updated.model_dump_json()) == updated
    with pytest.raises(ValidationError, match="bounded documents require concept"):
        _LaunchManifest.model_validate(
            launch.model_dump() | {"document_policy": bound(tmp_path)}
        )
    active_request = request.model_copy(update={"document_policy": bound(tmp_path)})
    with pytest.raises(ValueError, match="requires concept"):
        create_abaqus_coding_agent(FunctionModel(lambda m, i: ready()), active_request)
    for flag, value in (
        ("--document-policy-mode", "off"),
        ("--document-policy-spec", "changed.json"),
    ):
        args = build_parser().parse_args(
            ["--resume", str(request.state_dir), flag, value]
        )
        with pytest.raises(ValueError, match="document-policy"):
            _validate_resume_arguments(args)


def test_spec_paths_relative_to_file_and_not_overwritten(tmp_path):
    spec = bound(tmp_path).spec.model_dump(mode="json")
    spec["candidate"] = "candidate"
    path = tmp_path / "spec.json"
    payload = json.dumps(spec).encode()
    path.write_bytes(payload)
    parsed = load_document_policy_spec(path)
    assert parsed.candidate == tmp_path / "candidate"
    assert path.read_bytes() == payload
    path.write_text(json.dumps(spec | {"provider_api_key": "forbidden"}))
    with pytest.raises(ValidationError):
        load_document_policy_spec(path)


def test_cli_bounded_profile_persists_launch_and_ignores_edited_spec_on_resume(
    tmp_path, monkeypatch
):
    from abaqus_sim_agent.coding import cli
    from abaqus_sim_agent.evaluation import document_policy_adapter

    workspace, state = tmp_path / "workspace", tmp_path / "state"
    workspace.mkdir()
    spec_path = tmp_path / "spec.json"
    spec_path.write_text(bound(tmp_path).spec.model_dump_json())
    port, validator = FakePort(), FakeValidator()
    bindings = []

    def bind_spec(path):
        bindings.append(path)
        return bound(tmp_path)

    monkeypatch.setattr(cli, "_bind_document_policy", bind_spec)
    monkeypatch.setattr(
        document_policy_adapter,
        "compose_document_backend",
        lambda *a, **k: (port, validator),
    )
    monkeypatch.setattr(
        cli, "take_gateway_api_key_from_environment", lambda p: "fake-not-sent"
    )

    class Client:
        async def close(self):
            pass

    factory_calls = 0

    def factory(**kwargs):
        nonlocal factory_calls
        factory_calls += 1
        run = factory_calls
        calls = 0

        def model(messages, info):
            nonlocal calls
            calls += 1
            if run == 1 and calls == 1:
                return tool(
                    "search_abaqus_docs", {"query": first().model_dump(mode="json")}
                )
            if run == 1 and calls == 2:
                return tool(
                    "record_document_assessment",
                    assessment_payload(returned(messages, "search_abaqus_docs")),
                )
            if (run == 1 and calls == 3) or (run == 2 and calls == 1):
                return tool(
                    "write_file",
                    {"path": "report.txt", "content": f"fixture revision {run}\n"},
                )
            return ready()

        return FunctionModel(model), Client()

    monkeypatch.setattr(cli, "create_deepseek_chat_model", factory)
    catalog = tmp_path / "api-catalog"
    ApiCatalog([], runtime_release="2024").write(catalog)
    assert (
        cli.run_cli(
            [
                "--goal",
                "Create a deterministic document receipt",
                "--workspace",
                str(workspace),
                "--state-dir",
                str(state),
                "--run-id",
                "cli-document",
                "--document-policy-mode",
                "bounded_v1",
                "--document-policy-spec",
                str(spec_path),
                "--concept-retrieval-mode",
                "tool",
            ]
        )
        == 0
    )
    original_request, original_launch = (
        (state / "request.json").read_bytes(),
        (state / "launch.json").read_bytes(),
    )
    assert json.loads(original_launch)["schema_version"] == "coding-run-launch-v10"
    assert json.loads(original_launch)["reasoning_policy"] == "provider_default"
    assert json.loads(original_launch)["planning_enabled"] is False
    assert json.loads(original_request)["document_policy"]["mode"] == "bounded_v1"
    spec_path.write_text("broken after immutable launch")
    assert (
        cli.run_cli(
            [
                "--resume",
                str(state),
                "--goal",
                "Update the receipt for the new revision",
            ]
        )
        == 0
    )
    assert len(bindings) == 1
    assert len(port.calls) == 1
    assert load_resume_state(state).task_revision == 1
    assert load_resume_state(state).dependencies.concept_lookup_calls == 1
    assert (state / "request.json").read_bytes() == original_request
    assert (state / "launch.json").read_bytes() == original_launch
    assert cli.run_cli(["--resume", str(state), "--concept-retrieval-mode", "off"]) == 1
    assert factory_calls == 2


def test_unresolved_abaqus_effect_keeps_reconcile_ahead_of_document_restore(tmp_path):
    from pydantic_ai_harness.step_persistence import ToolEffectRecord

    from abaqus_sim_agent.coding.resume import CodingRunReconciliationRequired

    request, snapshot = _request(tmp_path)
    request = request.model_copy(update={"document_policy": bound(tmp_path)})

    def broken(m, i):
        raise ConnectionError("stop before tools")

    service, _, _ = compose(request, snapshot, broken)
    with pytest.raises(CodingRunInfrastructureError):
        asyncio.run(service.run())
    checkpoint = load_resume_state(request.state_dir).document_policy_checkpoint
    step_store = create_step_store(request.state_dir)
    runs = asyncio.run(step_store.list_runs(conversation_id=request.run_id))
    asyncio.run(
        step_store.record_tool_effect(
            ToolEffectRecord(
                tool_call_id="unknown-solver",
                tool_name="run_solver_job",
                run_id=runs[0].run_id,
                status="started",
                idempotency_key="unknown",
                effect_summary="solver in flight",
            )
        )
    )
    called = []
    resumed, _, _ = compose(
        request, snapshot, lambda m, i: called.append(1), resume=True
    )
    with pytest.raises(CodingRunReconciliationRequired):
        asyncio.run(resumed.run())
    persisted = load_resume_state(request.state_dir)
    assert persisted.status == "needs_reconcile"
    assert persisted.document_policy_checkpoint == checkpoint
    assert called == []


def test_harness_observes_followup_and_third_query_denial(tmp_path):
    request, snapshot = _request(tmp_path)
    request = request.model_copy(update={"document_policy": bound(tmp_path)})
    replies = []
    count = 0

    def model(messages, info):
        nonlocal count
        count += 1
        if count == 1:
            return tool(
                "search_abaqus_docs", {"query": first().model_dump(mode="json")}
            )
        if count == 2:
            replies.append(returned(messages, "search_abaqus_docs"))
            return tool(
                "record_document_assessment",
                assessment_payload(
                    replies[0], status="partial", next_action="follow_up"
                ),
            )
        if count == 3:
            return tool(
                "search_abaqus_docs",
                {
                    "query": {
                        "kind": "follow_up",
                        "question": "Clarify remaining evidence",
                        "follow_up_of": replies[0]["root_search_id"],
                        "unresolved_need_ids": [n["id"] for n in replies[0]["needs"]],
                    }
                },
            )
        if count == 4:
            replies.append(returned(messages, "search_abaqus_docs"))
            return tool(
                "record_document_assessment",
                assessment_payload(replies[1], status="missing", next_action="stop"),
            )
        if count == 5:
            return tool(
                "search_abaqus_docs",
                {
                    "query": {
                        "kind": "follow_up",
                        "question": "Try a third time",
                        "follow_up_of": replies[0]["root_search_id"],
                        "unresolved_need_ids": [n["id"] for n in replies[0]["needs"]],
                    }
                },
            )
        assert returned(messages, "search_abaqus_docs")["status"] == "blocked"
        return tool(
            "final_result_FailedDecision",
            {
                "status": "failed",
                "failure_stage": "knowledge_evidence",
                "summary": "Required evidence remains missing",
            },
        )

    port = FakePort()
    service, deps, runner = compose(request, snapshot, model, port=port)
    assert asyncio.run(service.run()).outcome.status.value == "failed"
    assert len(port.calls) == 2
    assert deps.concept_lookup_calls == 2
    assert runner.calls == []


def test_only_host_hash_bound_independent_operation_can_pass_a_knowledge_gap(tmp_path):
    from abaqus_sim_agent.coding.document_policy_binding import (
        DeclaredIndependentOperation,
    )
    from abaqus_sim_agent.coding.workspace import WorkspaceResolver

    request, snapshot = _request(tmp_path)
    script = request.workspace / "inspect.py"
    script.write_text("# host-approved independent inspection fixture\n")
    snapshot = WorkspaceResolver(captured_cwd=request.workspace).snapshot(
        request.workspace
    )
    need = DeclaredDocumentNeed(
        condition="required evidence",
        affects_step="dependent analysis",
        origin="task_contract",
    )
    binding = bound(tmp_path, needs=(need,))
    binding = binding.model_copy(
        update={
            "spec": binding.spec.model_copy(
                update={
                    "independent_operations": (
                        DeclaredIndependentOperation(
                            kind="cae",
                            relative_path="inspect.py",
                            sha256=hashlib.sha256(script.read_bytes()).hexdigest(),
                            independent_of_conditions=(need.condition,),
                            host_reference="Task owner approves this fixed script as independent",
                        ),
                    )
                }
            )
        }
    )
    request = request.model_copy(update={"document_policy": binding})
    port, verifier = FakePort(), FakeVerifier()
    port.kind = "unavailable"
    calls = 0

    def model(messages, info):
        nonlocal calls
        calls += 1
        if calls == 1:
            return tool(
                "search_abaqus_docs",
                {"query": {"kind": "first", "question": "Find required evidence"}},
            )
        if calls == 2:
            return tool(
                "record_document_assessment",
                assessment_payload(
                    returned(messages, "search_abaqus_docs"),
                    status="missing",
                    next_action="stop",
                ),
            )
        if calls == 3:
            return tool("run_cae_script", {"script_path": "inspect.py"})
        if calls == 4:
            assert returned(messages, "run_cae_script")["succeeded"]
            return tool(
                "write_file",
                {
                    "path": "inspect.py",
                    "content": "# model changed the approved bytes\n",
                },
            )
        if calls == 5:
            return tool("run_cae_script", {"script_path": "inspect.py"})
        if calls == 6:
            assert returned(messages, "run_cae_script")["blocked"]
            return ready()
        assert verifier.calls == 0
        return tool(
            "final_result_FailedDecision",
            {
                "status": "failed",
                "failure_stage": "knowledge_evidence",
                "summary": "Known critical condition remains missing",
            },
        )

    service, deps, runner = compose(
        request, snapshot, model, port=port, verifier=verifier
    )
    assert asyncio.run(service.run()).outcome.status.value == "failed"
    assert runner.calls == ["cae"]
    assert deps.abaqus_calls == 1


@pytest.mark.skipif(os.name != "nt", reason="Windows supervised validation worker")
@pytest.mark.asyncio
async def test_real_validation_worker_rejects_missing_dependencies_and_retains_diagnostics(
    tmp_path,
):
    from abaqus_sim_agent.evaluation.document_policy_adapter import (
        RuntimeDocumentReceiptValidator,
        runtime_paths,
    )
    from abaqus_sim_agent.retrieval.evidence_contracts import (
        QuestionQuery,
        SearchRequest,
    )

    validator = RuntimeDocumentReceiptValidator(
        runtime_paths(bound(tmp_path).spec), tmp_path / "validation"
    )
    query = SearchRequest(
        query=QuestionQuery(question="Explain documented units"),
        constraints_origin="host_typed",
    )
    result = await FakePort().search(
        query, search_id="test-receipt", deadline=time.monotonic() + 30
    )
    with pytest.raises(ValueError, match="validation failed"):
        await validator.validate(
            query, result, binding=BINDING, deadline=time.monotonic() + 30
        )
    [state] = list((tmp_path / "validation").iterdir())
    assert (state / "stderr.bin").read_bytes()
    assert json.loads((state / "receipt.json").read_bytes())["validated"] is False
