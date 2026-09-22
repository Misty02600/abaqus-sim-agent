from __future__ import annotations

import asyncio
import math
import re
import sqlite3
from pathlib import Path

import pytest
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    ToolCallPart,
    ToolReturnPart,
)
from pydantic_ai.models.function import AgentInfo, FunctionModel

from abaqus_sim_agent.coding.agent import create_abaqus_coding_agent
from abaqus_sim_agent.coding.clarification import ScriptedClarificationProvider
from abaqus_sim_agent.coding.concept_lookup import (
    DOC_QUERY_FORMULATION_VERSION,
    _document_query_filters,
    concept_index_binding,
    doc_question_key,
)
from abaqus_sim_agent.coding.contracts import (
    AbaqusDocEvidenceBundle,
    AbaqusDocQuestion,
    CodingRunBudget,
    CodingRunRequest,
    CodingRunStatus,
    ConceptLookupTrace,
    FailedDecision,
    RuntimeProfile,
)
from abaqus_sim_agent.coding.dependencies import CodingAgentDependencies
from abaqus_sim_agent.coding.execution_verifier import ExecutionEvidenceVerifier
from abaqus_sim_agent.coding.instructions import CONCEPT_ADOPTION_POLICY_VERSION
from abaqus_sim_agent.coding.resume import (
    CodingRunResumeError,
    DependencyResumeState,
    create_step_store,
    load_document_lookup_traces,
    load_resume_state,
)
from abaqus_sim_agent.coding.service import (
    CodingRunInfrastructureError,
    CodingRunService,
)
from abaqus_sim_agent.coding.workspace import WorkspaceResolver
from abaqus_sim_agent.execution import Abaqus2024Runner
from abaqus_sim_agent.retrieval import (
    AbaqusDocumentIndex,
    AbaqusDocumentLoader,
)
from abaqus_sim_agent.retrieval.types import Embeddings

_FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "abaqus_docs"
_VOCABULARY = ("static", "stress", "element", "plane", "output")


class _FixtureEmbeddings(Embeddings):
    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [_embed(text) for text in texts]

    def embed_query(self, text: str) -> list[float]:
        return _embed(text)


def _embed(text: str) -> list[float]:
    words = re.findall(r"[a-z]+", text.casefold())
    vector = [float(words.count(token)) for token in _VOCABULARY]
    norm = math.sqrt(sum(value * value for value in vector))
    return [value / norm for value in vector] if norm else [0.0, 0.0, 0.0, 0.0, 1.0]


def _index(path: Path) -> AbaqusDocumentIndex:
    loader = AbaqusDocumentLoader(
        _FIXTURE_ROOT,
        guide_ids=("SIMACAEANLRefMap", "SIMACAEKEYRefMap", "SIMACAEELMRefMap"),
    )
    index, _manifest = AbaqusDocumentIndex.build(
        loader,
        path,
        embeddings=_FixtureEmbeddings(),
        collection_name="concept_tool_fixture",
    )
    return index


