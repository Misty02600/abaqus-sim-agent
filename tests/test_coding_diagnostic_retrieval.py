from __future__ import annotations

import asyncio
import hashlib
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

import abaqus_sim_agent.coding.runtime_tools as runtime_tools_module
from abaqus_sim_agent.coding.agent import create_abaqus_coding_agent
from abaqus_sim_agent.coding.concept_lookup import concept_index_binding
from abaqus_sim_agent.coding.contracts import (
    CodingRunBudget,
    CodingRunRequest,
    DiagnosticRetrievalTrace,
    FailedDecision,
    RuntimeProfile,
)
from abaqus_sim_agent.coding.dependencies import CodingAgentDependencies
from abaqus_sim_agent.coding.diagnostic_retrieval import (
    DIAGNOSTIC_FETCH_K,
    DIAGNOSTIC_QUERY_FORMULATION_VERSION,
    DIAGNOSTIC_RRF_K,
    DIAGNOSTIC_TOP_K,
    formulate_solver_diagnostic_query,
    maybe_add_solver_diagnostic_advisory,
)
from abaqus_sim_agent.coding.resume import (
    DependencyResumeState,
    load_diagnostic_retrieval_traces,
)
from abaqus_sim_agent.coding.runtime_tools import _runtime_tool_result
from abaqus_sim_agent.execution import (
    Abaqus2024Runner,
    AbaqusDiagnostic,
    AbaqusExecutionResult,
    AbaqusInvocation,
    ArtifactFingerprint,
)
from abaqus_sim_agent.retrieval import (
    AbaqusDocumentIndex,
    AbaqusDocumentLoader,
    RetrievalHit,
)
from abaqus_sim_agent.retrieval.reproduction import dense_vector_storage_snapshot
from abaqus_sim_agent.retrieval.types import Document, Embeddings

_FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "abaqus_docs"
_VOCABULARY = ("static", "stress", "element", "plane", "output")
_WARNING = (
    "***WARNING: OUTPUT REQUEST RF3 IS NOT VALID SINCE IT REQUESTS OUTPUT "
    "AT A DOF THAT IS NOT ACTIVE"
)


class _FixtureEmbeddings(Embeddings):
    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [_embed(text) for text in texts]

    def embed_query(self, text: str) -> list[float]:
        return _embed(text)


def _embed(text: str) -> list[float]:
    words = re.findall(r"[a-z]+", text.casefold())
    vector = [float(words.count(token)) for token in _VOCABULARY]
    norm = math.sqrt(sum(value * value for value in vector))
    return [value / norm for value in vector] if norm else [0.0] * len(vector)


def _index(path: Path) -> AbaqusDocumentIndex:
    loader = AbaqusDocumentLoader(
        _FIXTURE_ROOT,
        guide_ids=("SIMACAEANLRefMap", "SIMACAEKEYRefMap", "SIMACAEELMRefMap"),
    )
    index, _manifest = AbaqusDocumentIndex.build(
        loader,
        path,
        embeddings=_FixtureEmbeddings(),
        collection_name="diagnostic_advisory_fixture",
    )
    index.build_bm25()
    return index


def _request(tmp_path: Path, index: AbaqusDocumentIndex) -> CodingRunRequest:
    workspace = (tmp_path / "workspace").resolve()
    state_dir = (tmp_path / "state").resolve()
    workspace.mkdir()
    state_dir.mkdir()
    return CodingRunRequest(
        run_id="diagnostic-advisory-test",
        goal="Repair the verified CPS4R plane stress plate model.",
        workspace=workspace,
        state_dir=state_dir,
        runtime=RuntimeProfile(
            launcher_path=Path(r"D:\SIMULIA\Commands\abq2024.bat").resolve()
        ),
        concept_index=concept_index_binding(index),
        **CodingRunBudget(
            max_abaqus_calls=1,
            max_solver_calls=1,
            max_diagnostic_retrieval_calls=1,
        ).as_request_controls(),
    )


def _result(
    request: CodingRunRequest, *, message: str = _WARNING
) -> AbaqusExecutionResult:
    return AbaqusExecutionResult(
        invocation=AbaqusInvocation.SOLVER_JOB,
        command=("abq2024.bat", "job=plate", "input=plate.inp"),
        workspace=request.workspace,
        exit_code=0,
        timed_out=False,
        duration_seconds=1.0,
        stdout="analysis complete",
        stderr="",
        stdout_encoding="utf-8",
        stderr_encoding="utf-8",
        stdout_raw_path=request.state_dir / "stdout.bin",
        stderr_raw_path=request.state_dir / "stderr.bin",
        diagnostics=(
            AbaqusDiagnostic(
                level="warning",
                source="plate.dat",
                line=10,
                message=message,
            ),
        ),
    )


