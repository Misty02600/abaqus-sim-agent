from __future__ import annotations

import asyncio
import hashlib
import json
import os
import shutil
from pathlib import Path

import pytest

from abaqus_sim_agent.api_catalog import ApiCatalog
from abaqus_sim_agent.coding.static_preflight import (
    AbqpyBasedPyrightPreflight,
    prepare_abqpy_source,
)
from abaqus_sim_agent.evaluation.api_repair import (
    _prepare_static_source,
    _validate_case_fixtures,
    load_case_specs,
)
from abaqus_sim_agent.evaluation.runtime_feedback_repair import (
    DEFAULT_MANIFEST,
    DEFAULT_WHEEL,
    _bindings,
    _pairs,
    build_trial_plan,
    paired_outcome,
    run_pilot,
)
from abaqus_sim_agent.execution import Abaqus2024Runner


def test_runtime_feedback_manifest_is_frozen_and_balanced() -> None:
    cases = load_case_specs(DEFAULT_MANIFEST)
    assert [case.case_id for case in cases] == [
        "undefined_result_name",
        "field_output_missing_step_import",
        "closed_stream_write",
        "assembly_set_scalar_edge",
    ]
    assert [case.split for case in cases] == ["dev", "dev", "holdout", "holdout"]
    assert (
        sum(
            case.source_category == "ordinary_python_static_candidate" for case in cases
        )
        == 1
    )
    assert (
        sum(case.source_category == "runtime_only_negative_control" for case in cases)
        == 1
    )
    _validate_case_fixtures(Path(__file__).parents[1], cases)

    raw = [
        json.loads(line)
        for line in DEFAULT_MANIFEST.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    for item in raw:
        fixture = Path(__file__).parents[1] / item["fixture_dir"] / item["entry_script"]
        assert (
            hashlib.sha256(fixture.read_bytes()).hexdigest() == item["fixture_sha256"]
        )


def test_paired_plan_reverses_arm_order() -> None:
    plan = build_trial_plan(("a", "b"), repetitions=2)
    assert [(item.case_id, item.repetition, item.profile) for item in plan] == [
        ("a", 1, "off"),
        ("a", 1, "on"),
        ("b", 1, "off"),
        ("b", 1, "on"),
        ("a", 2, "on"),
        ("a", 2, "off"),
        ("b", 2, "on"),
        ("b", 2, "off"),
    ]
    with pytest.raises(ValueError, match="positive"):
        build_trial_plan(("a",), repetitions=0)


def test_paired_manifest_binds_complete_local_runtime_chain(tmp_path: Path) -> None:
    if not DEFAULT_WHEEL.is_file():
        pytest.skip("fixed abqpy source is unavailable")
    catalog = tmp_path / "api-catalog"
    ApiCatalog([], runtime_release="2024").write(catalog)
    bindings = _bindings(
        DEFAULT_MANIFEST,
        load_case_specs(DEFAULT_MANIFEST),
        DEFAULT_WHEEL,
        catalog,
    )
    for required in (
        "src/abaqus_sim_agent/coding/agent.py",
        "src/abaqus_sim_agent/coding/runtime_tools.py",
        "src/abaqus_sim_agent/execution/abaqus2024.py",
        "src/abaqus_sim_agent/evaluation/api_repair.py",
        "src/abaqus_sim_agent/verification/api_repair.py",
        "src/abaqus_sim_agent/skills/abaqus-2024-coding/SKILL.md",
        "pyproject.toml",
        "uv.lock",
        "abqpy_static_wheel",
        "api_catalog/manifest.json",
        "api_catalog/methods.jsonl",
    ):
        assert required in bindings


@pytest.mark.parametrize(
    ("control", "treatment", "expected"),
    [
        ("fail", "pass", "win"),
        ("pass", "fail", "loss"),
        ("pass", "pass", "tie"),
        ("fail", "fail", "tie"),
        ("unknown", "pass", "unpaired_unknown"),
    ],
)
def test_paired_outcome(control, treatment, expected) -> None:
    assert paired_outcome(control, treatment) == expected


def test_pair_report_uses_profile_identity_not_run_order() -> None:
    observations = [
        {"case_id": "case", "repetition": 1, "profile": "on", "verdict": "pass"},
        {"case_id": "case", "repetition": 1, "profile": "off", "verdict": "fail"},
    ]
    assert _pairs(observations, ("case",), 1) == [
        {
            "case_id": "case",
            "repetition": 1,
            "control": "fail",
            "treatment": "pass",
            "outcome": "win",
        }
    ]


def test_static_source_profile_contract(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="must not receive"):
        _prepare_static_source("off", tmp_path / "wheel.whl", tmp_path / "cache")
    with pytest.raises(ValueError, match="requires"):
        _prepare_static_source("on", None, None)


def test_pilot_rejects_missing_provider_key_before_creating_batch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.delenv("OPENCODE_API_KEY", raising=False)
    root = tmp_path / "batch"
    with pytest.raises(ValueError, match="Provider API key"):
        asyncio.run(
            run_pilot(
                root,
                repetitions=1,
                api_catalog_dir=tmp_path / "missing-catalog",
                static_preflight_cache_dir=tmp_path / "cache",
            )
        )
    assert not root.exists()


def test_checked_in_cases_have_expected_static_qualification(tmp_path: Path) -> None:
    repository = Path(__file__).parents[1]
    wheel = repository / "var/api-catalog/sources/abqpy-2024.10-py3-none-any.whl"
    if not wheel.is_file():
        pytest.skip("fixed abqpy source is unavailable")
    source = prepare_abqpy_source(wheel, tmp_path / "cache")
    expected = {
        "undefined_result_name": {"reportUndefinedVariable"},
        "field_output_missing_step_import": set(),
        "closed_stream_write": set(),
        "assembly_set_scalar_edge": {"reportArgumentType"},
    }
    for case in load_case_specs(DEFAULT_MANIFEST):
        fixture = repository / case.fixture_dir / case.entry_script
        checker = AbqpyBasedPyrightPreflight(source, tmp_path / case.case_id)
        report = asyncio.run(checker.inspect(fixture.parent, fixture.name))
        assert {item.rule for item in report.diagnostics} == expected[case.case_id]


@pytest.mark.abaqus
@pytest.mark.skipif(
    os.environ.get("ABAQUS_LICENSED_TESTS") != "1",
    reason="set ABAQUS_LICENSED_TESTS=1 to use the local Abaqus license",
)
@pytest.mark.parametrize(
    "case",
    load_case_specs(DEFAULT_MANIFEST),
    ids=lambda case: case.case_id,
)
def test_broken_runtime_feedback_fixture_fails_in_abaqus2024(
    case, tmp_path: Path
) -> None:
    launcher = Path(r"D:\SIMULIA\Commands\abq2024.bat")
    if not launcher.is_file():
        pytest.skip("Abaqus 2024 launcher is unavailable")
    repository = Path(__file__).parents[1]
    fixture_root = repository / case.fixture_dir
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    shutil.copyfile(fixture_root / case.entry_script, workspace / case.entry_script)
    runner = Abaqus2024Runner(
        workspace.resolve(), tmp_path / "state", launcher=launcher.resolve(strict=True)
    )
    result = asyncio.run(
        runner.run_cae_script(case.entry_script, timeout_seconds=300.0)
    )
    assert not result.timed_out
    assert not (workspace / case.result_file).exists()
    combined = (result.stdout + "\n" + result.stderr).casefold()
    assert not [
        term for term in case.expected_error_terms if term.casefold() not in combined
    ]
