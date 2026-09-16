from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from types import MappingProxyType, SimpleNamespace
from typing import cast

import pytest

from abaqus_sim_agent.coding.contracts import (
    CodingRunBudget,
    CodingRunOutcome,
    CodingRunStatus,
    CodingRunUsage,
    DiagnosticRetrievalHitTrace,
    DiagnosticRetrievalIndexTrace,
    DiagnosticRetrievalTrace,
    RuntimeScriptPolicy,
)
from abaqus_sim_agent.coding.diagnostic_retrieval import (
    DIAGNOSTIC_QUERY_FORMULATION_VERSION,
    formulate_solver_diagnostic_query,
)
from abaqus_sim_agent.evaluation import plate_output_warning_repair as repair_evaluation
from abaqus_sim_agent.evaluation.plate_output_warning_repair import (
    FAULT_STATEMENT_TEMPLATE,
    FAULT_TRANSFORM_VERSION,
    GOLD_DOCUMENT_ID,
    GOLD_SECTION_ID,
    GOLD_SECTION_TITLE,
    RAW_RF3_WARNING,
    REPAIR_GOAL,
    PlateOutputWarningRepairObservation,
    _arm_contract_passed,
    _decode_persisted_json_contracts,
    _gate,
    _run_query_gate,
    _validate_persisted_agent_batch,
    _write_completion_seal,
    audit_completion_seal,
    build_dataset,
    derive_defective_seed,
    finalize_evaluation,
)
from abaqus_sim_agent.evaluation.verified_plate_seed import (
    PlateSeedProvenance,
    VerifiedPlateSeed,
)
from abaqus_sim_agent.execution import (
    AbaqusDiagnostic,
    AbaqusExecutionResult,
    AbaqusInvocation,
)
from abaqus_sim_agent.retrieval import AbaqusDocumentIndex, RetrievalHit
from abaqus_sim_agent.retrieval.types import Document
from abaqus_sim_agent.verification.plate_output_warning_repair_run import (
    PlateOutputWarningRepairVerificationAttempt,
    _allowed_build_repair,
)


def _source_seed(*, crlf: bool = False) -> VerifiedPlateSeed:
    build_text = (
        "left_set = object()\n"
        "model.FieldOutputRequest(name='F-Output-1', createStepName='Step-1',\n"
        "                         variables=('U', 'S', 'RF'))\n"
    )
    if crlf:
        build_text = build_text.replace("\n", "\r\n")
    build = build_text.encode()
    provenance = PlateSeedProvenance(
        source_run_id="source-run",
        source_run_dir="source",
        request_sha256="1" * 64,
        outcome_sha256="2" * 64,
        verification_sha256="3" * 64,
        artifact_sha256={"build_model.py": "4" * 64},
    )
    return cast(
        VerifiedPlateSeed,
        SimpleNamespace(
            seed_files=MappingProxyType(
                {
                    "build_model.py": build,
                    "extract_results.py": b"print('extract')\n",
                }
            ),
            provenance=provenance,
            outcome=CodingRunOutcome(
                run_id="source-run",
                status=CodingRunStatus.COMPLETED,
                summary="verified",
            ),
        ),
    )


def _write_passing_reference_phase(root: Path) -> None:
    state = root / "reference-rf3" / "state"
    state.mkdir(parents=True)
    result_path = state / "reference_result.json"
    result_path.write_text(
        PlateOutputWarningRepairVerificationAttempt(
            passed=True,
            feedback="passed",
            baseline_warning_reproduced=True,
            final_warning_removed=True,
            ordered_runtime_passed=True,
        ).model_dump_json(),
        encoding="utf-8",
    )
    (root / "phase_events.jsonl").write_text(
        json.dumps(
            {
                "event": "licensed_reference_passed",
                "reference_run_id": "reference-rf3",
                "reference_result_sha256": hashlib.sha256(
                    result_path.read_bytes()
                ).hexdigest(),
            }
        )
        + "\n",
        encoding="utf-8",
    )


def test_defective_seed_is_one_bound_transform_without_mutating_source() -> None:
    source = _source_seed()
    original = source.seed_files["build_model.py"]

    defective = derive_defective_seed(source)

    assert source.seed_files["build_model.py"] == original
    fault = FAULT_STATEMENT_TEMPLATE.format(region="left_set").encode()
    assert fault.rstrip() in defective.seed_files["build_model.py"]
    assert defective.provenance.transform_version == FAULT_TRANSFORM_VERSION
    assert defective.provenance.source_build_sha256 != (
        defective.provenance.defective_build_sha256
    )
    assert defective.seed_files["extract_results.py"] == b"print('extract')\n"
    assert "source_run_dir" not in defective.provenance.model_dump_json()


