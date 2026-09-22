import json
from pathlib import Path
from typing import cast

import pytest

from abaqus_sim_agent.skills.import_official_structural_examples import (
    DEFAULT_EXAMPLES,
    build_linked_example_inventory,
    export_structural_example_pool,
)


def _write_fake_page(path: Path, script_names: list[str]) -> None:
    blocks = "".join(
        f"<h2>{script_name}</h2>"
        "<pre class='codeblock'>from abaqus import mdb\nvalue = 1\n</pre>"
        for script_name in script_names
    )
    path.write_text(
        f"<html><body>{blocks}</body></html>",
        encoding="utf-8",
    )


def _write_fake_linked_page(path: Path, script_names: list[str]) -> None:
    links = "".join(
        f"<a href='../SIMAINPRefResources/{script_name}'>{script_name}</a>"
        for script_name in script_names
    )
    path.write_text(
        "<html><head><title>Fixture title</title>"
        "<meta name='description' content='Fixture description'></head>"
        f"<body>{links}</body></html>",
        encoding="utf-8",
    )


def test_export_structural_example_pool_preserves_one_canonical_copy_per_script(
    tmp_path: Path,
) -> None:
    docs_root = tmp_path / "docs"
    scripts_by_page: dict[tuple[str, str, str], list[str]] = {}
    for spec in DEFAULT_EXAMPLES:
        key = (spec.source_map, spec.page_name, spec.extraction_kind)
        scripts_by_page.setdefault(key, []).append(spec.script_name)
    for (
        source_map,
        page_name,
        extraction_kind,
    ), script_names in scripts_by_page.items():
        map_root = docs_root / source_map
        map_root.mkdir(parents=True, exist_ok=True)
        if extraction_kind == "embedded-codeblock":
            _write_fake_page(map_root / page_name, script_names)
        else:
            _write_fake_linked_page(map_root / page_name, script_names)
            resource_root = docs_root / "SIMAINPRefResources"
            resource_root.mkdir(exist_ok=True)
            for script_name in script_names:
                (resource_root / script_name).write_text(
                    "from abaqus import mdb\nvalue = 1\n",
                    encoding="utf-8",
                )

    output = tmp_path / "pool"
    manifest = export_structural_example_pool(
        docs_root,
        output,
        source_release="2026 FD02 test fixture",
    )
    manifest_examples = cast(list[dict[str, object]], manifest["examples"])
    target_runtime = cast(str, manifest["target_runtime"])

    assert len(list((output / "scripts").glob("*.py"))) == len(DEFAULT_EXAMPLES)
    assert len(DEFAULT_EXAMPLES) == 22
    assert len(manifest_examples) == len(DEFAULT_EXAMPLES)
    assert manifest["abaqus_executed"] is False
    assert "compatibility not established" in target_runtime
    persisted = json.loads((output / "source.json").read_text(encoding="utf-8"))
    assert persisted == manifest
    inventory = json.loads((output / "inventory.json").read_text(encoding="utf-8"))
    assert inventory["anchor_link_count"] == 7
    assert inventory["reference_count"] == 7
    assert inventory["unique_script_count"] == 7
    assert inventory["all_python310_syntax"] is True
    assert {item["primary_topic"] for item in persisted["examples"]} >= {
        "hyperelasticity",
        "plasticity",
        "geometric-nonlinearity",
        "structural-dynamics",
        "explicit-dynamics",
        "contact",
    }
    by_script = {Path(item["script"]).name: item for item in persisted["examples"]}
    assert by_script["gsi_channel_caemodel.py"]["primary_topic"] == "contact"
    assert "explicit-dynamics" in by_script["gsi_channel_caemodel.py"]["cross_topics"]
    assert by_script["gsi_channel_caemodel_spring.py"]["primary_topic"] == "plasticity"
    assert by_script["gxi_circuit_caemodel.py"]["primary_topic"] == "explicit-dynamics"
    assert (
        by_script["gsi_frame_caemodel.py"]["primary_topic"]
        == "linear-structural-foundation"
    )
    assert (
        by_script["pillar_fastener_std.py"]["teaching_tier"] == "complex-comprehensive"
    )
    assert by_script["alfano_2d_model.py"]["primary_topic"] == "interface-cohesive"
    assert by_script["alfano_2d_model.py"]["extraction_kind"] == "linked-resource"
    assert (
        by_script["alfano_2d_model.py"]["source_page"]
        == "sources/SIMACAEBMKRefMap/simabmk-c-alfanodelamination.htm"
    )


def test_linked_inventory_groups_repeated_resources(tmp_path: Path) -> None:
    docs_root = tmp_path / "docs"
    resource_root = docs_root / "SIMAINPRefResources"
    resource_root.mkdir(parents=True)
    for script_name in ("shared.py", "other.py"):
        (resource_root / script_name).write_text("value = 1\n", encoding="utf-8")

    exa_root = docs_root / "SIMACAEEXARefMap"
    bmk_root = docs_root / "SIMACAEBMKRefMap"
    ver_root = docs_root / "SIMACAEVERRefMap"
    exa_root.mkdir()
    bmk_root.mkdir()
    ver_root.mkdir()
    _write_fake_linked_page(
        exa_root / "exa.htm", ["shared.py", "other.py", "shared.py"]
    )
    _write_fake_linked_page(bmk_root / "bmk.htm", ["shared.py"])

    inventory = build_linked_example_inventory(
        docs_root,
        source_release="2026 FD02 test fixture",
    )

    assert inventory["anchor_link_count"] == 4
    assert inventory["reference_count"] == 3
    assert inventory["unique_script_count"] == 2
    assert inventory["missing_resource_count"] == 0
    assert inventory["invalid_python310_count"] == 0
    scripts = cast(list[dict[str, object]], inventory["scripts"])
    by_name = {cast(str, item["script_name"]): item for item in scripts}
    shared_refs = cast(list[dict[str, object]], by_name["shared.py"]["references"])
    assert len(shared_refs) == 2
    assert {item["source_map"] for item in shared_refs} == {
        "SIMACAEEXARefMap",
        "SIMACAEBMKRefMap",
    }


def test_export_structural_example_pool_rejects_overwrite(tmp_path: Path) -> None:
    docs_root = tmp_path / "docs"
    (docs_root / "SIMACAEGSARefMap").mkdir(parents=True)
    output = tmp_path / "pool"
    output.mkdir()

    with pytest.raises(FileExistsError):
        export_structural_example_pool(
            docs_root,
            output,
            source_release="2026 FD02 test fixture",
        )
