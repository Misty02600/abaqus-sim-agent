"""离线封存下一轮测量规格，不加载模型、不查询索引、不执行批次。"""

import argparse
import json
from importlib.metadata import version
from pathlib import Path

from .behavior_measurement import file_digest
from .document_behavior_audit import rubric
from .document_behavior_profile import CALIBRATED_PROFILE, model_settings


def prepare_spec(repo: Path, source_seal: Path) -> dict:
    repo = repo.resolve(strict=True)
    cases_path = repo / "benchmarks/coding/document_policy_behavior_cases_v1.json"
    policy_spec = repo / "benchmarks/rag/document_policy_spec_v1.json"
    cases = json.loads(cases_path.read_bytes())["cases"]
    previous = json.loads(source_seal.read_bytes())
    if (
        previous["profile"] != "document-policy-live-behavior-v1"
        or previous["budget"]["max_abaqus_calls"] != 0
        or previous["model"]["settings"] != model_settings("legacy")
        or previous["model"]["http_retries"] != 0
        or len(cases) != 8
        or len({c["id"] for c in cases}) != 8
    ):
        raise ValueError("expected the frozen eight-case document-only legacy profile")
    if file_digest(cases_path) != previous["cases_sha256"]:
        raise ValueError("case text changed from historical baseline")
    if file_digest(policy_spec) != previous["spec_sha256"]:
        raise ValueError("policy spec changed from historical baseline")
    sources = previous["source_files"]
    for path, expected in sources.items():
        if file_digest(Path(path)) != expected:
            raise ValueError("bound source changed: " + path)
    files = {
        p.relative_to(repo).as_posix(): file_digest(p)
        for p in sorted((repo / "src/abaqus_sim_agent").rglob("*.py"))
    }
    for path in (
        cases_path,
        policy_spec,
        repo / "uv.lock",
        repo / "src/abaqus_sim_agent/skills/abaqus-2024-coding/SKILL.md",
    ):
        files[path.relative_to(repo).as_posix()] = file_digest(path)
    return {
        "schema_version": "document-behavior-experiment-spec-v2",
        "profile": CALIBRATED_PROFILE,
        "execution_authorized": False,
        "launch_requires": "Explicit phase-B approval and revalidation of this spec, bindings and exact implementation before first request; any change requires a new seal.",
        "historical_source_seal_sha256": file_digest(source_seal),
        "historical_source_seal": str(source_seal.resolve(strict=True)),
        "implementation_and_inputs": files,
        "source_files": sources,
        "base_backend_binding": previous["base_backend_binding"],
        "evidence_mode": "controlled_official_source_replay_not_live_ranking",
        "rubrics": [rubric(c).model_dump(mode="json") for c in cases],
        "model": previous["model"] | {"settings": model_settings("calibrated")},
        "task_instructions": previous["task_instructions"],
        "pydantic_ai_version": version("pydantic-ai-slim"),
        "harness_version": version("pydantic-ai-harness"),
        "budget_per_trial": previous["budget"],
        "planned_trials": [
            {"trial_id": f"{c['id']}-r{rep}", "case_id": c["id"], "repetition": rep}
            for rep in range(1, 4)
            for c in cases
        ],
        "execution_layout": "Three isolated eight-case execute_batch invocations with measurement_profile=calibrated and distinct output roots; each run id incorporates its root hash. No invocation in stage A.",
        "http_retries": 0,
        "provider_resume": "none; only the case-declared injected interruption resumes the same run",
        "replacement_trials": False,
        "maximum_model_requests": len(cases)
        * 3
        * previous["budget"]["max_model_requests"],
        "maximum_trial_wall_seconds_sum": len(cases)
        * 3
        * previous["budget"]["wall_time_seconds"],
        "scope": "Development behavior measurement only; no task_oracle, no live ranking or policy causal comparison.",
    }


def verify_spec(repo: Path, spec_path: Path) -> dict:
    """重新计算封存内容，拒绝新增/删除源码、参数、判据或依赖漂移。

    Note:
        只校验可执行条件，不授予模型调用权限，也不修改旧 seal。
    """
    actual = json.loads(spec_path.read_bytes())
    source = Path(actual["historical_source_seal"])
    expected = prepare_spec(repo, source)
    if actual != expected:
        changed = sorted(
            k for k in set(actual) | set(expected) if actual.get(k) != expected.get(k)
        )
        raise ValueError("experiment seal drift: " + ", ".join(changed))
    return actual


def verify_runtime_manifest(experiment: dict, runtime: dict) -> None:
    pairs = {
        "profile": "profile",
        "base_backend_binding": "base_backend_binding",
        "model": "model",
        "task_instructions": "task_instructions",
        "budget_per_trial": "budget",
        "pydantic_ai_version": "pydantic_ai_version",
        "harness_version": "harness_version",
        "source_files": "source_files",
    }
    for key, runtime_key in pairs.items():
        if experiment[key] != runtime[runtime_key]:
            raise ValueError("runtime differs from experiment seal: " + key)
    if (
        runtime["planning"]
        or runtime["api_catalog"] != "off"
        or runtime["real_ranking_calls"] != 0
    ):
        raise ValueError("unexpected runtime capability")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-seal", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError(args.output)
    spec = prepare_spec(Path(__file__).parents[3], args.source_seal)
    with args.output.open("x", encoding="utf-8") as stream:
        json.dump(spec, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    print(
        json.dumps(
            {
                "profile": spec["profile"],
                "execution_authorized": False,
                "trials": len(spec["planned_trials"]),
                "sha256": file_digest(args.output),
            }
        )
    )


if __name__ == "__main__":
    main()
