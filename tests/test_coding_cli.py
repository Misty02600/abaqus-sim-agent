import json
import zipfile
from pathlib import Path

import pytest
from pydantic_ai.messages import ModelMessage, ModelResponse, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from abaqus_sim_agent.api_catalog import ApiCatalog, ApiMethodCard
from abaqus_sim_agent.coding.amendments import load_amendment_ledger
from abaqus_sim_agent.coding.cli import (
    _api_catalog_binding,
    _LaunchManifest,
    _load_answers,
    _load_api_catalog,
    _load_launch_manifest,
    _prepare_static_preflight_source,
    _product_verifier,
    _resolve_api_catalog_mode,
    _resolve_concept_retrieval_mode,
    _resolve_goal,
    _runtime_script_policy,
    _validate_resume_arguments,
    build_parser,
    run_cli,
)
from abaqus_sim_agent.coding.execution_verifier import ExecutionEvidenceVerifier
from abaqus_sim_agent.coding.model import DeepSeekGateway
from abaqus_sim_agent.coding.resume import (
    load_request_for_resume,
    load_resume_state,
)


def test_parser_leaves_workspace_unset_for_cwd_default() -> None:
    args = build_parser().parse_args(["--goal", "test"])

    assert args.workspace is None
    assert args.launcher is None
    assert args.model_provider is None
    assert args.model is None
    assert args.http_retries is None
    assert args.api_catalog_dir is None
    assert args.api_catalog_mode is None
    assert args.diagnostic_retrieval_mode is None
    assert args.static_preflight_mode == "off"
    assert args.abqpy_static_wheel is None
    assert args.static_preflight_cache_dir is None
    assert args.allowed_cae_script is None
    assert args.allowed_abaqus_python_script is None
    assert args.allowed_solver_input is None


def test_product_cli_uses_execution_evidence_not_plate_oracle() -> None:
    assert isinstance(_product_verifier(), ExecutionEvidenceVerifier)


def test_resume_parser_reuses_immutable_launch_manifest(tmp_path: Path) -> None:
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    args = build_parser().parse_args(["--resume", str(state_dir)])

    _validate_resume_arguments(args)
    assert args.resume == state_dir

    revised = build_parser().parse_args(
        ["--resume", str(state_dir), "--goal", "new goal"]
    )
    _validate_resume_arguments(revised)

    conflicting = build_parser().parse_args(
        ["--resume", str(state_dir), "--goal", "new goal", "--workspace", "."]
    )
    with pytest.raises(ValueError, match="run identity"):
        _validate_resume_arguments(conflicting)

    diagnostic_override = build_parser().parse_args(
        [
            "--resume",
            str(state_dir),
            "--diagnostic-retrieval-mode",
            "advisory",
        ]
    )
    with pytest.raises(ValueError, match="diagnostic-retrieval-mode"):
        _validate_resume_arguments(diagnostic_override)

    manifest = _LaunchManifest(
        model_provider=DeepSeekGateway.OPENCODE_GO,
        model_name="deepseek-v4-flash",
        http_retries=10,
        skill_library=tmp_path.resolve(),
        diagnostic_retrieval_mode="advisory",
    )
    (state_dir / "launch.json").write_text(
        manifest.model_dump_json(indent=2),
        encoding="utf-8",
    )

    assert _load_launch_manifest(state_dir) == manifest


def test_diagnostic_advisory_requires_a_concept_index() -> None:
    assert _resolve_concept_retrieval_mode("off", "off", None) == "off"
    with pytest.raises(ValueError, match="active retrieval mode"):
        _resolve_concept_retrieval_mode("off", "advisory", None)


