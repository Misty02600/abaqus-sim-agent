import lxml.etree as etree
import pytest

from abaqus_sim_agent.evaluation.structural_chunking import (
    chunk_source_blocks,
    extract_source_blocks,
)


def parse(body: str):
    tree = etree.ElementTree(
        etree.fromstring(
            f'<html><body id="page"><article id="outer">{body}</article></body></html>'
        )
    )
    return extract_source_blocks(tree, title="Manual", document_id="page")


def test_inline_order_and_heading_only_elimination():
    blocks = parse(
        '<h2>In <b>Abaqus/Standard</b></h2><article id="inner"><h3>Timing</h3><p>Before <b>Abaqus/Standard</b> after <a>link</a>.</p></article>'
    )
    assert len(blocks) == 1
    assert blocks[0].text == "Before Abaqus/Standard after link."
    assert blocks[0].anchor == "inner"
    assert blocks[0].heading_path == ("Manual", "In Abaqus/Standard", "Timing")


def test_long_definition_is_not_truncated_and_keeps_term():
    text = "Long rule. " * 120 + "However, retain the final exception."
    blocks = parse(f"<h2>Parameters</h2><dl><dt>CHOICE</dt><dd>{text}</dd></dl>")
    chunks = chunk_source_blocks(blocks, document_id="page")
    assert len(chunks) == 1
    assert chunks[0].page_content == "CHOICE\n" + text
    assert chunks[0].metadata["oversized_block"]


def test_lists_tables_math_and_code_preserve_structure():
    blocks = parse(
        '<h2>Structures</h2><ol><li>First <b>condition</b></li><li>Second condition</li></ol><table><tr><th rowspan="2">Product</th><th>Value</th></tr><tr><td>Standard</td></tr></table><p>Energy <math><msup><mi>x</mi><mn>2</mn></msup></math> follows.</p><pre>if flag:\n    run()</pre>'
    )
    assert blocks[0].text == "1. First condition\n2. Second condition"
    assert 'rowspan="2"' in blocks[1].text
    assert "[TableXML]" in blocks[1].text
    assert "<msup>" in blocks[2].text
    assert blocks[2].text.endswith(" follows.")
    assert blocks[3].text == "[Code]\nif flag:\n    run()\n[/Code]"


def test_chunks_keep_whole_paragraphs_and_neighbor_links():
    blocks = parse(
        "<h2>Rules</h2><p>First paragraph.</p><p>However, second paragraph.</p>"
    )
    chunks = chunk_source_blocks(blocks, document_id="page", soft_limit=20)
    assert [d.page_content for d in chunks] == [
        "First paragraph.",
        "However, second paragraph.",
    ]
    assert chunks[0].metadata["next_chunk_id"] == chunks[1].id
    assert chunks[1].metadata["previous_chunk_id"] == chunks[0].id
    assert len({p for c in chunks for p in c.metadata["source_block_xpaths"]}) == 2
    with pytest.raises(ValueError, match="positive"):
        chunk_source_blocks(blocks, document_id="page", soft_limit=0)


def test_nested_lists_and_complex_definitions_keep_original_structure():
    blocks = parse(
        '<h2>Rules</h2><ol start="3"><li>Outer<ul><li>Inner</li></ul></li></ol><dl><dt>CODE</dt><dd><p>Explanation</p><pre>if flag:\n    preserve()</pre></dd></dl>'
    )
    assert "[ListXML]" in blocks[0].text
    assert 'start="3"' in blocks[0].text
    assert "<ul><li>Inner</li></ul>" in blocks[0].text
    assert "[DefinitionXML]" in blocks[1].text
    assert "if flag:\n    preserve()" in blocks[1].text


def test_abstract_and_nested_article_scope_are_preserved_without_duplicate_roots():
    tree = etree.ElementTree(
        etree.fromstring(
            '<html><body id="page"><p class="abstract">Intro rule.</p><article id="outer"><h2>Outer</h2><article id="inner"><h3>Inner</h3><p>Inner text.</p></article><p>Outer text.</p></article></body></html>'
        )
    )
    blocks = extract_source_blocks(tree, title="Manual", document_id="page")
    assert [b.text for b in blocks] == ["Intro rule.", "Inner text.", "Outer text."]
    assert blocks[1].heading_path == ("Manual", "Outer", "Inner")
    assert blocks[2].heading_path == ("Manual", "Outer")


def test_nested_usage_container_does_not_flatten_pre_code():
    blocks = parse(
        '<h2>API</h2><figure class="fig usage"><figcaption>Input File Usage</figcaption><p>Use this:</p><pre>STEP, NAME=Example\n  LINE TWO\nEND STEP</pre></figure>'
    )
    code = [b for b in blocks if b.kind == "pre"]
    assert len(code) == 1
    assert code[0].text == "[Code]\nSTEP, NAME=Example\n  LINE TWO\nEND STEP\n[/Code]"
    assert any(b.text == "Use this:" for b in blocks)


def test_block_inside_unknown_inline_wrapper_is_not_flattened():
    blocks = parse(
        "<h2>Example</h2><p>Before<span><pre>if x:\n    y()</pre></span>After</p>"
    )
    assert any(b.kind == "pre" and "if x:\n    y()" in b.text for b in blocks)
