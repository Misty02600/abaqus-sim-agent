import json
from importlib.metadata import version
from pathlib import Path

import httpx
import pytest
from openai import AsyncOpenAI
from pydantic import SecretStr
from pydantic_ai.messages import ModelRequest, UserPromptPart
from pydantic_ai.models import ModelRequestParameters
from pydantic_ai.tools import ToolDefinition

from abaqus_sim_agent.coding.contracts import CodingRunBudget
from abaqus_sim_agent.coding.model import DeepSeekGateway, create_deepseek_chat_model
from abaqus_sim_agent.evaluation import document_policy_behavior as behavior
from abaqus_sim_agent.evaluation.behavior_measurement import file_digest
from abaqus_sim_agent.evaluation.document_behavior_audit import build_audit, rubric
from abaqus_sim_agent.evaluation.document_behavior_profile import (
    CALIBRATED_PROFILE,
    model_settings,
)
from abaqus_sim_agent.evaluation.document_behavior_spec import (
    prepare_spec,
    verify_runtime_manifest,
    verify_spec,
)
from abaqus_sim_agent.evaluation.document_policy_behavior import (
    SETTINGS,
    execute_batch,
    http_attempt_recorder,
)
from tests.test_document_policy_integration import bound
from tests.test_runtime_evidence_service import evidence


@pytest.mark.usefixtures("mock_model_requests")
@pytest.mark.asyncio
async def test_actual_go_http_body_only_differs_in_output_cap(monkeypatch):
    bodies = []

    def reply(request):
        bodies.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "id": "offline",
                "object": "chat.completion",
                "created": 0,
                "model": "deepseek-v4-flash",
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": "stop",
                        "message": {"role": "assistant", "content": "done"},
                    }
                ],
                "usage": {
                    "prompt_tokens": 10,
                    "completion_tokens": 1,
                    "total_tokens": 11,
                },
            },
        )

    transport = httpx.AsyncClient(transport=httpx.MockTransport(reply))
    monkeypatch.setattr(
        "abaqus_sim_agent.coding.model.AsyncOpenAI",
        lambda **kwargs: AsyncOpenAI(**kwargs, http_client=transport),
    )
    model, client = create_deepseek_chat_model(
        gateway=DeepSeekGateway.OPENCODE_GO,
        model_name="deepseek-v4-flash",
        api_key=SecretStr("offline-only"),
        http_retries=0,
        conversation_id="offline-profile-parity",
    )
    messages = [ModelRequest(parts=[UserPromptPart("same task")])]
    parameters = ModelRequestParameters(
        function_tools=[
            ToolDefinition(
                name="write_file",
                parameters_json_schema={
                    "type": "object",
                    "properties": {"content": {"type": "string"}},
                },
            )
        ]
    )
    try:
        await model.request(messages, model_settings("legacy"), parameters)
        await model.request(messages, model_settings("calibrated"), parameters)
    finally:
        await client.close()
    legacy, calibrated = bodies
    cap_key = (
        "max_completion_tokens" if "max_completion_tokens" in legacy else "max_tokens"
    )
    assert legacy.pop(cap_key) == 2400
    assert "max_tokens" not in calibrated
    assert "max_completion_tokens" not in calibrated
    assert legacy == calibrated  # 同时检查 messages、tools、thinking 等没有暗中变化。
    assert len(calibrated["tools"]) == 1


def test_legacy_settings_unchanged_and_no_shared_mutable_settings():
    assert model_settings("legacy") == SETTINGS
    settings = model_settings("calibrated")
    settings["extra_body"]["thinking"]["type"] = "enabled"
    assert model_settings("calibrated")["extra_body"]["thinking"]["type"] == "disabled"
    with pytest.raises(ValueError, match="unknown"):
        model_settings("invented")


