from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import urlsplit

import httpx
import pytest
import requests.adapters
from pydantic_ai import models

from abaqus_sim_agent.coding import domain_knowledge, preferences

EVALUATION_TEST_FILES = frozenset(
    {
        "test_abaqus2024_container_probe.py",
        "test_abaqus2024_surface_traction_probe.py",
        "test_api_catalog_verified_notes.py",
        "test_api_discovery_loop.py",
        "test_behavior_measurement.py",
        "test_document_behavior_profile.py",
        "test_document_policy_behavior.py",
        "test_foundation_pilot_trace.py",
        "test_foundation_public_mechanics_benchmark.py",
        "test_foundation_skill_paired_pilot.py",
        "test_full_task_api_efficiency.py",
        "test_intent_suite_scoring.py",
        "test_model_change_evidence.py",
        "test_model_change_suite.py",
        "test_public_mechanics.py",
        "test_public_mechanics_infrastructure.py",
        "test_rag_evidence_baseline.py",
        "test_retrieval_comparison.py",
        "test_retrieval_diagnostic.py",
        "test_retrieval_heldout.py",
        "test_retrieval_input_inventory.py",
        "test_retrieval_reproduction.py",
        "test_response_diagnostic.py",
        "test_runtime_feedback_repair.py",
        "test_simple_modeling_batch.py",
        "test_task_baseline.py",
        "test_task_baseline_pilot.py",
        "test_task_baseline_service.py",
        "test_verification_run.py",
    }
)

EVALUATION_TEST_FUNCTIONS = frozenset(
    {
        (
            "test_execution_config.py",
            "test_interaction_policy_changes_only_the_declared_clarification_boundary",
        ),
        (
            "test_static_preflight.py",
            "test_static_preflight_v2_benchmark_matches_current_fixtures",
        ),
        (
            "test_static_preflight.py",
            "test_enforced_aci_benchmark_matches_current_fixtures",
        ),
    }
)

COMPONENT_TEST_FILES = frozenset(
    {
        "test_aci.py",
        "test_abaqus2024_runner.py",
        "test_abaqus2024_skill.py",
        "test_active_wall.py",
        "test_api_catalog_abqpy.py",
        "test_api_catalog_members.py",
        "test_api_catalog_official.py",
        "test_api_discovery.py",
        "test_api_docstrings.py",
        "test_api_e5.py",
        "test_api_intent.py",
        "test_api_reference_text.py",
        "test_api_traceback.py",
        "test_cae_script_policy.py",
        "test_coding_amendments.py",
        "test_coding_clarification.py",
        "test_coding_concept_lookup.py",
        "test_coding_contracts.py",
        "test_coding_dependencies.py",
        "test_coding_diagnostic_retrieval.py",
        "test_coding_harness_compat.py",
        "test_coding_model.py",
        "test_coding_probe.py",
        "test_coding_resume.py",
        "test_coding_run_lock.py",
        "test_coding_verification_port.py",
        "test_coding_workspace.py",
        "test_cross_encoder_experiment.py",
        "test_document_evidence.py",
        "test_document_policy.py",
        "test_document_policy_integration.py",
        "test_domain_knowledge.py",
        "test_edit_scope.py",
        "test_equation_context_delivery.py",
        "test_equation_prose.py",
        "test_evidence_blocks.py",
        "test_evidence_candidate.py",
        "test_evidence_units.py",
        "test_evidence_windows.py",
        "test_execution_config.py",
        "test_execution_evidence_verifier.py",
        "test_ingestion_fixes_candidate.py",
        "test_knowledge_files.py",
        "test_knowledge_scope.py",
        "test_official_example_import.py",
        "test_official_structural_examples_import.py",
        "test_official_structural_texts_import.py",
        "test_open_requests.py",
        "test_passage_selection_experiment.py",
        "test_recall_policies.py",
        "test_retrieval_bm25.py",
        "test_retrieval_documents.py",
        "test_retrieval_index.py",
        "test_runtime_evidence_service.py",
        "test_static_preflight.py",
        "test_structural_chunking.py",
        "test_structured_projection.py",
        "test_unified_candidate_backend.py",
        "test_unified_retrieval.py",
        "test_web_boundaries.py",
        "test_web_domain_knowledge.py",
    }
)

