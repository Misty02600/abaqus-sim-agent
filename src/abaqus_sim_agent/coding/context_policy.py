from __future__ import annotations

import hashlib
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from pydantic_ai.messages import (
    LoadCapabilityCallPart,
    LoadCapabilityReturnPart,
    ModelMessage,
    ModelRequest,
    ModelResponse,
    SystemPromptPart,
    ToolAvailabilityDeltaPart,
)
from pydantic_ai.tools import RunContext
from pydantic_ai_harness.compaction import (
    ClearToolResults,
    SummarizingCompaction,
    TieredCompaction,
)

from .contracts import CapabilityDefinitionBinding

CONTEXT_COMPACTION_TARGET_TOKENS = 960_000
CONTEXT_COMPACTION_KEEP_TOKENS = 32_000
CONTEXT_POLICY_VERSION = "deepseek-1m-tiered-summary-v1"

PROJECT_SUMMARY_PROMPT = """\
You are replacing the older part of an Abaqus coding-agent conversation with a compact continuation summary.

Use these exact headings and omit only sections with no content:

## Intent
The active user goal, physical problem, units, and standing constraints.

## Key decisions
Accepted modeling, API, runtime, and verification decisions with their reasons.

## Artifacts
Exact workspace-relative files, job names, identifiers, and important hashes.

## Current state
What is complete, what remains, loaded capability IDs, model-observed runtime results, and current verifier feedback.

## Next steps
The smallest concrete actions needed to finish.

## Open questions
Missing physical inputs, unresolved evidence, or reconciliation blockers.

Runtime results in this summary are model-visible context, not authoritative host execution or verification state. Respond only with the summary.

<messages>
{messages}
</messages>\
"""


@dataclass(frozen=True, slots=True)
class CapabilityPreservingCompaction:
    inner: Any

    async def compact(
        self,
        messages: list[ModelMessage],
        ctx: RunContext[Any],
    ) -> list[ModelMessage]:
        capsules = _activation_capsules(messages)
        compacted = await self.inner.compact(messages, ctx)
        present = _loaded_capability_ids(compacted)
        missing = [capsule for cap_id, capsule in capsules if cap_id not in present]
        if not missing:
            return compacted
        index = _leading_system_context_length(compacted)
        restored = [message for capsule in missing for message in capsule]
        return [*compacted[:index], *restored, *compacted[index:]]


def build_context_policy() -> TieredCompaction[Any]:
    summarizer = CapabilityPreservingCompaction(
        SummarizingCompaction(
            model=None,
            max_messages=1,
            keep_tokens=CONTEXT_COMPACTION_KEEP_TOKENS,
            incremental=True,
            keep_user_messages=True,
            receipts=False,
            tool_return_max_chars=500,
            summary_prompt=PROJECT_SUMMARY_PROMPT,
        )
    )
    return TieredCompaction(
        target_tokens=CONTEXT_COMPACTION_TARGET_TOKENS,
        tiers=(
            ClearToolResults(
                max_messages=1,
                keep_pairs=3,
                min_clear_tokens=16_384,
            ),
            summarizer,
        ),
    )


def bind_filesystem_skills(
    skill_library: Path,
    capability_ids: tuple[str, ...],
) -> tuple[CapabilityDefinitionBinding, ...]:
    root = skill_library.resolve(strict=True)
    bindings: list[CapabilityDefinitionBinding] = []
    for capability_id in capability_ids:
        path = (root / capability_id / "SKILL.md").resolve(strict=True)
        if not path.is_relative_to(root) or not path.is_file() or path.is_symlink():
            raise ValueError(f"invalid filesystem Skill: {capability_id}")
        digest = hashlib.sha256(
            b"abaqus-sim-agent-filesystem-skill-v1\0"
            + capability_id.encode("utf-8")
            + b"\0"
            + path.read_bytes()
        ).hexdigest()
        bindings.append(
            CapabilityDefinitionBinding(
                capability_id=capability_id,
                definition_sha256=digest,
            )
        )
    return tuple(bindings)


def validate_filesystem_skill_bindings(
    skill_library: Path,
    expected: tuple[CapabilityDefinitionBinding, ...],
) -> None:
    current = bind_filesystem_skills(
        skill_library,
        tuple(item.capability_id for item in expected),
    )
    if current != expected:
        raise ValueError("filesystem Skill definition binding changed")


def _activation_capsules(
    messages: list[ModelMessage],
) -> list[tuple[str, tuple[ModelMessage, ModelMessage]]]:
    calls: dict[str, tuple[str, ModelResponse, LoadCapabilityCallPart]] = {}
    capsules: list[tuple[str, tuple[ModelMessage, ModelMessage]]] = []
    for message in messages:
        if isinstance(message, ModelResponse):
            for part in message.parts:
                if isinstance(part, LoadCapabilityCallPart) and part.capability_id:
                    calls[part.tool_call_id] = (part.capability_id, message, part)
            continue
        for part in message.parts:
            if not isinstance(part, LoadCapabilityReturnPart):
                continue
            prior = calls.get(part.tool_call_id)
            if prior is None:
                continue
            capability_id, response, call = prior
            deltas = [
                item
                for item in message.parts
                if isinstance(item, ToolAvailabilityDeltaPart)
            ]
            call_message = replace(response, parts=[call])
            return_message = replace(message, parts=[part, *deltas])
            capsules.append((capability_id, (call_message, return_message)))
    return capsules


def _loaded_capability_ids(messages: list[ModelMessage]) -> set[str]:
    calls: dict[str, str] = {}
    loaded: set[str] = set()
    for message in messages:
        for part in message.parts:
            if isinstance(part, LoadCapabilityCallPart) and part.capability_id:
                calls[part.tool_call_id] = part.capability_id
            elif isinstance(part, LoadCapabilityReturnPart):
                if capability_id := calls.get(part.tool_call_id):
                    loaded.add(capability_id)
    return loaded


def _leading_system_context_length(messages: list[ModelMessage]) -> int:
    count = 0
    for message in messages:
        if not isinstance(message, ModelRequest) or not message.parts:
            break
        if not all(isinstance(part, SystemPromptPart) for part in message.parts):
            break
        count += 1
    return count
