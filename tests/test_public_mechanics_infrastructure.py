import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import httpx
import pytest
from pydantic import SecretStr
from pydantic_ai import Agent
from pydantic_ai.exceptions import ModelAPIError
from pydantic_ai.messages import ModelResponse, ToolCallPart
from pydantic_ai.models.function import FunctionModel

from abaqus_sim_agent.coding.model import (
    DEEPSEEK_V4_FLASH,
    DeepSeekGateway,
    create_deepseek_chat_model,
    new_run_execution_config,
)
from abaqus_sim_agent.evaluation import public_mechanics_suite as suite
from abaqus_sim_agent.evaluation.public_mechanics_cases import (
    BASIC_LINEAR_CASE_IDS,
    PUBLIC_MECHANICS_PROTOCOL,
    get_case,
)
from abaqus_sim_agent.evaluation.public_mechanics_control import (
    ObservedTransport,
    append_event,
    build_report,
    read_json,
    run_until_pause,
    save_json,
    transport_summary,
)


def root_fixture(tmp_path, names=None):
    if names is None:
        names = ["solid-cantilever-directional-bending", "plate-with-hole-tension"]
    config = new_run_execution_config(
        model_provider=DeepSeekGateway.OPENCODE_GO,
        model_name=DEEPSEEK_V4_FLASH,
        http_retries=4,
        http_timeout_seconds=300.0,
    )
    save_json(
        tmp_path / "manifest.json",
        {
            "version": PUBLIC_MECHANICS_PROTOCOL,
            "case_ids": names,
            "scoring_case_ids": list(BASIC_LINEAR_CASE_IDS),
            "batch_id": "test",
            "foundation": True,
            "catalog_mode": "full",
            "knowledge_resources": None,
            "max_task_concurrency": 1,
            "max_runtime_concurrency": 1,
            "verification_policy": "results_first_v1",
            "max_explicit_resumes": 3,
            "execution_config": config.model_dump(mode="json"),
            "budgets": {
                name: get_case(name).run_budget().model_dump(mode="json")
                for name in names
            },
        },
    )
    save_json(
        tmp_path / "cases.json",
        [get_case(name).model_dump(mode="json") for name in names],
    )
    seal_contracts(tmp_path)
    save_json(tmp_path / "goals.json", {n: get_case(n).goal() for n in names})
    return names


def seal_contracts(root):
    save_json(
        root / "seal.json",
        {
            "files": {
                name: suite.file_digest(root / name)
                for name in ("cases.json", "manifest.json")
            }
        },
    )


def test_solid_goal_uses_contract_values_without_ambiguous_ranges():
    case = get_case("solid-cantilever-directional-bending")
    assert "0 ≤ y ≤ 0.2 in，0 ≤ z ≤ 0.1 in" in case.goal()
    assert "..." not in case.goal()
    changed = case.model_copy(update={"width": 0.35, "height": 0.07, "length": 9.0})
    assert "0 ≤ x ≤ 9 in，0 ≤ y ≤ 0.35 in，0 ≤ z ≤ 0.07 in" in changed.goal()
    assert "y ≤ 0.2" not in changed.goal()


@pytest.mark.usefixtures("mock_model_requests")
@pytest.mark.parametrize("first_failure", ["503", "read_timeout"])
def test_sdk_retry_transport_records_actual_attempts_without_secrets(
    tmp_path, first_failure
):
    path = tmp_path / "http.jsonl"
    calls = []

    async def respond(request):
        calls.append(request)
        if len(calls) == 1:
            if first_failure == "read_timeout":
                raise httpx.ReadTimeout("sensitive timeout message", request=request)
            return httpx.Response(
                503, json={"error": "temporary"}, headers={"retry-after-ms": "1"}
            )
        return httpx.Response(
            200,
            json={
                "id": "fixture",
                "object": "chat.completion",
                "created": 1,
                "model": DEEPSEEK_V4_FLASH,
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": "stop",
                        "message": {"role": "assistant", "content": "OK"},
                    }
                ],
                "usage": {
                    "prompt_tokens": 3,
                    "completion_tokens": 1,
                    "total_tokens": 4,
                },
            },
        )

    async def run():
        model, client = create_deepseek_chat_model(
            gateway=DeepSeekGateway.OPENCODE_GO,
            model_name=DEEPSEEK_V4_FLASH,
            api_key=SecretStr("secret-do-not-log"),
            conversation_id="telemetry-test",
            http_retries=1,
            http_client=httpx.AsyncClient(
                transport=ObservedTransport(path, httpx.MockTransport(respond))
            ),
        )
        try:
            assert (
                await Agent(model).run("private prompt not for logs")
            ).output == "OK"
        finally:
            await client.close()

    asyncio.run(run())
    assert transport_summary(path) == {
        "attempts": 2,
        "completed": 2 if first_failure == "503" else 1,
        "failed": 0 if first_failure == "503" else 1,
        "unknown": 0,
    }
    text = path.read_text(encoding="utf-8")
    assert "secret-do-not-log" not in text
    assert "private prompt" not in text
    if first_failure == "503":
        assert '"status": 503' in text
    else:
        assert '"error_type": "ReadTimeout"' in text
        assert "sensitive timeout message" not in text
    assert '"retry": "1"' in text


