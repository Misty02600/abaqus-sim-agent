"""Skill测评的证据读取与确定性路由判据；不执行Agent或进行语义审查。"""

import hashlib
import json
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

from pydantic_ai import ModelMessagesTypeAdapter
from pydantic_ai.messages import ModelResponse, ToolCallPart


@dataclass(frozen=True)
class SkillEvidence:
    load_ids: tuple[str, ...]
    load_call_count: int
    runtime_requested: bool
    analysis: object
    outcome: object

    def observation_fields(self, case_id: str) -> dict[str, object]:
        loaded = set(self.load_ids)
        return {
            "case_id": case_id,
            "load_sequence": list(self.load_ids),
            "unique_loaded": sorted(loaded),
            "load_call_count": self.load_call_count,
            "duplicate_load_call_count": len(self.load_ids) - len(loaded),
            "runtime_requested": self.runtime_requested,
            "analysis": self.analysis,
            "outcome": self.outcome,
        }


def read_tool_calls(state: Path) -> list[dict[str, object]]:
    history = state / "history.json"
    if not history.is_file():
        return []
    messages = ModelMessagesTypeAdapter.validate_json(history.read_bytes())
    return [
        {
            "tool": part.tool_name,
            "arguments": part.args_as_dict(),
            "tool_call_id": part.tool_call_id,
        }
        for message in messages
        if isinstance(message, ModelResponse)
        for part in message.parts
        if isinstance(part, ToolCallPart)
    ]


def read_skill_evidence(case_dir: Path) -> SkillEvidence:
    """读取已存证据，保留旧观察器对缺失及损坏文件的不同处理。

    Note:
        缺失文件记为空；analysis无效时留下标记，history/outcome损坏则传播异常。
        runtime名称沿用这组冻结测评的工具合同，不推断实际工具执行成功。
    """
    state = case_dir / "state"
    calls = read_tool_calls(state)
    load_calls = [call for call in calls if call["tool"] == "load_capability"]
    load_ids = tuple(
        str(arguments["id"])
        for call in load_calls
        if isinstance(arguments := call["arguments"], dict) and arguments.get("id")
    )
    analysis_path = case_dir / "workspace" / "analysis.json"
    analysis: object = None
    if analysis_path.is_file():
        try:
            analysis = json.loads(analysis_path.read_text(encoding="utf-8"))
        except (UnicodeError, ValueError):
            analysis = {"invalid_json": True}
    outcome_path = state / "outcome.json"
    outcome = (
        json.loads(outcome_path.read_text(encoding="utf-8"))
        if outcome_path.is_file()
        else None
    )
    return SkillEvidence(
        load_ids=load_ids,
        load_call_count=len(load_calls),
        runtime_requested=any(
            call["tool"] in {"run_cae_script", "run_solver_job", "run_abaqus_python"}
            for call in calls
        ),
        analysis=analysis,
        outcome=outcome,
    )


def score_skill_route(
    load_ids: Sequence[str],
    *,
    required_loads: Sequence[str],
    allowed_loads: Sequence[str],
    forbidden_loads: Sequence[str],
) -> dict[str, object]:
    loaded, required = set(load_ids), set(required_loads)
    allowed, forbidden = set(allowed_loads), set(forbidden_loads)
    return {
        "required_loads": list(required_loads),
        "allowed_loads": list(allowed_loads),
        "forbidden_loads": list(forbidden_loads),
        "required_load_recall": len(loaded & required) / len(required)
        if required
        else float(not loaded),
        "forbidden_load_count": len(loaded & forbidden),
        "unexpected_loads": sorted(loaded - allowed),
        "exact_route_pass": required <= loaded and loaded <= allowed,
    }


def loaded_skill_body_bytes(
    load_ids: Iterable[str], *, skill_root: Path, capability_ids: Sequence[str]
) -> int:
    return sum(
        path.stat().st_size
        for capability_id in dict.fromkeys(load_ids)
        if capability_id in capability_ids
        and (path := skill_root / capability_id / "SKILL.md").is_file()
    )


def skill_content_hashes(
    skill_root: Path, capability_ids: Sequence[str]
) -> dict[str, str]:
    return {
        capability_id: hashlib.sha256(
            (skill_root / capability_id / "SKILL.md").read_bytes()
        ).hexdigest()
        for capability_id in capability_ids
    }


def required_int(item: dict[str, object], key: str) -> int:
    value = item[key]
    if not isinstance(value, (str, int, float)):
        raise TypeError(f"{key} must be numeric")
    return int(value)


def write_new_json(path: Path, value: object) -> None:
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
