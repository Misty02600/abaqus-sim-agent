"""单条T1开发试点：先prepare供人放行，run必须匹配spec摘要；不自动扩为正式批次。"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import math
import platform
import time
from importlib.metadata import version
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
from abaqus_sim_agent.coding.workspace import WorkspaceResolver
from abaqus_sim_agent.evaluation.task_baseline import (
    PostRunCheck,
    TaskTrialReview,
    baseline_execution_config,
    prepare_task,
)
from abaqus_sim_agent.evaluation.task_baseline_runner import (
    _check_evidence,
    _context,
    _json,
    _load,
    _run_batch,
    _safe_file,
    _write_summary,
)
from abaqus_sim_agent.evaluation.task_baseline_service import run_service_live
from abaqus_sim_agent.execution import DEFAULT_ABAQUS_2024_LAUNCHER, Abaqus2024Runner
from abaqus_sim_agent.verification.assets import (
    bar_tension_3d_oracle_script,
    plate_tension_oracle_script,
)
from abaqus_sim_agent.verification.bar_measurements import BarTension3DMeasurements
from abaqus_sim_agent.verification.plate_reference import PlateTensionMeasurements

REPOSITORY = Path(__file__).resolve().parents[3]
REFERENCES = {"right_mean_u1": 0.1, "mean_s11": 210.0, "left_sum_rf1": -4200.0}


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def source_binding() -> dict[str, str]:
    paths = [
        REPOSITORY / "uv.lock",
        REPOSITORY / "pyproject.toml",
        *sorted((REPOSITORY / "src/abaqus_sim_agent").rglob("*.py")),
        REPOSITORY / "src/abaqus_sim_agent/skills/abaqus-2024-coding/SKILL.md",
    ]
    return {p.relative_to(REPOSITORY).as_posix(): digest(p) for p in paths}


def runtime_versions() -> dict[str, str]:
    return {
        "python": platform.python_version(),
        **{
            name: version(name)
            for name in (
                "pydantic",
                "pydantic-ai-slim",
                "pydantic-ai-harness",
                "pydantic-evals",
                "openai",
                "httpx",
            )
        },
    }


def task_snapshot() -> dict:
    prepared = prepare_task("requirements-plate-v1")
    return {
        "goal": prepared.candidate.service_goal,
        "files": {p: b.decode("utf-8") for p, b in prepared.workspace_files.items()},
        "new_deliverables": list(prepared.candidate.new_deliverables),
        "preserved_inputs": list(prepared.candidate.preserved_inputs),
    }


class T1PilotSpec(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)
    schema_version: Literal["t1-live-pilot-spec-v1"] = "t1-live-pilot-spec-v1"
    task_id: Literal["requirements-plate-v1"] = "requirements-plate-v1"
    run_root: Path
    launcher: Path
    launcher_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    configuration: ModelExecutionConfig
    budget: CodingRunBudget
    task: dict
    reference_values: dict[str, float]
    relative_tolerance: float = Field(
        default=0.02, ge=0.02, le=0.02, allow_inf_nan=False
    )
    absolute_tolerance: float = Field(default=0.0, ge=0.0, le=0.0, allow_inf_nan=False)
    oracle_timeout_seconds: float = Field(
        default=120.0, ge=120.0, le=120.0, allow_inf_nan=False
    )
    max_host_oracle_calls: Literal[1] = 1
    source_sha256: dict[str, str]
    versions: dict[str, str]

    @model_validator(mode="after")
    def approved_shape(self):
        if (
            self.configuration != baseline_execution_config()
            or self.reference_values != REFERENCES
        ):
            raise ValueError(
                "pilot must use the declared Go/high profile and fixed T1 references"
            )
        if (
            not self.run_root.is_absolute()
            or not str(self.run_root).isascii()
            or self.run_root != self.run_root.resolve()
        ):
            raise ValueError("pilot root must be resolved absolute ASCII")
        if not self.launcher.is_absolute() or self.launcher != self.launcher.resolve():
            raise ValueError("launcher must be resolved and absolute")
        maximum = prepare_task(self.task_id).candidate.candidate_budget
        for field in CodingRunBudget.model_fields:
            if getattr(self.budget, field) > getattr(maximum, field):
                raise ValueError(
                    f"pilot budget exceeds the proposed upper bound: {field}"
                )
        return self


def prepare_pilot_spec(
    path: Path, run_root: Path, launcher: Path = DEFAULT_ABAQUS_2024_LAUNCHER
) -> T1PilotSpec:
    """生成待批准spec；只读源文件与launcher，不检查许可证或发送模型请求。"""
    if path.exists() or run_root.exists():
        raise ValueError("spec and pilot root must be new paths")
    if path.resolve().is_relative_to(run_root.resolve()):
        raise ValueError("spec must be outside the new pilot run root")
    launcher = launcher.resolve(strict=True)
    spec = T1PilotSpec(
        run_root=run_root.resolve(),
        launcher=launcher,
        launcher_sha256=digest(launcher),
        configuration=baseline_execution_config(),
        budget=prepare_task("requirements-plate-v1").candidate.candidate_budget,
        task=task_snapshot(),
        reference_values=REFERENCES,
        source_sha256=source_binding(),
        versions=runtime_versions(),
    )
    write_text_atomic(path, _json(spec))
    return spec


def validate_pilot_spec(path: Path, approved_sha256: str) -> T1PilotSpec:
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != approved_sha256:
        raise ValueError("explicit approval must match the exact pilot spec SHA-256")
    spec = T1PilotSpec.model_validate_json(raw)
    if (
        spec.source_sha256 != source_binding()
        or spec.versions != runtime_versions()
        or spec.task != task_snapshot()
    ):
        raise ValueError(
            "pilot source, environment or task drifted; prepare a new spec"
        )
    if not spec.launcher.is_file() or digest(spec.launcher) != spec.launcher_sha256:
        raise ValueError("pilot launcher missing or changed")
    return spec


async def run_pilot(path: Path, *, approved_sha256: str) -> dict:
    """执行已由用户放行的一个T1及最多一次独立取数；没有sample重试或自动resume。

    Note:
        摘要参数记录操作者对指定spec的确认，不是用户权限认证系统。
        本函数会实际调用Go及本机Abaqus，调用前必须已取得用户授权。
    """
    spec = await asyncio.to_thread(validate_pilot_spec, path, approved_sha256)
    approved_raw = await asyncio.to_thread(path.read_bytes)
    if hashlib.sha256(approved_raw).hexdigest() != approved_sha256:
        raise ValueError("pilot spec changed after validation")
    if spec.run_root.exists():
        raise ValueError("pilot root already exists; no implicit rerun")
    key = take_gateway_api_key_from_environment(spec.configuration.model_provider)
    if not key.get_secret_value():
        raise ValueError("OPENCODE_API_KEY is missing")
    prepared = prepare_task(spec.task_id)
    from dataclasses import replace

    prepared = replace(
        prepared,
        candidate=prepared.candidate.model_copy(
            update={"candidate_budget": spec.budget}
        ),
    )

    async def task(context):
        return await run_service_live(
            context,
            prepared,
            launcher=spec.launcher,
            api_key=key,
            config=spec.configuration,
        )

    await _run_batch(
        spec.run_root,
        task,
        purpose="live_development_pilot",
        max_concurrency=1,
        manifest_extra={
            "spec": spec.model_dump(mode="json"),
            "spec_json": approved_raw.decode("utf-8"),
            "approved_spec_sha256": approved_sha256,
        },
    )
    return await verify_pilot(spec.run_root)


def _finite_results(path: Path) -> dict[str, float]:
    data = json.loads(path.read_bytes())
    if not isinstance(data, dict) or any(
        type(data.get(k)) not in (int, float) or not math.isfinite(data[k])
        for k in REFERENCES
    ):
        raise ValueError("results.json must contain all three finite numeric fields")
    return {k: float(data[k]) for k in REFERENCES}


async def verify_pilot(root: Path) -> dict:
    """对已批准pilot最多独立读一次ODB；重复调用只复用已保存结果，不重跑Agent。"""
    root = await asyncio.to_thread(root.resolve, strict=True)
    with CodingRunFileLock(root):
        batch = _load(root)
        if batch.purpose != "live_development_pilot" or batch.status == "running":
            raise ValueError("post verification requires a settled live pilot")
        row = batch.records[0]
        _check_evidence(root, row)
        if row.post_run_check:
            return _write_summary(root, batch)
        manifest = json.loads(_safe_file(root, "manifest.json").read_bytes())
        original_spec = manifest["pilot"]["spec_json"]
        if (
            hashlib.sha256(original_spec.encode()).hexdigest()
            != manifest["pilot"]["approved_spec_sha256"]
            or json.loads(original_spec) != manifest["pilot"]["spec"]
        ):
            raise ValueError("pilot approval/spec binding changed")
        spec = T1PilotSpec.model_validate_json(original_spec)
        if (
            spec.run_root != root
            or spec.source_sha256 != source_binding()
            or spec.versions != runtime_versions()
            or digest(spec.launcher) != spec.launcher_sha256
        ):
            raise ValueError("bound pilot environment changed before post verification")
        updated = await _verify_numeric_trial(
            root,
            row,
            launcher=spec.launcher,
            timeout_seconds=spec.oracle_timeout_seconds,
        )
        batch = batch.model_copy(update={"records": (updated,)})
        write_text_atomic(root / "batch.json", _json(batch))
        return _write_summary(root, batch)


async def _verify_numeric_trial(
    root: Path,
    row: TaskTrialReview,
    *,
    launcher: Path,
    timeout_seconds: float,
    bar: bool = False,
) -> TaskTrialReview:
    """在调用方批次锁内独立取数一次；不执行Agent、不修改原trial结果。"""
    _check_evidence(root, row)
    if row.post_run_check:
        return row
    stem = "bar_tension" if bar else "plate_tension"
    references = {**REFERENCES, "left_sum_rf1": -21000.0} if bar else REFERENCES
    context = _context(root, row.slot)
    post_dir = context.state_dir / "post-verification"
    if post_dir.is_symlink() or post_dir.resolve() != post_dir:
        raise ValueError(
            "post-verification directory must not redirect outside its declared path"
        )
    original = context.state_dir / "trial-result.json"
    report_path = post_dir / "report.json"
    started = time.monotonic()
    report = {
        "state": "unknown",
        "trial_result_sha256": digest(original),
        "scope": "bar3d_numeric_only_not_full_physics"
        if bar
        else "t1_numeric_only_not_full_physics",
        "host_calls": 0,
        "checks": {},
        "human_review_required": True,
    }
    before = WorkspaceResolver().snapshot(context.workspace)
    if report_path.is_file():
        report = json.loads(
            _safe_file(root, report_path.relative_to(root).as_posix()).read_bytes()
        )
    elif post_dir.exists():
        report.update(
            {
                "host_calls": 1,
                "error": "prior oracle reservation has no completed receipt; do not retry",
            }
        )
        write_text_atomic(report_path, json.dumps(report, indent=2))
    else:
        post_dir.mkdir()
        odb = context.workspace / f"{stem}.odb"
        if (
            row.artifact_acceptance != "pass"
            or row.execution_acceptance != "pass"
            or not odb.is_file()
        ):
            report["error"] = (
                "required fresh artifacts unavailable; no oracle dispatched"
            )
        else:
            # Reserve before external execution. A crash cannot silently buy another attempt.
            report["host_calls"] = 1
            write_text_atomic(
                post_dir / "attempt.json",
                json.dumps(
                    {
                        "status": "reserved",
                        "trial_result_sha256": report["trial_result_sha256"],
                    }
                ),
            )
            request_path, output_path = (
                post_dir / "request.json",
                post_dir / "oracle.json",
            )
            write_text_atomic(request_path, json.dumps({"odb_path": str(odb)}))
            runner = Abaqus2024Runner(context.workspace, post_dir, launcher=launcher)
            try:
                result = await runner.run_trusted_abaqus_python(
                    bar_tension_3d_oracle_script()
                    if bar
                    else plate_tension_oracle_script(),
                    args=(str(request_path), str(output_path)),
                    workspace_artifacts=(f"{stem}.odb",),
                    timeout_seconds=timeout_seconds,
                )
                write_text_atomic(
                    post_dir / "execution.json", result.model_dump_json(indent=2)
                )
                if not result.stage_succeeded or not output_path.is_file():
                    raise RuntimeError("independent ODB extraction did not complete")
                measured = (
                    BarTension3DMeasurements if bar else PlateTensionMeasurements
                ).model_validate_json(output_path.read_bytes(), strict=True)
                if (
                    await asyncio.to_thread(Path(measured.odb_path).resolve)
                    != odb.resolve()
                ):
                    raise ValueError("oracle returned the wrong ODB identity")
                values = {k: getattr(measured, k) for k in references}
                if not all(math.isfinite(v) for v in values.values()):
                    raise ValueError(
                        "independent extraction returned non-finite values"
                    )
                checks = {
                    k: abs(values[k] - expected) <= 0.02 * abs(expected)
                    for k, expected in references.items()
                }
                checks["element_contract"] = measured.element_type_counts == {
                    ("C3D8R" if bar else "CPS4R"): 80
                }
                try:
                    reported = _finite_results(context.workspace / "results.json")
                    checks["agent_matches_odb"] = all(
                        abs(reported[k] - values[k]) <= 0.02 * abs(values[k])
                        for k in references
                    )
                except (ValueError, OSError):
                    checks["agent_matches_odb"] = False
                report.update(
                    {
                        "state": "pass" if all(checks.values()) else "fail",
                        "checks": checks,
                        "measurements": measured.model_dump(mode="json"),
                    }
                )
            except Exception as exc:
                report["error"] = f"{type(exc).__name__}: {exc}"
        if WorkspaceResolver().snapshot(context.workspace) != before:
            report.update(
                {
                    "state": "unknown",
                    "error": "workspace changed during independent extraction",
                }
            )
        report["wall_seconds"] = time.monotonic() - started
        write_text_atomic(report_path, json.dumps(report, ensure_ascii=False, indent=2))
    if report["trial_result_sha256"] != digest(original):
        raise ValueError("post report belongs to another trial")
    evidence = {
        p.relative_to(root).as_posix(): digest(p)
        for p in post_dir.iterdir()
        if p.is_file() and p.name not in ("report.json", "coding_run.lock")
    }
    check = PostRunCheck(
        state=report["state"],
        report_path=report_path.relative_to(root).as_posix(),
        report_sha256=digest(report_path),
        trial_result_sha256=digest(original),
        evidence_sha256=evidence,
        host_calls=report["host_calls"],
        wall_seconds=float(report.get("wall_seconds", 0.0)),
    )
    updated = row.model_copy(update={"post_run_check": check})
    _check_evidence(root, updated)

    return updated


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("prepare", "run", "verify", "review-template"))
    parser.add_argument("path", type=Path)
    parser.add_argument("--run-root", type=Path)
    parser.add_argument("--launcher", type=Path, default=DEFAULT_ABAQUS_2024_LAUNCHER)
    parser.add_argument("--approve-spec-sha256")
    args = parser.parse_args()
    if args.mode == "prepare":
        if args.run_root is None:
            parser.error("prepare requires --run-root")
        prepare_pilot_spec(args.path, args.run_root, args.launcher)
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
            parser.error("run requires --approve-spec-sha256 after user approval")
        print(
            json.dumps(
                asyncio.run(
                    run_pilot(args.path, approved_sha256=args.approve_spec_sha256)
                ),
                indent=2,
            )
        )
    elif args.mode == "verify":
        print(json.dumps(asyncio.run(verify_pilot(args.path)), indent=2))
    else:
        root = args.path.resolve(strict=True)
        with CodingRunFileLock(root):
            batch = _load(root)
            if batch.purpose != "live_development_pilot" or batch.status == "running":
                raise ValueError("review template requires a settled live pilot")
            row = batch.records[0]
            from abaqus_sim_agent.evaluation.task_baseline import REVIEW_ITEMS

            print(
                json.dumps(
                    {
                        "source": "human",
                        "reviewer": "",
                        "reviewed_at": None,
                        "trial_result_sha256": digest(
                            _context(root, row.slot).state_dir / "trial-result.json"
                        ),
                        "checks": {
                            k: {"state": "unknown", "reason": "", "evidence": []}
                            for k in REVIEW_ITEMS
                        },
                    },
                    indent=2,
                )
            )


if __name__ == "__main__":
    main()
