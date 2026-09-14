import json
from pathlib import Path

from abaqus_sim_agent.coding.clarification import (
    ConsoleClarificationProvider,
    ScriptedClarificationProvider,
)
from abaqus_sim_agent.coding.contracts import (
    CLARIFICATION_POLICY_VERSION,
    ClarificationRequest,
)


def thickness_request() -> ClarificationRequest:
    return ClarificationRequest(
        field_id="plate_thickness",
        kind="missing_physical_input",
        question="板厚是多少？",
        impact="板厚会改变平面应力截面刚度和反力。",
    )


def test_scripted_provider_only_consumes_matching_field_id() -> None:
    provider = ScriptedClarificationProvider(
        {
            "plate_thickness": "1 mm",
            "material_name": "steel",
        }
    )

    assert (
        provider.get_answer(
            ClarificationRequest(
                field_id="load_magnitude",
                kind="missing_physical_input",
                question="载荷大小是多少？",
                impact="载荷大小会改变响应幅值。",
            )
        )
        is None
    )
    answer = provider.get_answer(thickness_request())

    assert answer is not None
    assert answer.field_id == "plate_thickness"
    assert answer.value == "1 mm"
    assert answer.source == "scripted"
    assert provider.get_answer(thickness_request()) is None
    material = provider.get_answer(
        ClarificationRequest(
            field_id="material_name",
            kind="missing_physical_input",
            question="材料名称是什么？",
            impact="材料定义会改变本构响应。",
        )
    )
    assert material is not None
    assert material.value == "steel"


def test_console_provider_returns_trimmed_answer() -> None:
    prompts: list[str] = []

    def input_fn(prompt: str) -> str:
        prompts.append(prompt)
        return "  1 mm  "

    answer = ConsoleClarificationProvider(input_fn).get_answer(thickness_request())

    assert answer is not None
    assert answer.value == "1 mm"
    assert answer.source == "console"
    assert prompts == [
        "[plate_thickness] 板厚是多少？\n"
        "Why it matters: 板厚会改变平面应力截面刚度和反力。\n> "
    ]


def test_console_provider_treats_blank_input_as_no_answer() -> None:
    provider = ConsoleClarificationProvider(lambda _: "   ")

    assert provider.get_answer(thickness_request()) is None


def test_checked_in_plate_clarification_cases_freeze_policy_boundary() -> None:
    path = (
        Path(__file__).parents[1]
        / "benchmarks"
        / "coding"
        / "plate_clarification_policy_cases.jsonl"
    )
    cases = [json.loads(line) for line in path.read_text("utf-8").splitlines()]

    assert [case["case_id"] for case in cases] == [
        "plate-element-derived",
        "plate-out-of-plane-ambiguous",
        "plate-explicit-compatible-element",
        "plate-explicit-element-conflict",
    ]
    assert {case["policy_version"] for case in cases} == {CLARIFICATION_POLICY_VERSION}
    assert [case["expected_decision"] for case in cases].count("proceed") == 2
    assert [case["expected_decision"] for case in cases].count("needs_input") == 2
    assert {
        (case["expected_kind"], tuple(case["expected_field_keywords"]))
        for case in cases
        if case["expected_decision"] == "needs_input"
    } == {
        (
            "ambiguous_physical_intent",
            ("out_of_plane",),
        ),
        (
            "conflicting_user_constraint",
            (
                "conflict",
                "idealization",
                "plane_strain",
                "element",
                "out_of_plane",
            ),
        ),
    }
    assert "CPS4R" not in cases[0]["goal"]
    assert cases[0]["expected_element_type"] is None
    assert cases[0]["allowed_element_types"] == ["CPS4", "CPS4R"]
    assert all("abaqus_api" in case["forbidden_question_topics"] for case in cases)
