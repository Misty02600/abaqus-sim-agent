from collections.abc import Callable, Mapping
from typing import Protocol

from abaqus_sim_agent.coding.contracts import (
    ClarificationAnswer,
    ClarificationRequest,
)


class ClarificationProvider(Protocol):
    def get_answer(
        self,
        request: ClarificationRequest,
    ) -> ClarificationAnswer | None: ...


class ConsoleClarificationProvider:
    def __init__(self, input_fn: Callable[[str], str] = input) -> None:
        self._input_fn = input_fn

    def get_answer(
        self,
        request: ClarificationRequest,
    ) -> ClarificationAnswer | None:
        value = self._input_fn(
            f"[{request.field_id}] {request.question}\n"
            f"Why it matters: {request.impact}\n> "
        ).strip()
        if not value:
            return None
        return ClarificationAnswer(
            field_id=request.field_id,
            value=value,
            source="console",
        )


class ScriptedClarificationProvider:
    def __init__(self, answers: Mapping[str, str]) -> None:
        self._answers = dict(answers)

    def get_answer(
        self,
        request: ClarificationRequest,
    ) -> ClarificationAnswer | None:
        value = self._answers.pop(request.field_id, None)
        if value is None:
            return None
        return ClarificationAnswer(
            field_id=request.field_id,
            value=value,
            source="scripted",
        )
