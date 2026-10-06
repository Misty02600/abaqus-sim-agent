"""通过原生模型响应钩子恢复一次没有可用输出的长度截断。"""

from pydantic_ai import ModelRetry, RunContext
from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.messages import ModelResponse, TextPart, ThinkingPart
from pydantic_ai.models import ModelRequestContext

from .dependencies import CodingAgentDependencies

RECOVERY_PROMPT = (
    "Your previous response reached the output-token limit without text or a tool call. "
    "Keep the next response concise and take one small actionable step. "
    "Split large work across multiple tool calls."
)


class OutputTruncationRecovery(AbstractCapability[CodingAgentDependencies]):
    async def after_model_request(
        self,
        ctx: RunContext[CodingAgentDependencies],
        *,
        request_context: ModelRequestContext,
        response: ModelResponse,
    ) -> ModelResponse:
        if response.finish_reason != "length" or not all(
            isinstance(part, ThinkingPart)
            or (isinstance(part, TextPart) and not part.content)
            for part in response.parts
        ):
            return response
        if ctx.deps.output_truncation_recovery_used:
            # Let the native length error run after history and usage are saved.
            return response
        ctx.deps.output_truncation_recovery_used = True
        raise ModelRetry(RECOVERY_PROMPT)
