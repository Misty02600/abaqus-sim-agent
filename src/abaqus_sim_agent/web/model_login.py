"""复用框架 OAuth 流程；登录令牌只保留在本机 Web 宿主内存。"""

import asyncio
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, SecretStr

from ..coding.providers import ModelProvider, require_provider_dependencies


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    provider: ModelProvider
    client_id: str | None = None


class LoginStatus(BaseModel):
    id: str
    state: Literal["waiting", "completed", "failed"]
    url: str
    user_code: str | None = None
    error: str | None = None


class MemoryCredentials:
    def __init__(self, credentials: Any):
        self.credentials = credentials

    async def load(self):
        return self.credentials

    async def save(self, credentials):
        self.credentials = credentials


class ModelLogins:
    def __init__(self, controller: Any):
        self.controller = controller
        self.statuses: dict[str, LoginStatus] = {}
        self.tasks: dict[str, asyncio.Task[None]] = {}

    async def begin(self, request: LoginRequest) -> LoginStatus:
        """启动一个限时登录任务，返回可展示的 URL/授权码。

        Note:
            仅在调用者主动请求时访问认证服务。登录结果保存在宿主内存，
            返回值不包含令牌；宿主关闭时必须调用 close 取消未完成任务。
        """
        require_provider_dependencies(request.provider)
        if any(not task.done() for task in self.tasks.values()):
            raise ValueError("已有登录正在等待完成。")
        if request.provider is ModelProvider.OPENAI_CODEX:
            from pydantic_ai.providers.openai_codex import OpenAICodexOAuthFlow

            flow = OpenAICodexOAuthFlow()
            status = LoginStatus(
                id=uuid4().hex, state="waiting", url=flow.authorization_url()
            )
            wait = flow.exchange_code_from_callback
        elif request.provider is ModelProvider.GITHUB_COPILOT:
            from pydantic_ai.providers.github_copilot import GitHubCopilotOAuthFlow

            if not request.client_id or not request.client_id.strip():
                raise ValueError("GitHub 设备登录需要 OAuth Client ID。")
            flow = GitHubCopilotOAuthFlow(client_id=request.client_id.strip())
            authorization = await flow.start()
            status = LoginStatus(
                id=uuid4().hex,
                state="waiting",
                url=authorization.verification_uri,
                user_code=authorization.user_code,
            )
            wait = flow.wait_for_authorization
        else:
            raise ValueError("此提供商使用 API Key 或本机云凭据。")
        self.statuses[status.id] = status

        async def finish() -> None:
            try:
                async with asyncio.timeout(600):
                    credentials = await wait()
                with self.controller._mutex:
                    if request.provider is ModelProvider.OPENAI_CODEX:
                        self.controller.model_credentials[request.provider] = (
                            MemoryCredentials(credentials)
                        )
                    else:
                        self.controller.api_keys[request.provider] = SecretStr(
                            credentials.access_token
                        )
                self.statuses[status.id] = status.model_copy(
                    update={"state": "completed"}
                )
            except asyncio.CancelledError:
                raise
            except Exception:
                # OAuth exceptions can include response bodies/tokens. Never echo them.
                self.statuses[status.id] = status.model_copy(
                    update={"state": "failed", "error": "登录未完成，请重试。"}
                )

        self.tasks[status.id] = asyncio.create_task(finish())
        return status

    async def close(self) -> None:
        for task in self.tasks.values():
            task.cancel()
        await asyncio.gather(*self.tasks.values(), return_exceptions=True)
        self.tasks.clear()
        self.statuses.clear()

    async def cancel(self, login_id: str) -> None:
        task = self.tasks.get(login_id)
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            self.statuses[login_id] = self.statuses[login_id].model_copy(
                update={"state": "failed", "error": "登录已取消。"}
            )