def test_body_timeout_and_process_interruption_remain_observable(tmp_path):
    path = tmp_path / "http.jsonl"

    class BrokenBody(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b"partial"
            raise httpx.ReadTimeout("sensitive error text")

    async def run():
        transport = ObservedTransport(
            path,
            httpx.MockTransport(
                lambda request: httpx.Response(200, stream=BrokenBody())
            ),
        )
        async with httpx.AsyncClient(transport=transport) as client:
            with pytest.raises(httpx.ReadTimeout):
                await client.get("https://example.invalid")

    asyncio.run(run())
    append_event(path, "http_started", attempt="killed", retry="0")
    assert transport_summary(path) == {
        "attempts": 2,
        "completed": 0,
        "failed": 1,
        "unknown": 1,
    }
    assert "sensitive error text" not in path.read_text(encoding="utf-8")


def test_pause_cancels_service_and_waits_for_its_cleanup(tmp_path):
    marker = tmp_path / "pause.json"
    cleaned = []

    class Service:
        async def run(self):
            save_json(marker, {})
            try:
                await asyncio.Event().wait()
            finally:
                cleaned.append(True)

    asyncio.run(run_until_pause(Service(), marker))
    assert cleaned == [True]


def test_report_rebuild_preserves_unknown_and_does_not_write_outcome(tmp_path):
    names = root_fixture(tmp_path)
    state = tmp_path / names[0] / "state"
    save_json(
        state / "resume_state.json",
        {
            "status": "running",
            "usage": {"requests": 3},
            "dependencies": {"abaqus_calls": 2, "solver_calls": 1},
            "active_wall_time_seconds": 23.0,
        },
    )
    report = build_report(tmp_path, bindings_unchanged=True)
    assert report["trials"][0]["classification"] == "interrupted_unknown"
    assert report["trials"][0]["http"]["attempts"] is None
    assert report["trials"][1]["classification"] == "not_started"
    assert not (state / "outcome.json").exists()
    assert (tmp_path / "report.md").exists()


@pytest.mark.parametrize("error_type", ["ModelAPIError", "ModelHTTPError"])
def test_report_classifies_model_provider_errors(tmp_path, error_type):
    names = root_fixture(tmp_path)
    state = tmp_path / names[0] / "state"
    save_json(
        state / "resume_state.json",
        {
            "status": "interrupted",
            "usage": {"requests": 0},
            "dependencies": {},
            "active_wall_time_seconds": 10.0,
            "last_error_type": error_type,
        },
    )
    report = build_report(tmp_path, bindings_unchanged=True)
    assert report["trials"][0]["classification"] == "provider_interrupted"


def test_health_stale_or_failed_prevents_run(tmp_path):
    root_fixture(tmp_path)
    with pytest.raises(ValueError, match="doctor"):
        suite._ensure_health(tmp_path)
    save_json(
        tmp_path / "health.json",
        {
            "passed": True,
            "seal_sha256": suite.file_digest(tmp_path / "seal.json"),
            "at": (datetime.now(UTC) - timedelta(minutes=11)).isoformat(),
        },
    )
    with pytest.raises(ValueError, match="expired"):
        suite._ensure_health(tmp_path)


def test_continue_never_replays_and_resume_is_bounded(tmp_path):
    names = root_fixture(tmp_path)
    save_json(tmp_path / "started.json", {})
    state = tmp_path / names[0] / "state"
    save_json(
        state / "resume_state.json",
        {"status": "interrupted", "active_wall_time_seconds": 20.0},
    )
    assert suite.selected_cases(tmp_path, "continue", None) == names[1:]
    assert suite.selected_cases(tmp_path, "resume", names[:1]) == names[:1]
    for index in range(1, 4):
        save_json(state / f"attempts/{index:03}.json", {"mode": "resume"})
    with pytest.raises(ValueError, match="allowance"):
        suite.selected_cases(tmp_path, "resume", names[:1])
    with pytest.raises(FileExistsError):
        suite.selected_cases(tmp_path, "run", None)


def test_batch_provider_failure_stops_remaining_cases_and_keeps_report(
    tmp_path, monkeypatch
):
    names = root_fixture(tmp_path)
    monkeypatch.setattr(suite, "_checked_preflight", lambda root: None)
    monkeypatch.setattr(suite, "verify_binding", lambda root: None)
    monkeypatch.setattr(suite, "_ensure_health", lambda root: None)
    monkeypatch.setattr(
        suite,
        "take_gateway_api_key_from_environment",
        lambda gateway: SecretStr("fixture"),
    )
    monkeypatch.setattr(suite.ApiCatalog, "open", lambda root: None)
    called = []

    async def fail(root, name, **kwargs):
        called.append(name)
        save_json(
            root / name / "state/resume_state.json",
            {
                "status": "interrupted",
                "usage": {"requests": 1},
                "dependencies": {},
                "last_error_type": "ModelAPIError",
            },
        )

    monkeypatch.setattr(suite, "_run_case", fail)
    asyncio.run(suite.run(tmp_path))
    assert called == names[:1]
    assert (
        read_json(tmp_path / "report.json")["trials"][1]["classification"]
        == "not_started"
    )
    assert read_json(tmp_path / "health.json")["passed"] is False


def test_suite_same_run_resume_retains_usage_and_file_work(tmp_path, monkeypatch):
    name = root_fixture(tmp_path)[0]
    factory = suite.create_abaqus_coding_agent
    monkeypatch.setattr(suite, "_eval_api_catalog_binding", lambda catalog: None)

    def agent_factory(*args, **kwargs):
        # Exercise the real service/agent loop with no test catalog installed.
        kwargs["api_catalog_mode"] = "off"
        return factory(*args, **kwargs)

    monkeypatch.setattr(suite, "create_abaqus_coding_agent", agent_factory)
    calls = 0

    def model(messages, info):
        nonlocal calls
        calls += 1
        if calls == 1:
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        "write_file", {"path": "evidence.txt", "content": "retained"}
                    )
                ]
            )
        if calls == 2:
            raise ModelAPIError("fixture", "injected outage")
        assert "retained" in str(messages)
        tool = next(
            t.name for t in info.output_tools if t.name.endswith("FailedDecision")
        )
        return ModelResponse(
            parts=[
                ToolCallPart(
                    tool,
                    {
                        "status": "failed",
                        "summary": "Fixture stopped after successful restore.",
                        "failure_stage": "fixture",
                    },
                )
            ]
        )

    class Client:
        async def close(self):
            pass

    def model_factory(**kwargs):
        return FunctionModel(model), Client()

    monkeypatch.setattr(suite, "create_deepseek_chat_model", model_factory)
    asyncio.run(
        suite._run_case(
            tmp_path, name, resume=False, key=SecretStr("fixture"), catalog=None
        )
    )
    state = tmp_path / name / "state"
    before = read_json(state / "resume_state.json")
    assert before["status"] == "interrupted"
    asyncio.run(
        suite._run_case(
            tmp_path, name, resume=True, key=SecretStr("fixture"), catalog=None
        )
    )
    after = read_json(state / "resume_state.json")
    assert after["turn"] == 2
    assert after["usage"]["requests"] == 2
    assert after["active_wall_time_seconds"] >= before["active_wall_time_seconds"]
    assert (state.parent / "workspace/evidence.txt").read_text() == "retained"
    assert len(list((state / "attempts").glob("*.json"))) == 2
    assert (
        read_json(state / "attempts/001.json")["resume_state_snapshot"]["status"]
        == "interrupted"
    )
    assert (
        read_json(state / "attempts/001.json")["resume_state_snapshot"]["usage"][
            "requests"
        ]
        == 1
    )
    assert read_json(state / "outcome.json")["status"] == "failed"


