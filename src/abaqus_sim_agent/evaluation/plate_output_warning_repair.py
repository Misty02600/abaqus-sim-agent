from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from importlib.metadata import version
from pathlib import Path
from types import MappingProxyType
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter
from pydantic_evals import Case, Dataset
from pydantic_evals.dataset import increment_eval_metric, set_eval_attribute
from pydantic_evals.evaluators import EvaluationReason, Evaluator, EvaluatorContext
from pydantic_evals.reporting import EvaluationReport

from abaqus_sim_agent.coding.concept_lookup import (
    concept_index_binding,
    open_concept_index,
)
from abaqus_sim_agent.coding.contracts import (
    CodingRunBudget,
    CodingRunOutcome,
    CodingRunRequest,
    CodingRunStatus,
    DeliverableDeclaration,
    DiagnosticRetrievalTrace,
    ReadyForVerificationDecision,
    RuntimeProfile,
    RuntimeScriptPolicy,
)
from abaqus_sim_agent.coding.diagnostic_retrieval import (
    DIAGNOSTIC_FETCH_K,
    DIAGNOSTIC_QUERY_FORMULATION_VERSION,
    DIAGNOSTIC_RRF_K,
    DIAGNOSTIC_TOP_K,
    formulate_solver_diagnostic_query,
)
from abaqus_sim_agent.coding.harness_compat import (
    apply_harness_windows_jsonl_append_patch,
    apply_harness_windows_newline_hash_patch,
    apply_harness_windows_shell_tree_patch,
)
from abaqus_sim_agent.coding.model import (
    DEEPSEEK_V4_FLASH,
    DEFAULT_MODEL_HTTP_RETRIES,
    DEFAULT_MODEL_HTTP_TIMEOUT_SECONDS,
    DeepSeekGateway,
    gateway_base_url,
    take_gateway_api_key_from_environment,
)
from abaqus_sim_agent.coding.resume import (
    append_execution_result,
    load_diagnostic_retrieval_traces,
    load_execution_results,
)
from abaqus_sim_agent.evaluation.model_change_runtime import (
    create_seeded_workspace_async,
    execute_model_change_case,
)
from abaqus_sim_agent.evaluation.verified_plate_seed import (
    VerifiedPlateSeed,
    load_verified_plate_seed,
    sha256_bytes,
    sha256_file,
)
from abaqus_sim_agent.execution import (
    DEFAULT_ABAQUS_2024_LAUNCHER,
    Abaqus2024Runner,
    AbaqusExecutionResult,
)
from abaqus_sim_agent.retrieval import AbaqusDocumentIndex, RetrievalHit
from abaqus_sim_agent.retrieval.reproduction import audit_reproduction
from abaqus_sim_agent.skills import skill_library_path
from abaqus_sim_agent.verification.assets import plate_tension_oracle_script
from abaqus_sim_agent.verification.plate_output_warning_repair_run import (
    RF3_INACTIVE_WARNING,
    PlateOutputWarningRepairRunVerifier,
    PlateOutputWarningRepairVerificationAttempt,
)

PROMPT_VERSION = "plate-output-warning-repair-v1"
FAULT_TRANSFORM_VERSION = "inject-rf3-history-output-v1"
FAULT_STATEMENT_TEMPLATE = (
    "model.historyOutputRequests['H-Output-1'].setValues(\n"
    "    region={region}, variables=('RF3',))\n"
)
_FIELD_OUTPUT_LINE = re.compile(
    r"(?m)^(?P<line>[^\n]*variables=\('U', 'S', 'RF'\)\))\r?$"
)
RAW_RF3_WARNING = (
    "***WARNING: OUTPUT REQUEST RF3 IS NOT VALID SINCE IT REQUESTS OUTPUT "
    "AT A DOF THAT IS NOT ACTIVE"
)
GOLD_DOCUMENT_ID = "simaelm-r-2delem"
GOLD_SECTION_ID = "simaelm-r-2delem-t-elemtypes1"
GOLD_SECTION_TITLE = "Plane Stress Elements"
MODEL_REQUEST_BUDGET = 24
ABAQUS_CALL_BUDGET = 8
SOLVER_CALL_BUDGET = 3

REPAIR_GOAL = """
Repair the existing verified CPS4R plane stress plate model. Before editing any
file, run the existing build_model.py and plate_tension.inp unchanged to
reproduce its current Solver output-request warning. Then repair only the
output-request defect in build_model.py and rerun the required CAE, Solver, and
extract_results.py stages.

Preserve the existing geometry, 1 mm thickness, material, CPS4R 20 x 4 mesh,
boundary conditions, Static General step with NLGEOM=OFF, and U/S/RF field
output. Do not modify extract_results.py or edit the generated INP directly.
All physical inputs are complete; do not ask for clarification. Inspect
results.json before requesting verification.
""".strip()


class _FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class PlateSourceEvidence(_FrozenModel):
    source_run_id: str
    request_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    outcome_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    verification_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    artifact_sha256: dict[str, str]


class DefectivePlateSeedProvenance(_FrozenModel):
    source: PlateSourceEvidence
    transform_version: Literal["inject-rf3-history-output-v1"] = FAULT_TRANSFORM_VERSION
    source_build_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    defective_build_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    extractor_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    fault_statement_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


@dataclass(frozen=True, slots=True)
class DefectivePlateSeed:
    source: VerifiedPlateSeed
    provenance: DefectivePlateSeedProvenance
    seed_files: Mapping[str, bytes]

    @property
    def outcome(self) -> CodingRunOutcome:
        return self.source.outcome

    @property
    def seed_texts(self) -> dict[str, str]:
        return {
            path: content.decode("utf-8") for path, content in self.seed_files.items()
        }


DiagnosticArm = Literal["control", "advisory"]


class PlateOutputWarningRepairInput(_FrozenModel):
    arm: DiagnosticArm
    replicate: int = Field(ge=1)
    goal: str = REPAIR_GOAL


class PlateOutputWarningRepairMetadata(_FrozenModel):
    arm: DiagnosticArm
    replicate: int = Field(ge=1)
    source_run_id: str


class PlateOutputWarningRepairObservation(_FrozenModel):
    arm: DiagnosticArm
    replicate: int = Field(ge=1)
    run_id: str
    outcome: CodingRunOutcome
    verification: PlateOutputWarningRepairVerificationAttempt | None = None
    diagnostic_traces: tuple[DiagnosticRetrievalTrace, ...] = ()
    arm_contract_passed: bool
    advisory_relevant: bool
    advisory_gold_rank: int | None = Field(default=None, ge=1)
    abaqus_runtime_seconds: float = Field(ge=0)


class ArmGate(_FrozenModel):
    arm: DiagnosticArm
    attempts: int = Field(ge=0)
    passes: int = Field(ge=0)
    required_passes: int = Field(ge=1)
    smoke_gate_passed: bool


class PlateOutputWarningRepairGate(_FrozenModel):
    control: ArmGate
    advisory: ArmGate
    advisory_pass_delta: int
    observed_pass_improvement: bool
    evaluation_complete: bool
    both_arms_smoke_gate_passed: bool


