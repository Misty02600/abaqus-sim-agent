from pathlib import Path

import lxml.etree as etree
import pytest
from tokenizers import Tokenizer, models, pre_tokenizers

from abaqus_sim_agent.evaluation.structural_chunking import extract_source_blocks
from abaqus_sim_agent.retrieval.evidence_blocks import (
    TokenBoundChildren,
    expand_ranked_children,
    make_evidence_block,
)
from abaqus_sim_agent.retrieval.evidence_units import (
    build_evidence_units,
    read_unit_store,
    write_unit_store,
)
from abaqus_sim_agent.retrieval.evidence_windows import source_role


def registry(body: str):
    tree = etree.ElementTree(
        etree.fromstring(
            f'<html><body id="page"><article id="scope"><h2>Rules</h2>{body}</article></body></html>'
        )
    )
    blocks = []
    nodes = {}
    for ordinal, block in enumerate(
        extract_source_blocks(tree, title="Manual", document_id="page")
    ):
        p = make_evidence_block(
            document_id="page",
            title="Manual",
            release="test",
            heading_path=block.heading_path,
            source="https://example.org/page#" + block.anchor,
            source_sha256="a" * 64,
            xpath=block.xpath,
            anchor=block.anchor,
            kind=block.kind,
            body=block.text,
            ordinal=ordinal,
        )
        node = tree.xpath(block.xpath)[0]
        p = p.model_copy(update={"source_role": source_role(node)})
        blocks.append(p)
        nodes[p.parent_id] = node
    return blocks, nodes


def test_prose_and_lead_list_are_distinct_units_with_all_members_once():
    blocks, nodes = registry(
        "<p>Definition.</p><p>However, qualification.</p><p>Available choices:</p><ul><li>A</li><li>B</li></ul>"
    )
    units = build_evidence_units(blocks, nodes)
    assert [u.unit_type for u in units] == ["prose", "list"]
    assert units[0].evidence.body == "Definition.\n\nHowever, qualification."
    assert units[1].evidence.search_text == "Available choices:\n\n- A\n- B"
    assert [key for u in units for key in u.member_ids] == [b.parent_id for b in blocks]


def test_definitions_are_atomic_and_prose_limits_do_not_cut_original():
    blocks, nodes = registry(
        "<p>First.</p><p>Second.</p><dl><dt>A</dt><dd>"
        + "Rule. " * 400
        + "</dd><dt>B</dt><dd>Another rule.</dd></dl>"
    )
    units = build_evidence_units(
        blocks, nodes, max_prose_blocks=1, max_prose_characters=10
    )
    assert [u.unit_type for u in units] == [
        "prose",
        "prose",
        "definition",
        "definition",
    ]
    assert units[2].oversized
    assert units[2].evidence.body == blocks[2].body
    assert len(units[2].member_ids) == 1


def test_code_keeps_direct_lead_and_indentation():
    blocks, nodes = registry(
        "<p>Use this example:</p><pre>if condition:\n    run()</pre>"
    )
    unit = build_evidence_units(blocks, nodes)[0]
    assert unit.unit_type == "code"
    assert "Use this example:\n\n[Code]\nif condition:\n    run()" in unit.evidence.body


def test_formula_and_variable_context_are_preserved_but_not_fake_text_search():
    blocks, nodes = registry(
        '<div class="equation"><math><mi>x</mi><mo>=</mo><mn>1</mn></math></div><p>where</p><dl class="variablelist"><dt>x</dt><dd>variable description</dd></dl><p>Separate explanation.</p>'
    )
    units = build_evidence_units(blocks, nodes)
    assert units[0].unit_type == "formula"
    assert "where" in units[0].evidence.body
    assert "variable description" in units[0].evidence.body
    assert units[0].evidence.projection_status == "deferred_math"
    assert units[0].evidence.search_text == ""
    assert units[-1].unit_type == "prose"


def test_table_retains_caption_and_explicit_note_without_embedding_xml():
    blocks, nodes = registry(
        '<table><caption>Limits</caption><tr><th>Unit</th><th>Value</th></tr><tr><td>mm</td><td>1</td></tr></table><p class="tablefootnote">Condition for this table.</p><p>Unrelated paragraph.</p>'
    )
    units = build_evidence_units(blocks, nodes)
    assert units[0].unit_type == "table"
    assert "<caption>Limits</caption>" in units[0].evidence.body
    assert "Condition for this table." in units[0].evidence.body
    assert "Unrelated" not in units[0].evidence.body
    assert units[0].evidence.projection_status == "deferred_table"


def test_scope_changes_and_source_metadata_break_units():
    blocks, nodes = registry(
        '<p>A.</p><div class="keygroup"><p>Metadata</p></div><p>B.</p><article id="other"><h3>Different</h3><p>C.</p></article>'
    )
    units = build_evidence_units(blocks, nodes)
    assert [u.unit_type for u in units] == ["prose", "excluded", "prose", "prose"]
    assert units[1].evidence.source_role == "metadata"
    assert units[-1].evidence.anchor == "other"


def test_registry_order_and_complete_node_inventory_are_checked():
    blocks, nodes = registry("<p>A.</p><p>B.</p><p>C.</p>")
    with pytest.raises(ValueError, match="registry"):
        build_evidence_units([blocks[1], blocks[0], blocks[2]], nodes)
    with pytest.raises(ValueError, match="registry"):
        build_evidence_units(blocks[:1], nodes)
    with pytest.raises(ValueError, match="limits"):
        build_evidence_units(blocks, nodes, max_prose_blocks=0)


def test_persisted_units_generate_children_and_restore_prebuilt_parent(tmp_path: Path):
    blocks, nodes = registry(
        "<p>" + "Long explanation. " * 100 + "</p><p>However, final qualification.</p>"
    )
    units = build_evidence_units(blocks, nodes)
    path = tmp_path / "units.jsonl"
    digest = write_unit_store(path, units)
    restored = read_unit_store(path, expected_sha256=digest)
    assert list(restored.values()) == units
    tokenizer = Tokenizer(models.WordLevel({"[UNK]": 0}, unk_token="[UNK]"))
    tokenizer.pre_tokenizer = pre_tokenizers.Whitespace()
    splitter = TokenBoundChildren(tokenizer, max_tokens=32)
    parents = {key: value.evidence for key, value in restored.items()}
    children = [c for p in parents.values() for c in splitter.split(p)]
    assert len(children) > 1
    result = expand_ranked_children(children, parents)
    assert result[0].page_content == units[0].evidence.body
    assert "final qualification" in result[0].page_content
    with pytest.raises(FileExistsError):
        write_unit_store(path, units)
    with pytest.raises(ValueError, match="hash mismatch"):
        read_unit_store(path, expected_sha256="0" * 64)


def test_explicit_usage_container_keeps_instructions_and_alternative_examples():
    blocks, nodes = registry(
        '<figure class="fig usage"><figcaption>Input File Usage</figcaption><p>Exact output:</p><pre>OUTPUT, TIME MARKS=YES\nNEXT LINE</pre><p>Approximate output:</p><pre>OUTPUT, TIME MARKS=NO</pre></figure>'
    )
    units = build_evidence_units(blocks, nodes)
    assert len(units) == 1
    assert units[0].unit_type == "code"
    assert units[0].construction_rule == "explicit_usage_container"
    assert "Exact output:" in units[0].evidence.body
    assert "TIME MARKS=YES\nNEXT LINE" in units[0].evidence.body
    assert "Approximate output:" in units[0].evidence.body