def test_discovery_profile_freezes_inputs_without_reference_attachments(
    tmp_path, monkeypatch
):
    # Exercise copying and sealing with small source/runtime inputs, not the installed SDK tree.
    source = tmp_path / "source"
    (source / "src").mkdir(parents=True)
    (source / "src/fixture.py").write_text("value = 1\n", encoding="utf-8")
    (source / "src/__pycache__").mkdir()
    (source / "src/__pycache__/fixture.pyc").write_bytes(b"ignored")
    (source / "pyproject.toml").write_text("# fixture project\n", encoding="utf-8")
    (source / "uv.lock").write_text("# fixture lock\n", encoding="utf-8")
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    (runtime / "client.py").write_text("# fixture dependency\n", encoding="utf-8")
    launcher = runtime / "abq2024.bat"
    launcher.write_text("rem not executed\n", encoding="ascii")
    dependency = SimpleNamespace(
        files=["client.py"],
        locate_file=lambda name: runtime / name,
        metadata={"Name": "fixture-dependency"},
        version="1.0",
    )
    monkeypatch.setattr(suite, "ROOT", source)
    monkeypatch.setattr(suite, "DEFAULT_ABAQUS_2024_LAUNCHER", launcher)
    monkeypatch.setattr(suite, "distribution", lambda name: dependency)
    monkeypatch.setattr(suite, "distributions", lambda: [dependency])
    root = tmp_path / "run"
    catalog = tmp_path / "catalog"
    knowledge = tmp_path / "knowledge"
    catalog.mkdir()
    knowledge.mkdir()
    (knowledge / "README.md").write_text("bounded reference", encoding="utf-8")
    monkeypatch.setattr(suite.ApiCatalog, "open", lambda path: object())
    with pytest.raises(ValueError, match="does not attach"):
        suite.prepare(root, catalog, knowledge_resources=knowledge)
    assert not root.exists()
    suite.prepare(
        root,
        catalog,
        ["plate-with-hole-tension", "solid-cantilever-directional-bending"],
        True,
        api_catalog_mode="full_with_discovery",
        max_task_concurrency=2,
        max_runtime_concurrency=2,
    )
    manifest = read_json(root / "manifest.json")
    assert manifest["catalog_mode"] == "full_with_discovery"
    assert manifest["max_task_concurrency"] == 2
    assert manifest["max_runtime_concurrency"] == 2
    assert manifest["max_explicit_resumes"] == 3
    assert manifest["execution_config"]["http_timeout_seconds"] == 300.0
    assert manifest["execution_config"]["http_retries"] == 4
    assert manifest["execution_config"]["planning_enabled"] is False
    for budget in manifest["budgets"].values():
        assert budget["max_model_requests"] == 200
        assert budget["max_api_lookup_calls"] is None
        assert budget["max_concept_lookup_calls"] is None
        assert budget["max_diagnostic_retrieval_calls"] is None
        assert budget["max_static_preflight_calls"] is None
        assert budget["max_abaqus_calls"] == 80
        assert budget["max_solver_calls"] == 10
        assert budget["wall_time_seconds"] == 5400.0
    assert manifest["knowledge_resources"] is None
    assert manifest["submission_policy"] == suite.SUBMISSION_POLICY
    assert not (root / "knowledge-resources").exists()
    assert (root / "snapshot/src/fixture.py").read_bytes() == (
        source / "src/fixture.py"
    ).read_bytes()
    assert not (root / "snapshot/src/__pycache__").exists()
    seal = read_json(root / "seal.json")
    assert seal["runtime_files"] == {
        str((runtime / "client.py").resolve()): suite.file_digest(runtime / "client.py")
    }
    monkeypatch.setattr(suite, "ROOT", root / "snapshot")
    suite.verify_binding(root)
    (root / "goals.json").write_text("changed", encoding="utf-8")
    with pytest.raises(ValueError, match="bound input changed"):
        suite.verify_binding(root)


