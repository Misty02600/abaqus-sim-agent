from collections import deque
from collections.abc import Iterable
from dataclasses import dataclass

from pydantic import BaseModel, ValidationError

from abaqus_sim_agent.inference.base import (
    ModelInvocationError,
    ModelMessage,
    ModelProfile,
    ModelUsage,
    StructuredModelRequest,
    StructuredModelResult,
)


@dataclass(frozen=True, slots=True)
class ScriptedModelResponse:
    output: BaseModel
    usage: ModelUsage | None = None
    provider_response_id: str | None = None
    resolved_model: str | None = None


type ScriptedModelStep = ScriptedModelResponse | ModelInvocationError


@dataclass(frozen=True, slots=True)
class RecordedModelCall:
    messages: tuple[ModelMessage, ...]
    output_model_name: str


class ScriptedStructuredModel:
    """供测试使用的 FIFO 模型替身；不会联网、重试或隐式 fallback。"""

    def __init__(
        self,
        profile: ModelProfile,
        steps: Iterable[ScriptedModelStep],
    ) -> None:
        self._profile = profile
        self._steps = deque(steps)
        self._calls: list[RecordedModelCall] = []

    @property
    def profile(self) -> ModelProfile:
        return self._profile

    @property
    def calls(self) -> tuple[RecordedModelCall, ...]:
        return tuple(self._calls)

    @property
    def remaining(self) -> int:
        return len(self._steps)

    async def generate_structured[OutputT: BaseModel](
        self,
        request: StructuredModelRequest[OutputT],
    ) -> StructuredModelResult[OutputT]:
        self._calls.append(
            RecordedModelCall(
                messages=request.messages,
                output_model_name=request.output_model.__name__,
            )
        )
        if not self._steps:
            raise AssertionError("scripted model response queue is empty")

        step = self._steps.popleft()
        if isinstance(step, ModelInvocationError):
            if step.profile != self.profile:
                raise AssertionError(
                    "scripted error profile does not match fake profile"
                )
            raise step

        try:
            output = request.output_model.model_validate(
                step.output.model_dump(mode="python")
            )
        except ValidationError as exc:
            raise AssertionError(
                "scripted output does not satisfy the requested model"
            ) from exc

        return StructuredModelResult(
            output=output,
            profile=self.profile,
            usage=step.usage,
            provider_response_id=step.provider_response_id,
            resolved_model=step.resolved_model,
        )
