"""PLAN-0006的离线候选合同与装配；不提供正式批次执行入口。"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Literal

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    computed_field,
    model_validator,
)
from pydantic_ai.models import Model
from pydantic_ai_harness.step_persistence import StepStore

from abaqus_sim_agent.coding.agent import create_abaqus_coding_agent
from abaqus_sim_agent.coding.contracts import (
    ClarificationAnswer,
    ClarificationRequest,
    CodingRunBudget,
    CodingRunRequest,
)
from abaqus_sim_agent.coding.model import (
    DEEPSEEK_V4_FLASH,
    DeepSeekGateway,
    ModelExecutionConfig,
    new_run_execution_config,
)
from abaqus_sim_agent.coding.user_questions import QuestionProtocol
from abaqus_sim_agent.coding.verification_port import CodingRunVerifier
from abaqus_sim_agent.evaluation.bar_tension_3d import GOLD_REQUEST as BAR_REQUIREMENTS
from abaqus_sim_agent.evaluation.beam_section_change import CHANGE_REQUEST
from abaqus_sim_agent.evaluation.plate_output_warning_repair import (
    REPAIR_GOAL,
    derive_defective_seed,
)
from abaqus_sim_agent.evaluation.plate_tension import GOLD_REQUEST
from abaqus_sim_agent.evaluation.verified_beam_seed import load_verified_beam_seed
from abaqus_sim_agent.evaluation.verified_plate_seed import load_verified_plate_seed
from abaqus_sim_agent.skills import skill_library_path
from abaqus_sim_agent.verification.assets import plate_tension_oracle_script
from abaqus_sim_agent.verification.bar_run import BarTension3DRunVerifier
from abaqus_sim_agent.verification.beam_section_change_run import (
    BeamSectionChangeRunVerifier,
)
from abaqus_sim_agent.verification.plate_output_warning_repair_run import (
    PlateOutputWarningRepairRunVerifier,
)
from abaqus_sim_agent.verification.run import PlateTensionRunVerifier

TaskId = Literal[
    "requirements-plate-v1",
    "seed-beam-section-v1",
    "missing-input-plate-v1",
    "seed-output-warning-v1",
    "requirements-bar3d-v1",
]
SeedKind = Literal["none", "verified_beam", "defective_plate"]
TASK_IDS: tuple[TaskId, ...] = (
    "requirements-plate-v1",
    "seed-beam-section-v1",
    "missing-input-plate-v1",
    "seed-output-warning-v1",
)
THICKNESS_LINE = "板厚为 1 mm。\n"
PLATE_REQUIREMENTS = GOLD_REQUEST.replace("当前空工作目录", "当前工作目录") + (
    "\n矩形板位于全局XY平面，长度沿X、高度沿Y；单一实例，不增加其他物理载荷或约束。"
    "results.json 必须包含数值 right_mean_u1、mean_s11、left_sum_rf1，分别为唯一分析步最终完成载荷状态的"
    "全部右边节点U1算术平均、全实例积分点S11算术平均和全部左边节点RF1之和（保留符号）。\n"
)
PLATE_GOAL = "请阅读工作区 requirements.md，按其中要求完成建模、求解与结果提取。不要改写需求文件。"


class _Strict(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")


class TaskCandidate(_Strict):
    task_id: TaskId
    goal: str
    candidate_budget: CodingRunBudget
    seed_kind: SeedKind
    immutable_paths: tuple[str, ...]
    verification_scope: Literal["execution_evidence"] = "execution_evidence"
    readiness: Literal["offline_candidate_not_sealed"] = "offline_candidate_not_sealed"

    @property
    def artifact_stem(self) -> str:
        if self.task_id == "requirements-bar3d-v1":
            return "bar_tension"
        return "beam_tension" if self.seed_kind == "verified_beam" else "plate_tension"

    @property
    def new_deliverables(self) -> tuple[str, ...]:
        scripts = (
            ("build_model.py", "extract_results.py")
            if self.seed_kind == "none"
            else ("build_model.py",)
        )
        return (
            *scripts,
            *(f"{self.artifact_stem}.{suffix}" for suffix in ("cae", "inp", "odb")),
            "results.json",
        )

    @property
    def preserved_inputs(self) -> tuple[str, ...]:
        return (
            ("requirements.md",)
            if self.seed_kind == "none"
            else ("extract_results.py",)
        )

    @property
    def service_goal(self) -> str:
        repair_context = (
            "\n初始仅提供两份脚本；先原样运行build_model.py生成INP，再从该INP运行Solver复现warning，基线运行前不修改脚本。"
            "保留全局XY平面100 mm×20 mm矩形板、1 mm厚度、E=210000 MPa、ν=0.3、CPS4R 20×4结构化网格；"
            "左边全部节点U1=0，仅左下角U2=0，右边全部节点U1=0.1 mm，不增加其他载荷/约束；"
            "唯一Static, General，NLGEOM=OFF及U/S/RF场输出。"
            if self.seed_kind == "defective_plate"
            else ""
        )
        quantities = (
            "right_mean_u1为右端节点U1，left_sum_rf1为左端RF1总和（保留符号），mean_sf1为原seed方向约定下全梁SF1算术平均，保留原生符号。"
            if self.seed_kind == "verified_beam"
            else "right_mean_u1为全部右边节点U1算术平均，mean_s11为全实例积分点S11算术平均，left_sum_rf1为全部左边节点RF1之和（保留符号）。"
        )
        return (
            self.goal
            + repair_context
            + "\n\n用户交付规则：\n新交付物："
            + ", ".join(self.new_deliverables)
            + (
                "。已有保留输入："
                + ", ".join(self.preserved_inputs)
                + "，保持原文，不作为必须fresh的新交付物列入ready manifest。"
                "必须通过三个typed工具完成CAE建模、独立Solver和独立odbAccess提取，不得手写正确数值代替结果。"
                "单一实例、唯一分析步；结果取最终完成载荷状态，不能取初始帧。"
                + quantities
                + "读取results.json后在最终summary说明目标量、数值、单位及警告/局限；不要求另写报告或生成云图。"
            )
        )


def baseline_execution_config() -> ModelExecutionConfig:
    return new_run_execution_config(
        model_provider=DeepSeekGateway.OPENCODE_GO,
        model_name=DEEPSEEK_V4_FLASH,
        http_retries=10,
        http_timeout_seconds=60.0,
        planning_enabled=False,
    )


def task_candidates() -> tuple[TaskCandidate, ...]:
    result = []
    rows: tuple[tuple[TaskId, str, int, int, SeedKind, tuple[str, ...]], ...] = (
        (TASK_IDS[0], PLATE_GOAL, 30, 9, "none", ("requirements.md",)),
        (TASK_IDS[1], CHANGE_REQUEST, 36, 12, "verified_beam", ("extract_results.py",)),
        (TASK_IDS[2], PLATE_GOAL, 30, 9, "none", ("requirements.md",)),
        (
            TASK_IDS[3],
            REPAIR_GOAL,
            24,
            8,
            "defective_plate",
            ("extract_results.py", "plate_tension.inp"),
        ),
    )
    for task_id, goal, model_limit, runtime_limit, seed_kind, immutable in rows:
        result.append(
            TaskCandidate(
                task_id=task_id,
                goal=goal,
                seed_kind=seed_kind,
                immutable_paths=immutable,
                candidate_budget=CodingRunBudget(
                    max_model_requests=model_limit,
                    max_abaqus_calls=runtime_limit,
                    max_solver_calls=3,
                    wall_time_seconds=2700.0,
                    max_api_lookup_calls=0,
                    max_concept_lookup_calls=0,
                    max_diagnostic_retrieval_calls=0,
                    max_static_preflight_calls=0,
                ),
            )
        )
    return tuple(result)


def planned_slots() -> tuple[str, ...]:
    return tuple(
        f"{task_id}/r{repeat}" for repeat in range(1, 4) for task_id in TASK_IDS
    )


def simple_task_candidates() -> tuple[TaskCandidate, ...]:
    return (
        task_candidates()[2],
        TaskCandidate(
            task_id="requirements-bar3d-v1",
            goal=PLATE_GOAL,
            seed_kind="none",
            immutable_paths=("requirements.md",),
            candidate_budget=task_candidates()[0].candidate_budget,
        ),
    )


def simple_slots() -> tuple[str, ...]:
    return tuple(f"{task.task_id}/r1" for task in simple_task_candidates())


class ClarificationObservation(_Strict):
    request: ClarificationRequest
    classification: Literal[
        "answered", "slot_consumed", "adapter_gap", "unexpected_question"
    ]
    slot: Literal["plate_thickness"] | None = None


class ThicknessClarificationFixture:
    """仅在T3回答预声明的厚度语义槽，别名不重复领取，未知问题保留适配缺口。

    Note:
        这是保守规则适配器，不是语义理解模型；不确定表达拒答并记录adapter_gap。
        答案与别名表不得加入Agent prompt或host_needs。
    """

    aliases = frozenset(
        {"plate_thickness", "thickness", "thickness_mm", "plate_thickness_mm"}
    )

    def __init__(self, *, enabled: bool):
        self.enabled = enabled
        self.consumed = False
        self.observations: list[ClarificationObservation] = []

    def get_answer(self, request: ClarificationRequest) -> ClarificationAnswer | None:
        question = request.question.casefold()
        recognized = (
            request.kind == "missing_physical_input"
            and request.field_id in self.aliases
            and bool(re.search(r"\bthickness\b|厚度|板厚", question))
            and not re.search(
                r"\b(?:cm|meter|metre|inch|inches|young|density|width|length)\b|厘米|英寸|弹性|密度|宽度|长度",
                question,
            )
        )
        if not self.enabled:
            classification = "unexpected_question"
        elif not recognized:
            classification = "adapter_gap"
        elif self.consumed:
            classification = "slot_consumed"
        else:
            classification = "answered"
        self.observations.append(
            ClarificationObservation(
                request=request,
                classification=classification,
                slot="plate_thickness" if recognized and self.enabled else None,
            )
        )
        if classification != "answered":
            return None
        self.consumed = True
        return ClarificationAnswer(
            field_id=request.field_id, value="1 mm", source="scripted"
        )

    def get_question_answer(self, question: str) -> str | None:
        answer = self.get_answer(
            ClarificationRequest(
                field_id="plate_thickness",
                kind="missing_physical_input",
                question=question,
                impact="Fixture mapping for the declared thickness slot",
            )
        )
        return answer.value if answer else None


@dataclass(frozen=True)
class PreparedTask:
    candidate: TaskCandidate
    workspace_files: Mapping[str, bytes]
    seed_provenance: BaseModel | None
    reference_verifier: CodingRunVerifier
    clarification: ThicknessClarificationFixture
    pending_gates: tuple[str, ...]


def prepare_task(
    task_id: TaskId, *, source_run_dir: Path | None = None
) -> PreparedTask:
    """只读校验seed并缓存bytes；不建run、不查文档、不调用模型或Abaqus。

    Raises:
        ValueError: 任务/seed不匹配，或source未通过既有hash/合同校验。
    """
    task = next(
        (
            t
            for t in (*task_candidates(), *simple_task_candidates())
            if t.task_id == task_id
        ),
        None,
    )
    if task is None:
        raise ValueError("unknown baseline task")
    provenance = None
    gates = (
        "provider_calibration",
        "licensed_reference_and_mutants",
        "budget_approval",
        "new_source_environment_seal",
    )
    if task.seed_kind == "none":
        if source_run_dir is not None:
            raise ValueError("requirements task must not receive a model seed")
        requirements = PLATE_REQUIREMENTS
        if task_id == TASK_IDS[0]:
            requirements += THICKNESS_LINE
        if task_id == "requirements-bar3d-v1":
            requirements = BAR_REQUIREMENTS.replace("当前空工作目录", "当前工作目录")
        files = {"requirements.md": requirements.encode("utf-8")}
        verifier = (
            BarTension3DRunVerifier()
            if task_id == "requirements-bar3d-v1"
            else PlateTensionRunVerifier(plate_tension_oracle_script())
        )
        gates += ("human_review_checklist_and_reference_ready",)
    else:
        if source_run_dir is None:
            raise ValueError("verified source run is required")
        if task.seed_kind == "verified_beam":
            seed = load_verified_beam_seed(source_run_dir)
            verifier = BeamSectionChangeRunVerifier(
                source_run_id=seed.outcome.run_id,
                source_run_verified=True,
                seed_texts=seed.seed_texts,
            )
        else:
            source = load_verified_plate_seed(source_run_dir)
            seed = derive_defective_seed(source)
            verifier = PlateOutputWarningRepairRunVerifier(
                defective_build=seed.seed_files["build_model.py"],
                source_build=source.seed_files["build_model.py"],
                source_extractor=source.seed_files["extract_results.py"],
            )
        files = dict(seed.seed_files)
        provenance = seed.provenance
    return PreparedTask(
        task,
        MappingProxyType(files),
        provenance,
        verifier,
        ThicknessClarificationFixture(enabled=task_id == TASK_IDS[2]),
        gates,
    )


def compose_baseline_agent(
    model: Model,
    request: CodingRunRequest,
    *,
    configuration: ModelExecutionConfig,
    immutable_workspace_paths: tuple[str, ...] = (),
    step_store: StepStore | None = None,
    question_protocol: QuestionProtocol = "legacy_decision",
):
    """复用产品Agent装配供离线parity检查；不启动service或批准候选预算。"""
    if configuration != baseline_execution_config():
        raise ValueError("baseline configuration must match its declared target")
    if request.api_catalog or request.concept_index or request.document_policy:
        raise ValueError("base-Coder baseline must not bind optional evidence systems")
    return create_abaqus_coding_agent(
        model,
        request,
        skill_library=skill_library_path(),
        execution_config=configuration,
        api_catalog_mode="off",
        concept_retrieval_mode="off",
        diagnostic_retrieval_mode="off",
        probe_protocol_mode="off",
        immutable_workspace_paths=immutable_workspace_paths,
        step_store=step_store,
        question_protocol=question_protocol,
        context_policy_enabled=True,
    )


CheckState = Literal["pass", "fail", "unknown"]
REVIEW_ITEMS = (
    "geometry_material",
    "loads_constraints",
    "analysis_mesh",
    "results",
    "delivery_regression",
    "interaction_authority",
)


class ReviewCheck(_Strict):
    state: CheckState = "unknown"
    reason: str = ""
    evidence: tuple[str, ...] = ()

    @model_validator(mode="after")
    def supported_judgment(self):
        if self.state != "unknown" and (
            not self.reason.strip()
            or not self.evidence
            or any(not p.strip() for p in self.evidence)
        ):
            raise ValueError("a decided review check requires a reason and evidence")
        return self


class HumanReview(_Strict):
    source: Literal["human", "fixture"]
    reviewer: str = Field(min_length=1)
    reviewed_at: AwareDatetime
    checks: dict[str, ReviewCheck]
    trial_result_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def complete_checklist(self):
        if not self.reviewer.strip() or set(self.checks) != set(REVIEW_ITEMS):
            raise ValueError("reviewer and all six review items are required")
        return self


class PostRunCheck(_Strict):
    state: CheckState
    report_path: str
    report_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    trial_result_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    evidence_sha256: dict[str, str]
    host_calls: int = Field(ge=0, le=1)
    wall_seconds: float = Field(ge=0, allow_inf_nan=False)


class TaskTrialReview(_Strict):
    slot: str
    observation_kind: Literal["fixture_stub", "service_fixture", "live_service"] = (
        "fixture_stub"
    )
    service_run_id: str | None = None
    service_verification_scope: Literal["execution_evidence"] | None = None
    terminal: Literal[
        "not_started",
        "running",
        "completed",
        "failed",
        "interrupted",
        "needs_reconcile",
        "waiting_for_user",
    ]
    cause: Literal[
        "none",
        "provider",
        "environment",
        "budget",
        "task",
        "adapter_gap",
        "tool_arguments",
        "unknown",
    ]
    artifact_acceptance: Literal["not_checked", "pass", "fail", "unknown"] = (
        "not_checked"
    )
    execution_acceptance: CheckState = "unknown"
    numeric_acceptance: CheckState = "unknown"
    interaction_acceptance: CheckState = "unknown"
    authority_acceptance: CheckState = "unknown"
    human_review: HumanReview | None = None
    post_run_check: PostRunCheck | None = None
    evidence_references: tuple[str, ...] = ()
    evidence_sha256: dict[str, str] = Field(default_factory=dict)
    logical_requests: int | None = Field(default=None, ge=0)
    model_requests_started: int | None = Field(default=None, ge=0)
    http_attempts: int | None = Field(default=None, ge=0)
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    runtime_calls: int | None = Field(default=None, ge=0)
    solver_calls: int | None = Field(default=None, ge=0)
    host_oracle_calls: int | None = Field(default=None, ge=0)
    wall_seconds: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    service_wall_seconds: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    error_type: str | None = None

    @model_validator(mode="after")
    def valid_identity(self):
        if self.slot not in (*planned_slots(), *simple_slots()):
            raise ValueError("unknown planned slot")
        if self.terminal == "completed" and self.cause != "none":
            raise ValueError("completed run cannot carry a termination cause")
        if (
            self.solver_calls is not None
            and self.runtime_calls is not None
            and self.solver_calls > self.runtime_calls
        ):
            raise ValueError("solver calls cannot exceed total runtime calls")
        if (
            self.model_requests_started is not None
            and self.http_attempts is not None
            and self.model_requests_started > self.http_attempts
        ):
            raise ValueError("started model requests cannot exceed HTTP attempts")
        return self

    @computed_field
    @property
    def verdict(self) -> CheckState:
        if self.terminal in ("not_started", "running"):
            return "unknown"
        automatic = (
            self.artifact_acceptance,
            self.execution_acceptance,
            self.numeric_acceptance,
            self.interaction_acceptance,
            self.authority_acceptance,
            self.post_run_check.state if self.post_run_check else "unknown",
        )
        manual = (
            tuple(c.state for c in self.human_review.checks.values())
            if self.human_review
            else ()
        )
        if "fail" in (*automatic, *manual):
            return "fail"
        if self.terminal == "waiting_for_user":
            return "unknown"
        if self.terminal != "completed":
            return "fail"
        if (
            self.evidence_references
            and manual
            and self.artifact_acceptance == "pass"
            and self.execution_acceptance == "pass"
            and all(v == "pass" for v in manual)
        ):
            return "pass"
        return "unknown"

    @computed_field
    @property
    def full_delivery_passed(self) -> bool:
        return self.verdict == "pass"
