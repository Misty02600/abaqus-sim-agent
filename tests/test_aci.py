import json
from pathlib import Path

import pytest
from pydantic_ai_harness.guardrails import ToolCallInfo

from abaqus_sim_agent.coding.aci import (
    PYTHON_SYNTAX_GATE_VERSION,
    ProductionPythonSyntaxGate,
)
from abaqus_sim_agent.coding.contracts import RuntimeScriptPolicy


def _gate(tmp_path: Path) -> ProductionPythonSyntaxGate:
    workspace = tmp_path / "workspace"
    state_dir = tmp_path / "state"
    workspace.mkdir()
    state_dir.mkdir()
    return ProductionPythonSyntaxGate(
        workspace=workspace,
        state_dir=state_dir,
        runtime_script_policy=RuntimeScriptPolicy(
            allowed_cae_scripts=("build_model.py",),
            allowed_abaqus_python_scripts=("extract_results.py",),
            allowed_solver_inputs=("beam_tension.inp",),
        ),
    )


def _write_call(path: str, content: str) -> ToolCallInfo:
    return ToolCallInfo(
        name="write_file",
        args={"path": path, "content": content},
        tool_call_id="write-1",
    )


def test_syntax_gate_blocks_invalid_production_write_before_file_change(
    tmp_path: Path,
) -> None:
    gate = _gate(tmp_path)

    verdict = gate.guard(_write_call("build_model.py", "def broken(:\n    pass\n"))

    assert verdict.action == "block"
    assert isinstance(verdict.message, str)
    payload = json.loads(verdict.message)
    assert payload["error_type"] == "PythonSyntaxEditDenied"
    assert payload["syntax_gate_version"] == PYTHON_SYNTAX_GATE_VERSION
    assert payload["python_version"] == "3.10"
    assert payload["edit_applied"] is False
    assert payload["budget_consumed"] is False
    assert not (gate.workspace / "build_model.py").exists()
    assert gate.edit_checks == 1
    assert gate.denials == 1
    trace = (gate.state_dir / "syntax_checks.jsonl").read_text(encoding="utf-8")
    assert '"status": "denied"' in trace


def test_syntax_gate_uses_python_310_grammar(tmp_path: Path) -> None:
    gate = _gate(tmp_path)

    verdict = gate.guard(_write_call("build_model.py", "type Alias = int\n"))

    assert verdict.action == "block"
    payload = json.loads(str(verdict.message))
    assert payload["diagnostic"]["line"] == 1


def test_syntax_gate_binds_valid_edit_to_runtime_hash(tmp_path: Path) -> None:
    gate = _gate(tmp_path)
    content = "from abaqus import mdb\nmodel = mdb.models['Model-1']\n"

    verdict = gate.guard(_write_call("build_model.py", content))
    (gate.workspace / "build_model.py").write_text(content, encoding="utf-8")
    runtime = gate.check_runtime("build_model.py")

    assert verdict.action == "allow"
    assert runtime.status == "clean"
    assert runtime.cache_hit is True
    assert runtime.script_sha256 is not None
    assert gate.edit_checks == 1
    assert gate.runtime_checks == 1
    assert gate.denials == 0


def test_syntax_gate_rechecks_out_of_band_runtime_change(tmp_path: Path) -> None:
    gate = _gate(tmp_path)
    valid = "value = 1\n"
    assert gate.guard(_write_call("build_model.py", valid)).action == "allow"
    (gate.workspace / "build_model.py").write_text(
        "if True print('broken')\n",
        encoding="utf-8",
    )

    runtime = gate.check_runtime("build_model.py")

    assert runtime.status == "denied"
    assert runtime.cache_hit is False
    assert runtime.diagnostic is not None
    assert gate.denials == 1


def test_syntax_gate_rejects_invalid_edit_without_changing_old_file(
    tmp_path: Path,
) -> None:
    gate = _gate(tmp_path)
    path = gate.workspace / "build_model.py"
    original = "value = 1\n"
    path.write_text(original, encoding="utf-8")
    call = ToolCallInfo(
        name="edit_file",
        args={
            "path": "build_model.py",
            "old_text": "value = 1",
            "new_text": "value = (",
        },
        tool_call_id="edit-1",
    )

    verdict = gate.guard(call)

    assert verdict.action == "block"
    assert path.read_text(encoding="utf-8") == original


def test_syntax_gate_does_not_claim_nonproduction_files(tmp_path: Path) -> None:
    gate = _gate(tmp_path)

    verdict = gate.guard(_write_call("notes.py", "def broken(:\n"))

    assert verdict.action == "allow"
    assert gate.checks == 0


def test_syntax_gate_cannot_be_bypassed_with_relative_path_alias(
    tmp_path: Path,
) -> None:
    gate = _gate(tmp_path)

    verdict = gate.guard(_write_call("staging/../build_model.py", "def broken(:\n"))

    assert verdict.action == "block"
    assert gate.denials == 1


def test_syntax_gate_requires_explicit_production_allowlists(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    with pytest.raises(ValueError, match="explicit CAE"):
        ProductionPythonSyntaxGate(
            workspace=workspace,
            state_dir=tmp_path / "state",
            runtime_script_policy=RuntimeScriptPolicy(),
        )
