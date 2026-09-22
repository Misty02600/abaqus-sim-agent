import json

import numpy as np
import pytest

from abaqus_sim_agent.api_catalog import (
    ApiCatalog,
    ApiMethodCard,
    ApiSourceRef,
    OfficialApiRecord,
)
from abaqus_sim_agent.api_catalog.cli import main
from abaqus_sim_agent.api_catalog.discovery import CatalogDiscovery


def catalog(tmp_path):
    cards = []
    for owner, name, summary in [
        ("Part", "assign", "Assign element types to geometry mesh regions"),
        ("Assembly", "assign", "Assign assembly reference positions"),
        ("Material", "Elastic", "Create isotropic elastic material properties"),
    ]:
        full = f"{owner}.{name}"
        cards.append(
            ApiMethodCard(
                card_id=full,
                canonical_name=full,
                declaring_name=full,
                entity_kind="method",
                class_name=owner,
                method_name=name,
                official_records=(
                    OfficialApiRecord(
                        record_id=full,
                        entity_kind="method",
                        owner_name=owner,
                        callable_name=name,
                        canonical_name=full,
                        summary=summary,
                        source=ApiSourceRef(
                            kind="official", release="2024", source_path="fixture"
                        ),
                    ),
                ),
            )
        )
    value = ApiCatalog(cards, runtime_release="2024")
    value.write(tmp_path / "catalog")
    return value


class FakeEncoder:
    def __init__(self):
        self.binding = {"fixture": "yes"}

    def encode(self, texts, *, query):
        rows = [
            [1, 0] if "element" in text or "单元" in text else [0, 1] for text in texts
        ]
        return np.asarray(rows, dtype=np.float32)


def test_discovery_exact_ambiguity_candidates_read_and_binding(tmp_path):
    value = catalog(tmp_path)
    folder = tmp_path / "index"
    index = CatalogDiscovery.build(value, folder, encoder=FakeEncoder())
    assert index.search("Part.assign", strategy="dense")["route"] == "exact"
    assert index.search("assign")["status"] == "ambiguous"
    assert index.search("Missing.assign", strategy="dense")["status"] == "not_found"
    candidate = index.search("Part.assign")["candidates"][0]
    assert candidate["sources"][0]["release"] == "2024"
    assert index.search(candidate["card_id"])["status"] == "found"
    result = index.search("How to assign element types to geometry?")
    assert result["candidates"][0]["name"] == "Part.assign"
    assert (
        index.read(result["candidates"][0]["card_id"]).canonical_name == "Part.assign"
    )
    mixed = index.search("Part.assign 为什么出错")
    assert mixed["route"] == "bm25"
    assert mixed["protected_symbols"] == ["Part.assign"]
    assert mixed["original_query"] == "Part.assign 为什么出错"
    assert (
        index.search("单元类型", strategy="dense", encoder=FakeEncoder())["candidates"][
            0
        ]["name"]
        == "Part.assign"
    )
    assert index.search("zzzzzzzz")["status"] == "no_candidates"
    with pytest.raises(ValueError, match="unknown"):
        index.read("../../secrets")
    with pytest.raises(ValueError, match="nonblank"):
        index.search(" ")
    with pytest.raises(ValueError, match="matching encoder"):
        index.search("material", strategy="dense")
    (folder / "vectors.npy").write_bytes(b"broken")
    with pytest.raises(ValueError, match="checksum"):
        CatalogDiscovery.open(value, folder)


def test_changed_catalog_or_view_is_rejected(tmp_path):
    value = catalog(tmp_path)
    folder = tmp_path / "index"
    index = CatalogDiscovery.build(value, folder)
    other = ApiCatalog(
        [value.cards[0].model_copy(update={"canonical_name": "Changed"})],
        runtime_release="2024",
    )
    other.write(tmp_path / "other")
    with pytest.raises(ValueError, match="binding"):
        CatalogDiscovery.open(other, folder)
    (folder / "views.jsonl").write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="checksum"):
        CatalogDiscovery.open(value, folder)
    assert index.vectors is None


def test_method_directory_uses_public_receiver_names_and_keeps_callable_factories():
    value = ApiCatalog(
        [
            ApiMethodCard(
                card_id="seed-part",
                entity_kind="method",
                canonical_name="MeshPart.seedPart",
                declaring_name="MeshPart.seedPart",
                class_name="MeshPart",
                method_name="seedPart",
                public_names=("Part.seedPart",),
            ),
            ApiMethodCard(
                card_id="set",
                entity_kind="constructor",
                canonical_name="Assembly.Set",
                declaring_name="AssemblyBase.Set",
                class_name="AssemblyBase",
                method_name="Set",
                public_names=("Assembly.Set",),
            ),
            ApiMethodCard(
                card_id="bounding-box",
                entity_kind="method",
                canonical_name="MeshNodeArray.getByBoundingBox",
                declaring_name="MeshNodeArray.getByBoundingBox",
                class_name="MeshNodeArray",
                method_name="getByBoundingBox",
            ),
            ApiMethodCard(
                card_id="factory",
                entity_kind="constructor",
                canonical_name="Part.BaseSolidBox",
                declaring_name="Part.BaseSolidBox",
                class_name="Part",
                method_name="BaseSolidBox",
            ),
            ApiMethodCard(
                card_id="dunder-init",
                entity_kind="constructor",
                canonical_name="MeshNodeArray.__init__",
                declaring_name="MeshNodeArray.__init__",
                class_name="MeshNodeArray",
                method_name="__init__",
            ),
        ],
        runtime_release="2024",
    )

    assert value.browse_methods("Part", query="seed") == (
        ("Part.seedPart", "seed-part"),
    )
    assert value.browse_methods("Assembly", query="set") == (("Assembly.Set", "set"),)
    assert value.browse_methods("PartInstance", query="set") == ()
    assert value.browse_methods("MeshNodeArray", query="bounding") == (
        ("MeshNodeArray.getByBoundingBox", "bounding-box"),
    )
    assert value.browse_methods("Part", query="base") == (
        ("Part.BaseSolidBox", "factory"),
    )
    assert value.browse_methods("MeshNodeArray", query="init") == ()


def test_offline_cli_build_discover_and_read(tmp_path, capsys):
    catalog(tmp_path)
    args = [
        "--catalog-dir",
        str(tmp_path / "catalog"),
        "--index-dir",
        str(tmp_path / "index"),
    ]
    assert main(["index", *args]) == 0
    capsys.readouterr()
    assert main(["discover", *args, "Assign element types"]) == 0
    result = json.loads(capsys.readouterr().out)
    reference = result["candidates"][0]["card_id"]
    assert main(["read", *args, reference]) == 0
    assert json.loads(capsys.readouterr().out)["canonical_name"] == "Part.assign"