def test_optional_concept_tool_is_compact_unlimited_and_traced(tmp_path: Path) -> None:
    workspace = (tmp_path / "workspace").resolve()
    state_dir = (tmp_path / "state").resolve()
    workspace.mkdir()
    state_dir.mkdir()
    index = _index((tmp_path / "index").resolve())
    request = CodingRunRequest(
        run_id="concept-tool-test",
        goal="Inspect modeling documentation without assuming physical values.",
        workspace=workspace,
        state_dir=state_dir,
        runtime=RuntimeProfile(
            launcher_path=Path(r"D:\SIMULIA\Commands\abq2024.bat").resolve()
        ),
        concept_index=concept_index_binding(index),
    )
    call_count = 0
    observed: list[ModelMessage] = []

    def model_function(
        messages: list[ModelMessage],
        info: AgentInfo,
    ) -> ModelResponse:
        nonlocal call_count
        call_count += 1
        tools = {tool.name for tool in info.function_tools}
        assert "search_abaqus_docs" in tools
        assert "lookup_abaqus_concept" not in tools
        schema = next(
            tool.parameters_json_schema
            for tool in info.function_tools
            if tool.name == "search_abaqus_docs"
        )
        assert set(schema["properties"]) == {"question", "observed_context"}
        assert info.instructions is not None
        normalized_instructions = " ".join(info.instructions.split())
        assert "exactly one lookup before" not in normalized_instructions
        assert "Zero lookups are valid" in normalized_instructions
        assert "after a successful run with warnings" in normalized_instructions
        assert "specific unresolved technical fact" in normalized_instructions
        assert "lookup budget" not in normalized_instructions
        assert (
            "must never invent or override a missing physical input"
            in normalized_instructions
        )
        if call_count == 1:
            args = {
                "question": "plane stress element and compatible section",
                "observed_context": "CPS4R, Abaqus/Standard",
            }
        elif call_count == 2:
            args = {
                "question": "PLANE STRESS ELEMENT AND COMPATIBLE SECTION",
                "observed_context": "cps4r, abaqus/standard",
            }
        elif call_count == 3:
            args = {"question": "static equilibrium", "observed_context": "*STATIC"}
        elif call_count == 4:
            args = {"question": "stress output variables"}
        else:
            observed.extend(messages)
            output_tool = next(
                tool
                for tool in info.output_tools
                if tool.name.endswith("FailedDecision")
            )
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        output_tool.name,
                        {
                            "status": "failed",
                            "failure_stage": "test",
                            "summary": "Concept evidence inspected.",
                        },
                    )
                ]
            )
        return ModelResponse(
            parts=[
                ToolCallPart(
                    "search_abaqus_docs",
                    args,
                    tool_call_id=f"concept-{call_count}",
                )
            ]
        )

    dependencies = CodingAgentDependencies(
        request=request,
        runner=Abaqus2024Runner(workspace, state_dir),
        concept_index=index,
    )
    agent = create_abaqus_coding_agent(
        FunctionModel(model_function),
        request,
        concept_retrieval_mode="tool",
        planning_enabled=False,
    )
    result = asyncio.run(agent.run(request.goal, deps=dependencies))

    assert isinstance(result.output, FailedDecision)
    assert dependencies.concept_lookup_calls == 3
    returns = [
        part
        for message in observed
        if isinstance(message, ModelRequest)
        for part in message.parts
        if isinstance(part, ToolReturnPart) and part.tool_name == "search_abaqus_docs"
    ]
    assert len(returns) == 4
    first = str(returns[0].content)
    assert all(
        key in first
        for key in ("title", "section", "excerpt", "source", "match", "CPS4R")
    )
    assert "duplicate" in str(returns[1].content)
    assert "budget is exhausted" not in str(returns[3].content).casefold()
    first_bundle = AbaqusDocEvidenceBundle.model_validate(returns[0].content)
    assert all(item.release == "2026 FD02" for item in first_bundle.evidence)
    assert all(item.section_id for item in first_bundle.evidence)
    assert all(item.heading_path for item in first_bundle.evidence)
    assert all(
        item.page_products_source != "unrecorded" for item in first_bundle.evidence
    )
    assert "answered" not in first_bundle.model_dump()

    traces = [
        ConceptLookupTrace.model_validate_json(line)
        for line in (state_dir / "concept_lookups.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    assert [trace.status for trace in traces] == [
        "found",
        "duplicate",
        "found",
        "found",
    ]
    assert [trace.counted for trace in traces] == [True, False, True, True]
    assert traces[0].exact_terms == ("CPS4R",)
    assert traces[0].products == ("Abaqus/Standard",)
    assert traces[0].document_ids
    assert [hit.rank for hit in traces[0].ranked_hits] == list(
        range(1, len(traces[0].ranked_hits) + 1)
    )
    assert (
        tuple(hit.document_id for hit in traces[0].ranked_hits)
        == traces[0].document_ids
    )
    assert traces[0].context.trigger_source == "task_start"
    assert traces[0].context.trigger_outcome == "none"
    assert traces[0].context.tool_call_id == "concept-1"
    assert traces[0].context.execution_count_before_lookup == 0
    assert traces[0].context.recorded_at is not None
    assert traces[0].payload_bytes > 0
    assert traces[0].question == "plane stress element and compatible section"
    assert traces[0].observed_context == "CPS4R, Abaqus/Standard"
    assert traces[0].query.endswith("\nCPS4R, Abaqus/Standard")
    assert traces[0].tool_name == "search_abaqus_docs"
    assert traces[0].index == request.concept_index
    assert traces[0].formulation_version == DOC_QUERY_FORMULATION_VERSION
    assert CONCEPT_ADOPTION_POLICY_VERSION == "docs-tool-selective-v1"


def _request(tmp_path: Path, index: AbaqusDocumentIndex) -> CodingRunRequest:
    workspace = (tmp_path / "workspace").resolve()
    state = (tmp_path / "state").resolve()
    workspace.mkdir()
    state.mkdir()
    return CodingRunRequest(
        run_id="docs-test",
        goal="Inspect the existing project and use documentation only when needed.",
        workspace=workspace,
        state_dir=state,
        runtime=RuntimeProfile(
            launcher_path=Path(r"D:\SIMULIA\Commands\abq2024.bat").resolve()
        ),
        **CodingRunBudget(max_model_requests=8).as_request_controls(),
        concept_index=concept_index_binding(index),
    )


def test_query_filters_use_index_metadata_and_respect_symbol_boundaries(
    tmp_path: Path,
) -> None:
    index = _index(tmp_path / "index")
    assert _document_query_filters(
        index, "当前 CPS4R; use *STATIC with Abaqus/Standard"
    ) == (("CPS4R", "*STATIC"), ("Abaqus/Standard",))
    assert _document_query_filters(index, "prefixCPS4R CPS4R_suffix") == ((), ())
    assert _document_query_filters(index, "ALLSDTOL for *STATIC") == (("*STATIC",), ())
    assert _document_query_filters(index, "Can a static step reach equilibrium?") == (
        (),
        (),
    )


def test_query_identity_preserves_changed_context_and_task_revision() -> None:
    question = AbaqusDocQuestion(
        question="Which output is valid?", observed_context="RF3"
    )
    normalized = AbaqusDocQuestion(
        question=" WHICH output is valid? ", observed_context=" rf3 "
    )
    changed = question.model_copy(update={"observed_context": "RF1"})
    assert doc_question_key(question, 0) == doc_question_key(normalized, 0)
    assert doc_question_key(question, 0) != doc_question_key(changed, 0)
    assert doc_question_key(question, 0) != doc_question_key(question, 1)
    with pytest.raises(ValueError, match="documentation question"):
        AbaqusDocQuestion(question=" \n ")
    with pytest.raises(ValueError, match="at most 4000"):
        AbaqusDocQuestion(question="question", observed_context="x" * 4_001)


@pytest.mark.parametrize("enabled", [False, True])
def test_docs_are_optional_and_zero_lookup_does_not_block_local_work(
    tmp_path: Path, enabled: bool
) -> None:
    index = _index(tmp_path / "index")
    request = _request(tmp_path, index)
    calls = 0

    def model_function(_messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        nonlocal calls
        calls += 1
        names = {tool.name for tool in info.function_tools}
        assert ("search_abaqus_docs" in names) is enabled
        assert "lookup_abaqus_concept" not in names
        if calls == 1:
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        "write_file",
                        {"path": "note.txt", "content": "Local evidence is enough."},
                    )
                ]
            )
        ready = next(
            t.name
            for t in info.output_tools
            if t.name.endswith("ReadyForVerificationDecision")
        )
        return ModelResponse(
            parts=[
                ToolCallPart(
                    ready,
                    {
                        "status": "ready_for_verification",
                        "deliverables": [{"relative_path": "note.txt"}],
                        "summary": "Requested local artifact is ready.",
                    },
                )
            ]
        )

    dependencies = CodingAgentDependencies(
        request=request,
        runner=Abaqus2024Runner(request.workspace, request.state_dir),
        concept_index=index,
    )
    result = asyncio.run(
        create_abaqus_coding_agent(
            FunctionModel(model_function),
            request,
            concept_retrieval_mode="tool" if enabled else "off",
            planning_enabled=False,
        ).run(request.goal, deps=dependencies)
    )
    assert result.output.status == "ready_for_verification"
    assert (request.workspace / "note.txt").is_file()
    assert dependencies.concept_lookup_calls == 0
    assert not (request.state_dir / "concept_lookups.jsonl").exists()