def test_allowed_repair_handles_crlf_and_accepts_only_reaction_components() -> None:
    source = _source_seed(crlf=True)
    source_text = source.seed_files["build_model.py"].decode()
    defective = derive_defective_seed(source)
    defective_text = defective.seed_files["build_model.py"].decode()

    assert _allowed_build_repair(
        source_text.replace("\r\n", "\n"),
        source_text=source_text,
        defective_text=defective_text,
    )
    assert _allowed_build_repair(
        defective_text.replace("'RF3'", "'RF1'"),
        source_text=source_text,
        defective_text=defective_text,
    )
    fault = FAULT_STATEMENT_TEMPLATE.format(region="left_set").rstrip("\n")
    assert _allowed_build_repair(
        defective_text.replace(fault, "\n"),
        source_text=source_text,
        defective_text=defective_text,
    )
    assert not _allowed_build_repair(
        defective_text.replace("'RF3'", "'U1'"),
        source_text=source_text,
        defective_text=defective_text,
    )
    assert not _allowed_build_repair(
        source_text.replace("left_set", "right_set"),
        source_text=source_text,
        defective_text=defective_text,
    )


def test_query_gate_uses_frozen_rrf_and_exact_gold_section() -> None:
    calls: list[tuple[str, int, int, int]] = []

    class _Index:
        def search_hybrid(
            self,
            query: str,
            *,
            top_k: int,
            fetch_k: int,
            rrf_k: int,
        ) -> list[RetrievalHit]:
            calls.append((query, top_k, fetch_k, rrf_k))
            return [
                RetrievalHit(
                    document=Document(
                        id=f"{GOLD_DOCUMENT_ID}#chunk",
                        page_content="Active degrees of freedom 1, 2",
                        metadata={
                            "document_id": GOLD_DOCUMENT_ID,
                            "section_id": GOLD_SECTION_ID,
                            "section_title": GOLD_SECTION_TITLE,
                        },
                    ),
                    match_type="hybrid",
                )
            ]

    gate = _run_query_gate(cast(AbaqusDocumentIndex, _Index()))

    assert gate.passed
    assert gate.gold_rank == 1
    assert calls == [(gate.query, 4, 20, 60)]
    assert "1 2" not in gate.query


def test_dataset_interleaves_three_control_and_advisory_cases() -> None:
    dataset = build_dataset("source-run", repeats=3)

    assert [case.name for case in dataset.cases] == [
        "rf3-control-r1",
        "rf3-advisory-r1",
        "rf3-advisory-r2",
        "rf3-control-r2",
        "rf3-control-r3",
        "rf3-advisory-r3",
    ]


def test_gate_requires_each_arm_to_pass_independently() -> None:
    passed_attempt = PlateOutputWarningRepairVerificationAttempt(
        passed=True,
        feedback="passed",
    )
    observations = [
        PlateOutputWarningRepairObservation(
            arm=arm,
            replicate=replicate,
            run_id=f"{arm}-{replicate}",
            outcome=CodingRunOutcome(
                run_id=f"{arm}-{replicate}",
                status=CodingRunStatus.COMPLETED,
                summary="completed",
            ),
            verification=passed_attempt,
            arm_contract_passed=True,
            advisory_relevant=arm == "advisory",
            advisory_gold_rank=1 if arm == "advisory" else None,
            abaqus_runtime_seconds=0.0,
        )
        for replicate in range(1, 4)
        for arm in ("control", "advisory")
    ]
    gate = _gate(observations, repeats=3, required_passes=2)

    assert gate.evaluation_complete
    assert gate.both_arms_smoke_gate_passed
    assert gate.control.passes == 3
    assert gate.advisory.passes == 3
    assert gate.advisory_pass_delta == 0
    assert not gate.observed_pass_improvement