def test_concurrent_wave_starts_both_cases_and_bounds_runtime(tmp_path, monkeypatch):
    root_fixture(tmp_path)
    manifest = read_json(tmp_path / "manifest.json")
    manifest.update(max_task_concurrency=2, max_runtime_concurrency=2)
    save_json(tmp_path / "manifest.json", manifest)
    seal_contracts(tmp_path)
    monkeypatch.setattr(suite, "_checked_preflight", lambda root: None)
    monkeypatch.setattr(suite, "verify_binding", lambda root: None)
    monkeypatch.setattr(suite, "_ensure_health", lambda root: None)
    monkeypatch.setattr(
        suite,
        "take_gateway_api_key_from_environment",
        lambda gateway: SecretStr("fixture"),
    )
    monkeypatch.setattr(suite.ApiCatalog, "open", lambda root: None)
    active = 0
    peak = 0

    async def complete(root, name, **kwargs):
        nonlocal active, peak
        assert kwargs["runtime_gate"]._value == 2
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.01)
        state = root / name / "state"
        state.mkdir(parents=True)
        save_json(
            state / "outcome.json",
            {"status": "failed", "failure_stage": "fixture", "summary": "done"},
        )
        active -= 1

    monkeypatch.setattr(suite, "_run_case", complete)
    asyncio.run(suite.run(tmp_path))
    assert peak == 2
    assert [
        row["classification"] for row in read_json(tmp_path / "report.json")["trials"]
    ] == ["task_failed", "task_failed"]