@pytest.mark.parametrize("gateway", ["deepseek", "opencode-go"])
def test_cli_resume_reloads_original_task_after_provider_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    gateway: str,
) -> None:
    workspace = tmp_path / "workspace"
    state_dir = tmp_path / "state"
    workspace.mkdir()
    factory_calls = 0
    factory_arguments: list[dict[str, object]] = []
    unavailable_calls = 0

    def unavailable_model(
        _messages: list[ModelMessage],
        _info: AgentInfo,
    ) -> ModelResponse:
        nonlocal unavailable_calls
        unavailable_calls += 1
        if unavailable_calls == 1:
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        "write_file",
                        {"path": "partial.py", "content": "partial = True\n"},
                        tool_call_id="write-before-cli-interruption",
                    )
                ]
            )
        raise ConnectionError("simulated gateway outage")

    recovered_calls = 0

    def recovered_model(
        messages: list[ModelMessage],
        info: AgentInfo,
    ) -> ModelResponse:
        nonlocal recovered_calls
        recovered_calls += 1
        assert "Create a resumable script" in str(messages)
        if recovered_calls == 1:
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        "write_file",
                        {"path": "result.py", "content": "value = 1\n"},
                        tool_call_id="write-cli-resume-result",
                    )
                ]
            )
        output_tool = next(
            tool
            for tool in info.output_tools
            if tool.name.endswith("ReadyForVerificationDecision")
        )
        return ModelResponse(
            parts=[
                ToolCallPart(
                    output_tool.name,
                    {
                        "status": "ready_for_verification",
                        "deliverables": [{"relative_path": "result.py"}],
                        "summary": "Recovered task is ready.",
                    },
                )
            ]
        )

    class _Client:
        async def close(self) -> None:
            return None

    def model_factory(**kwargs: object) -> tuple[FunctionModel, _Client]:
        nonlocal factory_calls
        factory_calls += 1
        factory_arguments.append(kwargs)
        function = unavailable_model if factory_calls == 1 else recovered_model
        return FunctionModel(function), _Client()

    monkeypatch.setattr(
        "abaqus_sim_agent.coding.cli.take_gateway_api_key_from_environment",
        lambda _provider: "test-key",
    )
    monkeypatch.setattr(
        "abaqus_sim_agent.coding.cli.create_deepseek_chat_model",
        model_factory,
    )

    first_exit = run_cli(
        [
            "--goal",
            "Create a resumable script",
            "--workspace",
            str(workspace),
            "--state-dir",
            str(state_dir),
            "--run-id",
            "cli-resume-test",
            "--model-provider",
            gateway,
        ]
    )
    assert first_exit == 1
    immutable_request = (state_dir / "request.json").read_bytes()
    immutable_launch = (state_dir / "launch.json").read_bytes()

    resumed_exit = run_cli(
        [
            "--resume",
            str(state_dir),
            "--wall-time-seconds",
            "5400",
            "--max-model-requests",
            "40",
            "--model",
            "deepseek-v4-flash" if gateway == "opencode-go" else "amended-model",
            "--http-retries",
            "7",
            "--amendment-reason",
            "User expanded the interrupted run budget",
        ]
    )

    assert resumed_exit == 0
    assert factory_calls == 2
    outcome = json.loads((state_dir / "outcome.json").read_text(encoding="utf-8"))
    assert outcome["usage"]["model_requests"] == 3
    assert factory_arguments[1]["model_name"] == (
        "deepseek-v4-flash" if gateway == "opencode-go" else "amended-model"
    )
    assert factory_arguments[0]["execution_config"].reasoning_policy == (
        "enabled_high" if gateway == "opencode-go" else "provider_default"
    )
    assert (
        factory_arguments[1]["execution_config"].reasoning_policy
        == factory_arguments[0]["execution_config"].reasoning_policy
    )
    assert factory_arguments[1]["http_retries"] == 7
    assert (state_dir / "request.json").read_bytes() == immutable_request
    assert (state_dir / "launch.json").read_bytes() == immutable_launch
    effective = load_request_for_resume(state_dir)
    assert effective.budget.wall_time_seconds == 5400
    assert effective.budget.max_model_requests == 40
    ledger = load_amendment_ledger(state_dir, run_id="cli-resume-test")
    assert ledger.sequence == 1
    assert ledger.amendments[0].kind == "operational"
    assert outcome["status"] == "completed"

    protected = {
        name: (state_dir / name).read_bytes()
        for name in (
            "launch.json",
            "request.json",
            "initial_snapshot.json",
            "resume_state.json",
            "outcome.json",
        )
    }
    duplicate_exit = run_cli(
        [
            "--goal",
            "A different task must not overwrite the old state",
            "--workspace",
            str(workspace),
            "--state-dir",
            str(state_dir),
            "--run-id",
            "cli-resume-test",
            "--model-provider",
            "opencode-go",
        ]
    )

    assert duplicate_exit == 1
    assert {name: (state_dir / name).read_bytes() for name in protected} == protected


