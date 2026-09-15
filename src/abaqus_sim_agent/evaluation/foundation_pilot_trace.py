"""Foundation 离线轨迹统计；不读取或输出模型推理正文。"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

from pydantic_ai import ModelMessagesTypeAdapter
from pydantic_ai.messages import (
    ModelResponse,
    RetryPromptPart,
    ToolCallPart,
    ToolReturnPart,
)

RUNTIME_TOOLS = {"run_cae_script", "run_solver_job", "run_abaqus_python"}


def summarize_trace(state: Path, usage: dict[str, Any]) -> dict[str, Any]:
    """按已保存响应和工具反馈统计，不把中断期间的请求或计费补成零。

    Args:
        state: 单条 trial 的宿主状态目录。
        usage: outcome 中记录的累计 usage，用于与实际响应核对。

    Returns:
        文件覆盖、逐响应分组及返回/校验拒绝等观测；不包含消息正文。

    Raises:
        ValueError: 保存的响应用量与 outcome 不一致，或工具调用 ID 重复。
    """
    path = state / "history.json"
    messages = (
        ModelMessagesTypeAdapter.validate_json(path.read_bytes())
        if path.is_file()
        else []
    )
    calls: dict[str, dict[str, Any]] = {}
    responses: list[dict[str, Any]] = []
    totals: Counter[str] = Counter()
    loaded = False
    wrote = False
    delivered_instruction_bytes = 0
    unknown_instruction_returns = 0
    for message_index, message in enumerate(messages):
        if isinstance(message, ModelResponse):
            current_calls = [p for p in message.parts if isinstance(p, ToolCallPart)]
            names = [p.tool_name for p in current_calls]
            if not wrote and "write_file" in names:
                phase = "first_write_response"
            elif wrote:
                phase = "after_first_write"
            elif loaded:
                phase = "after_load_before_write"
            else:
                phase = "before_load_and_write"
            row = {
                "history_message_index": message_index,
                "tools_in_same_response": names,
                "phase": phase,
                "input_tokens": message.usage.input_tokens,
                "output_tokens": message.usage.output_tokens,
                "cache_read_tokens": message.usage.cache_read_tokens,
            }
            responses.append(row)
            totals.update(
                {
                    key: row[key]
                    for key in ("input_tokens", "output_tokens", "cache_read_tokens")
                }
            )
            for part in current_calls:
                if part.tool_call_id in calls:
                    raise ValueError("duplicate tool call id in saved history")
                calls[part.tool_call_id] = {
                    "tool": part.tool_name,
                    "status": "unresolved",
                    "history_message_index": message_index,
                }
            wrote = wrote or "write_file" in names
            continue
        for part in message.parts:
            if not isinstance(part, (ToolReturnPart, RetryPromptPart)):
                continue
            call = calls.get(part.tool_call_id)
            if call is None:
                continue
            if isinstance(part, ToolReturnPart):
                # outcome='failed' 是一次返回的工具失败，不是有效读取。
                call["status"] = (
                    "returned" if part.outcome == "success" else "tool_failed"
                )
                if part.tool_name == "load_capability" and part.outcome == "success":
                    loaded = True
                    instructions = (
                        part.content.get("instructions")
                        if isinstance(part.content, dict)
                        else None
                    )
                    if isinstance(instructions, str):
                        delivered_instruction_bytes += len(instructions.encode("utf-8"))
                    else:
                        unknown_instruction_returns += 1
            else:
                call["status"] = (
                    "validation_rejected"
                    if isinstance(part.content, list)
                    else "retry_feedback"
                )
    for key, observed in (
        ("input_tokens", totals["input_tokens"]),
        ("output_tokens", totals["output_tokens"]),
        ("model_requests", len(responses)),
    ):
        if path.is_file() and usage.get(key) is not None and observed != usage[key]:
            raise ValueError(f"saved history and outcome disagree on {key}")
    by_tool: dict[str, Counter[str]] = {}
    for call in calls.values():
        by_tool.setdefault(call["tool"], Counter())[call["status"]] += 1
    phase_cost: dict[str, Counter[str]] = {}
    for row in responses:
        costs = phase_cost.setdefault(row["phase"], Counter())
        costs["responses"] += 1
        costs.update(
            {
                key: row[key]
                for key in ("input_tokens", "output_tokens", "cache_read_tokens")
            }
        )
    if not messages:
        position = "history_unavailable"
    elif not responses:
        position = "before_first_complete_response"
    elif calls and all(c["status"] != "unresolved" for c in calls.values()):
        position = "after_tool_feedback"
    else:
        position = "incomplete_response_or_tool_feedback"
    persistence = state / "step-persistence"
    effect_statuses: Counter[str] = Counter()
    for ledger in persistence.rglob("tool_effects.jsonl"):
        for line in ledger.read_text(encoding="utf-8").splitlines():
            record = json.loads(line)
            effect_statuses[record["status"]] += 1
    return {
        "files": {
            name: (state / name).is_file()
            for name in (
                "history.json",
                "events.jsonl",
                "request.json",
                "outcome.json",
                "resume_state.json",
            )
        },
        "snapshots": sum(1 for _ in persistence.rglob("snapshots/*.json")),
        "effect_records_by_status": dict(effect_statuses),
        "complete_responses": len(responses),
        "input_tokens": totals["input_tokens"],
        "output_tokens": totals["output_tokens"],
        "cache_read_tokens": totals["cache_read_tokens"],
        "delivered_capability_instruction_bytes": (
            delivered_instruction_bytes if not unknown_instruction_returns else None
        ),
        "runtime_tool_attempts": sum(
            c["tool"] in RUNTIME_TOOLS for c in calls.values()
        ),
        "tools": {name: dict(counts) for name, counts in by_tool.items()},
        "responses": responses,
        "phase_cost": {name: dict(counts) for name, counts in phase_cost.items()},
        "last_observed_position": position,
        "interpretation": "Tool return is not semantic self-verification. Response groups may contain parallel tools. HTTP attempts and unreturned-token billing are unknown.",
    }
