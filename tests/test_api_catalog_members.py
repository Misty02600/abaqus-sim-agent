from __future__ import annotations

import asyncio
import hashlib
import json
import zipfile
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError
from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    ToolCallPart,
    ToolReturnPart,
)
from pydantic_ai.models.function import FunctionModel

from abaqus_sim_agent.api_catalog import (
    AbqpyExtractionError,
    ApiCatalog,
    ApiCatalogManifest,
    ApiLookupResult,
    load_abqpy_member_cards,
    load_abqpy_records,
)
from abaqus_sim_agent.api_catalog.cli import main
from abaqus_sim_agent.coding.agent import create_abaqus_coding_agent
from abaqus_sim_agent.coding.api_lookup import runtime_api_hint
from abaqus_sim_agent.coding.cli import _api_catalog_binding
from abaqus_sim_agent.coding.contracts import (
    ApiCatalogBinding,
    ApiLookupTrace,
    CodingRunBudget,
    CodingRunRequest,
    RuntimeProfile,
)
from abaqus_sim_agent.coding.dependencies import CodingAgentDependencies
from abaqus_sim_agent.coding.resume import DependencyResumeState
from abaqus_sim_agent.execution import (
    Abaqus2024Runner,
    AbaqusExecutionResult,
    AbaqusInvocation,
)

_SOURCE = """raise RuntimeError("source must never execute")
class Base:
    #: A repository of node sets.
    nodeSets: dict[str, Set] = {}
    hidden: str
    _private: int
class OdbAssembly(Base):
    @property
    def hidden(self): ...
class OdbInstance:
    nodeSets: dict[str, Set] = {}
class FieldOutput:
    values: list[float]
    def getSubset(self, region: Set): ...
"""


def _wheel(tmp_path: Path, source: str = _SOURCE, *, version: str = "2024.10") -> Path:
    path = tmp_path / "abqpy-fixture.whl"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(
            "abqpy.dist-info/METADATA", f"Name: abqpy\nVersion: {version}\n"
        )
        archive.writestr("abaqus/__init__.py", "")
        archive.writestr("abaqus/Odb.py", source)
    return path


def _members(
    path: Path, owners: tuple[str, ...] = ("OdbAssembly", "OdbInstance", "FieldOutput")
):
    return load_abqpy_member_cards(
        path, version="2024.10", release="2024", runtime_release="2024", owners=owners
    )


def test_member_declaration_inheritance_source_and_adjacent_method(
    tmp_path: Path,
) -> None:
    path = _wheel(tmp_path)
    members = _members(path)
    catalog = ApiCatalog.build(
        runtime_release="2024",
        members=members,
        abqpy_records=load_abqpy_records(path, version="2024.10", release="2024"),
    )
    result = catalog.lookup("OdbAssembly.nodeSets")
    assert result.status == "found"
    assert result.cards == ()
    card = result.members[0]
    assert card.declaring_name == "Base.nodeSets"
    assert card.annotation == "dict[str, Set]"
    assert card.description == "A repository of node sets."
    assert card.source.version == "2024.10"
    assert card.source.source_path == "abaqus/Odb.py"
    assert card.source_sha256 == hashlib.sha256(_SOURCE.encode()).hexdigest()
    assert card.distribution_sha256 == hashlib.sha256(path.read_bytes()).hexdigest()
    assert (card.source_line, card.source_end_line) == (4, 4)
    assert "default" not in card.model_dump()
    assert catalog.lookup("abaqus.Odb.OdbAssembly.nodeSets").members == (card,)
    assert catalog.lookup("Base.nodeSets").members == (card,)
    assert catalog.lookup("nodeSets").status == "ambiguous"
    assert len(catalog.lookup("nodeSets").members) == 2
    assert catalog.lookup("OdbAssembly.missing").status == "not_found"
    assert catalog.lookup("OdbAssembly.nodesets").status == "not_found"
    assert catalog.lookup("OdbAssembly._private").status == "not_found"
    assert not catalog.lookup("OdbAssembly.hidden").members
    assert catalog.lookup("FieldOutput.getSubset.region").parameter_name == "region"


def test_c3_shadowing_and_unannotated_override(tmp_path: Path) -> None:
    path = _wheel(
        tmp_path,
        """class Root:
    value: str
    shadow: str
class Left(Root): pass
class Right(Root):
    value: int
class Child(Left, Right):
    shadow = object()
""",
    )
    members = _members(path, ("Child",))
    assert [
        (card.canonical_name, card.declaring_name, card.annotation) for card in members
    ] == [
        ("Child.value", "Right.value", "int"),
    ]


