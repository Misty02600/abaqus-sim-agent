"""一题一会话的轻量离线查询准备；检索和语义审查在模型完成后独立进行。"""

import argparse
import asyncio
import hashlib
import json
import shutil
import sys
from pathlib import Path

from abaqus_sim_agent.api_catalog import ApiCatalog

PROJECT = Path(__file__).resolve().parents[2]


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_new(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)


def tree_hashes(root):
    return {
        str(p.relative_to(root)): sha(p)
        for p in sorted(root.rglob("*"))
        if p.is_file() and "__pycache__" not in p.parts
    }


def prepare(root):
    root.mkdir(parents=True, exist_ok=False)
    old = json.loads(
        (PROJECT / "benchmarks/api_catalog/discovery_development_v1.json").read_bytes()
    )
    protocol = json.loads(
        (
            PROJECT / "benchmarks/api_catalog/intent_regression_protocol_v1.json"
        ).read_bytes()
    )
    regression = [
        {
            "id": "old_" + f["id"],
            "question": f["zh"],
            "action": "search",
            "expected": f["expected"],
            "review": "保留原操作和对象，不新增建模条件；原请求完整保留。",
        }
        for f in old["families"]
    ]
    regression += [
        {
            "id": "old_" + c["id"],
            "question": c["question"],
            "action": "clarify" if c["id"] == "guard_missing" else "search",
            "expected": c["expected"],
            "review": "保留符号、当前/目标状态、禁止事项；缺值需询问，不猜补。",
        }
        for c in protocol["guards"]
    ]
    acceptance = json.loads(
        (
            PROJECT / "benchmarks/api_catalog/intent_acceptance_cases_v1.json"
        ).read_bytes()
    )["cases"]
    repeat = []
    for name in [
        "old_sketch",
        "old_section",
        "old_guard_missing",
        "old_guard_constraints",
    ]:
        case = next(c for c in regression if c["id"] == name)
        repeat += [
            {**case, "id": name + "-repeat" + str(i), "repeat_of": name} for i in [2, 3]
        ]
    catalog = ApiCatalog.open(
        PROJECT / "var/api-catalog/abaqus-2024-reference-context-v2"
    )
    target_evidence = {}
    for case in regression + acceptance:
        for name in case["expected"]:
            result = catalog.lookup(name)
            assert result.status == "found", name
            target_evidence[name] = [c.model_dump(mode="json") for c in result.cards]
    # Freeze the entire current skill library so concurrent authoring does not change this batch.
    skill_source = PROJECT / "src/abaqus_sim_agent/skills"
    before = tree_hashes(skill_source)
    shutil.copytree(skill_source, root / "skills")
    assert tree_hashes(skill_source) == before == tree_hashes(root / "skills")
    prompt = ("\r\n".join(protocol["goal_lines"]) + "\r\n").encode("utf-8")
    (root / "goal.txt").write_bytes(prompt)
    frozen = {
        str(p.relative_to(PROJECT)): sha(p)
        for p in (PROJECT / "src/abaqus_sim_agent").rglob("*.py")
    }
    write_new(
        root / "seal.json",
        {
            "profile": "intent-independent-expansion-v1",
            "regression": regression,
            "acceptance": acceptance,
            "repeat": repeat,
            "skills": before,
            "goal_sha256": hashlib.sha256(prompt).hexdigest(),
            "source_hashes": frozen,
            "uv_lock_sha256": sha(PROJECT / "uv.lock"),
            "driver_sha256": sha(Path(__file__)),
            "new_cases_sha256": sha(
                PROJECT / "benchmarks/api_catalog/intent_acceptance_cases_v1.json"
            ),
            "index_manifest_sha256": sha(
                PROJECT
                / "var/api-catalog/discovery-reference-context-v2-e5/manifest.json"
            ),
            "protocol": {
                "one_case_per_session": True,
                "concurrency": 2,
                "max_model_requests": 20,
                "wall_seconds": 900,
                "reasoning": "enabled_high",
                "http_timeout": 60,
                "http_retries": 10,
                "abaqus_budget": 0,
                "lookup_budget": 0,
                "attempt_replacement": False,
                "qualification": "query-disjoint acceptance, not family-disjoint held-out; review before ranking; repeat outcomes separate",
            },
        },
    )
    write_new(root / "target_evidence.json", target_evidence)
    print("sealed counts", len(regression), len(acceptance), len(repeat), flush=True)


