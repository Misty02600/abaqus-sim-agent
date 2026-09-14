import lxml.etree as etree
import pytest
from tests.test_evidence_blocks import tokenizer
from tests.test_evidence_units import registry

from abaqus_sim_agent.evaluation.structural_chunking import extract_source_blocks
from abaqus_sim_agent.retrieval.equation_prose import build_equation_prose_units
from abaqus_sim_agent.retrieval.evidence_blocks import (
    TokenBoundChildren,
    make_evidence_block,
)
from abaqus_sim_agent.retrieval.evidence_units import build_evidence_units
from abaqus_sim_agent.retrieval.evidence_windows import source_role
from abaqus_sim_agent.retrieval.structured_projection import project_evidence_unit

EQUATION = '<div class="equation"><math><mi>x</mi><mo>=</mo><mn>1</mn></math></div>'
DEFINITIONS = '<p>where</p><dl class="variablelist"><dt>x</dt><dd><p>variable description <math><mi>x</mi></math></p></dd></dl>'
EXPLANATION = "<p>The second term uses the initial <math><mi>T</mi></math> value.</p>"


def test_explanation_is_searchable_without_losing_equation_or_definitions():
    blocks, nodes = registry(EQUATION + DEFINITIONS + EXPLANATION + "<p>Separate.</p>")
    old = build_evidence_units(blocks, nodes)
    result = build_equation_prose_units(blocks, nodes)
    assert [u.unit_type for u in result.units] == ["formula", "prose", "prose"]
    assert result.units[-1] == old[-1]
    assert [i for u in result.units for i in u.member_ids] == [
        b.parent_id for b in blocks
    ]
    assert "\n\n".join(u.evidence.body for u in result.units) == "\n\n".join(
        u.evidence.body for u in old
    )
    assert "variable description" in result.units[0].evidence.body
    assert result.units[1].evidence.body == blocks[-2].body
    assert result.units[1].evidence.xpath == blocks[-2].xpath
    assert result.units[1].evidence.source_sha256 == blocks[-2].source_sha256
    (relation,) = result.relations
    assert relation.explanation_id == result.units[1].evidence.parent_id
    assert relation.context_ids == (result.units[0].evidence.parent_id,)
    assert relation.source_group_id == old[0].evidence.parent_id
    split = TokenBoundChildren(tokenizer())
    projected = [
        project_evidence_unit(u, split, allow_headerless=True) for u in result.units
    ]
    assert projected[0].evidence.projection_status == "deferred_math"
    assert projected[1].evidence.projection_status == "text"
    assert (
        "The second term uses the initial [Formula: T] value."
        in projected[1].evidence.search_text
    )
    assert split.split(projected[1].evidence)


def test_explicit_empty_eligibility_preserves_all_original_ids_and_bytes():
    blocks, nodes = registry(EQUATION + DEFINITIONS + EXPLANATION)
    old = build_evidence_units(blocks, nodes)
    result = build_equation_prose_units(blocks, nodes, eligible_group_ids=frozenset())
    assert result.units == tuple(old)
    assert result.relations == ()


@pytest.mark.parametrize(
    "body",
    [
        "<p>Only inline <math><mi>T</mi></math> with prose.</p>",
        EQUATION + "<p><math><mi>T</mi></math></p>",
        EQUATION + "<p><!-- prose is not visible --><math><mi>T</mi></math></p>",
        EQUATION + EXPLANATION + EQUATION,
        EQUATION + EXPLANATION + DEFINITIONS.removeprefix("<p>where</p>"),
        EQUATION
        + '<dl class="variablelist"><dt>T</dt><dd>'
        + EXPLANATION
        + "</dd></dl>",
        EQUATION + "<ul><li>" + EXPLANATION + "</li></ul>",
        EQUATION + "<table><tr><td>" + EXPLANATION + "</td></tr></table>",
        '<figure class="usage">' + EQUATION + EXPLANATION + "</figure>",
        EQUATION + '<div class="keygroup">' + EXPLANATION + "</div>",
        EQUATION
        + '<article id="new"><h3>Other section</h3>'
        + EXPLANATION
        + "</article>",
    ],
)
def test_ambiguous_or_non_explanation_structures_are_not_split(body):
    blocks, nodes = registry(body)
    assert build_equation_prose_units(blocks, nodes).units == tuple(
        build_evidence_units(blocks, nodes)
    )


def test_unknown_inline_math_is_preserved_and_still_not_searchable():
    blocks, nodes = registry(
        EQUATION + DEFINITIONS + "<p>Explain <math><unknown>T</unknown></math>.</p>"
    )
    result = build_equation_prose_units(blocks, nodes)
    assert len(result.relations) == 1
    unit = project_evidence_unit(result.units[-1], TokenBoundChildren(tokenizer()))
    assert unit.evidence.projection_status == "deferred_math"
    assert "<unknown>T</unknown>" in unit.evidence.body


def test_partition_reuses_registry_and_limit_validation():
    blocks, nodes = registry(EQUATION + DEFINITIONS + EXPLANATION)
    with pytest.raises(ValueError, match="registry"):
        build_equation_prose_units(blocks[:-1], nodes)
    with pytest.raises(ValueError, match="limits"):
        build_equation_prose_units(blocks, nodes, max_prose_blocks=0)


def test_outer_site_layout_table_does_not_hide_article_explanations():
    tree = etree.ElementTree(
        etree.fromstring(
            '<html><body><table><tr><td><article id="inner"><h3>Inner</h3>'
            + EQUATION
            + DEFINITIONS
            + EXPLANATION
            + "</article></td></tr></table></body></html>"
        )
    )
    blocks, nodes = [], {}
    for ordinal, block in enumerate(
        extract_source_blocks(tree, title="Manual", document_id="page")
    ):
        node = tree.xpath(block.xpath)[0]
        parent = make_evidence_block(
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
        ).model_copy(update={"source_role": source_role(node)})
        blocks.append(parent)
        nodes[parent.parent_id] = node
    result = build_equation_prose_units(blocks, nodes)
    assert len(result.relations) == 1
    assert result.units[-1].unit_type == "prose"
