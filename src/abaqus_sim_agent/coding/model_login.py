"""CLI/Web 共用的主动登录；OAuth 协议和 Codex 刷新仍由 SDK 负责。"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from pydantic import SecretStr

from .credential_store import CredentialStore
from .providers import ModelProvider, require_provider_dependencies


class LoginUnavailable(ValueError):
    pass


@dataclass(frozen=True)
class LoginChallenge:
    url: str
    user_code: str | None
    complete: Callable[[], Awaitable[None]]


async def start_login(
    provider: ModelProvider, store: CredentialStore, *, client_id: str | None = None
) -> LoginChallenge:
    require_provider_dependencies(provider)
    if provider is ModelProvider.OPENAI_CODEX:
        from pydantic_ai.providers.openai_codex import OpenAICodexOAuthFlow

        flow = OpenAICodexOAuthFlow()

        async def complete_codex() -> None:
            store.save_codex(await flow.exchange_code_from_callback())

        return LoginChallenge(flow.authorization_url(), None, complete_codex)
    if provider is ModelProvider.GITHUB_COPILOT:
        from pydantic_ai.providers.github_copilot import GitHubCopilotOAuthFlow

        if not client_id or not client_id.strip():
            raise LoginUnavailable(
                "GitHub 设备登录需要自己的 OAuth Client ID；也可填写已有 Token。"
            )
        copilot = GitHubCopilotOAuthFlow(client_id=client_id.strip())
        authorization = await copilot.start()

        async def complete_copilot() -> None:
            credentials = await copilot.wait_for_authorization()
            # Use the SDK's access token directly; expired/revoked tokens require a new login.
            store.save_key(provider, SecretStr(credentials.access_token))

        return LoginChallenge(
            authorization.verification_uri, authorization.user_code, complete_copilot
        )
    raise ValueError("此提供商使用 API Key 或本机云凭据。")
