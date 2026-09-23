"""公开回复投影与宿主取消入口，不拥有运行工具或最终验收。"""

import asyncio
import json
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart
from pydantic_core import from_json

PUBLIC_OUTPUTS = {
    "final_result_ResponseDecision",
    "final_result_ReadyForVerificationDecision",
    "final_result_FailedDecision",
}
MAX_PREVIEW = 65_536


class UserStoppedError(RuntimeError):
    pass


class RunCancellationControl:
    """把宿主停止请求交给当前 AgentRun，并保留请求来源与收尾错误。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._requested = False
        self._source: Literal["user_stop", "delete"] | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._cancel: Callable[[], None] | None = None
        self._cleanup_error: str | None = None
        self.operation_id = uuid4().hex

    @property
    def requested(self) -> bool:
        with self._lock:
            return self._requested

    @property
    def bound(self) -> bool:
        with self._lock:
            return self._loop is not None and self._cancel is not None

    @property
    def user_stop_requested(self) -> bool:
        with self._lock:
            return self._source == "user_stop"

    @property
    def delete_requested(self) -> bool:
        with self._lock:
            return self._source == "delete"

    @property
    def cleanup_error(self) -> str | None:
        with self._lock:
            return self._cleanup_error

    def bind(self, cancel: Callable[[], None]) -> None:
        with self._lock:
            self._loop = asyncio.get_running_loop()
            self._cancel = cancel
            if self._requested:
                self._loop.call_soon(cancel)

    def unbind(self) -> None:
        with self._lock:
            self._cancel = None
            self._loop = None

    def request_cancel(self, source: Literal["user_stop", "delete"] = "delete") -> bool:
        """请求取消并返回是否已交给当前 AgentRun。"""
        with self._lock:
            if self._requested:
                if source == "delete":
                    self._source = "delete"
                return self._loop is not None and self._cancel is not None
            self._requested = True
            self._source = source
            if self._loop is None or self._cancel is None:
                return False
            self._loop.call_soon_threadsafe(self._cancel)
            return True

    def record_cleanup_error(self, exc: Exception) -> None:
        with self._lock:
            self._cleanup_error = f"{type(exc).__name__}: {exc}"


class LiveReply(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")
    request_id: str
    text: str = ""


class StoppedReply(LiveReply):
    turn: int = Field(ge=1)
    response_number: int = Field(ge=1)
    task_revision: int = Field(ge=0)


def public_response_text(response: ModelResponse, *, partial: bool = False) -> str:
    texts: list[str] = []
    for part in response.parts:
        if isinstance(part, TextPart):
            texts.append(part.content)
        elif isinstance(part, ToolCallPart) and part.tool_name in PUBLIC_OUTPUTS:
            try:
                args = (
                    from_json(
                        part.args,
                        allow_partial="trailing-strings" if partial else False,
                    )
                    if isinstance(part.args, str)
                    else part.args
                )
            except ValueError:
                continue
            summary = args.get("summary") if isinstance(args, dict) else None
            if isinstance(summary, str):
                texts.append(summary)
    return "\n\n".join(texts)


def load_stopped_replies(state_dir: Path) -> tuple[StoppedReply, ...]:
    path = state_dir / "events.jsonl"
    if not path.exists():
        return ()
    if path.is_symlink() or path.is_junction():
        raise ValueError("event log must be a regular file")
    with path.open("rb") as source:
        data = source.read(16 * 1024 * 1024 + 1)
    if len(data) > 16 * 1024 * 1024:
        raise ValueError("event log exceeds display limit")
    result = []
    for line in data.splitlines():
        event = json.loads(line)
        if event.get("event") == "model_response_stopped":
            result.append(StoppedReply.model_validate(event["data"]))
    return tuple(result)


class ModelResponseControl:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._cancel: Callable[[], None] | None = None
        self._request_id: str | None = None
        self._stopped_id: str | None = None

    @property
    def requested(self) -> bool:
        with self._lock:
            return self._stopped_id is not None

    @property
    def request_id(self) -> str | None:
        with self._lock:
            return self._request_id if self._stopped_id is None else None

    def begin(self, cancel: Callable[[], None]) -> str:
        with self._lock:
            if self._stopped_id is not None:
                raise UserStoppedError("User stopped the model response")
            self._loop = asyncio.get_running_loop()
            self._cancel = cancel
            self._request_id = uuid4().hex
            return self._request_id

    def end(self) -> None:
        with self._lock:
            self._cancel = None
            self._request_id = None

    def stop(self, request_id: str) -> bool:
        with self._lock:
            if self._stopped_id == request_id:
                return True
            if (
                self._request_id != request_id
                or self._cancel is None
                or self._loop is None
            ):
                return False
            self._stopped_id = request_id
            self._loop.call_soon_threadsafe(self._deliver, request_id)
            return True

    def _deliver(self, request_id: str) -> None:
        with self._lock:
            cancel = self._cancel if self._request_id == request_id else None
        if cancel is not None:
            cancel()
