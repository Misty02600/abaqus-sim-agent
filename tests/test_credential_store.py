import asyncio
import json
from concurrent.futures import ThreadPoolExecutor

import pytest
from pydantic import SecretStr
from pydantic_ai.providers.openai_codex import OpenAICodexCredentials

from abaqus_sim_agent.coding.cli import run_cli
from abaqus_sim_agent.coding.credential_store import (
    CredentialStore,
    CredentialStoreError,
)
from abaqus_sim_agent.coding.model_selection import available_models
from abaqus_sim_agent.coding.providers import ModelProvider


def credentials(suffix="old"):
    return OpenAICodexCredentials(
        access_token=f"private-access-{suffix}",
        refresh_token=f"private-refresh-{suffix}",
        account_id="fixture-account",
    )


def test_cli_login_is_reused_by_a_new_store_and_logout_keeps_environment(
    monkeypatch, capsys
):
    monkeypatch.setattr("getpass.getpass", lambda _: "saved-private-key")
    assert run_cli(["--login", "openai"]) == 0
    monkeypatch.setenv("OPENAI_API_KEY", "environment-private-key")
    store = CredentialStore()
    assert (
        store.api_keys({ModelProvider.OPENAI: SecretStr("environment-private-key")})[
            ModelProvider.OPENAI_RESPONSES
        ].get_secret_value()
        == "saved-private-key"
    )
    assert run_cli(["--list-models"]) == 0
    assert "gpt-4.1" in capsys.readouterr().out
    assert run_cli(["--logout", "openai-chat"]) == 0
    assert store.read() == {}
    assert available_models()  # removing saved auth does not unset the environment
    assert "private-key" not in capsys.readouterr().out


def test_concurrent_writers_preserve_other_providers(tmp_path):
    path = tmp_path / "auth.json"
    providers = [
        ModelProvider.OPENAI,
        ModelProvider.ANTHROPIC,
        ModelProvider.GOOGLE,
        ModelProvider.OPENROUTER,
    ]
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(
            pool.map(
                lambda p: CredentialStore(path).save_key(p, SecretStr(p.value)),
                providers,
            )
        )
    assert set(CredentialStore(path).read()) == {p.value for p in providers}
    assert not list(tmp_path.glob("*.tmp"))


@pytest.mark.asyncio
async def test_refresh_survives_restart_and_cannot_resurrect_logout():
    store = CredentialStore()
    store.save_codex(credentials())
    source = store.codex_source()
    assert await source.load() == credentials()
    await source.save(credentials("new"))
    restarted = CredentialStore().codex_source()
    assert await restarted.load() == credentials("new")
    store.remove(ModelProvider.OPENAI_CODEX)
    with pytest.raises(CredentialStoreError, match="更新或删除"):
        await source.save(credentials("stale"))
    assert CredentialStore().read() == {}


@pytest.mark.asyncio
async def test_refresh_cannot_overwrite_a_new_login():
    store = CredentialStore()
    store.save_codex(credentials())
    source = store.codex_source()
    await source.load()
    store.save_codex(credentials("other-login"))
    with pytest.raises(CredentialStoreError, match="更新或删除"):
        await source.save(credentials("stale"))
    assert await store.codex_source().load() == credentials("other-login")


def test_bad_credentials_are_not_overwritten_or_echoed(tmp_path):
    path = tmp_path / "auth.json"
    path.write_text('{"openai": "private-invalid-secret"}', encoding="utf-8")
    store = CredentialStore(path)
    with pytest.raises(CredentialStoreError) as exc:
        store.save_key(ModelProvider.ANTHROPIC, SecretStr("new-secret"))
    assert "private-invalid-secret" not in str(exc.value)
    assert json.loads(path.read_text()) == {"openai": "private-invalid-secret"}


def test_failed_replace_preserves_previous_credentials(tmp_path, monkeypatch):
    from pathlib import Path

    store = CredentialStore(tmp_path / "auth.json")
    store.save_key(ModelProvider.OPENAI, SecretStr("old"))
    original = store.path.read_bytes()

    def fail(*args):
        raise PermissionError("private-token-in-error")

    monkeypatch.setattr(Path, "replace", fail)
    with pytest.raises(CredentialStoreError) as exc:
        store.save_key(ModelProvider.OPENAI, SecretStr("new"))
    assert "private-token" not in str(exc.value)
    assert store.path.read_bytes() == original
    assert not list(tmp_path.glob("*.tmp"))


def test_cli_codex_login_and_model_inventory(monkeypatch, capsys):
    from pydantic_ai.providers import openai_codex

    class Flow:
        def authorization_url(self):
            return "https://auth.example.invalid/authorize"

        async def exchange_code_from_callback(self):
            return credentials()

    monkeypatch.setattr(openai_codex, "OpenAICodexOAuthFlow", Flow)
    assert run_cli(["--login", "openai-codex"]) == 0
    assert "private-access" not in capsys.readouterr().out
    assert run_cli(["--list-models"]) == 0
    models = json.loads(capsys.readouterr().out)
    assert models == []
    assert asyncio.run(CredentialStore().codex_source().load()) == credentials()


def test_cli_copilot_token_input_does_not_require_an_oauth_application(monkeypatch):
    monkeypatch.setattr("getpass.getpass", lambda _: "private-copilot-token")
    assert run_cli(["--login", "github-copilot"]) == 0
    assert (
        CredentialStore().api_keys({})[ModelProvider.GITHUB_COPILOT].get_secret_value()
        == "private-copilot-token"
    )


def test_cancelled_cli_key_input_does_not_create_a_store(monkeypatch):
    def cancel(_):
        raise EOFError

    monkeypatch.setattr("getpass.getpass", cancel)
    assert run_cli(["--login", "openai"]) == 1
    assert not CredentialStore().path.exists()


@pytest.mark.parametrize("value", ["", " "])
def test_empty_key_does_not_replace_saved_key(value):
    store = CredentialStore()
    store.save_key(ModelProvider.OPENAI, SecretStr("old"))
    with pytest.raises(CredentialStoreError):
        store.save_key(ModelProvider.OPENAI, SecretStr(value))
    assert store.api_keys({})[ModelProvider.OPENAI].get_secret_value() == "old"


@pytest.mark.asyncio
async def test_native_codex_factory_refreshes_and_persists_through_sdk():
    import httpx2

    from abaqus_sim_agent.coding.model_connections import create_model

    store = CredentialStore()
    store.save_codex(credentials())
    seen = []

    def handle(request):
        if request.url.host == "auth.openai.com":
            assert request.url.path == "/oauth/token"
            assert b"private-refresh-old" in request.content
            assert "authorization" not in request.headers
            return httpx2.Response(
                200,
                json={
                    "access_token": "private-access-new",
                    "refresh_token": "private-refresh-new",
                },
            )
        token = request.headers["authorization"]
        seen.append(token)
        return httpx2.Response(401 if token.endswith("old") else 200, json={})

    transport = httpx2.AsyncClient(transport=httpx2.MockTransport(handle))
    _, client = create_model(
        gateway=ModelProvider.OPENAI_CODEX,
        model_name="gpt-5.5",
        api_key=SecretStr(""),
        http_client=transport,
    )
    try:
        response = await transport.get("https://chatgpt.com/backend-api/codex/fixture")
        assert response.status_code == 200
        assert seen == ["Bearer private-access-old", "Bearer private-access-new"]
        assert await CredentialStore().codex_source().load() == credentials("new")
    finally:
        await client.close()