def test_report_keeps_corrupt_evidence_visible(tmp_path):
    names = root_fixture(tmp_path)
    state = tmp_path / names[0] / "state"
    state.mkdir(parents=True)
    (state / "resume_state.json").write_text('{"broken":', encoding="utf-8")
    report = build_report(tmp_path, bindings_unchanged=True)
    assert report["trials"][0]["classification"] == "invalid_evidence"
    assert report["trials"][1]["classification"] == "not_started"


def test_model_factory_error_is_retained_without_an_outcome(tmp_path, monkeypatch):
    name = root_fixture(tmp_path)[0]
    monkeypatch.setattr(suite, "_eval_api_catalog_binding", lambda catalog: None)

    def broken(**kwargs):
        raise ValueError("credential-like-text-must-not-be-logged")

    monkeypatch.setattr(suite, "create_deepseek_chat_model", broken)
    asyncio.run(
        suite._run_case(
            tmp_path, name, resume=False, key=SecretStr("fixture"), catalog=None
        )
    )
    report = build_report(tmp_path, bindings_unchanged=True)
    assert report["trials"][0]["classification"] == "setup_error"
    assert "credential-like-text" not in (tmp_path / "report.json").read_text(
        encoding="utf-8"
    )


def test_hidden_worker_starts_after_launcher_releases_lock(tmp_path, monkeypatch):
    import os
    import time

    if os.name != "nt":
        pytest.skip("Windows detached-process integration")
    root_fixture(tmp_path)
    monkeypatch.setattr(suite, "_checked_preflight", lambda root: None)
    monkeypatch.setattr(suite, "_ensure_health", lambda root: None)
    (tmp_path / "worker.py").write_text(
        "from pathlib import Path\nimport sys\n"
        f"sys.path.insert(0, {str(suite.ROOT / 'src')!r})\n"
        "from abaqus_sim_agent.coding.run_lock import CodingRunFileLock\n"
        "root=Path(__file__).parent\n"
        "with CodingRunFileLock(root/'control'):\n"
        "    (root/'detached.ok').write_text('no model or Abaqus calls')\n",
        encoding="utf-8",
    )
    suite.launch(tmp_path, "run", None)
    deadline = time.monotonic() + 10
    while not (tmp_path / "detached.ok").exists() and time.monotonic() < deadline:
        time.sleep(0.05)
    assert (tmp_path / "detached.ok").exists()
    assert list((tmp_path / "control").glob("*/launch.json"))


def test_doctor_records_setup_and_client_close_errors(tmp_path, monkeypatch):
    root_fixture(tmp_path)
    monkeypatch.setattr(suite, "_checked_preflight", lambda root: None)
    monkeypatch.setattr(
        suite,
        "take_gateway_api_key_from_environment",
        lambda gateway: SecretStr("fixture"),
    )

    class BrokenClose:
        async def close(self):
            raise OSError("secret-like-close-error")

    def model(messages, info):
        from pydantic_ai.messages import TextPart

        return ModelResponse(parts=[TextPart("OK")])

    monkeypatch.setattr(
        suite,
        "create_deepseek_chat_model",
        lambda **kwargs: (FunctionModel(model), BrokenClose()),
    )
    result = asyncio.run(suite.doctor(tmp_path))
    assert result["passed"] is False
    assert result["client_close_error_type"] == "OSError"
    assert read_json(tmp_path / "health.json")["passed"] is False
    assert len(list((tmp_path / "health").glob("*.json"))) == 1