def test_completion_seal_detects_any_evidence_mutation(tmp_path: Path) -> None:
    (tmp_path / "experiment_manifest.json").write_text(
        json.dumps({"environment_seal_sha256": "a" * 64}),
        encoding="utf-8",
    )
    (tmp_path / "evaluation_summary.json").write_text(
        json.dumps({"run_ids": []}),
        encoding="utf-8",
    )
    evidence = tmp_path / "other-evidence.json"
    evidence.write_text('{"status":"completed"}', encoding="utf-8")
    _write_passing_reference_phase(tmp_path)

    seal = _write_completion_seal(
        tmp_path,
        (),
        environment_seal_sha256="a" * 64,
    )

    assert audit_completion_seal(tmp_path) == seal
    seal_path = tmp_path / "completion_seal.json"
    original_seal = seal_path.read_bytes()
    tampered = json.loads(original_seal)
    tampered["environment_seal_sha256"] = "b" * 64
    seal_path.write_text(json.dumps(tampered), encoding="utf-8")
    with pytest.raises(ValueError, match="environment hash mismatch"):
        audit_completion_seal(tmp_path)
    seal_path.write_bytes(original_seal)
    (tmp_path / "evaluation_summary.json").write_text(
        json.dumps({"run_ids": ["ghost-run"]}),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="run IDs mismatch"):
        audit_completion_seal(tmp_path)
    (tmp_path / "evaluation_summary.json").write_text(
        json.dumps({"run_ids": []}),
        encoding="utf-8",
    )
    evidence.write_text('{"status":"failed"}', encoding="utf-8")
    with pytest.raises(ValueError, match="file binding mismatch"):
        audit_completion_seal(tmp_path)


def test_arm_contract_rejects_control_pollution_and_accepts_strict_duplicates(
    tmp_path: Path,
) -> None:
    manifest_path = tmp_path / "manifest.json"
    bm25_path = tmp_path / "lexical.json"
    manifest_path.write_text("dense", encoding="utf-8")
    bm25_path.write_text("sparse", encoding="utf-8")

    class _Index:
        def __init__(self) -> None:
            self.manifest_path = manifest_path
            self.bm25_manifest_path = bm25_path

        def read_manifest(self) -> SimpleNamespace:
            return SimpleNamespace(release="2026 FD02", collection_name="fixture")

        def read_bm25_manifest(self) -> SimpleNamespace:
            return SimpleNamespace(
                database_sha256="d" * 64,
                semantic_corpus_sha256="e" * 64,
            )

    diagnostic = AbaqusDiagnostic(
        level="warning",
        source="plate_tension.dat",
        line=10,
        message=RAW_RF3_WARNING,
    )
    execution = AbaqusExecutionResult(
        invocation=AbaqusInvocation.SOLVER_JOB,
        command=("abq2024.bat", "job=plate_tension"),
        workspace=tmp_path,
        exit_code=0,
        timed_out=False,
        duration_seconds=1.0,
        stdout="ok",
        stderr="",
        stdout_encoding="utf-8",
        stderr_encoding="utf-8",
        stdout_raw_path=tmp_path / "stdout.bin",
        stderr_raw_path=tmp_path / "stderr.bin",
        diagnostics=(diagnostic,),
    )
    query = formulate_solver_diagnostic_query(REPAIR_GOAL, diagnostic.message)
    index_trace = DiagnosticRetrievalIndexTrace(
        release="2026 FD02",
        collection_name="fixture",
        dense_manifest_sha256=hashlib.sha256(b"dense").hexdigest(),
        bm25_manifest_sha256=hashlib.sha256(b"sparse").hexdigest(),
        bm25_database_sha256="d" * 64,
        semantic_corpus_sha256="e" * 64,
    )
    primary = DiagnosticRetrievalTrace(
        run_id="advisory-run",
        recorded_at=datetime.now(UTC),
        amendment_sequence=0,
        task_revision=0,
        execution_index=0,
        diagnostic_level="warning",
        diagnostic_source=diagnostic.source,
        diagnostic_line=diagnostic.line,
        diagnostic_sha256=hashlib.sha256(
            " ".join(diagnostic.message.split()).encode()
        ).hexdigest(),
        formulation_version=DIAGNOSTIC_QUERY_FORMULATION_VERSION,
        query=query,
        query_sha256=hashlib.sha256(query.encode()).hexdigest(),
        index=index_trace,
        status="found",
        counted=True,
        ranked_hits=(
            DiagnosticRetrievalHitTrace(
                rank=1,
                document_id=GOLD_DOCUMENT_ID,
                section_id=GOLD_SECTION_ID,
                section_title=GOLD_SECTION_TITLE,
            ),
        ),
        evidence_id="f" * 64,
        payload_bytes=10,
        elapsed_ms=1.0,
    )
    duplicate = primary.model_copy(
        update={
            "recorded_at": datetime.now(UTC),
            "execution_index": 1,
            "status": "duplicate",
            "counted": False,
            "ranked_hits": (),
            "evidence_id": None,
            "payload_bytes": 5,
            "elapsed_ms": 0.0,
        }
    )
    verification = PlateOutputWarningRepairVerificationAttempt(
        passed=True,
        feedback="passed",
        baseline_warning_execution_index=0,
    )
    advisory_outcome = CodingRunOutcome(
        run_id="advisory-run",
        status=CodingRunStatus.COMPLETED,
        summary="completed",
        usage=CodingRunUsage(diagnostic_retrieval_calls=1),
    )
    control_outcome = CodingRunOutcome(
        run_id="control-run",
        status=CodingRunStatus.COMPLETED,
        summary="completed",
    )
    index = cast(AbaqusDocumentIndex, _Index())

    assert _arm_contract_passed(
        "advisory",
        "advisory-run",
        advisory_outcome,
        verification,
        (primary, duplicate),
        (execution, execution),
        index,
    )
    assert not _arm_contract_passed(
        "control",
        "control-run",
        control_outcome,
        verification,
        (primary,),
        (execution,),
        index,
    )
    assert not _arm_contract_passed(
        "advisory",
        "wrong-run",
        advisory_outcome,
        verification,
        (primary,),
        (execution,),
        index,
    )


