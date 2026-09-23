from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
from dataclasses import asdict, dataclass
from importlib.metadata import version
from pathlib import Path
from typing import Literal, cast

from abaqus_sim_agent.api_catalog import ApiCatalog
from abaqus_sim_agent.coding.api_lookup import DEFAULT_PRODUCT_API_CATALOG_MODE
from abaqus_sim_agent.coding.model import (
    DEEPSEEK_V4_FLASH,
    DEFAULT_MODEL_HTTP_RETRIES,
    DeepSeekGateway,
    take_gateway_api_key_from_environment,
)
from abaqus_sim_agent.evaluation.api_repair import (
    ApiRepairEvalGate,
    load_case_specs,
    run_evaluation,
)

REPO_ROOT = Path(__file__).parents[3]
DEFAULT_MANIFEST = (
    REPO_ROOT / "benchmarks/coding/runtime_feedback_repair_cases_v1.jsonl"
)
DEFAULT_WHEEL = REPO_ROOT / "var/api-catalog/sources/abqpy-2024.10-py3-none-any.whl"
Profile = Literal["off", "on"]
Verdict = Literal["pass", "fail", "unknown"]
PairOutcome = Literal["win", "tie", "loss", "unpaired_unknown"]


@dataclass(frozen=True, slots=True)
class Trial:
    trial_id: str
    case_id: str
    repetition: int
    profile: Profile


def build_trial_plan(
    case_ids: tuple[str, ...], repetitions: int = 2
) -> tuple[Trial, ...]:
    if repetitions < 1:
        raise ValueError("repetitions must be positive")
    trials = []
    for repetition in range(1, repetitions + 1):
        profiles: tuple[Profile, Profile] = (
            ("off", "on") if repetition % 2 else ("on", "off")
        )
        for case_id in case_ids:
            for profile in profiles:
                trials.append(
                    Trial(
                        trial_id=f"r{repetition}-{case_id}-{profile}",
                        case_id=case_id,
                        repetition=repetition,
                        profile=profile,
                    )
                )
    return tuple(trials)


def paired_outcome(control: Verdict, treatment: Verdict) -> PairOutcome:
    if "unknown" in {control, treatment}:
        return "unpaired_unknown"
    if control == "fail" and treatment == "pass":
        return "win"
    if control == "pass" and treatment == "fail":
        return "loss"
    return "tie"