def test_new_report_command_never_overwrites_historical_reports(tmp_path, monkeypatch):
    save_json(tmp_path / "manifest.json", {"version": "public-mechanics-e2e-v1"})
    original = b'{"historical":true}'
    (tmp_path / "report.json").write_bytes(original)
    monkeypatch.setattr("sys.argv", ["worker", "report", "--root", str(tmp_path)])
    with pytest.raises(ValueError, match="historical reports"):
        suite.main()
    assert (tmp_path / "report.json").read_bytes() == original


def test_frozen_worker_rejects_ignored_transport_overrides(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "sys.argv", ["worker", "run", "--root", str(tmp_path), "--http-retries", "10"]
    )
    with pytest.raises(SystemExit) as exc:
        suite.main()
    assert exc.value.code == 2
    assert not (tmp_path / "started.json").exists()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "case_id", ["nafems-le10-thick-plate-pressure", "plate-with-hole-tension"]
)
async def test_diagnostic_filenames_are_allowed_but_do_not_satisfy_final_delivery(
    tmp_path, case_id
):
    from abaqus_sim_agent.coding.dependencies import CodingAgentDependencies
    from abaqus_sim_agent.coding.workspace import WorkspaceResolver
    from abaqus_sim_agent.evaluation.public_mechanics_runner import (
        PublicMechanicsRunner,
    )
    from abaqus_sim_agent.execution import ProcessOutput
    from abaqus_sim_agent.verification.public_mechanics_run import (
        PublicMechanicsRunVerifier,
    )

    root_fixture(tmp_path, [case_id])
    request = suite._request(tmp_path, case_id, None)
    workspace = request.workspace
    workspace.mkdir(parents=True)
    initial = WorkspaceResolver(captured_cwd=workspace).snapshot(workspace)
    (workspace / "check_mesh.py").write_text("pass\n", encoding="ascii")
    (workspace / "inspect_odb.py").write_text("pass\n", encoding="ascii")
    (workspace / "minimal_c3d20r.inp").write_text("*HEADING\n", encoding="ascii")
    commands = []

    class Process:
        async def run(self, command, *, cwd, timeout_seconds):
            commands.append(tuple(command))
            return ProcessOutput(exit_code=0, stdout=b"", stderr=b"")

    runner = PublicMechanicsRunner(
        workspace, request.state_dir, process_strategy=Process()
    )
    deps = CodingAgentDependencies(request=request, runner=runner)
    assert deps.runtime_script_policy_denial(kind="cae", path="check_mesh.py") is None
    assert (
        deps.runtime_script_policy_denial(kind="abaqus_python", path="inspect_odb.py")
        is None
    )
    assert (
        deps.runtime_script_policy_denial(
            kind="solver_input", path="minimal_c3d20r.inp"
        )
        is None
    )
    executions = [
        await runner.run_cae_script("check_mesh.py"),
        await runner.run_abaqus_python("inspect_odb.py"),
        await runner.run_solver_job("minimal_c3d20r", input_file="minimal_c3d20r.inp"),
    ]
    assert all(result.stage_succeeded for result in executions)
    assert "input=minimal_c3d20r.inp" in commands[-1]
    outside = tmp_path / "outside.inp"
    outside.write_text("*HEADING\n", encoding="ascii")
    with pytest.raises(ValueError, match="inside workspace"):
        await runner.run_solver_job("outside", input_file=outside)
    with pytest.raises(ValueError, match="suffix"):
        await runner.run_solver_job("wrong", input_file="check_mesh.py")
    assert len(commands) == 3
    verdict = await PublicMechanicsRunVerifier(get_case(case_id)).verify(
        request, runner, initial, executions
    )
    assert not verdict.passed
    assert verdict.failure_stage == "provenance"
    assert "missing/stale artifact model.inp" in verdict.feedback
