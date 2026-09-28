from dataclasses import replace
from uuid import uuid4

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient
from pydantic import SecretStr

from abaqus_sim_agent.coding import preferences
from abaqus_sim_agent.coding.model import DeepSeekGateway
from abaqus_sim_agent.coding.preferences import PROJECT_SETTINGS_PATH
from tests.support.web import answer_body, configured_app, login, settled, start
from tests.test_model_preferences import write_preferences
from tests.test_static_preflight import _write_abqpy_wheel


def create(client, workspace, **overrides):
    workspace.mkdir(exist_ok=True)
    return client.post(
        "/api/v1/runs",
        json={
            "idempotency_key": str(uuid4()),
            "goal": "Ask for thickness",
            "workspace": str(workspace),
            **overrides,
        },
    )


def test_web_binds_shared_static_preferences_and_can_start(tmp_path):
    wheel = tmp_path / "abqpy.whl"
    _write_abqpy_wheel(wheel)
    write_preferences(
        preferences.user_settings_path(),
        static_preflight_mode="on",
        abqpy_static_wheel=str(wheel),
        static_preflight_cache_dir=str(tmp_path / "cache"),
        launcher_path=str(tmp_path / "configured-launcher.bat"),
    )
    app, _ = configured_app(tmp_path)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        login(client)
        result = create(client, tmp_path / "workspace")
        assert result.status_code == 201, result.text
        rid = result.json()["view"]["run_id"]
        registration = app.state.controller.registry.load(rid)
        assert registration.launch.static_preflight_mode == "on"
        assert registration.launch.abqpy_static_wheel == wheel
        assert registration.request.static_preflight is not None
        assert (
            registration.request.runtime.launcher_path
            == tmp_path / "configured-launcher.bat"
        )
        start(client, rid)
        settled(client, rid, status="waiting_for_user")


def test_web_precedence_and_resume_use_session_provider_after_defaults_change(tmp_path):
    user_path = preferences.user_settings_path()
    workspace = tmp_path / "workspace"
    project = workspace / PROJECT_SETTINGS_PATH
    write_preferences(user_path, model_provider="opencode-zen", http_retries=2)
    write_preferences(project, model_provider="opencode-go", http_retries=3)
    app, _ = configured_app(tmp_path)
    controller = app.state.controller
    controller.api_keys[DeepSeekGateway.OPENCODE_GO] = SecretStr("go-key")
    observed = []
    factory = controller.model_factory

    def capture(**kwargs):
        observed.append(kwargs)
        return factory(**kwargs)

    controller.model_factory = capture
    controller.config = replace(
        controller.config,
        overrides=controller.config.overrides.model_copy(update={"http_retries": 4}),
    )
    with TestClient(app, base_url="http://127.0.0.1") as client:
        login(client)
        created = create(client, workspace, preferences={"http_retries": 0})
        assert created.status_code == 201, created.text
        rid = created.json()["view"]["run_id"]
        registered = controller.registry.load(rid)
        assert registered.launch.model_provider is DeepSeekGateway.OPENCODE_GO
        assert registered.launch.http_retries == 0
        start(client, rid)
        waiting = settled(client, rid, status="waiting_for_user")
        assert observed[0]["api_key"].get_secret_value() == "go-key"
        launch = registered.request.state_dir / "launch.json"
        base = launch.read_bytes()

    # A later host has different defaults and even a malformed user preference file.
    # Resume must not read those preferences or demand the new default provider's key.
    user_path.write_text("invalid JSON", encoding="utf-8")
    other, _ = configured_app(tmp_path)
    restored = other.state.controller
    restored.api_keys = {DeepSeekGateway.OPENCODE_GO: SecretStr("restored-go-key")}
    original = restored.model_factory

    def capture_restored(**kwargs):
        observed.append(kwargs)
        return original(**kwargs)

    restored.model_factory = capture_restored
    restored.config = replace(
        restored.config,
        overrides=restored.config.overrides.model_copy(
            update={
                "model_provider": DeepSeekGateway.OFFICIAL,
                "api_catalog_dir": tmp_path / "missing-new-default-catalog",
            }
        ),
    )
    with TestClient(other, base_url="http://127.0.0.1") as client:
        login(client)
        response = client.post(f"/api/v1/runs/{rid}/answers", json=answer_body(waiting))
        assert response.status_code == 202, response.text
        done = settled(client, rid, status="completed")
        assert (
            done["view"]["usage"]["model_requests"]
            > waiting["view"]["usage"]["model_requests"]
        )
        assert observed[-1]["gateway"] is DeepSeekGateway.OPENCODE_GO
        assert observed[-1]["http_retries"] == 0
        assert observed[-1]["api_key"].get_secret_value() == "restored-go-key"
        assert launch.read_bytes() == base
        assert user_path.read_text() == "invalid JSON"


def test_web_explicit_model_is_session_only_and_invalid_preferences_leave_no_registration(
    tmp_path,
):
    user = preferences.user_settings_path()
    write_preferences(user, model_provider="opencode-go")
    app, _ = configured_app(tmp_path)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        login(client)
        before = user.read_bytes()
        result = create(
            client,
            tmp_path / "workspace",
            preferences={
                "model_provider": "deepseek",
                "model_name": "deepseek-v4-flash",
            },
        )
        assert result.status_code == 201, result.text
        rid = result.json()["view"]["run_id"]
        assert (
            app.state.controller.registry.load(rid).launch.model_provider
            is DeepSeekGateway.OFFICIAL
        )
        assert user.read_bytes() == before
        user.write_text("{}", encoding="utf-8")
        other = create(
            client,
            tmp_path / "other",
            preferences={
                "model_provider": "deepseek",
                "reasoning_policy": "enabled_high",
            },
        )
        assert other.status_code == 422, other.text
        assert (
            len(
                list(
                    app.state.controller.registry.root.glob(
                        "registry/*/registration.json"
                    )
                )
            )
            == 1
        )