def test_cli_task_revision_preserves_old_outcome_and_reverifies_fresh_work(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    state_dir = tmp_path / "state"
    workspace.mkdir()
    factory_calls = 0
    initial_calls = 0

    def initial_model(
        _messages: list[ModelMessage],
        info: AgentInfo,
    ) -> ModelResponse:
        nonlocal initial_calls
        initial_calls += 1
        if initial_calls == 1:
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        "write_file",
                        {"path": "old.py", "content": "value = 'old'\n"},
                        tool_call_id="write-old-revision",
                    )
                ]
            )
        output_tool = next(
            tool
            for tool in info.output_tools
            if tool.name.endswith("ReadyForVerificationDecision")
        )
        return ModelResponse(
            parts=[
                ToolCallPart(
                    output_tool.name,
                    {
                        "status": "ready_for_verification",
                        "deliverables": [{"relative_path": "old.py"}],
                        "summary": "Initial task completed.",
                    },
                )
            ]
        )

    revised_calls = 0

    def revised_model(
        messages: list[ModelMessage],
        info: AgentInfo,
    ) -> ModelResponse:
        nonlocal revised_calls
        revised_calls += 1
        assert "Create a different fresh artifact" in str(messages)
        output_tool = next(
            tool
            for tool in info.output_tools
            if tool.name.endswith("ReadyForVerificationDecision")
        )
        if revised_calls == 1:
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        output_tool.name,
                        {
                            "status": "ready_for_verification",
                            "deliverables": [{"relative_path": "old.py"}],
                            "summary": "Try to reuse the old result.",
                        },
                    )
                ]
            )
        if revised_calls == 2:
            assert "Declared deliverable verification failed" in str(messages)
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        "write_file",
                        {"path": "new.py", "content": "value = 'new'\n"},
                        tool_call_id="write-new-revision",
                    )
                ]
            )
        return ModelResponse(
            parts=[
                ToolCallPart(
                    output_tool.name,
                    {
                        "status": "ready_for_verification",
                        "deliverables": [{"relative_path": "new.py"}],
                        "summary": "Revised task completed.",
                    },
                )
            ]
        )

    class _Client:
        async def close(self) -> None:
            return None

    def model_factory(**_kwargs: object) -> tuple[FunctionModel, _Client]:
        nonlocal factory_calls
        factory_calls += 1
        return (
            FunctionModel(initial_model if factory_calls == 1 else revised_model),
            _Client(),
        )

    monkeypatch.setattr(
        "abaqus_sim_agent.coding.cli.take_gateway_api_key_from_environment",
        lambda _provider: "test-key",
    )
    monkeypatch.setattr(
        "abaqus_sim_agent.coding.cli.create_deepseek_chat_model",
        model_factory,
    )

    assert (
        run_cli(
            [
                "--goal",
                "Create the initial artifact",
                "--workspace",
                str(workspace),
                "--state-dir",
                str(state_dir),
                "--run-id",
                "cli-task-revision",
            ]
        )
        == 0
    )
    original_request = (state_dir / "request.json").read_bytes()
    original_launch = (state_dir / "launch.json").read_bytes()
    original_outcome = (state_dir / "outcome.json").read_bytes()
    old_verification = b'{"revision": 0, "passed": true}'
    (state_dir / "verification.json").write_bytes(old_verification)

    assert (
        run_cli(
            [
                "--resume",
                str(state_dir),
                "--goal",
                "Create a different fresh artifact",
                "--amendment-reason",
                "User changed the task",
            ]
        )
        == 0
    )

    assert factory_calls == 2
    assert revised_calls == 3
    assert (state_dir / "request.json").read_bytes() == original_request
    assert (state_dir / "launch.json").read_bytes() == original_launch
    assert (
        state_dir / "revisions" / "task-0000" / "sequence-0000-outcome.json"
    ).read_bytes() == original_outcome
    assert not (state_dir / "verification.json").exists()
    assert (
        state_dir / "revisions" / "task-0000" / "sequence-0000-verification.json"
    ).read_bytes() == old_verification
    outcome = json.loads((state_dir / "outcome.json").read_text(encoding="utf-8"))
    assert outcome["status"] == "completed"
    assert outcome["task_revision"] == 1
    assert outcome["amendment_sequence"] == 1
    artifacts = {
        artifact["relative_path"]: artifact["fresh"]
        for artifact in outcome["artifacts"]
    }
    assert artifacts == {"new.py": True, "old.py": False}
    effective = load_request_for_resume(state_dir)
    assert effective.goal == "Create a different fresh artifact"
    resume_state = load_resume_state(state_dir)
    assert resume_state.task_revision == 1
    assert resume_state.amendment_sequence == 1