async def run_pilot(
    run_root: Path,
    *,
    manifest_path: Path = DEFAULT_MANIFEST,
    repetitions: int = 2,
    model_gateway: DeepSeekGateway = DeepSeekGateway.OPENCODE_GO,
    model_name: str = DEEPSEEK_V4_FLASH,
    http_retries: int = DEFAULT_MODEL_HTTP_RETRIES,
    api_catalog_dir: Path,
    abqpy_static_wheel: Path = DEFAULT_WHEEL,
    static_preflight_cache_dir: Path,
) -> dict[str, object]:
    api_key = take_gateway_api_key_from_environment(model_gateway)
    if not api_key.get_secret_value():
        raise ValueError("paired pilot requires the selected Provider API key")
    await asyncio.to_thread(ApiCatalog.open, api_catalog_dir)
    root, cases = await asyncio.gather(
        asyncio.to_thread(_prepare_root, run_root),
        asyncio.to_thread(load_case_specs, manifest_path),
    )
    case_ids = tuple(case.case_id for case in cases)
    trials = build_trial_plan(case_ids, repetitions)
    bindings = await asyncio.to_thread(
        _bindings,
        manifest_path,
        cases,
        abqpy_static_wheel,
        api_catalog_dir,
    )
    plan = {
        "schema_version": "runtime-feedback-repair-paired-v2",
        "model_provider": model_gateway.value,
        "model": model_name,
        "http_retries": http_retries,
        "repetitions": repetitions,
        "profiles": {"control": "off", "treatment": "on"},
        "api_catalog_mode": DEFAULT_PRODUCT_API_CATALOG_MODE,
        "bindings": bindings,
        "versions": {
            "pydantic_ai": version("pydantic-ai-slim"),
            "pydantic_ai_harness": version("pydantic-ai-harness"),
            "basedpyright": version("basedpyright"),
        },
        "trials": [asdict(trial) for trial in trials],
    }
    await asyncio.to_thread(_write_new, root / "manifest.json", plan)
    observations = []
    for index, trial in enumerate(trials, start=1):
        trial_root = root / trial.trial_id
        gate: ApiRepairEvalGate | None = None
        error = None
        try:
            gate = await run_evaluation(
                trial_root,
                manifest_path=manifest_path,
                model_gateway=model_gateway,
                model_name=model_name,
                http_retries=http_retries,
                api_catalog_mode=DEFAULT_PRODUCT_API_CATALOG_MODE,
                api_catalog_dir=api_catalog_dir,
                case_ids=(trial.case_id,),
                static_preflight_mode=trial.profile,
                abqpy_static_wheel=(
                    abqpy_static_wheel if trial.profile == "on" else None
                ),
                static_preflight_cache_dir=(
                    static_preflight_cache_dir if trial.profile == "on" else None
                ),
                repetition=trial.repetition,
                api_key=api_key,
            )
        except Exception as exc:
            error = type(exc).__name__
        observation = await asyncio.to_thread(
            _observe, trial_root, trial, gate=gate, error=error
        )
        observations.append(observation)
        print(
            f"[{index}/{len(trials)}] {trial.trial_id}: {observation['verdict']}",
            flush=True,
        )
    current_bindings = await asyncio.to_thread(
        _bindings,
        manifest_path,
        cases,
        abqpy_static_wheel,
        api_catalog_dir,
    )
    if bindings != current_bindings:
        raise RuntimeError("paired-pilot inputs changed during execution")
    pairs = _pairs(observations, case_ids, repetitions)
    report = {
        "schema_version": "runtime-feedback-repair-paired-report-v2",
        "manifest_sha256": _sha256(root / "manifest.json"),
        "trial_count": len(observations),
        "valid_pair_count": sum(
            item["outcome"] != "unpaired_unknown" for item in pairs
        ),
        "wins": sum(item["outcome"] == "win" for item in pairs),
        "ties": sum(item["outcome"] == "tie" for item in pairs),
        "losses": sum(item["outcome"] == "loss" for item in pairs),
        "unpaired_unknowns": sum(
            item["outcome"] == "unpaired_unknown" for item in pairs
        ),
        "trials": observations,
        "pairs": pairs,
        "claim_boundary": (
            "Development paired pilot only; detection, model adoption, runtime cost, "
            "and task success remain separate evidence."
        ),
    }
    await asyncio.to_thread(_write_new, root / "report.json", report)
    return report


def _observe(
    trial_root: Path,
    trial: Trial,
    *,
    gate: ApiRepairEvalGate | None,
    error: str | None,
) -> dict[str, object]:
    summary_path = trial_root / "evaluation_summary.json"
    summary = (
        json.loads(summary_path.read_text(encoding="utf-8"))
        if summary_path.is_file()
        else None
    )
    failures = summary.get("failures", []) if isinstance(summary, dict) else []
    verdict: Verdict
    if error is not None or failures:
        verdict = "unknown"
    elif gate is not None and gate.attempts == gate.passes == 1:
        verdict = "pass"
    elif gate is not None and gate.attempts == 1:
        verdict = "fail"
    else:
        verdict = "unknown"
    metrics = {}
    if isinstance(summary, dict) and summary.get("cases"):
        metrics = summary["cases"][0].get("metrics", {})
    return {
        **asdict(trial),
        "verdict": verdict,
        "error_type": error,
        "metrics": metrics,
        "summary_path": str(summary_path) if summary_path.is_file() else None,
    }


