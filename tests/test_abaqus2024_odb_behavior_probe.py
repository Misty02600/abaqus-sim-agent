from __future__ import annotations

import asyncio
import json
import os
import shutil
from pathlib import Path

import pytest

from abaqus_sim_agent.execution import Abaqus2024Runner, AbaqusExecutionResult

_LICENSED_TESTS_ENABLED = os.environ.get("ABAQUS_LICENSED_TESTS") == "1"
_LAUNCHER = Path(r"D:\SIMULIA\Commands\abq2024.bat")
_FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "abaqus2024"

pytestmark = [
    pytest.mark.abaqus,
    pytest.mark.skipif(
        not _LICENSED_TESTS_ENABLED,
        reason="set ABAQUS_LICENSED_TESTS=1 to use the local Abaqus license",
    ),
]


def test_abaqus2024_odb_behavior_probe_completes(tmp_path: Path) -> None:
    if not _LAUNCHER.is_file():
        pytest.skip(f"Abaqus 2024 launcher is not installed at {_LAUNCHER}")

    workspace = (tmp_path / "workspace").resolve()
    state_dir = (tmp_path / "state").resolve()
    workspace.mkdir()
    shutil.copyfile(
        _FIXTURE_ROOT / "beam_bending_change" / "build_model.py",
        workspace / "build_model.py",
    )
    shutil.copyfile(
        _FIXTURE_ROOT / "odb_behavior_probe.py",
        workspace / "odb_behavior_probe.py",
    )
    runner = Abaqus2024Runner(
        workspace,
        state_dir,
        launcher=_LAUNCHER.resolve(strict=True),
    )

    cae_result = asyncio.run(
        runner.run_cae_script("build_model.py", timeout_seconds=600.0)
    )
    _assert_execution("CAE build", cae_result)
    solver_result = asyncio.run(
        runner.run_solver_job(
            "beam_tension",
            input_file="beam_tension.inp",
            timeout_seconds=1_800.0,
        )
    )
    _assert_execution("Solver", solver_result)
    probe_result = asyncio.run(
        runner.run_abaqus_python(
            "odb_behavior_probe.py",
            args=("beam_tension.odb", "odb_behavior_probe_results.json"),
            timeout_seconds=120.0,
        )
    )
    _assert_execution("ODB behavior probe", probe_result)

    output_path = workspace / "odb_behavior_probe_results.json"
    assert output_path.is_file()
    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert payload["schema_version"] == "1"
    assert payload["completed"] is True
    assert payload["field_keys"] == ["RF", "RM", "SF", "SM", "U", "UR"]
    assert not payload["instance_nodes_has_findAt"]
    assert payload["instance_nodes_public_methods"] == []
    assert payload["instance_nodes"]["first_label"] == {
        "status": "ok",
        "value": 1,
    }
    assert payload["instance_nodes"]["slice_one"] == {
        "status": "ok",
        "value": 1,
    }
    assert payload["u_subset"] == {
        "component_labels": ["U1", "U2", "U3"],
        "node_label": 1,
        "value_count": 1,
    }

    for field in payload["fields"].values():
        assert field["type_name"] == "FieldOutput"
        assert field["field_type_name"] == "SymbolicConstant"
        assert field["field_type_json"]["status"] == "error"
        assert field["field_type_json"]["error_type"] == "TypeError"
        assert field["has_position"] is False
        assert field["first_value_type"] == "FieldValue"
        assert field["first_value_has_component_labels"] is False
        assert field["values"]["type_name"] == "FieldValueArray"
        assert field["values"]["slice_one"]["status"] == "error"
        assert field["values"]["slice_one"]["error_type"] == "OdbError"
        assert field["raw_scalar_type"] == "float32"
        assert field["raw_scalar_json"]["status"] == "error"
        assert field["raw_scalar_json"]["error_type"] == "TypeError"
        assert field["float_scalar_json"]["status"] == "ok"

    instance_set = payload["instance_node_sets"]["BEAM_EDGES"]
    assert instance_set["nodes"]["type_name"] == "OdbMeshNodeArray"
    assert instance_set["nodes"]["first_has_label"] == {
        "status": "ok",
        "value": True,
    }
    for set_object in payload["assembly_node_sets"].values():
        assert set_object["nodes"]["type_name"] == "tuple"
        assert set_object["nodes"]["first_type"] == {
            "status": "ok",
            "value": "OdbMeshNodeArray",
        }
        assert set_object["first_nested_label"]["status"] == "ok"


def _assert_execution(stage: str, result: AbaqusExecutionResult) -> None:
    if not result.succeeded:
        pytest.fail(
            f"{stage} failed despite exit_code={result.exit_code} and "
            f"timed_out={result.timed_out}.\n"
            f"stdout:\n{result.stdout[-4000:]}\n"
            f"stderr:\n{result.stderr[-4000:]}",
            pytrace=False,
        )
