import json

from abaqus_sim_agent.evaluation.foundation_public_mechanics_benchmark import (
    PILOT_DIAGNOSTIC_CASE_IDS,
    PILOT_PUBLIC_CASE_IDS,
)
from abaqus_sim_agent.evaluation.foundation_skill_paired_pilot import (
    DEFAULT_REPETITIONS,
    agent_case_payload,
    build_trial_plan,
    load_pilot_cases,
)
from abaqus_sim_agent.evaluation.foundation_skill_paired_pilot_audit import (
    severity_counts,
    summarize_pair_outcomes,
)


def test_pilot_selection_is_exactly_the_predeclared_six_cases() -> None:
    cases, rubrics = load_pilot_cases()

    assert tuple(case.case_id for case in cases) == (
        *PILOT_PUBLIC_CASE_IDS,
        *PILOT_DIAGNOSTIC_CASE_IDS,
    )
    assert {case.case_id for case in cases} == set(rubrics)
    assert [case.case_kind for case in cases] == [
        "public",
        "public",
        "public",
        "diagnostic",
        "diagnostic",
        "diagnostic",
    ]


def test_two_repeat_plan_is_24_trials_and_interleaves_profile_order() -> None:
    cases, _ = load_pilot_cases()
    plan = build_trial_plan(cases, DEFAULT_REPETITIONS)

    assert len(plan) == 24
    first_case = cases[0].case_id
    assert [
        trial.profile
        for trial in plan
        if trial.case_id == first_case and trial.repetition == 1
    ] == ["control", "foundation"]
    assert [
        trial.profile
        for trial in plan
        if trial.case_id == first_case and trial.repetition == 2
    ] == ["foundation", "control"]
    for case in cases:
        for repetition in (1, 2):
            paired = [
                trial
                for trial in plan
                if trial.case_id == case.case_id and trial.repetition == repetition
            ]
            assert {trial.profile for trial in paired} == {"control", "foundation"}
            assert {trial.case_kind for trial in paired} == {case.case_kind}


def test_agent_payload_is_identical_within_pair_and_excludes_public_reference() -> None:
    cases, _ = load_pilot_cases()
    plan = build_trial_plan(cases)
    by_case = {case.case_id: case for case in cases}

    for case in cases:
        paired = [
            trial
            for trial in plan
            if trial.case_id == case.case_id and trial.repetition == 1
        ]
        assert len(paired) == 2
        control_payload = agent_case_payload(by_case[paired[0].case_id])
        foundation_payload = agent_case_payload(by_case[paired[1].case_id])
        assert control_payload == foundation_payload
        assert set(control_payload) == {"goal", "seed_files"}

    plate = agent_case_payload(by_case["plate-with-hole-tension"])
    serialized = json.dumps(plate, ensure_ascii=False)
    assert "reference" not in serialized
    assert "source_ids" not in serialized
    assert "312.5" not in serialized
    assert "3.125" not in serialized


def test_pair_summary_keeps_fail_fail_as_tie_and_invalid_as_unknown() -> None:
    summary = summarize_pair_outcomes(
        {
            ("case-a", 1, "control"): "fail",
            ("case-a", 1, "foundation"): "pass",
            ("case-a", 2, "control"): "fail",
            ("case-a", 2, "foundation"): "fail",
            ("case-b", 1, "control"): "pass",
            ("case-b", 1, "foundation"): "fail",
            ("case-b", 2, "control"): "unknown",
            ("case-b", 2, "foundation"): "pass",
        }
    )

    assert summary["counts"] == {
        "win": 1,
        "tie": 1,
        "loss": 1,
        "unpaired_unknown": 1,
    }


def test_severity_aggregation_counts_s2_s3_only_for_valid_trials() -> None:
    result = severity_counts(
        [
            ("control", "S2", True),
            ("control", "S3", True),
            ("control", "S3", False),
            ("foundation", "S1", True),
            ("foundation", "S2", True),
            ("foundation", "S0", False),
        ]
    )

    assert result["control"]["S2_S3"] == 2
    assert result["control"]["invalid"] == 1
    assert result["foundation"]["S2_S3"] == 1
    assert result["foundation"]["invalid"] == 1
