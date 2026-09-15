"""准备离线 Foundation v2 输入与源码快照；不访问模型或 Abaqus。"""

import argparse
import hashlib
import json
import shutil
import sys
from datetime import UTC, datetime
from importlib.metadata import distribution, distributions
from pathlib import Path

from abaqus_sim_agent.evaluation.foundation_followup_v2 import (
    CASE_IDS,
    TASK_INSTRUCTIONS,
    budget,
    execution_config,
    load_inputs,
    write_new,
)
from abaqus_sim_agent.evaluation.foundation_skill_paired_pilot import build_trial_plan

ROOT = Path(__file__).resolve().parents[2]
RUNTIME_DISTRIBUTIONS = (
    "pydantic-ai-harness",
    "pydantic-ai-slim",
    "pydantic",
    "pydantic-core",
    "openai",
    "httpx",
    "httpcore",
    "anyio",
    "sniffio",
    "typing-extensions",
)


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def prepare(output: Path):
    output = output.resolve()
    if output.exists():
        raise FileExistsError(output)
    prior = ROOT / "output/foundation-skill-paired-pilot-v1-20260915"
    original = {
        row["case_id"]: row
        for row in json.loads((prior / "agent_cases.json").read_bytes())
    }
    old_rubrics = {
        row["case_id"]: row for row in json.loads((prior / "rubrics.json").read_bytes())
    }
    protocol = ROOT / "benchmarks/coding/foundation_pilot_followup_v2.md"
    text = protocol.read_text(encoding="utf-8")
    block = text.split("### 精确的候选任务文本\n", 1)[1].split("### 主评分项", 1)[0]
    goal = "\n".join(
        line[1:].lstrip() for line in block.splitlines() if line.startswith(">")
    )
    if not goal or "312.5" in goal or "3.125" in goal:
        raise ValueError("unexpected follow-up goal")
    cases = [original[name] for name in CASE_IDS[:2]]
    cases.append(
        {"case_id": CASE_IDS[2], "case_kind": "public", "goal": goal, "seed_files": {}}
    )
    block = text.split("### 主评分项\n", 1)[1].split("所有已作出的", 1)[0]
    criteria = []
    for line in block.splitlines():
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        if len(cells) == 2 and cells[0] in {
            "qoi_scope",
            "discretization_reason",
            "reference_applicability",
            "evidence_boundary",
            "verification_scope",
            "no_runtime",
        }:
            criteria.append(
                {
                    "id": cells[0],
                    "category": "constraint" if cells[0] == "no_runtime" else "task",
                    "origin": "user_task",
                    "requirement": cells[1],
                    "basis": "Frozen follow-up v2 method and evidence-boundary task; no hidden numeric recall requirement.",
                }
            )
    if len(criteria) != 6:
        raise ValueError("expected six exact method criteria")
    rubrics = [old_rubrics[name] for name in CASE_IDS[:2]]
    rubrics.append(
        {
            "case_id": CASE_IDS[2],
            "accepted_completions": ["delivered"],
            "criteria": criteria,
        }
    )
    output.mkdir(parents=True)
    write_new(output / "agent_cases.json", cases)
    write_new(output / "rubrics.json", rubrics)
    loaded, _ = load_inputs(output)
    assert len(build_trial_plan(loaded, 2)) == 12
    snapshot = output / "snapshot"
    snapshot.mkdir()
    shutil.copytree(
        ROOT / "src",
        snapshot / "src",
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )
    for name in ("pyproject.toml", "uv.lock"):
        shutil.copy2(ROOT / name, snapshot / name)
    scripts = snapshot / "benchmarks/coding"
    scripts.mkdir(parents=True)
    shutil.copy2(Path(__file__), scripts / Path(__file__).name)
    shutil.copy2(protocol, scripts / protocol.name)
    # -I 避免 cwd/PYTHONPATH 抢占；显式快照路径先于可编辑安装路径。
    worker = output / "worker.py"
    worker.write_text(
        "import sys\nfrom pathlib import Path\n"
        "sys.dont_write_bytecode = True\n"
        "sys.path.insert(0, str(Path(__file__).resolve().parent / 'snapshot/src'))\n"
        "from abaqus_sim_agent.evaluation.foundation_followup_v2 import main\nmain()\n",
        encoding="utf-8",
    )
    external = {str(Path(sys.executable).resolve()): sha(Path(sys.executable))}
    runtime_paths = {}
    for name in RUNTIME_DISTRIBUTIONS:
        dist = distribution(name)
        paths = []
        for item in dist.files or []:
            path = Path(str(dist.locate_file(item))).resolve()
            if path.is_file() and path.suffix.lower() in {".py", ".pyd", ".dll"}:
                external[str(path)] = sha(path)
                paths.append(str(path))
        runtime_paths[name] = {"version": dist.version, "bound_file_count": len(paths)}
    files = {
        p.relative_to(output).as_posix(): sha(p)
        for p in output.rglob("*")
        if p.is_file()
    }
    seal = {
        "schema_version": "foundation-followup-seal-v2",
        "created_at": datetime.now(UTC).isoformat(),
        "snapshot_files": files,
        "external_runtime_files": external,
        "runtime_packages": runtime_paths,
        "installed_versions": {d.metadata["Name"]: d.version for d in distributions()},
        "python_version": sys.version,
        "execution_config": execution_config().model_dump(mode="json"),
        "budget": budget().model_dump(mode="json"),
        "task_instructions": TASK_INSTRUCTIONS,
        "question_protocol": "legacy_decision",
        "source_batch_sha256": {
            name: sha(prior / name)
            for name in (
                "manifest.json",
                "report.json",
                "agent_cases.json",
                "rubrics.json",
            )
        },
        "boundary": "All project source/data/Skill references copied and bound. Listed external Python/native runtime files are rechecked per trial; other installed distributions have version inventory only. OS/provider internals are not frozen. No retrospective claim about v1.",
    }
    write_new(output / "seal.json", seal)
    print(
        json.dumps(
            {
                "root": str(output),
                "snapshot_files": len(files),
                "external_files": len(external),
                "trial_count": 12,
            }
        )
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    prepare(parser.parse_args().root)