def test_formulation_uses_only_bounded_goal_and_diagnostic_terms() -> None:
    query = formulate_solver_diagnostic_query(
        "Repair the verified CPS4R plane stress plate model.",
        _WARNING,
    )

    assert query == "CPS4R RF3 plane stress plate OUTPUT DOF ACTIVE"
    assert "1 2" not in query
    assert len(query.split()) <= 12
    assert DIAGNOSTIC_QUERY_FORMULATION_VERSION == "goal-identifier-window-v1"


def test_advisory_evidence_keeps_page_scope_separate_from_heading_mentions() -> None:
    from abaqus_sim_agent.coding.diagnostic_retrieval import _evidence_payload

    hit = RetrievalHit(
        document=Document(
            page_content="A candidate passage.",
            metadata={
                "document_id": "mixed-page",
                "section_id": "explicit-section",
                "section_title": "Field output",
                "products": ["Abaqus/Standard", "Abaqus/Explicit"],
                "products_source": "official_page_header",
                "heading_path": ["Output", "Output in Abaqus/Explicit", "Field output"],
                "section_product_mentions": ["Abaqus/Explicit"],
                "section_product_heading": "Output in Abaqus/Explicit",
            },
        ),
        match_type="hybrid",
    )
    evidence = _evidence_payload(hit)
    assert evidence["page_products"] == ["Abaqus/Standard", "Abaqus/Explicit"]
    assert evidence["section_product_mentions"] == ["Abaqus/Explicit"]
    assert evidence["page_products_source"] == "official_page_header"
    assert "applicable" not in evidence


def test_control_and_advisory_can_share_identical_instructions(tmp_path: Path) -> None:
    index = _index((tmp_path / "index").resolve())
    request = _request(tmp_path, index)
    captured: list[str] = []

    def capture_model(
        _messages: list[ModelMessage],
        info: AgentInfo,
    ) -> ModelResponse:
        assert info.instructions is not None
        captured.append(info.instructions)
        output_tool = next(
            tool for tool in info.output_tools if tool.name.endswith("FailedDecision")
        )
        return ModelResponse(
            parts=[
                ToolCallPart(
                    output_tool.name,
                    {
                        "status": "failed",
                        "failure_stage": "test",
                        "summary": "Captured shared instructions.",
                    },
                )
            ]
        )

    control = create_abaqus_coding_agent(
        FunctionModel(capture_model),
        request,
        diagnostic_retrieval_mode="off",
        diagnostic_retrieval_instructions_enabled=True,
        planning_enabled=False,
    )
    advisory = create_abaqus_coding_agent(
        FunctionModel(capture_model),
        request,
        diagnostic_retrieval_mode="advisory",
        diagnostic_retrieval_instructions_enabled=True,
        planning_enabled=False,
    )

    for agent in (control, advisory):
        dependencies = CodingAgentDependencies(
            request=request,
            runner=Abaqus2024Runner(request.workspace, request.state_dir),
            concept_index=index,
        )
        result = asyncio.run(agent.run(request.goal, deps=dependencies))
        assert isinstance(result.output, FailedDecision)

    assert captured[0] == captured[1]


def test_concept_binding_changes_when_dense_vector_bytes_drift(tmp_path: Path) -> None:
    index = _index((tmp_path / "index").resolve())
    before = concept_index_binding(index)
    snapshot = dense_vector_storage_snapshot(
        index.persist_directory,
        collection_name=index.collection_name,
    )
    target = index.persist_directory / snapshot.files[0].path
    original = target.read_bytes()
    try:
        target.write_bytes(original + b"drift")
        after = concept_index_binding(index)
    finally:
        target.write_bytes(original)

    assert before.dense_vector_sha256 != after.dense_vector_sha256


