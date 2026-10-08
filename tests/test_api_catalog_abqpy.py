from __future__ import annotations

import builtins
import subprocess
import sys
import zipfile
from collections.abc import Mapping, Sequence
from pathlib import Path

import pytest

from abaqus_sim_agent.api_catalog.abqpy import (
    AbqpyExtractionError,
    load_abqpy_records,
)
from abaqus_sim_agent.api_catalog.catalog import ApiCatalog

_FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "api_catalog" / "abqpy" / "source"
_FIXTURE_SRC = _FIXTURE_ROOT / "src"


def test_loads_overloads_signatures_and_bound_parameters() -> None:
    records = load_abqpy_records(
        _FIXTURE_ROOT,
        version="2024.test",
        release="2024",
    )
    by_name = {record.declaring_name: record for record in records}

    seed = by_name["MeshPart.seedEdgeByNumber"]
    assert seed.record_id == (
        "abqpy:2024.test:abaqus.Mesh.MeshPart.MeshPart.seedEdgeByNumber"
    )
    assert seed.entity_kind == "method"
    assert seed.public_names == (
        "MeshPart.seedEdgeByNumber",
        "Part.seedEdgeByNumber",
    )
    assert seed.aliases == (
        "abaqus.Mesh.MeshPart.MeshPart.seedEdgeByNumber",
        "abaqus.Part.Part.Part.seedEdgeByNumber",
    )
    assert [signature.overload for signature in seed.signatures] == [
        True,
        True,
        False,
    ]
    assert seed.signatures[0].text == (
        "seedEdgeByNumber(edges: Sequence[Edge], number: int, "
        "constraint: SymbolicConstant = FREE) -> None"
    )
    assert [parameter.name for parameter in seed.signatures[0].parameters] == [
        "edges",
        "number",
        "constraint",
    ]
    assert seed.signatures[0].parameters[0].required
    assert seed.signatures[0].parameters[2].default == "FREE"
    assert not seed.signatures[0].parameters[2].required
    assert seed.signatures[1].text == (
        "seedEdgeByNumber(edges: EdgeArray, *, number: int) -> None"
    )
    assert seed.source.kind == "abqpy"
    assert seed.source.release == "2024"
    assert seed.source.version == "2024.test"
    assert seed.source.source_path == "abaqus/Mesh/MeshPart.py"

    constructor = by_name["MeshPart.__init__"]
    assert constructor.entity_kind == "constructor"
    assert constructor.public_names == ("MeshPart.__init__",)
    assert constructor.signatures[0].text == (
        "__init__(name: str, /, *, enabled: bool = True) -> None"
    )
    assert [parameter.kind for parameter in constructor.signatures[0].parameters] == [
        "positional_only",
        "keyword_only",
    ]

    command = by_name["abaqus.Mesh.MeshPart.upgradeScript"]
    assert command.entity_kind == "command"
    assert command.class_name == "abaqus.Mesh.MeshPart"
    assert command.signatures[0].text == (
        "upgradeScript(fileNames: str, /, *searchPaths: str, "
        "preview: bool = True, **options: object) -> int"
    )
    assert [parameter.required for parameter in command.signatures[0].parameters] == [
        True,
        False,
        False,
        False,
    ]


def test_resolves_only_related_inheritance_receivers() -> None:
    records = load_abqpy_records(
        _FIXTURE_SRC,
        version="2024.test",
        release="2024",
    )
    by_name = {record.declaring_name: record for record in records}

    seed = by_name["MeshPart.seedEdgeByNumber"]
    assert [node.class_name for node in seed.class_graph] == ["MeshPart", "Part"]
    assert seed.bases == ("abaqus.Mesh.MeshPart.MeshBase",)
    assert "abaqus.Mesh.MeshPart.MeshPart" in seed.class_graph[1].bases

    displacement = by_name["BoundaryConditionModel.DisplacementBC"]
    assert displacement.public_names == (
        "BoundaryConditionModel.DisplacementBC",
        "Model.DisplacementBC",
    )
    assert [node.class_name for node in displacement.class_graph] == [
        "BoundaryConditionModel",
        "Model",
    ]

    assembly_set = by_name["RegionAssembly.Set"]
    assert assembly_set.public_names == (
        "RegionAssembly.Set",
        "Assembly.Set",
    )
    assert [node.class_name for node in assembly_set.class_graph] == [
        "RegionAssembly",
        "Assembly",
    ]

    unrelated = {node.class_name for node in seed.class_graph}
    assert "Model" not in unrelated
    assert "Assembly" not in unrelated


@pytest.mark.parametrize(
    ("archive_name", "prefix"),
    [
        ("abqpy-2024.test-py3-none-any.whl", ""),
        ("abqpy-2024.test.zip", "abqpy-2024.test/src"),
    ],
)
def test_reads_wheel_and_source_zip_without_importing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    archive_name: str,
    prefix: str,
) -> None:
    archive = tmp_path / archive_name
    with zipfile.ZipFile(archive, "w") as distribution:
        for source_file in sorted((_FIXTURE_SRC / "abaqus").rglob("*.py")):
            relative = source_file.relative_to(_FIXTURE_SRC).as_posix()
            member = f"{prefix}/{relative}" if prefix else relative
            distribution.write(source_file, member)

    original_import = builtins.__import__

    def guarded_import(
        name: str,
        globals: Mapping[str, object] | None = None,
        locals: Mapping[str, object] | None = None,
        fromlist: Sequence[str] | None = (),
        level: int = 0,
    ):
        if name == "abaqus" or name.startswith("abaqus."):
            raise AssertionError(f"extractor attempted to import {name}")
        return original_import(name, globals, locals, fromlist, level)

    assert "abaqus" not in sys.modules
    assert not hasattr(builtins, "ABQPY_AST_FIXTURE_EXECUTED")
    monkeypatch.setattr(builtins, "__import__", guarded_import)

    records = load_abqpy_records(
        archive,
        version="2024.test",
        release="2024",
    )

    assert any("Part.seedEdgeByNumber" in record.public_names for record in records)
    assert "abaqus" not in sys.modules
    assert not hasattr(builtins, "ABQPY_AST_FIXTURE_EXECUTED")


def test_rejects_a_source_without_an_abaqus_package(tmp_path: Path) -> None:
    with pytest.raises(AbqpyExtractionError, match="locate an abaqus package"):
        load_abqpy_records(
            tmp_path,
            version="2024.test",
            release="2024",
        )


def test_catalog_command_accepts_abqpy_source_without_intermediate_jsonl(
    tmp_path: Path,
) -> None:
    output_dir = tmp_path / "catalog"

    subprocess.run(
        [
            str(
                Path(sys.executable).with_name(
                    "abaqus-sim-agent-catalog.exe"
                    if sys.platform == "win32"
                    else "abaqus-sim-agent-catalog"
                )
            ),
            "--runtime-release",
            "2024",
            "--output-dir",
            str(output_dir),
            "--abqpy-source",
            str(_FIXTURE_ROOT),
            "--abqpy-version",
            "2024.test",
        ],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
    )

    catalog = ApiCatalog.open(output_dir)
    assert catalog.lookup("Part.seedEdgeByNumber").status == "found"
    assert catalog.manifest is not None
    assert set(catalog.manifest.source_digests) == {"abqpy_source"}