COMPONENT_TEST_FUNCTIONS = frozenset(
    {
        # test_coding_agent.py mixes product wiring with lower-level knowledge,
        # preflight, probe and diagnostic regression coverage.
        (
            "test_coding_agent.py",
            "test_untyped_cae_failure_delivers_scoped_note_and_keeps_budget_and_replay_guards",
        ),
        (
            "test_coding_agent.py",
            "test_static_preflight_pauses_once_then_unchanged_retry_runs_abaqus",
        ),
        (
            "test_coding_agent.py",
            "test_enforced_preflight_unavailable_fails_closed_before_abaqus",
        ),
        (
            "test_coding_agent.py",
            "test_enforced_runtime_policy_denial_precedes_syntax_and_api_checks",
        ),
        (
            "test_coding_agent.py",
            "test_typed_probe_protocol_exposes_conclusion_tool_and_guidance",
        ),
        (
            "test_coding_agent.py",
            "test_automatic_modes_are_invisible_until_runtime_failure",
        ),
        (
            "test_coding_agent.py",
            "test_explicit_api_discovery_requires_exact_lookup_for_evidence",
        ),
        (
            "test_coding_agent.py",
            "test_agent_parameter_lookup_is_scoped_and_independent_of_method_card",
        ),
        (
            "test_coding_agent.py",
            "test_cae_error_reports_stage_failure_and_attaches_exact_api_hint",
        ),
        (
            "test_coding_agent.py",
            "test_automatic_verified_only_hint_is_compact_and_fingerprint_is_soft",
        ),
        (
            "test_coding_agent.py",
            "test_automatic_verified_only_returns_no_hint_without_verified_note",
        ),
        (
            "test_coding_agent.py",
            "test_automatic_exact_signature_returns_compact_unique_card_without_note",
        ),
        (
            "test_coding_agent.py",
            "test_exit_zero_python_traceback_is_not_a_successful_stage",
        ),
        (
            "test_coding_agent.py",
            "test_solver_tool_payload_includes_high_signal_diagnostics",
        ),
        # CLI parsing/configuration permutations are valuable regression tests,
        # but the default suite keeps only representative end-to-end CLI paths.
        ("test_coding_cli.py", "test_parser_leaves_workspace_unset_for_cwd_default"),
        ("test_coding_cli.py", "test_noninteractive_cli_output_is_utf8"),
        (
            "test_coding_cli.py",
            "test_resume_parser_rejects_pre_release_launch_manifest",
        ),
        (
            "test_coding_cli.py",
            "test_diagnostic_advisory_requires_a_concept_index",
        ),
        (
            "test_coding_cli.py",
            "test_parser_accepts_explicit_opencode_gateway_and_model",
        ),
        ("test_coding_cli.py", "test_resolve_goal_supports_json_and_plain_text"),
        ("test_coding_cli.py", "test_load_answers_requires_string_mapping"),
        (
            "test_coding_cli.py",
            "test_cli_resolves_startup_cwd_before_model_configuration",
        ),
        ("test_coding_cli.py", "test_cli_explicit_workspace_overrides_startup_cwd"),
        ("test_coding_cli.py", "test_cli_opencode_gateway_reads_only_opencode_key"),
        ("test_coding_cli.py", "test_cli_loads_catalog_only_outside_workspace"),
        (
            "test_coding_cli.py",
            "test_cli_catalog_mode_defaults_and_validates_catalog_presence",
        ),
        (
            "test_coding_cli.py",
            "test_cli_static_preflight_requires_explicit_external_source",
        ),
        (
            "test_coding_cli.py",
            "test_cli_prepares_pinned_static_source_outside_workspace",
        ),
        (
            "test_coding_cli.py",
            "test_cli_accepts_enforced_aci_and_explicit_runtime_files",
        ),
        # Fixed-budget and optional-profile service branches are component
        # contracts; normal product smoke keeps the resume/reconcile paths.
        (
            "test_coding_service.py",
            "test_service_records_verifier_feedback_as_concept_trigger",
        ),
        (
            "test_coding_service.py",
            "test_service_converts_request_limit_into_failed_outcome",
        ),
        (
            "test_coding_service.py",
            "test_service_can_finalize_verified_artifacts_after_request_limit",
        ),
        (
            "test_coding_service.py",
            "test_service_keeps_budget_failure_when_terminal_verifier_rejects",
        ),
        (
            "test_coding_service.py",
            "test_service_stops_when_runtime_budget_is_exhausted",
        ),
        (
            "test_coding_service.py",
            "test_service_rejects_verification_with_unresolved_probe_state",
        ),
        (
            "test_coding_service.py",
            "test_service_persists_completed_tool_exchange_when_request_limit_interrupts_turn",
        ),
        (
            "test_coding_service.py",
            "test_model_budget_failure_records_terminal_artifact_hash_after_repair",
        ),
        # Streaming adapter/projection details are covered in component tests;
        # default smoke keeps visible streaming, stop and verification flows.
        (
            "test_web_streaming.py",
            "test_rename_while_streaming_is_cosmetic_versioned_and_persistent",
        ),
        (
            "test_web_streaming.py",
            "test_public_projection_ignores_reasoning_and_ordinary_tool_arguments",
        ),
        (
            "test_web_streaming.py",
            "test_go_http_streaming_adapter_retry_and_transport_close",
        ),
        # ask_user/resume remains a core smoke path, while catalog coupling,
        # CLI input forms and corruption/conflict variants stay in component.
        (
            "test_user_questions.py",
            "test_cli_catalog_questions_resume_preserves_schema_binding_and_lookup_usage",
        ),
        (
            "test_user_questions.py",
            "test_question_mixed_with_catalog_lookup_has_no_lookup_effect",
        ),
        (
            "test_user_questions.py",
            "test_wrong_answer_id_conflict_and_workspace_drift_are_rejected",
        ),
        (
            "test_user_questions.py",
            "test_cli_default_asks_outside_event_loop_and_run_lock",
        ),
        (
            "test_user_questions.py",
            "test_answer_receipt_recovers_interrupted_revision_commit",
        ),
        ("test_user_questions.py", "test_wait_time_not_charged_to_active_wall"),
        (
            "test_user_questions.py",
            "test_corrupt_question_ledger_is_not_overwritten",
        ),
        (
            "test_user_questions.py",
            "test_cli_resume_without_answer_stays_pending_without_a_model",
        ),
        (
            "test_user_questions.py",
            "test_cli_answer_file_continues_without_console",
        ),
        (
            "test_user_questions.py",
            "test_cli_new_resume_rejects_legacy_answer_mapping_before_model",
        ),
    }
)


