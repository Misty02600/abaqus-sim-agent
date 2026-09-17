import hashlib
import json
from concurrent.futures import ThreadPoolExecutor

import pytest
from pydantic import ValidationError

from abaqus_sim_agent.api_catalog import (
    ApiCatalog,
    ApiMethodCard,
    ApiSourceRef,
    OfficialApiRecord,
)
from abaqus_sim_agent.api_catalog.cli import main
from abaqus_sim_agent.api_catalog.discovery import CatalogDiscovery
from abaqus_sim_agent.api_catalog.intent import DiscoveryIntent
from abaqus_sim_agent.api_catalog.lexical import EnglishLexicalAnalyzer, source_symbols


def make_index(tmp_path):
    cards = []
    for name, summary in [
        ("Part.assign", "Assignments of element types to geometric regions"),
        ("Assembly.inspect", "Inspects reference positions"),
    ]:
        owner, method = name.split(".")
        cards.append(
            ApiMethodCard(
                card_id=name,
                canonical_name=name,
                declaring_name=name,
                entity_kind="method",
                class_name=owner,
                method_name=method,
                official_records=(
                    OfficialApiRecord(
                        record_id=name,
                        entity_kind="method",
                        owner_name=owner,
                        callable_name=method,
                        canonical_name=name,
                        summary=summary,
                        source=ApiSourceRef(
                            kind="official", release="2024", source_path="fixture"
                        ),
                    ),
                ),
            )
        )
    catalog = ApiCatalog(cards, runtime_release="2024")
    catalog.write(tmp_path / "catalog")
    CatalogDiscovery.build(catalog, tmp_path / "index")
    return catalog


def test_normalizes_words_without_rewriting_symbols_or_negation():
    analyzer = EnglishLexicalAnalyzer()
    assert analyzer("assign assigned assignments") == ["word:assign"] * 3
    assert (
        analyzer("type types region regions") == ["word:type"] * 2 + ["word:region"] * 2
    )
    assert analyzer("How do I") == []
    assert "word:not" in analyzer("not")
    assert "word:don't" in analyzer("don't")
    text = "在Part.faces中查找，不调用generateMesh；使用CPS4R与OFF。"
    assert source_symbols(text) == ("Part.faces", "generateMesh", "CPS4R", "OFF")
    assert "symbol:Part.faces" in analyzer(text)
    assert "symbol:Part.faces" not in analyzer("Part.Faces")
    assert analyzer.binding["profile"] == "english-snowball-v1"
    with ThreadPoolExecutor(max_workers=4) as pool:
        assert (
            list(pool.map(analyzer, ["assignments regions"] * 12))
            == [["word:assign", "word:region"]] * 12
        )


def test_intent_retains_authoritative_source_and_keeps_exact(tmp_path):
    catalog = make_index(tmp_path)
    index = CatalogDiscovery.open(
        catalog, tmp_path / "index", lexical_profile="english-snowball-v1"
    )
    original = "  给已有零件指定CPS4R；不求解、不修改厚度。\n"
    intent = DiscoveryIntent(
        original_request=original, operation="assign element type to a region"
    )
    result = index.search_intent(intent)
    assert result["candidates"][0]["name"] == "Part.assign"
    assert result["original_query"] == original
    assert result["retrieval_query"] == intent.operation
    assert result["intent"]["original_request"] == original
    assert (
        result["intent"]["original_sha256"]
        == hashlib.sha256(original.encode()).hexdigest()
    )
    assert result["intent"]["source_symbols"] == ["CPS4R"]
    assert result["lexical_configuration"]["profile"] == "english-snowball-v1"
    assert index.search("Part.assign")["route"] == "exact"
    assert index.search("Part.Assign")["status"] == "not_found"
    for value in ["", "  "]:
        with pytest.raises(ValidationError):
            DiscoveryIntent(original_request=original, operation=value)


def test_cli_intent_is_explicit(tmp_path, capsys):
    make_index(tmp_path)
    file = tmp_path / "intent.json"
    file.write_text(
        DiscoveryIntent(
            original_request="不提交求解", operation="assign element types"
        ).model_dump_json(),
        encoding="utf-8",
    )
    args = [
        "--catalog-dir",
        str(tmp_path / "catalog"),
        "--index-dir",
        str(tmp_path / "index"),
        "--intent-file",
        str(file),
        "--lexical-profile",
        "english-snowball-v1",
    ]
    assert main(["discover", *args]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["intent"]["original_request"] == "不提交求解"
    with pytest.raises(SystemExit):
        main(["discover", *args, "another question"])
