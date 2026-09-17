from pathlib import Path

import pytest
from pydantic import TypeAdapter, ValidationError

from abaqus_sim_agent.coding.contracts import (
    ApiCatalogBinding,
    ApiLookupTrace,
    ArtifactKind,
    ArtifactRef,
    ClarificationEvent,
    ClarificationKind,
    ClarificationRequest,
    CodingRunBudget,
    CodingRunDecision,
    CodingRunOutcome,
    CodingRunRequest,
    CodingRunStatus,
    DeliverableDeclaration,
    FailedDecision,
    NeedsInputDecision,
    ProbeAdoptionTrace,
    ProbeConclusionTrace,
    ProbeResultInput,
    ProbeTrace,
    ReadyForVerificationDecision,
    RuntimeProfile,
    RuntimeScriptPolicy,
    StaticPreflightBinding,
)


def make_runtime(tmp_path: Path) -> RuntimeProfile:
    return RuntimeProfile(launcher_path=(tmp_path / "abq2024.bat").resolve())


def test_request_is_frozen_and_requires_resolved_separate_paths(
    tmp_path: Path,
) -> None:
    workspace = (tmp_path / "workspace").resolve()
    state_dir = (tmp_path / "state").resolve()
    request = CodingRunRequest(
        run_id="run-001",
        goal="创建二维拉伸模型",
        workspace=workspace,
        state_dir=state_dir,
        runtime=make_runtime(tmp_path),
    )

    with pytest.raises(ValidationError, match="frozen"):
        request.goal = "changed"

    with pytest.raises(ValidationError, match="outside workspace"):
        CodingRunRequest(
            run_id="run-001",
            goal="创建二维拉伸模型",
            workspace=workspace,
            state_dir=workspace / "state",
            runtime=make_runtime(tmp_path),
        )


def test_request_rejects_relative_paths(tmp_path: Path) -> None:
    with pytest.raises(ValidationError, match="workspace must be absolute"):
        CodingRunRequest(
            run_id="run-001",
            goal="创建二维拉伸模型",
            workspace=Path("workspace"),
            state_dir=tmp_path.resolve(),
            runtime=make_runtime(tmp_path),
        )


def test_runtime_script_policy_normalizes_and_rejects_unsafe_paths() -> None:
    policy = RuntimeScriptPolicy(
        allowed_cae_scripts=(r"stages\build_model.py",),
        allowed_abaqus_python_scripts=("extract_results.py",),
        allowed_solver_inputs=("beam_tension.inp",),
    )

    assert policy.allowed_cae_scripts == ("stages/build_model.py",)
    with pytest.raises(ValidationError, match="stay relative"):
        RuntimeScriptPolicy(allowed_cae_scripts=("../probe.py",))
    with pytest.raises(ValidationError, match=r"\.py suffix"):
        RuntimeScriptPolicy(allowed_cae_scripts=("build_model.inp",))
    with pytest.raises(ValidationError, match="duplicate"):
        RuntimeScriptPolicy(
            allowed_solver_inputs=("Beam_Tension.inp", "beam_tension.inp")
        )


def test_enforced_aci_rejects_policy_or_allowlist_mismatch(tmp_path: Path) -> None:
    with pytest.raises(ValidationError, match="enforced policy version"):
        StaticPreflightBinding(
            mode="enforced_verified",
            engine_version="1.39.10",
            api_source_version="2024.10",
            api_source_sha256="a" * 64,
        )

    binding = StaticPreflightBinding(
        mode="enforced_verified",
        policy_version="abqpy-enforced-verified-v1",
        syntax_gate_version="python-3.10-production-syntax-v1",
        engine_version="1.39.10",
        api_source_version="2024.10",
        api_source_sha256="a" * 64,
    )
    with pytest.raises(ValidationError, match="explicit CAE"):
        CodingRunRequest(
            run_id="run-enforced-no-policy",
            goal="Invalid enforced profile.",
            workspace=(tmp_path / "workspace").resolve(),
            state_dir=(tmp_path / "state").resolve(),
            runtime=make_runtime(tmp_path),
            static_preflight=binding,
        )