@pytest.mark.parametrize(
    "source",
    [
        "class Child(Unknown):\n    value: str\n",
        "from external import Root\nclass Root:\n    value: str\nclass Child(Root): pass\n",
        "class A: pass\nclass B: pass\nclass L(A,B): pass\nclass R(B,A): pass\nclass Child(L,R): pass\n",
        "class Root: pass\nclass Child(Root,Root): pass\n",
        "class Child:\n    if True:\n        value: str\n",
    ],
)
def test_uncertain_static_hierarchy_fails_closed(tmp_path: Path, source: str) -> None:
    with pytest.raises(AbqpyExtractionError):
        _members(_wheel(tmp_path, source), ("Child",))


def test_version_and_owner_selection_are_checked(tmp_path: Path) -> None:
    path = _wheel(tmp_path, version="2026.1")
    with pytest.raises(AbqpyExtractionError, match="identity/version"):
        _members(path)
    with pytest.raises(AbqpyExtractionError, match="release"):
        load_abqpy_member_cards(
            path,
            version="2026.1",
            release="2024",
            runtime_release="2024",
            owners=("OdbAssembly",),
        )
    with pytest.raises(AbqpyExtractionError, match="explicit owners"):
        _members(path, ())
    path = _wheel(tmp_path)
    with pytest.raises(AbqpyExtractionError, match="unknown or ambiguous"):
        _members(path, ("Unknown",))
    with zipfile.ZipFile(path, "a") as archive:
        archive.writestr("abaqus/Other.py", "class OdbAssembly: pass\n")
    with pytest.raises(AbqpyExtractionError, match="unknown or ambiguous"):
        _members(path)


def test_sidecar_roundtrip_and_hash_binding(
    tmp_path: Path,
) -> None:
    members = _members(_wheel(tmp_path))
    catalog = ApiCatalog((), runtime_release="2024", members=members)
    root = tmp_path / "catalog"
    manifest = catalog.write(root)
    assert manifest.schema_version == "2"
    assert manifest.member_count == 3
    opened = ApiCatalog.open(root)
    assert opened.members == catalog.members
    binding = _api_catalog_binding(opened)
    assert binding is not None
    assert binding.members_sha256 == manifest.members_sha256
    assert binding.member_count == 3
    other_root = tmp_path / "other-catalog"
    changed = members[0].model_copy(update={"description": "different source comment"})
    other = ApiCatalog((), runtime_release="2024", members=(changed, *members[1:]))
    other.write(other_root)
    assert _api_catalog_binding(other) != binding
    assert other.manifest is not None
    assert other.manifest.cards_sha256 == manifest.cards_sha256
    with pytest.raises(FileExistsError):
        catalog.write(root)
    (root / "members.jsonl").write_bytes(b"{}\n")
    with pytest.raises(ValueError, match="checksum"):
        ApiCatalog.open(root)
def test_member_strict_lookup_and_manifest_contracts(tmp_path: Path) -> None:
    member = _members(_wheel(tmp_path))[0]
    with pytest.raises(ValidationError):
        ApiLookupResult(
            status="found", query="x", members=(member,), parameter_name="x"
        )
    with pytest.raises(ValidationError):
        ApiLookupResult(status="not_found", query="x", members=(member,))
    with pytest.raises(ValidationError):
        ApiLookupResult(status="ambiguous", query="x", members=(member,))
    with pytest.raises(ValidationError):
        ApiCatalogBinding(
            schema_version="2",
            cards_sha256="a" * 64,
            card_count=0,
            runtime_release="2024",
            member_count=1,
        )
    with pytest.raises(ValidationError, match="member sidecar digest"):
        ApiCatalogManifest(
            schema_version="2",
            built_at=datetime.now(UTC),
            runtime_release="2024",
            card_count=0,
            conflict_count=0,
            cards_sha256="a" * 64,
        )