class DiagnosticQueryGate(_FrozenModel):
    query: str
    query_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    formulation_version: str
    top_k: Literal[4] = 4
    fetch_k: Literal[20] = 20
    rrf_k: Literal[60] = 60
    gold_document_id: str
    gold_section_id: str
    gold_section_title: str
    gold_rank: int | None = Field(default=None, ge=1)
    passed: bool
    retrieved: tuple[tuple[str, str, str], ...]


class CompletionFileBinding(_FrozenModel):
    path: str = Field(min_length=1)
    size_bytes: int = Field(ge=0)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class PlateOutputWarningRepairCompletionSeal(_FrozenModel):
    schema_version: Literal["plate-output-warning-repair-completion-v1"] = (
        "plate-output-warning-repair-completion-v1"
    )
    created_at: datetime
    environment_seal_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    experiment_manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    reference_run_id: str = Field(min_length=1)
    reference_result_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    run_ids: tuple[str, ...]
    files: tuple[CompletionFileBinding, ...] = Field(min_length=1)


@dataclass
class PlateOutputWarningRepairEvaluator(
    Evaluator[
        PlateOutputWarningRepairInput,
        PlateOutputWarningRepairObservation,
        PlateOutputWarningRepairMetadata,
    ]
):
    def evaluate(
        self,
        ctx: EvaluatorContext[
            PlateOutputWarningRepairInput,
            PlateOutputWarningRepairObservation,
            PlateOutputWarningRepairMetadata,
        ],
    ) -> dict[str, EvaluationReason | float | str]:
        output = ctx.output
        verification = output.verification
        closed_loop_pass = (
            output.outcome.status is CodingRunStatus.COMPLETED
            and not output.outcome.clarifications
            and verification is not None
            and verification.passed
            and output.arm_contract_passed
            and (
                output.arm == "control"
                or (bool(output.diagnostic_traces) and output.advisory_relevant)
            )
        )
        return {
            "closed_loop_pass": EvaluationReason(
                value=closed_loop_pass,
                reason=(
                    "warning reproduced, repaired, and independently verified"
                    if closed_loop_pass
                    else "repair failed at "
                    + (output.outcome.failure_stage or "verification")
                ),
            ),
            "baseline_warning_reproduced": float(
                verification is not None and verification.baseline_warning_reproduced
            ),
            "final_warning_removed": float(
                verification is not None and verification.final_warning_removed
            ),
            "advisory_delivered": float(bool(output.diagnostic_traces)),
            "advisory_relevant": float(output.advisory_relevant),
            "arm_contract_passed": float(output.arm_contract_passed),
            "advisory_gold_rank": str(output.advisory_gold_rank or "none"),
            "repair_kind": (
                verification.repair_kind
                if verification is not None and verification.repair_kind is not None
                else "none"
            ),
            "failure_stage": output.outcome.failure_stage or "none",
        }


def derive_defective_seed(source: VerifiedPlateSeed) -> DefectivePlateSeed:
    source_build = source.seed_files["build_model.py"]
    build_text = source_build.decode("utf-8")
    matches = list(_FIELD_OUTPUT_LINE.finditer(build_text))
    if len(matches) != 1:
        raise ValueError("verified build script lacks the frozen output tuple")
    if re.search(r"(?m)^leftSet\s*=", build_text):
        region = "leftSet"
    elif re.search(r"(?m)^left_set\s*=", build_text):
        region = "left_set"
    else:
        raise ValueError("verified build script lacks a supported left region")
    fault_statement = FAULT_STATEMENT_TEMPLATE.format(region=region)
    match = matches[0]
    defective_text = (
        build_text[: match.end()]
        + "\n\n"
        + fault_statement.rstrip("\n")
        + build_text[match.end() :]
    )
    defective_build = defective_text.encode("utf-8")
    extractor = source.seed_files["extract_results.py"]
    provenance = DefectivePlateSeedProvenance(
        source=PlateSourceEvidence(
            source_run_id=source.provenance.source_run_id,
            request_sha256=source.provenance.request_sha256,
            outcome_sha256=source.provenance.outcome_sha256,
            verification_sha256=source.provenance.verification_sha256,
            artifact_sha256=source.provenance.artifact_sha256,
        ),
        source_build_sha256=sha256_bytes(source_build),
        defective_build_sha256=sha256_bytes(defective_build),
        extractor_sha256=sha256_bytes(extractor),
        fault_statement_sha256=sha256_bytes(fault_statement.encode("utf-8")),
    )
    return DefectivePlateSeed(
        source=source,
        provenance=provenance,
        seed_files=MappingProxyType(
            {
                "build_model.py": defective_build,
                "extract_results.py": extractor,
            }
        ),
    )


def build_dataset(
    source_run_id: str,
    *,
    repeats: int,
) -> Dataset[
    PlateOutputWarningRepairInput,
    PlateOutputWarningRepairObservation,
    PlateOutputWarningRepairMetadata,
]:
    cases = []
    for replicate in range(1, repeats + 1):
        arms = ("control", "advisory") if replicate % 2 else ("advisory", "control")
        for arm in arms:
            cases.append(
                Case(
                    name=f"rf3-{arm}-r{replicate}",
                    inputs=PlateOutputWarningRepairInput(
                        arm=arm,
                        replicate=replicate,
                    ),
                    metadata=PlateOutputWarningRepairMetadata(
                        arm=arm,
                        replicate=replicate,
                        source_run_id=source_run_id,
                    ),
                )
            )
    return Dataset(
        name="abaqus2024_plate_output_warning_repair_v1",
        cases=cases,
        evaluators=[PlateOutputWarningRepairEvaluator()],
    )


