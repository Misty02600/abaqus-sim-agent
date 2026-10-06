import json
import sys
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from abaqus_sim_agent.evaluation import api_repair as repair
from abaqus_sim_agent.evaluation import public_mechanics_suite as suite


@pytest.mark.parametrize("enabled", [False, True])
def test_mechanics_foreground_run_owns_tracing(tmp_path, monkeypatch, enabled):
    sessions = []
    runs = []

    async def run(*args):
        runs.append(args)

    @contextmanager
    def session(active, *, environment):
        sessions.append((active, environment))
        yield

    monkeypatch.setattr(suite.telemetry, "telemetry_session", session)
    monkeypatch.setattr(suite, "run", run)
    argv = [
        "worker.py",
        "resume",
        "--root",
        str(tmp_path),
        "--case",
        "circular-shaft-pure-torsion",
    ]
    if enabled:
        argv.append("--phoenix")
    monkeypatch.setattr(sys, "argv", argv)
    suite.main()
    assert sessions == [(enabled, "evaluation")]
    assert runs == [(tmp_path.resolve(), "resume", ["circular-shaft-pure-torsion"])]


@pytest.mark.parametrize("mode", ["launch", "pause"])
def test_removed_mechanics_controls_are_rejected(tmp_path, monkeypatch, mode):
    monkeypatch.setattr(sys, "argv", ["worker.py", mode, "--root", str(tmp_path)])
    with pytest.raises(SystemExit) as exc:
        suite.main()
    assert exc.value.code == 2
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("mode", ["run", "continue", "resume"])
def test_mechanics_async_commands_use_existing_library_operation(
    tmp_path, monkeypatch, mode
):
    calls = []

    async def run(root, action, case_ids):
        calls.append((root, action, case_ids))

    monkeypatch.setattr(suite, "run", run)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "worker.py",
            mode,
            "--root",
            str(tmp_path),
            "--case",
            "cantilever-uniform-load",
        ],
    )
    suite.main()
    assert calls == [(tmp_path.resolve(), mode, ["cantilever-uniform-load"])]


@pytest.mark.parametrize("valid", [True, False])
def test_mechanics_report_preserves_binding_failure_and_still_marks_report_invalid(
    tmp_path, monkeypatch, valid
):
    (tmp_path / "manifest.json").write_text(
        json.dumps({"version": suite.VERSION}), encoding="utf-8"
    )
    checks = []

    def verify(root):
        checks.append("verified")
        if not valid:
            raise ValueError("fixture binding drift")

    def report(root, *, bindings_unchanged):
        checks.append(bindings_unchanged)

    monkeypatch.setattr(suite, "verify_binding", verify)
    monkeypatch.setattr(suite, "build_report", report)
    monkeypatch.setattr(sys, "argv", ["worker.py", "report", "--root", str(tmp_path)])
    if valid:
        suite.main()
    else:
        with pytest.raises(ValueError, match="fixture binding drift"):
            suite.main()
    assert checks == ["verified", valid]


@pytest.mark.parametrize(("passed", "code"), [(True, 0), (False, 1)])
def test_api_repair_command_preserves_arguments_and_gate_exit_code(
    tmp_path, monkeypatch, passed, code
):
    calls = []

    async def evaluate(root, **kwargs):
        calls.append((root, kwargs))
        return SimpleNamespace(passed=passed)

    monkeypatch.setattr(repair, "run_evaluation", evaluate)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "api_repair",
            "--run-root",
            str(tmp_path),
            "--case-id",
            "first",
            "--case-id",
            "second",
            "--model-provider",
            "opencode-go",
            "--model",
            "fixture-model",
            "--repetition",
            "2",
            "--http-retries",
            "3",
            "--static-preflight-mode",
            "on",
            "--api-catalog-mode",
            "full",
        ],
    )
    with pytest.raises(SystemExit) as exc:
        repair.main()
    assert exc.value.code == code
    root, settings = calls[0]
    assert root == tmp_path.resolve()
    assert settings["case_ids"] == ("first", "second")
    assert settings["model_gateway"] is repair.DeepSeekGateway.OPENCODE_GO
    assert settings["model_name"] == "fixture-model"
    assert settings["repetition"] == 2
    assert settings["http_retries"] == 3
    assert settings["static_preflight_mode"] == "on"
    assert settings["api_catalog_mode"] == "full"
    assert not list(tmp_path.iterdir())


def test_api_repair_defaults_are_still_owned_by_library(tmp_path, monkeypatch):
    async def evaluate(root, **kwargs):
        assert kwargs["manifest_path"] == repair.DEFAULT_MANIFEST
        assert kwargs["model_name"] == repair.DEEPSEEK_V4_FLASH
        assert kwargs["model_gateway"] is repair.DeepSeekGateway.OFFICIAL
        assert kwargs["http_retries"] == repair.DEFAULT_MODEL_HTTP_RETRIES
        assert kwargs["case_ids"] is None
        assert kwargs["static_preflight_mode"] == "off"
        assert kwargs["api_catalog_mode"] == "off"
        return SimpleNamespace(passed=True)

    monkeypatch.setattr(repair, "run_evaluation", evaluate)
    monkeypatch.setattr(sys, "argv", ["api_repair", "--run-root", str(tmp_path)])
    with pytest.raises(SystemExit) as exc:
        repair.main()
    assert exc.value.code == 0


@pytest.mark.parametrize(
    ("mode", "passed"), [("preflight", True), ("doctor", True), ("doctor", False)]
)
def test_mechanics_preflight_and_doctor_preserve_exit_contract(
    tmp_path, monkeypatch, mode, passed
):
    calls = []

    async def operation(root):
        calls.append(root)
        return {"passed": passed}

    monkeypatch.setattr(suite, mode, operation)
    monkeypatch.setattr(sys, "argv", ["worker.py", mode, "--root", str(tmp_path)])
    if passed:
        suite.main()
    else:
        with pytest.raises(SystemExit) as exc:
            suite.main()
        assert exc.value.code == 2
    assert calls == [tmp_path.resolve()]