@pytest.mark.parametrize("search_error", [False, True])
def test_document_search_empty_or_database_error_is_reported_and_journaled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, search_error: bool
) -> None:
    index = _index(tmp_path / "index")
    request = _request(tmp_path, index)
    calls = 0

    def search(*_args: object, **_kwargs: object) -> list[object]:
        if search_error:
            raise sqlite3.OperationalError("unavailable database")
        return []

    monkeypatch.setattr(index, "search", search)

    def model_function(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        nonlocal calls
        calls += 1
        if calls == 1:
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        "search_abaqus_docs", {"question": "Which element applies?"}
                    )
                ]
            )
        part = next(
            part
            for message in reversed(messages)
            if isinstance(message, ModelRequest)
            for part in message.parts
            if isinstance(part, ToolReturnPart)
            and part.tool_name == "search_abaqus_docs"
        )
        bundle = AbaqusDocEvidenceBundle.model_validate(part.content)
        assert bundle.status == ("error" if search_error else "not_found")
        assert not bundle.evidence
        assert bundle.error_type == ("OperationalError" if search_error else None)
        failed = next(
            t.name for t in info.output_tools if t.name.endswith("FailedDecision")
        )
        return ModelResponse(
            parts=[
                ToolCallPart(
                    failed,
                    {
                        "status": "failed",
                        "failure_stage": "test",
                        "summary": "Result inspected.",
                    },
                )
            ]
        )

    dependencies = CodingAgentDependencies(
        request=request,
        runner=Abaqus2024Runner(request.workspace, request.state_dir),
        concept_index=index,
    )
    before = DependencyResumeState.capture(dependencies)
    asyncio.run(
        create_abaqus_coding_agent(
            FunctionModel(model_function),
            request,
            concept_retrieval_mode="tool",
            planning_enabled=False,
        ).run(request.goal, deps=dependencies)
    )
    traces = load_document_lookup_traces(request.state_dir)
    assert len(traces) == 1
    assert traces[0].counted
    restored = CodingAgentDependencies(
        request=request,
        runner=Abaqus2024Runner(request.workspace, request.state_dir),
        concept_index=index,
    )
    before.restore_into(restored, journaled_document_lookups=traces)
    assert restored.concept_lookup_calls == 1
    assert restored.concept_lookup_queries == dependencies.concept_lookup_queries
    assert restored.diagnostic_retrieval_calls == 0
    with (request.state_dir / "concept_lookups.jsonl").open(
        "a", encoding="utf-8"
    ) as stream:
        stream.write('{"tool_name":"search_abaqus_docs"}\n')
    with pytest.raises(CodingRunResumeError, match="document lookup journal"):
        load_document_lookup_traces(request.state_dir)


