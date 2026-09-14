import lxml.etree as etree
import pytest
from pydantic import ValidationError
from tests.test_evidence_blocks import tokenizer
from tests.test_evidence_units import registry

from abaqus_sim_agent.retrieval.evidence_blocks import (
    EvidenceBlock,
    TokenBoundChildren,
    expand_ranked_children,
    scoring_document,
)
from abaqus_sim_agent.retrieval.evidence_units import build_evidence_units
from abaqus_sim_agent.retrieval.structured_projection import (
    UnsupportedProjection,
    mathml_projection,
    project_evidence_unit,
    table_projection,
)


def projected(body: str, *, limit: int = 256, allow_headerless: bool = False):
    blocks, nodes = registry(body)
    units = build_evidence_units(blocks, nodes)
    split = TokenBoundChildren(tokenizer(), max_tokens=limit)
    return (
        units[0],
        project_evidence_unit(units[0], split, allow_headerless=allow_headerless),
        split,
    )


def test_table_rows_have_headers_units_notes_and_original_parent():
    old, unit, split = projected(
        "<table><caption>Elastic material</caption><tr><th>Name</th><th>E (MPa)</th></tr>"
        + "".join(
            f"<tr><td>Steel {i}</td><td>{210000 + i}</td></tr>" for i in range(10)
        )
        + '</table><p class="tablefootnote">Only at room temperature.</p>',
        limit=75,
    )
    assert unit.evidence.body == old.evidence.body
    assert unit.evidence.parent_id != old.evidence.parent_id
    assert unit.evidence.projection_status == "text"
    children = split.split(unit.evidence)
    assert 1 < len(children) < 10
    parents = {unit.evidence.parent_id: unit.evidence}
    for child in children:
        assert "E (MPa):" in child.page_content
        assert "Only at room temperature." in child.page_content
        assert "Elastic material" in scoring_document(child, parents).page_content
        assert split.count(child.page_content) <= 75
    assert (
        "".join(
            unit.evidence.search_text[
                c.metadata["search_start"] : c.metadata["search_end"]
            ]
            for c in children
        )
        == unit.evidence.search_text
    )
    assert (
        expand_ranked_children(children[-1:], parents)[0].page_content
        == old.evidence.body
    )


def test_small_table_is_one_child_and_empty_cells_are_explicit():
    _, unit, split = projected(
        "<table><tr><th>Type</th><th>Value</th></tr><tr><td>A</td><td/></tr><tr><td>B</td><td>0</td></tr></table>"
    )
    assert len(split.split(unit.evidence)) == 1
    assert 'Value: ""' in unit.evidence.search_text
    assert 'Value: "0"' in unit.evidence.search_text


@pytest.mark.parametrize(
    ("table", "reason"),
    [
        ("<table><tr><td>A</td></tr><tr><td>1</td></tr></table>", "single_header"),
        (
            '<table><tr><th>A</th><th>B</th></tr><tr><td colspan="2">1</td></tr></table>',
            "nonrectangular",
        ),
        (
            '<table><tr><th>A</th></tr><tr><td rowspan="2">1</td></tr><tr><td>2</td></tr></table>',
            "merged_cells",
        ),
        (
            '<table><tr><th>A</th></tr><tr><td><img src="x"/></td></tr></table>',
            "inline_element",
        ),
        (
            "<table><tr><th>A</th></tr><tr><td><table/></td></tr></table>",
            "inline_element",
        ),
        (
            '<table><tr><th id="a">A</th></tr><tr><td headers="other">1</td></tr></table>',
            "header_association",
        ),
        (
            '<table><tr><th>A</th></tr><tr><td>1<a href="#missing"><sup>1</sup></a></td></tr></table>',
            "unresolved_footnote",
        ),
        (
            "<table><tr><th>A</th></tr><tr><td>1</td>lost condition</tr></table>",
            "structural_tail",
        ),
        (
            "<table><tr><th>A</th></tr><tr><th>B</th></tr></table>",
            "multiple_header_rows",
        ),
    ],
)
def test_unsupported_tables_remain_original_and_have_specific_reason(
    table: str, reason: str
):
    old, unit, split = projected(table)
    assert unit.evidence.body == old.evidence.body
    assert unit.evidence.projection_status == "deferred_table"
    assert reason in unit.projection_reason
    assert split.split(unit.evidence) == []