@pytest.mark.asyncio
async def test_http_attempt_is_recorded_even_without_response_and_without_headers(
    tmp_path,
):
    def timeout(request):
        raise httpx.ReadTimeout("offline injected timeout", request=request)

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(timeout),
        event_hooks={"request": [http_attempt_recorder(tmp_path)]},
    ) as client:
        with pytest.raises(httpx.ReadTimeout):
            await client.post(
                "https://offline.invalid/chat?secret=hidden",
                json={"messages": []},
                headers={"Authorization": "secret-test-only"},
            )
    recorded = (tmp_path / "http-attempts/001.json").read_text(encoding="utf-8")
    assert json.loads(recorded) == {
        "method": "POST",
        "url_path": "/chat",
        "body": {"messages": []},
    }
    assert "secret" not in recorded


def test_offline_spec_freezes_twenty_four_slots_and_refuses_source_drift(tmp_path):
    def write(name, value):
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value, encoding="utf-8")
        return path

    cases = write(
        "benchmarks/coding/document_policy_behavior_cases_v1.json",
        json.dumps(
            {"cases": [{"id": f"case-{i}", "goal": "fixture"} for i in range(8)]}
        ),
    )
    policy = write("benchmarks/rag/document_policy_spec_v1.json", "{}")
    write("uv.lock", "fixture")
    write("src/abaqus_sim_agent/skills/abaqus-2024-coding/SKILL.md", "fixture")
    write("src/abaqus_sim_agent/example.py", "x = 1\n")
    source = write("official.html", "official fixture")
    seal = write(
        "legacy-seal.json",
        json.dumps(
            {
                "profile": "document-policy-live-behavior-v1",
                "cases_sha256": file_digest(cases),
                "spec_sha256": file_digest(policy),
                "source_files": {str(source): file_digest(source)},
                "base_backend_binding": {},
                "model": {"settings": model_settings("legacy"), "http_retries": 0},
                "task_instructions": "same instructions",
                "budget": {
                    "max_abaqus_calls": 0,
                    "max_model_requests": 12,
                    "wall_time_seconds": 360,
                },
            }
        ),
    )
    spec = prepare_spec(tmp_path, seal)
    assert not spec["execution_authorized"]
    assert len({t["trial_id"] for t in spec["planned_trials"]}) == 24
    assert spec["maximum_model_requests"] == 288
    assert spec["maximum_trial_wall_seconds_sum"] == 8640
    assert "max_tokens" not in spec["model"]["settings"]
    assert spec["implementation_and_inputs"]["src/abaqus_sim_agent/example.py"]
    spec_path = write("experiment.json", json.dumps(spec))
    assert verify_spec(tmp_path, spec_path) == spec
    added = write("src/abaqus_sim_agent/extra.py", "pass\n")
    with pytest.raises(ValueError, match="seal drift"):
        verify_spec(tmp_path, spec_path)
    added.unlink()
    assert verify_spec(tmp_path, spec_path) == spec
    source_code = tmp_path / "src/abaqus_sim_agent/example.py"
    original_code = source_code.read_bytes()
    source_code.unlink()
    with pytest.raises(ValueError, match="seal drift"):
        verify_spec(tmp_path, spec_path)
    source_code.write_bytes(original_code)
    source.write_text("changed", encoding="utf-8")
    with pytest.raises(ValueError, match="source changed"):
        prepare_spec(tmp_path, seal)


@pytest.mark.asyncio
async def test_calibrated_rejects_missing_or_stale_seal_before_credentials(
    tmp_path, monkeypatch
):
    def credentials(*args):
        pytest.fail("credential access before seal validation")

    monkeypatch.setattr(
        "abaqus_sim_agent.evaluation.document_policy_behavior.take_gateway_api_key_from_environment",
        credentials,
    )
    with pytest.raises(ValueError, match="requires experiment_spec"):
        await execute_batch(
            tmp_path / "out",
            tmp_path / "cases",
            tmp_path / "policy",
            measurement_profile="calibrated",
        )
    stale = tmp_path / "stale.json"
    stale.write_text("{}", encoding="utf-8")
    with pytest.raises(KeyError, match="historical_source_seal"):
        await execute_batch(
            tmp_path / "out",
            tmp_path / "cases",
            tmp_path / "policy",
            measurement_profile="calibrated",
            experiment_spec=stale,
            repetition=1,
        )
    assert not (tmp_path / "out").exists()