def test_service_resumes_document_query_without_retrieving_it_again(
    tmp_path: Path,
) -> None:
    index = _index(tmp_path / "index")
    request = _request(tmp_path, index)
    initial = WorkspaceResolver(captured_cwd=request.workspace).snapshot(
        request.workspace
    )
    first_calls = 0
    query_args = {"question": "Which section fits?", "observed_context": "CPS4R"}

    def interrupted_model(
        _messages: list[ModelMessage], _info: AgentInfo
    ) -> ModelResponse:
        nonlocal first_calls
        first_calls += 1
        if first_calls == 1:
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        "search_abaqus_docs",
                        query_args,
                        tool_call_id="docs-before-outage",
                    )
                ]
            )
        raise ConnectionError("provider outage after document retrieval")

    def service(model: FunctionModel, *, resume: bool = False) -> CodingRunService:
        store = create_step_store(request.state_dir)
        dependencies = CodingAgentDependencies(
            request=request,
            runner=Abaqus2024Runner(request.workspace, request.state_dir),
            concept_index=index,
            step_store=store,
        )
        return CodingRunService(
            agent=create_abaqus_coding_agent(
                model,
                request,
                concept_retrieval_mode="tool",
                planning_enabled=False,
                step_store=store,
            ),
            request=request,
            dependencies=dependencies,
            clarification_provider=ScriptedClarificationProvider({}),
            verifier=ExecutionEvidenceVerifier(),
            initial_snapshot=initial,
            resume=resume,
            step_store=store,
        )

    with pytest.raises(CodingRunInfrastructureError, match="provider outage"):
        asyncio.run(service(FunctionModel(interrupted_model)).run())
    assert load_resume_state(request.state_dir).dependencies.concept_lookup_calls == 1
    resumed_calls = 0

    def resumed_model(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        nonlocal resumed_calls
        resumed_calls += 1
        assert "docs-before-outage" in str(messages)
        if resumed_calls == 1:
            return ModelResponse(parts=[ToolCallPart("search_abaqus_docs", query_args)])
        if resumed_calls == 2:
            assert "duplicate" in str(messages[-1])
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        "search_abaqus_docs", {**query_args, "observed_context": "CPS4"}
                    )
                ]
            )
        if resumed_calls == 3:
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        "write_file",
                        {
                            "path": "note.txt",
                            "content": "Document candidates inspected.",
                        },
                    )
                ]
            )
        ready = next(
            t.name
            for t in info.output_tools
            if t.name.endswith("ReadyForVerificationDecision")
        )
        return ModelResponse(
            parts=[
                ToolCallPart(
                    ready,
                    {
                        "status": "ready_for_verification",
                        "deliverables": [{"relative_path": "note.txt"}],
                        "summary": "Local result completed after resuming.",
                    },
                )
            ]
        )

    result = asyncio.run(service(FunctionModel(resumed_model), resume=True).run())
    assert result.outcome.status is CodingRunStatus.COMPLETED
    assert result.outcome.usage.concept_lookup_calls == 2
    assert result.outcome.usage.abaqus_calls == 0
    assert load_resume_state(request.state_dir).turn == 2
    assert [
        trace.status for trace in load_document_lookup_traces(request.state_dir)
    ] == ["found", "duplicate", "found"]