def test_request_rejects_api_catalog_for_another_runtime(tmp_path: Path) -> None:
    with pytest.raises(ValidationError, match="runtime_release"):
        CodingRunRequest(
            run_id="run-wrong-catalog",
            goal="Create a model.",
            workspace=(tmp_path / "workspace").resolve(),
            state_dir=(tmp_path / "state").resolve(),
            runtime=make_runtime(tmp_path),
            api_catalog=ApiCatalogBinding(
                schema_version="1",
                cards_sha256="a" * 64,
                card_count=1,
                runtime_release="2022",
            ),
        )


def test_decision_uses_status_discriminator() -> None:
    adapter = TypeAdapter(CodingRunDecision)

    decision = adapter.validate_python(
        {
            "status": "needs_input",
            "clarification": {
                "field_id": "plate_thickness",
                "kind": "missing_physical_input",
                "question": "板厚是多少？",
                "impact": "板厚会改变平面应力截面刚度和反力。",
            },
            "summary": "缺少平面应力所需的板厚。",
        }
    )

    assert isinstance(decision, NeedsInputDecision)
    assert decision.clarification.field_id == "plate_thickness"
    assert decision.clarification.kind == "missing_physical_input"
    assert isinstance(
        adapter.validate_python(
            {
                "status": "failed",
                "failure_stage": "planning",
                "summary": "任务超出支持范围。",
            }
        ),
        FailedDecision,
    )


def test_ready_decision_requires_unique_workspace_deliverables() -> None:
    decision = ReadyForVerificationDecision.model_validate(
        {
            "status": "ready_for_verification",
            "deliverables": [
                {"relative_path": "scripts\\build.py"},
                {"relative_path": "model.inp"},
            ],
            "summary": "Requested deliverables are ready.",
        }
    )

    assert decision.deliverables == (
        DeliverableDeclaration(relative_path="scripts/build.py"),
        DeliverableDeclaration(relative_path="model.inp"),
    )
    with pytest.raises(ValidationError, match="deliverables"):
        ReadyForVerificationDecision(
            deliverables=(),
            summary="Nothing declared.",
        )
    with pytest.raises(ValidationError, match="duplicate"):
        ReadyForVerificationDecision(
            deliverables=(
                DeliverableDeclaration(relative_path="MODEL.inp"),
                DeliverableDeclaration(relative_path="model.inp"),
            ),
            summary="Duplicate paths.",
        )
    with pytest.raises(ValidationError, match="within the workspace"):
        DeliverableDeclaration(relative_path="../escape.py")


def test_budget_rejects_solver_calls_above_abaqus_calls() -> None:
    with pytest.raises(ValidationError, match="max_solver_calls"):
        CodingRunBudget(max_abaqus_calls=1, max_solver_calls=2)


def test_api_lookup_budget_defaults_to_four_and_trace_is_strict() -> None:
    assert CodingRunBudget().max_api_lookup_calls == 4
    assert CodingRunBudget().max_diagnostic_retrieval_calls == 2
    assert CodingRunBudget().wall_time_seconds == 2_700.0

    trace = ApiLookupTrace(
        source="automatic",
        query="Set",
        status="ambiguous",
        card_ids=("api:Part.Set", "api:Assembly.Set"),
        delivered_note_ids=("runtime:2024:Assembly.Set:native-sequence",),
        counted=False,
        payload_bytes=128,
    )

    assert ApiLookupTrace.model_validate_json(trace.model_dump_json()) == trace
    assert trace.delivered_note_ids == ("runtime:2024:Assembly.Set:native-sequence",)
    with pytest.raises(ValidationError, match="source"):
        ApiLookupTrace.model_validate(
            {
                **trace.model_dump(),
                "source": "model",
            }
        )


