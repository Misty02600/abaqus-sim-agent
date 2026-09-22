from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
from datetime import UTC, datetime
from importlib.metadata import version
from pathlib import Path, PurePosixPath
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field
from pydantic_ai import ModelMessagesTypeAdapter
from pydantic_ai.messages import ModelResponse, ToolCallPart

from abaqus_sim_agent.api_catalog import ApiCatalog
from abaqus_sim_agent.coding.agent import create_abaqus_coding_agent
from abaqus_sim_agent.coding.clarification import ScriptedClarificationProvider
from abaqus_sim_agent.coding.concept_lookup import (
    concept_index_binding,
    open_concept_index,
)
from abaqus_sim_agent.coding.contracts import (
    ApiCatalogBinding,
    CodingRunBudget,
    CodingRunRequest,
    RuntimeProfile,
)
from abaqus_sim_agent.coding.dependencies import CodingAgentDependencies
from abaqus_sim_agent.coding.execution_verifier import ExecutionEvidenceVerifier
from abaqus_sim_agent.coding.instructions import CONCEPT_ADOPTION_POLICY_VERSION
from abaqus_sim_agent.coding.model import (
    DEEPSEEK_V4_FLASH,
    DEFAULT_MODEL_HTTP_RETRIES,
    DeepSeekGateway,
    create_deepseek_chat_model,
    take_gateway_api_key_from_environment,
)
from abaqus_sim_agent.coding.resume import load_document_lookup_traces
from abaqus_sim_agent.coding.service import CodingRunService
from abaqus_sim_agent.coding.workspace import WorkspaceResolver
from abaqus_sim_agent.execution import DEFAULT_ABAQUS_2024_LAUNCHER, Abaqus2024Runner
from abaqus_sim_agent.retrieval.reproduction import audit_reproduction
from abaqus_sim_agent.skills import skill_library_path

DEFAULT_CASES = (
    Path(__file__).parents[3] / "benchmarks/coding/docs_tool_behavior_cases_v1.jsonl"
)
EVALUATOR_VERSION = "docs-tool-behavior-v1"
TASK_INSTRUCTIONS = """
Complete the user's offline inspection/editing task using the available workspace
and knowledge tools as appropriate. Do not run Abaqus or any other program.
All Abaqus execution budgets are zero. Preserve the supplied project context.
When input is sufficient, write analysis.json with summary (string), findings
(array of strings), next_action (string), and evidence_refs (array of source/file
references actually used; it may be empty). Also make any requested file edit.
Inspect the deliverables and submit ready_for_verification. If a required user
physical input is missing, submit needs_input instead of inventing a value.
This task checks inspection artifacts and tool behavior, not simulation results.
""".strip()


