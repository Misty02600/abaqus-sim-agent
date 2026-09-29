from __future__ import annotations

import os
from urllib.parse import urlsplit

import httpx
import pytest
import requests.adapters
from pydantic_ai import models

from abaqus_sim_agent.coding import (
    credential_store,
    domain_knowledge,
    model_catalog,
    model_selection,
    static_preflight,
)
from abaqus_sim_agent.coding.providers import API_KEY_ENVIRONMENTS

# Collection and ordinary tests must not accidentally use a provider model.
models.ALLOW_MODEL_REQUESTS = False


@pytest.fixture(autouse=True)
def offline_environment(monkeypatch, tmp_path):
    from tests.support.model_catalog import catalog_payload

    store = model_catalog.ModelCatalog(tmp_path / "model-cache.json")
    monkeypatch.setattr(model_catalog, "catalog_store", lambda: store)
    monkeypatch.setattr(model_catalog, "download_catalog", catalog_payload)
    monkeypatch.setattr(
        credential_store,
        "credential_path",
        lambda: tmp_path / "host-auth" / "auth.json",
    )
    monkeypatch.setattr(models, "ALLOW_MODEL_REQUESTS", False)
    monkeypatch.setattr(
        model_selection, "local_authenticated_providers", lambda: frozenset()
    )
    monkeypatch.setattr(
        static_preflight, "static_cache_root", lambda: tmp_path / "static-cache"
    )
    for name in tuple(os.environ):
        if name.startswith(("LOGFIRE_", "PHOENIX_", "OTEL_")):
            monkeypatch.delenv(name)
    for name in {name for names in API_KEY_ENVIRONMENTS.values() for name in names}:
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


@pytest.fixture
def product_catalog_path(tmp_path_factory):
    from abaqus_sim_agent.api_catalog import ApiCatalog

    path = tmp_path_factory.mktemp("product-catalog") / "catalog"
    ApiCatalog([], runtime_release="2024").write(path)
    return path


@pytest.fixture(autouse=True)
def bundled_product_catalog(monkeypatch, product_catalog_path):
    from abaqus_sim_agent.coding import options

    monkeypatch.setattr(options, "bundled_catalog_path", lambda: product_catalog_path)