def test_warning_success_runs_fixed_rrf_once_and_records_strict_trace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    index = _index((tmp_path / "index").resolve())
    request = _request(tmp_path, index)
    result = _result(request)
    dependencies = CodingAgentDependencies(
        request=request,
        runner=Abaqus2024Runner(request.workspace, request.state_dir),
        concept_index=index,
    )
    dependencies.current_turn = 2
    dependencies.record_runtime_execution(result)
    calls: list[tuple[str, int, int, int]] = []

    def search_hybrid(
        _self: AbaqusDocumentIndex,
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
                    id="simaelm-r-2delem#plane-stress",
                    page_content="CPS4R Active Degrees of Freedom 1, 2",
                    metadata={
                        "document_id": "simaelm-r-2delem",
                        "title": "Two-Dimensional Solid Element Library",
                        "section_id": "plane-stress",
                        "section_title": "Plane Stress Elements",
                    },
                ),
                match_type="hybrid",
                score=0.03,
            )
        ]

    monkeypatch.setattr(AbaqusDocumentIndex, "search_hybrid", search_hybrid)

    payload = asyncio.run(maybe_add_solver_diagnostic_advisory(dependencies, result))

    assert payload is not None
    assert payload["status"] == "found"
    assert payload["trace_status"] == "recorded"
    assert payload["evidence_id"]
    assert "source" not in payload["evidence"][0]  # type: ignore[index]
    assert calls == [
        (
            "CPS4R RF3 plane stress plate OUTPUT DOF ACTIVE",
            DIAGNOSTIC_TOP_K,
            DIAGNOSTIC_FETCH_K,
            DIAGNOSTIC_RRF_K,
        )
    ]
    assert dependencies.diagnostic_retrieval_calls == 1
    assert dependencies.concept_lookup_calls == 0
    trace = DiagnosticRetrievalTrace.model_validate_json(
        (request.state_dir / "diagnostic_retrievals.jsonl").read_text(encoding="utf-8")
    )
    assert trace.status == "found"
    assert trace.counted
    assert trace.turn == 2
    assert trace.execution_index == 0
    assert trace.ranked_hits[0].document_id == "simaelm-r-2delem"
    assert trace.index is not None
    assert len(trace.index.bm25_database_sha256) == 64

    duplicate = asyncio.run(maybe_add_solver_diagnostic_advisory(dependencies, result))
    assert duplicate is not None
    assert duplicate["status"] == "duplicate"
    assert dependencies.diagnostic_retrieval_calls == 1


def test_trace_journal_closes_resume_snapshot_gap(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    index = _index((tmp_path / "index").resolve())
    request = _request(tmp_path, index)
    result = _result(request)
    original = CodingAgentDependencies(
        request=request,
        runner=Abaqus2024Runner(request.workspace, request.state_dir),
        concept_index=index,
    )
    original.record_runtime_execution(result)
    monkeypatch.setattr(
        AbaqusDocumentIndex,
        "search_hybrid",
        lambda *_args, **_kwargs: [],
    )
    assert (
        asyncio.run(maybe_add_solver_diagnostic_advisory(original, result)) is not None
    )
    traces = load_diagnostic_retrieval_traces(request.state_dir)
    assert len(traces) == 1

    restored = CodingAgentDependencies(
        request=request,
        runner=Abaqus2024Runner(request.workspace, request.state_dir),
        concept_index=index,
    )
    DependencyResumeState().restore_into(
        restored,
        journaled_diagnostic_retrievals=traces,
    )

    assert restored.diagnostic_retrieval_calls == 1
    assert restored.diagnostic_retrieval_keys == {f"0:{traces[0].diagnostic_sha256}"}


def test_invalid_trace_journal_fails_closed(tmp_path: Path) -> None:
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    (state_dir / "diagnostic_retrievals.jsonl").write_text(
        "{broken\n",
        encoding="utf-8",
    )

    with pytest.raises(RuntimeError, match="invalid diagnostic retrieval journal"):
        load_diagnostic_retrieval_traces(state_dir)


@pytest.mark.parametrize(
    ("invocation", "timed_out", "message"),
    [
        (AbaqusInvocation.CAE_NOGUI, False, _WARNING),
        (AbaqusInvocation.SOLVER_JOB, True, _WARNING),
        (AbaqusInvocation.SOLVER_JOB, False, "1 WARNING MESSAGES"),
        (AbaqusInvocation.SOLVER_JOB, False, "THE ANALYSIS HAS TERMINATED"),
    ],
)
def test_ineligible_runtime_evidence_does_not_retrieve(
    tmp_path: Path,
    invocation: AbaqusInvocation,
    timed_out: bool,
    message: str,
) -> None:
    index = _index((tmp_path / "index").resolve())
    request = _request(tmp_path, index)
    base = _result(request, message=message)
    result = base.model_copy(update={"invocation": invocation, "timed_out": timed_out})
    dependencies = CodingAgentDependencies(
        request=request,
        runner=Abaqus2024Runner(request.workspace, request.state_dir),
        concept_index=index,
    )
    dependencies.record_runtime_execution(result)

    assert (
        asyncio.run(maybe_add_solver_diagnostic_advisory(dependencies, result)) is None
    )
    assert dependencies.diagnostic_retrieval_calls == 0


def test_advisory_is_visible_in_same_solver_tool_return(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    index = _index((tmp_path / "index").resolve())
    request = _request(tmp_path, index)
    observed: list[ModelMessage] = []
    calls = 0

    async def run_solver_job(
        _runner: Abaqus2024Runner,
        job_name: str,
        *,
        input_file: str | None = None,
        timeout_seconds: float = 300.0,
    ) -> AbaqusExecutionResult:
        del job_name, input_file, timeout_seconds
        return _result(request)

    monkeypatch.setattr(Abaqus2024Runner, "run_solver_job", run_solver_job)

    def model_function(
        messages: list[ModelMessage],
        info: AgentInfo,
    ) -> ModelResponse:
        nonlocal calls
        calls += 1
        assert "search_abaqus_docs" not in {tool.name for tool in info.function_tools}
        assert info.instructions is not None
        normalized_instructions = " ".join(info.instructions.split())
        assert "nested `diagnostic_retrieval` advisory" in normalized_instructions
        if calls == 1:
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        "run_solver_job",
                        {"job_name": "plate", "input_file": "plate.inp"},
                        tool_call_id="solver-1",
                    )
                ]
            )
        observed.extend(messages)
        output_tool = next(
            tool for tool in info.output_tools if tool.name.endswith("FailedDecision")
        )
        return ModelResponse(
            parts=[
                ToolCallPart(
                    output_tool.name,
                    {
                        "status": "failed",
                        "failure_stage": "test",
                        "summary": "Advisory inspected.",
                    },
                )
            ]
        )

    dependencies = CodingAgentDependencies(
        request=request,
        runner=Abaqus2024Runner(request.workspace, request.state_dir),
        concept_index=index,
    )
    agent = create_abaqus_coding_agent(
        FunctionModel(model_function),
        request,
        diagnostic_retrieval_mode="advisory",
        planning_enabled=False,
    )

    result = asyncio.run(agent.run(request.goal, deps=dependencies))

    assert isinstance(result.output, FailedDecision)
    tool_return = next(
        part
        for message in observed
        if isinstance(message, ModelRequest)
        for part in message.parts
        if isinstance(part, ToolReturnPart) and part.tool_name == "run_solver_job"
    )
    assert isinstance(tool_return.content, dict)
    assert tool_return.content["stage_succeeded"] is True
    advisory = tool_return.content["diagnostic_retrieval"]
    assert isinstance(advisory, dict)
    assert advisory["status"] == "found"