def test_table_row_overflow_is_not_partially_indexed():
    old, unit, split = projected(
        "<table><tr><th>A</th></tr><tr><td>" + "word " * 100 + "</td></tr></table>",
        limit=32,
    )
    assert unit.projection_reason.startswith("token_budget:")
    assert unit.evidence == old.evidence.model_copy(
        update={"parent_id": unit.evidence.parent_id}
    )
    assert split.split(unit.evidence) == []


@pytest.mark.parametrize(
    ("expression", "expected"),
    [
        ("<msub><mi>U</mi><mn>1</mn></msub>", "(U)_(1)"),
        ("<msup><mi>x</mi><mn>2</mn></msup>", "(x)^(2)"),
        ("<msubsup><mi>x</mi><mi>i</mi><mn>2</mn></msubsup>", "(x)_(i)^(2)"),
        ("<mfrac><mi>a</mi><mi>b</mi></mfrac>", "(a)/(b)"),
        ("<msqrt><mi>x</mi></msqrt>", "sqrt(x)"),
        ("<mroot><mi>x</mi><mn>3</mn></mroot>", "root(x, index=3)"),
        ('<mi mathvariant="bold">v</mi>', "bold(v)"),
    ],
)
def test_math_presentation_structure_is_explicit(expression: str, expected: str):
    assert (
        mathml_projection(
            etree.fromstring(
                f'<math xmlns="http://www.w3.org/1998/Math/MathML">{expression}</math>'
            )
        )
        == expected
    )


def test_math_formula_and_variable_definitions_are_searchable_together():
    old, unit, split = projected(
        '<div class="equation"><math><mfrac><mi>F</mi><mi>A</mi></mfrac></math></div><p>where</p><dl class="variablelist"><dt>F</dt><dd>axial force</dd><dt>A</dt><dd>section area</dd></dl>'
    )
    assert unit.evidence.projection_status == "text"
    assert "[Formula: (F)/(A)]" in unit.evidence.search_text
    assert "axial force" in unit.evidence.search_text
    assert "section area" in unit.evidence.search_text
    assert unit.evidence.body == old.evidence.body
    assert split.split(unit.evidence)


@pytest.mark.parametrize(
    "xml",
    [
        "<mtable/>",
        "<mfrac><mi>x</mi></mfrac>",
        '<mfrac linethickness="0"><mi>x</mi><mi>y</mi></mfrac>',
        "<mphantom><mi>x</mi></mphantom>",
        "<mi><mglyph/></mi>",
    ],
)
def test_unknown_math_is_deferred_without_losing_neighbor_text(xml: str):
    old, unit, split = projected(
        f"<p>Important condition <math>{xml}</math> applies.</p>"
    )
    assert unit.evidence.projection_status == "deferred_math"
    assert unit.evidence.body == old.evidence.body
    assert unit.projection_reason
    assert not split.split(unit.evidence)


def test_table_supports_inline_math_without_flattening_subscripts():
    _, unit, _ = projected(
        "<table><tr><th>Variable</th><th>Value</th></tr><tr><td><math><msub><mi>U</mi><mn>2</mn></msub></math></td><td>mm<sup>2</sup></td></tr></table>"
    )
    assert "(U)_(2)" in unit.evidence.search_text
    assert "mm^(2)" in unit.evidence.search_text


def test_atomic_boundary_and_child_tampering_are_rejected():
    _, unit, split = projected(
        "<table><tr><th>A</th></tr><tr><td>one</td></tr><tr><td>two</td></tr></table>"
    )
    data = unit.evidence.model_dump()
    data["atomic_search_ends"] = (0,)
    with pytest.raises(ValidationError, match="boundaries"):
        EvidenceBlock.model_validate(data)
    child = split.split(unit.evidence)[0]
    child.metadata["search_end"] -= 1
    with pytest.raises(ValueError, match="does not match"):
        scoring_document(child, {unit.evidence.parent_id: unit.evidence})
    with pytest.raises(ValueError, match="already"):
        project_evidence_unit(unit, split)


def test_xml_entity_is_not_resolved():
    with pytest.raises(UnsupportedProjection, match="declaration"):
        table_projection(
            '[TableXML]\n<!DOCTYPE table [<!ENTITY e SYSTEM "file:///secret">]><table>&e;</table>\n[/TableXML]'
        )


