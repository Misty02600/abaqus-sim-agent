import hashlib
from dataclasses import replace
from pathlib import Path
from uuid import uuid4

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient
from pydantic import SecretStr

from abaqus_sim_agent.coding import options, static_preflight
from abaqus_sim_agent.coding.model import DeepSeekGateway
from abaqus_sim_agent.coding.options import RunOptions
from tests.support.web import answer_body, configured_app, login, settled, start
from tests.test_static_preflight import _write_abqpy_wheel


def create(client, workspace, **options):
    workspace.mkdir(exist_ok=True)
    return client.post(
        "/api/v1/runs",
        json={
            "idempotency_key": str(uuid4()),
            "goal": "Ask for thickness",
            "workspace": str(workspace),
            "options": {
                "model_provider": "deepseek",
                "model_name": "deepseek-v4-flash",
                **options,
            },
        },
    )


def test_web_uses_managed_static_source(tmp_path, monkeypatch):
    fixture = tmp_path / "fixture.whl"
    _write_abqpy_wheel(fixture)
    monkeypatch.setattr(
        static_preflight,
        "STATIC_WHEEL_SHA256",
        hashlib.sha256(fixture.read_bytes()).hexdigest(),
    )
    wheel, _ = static_preflight.managed_static_paths()
    wheel.parent.mkdir(parents=True)
    wheel.write_bytes(fixture.read_bytes())
    app, _ = configured_app(tmp_path)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        login(client)
        result = create(client, tmp_path / "workspace", static_preflight_mode="on")
        assert result.status_code == 201, result.text
        rid = result.json()["view"]["run_id"]
        registered = app.state.controller.registry.load(rid)
        assert registered.launch.abqpy_static_wheel == wheel
        assert registered.request.static_preflight is not None
        assert registered.request.runtime.launcher_path == Path("abq2024")
        start(client, rid)
        settled(client, rid, status="waiting_for_user")


def test_web_resume_keeps_saved_model_and_resources(tmp_path, monkeypatch):
    app, _ = configured_app(tmp_path)
    ctl = app.state.controller
    ctl.api_keys[DeepSeekGateway.OPENCODE_GO] = SecretStr("go-key")
    ctl.config = replace(
        ctl.config,
        overrides=ctl.config.overrides.model_copy(update={"http_retries": 4}),
    )
    workspace = tmp_path / "workspace"
    old = workspace / ".abaqus-sim-agent/settings.json"
    old.parent.mkdir(parents=True)
    old.write_text("invalid JSON", encoding="utf-8")
    with TestClient(app, base_url="http://127.0.0.1") as client:
        login(client)
        result = create(
            client,
            workspace,
            model_provider="opencode-go",
            model_name="deepseek-v4-flash",
            http_retries=0,
        )
        assert result.status_code == 201, result.text
        rid = result.json()["view"]["run_id"]
        start(client, rid)
        waiting = settled(client, rid, status="waiting_for_user")
        launch = ctl.registry.load(rid).request.state_dir / "launch.json"
        before = launch.read_bytes()

    other, _ = configured_app(tmp_path)
    ctl = other.state.controller
    ctl.api_keys = {DeepSeekGateway.OPENCODE_GO: SecretStr("new-go-key")}
    ctl.config = replace(
        ctl.config,
        overrides=RunOptions(),
    )
    monkeypatch.setattr(
        options, "bundled_catalog_path", lambda: tmp_path / "missing-new-default"
    )
    observed = []
    factory = ctl.model_factory

    def capture(**kwargs):
        observed.append(kwargs)
        return factory(**kwargs)

    ctl.model_factory = capture
    with TestClient(other, base_url="http://127.0.0.1") as client:
        login(client)
        response = client.post(f"/api/v1/runs/{rid}/answers", json=answer_body(waiting))
        assert response.status_code == 202, response.text
        settled(client, rid, status="completed")
        assert observed[-1]["gateway"] is DeepSeekGateway.OPENCODE_GO
        assert observed[-1]["http_retries"] == 0
        assert observed[-1]["api_key"].get_secret_value() == "new-go-key"
        assert launch.read_bytes() == before
        assert old.read_text() == "invalid JSON"


def test_failed_creation_can_retry_the_same_request(
    tmp_path, monkeypatch, product_catalog_path
):
    app, _ = configured_app(tmp_path)
    ctl = app.state.controller
    incomplete = tmp_path / "incomplete-catalog"
    incomplete.mkdir()
    monkeypatch.setattr(options, "bundled_catalog_path", lambda: incomplete)
    key = uuid4()
    rid = "web-" + key.hex
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    payload = {
        "options": {"model_provider": "deepseek", "model_name": "deepseek-v4-flash"},
        "idempotency_key": str(key),
        "goal": "test",
        "workspace": str(workspace),
    }
    with TestClient(app, base_url="http://127.0.0.1") as client:
        login(client)
        assert client.post("/api/v1/runs", json=payload).status_code == 409
        assert not ctl.registry.directory(rid).exists()
        assert not ctl.registry.state_dir(rid).exists()
        monkeypatch.setattr(
            options, "bundled_catalog_path", lambda: product_catalog_path
        )
        retry = client.post("/api/v1/runs", json=payload)
        assert retry.status_code == 201, retry.text