def test_runtime_tool_is_a_barrier_between_workspace_writes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    index = _index((tmp_path / "index").resolve())
    request = _request(tmp_path, index)
    runtime_contents: list[str] = []
    model_calls = 0

    async def run_cae_script(
        _runner: Abaqus2024Runner,
        script: str,
        *,
        args: tuple[str, ...] = (),
        timeout_seconds: float = 180.0,
    ) -> AbaqusExecutionResult:
        del args, timeout_seconds
        content = (request.workspace / script).read_text(encoding="utf-8")
        runtime_contents.append(content)
        fingerprint = hashlib.sha256(content.encode()).hexdigest()
        return AbaqusExecutionResult(
            invocation=AbaqusInvocation.CAE_NOGUI,
            command=("abq2024.bat", "cae", f"noGUI={script}"),
            workspace=request.workspace,
            exit_code=0,
            timed_out=False,
            duration_seconds=0.1,
            stdout="ok",
            stderr="",
            stdout_encoding="utf-8",
            stderr_encoding="utf-8",
            stdout_raw_path=request.state_dir / "cae-stdout.bin",
            stderr_raw_path=request.state_dir / "cae-stderr.bin",
            input_fingerprints=(
                ArtifactFingerprint(
                    path=script,
                    size_bytes=len(content.encode()),
                    sha256=fingerprint,
                ),
            ),
        )

    monkeypatch.setattr(Abaqus2024Runner, "run_cae_script", run_cae_script)

    def model_function(
        _messages: list[ModelMessage],
        info: AgentInfo,
    ) -> ModelResponse:
        nonlocal model_calls
        model_calls += 1
        runtime_tools = {
            tool.name: tool
            for tool in info.function_tools
            if tool.name.startswith("run_")
        }
        assert all(tool.sequential for tool in runtime_tools.values())
        if model_calls == 1:
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        "write_file",
                        {"path": "build_model.py", "content": "state = 'before'\n"},
                        tool_call_id="write-before",
                    ),
                    ToolCallPart(
                        "run_cae_script",
                        {"script_path": "build_model.py"},
                        tool_call_id="runtime-barrier",
                    ),
                    ToolCallPart(
                        "write_file",
                        {"path": "build_model.py", "content": "state = 'after'\n"},
                        tool_call_id="write-after",
                    ),
                ]
            )
        output_tool = next(
            tool for tool in info.output_tools if tool.name.endswith("FailedDecision")
        )
        return ModelResponse(
            parts=[
                ToolCallPart(
                    output_tool.name,
                    {
                        "status": "failed",
                        "failure_stage": "test",
                        "summary": "Barrier observed.",
                    },
                )
            ]
        )

    dependencies = CodingAgentDependencies(
        request=request,
        runner=Abaqus2024Runner(request.workspace, request.state_dir),
        concept_index=index,
    )
    agent = create_abaqus_coding_agent(
        FunctionModel(model_function),
        request,
        planning_enabled=False,
    )

    result = asyncio.run(agent.run(request.goal, deps=dependencies))

    assert isinstance(result.output, FailedDecision)
    assert runtime_contents == ["state = 'before'\n"]
    assert (request.workspace / "build_model.py").read_text(encoding="utf-8") == (
        "state = 'after'\n"
    )


