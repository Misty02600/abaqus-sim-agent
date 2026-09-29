"""Web 登录任务与进度；成功结果持久保存到宿主凭据文件。"""

import asyncio
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict

from ..coding.credential_store import CredentialStoreError
from ..coding.model_login import LoginUnavailable, start_login
from ..coding.providers import ModelProvider


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


class ModelLogins:
    def __init__(self, controller: Any):
        self.controller = controller
        self.statuses: dict[str, LoginStatus] = {}
        self.tasks: dict[str, asyncio.Task[None]] = {}

    async def begin(self, request: LoginRequest) -> LoginStatus:
        """启动一个限时登录任务，返回可展示的 URL/授权码。

        Note:
            仅在调用者主动请求时访问认证服务。登录结果保存到宿主凭据文件，
            返回值不包含令牌；宿主关闭时必须调用 close 取消未完成任务。
        """
        if any(not task.done() for task in self.tasks.values()):
            raise ValueError("已有登录正在等待完成。")
        challenge = await start_login(
            request.provider,
            self.controller.credential_store,
            client_id=request.client_id,
        )
        status = LoginStatus(
            id=uuid4().hex,
            state="waiting",
            url=challenge.url,
            user_code=challenge.user_code,
        )
        self.statuses[status.id] = status

        async def finish() -> None:
            try:
                async with asyncio.timeout(600):
                    await challenge.complete()
                self.statuses[status.id] = status.model_copy(
                    update={"state": "completed"}
                )
            except asyncio.CancelledError:
                raise
            except (CredentialStoreError, LoginUnavailable) as exc:
                self.statuses[status.id] = status.model_copy(
                    update={"state": "failed", "error": str(exc)}
                )
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