def _pairs(
    observations: list[dict[str, object]],
    case_ids: tuple[str, ...],
    repetitions: int,
) -> list[dict[str, object]]:
    by_key = {
        (item["case_id"], item["repetition"], item["profile"]): item
        for item in observations
    }
    result = []
    for repetition in range(1, repetitions + 1):
        for case_id in case_ids:
            control = by_key[(case_id, repetition, "off")]
            treatment = by_key[(case_id, repetition, "on")]
            control_verdict = cast(Verdict, control["verdict"])
            treatment_verdict = cast(Verdict, treatment["verdict"])
            outcome = paired_outcome(control_verdict, treatment_verdict)
            result.append(
                {
                    "case_id": case_id,
                    "repetition": repetition,
                    "control": control["verdict"],
                    "treatment": treatment["verdict"],
                    "outcome": outcome,
                }
            )
    return result


def _bindings(
    manifest_path: Path,
    cases,
    wheel: Path,
    api_catalog_dir: Path,
) -> dict[str, str]:
    paths = [
        manifest_path,
        REPO_ROOT / "pyproject.toml",
        REPO_ROOT / "uv.lock",
        REPO_ROOT / "src/abaqus_sim_agent/verification/api_repair.py",
        REPO_ROOT / "src/abaqus_sim_agent/skills/abaqus-2024-coding/SKILL.md",
    ]
    for directory in (
        REPO_ROOT / "src/abaqus_sim_agent/coding",
        REPO_ROOT / "src/abaqus_sim_agent/execution",
        REPO_ROOT / "src/abaqus_sim_agent/evaluation",
    ):
        paths.extend(directory.rglob("*.py"))
    paths.extend(REPO_ROOT / case.fixture_dir / case.entry_script for case in cases)
    result = {
        path.relative_to(REPO_ROOT).as_posix(): _sha256(path)
        for path in sorted(set(paths))
    }
    result["abqpy_static_wheel"] = _sha256(wheel)
    catalog_root = api_catalog_dir.resolve(strict=True)
    for name in ("manifest.json", "methods.jsonl"):
        result[f"api_catalog/{name}"] = _sha256(catalog_root / name)
    members = catalog_root / "members.jsonl"
    if members.is_file():
        result["api_catalog/members.jsonl"] = _sha256(members)
    return result


def _prepare_root(path: Path) -> Path:
    root = path.resolve()
    root.mkdir(parents=True, exist_ok=False)
    return root


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.resolve(strict=True).read_bytes()).hexdigest()


def _write_new(path: Path, payload: object) -> None:
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(payload, stream, ensure_ascii=False, indent=2, sort_keys=True)
        stream.write("\n")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run the paired runtime-feedback pilot."
    )
    parser.add_argument("--run-root", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--repetitions", type=int, default=2)
    parser.add_argument(
        "--model-provider",
        type=DeepSeekGateway,
        choices=list(DeepSeekGateway),
        default=DeepSeekGateway.OPENCODE_GO,
    )
    parser.add_argument("--model", default=DEEPSEEK_V4_FLASH)
    parser.add_argument("--http-retries", type=int, default=DEFAULT_MODEL_HTTP_RETRIES)
    parser.add_argument("--api-catalog-dir", type=Path, required=True)
    parser.add_argument("--abqpy-static-wheel", type=Path, default=DEFAULT_WHEEL)
    parser.add_argument("--static-preflight-cache-dir", type=Path, required=True)
    args = parser.parse_args()
    asyncio.run(
        run_pilot(
            args.run_root,
            manifest_path=args.manifest,
            repetitions=args.repetitions,
            model_gateway=args.model_provider,
            model_name=args.model,
            http_retries=args.http_retries,
            api_catalog_dir=args.api_catalog_dir,
            abqpy_static_wheel=args.abqpy_static_wheel,
            static_preflight_cache_dir=args.static_preflight_cache_dir,
        )
    )


if __name__ == "__main__":
    main()