def test_advisory_database_error_preserves_original_solver_payload(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    index = _index((tmp_path / "index").resolve())
    request = _request(tmp_path, index)
    result = _result(request)
    dependencies = CodingAgentDependencies(
        request=request,
        runner=Abaqus2024Runner(request.workspace, request.state_dir),
        concept_index=index,
    )

    async def fail_advisory(
        _dependencies: CodingAgentDependencies,
        _result: AbaqusExecutionResult,
    ) -> dict[str, object] | None:
        raise sqlite3.OperationalError("locked")

    async def execution() -> AbaqusExecutionResult:
        return result

    monkeypatch.setattr(
        runtime_tools_module,
        "maybe_add_solver_diagnostic_advisory",
        fail_advisory,
    )

    payload = asyncio.run(
        _runtime_tool_result(
            dependencies,
            execution(),
            api_catalog_mode="off",
            diagnostic_retrieval_mode="advisory",
        )
    )

    assert payload["stage_succeeded"] is True
    assert payload["stdout_tail"] == "analysis complete"
    assert payload["diagnostics"] == [
        diagnostic.model_dump(mode="json") for diagnostic in result.diagnostics
    ]
    assert payload["diagnostic_retrieval"] == {
        "status": "error",
        "error_type": "OperationalError",
        "trace_status": "not_recorded",
    }
    assert dependencies.executions == [result]


def test_immutable_workspace_guard_blocks_canonical_path_aliases(
    tmp_path: Path,
) -> None:
    index = _index((tmp_path / "index").resolve())
    request = _request(tmp_path, index)
    target = request.workspace / "plate_tension.inp"
    target.write_text("original\n", encoding="utf-8")
    (request.workspace / "subdir").mkdir()
    observed: list[ModelMessage] = []
    model_calls = 0

    def model_function(
        messages: list[ModelMessage],
        info: AgentInfo,
    ) -> ModelResponse:
        nonlocal model_calls
        model_calls += 1
        if model_calls == 1:
            aliases = (
                "./plate_tension.inp",
                "subdir/../plate_tension.inp",
                r"subdir\..\plate_tension.inp",
                str(target),
            )
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        "write_file",
                        {"path": path, "content": "changed\n"},
                        tool_call_id=f"immutable-{index}",
                    )
                    for index, path in enumerate(aliases)
                ]
            )
        observed.extend(messages)
        output_tool = next(
            tool for tool in info.output_tools if tool.name.endswith("FailedDecision")
        )
        return ModelResponse(
            parts=[
                ToolCallPart(
                    output_tool.name,
                    {
                        "status": "failed",
                        "failure_stage": "test",
                        "summary": "Immutable aliases were rejected.",
                    },
                )
            ]
        )

    dependencies = CodingAgentDependencies(
        request=request,
        runner=Abaqus2024Runner(request.workspace, request.state_dir),
        concept_index=index,
    )
    agent = create_abaqus_coding_agent(
        FunctionModel(model_function),
        request,
        immutable_workspace_paths=("plate_tension.inp",),
        planning_enabled=False,
    )

    result = asyncio.run(agent.run(request.goal, deps=dependencies))

    assert isinstance(result.output, FailedDecision)
    assert target.read_text(encoding="utf-8") == "original\n"
    returns = [
        part
        for message in observed
        if isinstance(message, ModelRequest)
        for part in message.parts
        if isinstance(part, ToolReturnPart) and part.tool_name == "write_file"
    ]
    assert len(returns) == 4
    assert all("immutable for this task" in str(part.content) for part in returns)