async def run_evaluation(
    run_root: Path,
    *,
    source_run_dir: Path,
    concept_index_dir: Path,
    environment_seal: Path,
    repository_root: Path,
    model_gateway: DeepSeekGateway = DeepSeekGateway.OPENCODE_GO,
    model_name: str = DEEPSEEK_V4_FLASH,
    http_retries: int = DEFAULT_MODEL_HTTP_RETRIES,
    repeats: int = 3,
    required_passes: int = 2,
) -> PlateOutputWarningRepairGate:
    if repeats < 1:
        raise ValueError("repeats must be positive")
    if required_passes < 1 or required_passes > repeats:
        raise ValueError("required_passes must be between 1 and repeats")
    root = await asyncio.to_thread(run_root.resolve)
    if await asyncio.to_thread(root.exists):
        raise FileExistsError(f"evaluation run root already exists: {root}")
    await asyncio.to_thread(root.mkdir, parents=True)
    _append_phase(root, "initialized")
    source = load_verified_plate_seed(source_run_dir, forbidden_root=root)
    defective = derive_defective_seed(source)
    index = open_concept_index(concept_index_dir, forbidden_root=root)
    environment = audit_reproduction(
        index,
        environment_seal,
        repository_root=repository_root,
    )
    query_gate_attempts = root / "query_gate_attempts.jsonl"
    _append_jsonl(
        query_gate_attempts,
        _query_gate_started(index, environment.seal_sha256),
    )
    try:
        query_gate = _run_query_gate(index)
    except Exception as exc:
        _append_jsonl(
            query_gate_attempts,
            {
                "event": "failed",
                "completed_at": datetime.now(UTC).isoformat(),
                "rankings_emitted": True,
                "error_type": type(exc).__name__,
            },
        )
        raise
    (root / "query_gate.json").write_text(
        query_gate.model_dump_json(indent=2),
        encoding="utf-8",
    )
    _append_jsonl(
        query_gate_attempts,
        {
            "event": "completed",
            "completed_at": datetime.now(UTC).isoformat(),
            "rankings_emitted": True,
            "passed": query_gate.passed,
            "gold_rank": query_gate.gold_rank,
            "query_gate_sha256": sha256_file(root / "query_gate.json"),
        },
    )
    if not query_gate.passed:
        raise RuntimeError(
            "RF3 diagnostic formulation did not retrieve its gold section"
        )
    _append_phase(root, "query_gate_passed")
    api_key = take_gateway_api_key_from_environment(model_gateway)
    apply_harness_windows_newline_hash_patch()
    apply_harness_windows_shell_tree_patch()
    apply_harness_windows_jsonl_append_patch()
    budget = CodingRunBudget(
        max_clarifications=0,
        max_model_requests=MODEL_REQUEST_BUDGET,
        max_api_lookup_calls=0,
        max_concept_lookup_calls=0,
        max_diagnostic_retrieval_calls=1,
        max_abaqus_calls=ABAQUS_CALL_BUDGET,
        max_solver_calls=SOLVER_CALL_BUDGET,
        wall_time_seconds=2_700.0,
    )
    policy = RuntimeScriptPolicy(
        allowed_cae_scripts=("build_model.py",),
        allowed_abaqus_python_scripts=("extract_results.py",),
        allowed_solver_inputs=("plate_tension.inp",),
    )
    _write_experiment_manifest(
        root,
        source=source,
        defective=defective,
        environment_seal_sha256=environment.seal_sha256,
        query_gate=query_gate,
        budget=budget,
        policy=policy,
        model_gateway=model_gateway,
        model_name=model_name,
        http_retries=http_retries,
        repeats=repeats,
        required_passes=required_passes,
    )
    observations: list[PlateOutputWarningRepairObservation] = []
    _append_phase(root, "agent_batch_started")

    async def run_case(
        inputs: PlateOutputWarningRepairInput,
    ) -> PlateOutputWarningRepairObservation:
        runtime = await execute_model_change_case(
            root,
            seed=defective,
            goal=inputs.goal,
            run_id_prefix=f"rf3-{inputs.arm}-r{inputs.replicate}",
            verifier=_verifier(defective),
            budget=budget,
            model_gateway=model_gateway,
            model_name=model_name,
            api_key=api_key,
            http_retries=http_retries,
            concept_index=index,
            diagnostic_retrieval_mode=(
                "advisory" if inputs.arm == "advisory" else "off"
            ),
            diagnostic_retrieval_instructions_enabled=True,
            immutable_workspace_paths=("plate_tension.inp", "extract_results.py"),
            runtime_script_policy=policy,
            task_instructions=_task_instructions(),
            verify_on_budget_exhaustion=False,
        )
        traces = load_diagnostic_retrieval_traces(runtime.workspace.parent / "state")
        executions = load_execution_results(runtime.workspace.parent / "state")
        gold_rank = _advisory_gold_rank(traces)
        arm_contract_passed = _arm_contract_passed(
            inputs.arm,
            runtime.run_id,
            runtime.outcome,
            runtime.verification,
            traces,
            executions,
            index,
        )
        observation = PlateOutputWarningRepairObservation(
            arm=inputs.arm,
            replicate=inputs.replicate,
            run_id=runtime.run_id,
            outcome=runtime.outcome,
            verification=runtime.verification,
            diagnostic_traces=traces,
            arm_contract_passed=arm_contract_passed,
            advisory_relevant=gold_rank is not None,
            advisory_gold_rank=gold_rank,
            abaqus_runtime_seconds=runtime.abaqus_runtime_seconds,
        )
        observations.append(observation)
        usage = runtime.outcome.usage
        for name, value in {
            "model_requests": usage.model_requests,
            "tool_calls": usage.tool_calls,
            "abaqus_calls": usage.abaqus_calls,
            "solver_calls": usage.solver_calls,
            "diagnostic_retrieval_calls": usage.diagnostic_retrieval_calls,
            "input_tokens": usage.input_tokens,
            "output_tokens": usage.output_tokens,
            "abaqus_runtime_s": runtime.abaqus_runtime_seconds,
        }.items():
            increment_eval_metric(name, value)
        set_eval_attribute("arm", inputs.arm)
        set_eval_attribute("replicate", inputs.replicate)
        set_eval_attribute("run_id", runtime.run_id)
        set_eval_attribute("advisory_gold_rank", gold_rank or "none")
        return observation

    metadata = {
        "model_gateway": model_gateway.value,
        "model": model_name,
        "base_url": gateway_base_url(model_gateway),
        "http_retries": http_retries,
        "http_timeout_seconds": DEFAULT_MODEL_HTTP_TIMEOUT_SECONDS,
        "prompt_version": PROMPT_VERSION,
        "source_run_id": source.outcome.run_id,
        "defective_seed": defective.provenance.model_dump(mode="json"),
        "environment_seal_sha256": environment.seal_sha256,
        "experiment_manifest_sha256": sha256_file(root / "experiment_manifest.json"),
        "query_gate": query_gate.model_dump(mode="json"),
        "skill_sha256": sha256_file(
            skill_library_path() / "abaqus-2024-coding" / "SKILL.md"
        ),
        "diagnostic_retrieval_py_sha256": sha256_file(
            Path(__file__).parents[1] / "coding" / "diagnostic_retrieval.py"
        ),
        "pydantic_ai_version": version("pydantic-ai-slim"),
        "pydantic_ai_harness_version": version("pydantic-ai-harness"),
        "repeats_per_arm": repeats,
        "required_passes_per_arm": required_passes,
        "api_catalog_mode": "off",
        "concept_tool_mode": "off",
        "planning_enabled": False,
    }
    report = await build_dataset(
        source.outcome.run_id,
        repeats=repeats,
    ).evaluate(
        run_case,
        name=(f"{model_gateway.value}-{model_name}-abaqus2024-{PROMPT_VERSION}"),
        repeat=1,
        max_concurrency=1,
        retry_task=None,
        retry_evaluators=None,
        metadata=metadata,
    )
    _persist_agent_batch(root, report, observations)
    _append_phase(
        root,
        "agent_batch_completed",
        agent_batch_sha256=sha256_file(root / "agent_batch.json"),
    )
    _validate_persisted_agent_batch(root, index)
    (root / "source_provenance_completion.json").write_text(
        source.provenance.model_dump_json(indent=2),
        encoding="utf-8",
    )
    reference_run_id = _next_reference_run_id(root)
    reference = await _run_reference(
        root,
        defective,
        run_id=reference_run_id,
    )
    if not reference.passed:
        raise RuntimeError("licensed deterministic RF3 reference did not pass")
    _append_phase(
        root,
        "licensed_reference_passed",
        reference_run_id=reference_run_id,
        reference_result_sha256=sha256_file(
            root / reference_run_id / "state" / "reference_result.json"
        ),
    )
    environment_after = audit_reproduction(
        index,
        environment_seal,
        repository_root=repository_root,
    )
    if environment_after.seal_sha256 != environment.seal_sha256:
        raise RuntimeError("retrieval environment seal changed during the A/B batch")
    _verify_experiment_implementation(root)
    _append_phase(root, "post_run_attestation_passed")
    gate = _gate(observations, repeats=repeats, required_passes=required_passes)
    _write_artifacts(root, gate, observations)
    _append_phase(root, "final_evidence_ready")
    _write_completion_seal(
        root,
        observations,
        environment_seal_sha256=environment.seal_sha256,
    )
    report.print(include_output=True, include_reasons=True)
    return gate


