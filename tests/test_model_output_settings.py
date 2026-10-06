import asyncio
import json
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr, ValidationError
from pydantic_ai.messages import ModelRequest, UserPromptPart
from pydantic_ai.models import ModelRequestParameters

from abaqus_sim_agent.coding.cli import _validate_resume_arguments, build_parser
from abaqus_sim_agent.coding.launch import LaunchManifest
from abaqus_sim_agent.coding.model import ModelExecutionConfig, new_run_execution_config
from abaqus_sim_agent.coding.model_connections import create_model
from abaqus_sim_agent.coding.model_output_settings import (
    ModelOutputSettings,
    ModelOutputSettingsStore,
)
from abaqus_sim_agent.coding.option_arguments import options_from_args
from abaqus_sim_agent.coding.options import RunOptions, model_execution
from abaqus_sim_agent.coding.providers import ModelProvider
from tests.support.web import configured_app, login

PROVIDER = ModelProvider.OPENCODE_GO
MODEL = "deepseek-v4-flash"


def test_per_model_saved_override_explicit_precedence_and_clear():
    store = ModelOutputSettingsStore()
    options = RunOptions(model_provider=PROVIDER, model_name=MODEL)
    assert model_execution(options).max_tokens is None
    store.save(PROVIDER, MODEL, ModelOutputSettings(max_tokens=131072))
    assert model_execution(options).model_settings()["max_tokens"] == 131072
    assert store.get(PROVIDER, "another-model").max_tokens is None
    assert store.get(ModelProvider.OFFICIAL, MODEL).max_tokens is None
    assert (
        model_execution(options.model_copy(update={"max_tokens": 64000})).max_tokens
        == 64000
    )
    frozen = model_execution(options)
    store.save(PROVIDER, MODEL, ModelOutputSettings())
    assert model_execution(options).max_tokens is None
    assert frozen.max_tokens == 131072


@pytest.mark.parametrize("value", [0, -1, True, 1.5, "64000"])
def test_invalid_output_limit_rejected(value):
    with pytest.raises(ValidationError):
        ModelOutputSettings(max_tokens=value)


def test_corrupt_saved_settings_are_not_overwritten(tmp_path):
    path = tmp_path / "settings.json"
    path.write_bytes(b'{"opencode-go": {"deepseek-v4-flash": {"typo": 20}}}')
    original = path.read_bytes()
    store = ModelOutputSettingsStore(path)
    with pytest.raises(ValueError, match="原文件未被覆盖"):
        store.save(PROVIDER, MODEL, ModelOutputSettings(max_tokens=131072))
    assert path.read_bytes() == original


def test_saved_limit_cannot_be_overridden_downstream():
    config = new_run_execution_config(
        model_provider=PROVIDER, model_name=MODEL, max_tokens=131072
    )
    assert config.checked_settings({"max_tokens": 131072})["max_tokens"] == 131072
    for supplied in (
        {"max_tokens": 42},
        {"max_completion_tokens": 42},
        {"extra_body": {"max_tokens": 42}},
    ):
        with pytest.raises(ValueError, match="max_tokens"):
            config.checked_settings(supplied)
    legacy = ModelExecutionConfig(model_provider=PROVIDER, model_name=MODEL)
    assert legacy.output_truncation_recovery is False
    assert "max_tokens" not in legacy.model_settings()


@pytest.mark.usefixtures("mock_model_requests")
@pytest.mark.parametrize(
    "provider", [PROVIDER, ModelProvider.OFFICIAL, ModelProvider.OPENCODE_ZEN]
)
def test_deepseek_limit_uses_documented_wire_field(provider):
    requests = []

    def respond(request):
        requests.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "id": "test",
                "object": "chat.completion",
                "created": 0,
                "model": MODEL,
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": "stop",
                        "message": {"role": "assistant", "content": "ok"},
                    }
                ],
                "usage": {
                    "prompt_tokens": 2,
                    "completion_tokens": 1,
                    "total_tokens": 3,
                },
            },
        )

    async def run():
        config = new_run_execution_config(
            model_provider=provider, model_name=MODEL, max_tokens=131072
        )
        model, client = create_model(
            gateway=provider,
            model_name=MODEL,
            api_key=SecretStr("test-key"),
            execution_config=config,
            conversation_id="limit-test" if provider is PROVIDER else None,
            http_client=httpx.AsyncClient(transport=httpx.MockTransport(respond)),
        )
        try:
            await model.request(
                [ModelRequest(parts=[UserPromptPart("test")])],
                config.model_settings(),
                ModelRequestParameters(),
            )
        finally:
            await client.close()

    asyncio.run(run())
    assert requests[0]["max_tokens"] == 131072
    assert "max_completion_tokens" not in requests[0]


def test_web_saves_override_new_run_snapshots_it_and_reset_leaves_run_unchanged(
    tmp_path,
):
    app, _ = configured_app(tmp_path)
    url = f"/api/v1/model-output-settings?provider={PROVIDER.value}&model_name={MODEL}"
    with TestClient(app, base_url="http://127.0.0.1") as client:
        assert client.put(url, json={"max_tokens": 131072}).status_code == 401
        login(client)
        assert (
            client.post(
                "/api/v1/model-connections",
                json={"provider": PROVIDER.value, "api_key": "test-key"},
            ).status_code
            == 200
        )
        assert client.put(url, json={"max_tokens": 131072}).status_code == 200
        assert client.get(url).json() == {"max_tokens": 131072}
        assert client.put(url, json={"max_tokens": -1}).status_code == 422
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        response = client.post(
            "/api/v1/runs",
            json={
                "idempotency_key": str(uuid4()),
                "goal": "Explain",
                "workspace": str(workspace),
                "options": {"model_provider": PROVIDER.value, "model_name": MODEL},
            },
        )
        assert response.status_code == 201, response.text
        run = app.state.controller.registry.load(response.json()["view"]["run_id"])
        restored = LaunchManifest.model_validate_json(
            run.launch.model_dump_json()
        ).effective_execution()
        assert restored.max_tokens == 131072
        assert restored.output_truncation_recovery is True
        assert client.put(url, json={"max_tokens": None}).status_code == 200
        assert restored.max_tokens == 131072
        assert client.get(url).json() == {"max_tokens": None}


def test_cli_limit_is_explicit_and_not_allowed_on_resume():
    args = build_parser().parse_args(["--max-tokens", "131072"])
    assert options_from_args(args).max_tokens == 131072
    with pytest.raises(ValueError, match="--max-tokens"):
        _validate_resume_arguments(args)
