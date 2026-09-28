import httpx
import pytest
from pydantic import SecretStr
from pydantic_ai import Agent
from pydantic_ai.models import check_allow_model_requests

from abaqus_sim_agent.coding.model import create_deepseek_v4_flash_model


@pytest.mark.asyncio
async def test_provider_requests_are_blocked_by_default():
    model, client = create_deepseek_v4_flash_model(api_key=SecretStr("fixture-key"))
    try:
        with pytest.raises(RuntimeError, match="ALLOW_MODEL_REQUESTS is False"):
            await Agent(model).run("This request must never reach a provider")
    finally:
        await client.close()


def test_sdk_opt_in_still_blocks_external_sync_http(monkeypatch, mock_model_requests):
    check_allow_model_requests()
    with httpx.HTTPTransport() as transport:

        def reached_network(*args, **kwargs):
            pytest.fail("Network pool was reached")

        monkeypatch.setattr(transport._pool, "handle_request", reached_network)
        with pytest.raises(pytest.fail.Exception, match="External HTTP is disabled"):
            transport.handle_request(httpx.Request("GET", "https://provider.invalid"))


@pytest.mark.asyncio
async def test_sdk_opt_in_still_blocks_external_async_http(
    monkeypatch, mock_model_requests
):
    check_allow_model_requests()
    async with httpx.AsyncHTTPTransport() as transport:

        async def reached_network(*args, **kwargs):
            pytest.fail("Network pool was reached")

        monkeypatch.setattr(transport._pool, "handle_async_request", reached_network)
        with pytest.raises(pytest.fail.Exception, match="External HTTP is disabled"):
            await transport.handle_async_request(
                httpx.Request("GET", "https://provider.invalid")
            )