def verify_seal(root, seal):
    assert tree_hashes(root / "skills") == seal["skills"]
    assert sha(root / "goal.txt") == seal["goal_sha256"]
    assert sha(PROJECT / "uv.lock") == seal["uv_lock_sha256"]
    assert (
        sha(PROJECT / "var/api-catalog/discovery-reference-context-v2-e5/manifest.json")
        == seal["index_manifest_sha256"]
    )
    assert all(
        sha(PROJECT / p) == digest for p, digest in seal["source_hashes"].items()
    ), "product source changed; do not mix candidates"


async def run(root, phase):
    seal = json.loads((root / "seal.json").read_bytes())
    verify_seal(root, seal)
    sem = asyncio.Semaphore(2)

    async def attempt(case):
        async with sem:
            verify_seal(root, seal)
            folder = root / phase / case["id"]
            folder.mkdir(parents=True, exist_ok=False)
            workspace = (
                Path("D:/abaqus-runs") / root.name / phase / case["id"] / "workspace"
            )
            workspace.mkdir(parents=True, exist_ok=False)
            write_new(
                workspace / "cases.json",
                [{"id": case["id"], "question": case["question"]}],
            )
            state = folder / "state"
            args = [
                sys.executable,
                "-c",
                "from abaqus_sim_agent.coding.cli import main; raise SystemExit(main())",
                "--request-file",
                str(root / "goal.txt"),
                "--workspace",
                str(workspace),
                "--state-dir",
                str(state),
                "--skills-dir",
                str(root / "skills"),
                "--run-id",
                root.name + "-" + case["id"],
                "--model-provider",
                "opencode-go",
                "--model",
                "deepseek-v4-flash",
                "--reasoning-policy",
                "enabled_high",
                "--planning",
                "--http-timeout-seconds",
                "60",
                "--http-retries",
                "10",
                "--max-model-requests",
                "20",
                "--wall-time-seconds",
                "900",
                "--max-abaqus-calls",
                "0",
                "--max-solver-calls",
                "0",
                "--max-api-lookup-calls",
                "0",
                "--max-concept-lookup-calls",
                "0",
                "--max-diagnostic-retrieval-calls",
                "0",
            ]
            write_new(
                folder / "attempt.json",
                {
                    "case_id": case["id"],
                    "workspace": str(workspace),
                    "state": str(state),
                    "phase": phase,
                },
            )
            print("START", phase, case["id"], flush=True)
            with (
                (folder / "stdout.bin").open("xb") as stdout,
                (folder / "stderr.bin").open("xb") as stderr,
            ):
                proc = await asyncio.create_subprocess_exec(
                    *args,
                    cwd=PROJECT,
                    stdout=stdout,
                    stderr=stderr,
                    stdin=asyncio.subprocess.DEVNULL,
                )
                code = await proc.wait()
            outcome = (
                json.loads((state / "outcome.json").read_bytes())
                if (state / "outcome.json").exists()
                else None
            )
            write_new(
                folder / "completion.json",
                {
                    "exit_code": code,
                    "status": outcome.get("status") if outcome else "no_outcome",
                    "failure_stage": outcome.get("failure_stage") if outcome else None,
                },
            )
            print(
                "END",
                phase,
                case["id"],
                code,
                outcome.get("status") if outcome else "no_outcome",
                flush=True,
            )

    # Finish already-started children before reporting a preparation/seal failure.
    results = await asyncio.gather(
        *(attempt(case) for case in seal[phase]), return_exceptions=True
    )
    failures = [r for r in results if isinstance(r, BaseException)]
    if failures:
        raise RuntimeError(
            f"{len(failures)} cases could not complete orchestration"
        ) from failures[0]
    verify_seal(root, seal)
    print("PHASE COMPLETE", phase, flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["prepare", "run"])
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--phase", choices=["regression", "acceptance", "repeat"])
    args = parser.parse_args()
    root = args.root.resolve()
    if args.command == "prepare":
        prepare(root)
    elif args.phase:
        asyncio.run(run(root, args.phase))
    else:
        parser.error("--phase required for run")