def test_cli_member_opt_in(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = _wheel(tmp_path)
    root = tmp_path / "cli-catalog"
    assert (
        main(
            [
                "build",
                "--runtime-release",
                "2024",
                "--output-dir",
                str(root),
                "--abqpy-source",
                str(path),
                "--abqpy-version",
                "2024.10",
                "--abqpy-member-owner",
                "OdbAssembly",
            ]
        )
        == 0
    )
    capsys.readouterr()
    assert main(["lookup", "--catalog-dir", str(root), "OdbAssembly.nodeSets"]) == 0
    assert (
        json.loads(capsys.readouterr().out)["members"][0]["declaring_name"]
        == "Base.nodeSets"
    )


def test_members_traverse_real_coder_tool_budget_and_trace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = _wheel(tmp_path)
    catalog = ApiCatalog.build(
        runtime_release="2024",
        members=_members(path),
        abqpy_records=load_abqpy_records(path, version="2024.10", release="2024"),
    )
    root = tmp_path / "catalog"
    catalog.write(root)
    catalog = ApiCatalog.open(root)
    workspace, state = tmp_path / "workspace", tmp_path / "state"
    workspace.mkdir()
    state.mkdir()
    request = CodingRunRequest(
        run_id="member-tool-test",
        goal="Inspect API evidence only.",
        workspace=workspace.resolve(),
        state_dir=state.resolve(),
        runtime=RuntimeProfile(launcher_path=(tmp_path / "unused.bat").resolve()),
        api_catalog=_api_catalog_binding(catalog),
        budget=CodingRunBudget(max_api_lookup_calls=2),
    )
    queries = [
        "OdbAssembly.nodeSets",
        "Base.nodeSets",
        "nodeSets",
        "OdbAssembly.unknown",
        "FieldOutput.getSubset.region",
        "OdbInstance.nodeSets",
        "OdbAssembly.nodeSets",
    ]
    observed: list[dict[str, object]] = []
    turn = 0

    def model(messages, info):
        nonlocal turn
        tools = {tool.name for tool in info.function_tools}
        assert "lookup_abaqus_api" in tools
        assert "run_command" not in tools
        if turn < len(queries):
            name = queries[turn]
            turn += 1
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        "lookup_abaqus_api",
                        {"name": name},
                        tool_call_id=f"member-{turn}",
                    )
                ]
            )
        for message in messages:
            if isinstance(message, ModelRequest):
                for part in message.parts:
                    if (
                        isinstance(part, ToolReturnPart)
                        and part.tool_name == "lookup_abaqus_api"
                    ):
                        observed.append(part.content)
        output = next(
            tool for tool in info.output_tools if tool.name.endswith("FailedDecision")
        )
        return ModelResponse(
            parts=[
                ToolCallPart(
                    output.name,
                    {
                        "status": "failed",
                        "failure_stage": "test",
                        "summary": "Read-only evidence inspected.",
                    },
                )
            ]
        )

    deps = CodingAgentDependencies(
        request=request, runner=Abaqus2024Runner(workspace, state), api_catalog=catalog
    )
    agent = create_abaqus_coding_agent(
        FunctionModel(model), request, api_catalog_mode="full"
    )
    asyncio.run(agent.run(request.goal, deps=deps))
    assert [item["status"] for item in observed] == [
        "found",
        "duplicate",
        "ambiguous",
        "not_found",
        "found",
        "unavailable",
        "duplicate",
    ]
    payload = observed[0]["members"][0]
    assert payload["source"]["version"] == "2024.10"
    assert payload["evidence_kind"] == "abqpy_static_declaration"
    assert "not licensed runtime verification" in payload["evidence_limit"]
    assert observed[4]["parameter"]["abqpy_sources"][0]["version"] == "2024.10"
    assert "does not establish" in observed[3]["coverage_note"]
    assert deps.api_lookup_calls == 2
    assert deps.abaqus_calls == 0
    assert deps.solver_calls == 0
    traces = [
        ApiLookupTrace.model_validate_json(line)
        for line in (state / "api_lookups.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    assert [trace.counted for trace in traces] == [
        True,
        False,
        False,
        False,
        True,
        False,
        False,
    ]
    assert traces[0].card_ids == (catalog.lookup(queries[0]).members[0].card_id,)
    assert not list(workspace.iterdir())
    snapshot = DependencyResumeState.model_validate_json(
        DependencyResumeState.capture(deps).model_dump_json()
    )
    fresh = CodingAgentDependencies(
        request=request, runner=Abaqus2024Runner(workspace, state), api_catalog=catalog
    )
    snapshot.restore_into(fresh)
    assert fresh.api_lookup_calls == 2
    assert fresh.returned_api_card_ids == deps.returned_api_card_ids
    assert fresh.all_api_cards_returned(traces[0].card_ids)

    monkeypatch.setattr(
        "abaqus_sim_agent.coding.api_lookup.resolve_odb_runtime_behavior_query",
        lambda _stderr: "OdbAssembly.nodeSets",
    )
    execution = AbaqusExecutionResult(
        invocation=AbaqusInvocation.ABAQUS_PYTHON,
        command=("unused",),
        workspace=workspace,
        exit_code=1,
        timed_out=False,
        duration_seconds=0.0,
        stdout="",
        stderr="member fixture",
        stdout_encoding="utf-8",
        stderr_encoding="utf-8",
        stdout_raw_path=state / "stdout",
        stderr_raw_path=state / "stderr",
    )
    for mode in ("full", "automatic_verified_only", "automatic_exact_signature"):
        assert runtime_api_hint(fresh, execution, mode=mode) is None
    assert fresh.api_lookup_calls == 2
    assert (
        len((state / "api_lookups.jsonl").read_text(encoding="utf-8").splitlines()) == 7
    )