@pytest.mark.parametrize(
    "rows",
    [
        [("alpha", "description one"), ("beta", "description two")],
        [("scope.item", "an item at this level")],
        [("1", "first component"), ("1", "another occurrence, not overwritten")],
        [("spelling example", "accepted"), ("another example", "rejected")],
    ],
)
def test_headerless_pairs_preserve_every_row_without_inventing_meaning(rows):
    import json

    body = (
        "<table><caption>Original caption</caption>"
        + "".join(f"<tr><td>{a}</td><td>{b}</td></tr>" for a, b in rows)
        + '</table><p class="tablefootnote">Original condition.</p>'
    )
    old, unit, split = projected(body, allow_headerless=True)
    assert unit.evidence.projection_status == "text"
    assert unit.projection_version == "headerless-two-column-rows-v1"
    assert unit.evidence.body == old.evidence.body
    assert unit.member_ids == old.member_ids
    assert unit.evidence.search_text == "".join(
        f"Row {i}: " + json.dumps([a, b]) + "\n" for i, (a, b) in enumerate(rows, 1)
    )
    children = split.split(unit.evidence)
    assert all(
        "Original caption" in c.page_content
        and "Original condition." in c.page_content
        and "Section: Manual > Rules" in c.page_content
        for c in children
    )
    assert (
        expand_ranked_children(children, {unit.evidence.parent_id: unit.evidence})[
            0
        ].page_content
        == old.evidence.body
    )


def test_headerless_mode_is_explicit_and_does_not_rewrite_existing_headered_units():
    body = "<table><tr><td>A</td><td>B</td></tr></table>"
    assert projected(body)[1].evidence.projection_status == "deferred_table"
    normal = "<table><tr><th>Name</th><th>Description</th></tr><tr><td>A</td><td>B</td></tr></table>"
    assert projected(normal)[1] == projected(normal, allow_headerless=True)[1]


@pytest.mark.parametrize(
    ("body", "reason"),
    [
        ("<table><tr><td>A</td><td/></tr></table>", "empty_cell"),
        ("<table><tr><td>A</td><td>B</td><td>C</td></tr></table>", "two_columns"),
        ("<table><tr><td>A</td></tr></table>", "two_columns"),
        (
            "<table><thead><tr><td>A</td><td>B</td></tr></thead><tbody><tr><td>C</td><td>D</td></tr></tbody></table>",
            "undeclared_header",
        ),
        ('<table><tr><td scope="row">A</td><td>B</td></tr></table>', "header_scope"),
        (
            '<table><tr><td headers="missing">A</td><td>B</td></tr></table>',
            "header_association",
        ),
        (
            '<table><tr><td rowspan="2">A</td><td>B</td></tr><tr><td>C</td><td>D</td></tr></table>',
            "merged_cells",
        ),
        (
            '<table role="presentation"><tr><td>A</td><td>B</td></tr></table>',
            "presentational_role",
        ),
        (
            "<table><tr><td>A</td><td><table><tr><td>nested</td></tr></table></td></tr></table>",
            "inline_element",
        ),
        (
            "<table><tr><td>A</td><td>B</td>unowned condition</tr></table>",
            "structural_tail",
        ),
    ],
)
def test_headerless_pattern_does_not_guess_missing_relationships(body, reason):
    old, unit, split = projected(body, allow_headerless=True)
    assert unit.evidence.projection_status == "deferred_table"
    assert reason in unit.projection_reason
    assert unit.evidence.body == old.evidence.body
    assert not split.split(unit.evidence)


def test_headerless_rows_are_atomic_and_keep_context_in_every_child():
    body = (
        "<table>"
        + "".join(
            f"<tr><td>entry {i}</td><td>description {i}</td></tr>" for i in range(12)
        )
        + "</table>"
    )
    _, unit, split = projected(body, allow_headerless=True, limit=65)
    children = split.split(unit.evidence)
    assert len(children) > 1
    assert all("no declared column headers" in c.page_content for c in children)
    assert all(
        c.metadata["search_end"] in unit.evidence.atomic_search_ends for c in children
    )
    assert (
        "".join(
            unit.evidence.search_text[
                c.metadata["search_start"] : c.metadata["search_end"]
            ]
            for c in children
        )
        == unit.evidence.search_text
    )
    _, overflow, _ = projected(
        "<table><tr><td>A</td><td>" + "word " * 100 + "</td></tr></table>",
        allow_headerless=True,
        limit=40,
    )
    assert overflow.projection_reason.startswith("token_budget:")
