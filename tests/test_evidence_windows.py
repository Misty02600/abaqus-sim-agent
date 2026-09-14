import lxml.etree as etree
import pytest
from tokenizers import Tokenizer, models, pre_tokenizers

from abaqus_sim_agent.retrieval.evidence_blocks import (
    TokenBoundChildren,
    make_evidence_block,
    scoring_document,
)
from abaqus_sim_agent.retrieval.evidence_windows import (
    expand_reading_windows,
    reading_windows,
    source_role,
)


def parent(text: str, *, ordinal: int):
    return make_evidence_block(
        document_id="page",
        title="Manual",
        release="test",
        heading_path=("Manual", "Rules"),
        source="https://example.org/page#rules",
        source_sha256="a" * 64,
        xpath=f"/p[{ordinal + 1}]",
        anchor="rules",
        kind="p",
        body=text,
        ordinal=ordinal,
    )


def nodes(body: str):
    return etree.fromstring(f"<html><body>{body}</body></html>")


def test_metadata_uses_structure_not_keyword():
    tree = nodes(
        '<div class="section keygroup"><p>TypeHistory data</p></div><article><p>TypeHistory data is discussed here.</p></article>'
    )
    assert [source_role(p) for p in tree.xpath("//p")] == ["metadata", "content"]


def test_toc_label_and_links_are_navigation_but_semantic_lists_are_not():
    tree = nodes(
        '<div class="abstract"><p>Topics:</p><ul><li><a href="#a">A</a></li><li><a href="#b">B</a></li></ul><p>Conditions:</p><ul><li>No separation.</li><li>Allow deformation.</li></ul></div><article id="a"><h2>A</h2></article><article id="b"><h2>B</h2></article>'
    )
    labels = tree.xpath("//p")
    lists = tree.xpath("//ul")
    assert source_role(labels[0]) == "navigation"
    assert source_role(lists[0]) == "navigation"
    assert source_role(labels[1]) == "content"
    assert source_role(lists[1]) == "content"


def test_equation_connectors_require_structural_neighbors():
    tree = nodes(
        '<p>where</p><div class="equation">Equation</div><p>where</p><dl class="variablelist"><dt>x</dt><dd>Meaning</dd></dl><p>and</p>'
    )
    assert [source_role(p) for p in tree.xpath("//p")] == [
        "content",
        "equation_context",
        "content",
    ]


def content(text: str, ordinal: int):
    p = parent(text, ordinal=ordinal)
    return p.model_copy(update={"source_role": "content"})


def test_window_keeps_rule_and_qualification_but_respects_limits():
    a, b, c = (
        content("Rule.", 0),
        content("However, qualification.", 1),
        content("Another paragraph.", 2),
    )
    ps = {p.parent_id: p for p in [a, b, c]}
    assert reading_windows(ps, max_blocks=2) == [
        (a.parent_id, b.parent_id),
        (c.parent_id,),
    ]
    assert reading_windows(ps, max_characters=10) == [
        (a.parent_id,),
        (b.parent_id,),
        (c.parent_id,),
    ]
    with pytest.raises(ValueError, match="positive"):
        reading_windows(ps, max_blocks=0)


@pytest.mark.parametrize(
    "change",
    [
        {"heading_path": ("Different",)},
        {"anchor": "other"},
        {"source_sha256": "b" * 64},
        {"release": "other"},
        {"kind": "definition"},
        {"source_role": "navigation"},
    ],
)
def test_windows_do_not_cross_source_or_structural_boundaries(change):
    a, b = content("A.", 0), content("B.", 1).model_copy(update=change)
    assert reading_windows({a.parent_id: a, b.parent_id: b}) == [
        (a.parent_id,),
        (b.parent_id,),
    ]


def splitter():
    tok = Tokenizer(models.WordLevel({"[UNK]": 0}, unk_token="[UNK]"))
    tok.pre_tokenizer = pre_tokenizers.Whitespace()
    return TokenBoundChildren(tok)


def test_window_expansion_preserves_selection_and_does_not_backfill():
    a, b = content("Rule.", 0), content("Qualification.", 1)
    c = content("Unrelated.", 2).model_copy(update={"anchor": "elsewhere"})
    ps = {p.parent_id: p for p in [a, b, c]}
    children = [child for p in ps.values() for child in splitter().split(p)]
    result = expand_reading_windows(children, ps, top_k=2)
    assert len(result) == 1
    assert result[0].page_content == "Rule.\n\nQualification."
    assert result[0].metadata["member_parent_ids"] == [a.parent_id, b.parent_id]
    assert result[0].metadata["source_block_xpaths"] == [a.xpath, b.xpath]


def test_excluded_role_is_not_searchable_and_oversize_atom_is_not_cut():
    p = content("Long text.", 0)
    child = splitter().split(p)[0]
    blocked = p.model_copy(update={"source_role": "metadata"})
    assert splitter().split(blocked) == []
    with pytest.raises(ValueError, match="not searchable"):
        scoring_document(child, {blocked.parent_id: blocked})
    result = expand_reading_windows([child], {p.parent_id: p}, max_characters=3)
    assert result[0].page_content == p.body
    assert result[0].metadata["oversized_atomic_block"]
