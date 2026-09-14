"""固定双任务：缺厚度板与完整三维杆；先封存并批准，再运行，默认不执行。"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from abaqus_sim_agent.coding.contracts import CodingRunBudget
from abaqus_sim_agent.coding.model import (
    ModelExecutionConfig,
    take_gateway_api_key_from_environment,
)
from abaqus_sim_agent.coding.persistence import write_text_atomic
from abaqus_sim_agent.coding.run_lock import CodingRunFileLock
from abaqus_sim_agent.evaluation.task_baseline import (
    baseline_execution_config,
    prepare_task,
    simple_slots,
    simple_task_candidates,
)
from abaqus_sim_agent.evaluation.task_baseline_pilot import (
    REFERENCES,
    _verify_numeric_trial,
    digest,
    runtime_versions,
    source_binding,
)
from abaqus_sim_agent.evaluation.task_baseline_runner import (
    _json,
    _load,
    _run_batch,
    _safe_file,
    _write_summary,
)
from abaqus_sim_agent.evaluation.task_baseline_service import run_service_live
from abaqus_sim_agent.execution import DEFAULT_ABAQUS_2024_LAUNCHER


def task_snapshots() -> dict:
    return {
        task.task_id: {
            "goal": task.service_goal,
            "files": {
                name: data.decode("utf-8")
                for name, data in prepare_task(task.task_id).workspace_files.items()
            },
            "new_deliverables": list(task.new_deliverables),
            "preserved_inputs": list(task.preserved_inputs),
        }
        for task in simple_task_candidates()
    }


def numeric_references() -> dict:
    return {
        "missing-input-plate-v1": dict(REFERENCES),
        "requirements-bar3d-v1": {**REFERENCES, "left_sum_rf1": -21000.0},
    }


class SimpleModelingSpec(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)
    schema_version: Literal[
        "simple-modeling-pair-spec-v1",
        "simple-modeling-pair-spec-v2",
        "simple-modeling-pair-spec-v3",
    ] = "simple-modeling-pair-spec-v1"
    question_protocol: Literal["ask_user_v1"] | None = Field(
        default=None, exclude_if=lambda v: v is None
    )
    answer_mode: Literal["human_confirmed"] | None = Field(
        default=None, exclude_if=lambda v: v is None
    )
    run_root: Path
    launcher: Path
    launcher_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    configuration: ModelExecutionConfig
    budget_per_task: CodingRunBudget
    slots: tuple[str, ...]
    tasks: dict
    reference_values: dict
    relative_tolerance: float = Field(
        default=0.02, ge=0.02, le=0.02, allow_inf_nan=False
    )
    max_task_concurrency: Literal[2] = 2
    max_runtime_concurrency: Literal[1] = 1
    queue_in_task_wall: Literal[True] = True
    host_calls_per_task: Literal[1] = 1
    host_timeout_seconds: float = Field(
        default=120.0, ge=120.0, le=120.0, allow_inf_nan=False
    )
    source_sha256: dict[str, str]
    versions: dict[str, str]

    @model_validator(mode="after")
    def fixed_profile(self):
        if self.schema_version in (
            "simple-modeling-pair-spec-v2",
            "simple-modeling-pair-spec-v3",
        ):
            if self.question_protocol != "ask_user_v1":
                raise ValueError("pair spec v2 requires ask_user_v1")
        elif "question_protocol" in self.model_fields_set:
            raise ValueError("old pair spec cannot switch question protocol")
        if self.schema_version == "simple-modeling-pair-spec-v3":
            if self.answer_mode != "human_confirmed":
                raise ValueError("pair spec v3 requires human_confirmed answers")
        elif "answer_mode" in self.model_fields_set:
            raise ValueError("old pair spec cannot switch answer mode")
        if (
            self.configuration != baseline_execution_config()
            or self.budget_per_task != simple_task_candidates()[0].candidate_budget
            or self.slots != simple_slots()
            or self.tasks != task_snapshots()
            or self.reference_values != numeric_references()
        ):
            raise ValueError(
                "simple modeling spec must match the fixed two-task profile"
            )
        if (
            not self.run_root.is_absolute()
            or not str(self.run_root).isascii()
            or self.run_root != self.run_root.resolve()
        ):
            raise ValueError("run root must be resolved absolute ASCII")
        if not self.launcher.is_absolute() or self.launcher != self.launcher.resolve():
            raise ValueError("launcher must be resolved absolute")
        return self


def prepare_spec(
    path: Path, run_root: Path, launcher: Path = DEFAULT_ABAQUS_2024_LAUNCHER
) -> SimpleModelingSpec:
    """只读封存两题、预算、参考及环境；不查询许可证、密钥或Provider。"""
    if (
        path.exists()
        or run_root.exists()
        or path.resolve().is_relative_to(run_root.resolve())
    ):
        raise ValueError("spec must be new and outside the new run root")
    launcher = launcher.resolve(strict=True)
    spec = SimpleModelingSpec(
        schema_version="simple-modeling-pair-spec-v3",
        question_protocol="ask_user_v1",
        answer_mode="human_confirmed",
        run_root=run_root.resolve(),
        launcher=launcher,
        launcher_sha256=digest(launcher),
        configuration=baseline_execution_config(),
        budget_per_task=simple_task_candidates()[0].candidate_budget,
        slots=simple_slots(),
        tasks=task_snapshots(),
        reference_values=numeric_references(),
        source_sha256=source_binding(),
        versions=runtime_versions(),
    )
    write_text_atomic(path, _json(spec))
    return spec


def _validate_raw(raw: bytes, approved_sha256: str) -> SimpleModelingSpec:
    if hashlib.sha256(raw).hexdigest() != approved_sha256:
        raise ValueError("explicit approval must match the exact spec SHA-256")
    spec = SimpleModelingSpec.model_validate_json(raw)
    if (
        spec.source_sha256 != source_binding()
        or spec.versions != runtime_versions()
        or digest(spec.launcher) != spec.launcher_sha256
    ):
        raise ValueError("source/environment/launcher drifted; prepare a new spec")
    return spec


async def run_batch(path: Path, approved_sha256: str) -> dict:
    """仅在用户已批准两个任务及独立取数预算后调用；不追加样本或自动续跑。"""
    raw = await asyncio.to_thread(path.read_bytes)
    spec = await asyncio.to_thread(_validate_raw, raw, approved_sha256)
    if spec.run_root.exists():
        raise ValueError("batch root already exists; no implicit rerun")
    key = take_gateway_api_key_from_environment(spec.configuration.model_provider)
    if not key.get_secret_value():
        raise ValueError("OPENCODE_API_KEY is missing")
    gate = asyncio.Semaphore(1)
    prepared = {
        task.task_id: prepare_task(task.task_id) for task in simple_task_candidates()
    }

    async def task(context):
        return await run_service_live(
            context,
            prepared[context.slot.split("/")[0]],
            launcher=spec.launcher,
            api_key=key,
            config=spec.configuration,
            runtime_gate=gate,
            question_protocol=spec.question_protocol or "legacy_decision",
            answer_mode=spec.answer_mode or "scripted_fixture",
        )

    await _run_batch(
        spec.run_root,
        task,
        purpose="live_simple_modeling",
        max_concurrency=2,
        manifest_extra={
            "spec": spec.model_dump(mode="json"),
            "spec_json": raw.decode("utf-8"),
            "approved_spec_sha256": approved_sha256,
        },
    )
    return await verify_batch(spec.run_root)


async def verify_batch(root: Path) -> dict:
    """Agent全部结束后顺序取数；每题最多一次，重入只复用记录。"""
    root = await asyncio.to_thread(root.resolve, strict=True)
    with CodingRunFileLock(root):
        batch = _load(root)
        if batch.purpose != "live_simple_modeling" or batch.status != "finished":
            raise ValueError("requires a finished simple modeling batch")
        if all(row.post_run_check for row in batch.records):
            return _write_summary(root, batch)
        manifest = json.loads(_safe_file(root, "manifest.json").read_bytes())["pilot"]
        spec = _validate_raw(
            manifest["spec_json"].encode(), manifest["approved_spec_sha256"]
        )
        if spec.run_root != root or spec.model_dump(mode="json") != manifest["spec"]:
            raise ValueError("manifest spec/root binding mismatch")
        for index, row in enumerate(batch.records):
            updated = await _verify_numeric_trial(
                root,
                row,
                launcher=spec.launcher,
                timeout_seconds=spec.host_timeout_seconds,
                bar=row.slot.startswith("requirements-bar3d-v1/"),
            )
            rows = list(batch.records)
            rows[index] = updated
            batch = batch.model_copy(update={"records": tuple(rows)})
            write_text_atomic(root / "batch.json", _json(batch))
        return _write_summary(root, batch)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("prepare", "run", "verify"))
    parser.add_argument("path", type=Path)
    parser.add_argument("--run-root", type=Path)
    parser.add_argument("--launcher", type=Path, default=DEFAULT_ABAQUS_2024_LAUNCHER)
    parser.add_argument("--approve-spec-sha256")
    args = parser.parse_args()
    if args.mode == "prepare":
        if args.run_root is None:
            parser.error("prepare requires --run-root")
        prepare_spec(args.path, args.run_root, args.launcher)
        print(
            json.dumps(
                {
                    "spec": str(args.path.resolve()),
                    "sha256": digest(args.path),
                    "execution_started": False,
                }
            )
        )
    elif args.mode == "run":
        if not args.approve_spec_sha256:
            parser.error("run requires approval of the exact spec digest")
        print(
            json.dumps(
                asyncio.run(run_batch(args.path, args.approve_spec_sha256)), indent=2
            )
        )
    else:
        print(json.dumps(asyncio.run(verify_batch(args.path)), indent=2))


if __name__ == "__main__":
    main()