class BehaviorCase(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    case_id: str = Field(pattern=r"^[a-z][a-z0-9-]+$")
    preferred_route: Literal["workspace", "clarification", "api", "docs"]
    goal: str = Field(min_length=1)
    seed_files: dict[str, str]
    review_criteria: tuple[str, ...] = Field(min_length=1)
    gold_document_ids: tuple[str, ...] = ()
    gold_api_names: tuple[str, ...] = ()


def load_cases(path: Path = DEFAULT_CASES) -> tuple[BehaviorCase, ...]:
    cases = tuple(
        BehaviorCase.model_validate_json(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    )
    if not cases or len({case.case_id for case in cases}) != len(cases):
        raise ValueError("behavior cases must have unique identifiers and be non-empty")
    for case in cases:
        for name in case.seed_files:
            if PurePosixPath(name).name != name or "\\" in name or ":" in name:
                raise ValueError("seed files must be plain workspace file names")
    return cases


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_new(path: Path, value: object) -> None:
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write("\n")


def _phase(root: Path, event: str, **values: object) -> None:
    with (root / "attempts.jsonl").open("a", encoding="utf-8", newline="\n") as stream:
        stream.write(
            json.dumps(
                {"event": event, "at": datetime.now(UTC).isoformat(), **values},
                ensure_ascii=False,
            )
            + "\n"
        )


def _records(path: Path) -> list[dict[str, object]]:
    if not path.is_file():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _gold_source_bindings(
    docs_root: Path, cases: tuple[BehaviorCase, ...]
) -> dict[str, str]:
    bindings: dict[str, str] = {}
    for doc_id in sorted(
        {doc_id for case in cases for doc_id in case.gold_document_ids}
    ):
        paths = list(docs_root.glob(f"*/{doc_id}.htm"))
        if len(paths) != 1:
            raise ValueError(f"cannot identify the declared gold source: {doc_id}")
        bindings[paths[0].relative_to(docs_root).as_posix()] = _sha256(paths[0])
    return bindings


def observe_case(root: Path, case: BehaviorCase) -> dict[str, object]:
    state = root / case.case_id / "state"
    workspace = state.parent / "workspace"
    history_path = state / "history.json"
    calls: list[dict[str, object]] = []
    if history_path.is_file():
        messages = ModelMessagesTypeAdapter.validate_json(history_path.read_bytes())
        for message in messages:
            if isinstance(message, ModelResponse):
                calls.extend(
                    {
                        "tool": part.tool_name,
                        "arguments": part.args_as_dict(),
                        "tool_call_id": part.tool_call_id,
                    }
                    for part in message.parts
                    if isinstance(part, ToolCallPart)
                )
    decisions = [
        item["data"]
        for item in _records(state / "events.jsonl")
        if item.get("event") == "agent_decision"
    ]
    doc_traces = load_document_lookup_traces(state)
    docs = [trace.model_dump(mode="json") for trace in doc_traces]
    api = _records(state / "api_lookups.jsonl")
    analysis_path = workspace / "analysis.json"
    analysis: object = None
    if analysis_path.is_file():
        try:
            analysis = json.loads(analysis_path.read_text(encoding="utf-8"))
        except (UnicodeError, ValueError):
            analysis = {"invalid_json": True}
    outcome_path = state / "outcome.json"
    outcome = (
        json.loads(outcome_path.read_text(encoding="utf-8"))
        if outcome_path.is_file()
        else None
    )
    return {
        "case_id": case.case_id,
        "preferred_route": case.preferred_route,
        "tool_calls": calls,
        "decisions": decisions,
        "document_queries": docs,
        "api_queries": api,
        "declared_gold_page_hit": any(
            doc_id in case.gold_document_ids
            for trace in doc_traces
            for doc_id in trace.document_ids
        ),
        "analysis": analysis,
        "outcome": outcome,
        "runtime_requested": any(
            str(call["tool"]).startswith("run_") for call in calls
        ),
        "review_status": "pending_manual_review",
        "review_criteria": case.review_criteria,
        "workspace_sha256": {
            path.name: _sha256(path)
            for path in sorted(workspace.iterdir())
            if path.is_file()
        },
    }


async def run_evaluation(
    run_root: Path,
    *,
    index_dir: Path,
    catalog_dir: Path,
    environment_seal: Path,
    cases_path: Path = DEFAULT_CASES,
    gateway: DeepSeekGateway = DeepSeekGateway.OPENCODE_GO,
    model_name: str = DEEPSEEK_V4_FLASH,
    http_retries: int = DEFAULT_MODEL_HTTP_RETRIES,
) -> None:
    """用真实产品工具运行离线任务检查，记录行为但不自动裁决技术解释正确性。

    Note:
        每个场景运行一次；未查文档不自动判失败。Abaqus预算为零，ready仅通过
        execution_evidence验收。预先保存场景与实现hash，结尾复核环境；技术答案
        留给独立于模型自报的人工检查，不将page hit当成答案相关性或因果采用。
    """
    cases = load_cases(cases_path)
    root = await asyncio.to_thread(run_root.resolve)
    if root.exists():
        raise FileExistsError(root)
    index = open_concept_index(index_dir, forbidden_root=root)
    binding = concept_index_binding(index)
    catalog = ApiCatalog.open(catalog_dir)
    assert catalog.manifest is not None
    api_binding = ApiCatalogBinding(
        schema_version=catalog.manifest.schema_version,
        cards_sha256=catalog.manifest.cards_sha256,
        card_count=catalog.manifest.card_count,
        member_count=catalog.manifest.member_count,
        members_sha256=catalog.manifest.members_sha256,
        runtime_release=catalog.manifest.runtime_release,
    )
    repo = Path(__file__).parents[3]
    environment = audit_reproduction(index, environment_seal, repository_root=repo)
    docs_root = Path(index.read_manifest().docs_root)
    source_bindings = await asyncio.to_thread(_gold_source_bindings, docs_root, cases)
    api_key = take_gateway_api_key_from_environment(gateway)
    if not api_key.get_secret_value():
        raise ValueError("the selected model gateway has no configured API key")
    budget = CodingRunBudget(
        max_model_requests=12,
        max_api_lookup_calls=4,
        max_concept_lookup_calls=2,
        max_diagnostic_retrieval_calls=0,
        max_abaqus_calls=0,
        max_solver_calls=0,
        wall_time_seconds=360.0,
    )
    implementation_paths = [
        Path(__file__),
        *sorted((Path(__file__).parents[1] / "coding").glob("*.py")),
    ]
    hashes = {
        str(path.relative_to(repo).as_posix()): _sha256(path)
        for path in implementation_paths
    }
    root.mkdir(parents=True, exist_ok=False)
    _write_new(root / "cases.json", [case.model_dump(mode="json") for case in cases])
    _write_new(
        root / "manifest.json",
        {
            "evaluator_version": EVALUATOR_VERSION,
            "policy_version": CONCEPT_ADOPTION_POLICY_VERSION,
            "created_at": datetime.now(UTC).isoformat(),
            "cases_sha256": _sha256(root / "cases.json"),
            "case_source_sha256": _sha256(cases_path),
            "source_role": "five authored development scenarios plus one previously observed diagnostic excerpt",
            "gold_source_sha256": source_bindings,
            "diagnostic_source": {
                "dataset": "benchmarks/rag/abaqus_2026_diagnostic_retrieval_dev_v1.jsonl",
                "dataset_sha256": _sha256(
                    repo
                    / "benchmarks/rag/abaqus_2026_diagnostic_retrieval_dev_v1.jsonl"
                ),
                "case_id": "observed-exact-output-time-points-verbatim-excerpt",
            },
            "implementation_sha256": hashes,
            "skill_sha256": _sha256(
                skill_library_path() / "abaqus-2024-coding/SKILL.md"
            ),
            "environment_seal_sha256": environment.seal_sha256,
            "index": binding.model_dump(mode="json"),
            "catalog": api_binding.model_dump(mode="json"),
            "model": {
                "gateway": gateway.value,
                "name": model_name,
                "http_retries": http_retries,
                "http_timeout_seconds": 60,
            },
            "budget": budget.model_dump(mode="json"),
            "task_instructions": TASK_INSTRUCTIONS,
            "api_catalog_mode": "full",
            "concept_retrieval_mode": "tool",
            "diagnostic_retrieval_mode": "off",
            "planning_enabled": False,
            "repeats": 1,
            "dataset_role": "development_behavior_smoke",
            "grading": "tool facts automatically recorded; relevance and answer quality reviewed separately",
            "pydantic_ai_version": version("pydantic-ai-slim"),
            "limitations": [
                "Preferred routes are inspection hypotheses, not proof the model lacks knowledge.",
                "No lookup with a correct answer is not automatically a routing failure.",
                "Gold page hits do not prove answer-section relevance or causal adoption.",
                "Recorded solver diagnostic is a supplied file, not a new solver invocation.",
                "One run per scenario cannot estimate a reliable success rate.",
            ],
        },
    )
    observations: list[dict[str, object]] = []
    for case in cases:
        workspace = root / case.case_id / "workspace"
        state = root / case.case_id / "state"
        workspace.mkdir(parents=True)
        state.mkdir()
        for name, content in case.seed_files.items():
            with (workspace / name).open("x", encoding="utf-8", newline="\n") as stream:
                stream.write(content)
        request = CodingRunRequest(
            run_id=f"docs-behavior-{case.case_id}",
            goal=case.goal,
            workspace=workspace,
            state_dir=state,
            runtime=RuntimeProfile(
                launcher_path=DEFAULT_ABAQUS_2024_LAUNCHER.resolve()
            ),
            **budget.as_request_controls(),
            api_catalog=api_binding,
            concept_index=binding,
        )
        initial = WorkspaceResolver(captured_cwd=workspace).snapshot(workspace)
        model, client = create_deepseek_chat_model(
            gateway=gateway,
            model_name=model_name,
            api_key=api_key,
            http_retries=http_retries,
            conversation_id=request.run_id
            if gateway is DeepSeekGateway.OPENCODE_GO
            else None,
        )
        dependencies = CodingAgentDependencies(
            request=request,
            runner=Abaqus2024Runner(workspace, state),
            api_catalog=catalog,
            concept_index=index,
        )
        service = CodingRunService(
            agent=create_abaqus_coding_agent(
                model,
                request,
                skill_library=skill_library_path(),
                api_catalog_mode="full",
                concept_retrieval_mode="tool",
                diagnostic_retrieval_mode="off",
                planning_enabled=False,
                task_instructions=TASK_INSTRUCTIONS,
            ),
            request=request,
            dependencies=dependencies,
            clarification_provider=ScriptedClarificationProvider({}),
            verifier=ExecutionEvidenceVerifier(),
            initial_snapshot=initial,
        )
        _phase(
            root,
            "case_started",
            case_id=case.case_id,
            manifest_sha256=_sha256(root / "manifest.json"),
        )
        error: str | None = None
        try:
            await service.run()
        except Exception as exc:
            error = type(exc).__name__
        finally:
            await client.close()
        observation = observe_case(root, case)
        observation["infrastructure_error_type"] = error
        observation["actual_abaqus_calls"] = dependencies.abaqus_calls
        observation["actual_solver_calls"] = dependencies.solver_calls
        _write_new(state / "behavior_observation.json", observation)
        observations.append(observation)
        _phase(
            root,
            "case_recorded",
            case_id=case.case_id,
            observation_sha256=_sha256(state / "behavior_observation.json"),
        )
        print(
            f"{case.case_id}: recorded; API={dependencies.api_lookup_calls}, docs={dependencies.concept_lookup_calls}, Abaqus={dependencies.abaqus_calls}",
            flush=True,
        )
    after = audit_reproduction(index, environment_seal, repository_root=repo)
    if (
        after.seal_sha256 != environment.seal_sha256
        or concept_index_binding(index) != binding
    ):
        raise RuntimeError("retrieval environment changed during behavior evaluation")
    if _gold_source_bindings(docs_root, cases) != source_bindings:
        raise RuntimeError("declared gold sources changed during behavior evaluation")
    if hashes != {
        str(path.relative_to(repo).as_posix()): _sha256(path)
        for path in implementation_paths
    }:
        raise RuntimeError("implementation changed during behavior evaluation")
    _write_new(
        root / "report.json",
        {
            "manifest_sha256": _sha256(root / "manifest.json"),
            "cases": observations,
            "review_status": "pending_manual_review",
            "environment_verified_after_run": True,
        },
    )
    _phase(root, "batch_recorded", report_sha256=_sha256(root / "report.json"))
    _write_new(
        root / "completion.json",
        {
            "manifest_sha256": _sha256(root / "manifest.json"),
            "report_sha256": _sha256(root / "report.json"),
            "files_sha256": {
                path.relative_to(root).as_posix(): _sha256(path)
                for path in sorted(root.rglob("*"))
                if path.is_file()
            },
            "scope": "recorded behavior evidence; answer review is separate",
        },
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run six offline document-tool behavior probes with a real model."
    )
    parser.add_argument("--run-root", type=Path, required=True)
    parser.add_argument("--index-dir", type=Path, required=True)
    parser.add_argument("--catalog-dir", type=Path, required=True)
    parser.add_argument("--environment-seal", type=Path, required=True)
    args = parser.parse_args()
    asyncio.run(
        run_evaluation(
            args.run_root,
            index_dir=args.index_dir,
            catalog_dir=args.catalog_dir,
            environment_seal=args.environment_seal,
        )
    )


if __name__ == "__main__":
    main()