async def finalize_evaluation(
    run_root: Path,
    *,
    source_run_dir: Path,
    concept_index_dir: Path,
    environment_seal: Path,
    repository_root: Path,
    repeats: int = 3,
    required_passes: int = 2,
) -> PlateOutputWarningRepairGate:
    """在Agent batch已持久化后重试reference与最终证据封存。

    Args:
        run_root: 已存在且含有agent batch与实验manifest的评测根目录。
        source_run_dir: 与原实验一致的verified plate source run。
        concept_index_dir: 与原实验一致的本地文档索引。
        environment_seal: 需要在恢复前后复核的向量与编码器环境seal。
        repository_root: seal中实现文件相对路径的仓库根目录。
        repeats: 原实验每个arm的重复数。
        required_passes: 原实验每个arm的smoke阈值。

    Returns:
        从已交叉核对的持久化observation重新计算的双臂gate。

    Raises:
        ValueError: batch、manifest、run state、索引或配置与断点前不一致。
        RuntimeError: licensed reference或最终环境复核失败。
    """
    root = await asyncio.to_thread(run_root.resolve, strict=True)
    if (root / "completion_seal.json").is_file():
        audit_completion_seal(root)
        return PlateOutputWarningRepairGate.model_validate_json(
            (root / "gate.json").read_text(encoding="utf-8")
        )
    source = load_verified_plate_seed(source_run_dir, forbidden_root=root)
    defective = derive_defective_seed(source)
    index = open_concept_index(concept_index_dir, forbidden_root=root)
    environment = audit_reproduction(
        index,
        environment_seal,
        repository_root=repository_root,
    )
    manifest = json.loads(
        (root / "experiment_manifest.json").read_text(encoding="utf-8")
    )
    if manifest.get("environment_seal_sha256") != environment.seal_sha256:
        raise ValueError("finalization environment differs from experiment manifest")
    if manifest.get("defective_seed") != defective.provenance.model_dump(mode="json"):
        raise ValueError("finalization seed differs from experiment manifest")
    expected_order = manifest.get("case_order")
    if not isinstance(expected_order, list) or len(expected_order) != repeats * 2:
        raise ValueError("finalization repeat count differs from experiment manifest")
    if manifest.get("required_passes_per_arm") != required_passes:
        raise ValueError("finalization pass gate differs from experiment manifest")
    _verify_experiment_implementation(root)
    observations = _validate_persisted_agent_batch(root, index)
    if [f"rf3-{item.arm}-r{item.replicate}" for item in observations] != expected_order:
        raise ValueError("persisted Agent batch order differs from experiment manifest")
    source_completion = root / "source_provenance_completion.json"
    expected_source = source.provenance.model_dump_json(indent=2)
    if source_completion.is_file():
        if source_completion.read_text(encoding="utf-8") != expected_source:
            raise ValueError("completion source provenance changed")
    else:
        source_completion.write_text(expected_source, encoding="utf-8")
    _append_phase(root, "finalization_resumed")
    reference_run_id = _next_reference_run_id(root)
    reference = await _run_reference(
        root,
        defective,
        run_id=reference_run_id,
    )
    if not reference.passed:
        raise RuntimeError("licensed deterministic RF3 reference did not pass")
    _append_phase(
        root,
        "licensed_reference_passed",
        reference_run_id=reference_run_id,
        reference_result_sha256=sha256_file(
            root / reference_run_id / "state" / "reference_result.json"
        ),
    )
    environment_after = audit_reproduction(
        index,
        environment_seal,
        repository_root=repository_root,
    )
    if environment_after.seal_sha256 != environment.seal_sha256:
        raise RuntimeError("retrieval environment seal changed during finalization")
    _verify_experiment_implementation(root)
    _append_phase(root, "post_run_attestation_passed")
    gate = _gate(
        observations,
        repeats=repeats,
        required_passes=required_passes,
    )
    _write_artifacts(root, gate, observations)
    _append_phase(root, "final_evidence_ready")
    _write_completion_seal(
        root,
        observations,
        environment_seal_sha256=environment.seal_sha256,
    )
    return gate


def _run_query_gate(index: AbaqusDocumentIndex) -> DiagnosticQueryGate:
    query = formulate_solver_diagnostic_query(REPAIR_GOAL, RAW_RF3_WARNING)
    hits = index.search_hybrid(
        query,
        top_k=DIAGNOSTIC_TOP_K,
        fetch_k=DIAGNOSTIC_FETCH_K,
        rrf_k=DIAGNOSTIC_RRF_K,
    )
    retrieved = tuple(_hit_target(hit) for hit in hits)
    gold_rank = next(
        (
            rank
            for rank, target in enumerate(retrieved, start=1)
            if target == (GOLD_DOCUMENT_ID, GOLD_SECTION_ID, GOLD_SECTION_TITLE)
        ),
        None,
    )
    return DiagnosticQueryGate(
        query=query,
        query_sha256=hashlib.sha256(query.encode("utf-8")).hexdigest(),
        formulation_version=DIAGNOSTIC_QUERY_FORMULATION_VERSION,
        gold_document_id=GOLD_DOCUMENT_ID,
        gold_section_id=GOLD_SECTION_ID,
        gold_section_title=GOLD_SECTION_TITLE,
        gold_rank=gold_rank,
        passed=gold_rank is not None,
        retrieved=retrieved,
    )