def test_parser_accepts_explicit_opencode_gateway_and_model() -> None:
    args = build_parser().parse_args(
        [
            "--goal",
            "test",
            "--model-provider",
            "opencode-zen",
            "--model",
            "deepseek-v4-flash-free",
        ]
    )

    assert args.model_provider is DeepSeekGateway.OPENCODE_ZEN
    assert args.model == "deepseek-v4-flash-free"

    go_args = build_parser().parse_args(
        ["--goal", "test", "--model-provider", "opencode-go"]
    )
    assert go_args.model_provider is DeepSeekGateway.OPENCODE_GO


def test_resolve_goal_supports_json_and_plain_text(tmp_path: Path) -> None:
    json_path = tmp_path / "request.json"
    json_path.write_text(json.dumps({"goal": " JSON goal "}), encoding="utf-8")
    text_path = tmp_path / "request.txt"
    text_path.write_text(" plain goal ", encoding="utf-8")

    assert _resolve_goal(None, json_path) == "JSON goal"
    assert _resolve_goal(None, text_path) == "plain goal"
    with pytest.raises(ValueError, match="either"):
        _resolve_goal("inline", json_path)


def test_load_answers_requires_string_mapping(tmp_path: Path) -> None:
    path = tmp_path / "answers.json"
    path.write_text('{"plate_thickness": "1 mm"}', encoding="utf-8")

    assert _load_answers(path) == {"plate_thickness": "1 mm"}

    path.write_text('{"plate_thickness": 1}', encoding="utf-8")
    with pytest.raises(ValueError, match="mapping"):
        _load_answers(path)


def test_cli_resolves_startup_cwd_before_model_configuration(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    workspace = tmp_path / "workspace"
    state_dir = tmp_path / "state"
    workspace.mkdir()
    monkeypatch.chdir(workspace)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)

    exit_code = run_cli(
        [
            "--goal",
            "test",
            "--state-dir",
            str(state_dir),
            "--run-id",
            "cli-test",
        ]
    )

    output = capsys.readouterr().out
    assert exit_code == 1
    assert f"Workspace: {workspace.resolve()}" in output
    assert "DEEPSEEK_API_KEY must not be empty" in output


def test_cli_explicit_workspace_overrides_startup_cwd(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    startup_cwd = tmp_path / "startup"
    workspace = tmp_path / "explicit-workspace"
    state_dir = tmp_path / "state"
    startup_cwd.mkdir()
    workspace.mkdir()
    monkeypatch.chdir(startup_cwd)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)

    exit_code = run_cli(
        [
            "--goal",
            "test",
            "--workspace",
            str(workspace),
            "--state-dir",
            str(state_dir),
            "--run-id",
            "cli-workspace-test",
        ]
    )

    output = capsys.readouterr().out
    assert exit_code == 1
    assert f"Workspace: {workspace.resolve()}" in output
    assert f"Workspace: {startup_cwd.resolve()}" not in output


def test_cli_opencode_gateway_reads_only_opencode_key(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    workspace = tmp_path / "workspace"
    state_dir = tmp_path / "state"
    workspace.mkdir()
    monkeypatch.delenv("OPENCODE_API_KEY", raising=False)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "must-not-be-used")

    exit_code = run_cli(
        [
            "--goal",
            "test",
            "--workspace",
            str(workspace),
            "--state-dir",
            str(state_dir),
            "--run-id",
            "cli-opencode-test",
            "--model-provider",
            "opencode-zen",
        ]
    )

    output = capsys.readouterr().out
    assert exit_code == 1
    assert "OPENCODE_API_KEY must not be empty" in output


