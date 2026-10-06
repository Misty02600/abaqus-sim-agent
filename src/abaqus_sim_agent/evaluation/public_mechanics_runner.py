"""记录声明产物前后状态，区分相同字节重写与没有写入。"""

import hashlib
import json
from pathlib import Path

from abaqus_sim_agent.execution import Abaqus2024Runner, AbaqusExecutionResult
from abaqus_sim_agent.execution.abaqus2024 import SolverCpus, SolverMemory


def execution_key(result: AbaqusExecutionResult) -> str:
    return hashlib.sha256(result.model_dump_json().encode("utf-8")).hexdigest()


def output_state(workspace: Path, names: tuple[str, ...]) -> dict:
    result = {}
    for name in names:
        p = workspace / name
        if p.is_file():
            stat = p.stat()
            result[name] = {
                "sha256": hashlib.sha256(p.read_bytes()).hexdigest(),
                "size": stat.st_size,
                "mtime_ns": stat.st_mtime_ns,
            }
    return result


class PublicMechanicsRunner(Abaqus2024Runner):
    def __init__(self, workspace: Path, state_dir: Path, **kwargs):
        super().__init__(workspace, state_dir, **kwargs)
        self.observation_file = state_dir / "public_stage_outputs.jsonl"

    def _record(self, result, before, names):
        after = output_state(self.workspace, names)
        self.observation_file.parent.mkdir(parents=True, exist_ok=True)
        with self.observation_file.open("a", encoding="utf-8") as stream:
            stream.write(
                json.dumps(
                    {
                        "execution_sha256": execution_key(result),
                        "before": before,
                        "after": after,
                    }
                )
                + "\n"
            )
        return result

    def _cae_outputs(self):
        return (
            "model.cae",
            *(
                p.relative_to(self.workspace).as_posix()
                for p in self.workspace.rglob("*")
                if p.suffix.lower() == ".inp" and p.is_file() and not p.is_symlink()
            ),
        )

    async def run_cae_script(self, script, *, args=(), timeout_seconds=600.0):
        names = self._cae_outputs()
        before = output_state(self.workspace, names)
        result = await super().run_cae_script(
            script, args=args, timeout_seconds=timeout_seconds
        )
        return self._record(result, before, self._cae_outputs())

    async def run_solver_job(
        self,
        job_name,
        *,
        input_file=None,
        timeout_seconds=1800.0,
        cpus: SolverCpus | None = None,
        memory: SolverMemory | None = None,
    ):
        names = (job_name + ".odb", job_name + ".sta")
        before = output_state(self.workspace, names)
        result = await super().run_solver_job(
            job_name,
            input_file=input_file,
            timeout_seconds=timeout_seconds,
            cpus=cpus,
            memory=memory,
        )
        return self._record(result, before, names)

    async def run_abaqus_python(self, script, *, args=(), timeout_seconds=300.0):
        names = ("results.json",)
        before = output_state(self.workspace, names)
        result = await super().run_abaqus_python(
            script, args=args, timeout_seconds=timeout_seconds
        )
        return self._record(result, before, names)


def read_observations(state: Path) -> dict:
    path = state / "public_stage_outputs.jsonl"
    if not path.is_file():
        return {}
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    return {row["execution_sha256"]: row for row in rows}