async def _run_reference(
    root: Path,
    seed: DefectivePlateSeed,
    *,
    run_id: str = "reference-rf3",
) -> PlateOutputWarningRepairVerificationAttempt:
    resolved = await create_seeded_workspace_async(root, run_id, seed)
    request = CodingRunRequest(
        run_id=run_id,
        goal=REPAIR_GOAL,
        workspace=resolved.workspace,
        state_dir=resolved.state_dir,
        runtime=RuntimeProfile(launcher_path=DEFAULT_ABAQUS_2024_LAUNCHER.resolve()),
    )
    runner = Abaqus2024Runner(request.workspace, request.state_dir)
    (request.state_dir / "initial_snapshot.json").write_text(
        resolved.initial_snapshot.model_dump_json(indent=2),
        encoding="utf-8",
    )
    executions: list[AbaqusExecutionResult] = []
    for result in (
        await runner.run_cae_script("build_model.py", timeout_seconds=180.0),
        await runner.run_solver_job(
            "plate_tension",
            input_file="plate_tension.inp",
            timeout_seconds=300.0,
        ),
    ):
        executions.append(result)
        append_execution_result(request.state_dir, result)
    request.workspace.joinpath("build_model.py").write_bytes(
        seed.source.seed_files["build_model.py"]
    )
    for result in (
        await runner.run_cae_script("build_model.py", timeout_seconds=180.0),
        await runner.run_solver_job(
            "plate_tension",
            input_file="plate_tension.inp",
            timeout_seconds=300.0,
        ),
        await runner.run_abaqus_python("extract_results.py", timeout_seconds=120.0),
    ):
        executions.append(result)
        append_execution_result(request.state_dir, result)
    attempt = await _verifier(seed).verify(
        request,
        runner,
        resolved.initial_snapshot,
        executions,
        ReadyForVerificationDecision(
            deliverables=tuple(
                DeliverableDeclaration(relative_path=path)
                for path in (
                    "build_model.py",
                    "plate_tension.cae",
                    "plate_tension.inp",
                    "plate_tension.odb",
                    "results.json",
                )
            ),
            summary="Deterministic reference repair completed.",
        ),
    )
    (request.state_dir / "reference_result.json").write_text(
        attempt.model_dump_json(indent=2),
        encoding="utf-8",
    )
    return attempt


def _verifier(seed: DefectivePlateSeed) -> PlateOutputWarningRepairRunVerifier:
    return PlateOutputWarningRepairRunVerifier(
        defective_build=seed.seed_files["build_model.py"],
        source_build=seed.source.seed_files["build_model.py"],
        source_extractor=seed.source.seed_files["extract_results.py"],
    )


def _task_instructions() -> str:
    return """
This is a deterministic existing-model diagnostic-repair task. All physical
inputs are complete. Before editing, reproduce the current warning by running
the existing build_model.py and then plate_tension.inp. Modify only
build_model.py; never edit the generated INP or extract_results.py. After the
smallest output-request repair, rerun CAE, Solver, and extract_results.py, then
inspect results.json. Preserve the full plate physics and file-stage contract.
Only request verification after both the warning-producing baseline and the
clean final sequence exist in runtime evidence.
Declare build_model.py, plate_tension.cae, plate_tension.inp,
plate_tension.odb, and results.json as fresh deliverables. The unchanged
extract_results.py is a preserved input, not a newly produced deliverable.
""".strip()


def _hit_target(hit: RetrievalHit) -> tuple[str, str, str]:
    metadata = hit.document.metadata
    return (
        str(metadata.get("document_id") or hit.document.id or ""),
        str(metadata.get("section_id") or ""),
        str(metadata.get("section_title") or ""),
    )


def _advisory_gold_rank(traces: Sequence[DiagnosticRetrievalTrace]) -> int | None:
    return next(
        (
            hit.rank
            for trace in traces
            if trace.status == "found" and trace.counted
            for hit in trace.ranked_hits
            if (
                hit.document_id,
                hit.section_id,
                hit.section_title,
            )
            == (GOLD_DOCUMENT_ID, GOLD_SECTION_ID, GOLD_SECTION_TITLE)
        ),
        None,
    )


def _arm_contract_passed(
    arm: DiagnosticArm,
    run_id: str,
    outcome: CodingRunOutcome,
    verification: PlateOutputWarningRepairVerificationAttempt | None,
    traces: Sequence[DiagnosticRetrievalTrace],
    executions: Sequence[AbaqusExecutionResult],
    index: AbaqusDocumentIndex,
) -> bool:
    if arm == "control":
        return not traces and outcome.usage.diagnostic_retrieval_calls == 0
    counted_traces = [trace for trace in traces if trace.counted]
    if (
        len(counted_traces) != 1
        or outcome.usage.diagnostic_retrieval_calls != 1
        or verification is None
        or verification.baseline_warning_execution_index is None
    ):
        return False
    trace = counted_traces[0]
    execution_index = verification.baseline_warning_execution_index
    if execution_index >= len(executions):
        return False
    diagnostic = next(
        (
            item
            for item in executions[execution_index].diagnostics
            if RF3_INACTIVE_WARNING.casefold() in item.message.casefold()
        ),
        None,
    )
    if diagnostic is None or trace.index is None:
        return False
    expected_query = formulate_solver_diagnostic_query(REPAIR_GOAL, diagnostic.message)
    duplicate_traces_valid = all(
        duplicate.run_id == run_id
        and duplicate.status == "duplicate"
        and not duplicate.counted
        and duplicate.task_revision == 0
        and duplicate.amendment_sequence == 0
        and duplicate.diagnostic_sha256 == trace.diagnostic_sha256
        and duplicate.formulation_version == trace.formulation_version
        and duplicate.query == trace.query
        and not duplicate.ranked_hits
        for duplicate in traces
        if duplicate is not trace
    )
    dense = index.read_manifest()
    sparse = index.read_bm25_manifest()
    return all(
        (
            trace.run_id == run_id,
            trace.status == "found",
            trace.counted,
            trace.task_revision == 0,
            trace.amendment_sequence == 0,
            trace.execution_index == execution_index,
            trace.formulation_version == DIAGNOSTIC_QUERY_FORMULATION_VERSION,
            trace.query == expected_query,
            trace.query_sha256 == sha256_bytes(expected_query.encode("utf-8")),
            trace.diagnostic_sha256
            == sha256_bytes(" ".join(diagnostic.message.split()).encode("utf-8")),
            trace.top_k == DIAGNOSTIC_TOP_K,
            trace.fetch_k == DIAGNOSTIC_FETCH_K,
            trace.rrf_k == DIAGNOSTIC_RRF_K,
            trace.index.release == dense.release,
            trace.index.collection_name == dense.collection_name,
            trace.index.dense_manifest_sha256 == sha256_file(index.manifest_path),
            trace.index.bm25_manifest_sha256 == sha256_file(index.bm25_manifest_path),
            trace.index.bm25_database_sha256 == sparse.database_sha256,
            trace.index.semantic_corpus_sha256 == sparse.semantic_corpus_sha256,
            _advisory_gold_rank(traces) is not None,
            duplicate_traces_valid,
        )
    )


def _gate(
    observations: Sequence[PlateOutputWarningRepairObservation],
    *,
    repeats: int,
    required_passes: int,
) -> PlateOutputWarningRepairGate:
    gates: dict[DiagnosticArm, ArmGate] = {}
    for arm in ("control", "advisory"):
        selected = [item for item in observations if item.arm == arm]
        passes = sum(
            item.outcome.status is CodingRunStatus.COMPLETED
            and item.verification is not None
            and item.verification.passed
            and item.arm_contract_passed
            and (arm == "control" or item.advisory_relevant)
            for item in selected
        )
        gates[arm] = ArmGate(
            arm=arm,
            attempts=len(selected),
            passes=passes,
            required_passes=required_passes,
            smoke_gate_passed=(len(selected) == repeats and passes >= required_passes),
        )
    return PlateOutputWarningRepairGate(
        control=gates["control"],
        advisory=gates["advisory"],
        advisory_pass_delta=gates["advisory"].passes - gates["control"].passes,
        observed_pass_improvement=(gates["advisory"].passes > gates["control"].passes),
        evaluation_complete=(
            gates["control"].attempts == repeats
            and gates["advisory"].attempts == repeats
        ),
        both_arms_smoke_gate_passed=(
            gates["control"].smoke_gate_passed and gates["advisory"].smoke_gate_passed
        ),
    )