def test_probe_trace_is_strict_and_discriminated() -> None:
    conclusion = ProbeConclusionTrace(
        probe_id="probe-001",
        diagnostic_script="probe.py",
        invocation="cae_nogui",
        runtime_stage_succeeded=True,
        status="validated",
        claim="A side-aware Surface is accepted.",
        target_script="build_model.py",
        evidence="The candidate call created the expected load object.",
        target_sha256_before="a" * 64,
    )
    adoption = ProbeAdoptionTrace(
        probe_id="probe-001",
        target_script="build_model.py",
        target_sha256_before="a" * 64,
        target_sha256_after="b" * 64,
        production_invocation="cae_nogui",
        production_stage_succeeded=True,
    )
    adapter = TypeAdapter(ProbeTrace)

    assert adapter.validate_json(conclusion.model_dump_json()) == conclusion
    assert adapter.validate_json(adoption.model_dump_json()) == adoption
    with pytest.raises(ValidationError, match="target_script"):
        ProbeConclusionTrace(
            probe_id="probe-002",
            diagnostic_script="probe.py",
            invocation="cae_nogui",
            runtime_stage_succeeded=True,
            status="validated",
            claim="Candidate works.",
            evidence="Observed the expected object.",
        )
    assert (
        ProbeResultInput(
            status="validated",
            claim="Candidate works.",
            evidence="Runtime object was created.",
            target_script="build_model.py",
        ).target_script
        == "build_model.py"
    )
    with pytest.raises(ValidationError, match="must not declare"):
        ProbeResultInput(
            status="inconclusive",
            claim="More evidence is needed.",
            evidence="Introspection did not expose a signature.",
            target_script="build_model.py",
        )
    with pytest.raises(ValidationError, match="successful runtime"):
        ProbeConclusionTrace(
            probe_id="probe-003",
            diagnostic_script="probe.py",
            invocation="cae_nogui",
            runtime_stage_succeeded=False,
            status="validated",
            claim="Candidate works.",
            target_script="build_model.py",
            evidence="No successful runtime evidence exists.",
        )


def test_artifact_path_must_stay_inside_workspace() -> None:
    for invalid_path in ("../plate_tension.odb", "C:/plate_tension.odb"):
        with pytest.raises(ValidationError, match="within the workspace"):
            ArtifactRef(
                relative_path=invalid_path,
                size_bytes=1,
                sha256="0" * 64,
                kind=ArtifactKind.ODB,
                fresh=True,
            )


def test_failed_outcome_requires_failure_stage() -> None:
    with pytest.raises(ValidationError, match="failure_stage"):
        CodingRunOutcome(
            run_id="run-001",
            status=CodingRunStatus.FAILED,
            summary="执行失败。",
        )


def test_clarification_event_requires_answer_source_pair() -> None:
    with pytest.raises(ValidationError, match="answer and source"):
        ClarificationEvent(
            field_id="plate_thickness",
            question="板厚是多少？",
            kind="missing_physical_input",
            impact="板厚会改变平面应力截面刚度和反力。",
            answer="1 mm",
        )

    request = ClarificationRequest(
        field_id="plate_thickness",
        kind="missing_physical_input",
        question="板厚是多少？",
        impact="板厚会改变平面应力截面刚度和反力。",
    )
    assert request.field_id == "plate_thickness"

    with pytest.raises(ValidationError, match=r"kind|impact"):
        ClarificationEvent.model_validate(
            {
                "field_id": "plate_thickness",
                "question": "板厚是多少？",
                "answer": "1 mm",
                "source": "scripted",
            }
        )

    with pytest.raises(ValidationError, match="kind"):
        ClarificationRequest.model_validate(
            {
                "field_id": "plate_thickness",
                "question": "板厚是多少？",
                "impact": "板厚会改变截面刚度。",
            }
        )


@pytest.mark.parametrize(
    "kind",
    [
        "missing_physical_input",
        "ambiguous_physical_intent",
        "conflicting_user_constraint",
    ],
)
def test_clarification_request_accepts_only_policy_kinds(
    kind: ClarificationKind,
) -> None:
    request = ClarificationRequest(
        field_id="engineering_fact",
        kind=kind,
        question="Which engineering fact applies?",
        impact="The answer changes the physical model.",
    )

    assert request.kind == kind
    with pytest.raises(ValidationError, match="kind"):
        ClarificationRequest.model_validate(
            {
                "field_id": "engineering_fact",
                "kind": "api_uncertainty",
                "question": "Which API should be called?",
                "impact": "This is an implementation detail.",
            }
        )
