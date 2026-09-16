"""仅供宿主观察的阶段通知，不改变模型工具或持久运行合同。"""

import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class CodingProgress:
    kind: Literal[
        "checkpoint",
        "verification",
        "runtime_started",
        "runtime_finished",
        "model_started",
        "model_delta",
        "model_finished",
    ]
    phase: str
    turn: int
    task_revision: int
    checkpoint_json: bytes | None = None
    history_json: bytes | None = None
    question_json: bytes | None = None
    outcome_json: bytes | None = None
    verification_json: bytes | None = None
    reply_json: bytes | None = None
    stopped_reply_json: bytes | None = None


ProgressObserver = Callable[[CodingProgress], None]


def notify(observer: ProgressObserver | None, event: CodingProgress) -> None:
    if observer is not None:
        try:
            observer(event)
        except Exception:
            logging.getLogger(__name__).exception("CodingRun observer failed")