def test_cli_loads_catalog_only_outside_workspace(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    catalog_dir = tmp_path / "catalog"
    ApiCatalog(
        [
            ApiMethodCard(
                card_id="api:2024:Part.Set",
                entity_kind="method",
                canonical_name="Part.Set",
                declaring_name="Part.Set",
                class_name="Part",
                method_name="Set",
            )
        ],
        runtime_release="2024",
    ).write(catalog_dir)

    loaded = _load_api_catalog(catalog_dir, workspace=workspace.resolve())

    assert loaded is not None
    assert loaded.lookup("Part.Set").status == "found"
    binding = _api_catalog_binding(loaded)
    assert binding is not None
    assert binding.card_count == 1

    nested_catalog = workspace / "catalog"
    ApiCatalog([], runtime_release="2024").write(nested_catalog)
    with pytest.raises(ValueError, match="outside"):
        _load_api_catalog(nested_catalog, workspace=workspace.resolve())


def test_cli_catalog_mode_defaults_and_validates_catalog_presence() -> None:
    catalog = ApiCatalog([], runtime_release="2024")

    assert _resolve_api_catalog_mode(None, None) == "off"
    assert _resolve_api_catalog_mode(None, catalog) == "full"
    assert (
        _resolve_api_catalog_mode("automatic_verified_only", catalog)
        == "automatic_verified_only"
    )
    assert (
        _resolve_api_catalog_mode("automatic_exact_signature", catalog)
        == "automatic_exact_signature"
    )
    with pytest.raises(ValueError, match="requires"):
        _resolve_api_catalog_mode("automatic_verified_only", None)
    with pytest.raises(ValueError, match="requires"):
        _resolve_api_catalog_mode("automatic_exact_signature", None)
    with pytest.raises(ValueError, match="must not receive"):
        _resolve_api_catalog_mode("off", catalog)


def test_cli_static_preflight_requires_explicit_external_source(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    with pytest.raises(ValueError, match="requires"):
        _prepare_static_preflight_source(
            mode="advisory",
            wheel=None,
            cache_dir=None,
            workspace=workspace.resolve(),
        )
    with pytest.raises(ValueError, match="must not receive"):
        _prepare_static_preflight_source(
            mode="off",
            wheel=tmp_path / "unused.whl",
            cache_dir=None,
            workspace=workspace.resolve(),
        )


def test_cli_prepares_pinned_static_source_outside_workspace(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    wheel = tmp_path / "abqpy-2024.10-py3-none-any.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr(
            "abqpy-2024.10.dist-info/METADATA",
            "Metadata-Version: 2.1\nName: abqpy\nVersion: 2024.10\n",
        )
        archive.writestr("abaqus/__init__.py", "")

    source = _prepare_static_preflight_source(
        mode="advisory",
        wheel=wheel,
        cache_dir=tmp_path / "static-cache",
        workspace=workspace.resolve(),
    )

    assert source is not None
    assert source.binding().api_source_version == "2024.10"
    with pytest.raises(ValueError, match="outside"):
        _prepare_static_preflight_source(
            mode="advisory",
            wheel=wheel,
            cache_dir=workspace / "cache",
            workspace=workspace.resolve(),
        )


def test_cli_accepts_enforced_aci_and_explicit_runtime_files() -> None:
    args = build_parser().parse_args(
        [
            "--goal",
            "test",
            "--static-preflight-mode",
            "enforced_verified",
            "--abqpy-static-wheel",
            "abqpy.whl",
            "--static-preflight-cache-dir",
            "cache",
            "--allowed-cae-script",
            "build_model.py",
            "--allowed-abaqus-python-script",
            "extract_results.py",
            "--allowed-solver-input",
            "beam_tension.inp",
        ]
    )

    assert args.static_preflight_mode == "enforced_verified"
    policy = _runtime_script_policy(
        allowed_cae_scripts=args.allowed_cae_script,
        allowed_abaqus_python_scripts=args.allowed_abaqus_python_script,
        allowed_solver_inputs=args.allowed_solver_input,
    )
    assert policy.allowed_cae_scripts == ("build_model.py",)
    assert policy.allowed_abaqus_python_scripts == ("extract_results.py",)
    assert policy.allowed_solver_inputs == ("beam_tension.inp",)
