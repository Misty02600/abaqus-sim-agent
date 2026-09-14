from pathlib import Path

import pytest
from tokenizers import Tokenizer, models, pre_tokenizers, processors

from abaqus_sim_agent.retrieval.evidence_blocks import (
    TokenBoundChildren,
    expand_ranked_children,
    make_evidence_block,
    read_parent_store,
    retrieval_projection,
    scoring_document,
    write_parent_store,
)


def tokenizer() -> Tokenizer:
    result = Tokenizer(
        models.WordLevel({"[UNK]": 0, "[CLS]": 1, "[SEP]": 2}, unk_token="[UNK]")
    )
    result.pre_tokenizer = pre_tokenizers.Whitespace()
    result.post_processor = processors.TemplateProcessing(
        single="[CLS] $A [SEP]", special_tokens=[("[CLS]", 1), ("[SEP]", 2)]
    )
    result.enable_truncation(max_length=4)
    return result


def parent(text: str, *, kind: str = "p", ordinal: int = 0):
    return make_evidence_block(
        document_id="page",
        title="Manual",
        release="2026 FD02",
        heading_path=("Manual", "Scope"),
        source="https://example.org/page#scope",
        source_sha256="a" * 64,
        xpath="/html/body/p",
        anchor="scope",
        kind=kind,
        body=text,
        ordinal=ordinal,
    )


def test_token_bound_spans_cover_all_text_and_parent_expansion_restores_tail():
    original = parent("First sentence. " * 80 + "However, retain the final exception.")
    split = TokenBoundChildren(tokenizer(), max_tokens=24)
    children = split.split(original)
    assert len(children) > 1
    assert all(split.count(child.page_content) <= 24 for child in children)
    restored = "".join(
        original.search_text[c.metadata["search_start"] : c.metadata["search_end"]]
        for c in children
    )
    assert restored == original.search_text
    assert children[0].metadata["search_start"] == 0
    assert children[-1].metadata["search_end"] == len(original.search_text)
    store = {original.parent_id: original}
    expanded = expand_ranked_children([children[-1], children[0]], store)
    assert len(expanded) == 1
    assert expanded[0].page_content == original.body
    assert expanded[0].metadata["source_sha256"] == original.source_sha256
    assert "However" in expanded[0].page_content


def test_same_page_different_parents_remain_distinct():
    a, b = parent("Alpha."), parent("Beta.", ordinal=1)
    split = TokenBoundChildren(tokenizer(), max_tokens=24)
    hits = split.split(a) + split.split(b)
    assert len(expand_ranked_children(hits, {a.parent_id: a, b.parent_id: b})) == 2


def test_definition_projection_retains_inline_order_and_entry_in_every_child():
    original = parent(
        "SETTING\n[DefinitionXML]\n<dd><p>Before <b>Abaqus/Standard</b> after.</p><p>"
        + "Long rule. " * 60
        + "</p></dd>\n[/DefinitionXML]",
        kind="definition",
    )
    assert "Before Abaqus/Standard after." in original.search_text
    split = TokenBoundChildren(tokenizer(), max_tokens=28)
    assert all("Entry: SETTING\n" in c.page_content for c in split.split(original))
    assert "[DefinitionXML]" in original.body


@pytest.mark.parametrize(
    ("kind", "text", "status"),
    [
        ("table", "<table>data</table>", "deferred_table"),
        ("p", "Value [MathML]<math>x</math>[/MathML]", "deferred_math"),
        (
            "ol",
            "[ListXML]\n<ol start='2'><li>entry<ul><li>nested</li></ul></li></ol>\n[/ListXML]",
            "deferred_list",
        ),
    ],
)
def test_unvalidated_structures_are_saved_but_not_silently_embedded(
    kind: str, text: str, status: str
):
    original = parent(text, kind=kind)
    assert original.body == text
    assert original.projection_status == status
    assert TokenBoundChildren(tokenizer()).split(original) == []


def test_hash_bound_parent_store_and_no_overwrite(tmp_path: Path):
    original = parent("Evidence.")
    path = tmp_path / "parents.jsonl"
    digest = write_parent_store(path, [original])
    assert read_parent_store(path, expected_sha256=digest) == {
        original.parent_id: original
    }
    with pytest.raises(FileExistsError):
        write_parent_store(path, [original])
    path.write_bytes(path.read_bytes() + b" ")
    with pytest.raises(ValueError, match="hash mismatch"):
        read_parent_store(path, expected_sha256=digest)


def test_child_forgery_and_header_overflow_fail_explicitly():
    original = parent("Evidence.")
    split = TokenBoundChildren(tokenizer(), max_tokens=24)
    child = split.split(original)[0]
    child.page_content += "Corruption"
    with pytest.raises(ValueError, match="does not match"):
        scoring_document(child, {original.parent_id: original})
    with pytest.raises(ValueError, match="heading context"):
        TokenBoundChildren(tokenizer(), max_tokens=4).split(original)
    with pytest.raises(ValueError, match="positive"):
        expand_ranked_children([], {}, top_k=0)


def test_simple_projection_does_not_normalize_away_code_indentation():
    text = "[Code]\nif condition:\n    run()\n[/Code]"
    assert retrieval_projection(text, "pre") == (text, "text")


def test_flat_formatted_list_is_searchable_without_losing_comparison():
    body = '[ListXML]\n<ul class="ul"><li class="li">Kinematic <b>imposes rigidity</b>.</li><li>Distributing does not impose rigidity.</li></ul>\n[/ListXML]'
    text, status = retrieval_projection(body, "ul")
    assert status == "text"
    assert (
        text
        == "- Kinematic imposes rigidity.\n- Distributing does not impose rigidity."
    )
    original = parent(body, kind="ul")
    assert original.body == body
    assert TokenBoundChildren(tokenizer(), max_tokens=40).split(original)


@pytest.mark.parametrize(
    ("attributes", "items", "expected"),
    [
        (
            'start="3"',
            '<li>One</li><li value="7">Two</li><li>Three</li>',
            "3. One\n7. Two\n8. Three",
        ),
        ('reversed="reversed"', "<li>One</li><li>Two</li>", "2. One\n1. Two"),
    ],
)
def test_ordered_list_start_value_and_reverse_are_preserved(
    attributes: str, items: str, expected: str
):
    assert retrieval_projection(
        f"[ListXML]\n<ol {attributes}>{items}</ol>\n[/ListXML]", "ol"
    ) == (expected, "text")


def test_nondecimal_list_remains_explicitly_deferred():
    assert retrieval_projection(
        '[ListXML]\n<ol type="I"><li>One</li></ol>\n[/ListXML]', "ol"
    ) == ("", "deferred_list")


def test_list_with_unmodeled_tail_does_not_silently_drop_conditions():
    assert retrieval_projection(
        "[ListXML]\n<ul><li>Rule</li>However, exception.</ul>\n[/ListXML]", "ul"
    ) == ("", "deferred_list")
