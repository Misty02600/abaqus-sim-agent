import json
from pathlib import Path

import pytest

from abaqus_sim_agent.skills.import_official_structural_texts import (
    DEFAULT_TEXT_SOURCES,
    build_official_text_source_inventory,
    export_official_text_source_inventory,
)


def _write_fake_page(path: Path, title: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "<html><head>"
        f"<title>{title}</title>"
        f"<meta name='description' content='{title} description'>"
        "</head><body>"
        f"<h1>{title}</h1><h2>First section</h2><h3>Detail</h3>"
        "</body></html>",
        encoding="utf-8",
    )


def test_build_official_text_source_inventory_preserves_curated_provenance(
    tmp_path: Path,
) -> None:
    docs_root = tmp_path / "docs"
    for index, spec in enumerate(DEFAULT_TEXT_SOURCES):
        _write_fake_page(
            docs_root / spec.source_map / spec.page_name,
            f"Fixture {index}",
        )

    inventory = build_official_text_source_inventory(
        docs_root,
        source_release="2026 FD02 test fixture",
    )

    assert inventory["page_count"] == len(DEFAULT_TEXT_SOURCES)
    assert inventory["topic_counts"] == {
        "contact": 4,
        "explicit-dynamics": 4,
        "geometric-nonlinearity": 9,
        "hyperelasticity": 1,
        "interface-cohesive": 4,
        "linear-structural-foundation": 5,
        "plasticity": 2,
        "structural-dynamics": 4,
    }
    pages = inventory["pages"]
    assert isinstance(pages, list)
    first = pages[0]
    assert first["source_release"] == "2026 FD02 test fixture"
    assert first["title"] == "Fixture 0"
    assert first["description"] == "Fixture 0 description"
    assert first["section_headings"] == ["Fixture 0", "First section", "Detail"]
    assert len(first["sha256"]) == 64

    finite_strain_pages = {
        page["page"]: page
        for page in pages
        if "finite-strain-output" in page["cross_topics"]
    }
    assert set(finite_strain_pages) == {
        "simamod-c-conventions.htm",
        "simaout-c-stdoutputvar.htm",
        "simathe-c-strainmeas.htm",
        "simathe-c-stressmeas.htm",
    }
    assert (
        finite_strain_pages["simamod-c-conventions.htm"]["recommended_role"] == "core"
    )
    assert (
        finite_strain_pages["simathe-c-strainmeas.htm"]["recommended_role"] == "theory"
    )


def test_export_official_text_source_inventory_writes_metadata_only(
    tmp_path: Path,
) -> None:
    docs_root = tmp_path / "docs"
    for index, spec in enumerate(DEFAULT_TEXT_SOURCES):
        _write_fake_page(
            docs_root / spec.source_map / spec.page_name,
            f"Fixture {index}",
        )

    output = tmp_path / "inventory"
    export_official_text_source_inventory(
        docs_root,
        output,
        source_release="2026 FD02 test fixture",
    )

    persisted = json.loads((output / "inventory.json").read_text(encoding="utf-8"))
    manifest = json.loads((output / "source.json").read_text(encoding="utf-8"))
    assert persisted["page_count"] == len(DEFAULT_TEXT_SOURCES)
    assert manifest["vendor_pages_copied"] is False
    assert manifest["abaqus_executed"] is False
    assert {path.name for path in output.iterdir()} == {
        "README.md",
        "inventory.json",
        "source.json",
    }


def test_export_official_text_source_inventory_rejects_overwrite(
    tmp_path: Path,
) -> None:
    output = tmp_path / "inventory"
    output.mkdir()

    with pytest.raises(FileExistsError):
        export_official_text_source_inventory(
            tmp_path / "docs",
            output,
            source_release="fixture",
        )