def test_runtime_manifest_must_match_sealed_configuration():
    runtime = {
        "profile": "v2",
        "base_backend_binding": {},
        "model": {"settings": model_settings("calibrated")},
        "task_instructions": "fixed",
        "budget": {"max_model_requests": 12},
        "pydantic_ai_version": "fixture",
        "harness_version": "fixture",
        "source_files": {},
        "planning": False,
        "api_catalog": "off",
        "real_ranking_calls": 0,
    }
    experiment = {
        key: value
        for key, value in runtime.items()
        if key not in ("budget", "planning", "api_catalog", "real_ranking_calls")
    }
    experiment["budget_per_trial"] = runtime["budget"]
    verify_runtime_manifest(experiment, runtime)
    with pytest.raises(ValueError, match="model"):
        verify_runtime_manifest(
            experiment, runtime | {"model": {"settings": model_settings("legacy")}}
        )
    with pytest.raises(ValueError, match="capability"):
        verify_runtime_manifest(experiment, runtime | {"planning": True})


@pytest.mark.usefixtures("mock_model_requests")
@pytest.mark.asyncio
async def test_calibrated_harness_batch_to_predeclared_review_without_network(
    tmp_path, monkeypatch
):
    binding = bound(tmp_path)
    binding.spec.candidate.mkdir()
    binding.spec.scope_rules.write_text("{}", encoding="utf-8")
    source = tmp_path / "docs/SIMACAEMODRefMap/simamod-c-conventions.htm"
    source.parent.mkdir(parents=True)
    source.write_text("offline source fixture", encoding="utf-8")
    (binding.spec.candidate / "manifest.json").write_text(
        json.dumps({"source_root": str(tmp_path / "docs"), "encoder_files": {}}),
        encoding="utf-8",
    )
    units = binding.spec.candidate / "units.jsonl"
    units.write_text("{}", encoding="utf-8")
    policy_path = tmp_path / "policy.json"
    policy_path.write_text("{}", encoding="utf-8")
    cases_path = (
        Path(__file__).parents[1]
        / "benchmarks/coding/document_policy_behavior_cases_v1.json"
    )
    cases = json.loads(cases_path.read_bytes())["cases"]
    budget = CodingRunBudget(
        max_model_requests=12,
        max_api_lookup_calls=0,
        max_concept_lookup_calls=2,
        max_diagnostic_retrieval_calls=0,
        max_abaqus_calls=0,
        max_solver_calls=0,
        wall_time_seconds=360.0,
    )
    experiment = {
        "profile": CALIBRATED_PROFILE,
        "base_backend_binding": binding.model_dump(mode="json"),
        "model": {
            "provider": "opencode-go",
            "name": "deepseek-v4-flash",
            "http_retries": 0,
            "http_timeout_seconds": 60,
            "settings": model_settings("calibrated"),
        },
        "task_instructions": behavior.TASK_INSTRUCTIONS,
        "budget_per_trial": budget.model_dump(mode="json"),
        "pydantic_ai_version": version("pydantic-ai-slim"),
        "harness_version": version("pydantic-ai-harness"),
        "source_files": {
            str(p): file_digest(p) for p in (source, units, binding.spec.scope_rules)
        },
        "implementation_and_inputs": {
            "benchmarks/coding/document_policy_behavior_cases_v1.json": file_digest(
                cases_path
            ),
            "benchmarks/rag/document_policy_spec_v1.json": file_digest(policy_path),
        },
        "rubrics": [rubric(c).model_dump(mode="json") for c in cases],
        "planned_trials": [
            {"trial_id": c["id"] + "-r1", "repetition": 1} for c in cases
        ],
    }
    experiment_path = tmp_path / "experiment.json"
    experiment_path.write_text(json.dumps(experiment), encoding="utf-8")
    monkeypatch.setattr(behavior, "verify_spec", lambda *args: experiment)
    monkeypatch.setattr(
        behavior, "load_document_policy_spec", lambda *args: binding.spec
    )
    monkeypatch.setattr(behavior, "bind_document_policy", lambda *args: binding)
    monkeypatch.setattr(
        behavior,
        "prepare_sources",
        lambda *args: ({"units": evidence()}, lambda s: len(s.split())),
    )
    monkeypatch.setattr(behavior, "validate_evidence", lambda *args: None)
    monkeypatch.setattr(
        behavior,
        "take_gateway_api_key_from_environment",
        lambda *args: SecretStr("offline-fixture"),
    )
    bodies = []

    def reply(request):
        body = json.loads(request.content)
        bodies.append(body)
        assert "max_tokens" not in body
        assert "max_completion_tokens" not in body
        names = {t["function"]["name"] for t in body["tools"]}
        assert {"search_abaqus_docs", "record_document_assessment"} <= names
        assert "run_command" not in names
        return httpx.Response(
            200,
            json={
                "id": "offline",
                "object": "chat.completion",
                "created": 0,
                "model": "deepseek-v4-flash",
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": "tool_calls",
                        "message": {
                            "role": "assistant",
                            "content": None,
                            "tool_calls": [
                                {
                                    "id": "call-failure",
                                    "type": "function",
                                    "function": {
                                        "name": "final_result_FailedDecision",
                                        "arguments": json.dumps(
                                            {
                                                "status": "failed",
                                                "failure_stage": "knowledge_evidence",
                                                "summary": "Injected deterministic stop, not a behavior success.",
                                            }
                                        ),
                                    },
                                }
                            ],
                        },
                    }
                ],
                "usage": {
                    "prompt_tokens": 10,
                    "completion_tokens": 5,
                    "total_tokens": 15,
                },
            },
        )

    monkeypatch.setattr(
        "abaqus_sim_agent.coding.model.AsyncOpenAI",
        lambda **kwargs: AsyncOpenAI(
            **kwargs,
            http_client=httpx.AsyncClient(transport=httpx.MockTransport(reply)),
        ),
    )
    output = tmp_path / "batch"
    await execute_batch(
        output,
        cases_path,
        policy_path,
        measurement_profile="calibrated",
        experiment_spec=experiment_path,
        repetition=1,
    )
    assert len(bodies) == 8
    report = json.loads((output / "report.json").read_bytes())
    assert all(
        o["http_attempts"] == o["http_responses"] == 1 for o in report["observations"]
    )
    assert all(o["runtime_tool_attempts"] == 0 for o in report["observations"])
    annotations = []
    for case in cases:
        annotation = {
            "case_id": case["id"],
            "completion": "not_completed",
            "task": "fail",
            "truthfulness": "unknown",
            "protocol": "unknown",
            "opportunity": "not_observed",
            "reason": "Mock provider always stops without doing the requested work.",
        }
        if case.get("physical_input"):
            annotation["protocol"] = "fail"
            annotation["clarification_facts"] = {
                "required_field_requested": "fail",
                "impact_explained": "fail",
                "fabricated_input": "no",
                "dependent_work_before_clarification": "no",
                "user_forbidden_work": "no",
                "any_tool_before_clarification": True,
            }
        annotations.append(annotation)
    review_path = tmp_path / "review.json"
    review_path.write_text(
        json.dumps(
            {
                "schema_version": "document-behavior-host-review-v2",
                "review_kind": "predeclared_rubric_host_review",
                "reviewer": "deterministic fixture",
                "batch_seal_sha256": file_digest(output / "seal.json"),
                "batch_report_sha256": file_digest(output / "report.json"),
                "cases": annotations,
            }
        ),
        encoding="utf-8",
    )
    audit = build_audit(output, review_path)
    assert audit["summary"]["started"] == 8
    assert audit["summary"]["joint_pass"] == 0
    assert audit["summary"]["cost_all_started"]["http_attempts"] == {
        "known_sum": 8,
        "unknown_trials": 0,
    }
    assert audit["scores"][0]["review"]["trial_id"].endswith("-r1")
    assert audit["historical_review_unchanged"] is None
