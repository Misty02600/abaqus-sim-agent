import ast
import hashlib
import json

import pytest
from pydantic import ValidationError
from pydantic_ai.messages import ModelResponse, ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import FunctionModel

from abaqus_sim_agent.api_catalog import AbqpyApiRecord, ApiCatalog, load_abqpy_records
from abaqus_sim_agent.api_catalog.docstrings import extract_docstring
from abaqus_sim_agent.coding.agent import create_abaqus_coding_agent
from abaqus_sim_agent.coding.contracts import CodingRunRequest, RuntimeProfile
from abaqus_sim_agent.coding.dependencies import CodingAgentDependencies
from abaqus_sim_agent.execution import Abaqus2024Runner

SOURCE = '''raise RuntimeError("must never execute")
from typing import overload
class Part:
    @overload
    def select(self, x: int):
        """Choose by number.

        Parameters
        ----------
        x
            中文说明 with α and Unicode.

        Returns
        -------
        A selected object.
        """
    @overload
    def select(self, x: str):
        """Choose by name.

        Parameters
        ----------
        x : str
            Name of the selected object.
        """
    def select(self, x): ...
'''


def catalog_fixture(tmp_path):
    package = tmp_path / "abaqus"
    package.mkdir()
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "Part.py").write_text(SOURCE, encoding="utf-8")
    records = load_abqpy_records(package, version="2024.test", release="2024")
    return ApiCatalog.build(runtime_release="2024", abqpy_records=records), records[0]


def test_docstrings_keep_variants_raw_source_and_old_record_semantics(tmp_path):
    catalog, record = catalog_fixture(tmp_path)
    docs = record.documentation
    assert docs is not None
    assert len(docs) == len(record.signatures) == 3
    assert docs[0].parameters["x"] == "中文说明 with α and Unicode."
    assert docs[1].parameters["x"] == "Name of the selected object."
    assert docs[2].parse_status == "absent"
    assert docs[2].raw is None
    assert "Returns" in docs[0].raw
    assert docs[0].docstring_sha256 == hashlib.sha256(docs[0].raw.encode()).hexdigest()
    assert docs[0].source_line < docs[1].source_line
    raw = record.model_dump(mode="json")
    raw.pop("documentation")
    old = AbqpyApiRecord.model_validate_json(json.dumps(raw))
    assert old.documentation is None
    assert "documentation" not in old.model_dump(mode="json")
    raw["documentation"] = [docs[0].model_dump(mode="json")]
    with pytest.raises(ValidationError, match="one-to-one"):
        AbqpyApiRecord.model_validate_json(json.dumps(raw))
    directory = tmp_path / "catalog"
    catalog.write(directory)
    loaded = ApiCatalog.open(directory)
    result = loaded.lookup("Part.select.__doc__")
    assert result.documentation_requested
    assert result.cards[0].abqpy_records[0].documentation == docs
    with pytest.raises(ValueError, match="not a member or parameter"):
        loaded.lookup("Part.select.x.__doc__")


def test_unknown_docstring_format_is_preserved_not_falsely_parsed():
    source = (
        'def f(x):\n    """Summary.\n\n    Args:\n        x: description\n    """\n'
    )
    node = ast.parse(source).body[0]
    doc = extract_docstring(node, "0" * 64)
    assert doc.parse_status == "summary_only"
    assert doc.parameters == {}
    assert "Args:" in doc.raw
    with pytest.raises(ValidationError, match="checksum"):
        type(doc).model_validate({**doc.model_dump(), "raw": "changed"})


def test_empty_parameter_descriptions_are_partial_not_invented():
    node = ast.parse(
        'def f(x):\n    """Summary.\n\n    Parameters\n    ----------\n    x : int\n    """\n'
    ).body[0]
    doc = extract_docstring(node, "0" * 64)
    assert doc.parse_status == "partial"
    assert doc.parameters == {}
    assert "x : int" in doc.raw


@pytest.mark.asyncio
async def test_existing_lookup_can_read_summary_raw_and_parameter_without_duplicate_block(
    tmp_path,
):
    catalog, _record = catalog_fixture(tmp_path)
    workspace, state = tmp_path / "workspace", tmp_path / "state"
    workspace.mkdir()
    state.mkdir()
    request = CodingRunRequest(
        run_id="docstrings",
        goal="Read API docs",
        workspace=workspace,
        state_dir=state,
        runtime=RuntimeProfile(launcher_path=(tmp_path / "fake.bat").resolve()),
    )
    calls = 0
    returned = []

    def reply(messages, info):
        nonlocal calls
        if calls:
            returned.extend(
                p.content for p in messages[-1].parts if isinstance(p, ToolReturnPart)
            )
        names = ("Part.select", "Part.select.__doc__", "Part.select.x")
        if calls < 3:
            response = ModelResponse(
                parts=[
                    ToolCallPart(
                        "lookup_abaqus_api",
                        {"name": names[calls]},
                        tool_call_id=str(calls),
                    )
                ]
            )
        else:
            response = ModelResponse(
                parts=[
                    ToolCallPart(
                        "final_result_FailedDecision",
                        {
                            "status": "failed",
                            "summary": "fixture",
                            "failure_stage": "modeling",
                        },
                    )
                ]
            )
        calls += 1
        return response

    agent = create_abaqus_coding_agent(
        FunctionModel(reply),
        request,
        api_catalog_mode="full",
        question_protocol="ask_user_v1",
    )
    deps = CodingAgentDependencies(
        request=request, runner=Abaqus2024Runner(workspace, state), api_catalog=catalog
    )
    await agent.run("fixture", deps=deps)
    assert deps.api_lookup_calls == 3
    assert (
        returned[0]["cards"][0]["abqpy_documentation"][0]["summary"]
        == "Choose by number."
    )
    assert "Returns" in returned[1]["documentation"][0]["raw"]
    assert (
        returned[2]["parameter"]["abqpy_descriptions"][1]["description"]
        == "Name of the selected object."
    )