def _write_artifacts(
    root: Path,
    gate: PlateOutputWarningRepairGate,
    observations: Sequence[PlateOutputWarningRepairObservation],
) -> None:
    batch = json.loads((root / "agent_batch.json").read_text(encoding="utf-8"))
    (root / "evaluation_report.txt").write_bytes(
        (root / "agent_batch_report.txt").read_bytes()
    )
    summary = {
        "name": batch["name"],
        "experiment_metadata": batch["experiment_metadata"],
        "gate": gate.model_dump(mode="json"),
        "run_ids": [item.run_id for item in observations],
        "arm_aggregates": _arm_aggregates(observations),
        "cases": batch["cases"],
        "failures": batch["failures"],
    }
    (root / "evaluation_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    (root / "gate.json").write_text(gate.model_dump_json(indent=2), encoding="utf-8")


def _persist_agent_batch(
    root: Path,
    report: EvaluationReport[
        PlateOutputWarningRepairInput,
        PlateOutputWarningRepairObservation,
        PlateOutputWarningRepairMetadata,
    ],
    observations: Sequence[PlateOutputWarningRepairObservation],
) -> None:
    (root / "agent_batch_report.txt").write_text(
        report.render(include_output=True, include_reasons=True),
        encoding="utf-8",
    )
    batch = {
        "name": report.name,
        "experiment_metadata": report.experiment_metadata,
        "observations": [item.model_dump(mode="json") for item in observations],
        "cases": [
            {
                "name": case.name,
                "closed_loop_pass": (
                    case.assertions["closed_loop_pass"].value
                    if "closed_loop_pass" in case.assertions
                    else False
                ),
                "metrics": case.metrics,
                "labels": {key: value.value for key, value in case.labels.items()},
                "scores": {key: value.value for key, value in case.scores.items()},
                "task_duration": case.task_duration,
            }
            for case in report.cases
        ],
        "failures": [failure.name for failure in report.failures],
    }
    (root / "agent_batch.json").write_text(
        json.dumps(batch, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )


def _validate_persisted_agent_batch(
    root: Path,
    index: AbaqusDocumentIndex,
) -> tuple[PlateOutputWarningRepairObservation, ...]:
    phase_records = [
        json.loads(line)
        for line in (root / "phase_events.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
        if line.strip()
    ]
    batch_events = [
        item for item in phase_records if item.get("event") == "agent_batch_completed"
    ]
    if len(batch_events) != 1 or batch_events[0].get(
        "agent_batch_sha256"
    ) != sha256_file(root / "agent_batch.json"):
        raise ValueError("persisted Agent batch hash does not match its phase record")
    manifest_path = root / "experiment_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("query_gate_sha256") != sha256_file(root / "query_gate.json"):
        raise ValueError("persisted query gate differs from experiment manifest")
    if manifest.get("query_gate_attempts_sha256") != sha256_file(
        root / "query_gate_attempts.jsonl"
    ):
        raise ValueError("query gate attempt ledger differs from experiment manifest")
    batch = json.loads((root / "agent_batch.json").read_text(encoding="utf-8"))
    metadata = batch.get("experiment_metadata")
    if not isinstance(metadata, dict) or metadata.get(
        "experiment_manifest_sha256"
    ) != sha256_file(manifest_path):
        raise ValueError("Agent batch does not bind the experiment manifest")
    observations, expected_budget, expected_policy = _decode_persisted_json_contracts(
        batch,
        manifest,
    )
    current_index_binding = concept_index_binding(index)
    for observation in observations:
        state = root / observation.run_id / "state"
        request = CodingRunRequest.model_validate_json(
            (state / "request.json").read_text(encoding="utf-8")
        )
        outcome = CodingRunOutcome.model_validate_json(
            (state / "outcome.json").read_text(encoding="utf-8")
        )
        if (
            request.run_id != observation.run_id
            or request.goal != REPAIR_GOAL
            or request.budget != expected_budget
            or request.runtime_script_policy != expected_policy
            or request.concept_index != current_index_binding
            or outcome != observation.outcome
        ):
            raise ValueError(
                f"persisted run contract differs from Agent batch: {observation.run_id}"
            )
        traces = load_diagnostic_retrieval_traces(state)
        executions = load_execution_results(state)
        if traces != observation.diagnostic_traces:
            raise ValueError(
                f"persisted advisory trace differs from Agent batch: {observation.run_id}"
            )
        verification_path = state / "plate_output_warning_repair_verification.json"
        if observation.outcome.status is CodingRunStatus.COMPLETED:
            persisted_verification = (
                PlateOutputWarningRepairVerificationAttempt.model_validate_json(
                    verification_path.read_text(encoding="utf-8")
                )
            )
            if persisted_verification != observation.verification:
                raise ValueError(
                    "persisted verification differs from Agent batch: "
                    f"{observation.run_id}"
                )
        recomputed_contract = _arm_contract_passed(
            observation.arm,
            observation.run_id,
            observation.outcome,
            observation.verification,
            traces,
            executions,
            index,
        )
        if (
            recomputed_contract != observation.arm_contract_passed
            or _advisory_gold_rank(traces) != observation.advisory_gold_rank
        ):
            raise ValueError(
                f"persisted arm evidence differs from Agent batch: {observation.run_id}"
            )
    return observations


def _decode_persisted_json_contracts(
    batch: Mapping[str, object],
    manifest: Mapping[str, object],
) -> tuple[
    tuple[PlateOutputWarningRepairObservation, ...],
    CodingRunBudget,
    RuntimeScriptPolicy,
]:
    observations = TypeAdapter(
        tuple[PlateOutputWarningRepairObservation, ...]
    ).validate_json(json.dumps(batch.get("observations"), ensure_ascii=False))
    budget = CodingRunBudget.model_validate_json(
        json.dumps(manifest.get("budget"), ensure_ascii=False)
    )
    policy = RuntimeScriptPolicy.model_validate_json(
        json.dumps(manifest.get("runtime_script_policy"), ensure_ascii=False)
    )
    return observations, budget, policy


def _arm_aggregates(
    observations: Sequence[PlateOutputWarningRepairObservation],
) -> dict[str, dict[str, float | int]]:
    aggregates: dict[str, dict[str, float | int]] = {}
    for arm in ("control", "advisory"):
        selected = [item for item in observations if item.arm == arm]
        count = len(selected)
        aggregates[arm] = {
            "attempts": count,
            "completed": sum(
                item.outcome.status is CodingRunStatus.COMPLETED for item in selected
            ),
            "model_requests_total": sum(
                item.outcome.usage.model_requests for item in selected
            ),
            "abaqus_calls_total": sum(
                item.outcome.usage.abaqus_calls for item in selected
            ),
            "solver_calls_total": sum(
                item.outcome.usage.solver_calls for item in selected
            ),
            "diagnostic_retrieval_calls_total": sum(
                item.outcome.usage.diagnostic_retrieval_calls for item in selected
            ),
            "model_requests_mean": (
                sum(item.outcome.usage.model_requests for item in selected) / count
                if count
                else 0.0
            ),
            "abaqus_calls_mean": (
                sum(item.outcome.usage.abaqus_calls for item in selected) / count
                if count
                else 0.0
            ),
        }
    return aggregates


def _query_gate_started(
    index: AbaqusDocumentIndex,
    environment_seal_sha256: str,
) -> dict[str, object]:
    dense = index.read_manifest()
    sparse = index.read_bm25_manifest()
    return {
        "event": "started",
        "started_at": datetime.now(UTC).isoformat(),
        "rankings_emitted": False,
        "goal_sha256": sha256_bytes(REPAIR_GOAL.encode("utf-8")),
        "diagnostic_sha256": sha256_bytes(RAW_RF3_WARNING.encode("utf-8")),
        "formulation_version": DIAGNOSTIC_QUERY_FORMULATION_VERSION,
        "formulation_implementation_sha256": sha256_file(
            Path(__file__).parents[1] / "coding" / "diagnostic_retrieval.py"
        ),
        "environment_seal_sha256": environment_seal_sha256,
        "dense_manifest_sha256": sha256_file(index.manifest_path),
        "bm25_manifest_sha256": sha256_file(index.bm25_manifest_path),
        "bm25_database_sha256": sparse.database_sha256,
        "semantic_corpus_sha256": sparse.semantic_corpus_sha256,
        "release": dense.release,
        "collection_name": dense.collection_name,
        "retriever": {
            "algorithm": "rrf",
            "top_k": DIAGNOSTIC_TOP_K,
            "fetch_k": DIAGNOSTIC_FETCH_K,
            "rrf_k": DIAGNOSTIC_RRF_K,
        },
        "gold": {
            "document_id": GOLD_DOCUMENT_ID,
            "section_id": GOLD_SECTION_ID,
            "section_title": GOLD_SECTION_TITLE,
        },
    }


def _write_experiment_manifest(
    root: Path,
    *,
    source: VerifiedPlateSeed,
    defective: DefectivePlateSeed,
    environment_seal_sha256: str,
    query_gate: DiagnosticQueryGate,
    budget: CodingRunBudget,
    policy: RuntimeScriptPolicy,
    model_gateway: DeepSeekGateway,
    model_name: str,
    http_retries: int,
    repeats: int,
    required_passes: int,
) -> None:
    paths = _implementation_paths()
    manifest = {
        "schema_version": "plate-output-warning-repair-experiment-v1",
        "created_at": datetime.now(UTC).isoformat(),
        "source_run_id": source.outcome.run_id,
        "source_provenance": defective.provenance.source.model_dump(mode="json"),
        "defective_seed": defective.provenance.model_dump(mode="json"),
        "environment_seal_sha256": environment_seal_sha256,
        "query_gate_sha256": sha256_file(root / "query_gate.json"),
        "query_gate_attempts_sha256": sha256_file(root / "query_gate_attempts.jsonl"),
        "query_gate": query_gate.model_dump(mode="json"),
        "licensed_reference_timing": "after_agent_batch_before_completion_seal",
        "implementation_sha256": {
            name: sha256_file(path) for name, path in paths.items()
        },
        "model": {
            "gateway": model_gateway.value,
            "name": model_name,
            "http_retries": http_retries,
            "http_timeout_seconds": DEFAULT_MODEL_HTTP_TIMEOUT_SECONDS,
        },
        "prompt_contract": {
            "goal_sha256": sha256_bytes(REPAIR_GOAL.encode("utf-8")),
            "task_instructions_sha256": sha256_bytes(
                _task_instructions().encode("utf-8")
            ),
            "diagnostic_advisory_instructions_enabled_for_both_arms": True,
        },
        "budget": budget.model_dump(mode="json"),
        "runtime_script_policy": policy.model_dump(mode="json"),
        "arms": ["control", "advisory"],
        "arm_profiles": {
            "control": {"diagnostic_retrieval_mode": "off"},
            "advisory": {"diagnostic_retrieval_mode": "advisory"},
            "shared_diagnostic_retrieval_instructions": True,
        },
        "workspace_write_policy": {
            "immutable_workspace_paths": [
                "plate_tension.inp",
                "extract_results.py",
            ],
            "runtime_tools_sequential_barrier": True,
        },
        "repair_contract": {
            "baseline_runtime_prefix": [
                "defective build_model.py via CAE",
                "RF3-warning plate_tension.inp via Solver",
            ],
            "allowed_final_repairs": [
                "delete injected history-output override",
                "replace RF3 only with a non-empty subset of RF1/RF2",
            ],
            "final_lineage": "allowed build -> CAE/INP -> clean Solver/ODB -> exact extractor/results",
        },
        "case_order": [
            f"rf3-{arm}-r{replicate}"
            for replicate in range(1, repeats + 1)
            for arm in (
                ("control", "advisory") if replicate % 2 else ("advisory", "control")
            )
        ],
        "required_passes_per_arm": required_passes,
        "api_catalog_mode": "off",
        "concept_tool_mode": "off",
        "planning_enabled": False,
    }
    (root / "experiment_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )


def _verify_experiment_implementation(root: Path) -> None:
    manifest = json.loads(
        (root / "experiment_manifest.json").read_text(encoding="utf-8")
    )
    expected = manifest.get("implementation_sha256")
    if not isinstance(expected, dict):
        raise RuntimeError("experiment manifest lacks implementation hashes")
    paths = _implementation_paths()
    actual = {name: sha256_file(path) for name, path in paths.items()}
    if actual != expected:
        raise RuntimeError("experiment implementation changed during the A/B batch")


def _implementation_paths() -> dict[str, Path]:
    package = Path(__file__).parents[1]
    coding = package / "coding"
    verification = package / "verification"
    return {
        "evaluation": Path(__file__),
        "model_change_runtime": package / "evaluation" / "model_change_runtime.py",
        "verified_plate_seed": package / "evaluation" / "verified_plate_seed.py",
        "agent": coding / "agent.py",
        "instructions": coding / "instructions.py",
        "contracts": coding / "contracts.py",
        "dependencies": coding / "dependencies.py",
        "diagnostic_retrieval": coding / "diagnostic_retrieval.py",
        "runtime_tools": coding / "runtime_tools.py",
        "resume": coding / "resume.py",
        "service": coding / "service.py",
        "abaqus2024_runner": package / "execution" / "abaqus2024.py",
        "verifier": verification / "plate_output_warning_repair_run.py",
        "model_change_evidence": verification / "model_change_evidence.py",
        "plate_input_parser": verification / "plate_change.py",
        "plate_physics": verification / "plate_tension.py",
        "plate_oracle": plate_tension_oracle_script(),
        "skill": skill_library_path() / "abaqus-2024-coding" / "SKILL.md",
    }


def _append_jsonl(path: Path, payload: dict[str, object]) -> None:
    with path.open("a", encoding="utf-8", newline="\n") as stream:
        stream.write(
            json.dumps(
                payload,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            )
        )
        stream.write("\n")


def _append_phase(root: Path, event: str, **data: object) -> None:
    _append_jsonl(
        root / "phase_events.jsonl",
        {
            "event": event,
            "recorded_at": datetime.now(UTC).isoformat(),
            **data,
        },
    )


def _next_reference_run_id(root: Path) -> str:
    base = "reference-rf3"
    if not (root / base).exists():
        return base
    suffix = 2
    while (root / f"{base}-r{suffix}").exists():
        suffix += 1
    return f"{base}-r{suffix}"


def _write_completion_seal(
    root: Path,
    observations: Sequence[PlateOutputWarningRepairObservation],
    *,
    environment_seal_sha256: str,
) -> PlateOutputWarningRepairCompletionSeal:
    path = root / "completion_seal.json"
    if path.exists():
        raise FileExistsError(path)
    reference_run_id, reference_result_sha256 = _reference_phase_binding(root)
    seal = PlateOutputWarningRepairCompletionSeal(
        created_at=datetime.now(UTC),
        environment_seal_sha256=environment_seal_sha256,
        experiment_manifest_sha256=sha256_file(root / "experiment_manifest.json"),
        reference_run_id=reference_run_id,
        reference_result_sha256=reference_result_sha256,
        run_ids=tuple(item.run_id for item in observations),
        files=_completion_file_bindings(root),
    )
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(seal.model_dump_json(indent=2))
        stream.write("\n")
    return audit_completion_seal(root)


def audit_completion_seal(root: Path) -> PlateOutputWarningRepairCompletionSeal:
    path = root / "completion_seal.json"
    seal = PlateOutputWarningRepairCompletionSeal.model_validate_json(
        path.read_text(encoding="utf-8")
    )
    if seal.experiment_manifest_sha256 != sha256_file(
        root / "experiment_manifest.json"
    ):
        raise ValueError("completion seal experiment manifest hash mismatch")
    experiment = json.loads(
        (root / "experiment_manifest.json").read_text(encoding="utf-8")
    )
    if seal.environment_seal_sha256 != experiment.get("environment_seal_sha256"):
        raise ValueError("completion seal environment hash mismatch")
    reference_run_id, reference_result_sha256 = _reference_phase_binding(root)
    if (
        seal.reference_run_id != reference_run_id
        or seal.reference_result_sha256 != reference_result_sha256
    ):
        raise ValueError("completion seal reference binding mismatch")
    reference_result_path = (
        root / seal.reference_run_id / "state" / "reference_result.json"
    )
    reference = PlateOutputWarningRepairVerificationAttempt.model_validate_json(
        reference_result_path.read_text(encoding="utf-8")
    )
    if not (
        reference.passed
        and reference.baseline_warning_reproduced
        and reference.final_warning_removed
        and reference.ordered_runtime_passed
    ):
        raise ValueError("completion seal reference did not pass its strict contract")
    summary = json.loads((root / "evaluation_summary.json").read_text(encoding="utf-8"))
    run_ids = summary.get("run_ids")
    if not isinstance(run_ids, list) or not all(
        isinstance(run_id, str) for run_id in run_ids
    ):
        raise ValueError("evaluation summary lacks ordered run IDs")
    if seal.run_ids != tuple(run_ids):
        raise ValueError("completion seal run IDs mismatch")
    for run_id in seal.run_ids:
        request_path = root / run_id / "state" / "request.json"
        outcome_path = root / run_id / "state" / "outcome.json"
        if not request_path.is_file() or not outcome_path.is_file():
            raise ValueError(f"completion run evidence is missing: {run_id}")
        request = CodingRunRequest.model_validate_json(
            request_path.read_text(encoding="utf-8")
        )
        outcome = CodingRunOutcome.model_validate_json(
            outcome_path.read_text(encoding="utf-8")
        )
        if request.run_id != run_id or outcome.run_id != run_id:
            raise ValueError(f"completion run identity mismatch: {run_id}")
    if seal.files != _completion_file_bindings(root):
        raise ValueError("completion seal file binding mismatch")
    return seal


def _completion_file_bindings(root: Path) -> tuple[CompletionFileBinding, ...]:
    files: list[CompletionFileBinding] = []
    seal_path = root / "completion_seal.json"
    for candidate in sorted(root.rglob("*"), key=lambda item: item.as_posix()):
        if candidate == seal_path:
            continue
        if candidate.is_symlink():
            raise ValueError(f"completion evidence must not be a symlink: {candidate}")
        if not candidate.is_file():
            continue
        relative = candidate.relative_to(root).as_posix()
        files.append(
            CompletionFileBinding(
                path=relative,
                size_bytes=candidate.stat().st_size,
                sha256=sha256_file(candidate),
            )
        )
    return tuple(files)


def _reference_phase_binding(root: Path) -> tuple[str, str]:
    records = [
        json.loads(line)
        for line in (root / "phase_events.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
        if line.strip()
    ]
    passed = [
        item for item in records if item.get("event") == "licensed_reference_passed"
    ]
    if not passed:
        raise ValueError("completion evidence lacks a passing licensed reference")
    latest = passed[-1]
    run_id = latest.get("reference_run_id")
    expected_sha256 = latest.get("reference_result_sha256")
    if not isinstance(run_id, str) or not isinstance(expected_sha256, str):
        raise ValueError("licensed reference phase binding is malformed")
    result_path = root / run_id / "state" / "reference_result.json"
    if not result_path.is_file() or sha256_file(result_path) != expected_sha256:
        raise ValueError("licensed reference result hash mismatch")
    return run_id, expected_sha256


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run the CPS4R RF3 diagnostic-advisory development A/B."
    )
    parser.add_argument("--run-root", type=Path, required=True)
    parser.add_argument("--source-run-dir", type=Path, required=True)
    parser.add_argument("--concept-index-dir", type=Path, required=True)
    parser.add_argument("--environment-seal", type=Path, required=True)
    parser.add_argument("--repository-root", type=Path, default=Path.cwd())
    parser.add_argument(
        "--model-provider",
        type=DeepSeekGateway,
        choices=list(DeepSeekGateway),
        default=DeepSeekGateway.OPENCODE_GO,
    )
    parser.add_argument("--model", default=DEEPSEEK_V4_FLASH)
    parser.add_argument("--http-retries", type=int, default=DEFAULT_MODEL_HTTP_RETRIES)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--required-passes", type=int, default=2)
    parser.add_argument("--finalize-only", action="store_true")
    args = parser.parse_args()
    operation = finalize_evaluation if args.finalize_only else run_evaluation
    common = {
        "source_run_dir": args.source_run_dir,
        "concept_index_dir": args.concept_index_dir,
        "environment_seal": args.environment_seal,
        "repository_root": args.repository_root,
        "repeats": args.repeats,
        "required_passes": args.required_passes,
    }
    if args.finalize_only:
        gate = asyncio.run(operation(args.run_root, **common))
    else:
        gate = asyncio.run(
            run_evaluation(
                args.run_root,
                model_gateway=args.model_provider,
                model_name=args.model,
                http_retries=args.http_retries,
                **common,
            )
        )
    raise SystemExit(0 if gate.evaluation_complete else 1)


if __name__ == "__main__":
    main()