def _test_layer(path: Path, function_name: str) -> str | None:
    name = path.name
    if (
        name.endswith("_evaluation.py")
        or name.endswith("_verification.py")
        or name.startswith("test_api_repair_")
        or name in EVALUATION_TEST_FILES
        or (name, function_name) in EVALUATION_TEST_FUNCTIONS
    ):
        return "evaluation"
    if name in COMPONENT_TEST_FILES:
        return "component"
    if (name, function_name) in COMPONENT_TEST_FUNCTIONS:
        return "component"
    return None


@pytest.hookimpl(tryfirst=True)
def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """按测试文件职责分层，同时保持冻结测试文件本身的内容和 hash 不变。"""
    for item in items:
        function_name = getattr(item, "originalname", None) or item.name
        layer = _test_layer(Path(str(item.path)), function_name)
        if layer == "evaluation":
            item.add_marker(pytest.mark.evaluation)
        elif layer == "component":
            item.add_marker(pytest.mark.component)


models.ALLOW_MODEL_REQUESTS = False


@pytest.fixture(autouse=True)
def offline_environment(monkeypatch, tmp_path):
    monkeypatch.setattr(models, "ALLOW_MODEL_REQUESTS", False)
    monkeypatch.setattr(
        preferences, "user_settings_path", lambda: tmp_path / "user-settings.json"
    )
    for name in tuple(os.environ):
        if name.startswith(("LOGFIRE_", "PHOENIX_", "OTEL_")):
            monkeypatch.delenv(name)
    for name in (
        "DEEPSEEK_API_KEY",
        "OPENCODE_API_KEY",
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(
        domain_knowledge,
        "domain_knowledge_packs_root",
        lambda: tmp_path / "user-knowledge-packs",
    )

    def check_request(request):
        if request.url.host not in {"127.0.0.1", "::1", "localhost"}:
            pytest.fail(
                f"External HTTP is disabled in tests ({request.url.host}); use MockTransport",
                pytrace=False,
            )

    sync_request = httpx.HTTPTransport.handle_request
    async_request = httpx.AsyncHTTPTransport.handle_async_request

    def handle_request(transport, request):
        check_request(request)
        return sync_request(transport, request)

    async def handle_async_request(transport, request):
        check_request(request)
        return await async_request(transport, request)

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", handle_request)
    monkeypatch.setattr(
        httpx.AsyncHTTPTransport, "handle_async_request", handle_async_request
    )
    requests_send = requests.adapters.HTTPAdapter.send

    def send(adapter, request, **kwargs):
        if urlsplit(request.url).hostname not in {"127.0.0.1", "::1", "localhost"}:
            pytest.fail(
                "External Requests/OTLP HTTP is disabled in tests", pytrace=False
            )
        return requests_send(adapter, request, **kwargs)

    monkeypatch.setattr(requests.adapters.HTTPAdapter, "send", send)


@pytest.fixture
def mock_model_requests(offline_environment, monkeypatch):
    """允许 SDK 协议测试使用模型接口，外部 HTTP 拦截仍然生效。"""
    monkeypatch.setattr(models, "ALLOW_MODEL_REQUESTS", True)