def test_agent_batch_validation_rejects_phase_and_query_gate_drift(
    tmp_path: Path,
) -> None:
    batch_path = tmp_path / "agent_batch.json"
    batch_path.write_text("{}", encoding="utf-8")
    (tmp_path / "phase_events.jsonl").write_text(
        json.dumps(
            {
                "event": "agent_batch_completed",
                "agent_batch_sha256": "0" * 64,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="batch hash"):
        _validate_persisted_agent_batch(
            tmp_path,
            cast(AbaqusDocumentIndex, object()),
        )

    batch_hash = hashlib.sha256(batch_path.read_bytes()).hexdigest()
    (tmp_path / "phase_events.jsonl").write_text(
        json.dumps(
            {
                "event": "agent_batch_completed",
                "agent_batch_sha256": batch_hash,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    (tmp_path / "query_gate.json").write_text("gate", encoding="utf-8")
    (tmp_path / "query_gate_attempts.jsonl").write_text("attempt\n", encoding="utf-8")
    (tmp_path / "experiment_manifest.json").write_text(
        json.dumps(
            {
                "query_gate_sha256": "1" * 64,
                "query_gate_attempts_sha256": hashlib.sha256(b"attempt\n").hexdigest(),
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="query gate differs"):
        _validate_persisted_agent_batch(
            tmp_path,
            cast(AbaqusDocumentIndex, object()),
        )


def test_persisted_batch_contracts_decode_with_json_semantics() -> None:
    observation = PlateOutputWarningRepairObservation(
        arm="control",
        replicate=1,
        run_id="control-1",
        outcome=CodingRunOutcome(
            run_id="control-1",
            status=CodingRunStatus.COMPLETED,
            summary="completed",
        ),
        arm_contract_passed=True,
        advisory_relevant=False,
        abaqus_runtime_seconds=0.0,
    )
    budget = CodingRunBudget()
    policy = RuntimeScriptPolicy(
        allowed_cae_scripts=("build_model.py",),
        allowed_abaqus_python_scripts=("extract_results.py",),
        allowed_solver_inputs=("plate_tension.inp",),
    )
    batch = json.loads(
        json.dumps({"observations": [observation.model_dump(mode="json")]})
    )
    manifest = json.loads(
        json.dumps(
            {
                "budget": budget.model_dump(mode="json"),
                "runtime_script_policy": policy.model_dump(mode="json"),
            }
        )
    )

    restored_observations, restored_budget, restored_policy = (
        _decode_persisted_json_contracts(batch, manifest)
    )

    assert restored_observations == (observation,)
    assert restored_budget == budget
    assert restored_policy == policy


def test_finalize_continues_from_persisted_batch_without_agent_execution(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _source_seed()
    defective = derive_defective_seed(source)
    observations = tuple(
        PlateOutputWarningRepairObservation(
            arm=arm,
            replicate=1,
            run_id=f"{arm}-1",
            outcome=CodingRunOutcome(
                run_id=f"{arm}-1",
                status=CodingRunStatus.COMPLETED,
                summary="completed",
            ),
            verification=PlateOutputWarningRepairVerificationAttempt(
                passed=True,
                feedback="passed",
            ),
            arm_contract_passed=True,
            advisory_relevant=arm == "advisory",
            advisory_gold_rank=1 if arm == "advisory" else None,
            abaqus_runtime_seconds=0.0,
        )
        for arm in ("control", "advisory")
    )
    (tmp_path / "experiment_manifest.json").write_text(
        json.dumps(
            {
                "environment_seal_sha256": "a" * 64,
                "defective_seed": defective.provenance.model_dump(mode="json"),
                "case_order": ["rf3-control-r1", "rf3-advisory-r1"],
                "required_passes_per_arm": 1,
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "agent_batch.json").write_text(
        json.dumps(
            {
                "name": "persisted-batch",
                "experiment_metadata": {},
                "cases": [],
                "failures": [],
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "agent_batch_report.txt").write_text("persisted", encoding="utf-8")

    monkeypatch.setattr(
        repair_evaluation,
        "load_verified_plate_seed",
        lambda *_args, **_kwargs: source,
    )
    monkeypatch.setattr(
        repair_evaluation,
        "open_concept_index",
        lambda *_args, **_kwargs: object(),
    )
    monkeypatch.setattr(
        repair_evaluation,
        "audit_reproduction",
        lambda *_args, **_kwargs: SimpleNamespace(seal_sha256="a" * 64),
    )
    monkeypatch.setattr(
        repair_evaluation,
        "_verify_experiment_implementation",
        lambda _root: None,
    )
    monkeypatch.setattr(
        repair_evaluation,
        "_validate_persisted_agent_batch",
        lambda _root, _index: observations,
    )

    reference_calls: list[str] = []

    async def fake_reference(
        root: Path,
        _defective: object,
        *,
        run_id: str,
    ) -> PlateOutputWarningRepairVerificationAttempt:
        reference_calls.append(run_id)
        result = PlateOutputWarningRepairVerificationAttempt(
            passed=True,
            feedback="passed",
            baseline_warning_reproduced=True,
            final_warning_removed=True,
            ordered_runtime_passed=True,
        )
        state = root / run_id / "state"
        state.mkdir(parents=True)
        (state / "reference_result.json").write_text(
            result.model_dump_json(),
            encoding="utf-8",
        )
        return result

    sealed_runs: list[tuple[str, ...]] = []
    monkeypatch.setattr(repair_evaluation, "_run_reference", fake_reference)
    monkeypatch.setattr(
        repair_evaluation,
        "_write_completion_seal",
        lambda _root, items, **_kwargs: sealed_runs.append(
            tuple(item.run_id for item in items)
        ),
    )

    gate = asyncio.run(
        finalize_evaluation(
            tmp_path,
            source_run_dir=tmp_path / "source",
            concept_index_dir=tmp_path / "index",
            environment_seal=tmp_path / "environment-seal.json",
            repository_root=tmp_path,
            repeats=1,
            required_passes=1,
        )
    )

    assert gate.control.passes == 1
    assert gate.advisory.passes == 1
    assert reference_calls == ["reference-rf3"]
    assert sealed_runs == [("control-1", "advisory-1")]
    phases = [
        json.loads(line)["event"]
        for line in (tmp_path / "phase_events.jsonl").read_text().splitlines()
    ]
    assert phases == [
        "finalization_resumed",
        "licensed_reference_passed",
        "post_run_attestation_passed",
        "final_evidence_ready",
    ]


def test_finalize_is_idempotent_after_completion_seal(tmp_path: Path) -> None:
    (tmp_path / "experiment_manifest.json").write_text(
        json.dumps({"environment_seal_sha256": "a" * 64}),
        encoding="utf-8",
    )
    (tmp_path / "evaluation_summary.json").write_text(
        json.dumps({"run_ids": []}),
        encoding="utf-8",
    )
    gate = _gate((), repeats=1, required_passes=1)
    (tmp_path / "gate.json").write_text(gate.model_dump_json(), encoding="utf-8")
    _write_passing_reference_phase(tmp_path)
    _write_completion_seal(
        tmp_path,
        (),
        environment_seal_sha256="a" * 64,
    )

    restored = asyncio.run(
        finalize_evaluation(
            tmp_path,
            source_run_dir=tmp_path / "unused-source",
            concept_index_dir=tmp_path / "unused-index",
            environment_seal=tmp_path / "unused-seal",
            repository_root=tmp_path,
            repeats=1,
            required_passes=1,
        )
    )

    assert restored == gate
