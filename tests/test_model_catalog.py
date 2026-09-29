import json
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from abaqus_sim_agent.coding import model_catalog
from abaqus_sim_agent.coding.model_catalog import (
    ModelCatalog,
    ModelCatalogUnavailable,
    download_catalog,
    model_metadata,
)
from abaqus_sim_agent.coding.model_connections import resolve_context_window
from abaqus_sim_agent.coding.model_selection import available_models
from abaqus_sim_agent.coding.providers import ModelProvider, provider_catalog
from tests.support.model_catalog import catalog_payload
from tests.support.web import configured_app, login


def unavailable():
    raise httpx.ConnectError("offline fixture")


def test_first_load_downloads_once_and_survives_offline_restart(tmp_path, monkeypatch):
    requests = []

    def download():
        requests.append(True)
        return catalog_payload()

    monkeypatch.setattr(model_catalog, "download_catalog", download)
    path = tmp_path / "models.json"
    store = ModelCatalog(path)
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: store.get(), range(4)))
    assert len(requests) == 1
    assert all(result == results[0] for result in results)
    assert json.loads(path.read_bytes()) == json.loads(catalog_payload())
    monkeypatch.setattr(model_catalog, "download_catalog", unavailable)
    assert ModelCatalog(path).get() == results[0]


def test_first_failure_without_cache_is_explicit_and_refresh_can_retry(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(model_catalog, "download_catalog", unavailable)
    store = ModelCatalog(tmp_path / "models.json")
    with pytest.raises(ModelCatalogUnavailable, match="没有可用缓存"):
        store.get()
    assert not store.path.exists()
    monkeypatch.setattr(model_catalog, "download_catalog", catalog_payload)
    assert store.get(refresh=True)["openai"]


@pytest.mark.parametrize(
    "bad_payload", [b"not-json", b"{}", b'{"error":"bad gateway"}']
)
def test_malformed_download_preserves_cache(tmp_path, monkeypatch, bad_payload):
    store = ModelCatalog(tmp_path / "models.json")
    original = store.get()
    saved = store.path.read_bytes()
    monkeypatch.setattr(model_catalog, "download_catalog", lambda: bad_payload)
    assert store.get(refresh=True) == original
    assert store.path.read_bytes() == saved
    assert ModelCatalog(store.path).get() == original


def test_valid_download_recovers_bad_cache_and_cache_write_failure_is_nonfatal(
    tmp_path, monkeypatch, caplog
):
    path = tmp_path / "models.json"
    path.write_bytes(b"broken cache")
    store = ModelCatalog(path)
    assert store.get()["openai"]
    assert json.loads(path.read_bytes())

    def fail(*args):
        raise PermissionError("fixture")

    monkeypatch.setattr(model_catalog, "write_bytes_atomic", fail)
    assert store.get(refresh=True)["openai"]
    assert "缓存写入失败" in caplog.text


def test_refresh_updates_data_without_changing_an_already_resolved_configuration(
    tmp_path, monkeypatch
):
    from abaqus_sim_agent.coding.options import RunOptions, model_execution

    store = model_catalog.catalog_store()
    original = store.get()
    config = model_execution(
        RunOptions(model_provider=ModelProvider.OPENAI, model_name="gpt-4.1")
    )
    updated = json.loads(catalog_payload())
    updated["openai"]["models"]["gpt-4.1"]["limit"]["context"] = 64_000
    updated["openai"]["models"]["new-fixture"] = updated["openai"]["models"]["gpt-4.1"]
    monkeypatch.setattr(
        model_catalog, "download_catalog", lambda: json.dumps(updated).encode()
    )
    assert "new-fixture" in store.get(refresh=True)["openai"]
    assert original["openai"]["gpt-4.1"].context_window == 1_047_576
    assert resolve_context_window(ModelProvider.OPENAI, "gpt-4.1") == 64_000
    assert config.context_window == 1_000_000


def test_model_directory_uses_no_sdk_name_fallback_or_pi_codex_data():
    entries = {p.id: p.models for p in provider_catalog()}
    for provider in [
        ModelProvider.OPENAI_CODEX,
        ModelProvider.BEDROCK_MANTLE,
        ModelProvider.HEROKU,
        ModelProvider.TYPESAFE,
    ]:
        assert entries[provider] == ()
    assert "gpt-4.1" in entries[ModelProvider.OPENAI]
    assert "computer-use-preview" not in entries[ModelProvider.OPENAI]
    assert "claude-sonnet-4-5" not in entries[ModelProvider.GOOGLE_CLOUD]
    assert "gemini-2.5-flash" in entries[ModelProvider.GOOGLE_CLOUD]
    assert resolve_context_window(ModelProvider.OPENAI_CODEX, "gpt-5.5") == 1_000_000


def test_explicit_unlisted_model_keeps_sdk_metadata_support(monkeypatch):
    monkeypatch.setattr(
        "abaqus_sim_agent.coding.model_connections.infer_model_profile",
        lambda _: {"context_window": 64_000},
    )
    assert (
        resolve_context_window(ModelProvider.BEDROCK_MANTLE, "unlisted-fixture")
        == 64_000
    )


def test_new_session_requires_catalog_even_for_a_known_sdk_name(tmp_path, monkeypatch):
    from abaqus_sim_agent.coding.options import RunOptions, resolve_run_config

    monkeypatch.setattr(model_catalog, "download_catalog", unavailable)
    monkeypatch.setenv("OPENAI_API_KEY", "fixture")
    with pytest.raises(ModelCatalogUnavailable):
        resolve_run_config(
            tmp_path,
            RunOptions(model_provider=ModelProvider.OPENAI, model_name="gpt-4.1"),
        )


def test_bad_cache_without_network_is_not_mistaken_for_a_valid_directory(
    tmp_path, monkeypatch
):
    path = tmp_path / "bad-models.json"
    path.write_bytes(b"invalid")
    monkeypatch.setattr(model_catalog, "download_catalog", unavailable)
    with pytest.raises(ModelCatalogUnavailable):
        ModelCatalog(path).get()
    assert path.read_bytes() == b"invalid"


@pytest.mark.parametrize(
    "provider",
    [ModelProvider.OPENAI, ModelProvider.GITHUB_COPILOT, ModelProvider.OPENROUTER],
)
def test_connected_provider_uses_upstream_metadata(provider):
    models = available_models({provider: SecretStr("fixture")})
    selected = [m for m in models if m.provider == provider]
    assert selected
    for model in selected:
        metadata = model_metadata(provider, model.name)
        assert metadata.tools
        assert metadata.text
        assert not metadata.deprecated
        assert model.context_window <= metadata.context_window


def test_download_uses_public_json_without_provider_credentials(monkeypatch):
    calls = []
    client_class = httpx.Client

    def handle(request):
        calls.append(request)
        return httpx.Response(200, content=catalog_payload())

    with client_class(transport=httpx.MockTransport(handle)) as client:
        monkeypatch.setattr(httpx, "get", client.get)
        assert download_catalog() == catalog_payload()
    assert str(calls[0].url) == model_catalog.MODELS_URL
    assert "authorization" not in calls[0].headers
    assert "cookie" not in calls[0].headers


def test_metadata_flags_and_missing_windows_are_handled():
    data = json.loads(catalog_payload())
    models = data["openai"]["models"]
    models["gpt-4.1"]["status"] = "deprecated"
    models["gpt-4o"]["tool_call"] = False
    models["unknown"] = {"tool_call": True}
    result = model_catalog.parse_catalog(json.dumps(data).encode())
    assert result["openai"]["gpt-4.1"].deprecated
    assert not result["openai"]["gpt-4o"].tools
    assert "unknown" not in result["openai"]


def test_web_catalog_failure_does_not_hide_provider_connections_and_can_retry(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(model_catalog, "download_catalog", unavailable)
    app, _ = configured_app(tmp_path)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        login(client)
        assert client.get("/api/v1/model-providers").status_code == 200
        failed = client.get("/api/v1/models")
        assert failed.status_code == 503
        assert failed.json()["code"] == "model_catalog_unavailable"
        assert "没有可用缓存" in failed.json()["message"]
        assert client.get("/api/v1/model-connections").status_code == 200
        monkeypatch.setattr(model_catalog, "download_catalog", catalog_payload)
        refreshed = client.get("/api/v1/models?refresh=true")
        assert refreshed.status_code == 200
        assert refreshed.json()


def test_provider_discovery_and_empty_auth_do_not_require_a_catalog(monkeypatch):
    monkeypatch.setattr(model_catalog, "download_catalog", unavailable)
    assert provider_catalog(include_models=False)
    assert available_models({}) == ()
