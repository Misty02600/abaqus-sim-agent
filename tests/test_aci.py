import json
from pathlib import Path

import pytest

from abaqus_sim_agent.coding.aci import RuntimePythonSyntaxGate


def _gate(tmp_path: Path) -> RuntimePythonSyntaxGate:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    return RuntimePythonSyntaxGate(workspace, tmp_path / "state")


@pytest.mark.parametrize("source", ["def broken(:\n    pass\n", "type Alias = int\n"])
def test_runtime_syntax_denies_invalid_python_310(tmp_path: Path, source: str) -> None:
    gate = _gate(tmp_path)
    path = gate.workspace / "build_model.py"
    path.write_text(source, encoding="utf-8")
    result = gate.check_runtime(path.name)
    assert result.status == "denied"
    assert result.diagnostic is not None
    assert result.diagnostic.line == 1
    assert path.read_text(encoding="utf-8") == source
    assert gate.denials == 1
    record = json.loads((gate.state_dir / "syntax_checks.jsonl").read_text())
    assert record["script_sha256"] == result.script_sha256


def test_runtime_syntax_cache_tracks_actual_bytes(tmp_path: Path) -> None:
    gate = _gate(tmp_path)
    path = gate.workspace / "any_name.py"
    path.write_text("from abaqus import mdb\n", encoding="utf-8")
    assert gate.check_runtime(path.name).cache_hit is False
    assert gate.check_runtime(path.name).cache_hit is True
    path.write_text("value = (\n", encoding="utf-8")
    result = gate.check_runtime(path.name)
    assert result.status == "denied"
    assert result.cache_hit is False
    path.write_text("value = 1\n", encoding="utf-8")
    assert gate.check_runtime(path.name).status == "clean"


def test_runtime_syntax_respects_encoding_and_does_not_execute(tmp_path: Path) -> None:
    gate = _gate(tmp_path)
    source = "# coding: cp936\n# 注释\nraise RuntimeError('must not execute')\n"
    (gate.workspace / "extract.py").write_bytes(source.encode("cp936"))
    assert gate.check_runtime("extract.py").status == "clean"
    (gate.workspace / "undefined.py").write_text(
        "value = missing_name\n", encoding="utf-8"
    )
    assert gate.check_runtime("undefined.py").status == "clean"


@pytest.mark.parametrize("name", ["missing.py", "../outside.py", "input.inp"])
def test_runtime_syntax_rejects_unreadable_or_outside_scripts(
    tmp_path: Path, name: str
) -> None:
    gate = _gate(tmp_path)
    (tmp_path / "outside.py").write_text("pass\n", encoding="utf-8")
    (gate.workspace / "input.inp").write_text("pass\n", encoding="utf-8")
    assert gate.check_runtime(name).status == "unavailable"
