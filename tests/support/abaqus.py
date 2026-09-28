"""只生成确定性文件的假进程，不调用 Abaqus。"""

import json

from abaqus_sim_agent.execution import ProcessOutput


class FakeAbaqusProcess:
    def __init__(self, stem):
        self.stem = stem
        self.calls = []

    async def run(self, command, *, cwd, timeout_seconds):
        self.calls.append((tuple(command), cwd, timeout_seconds))
        marker = f"fixture-{len(self.calls)}"
        if "cae" in command:
            (cwd / f"{self.stem}.cae").write_text(marker)
            (cwd / f"{self.stem}.inp").write_text(marker)
        elif "python" in command:
            (cwd / "results.json").write_text(
                json.dumps({"fixture": True, "stage": marker})
            )
        else:
            (cwd / f"{self.stem}.odb").write_text(marker)
            (cwd / f"{self.stem}.sta").write_text(
                "THE ANALYSIS HAS COMPLETED SUCCESSFULLY"
            )
            if "'RF3'" in (cwd / "build_model.py").read_text():
                (cwd / f"{self.stem}.dat").write_text(
                    "***WARNING: OUTPUT REQUEST RF3 IS NOT VALID SINCE IT REQUESTS OUTPUT AT A DOF THAT IS NOT ACTIVE\n"
                )
                return ProcessOutput(
                    exit_code=0,
                    stdout=b"***WARNING: OUTPUT REQUEST RF3 IS NOT VALID SINCE IT REQUESTS OUTPUT AT A DOF THAT IS NOT ACTIVE\n",
                    stderr=b"",
                )
            (cwd / f"{self.stem}.dat").write_text("")
        return ProcessOutput(
            exit_code=0, stdout=b"offline simulated process", stderr=b""
        )
