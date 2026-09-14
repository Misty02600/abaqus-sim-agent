from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from pathlib import Path, PurePosixPath, PureWindowsPath

from pydantic import JsonValue

from abaqus_sim_agent.coding.contracts import (
    CodingRunRequest,
    ReadyForVerificationDecision,
)
from abaqus_sim_agent.coding.verification_port import (
    CodingRunVerifier,
    VerificationAttempt,
)
from abaqus_sim_agent.coding.workspace import (
    WorkspaceResolver,
    WorkspaceSnapshot,
    artifact_evidence_from_snapshots,
)
from abaqus_sim_agent.execution import (
    Abaqus2024Runner,
    AbaqusExecutionResult,
    AbaqusInvocation,
)


class ApiRepairVerificationAttempt(VerificationAttempt):
    result_payload: dict[str, JsonValue] | None = None
    script_modified: bool = False
    result_created_by_successful_cae: bool = False


class ApiRepairRunVerifier(CodingRunVerifier[ApiRepairVerificationAttempt]):
    """验证已有 Abaqus 脚本修复，而不信任 Agent 的完成声明。"""

    def __init__(
        self,
        *,
        entry_script: str,
        result_file: str,
        expected_result: Mapping[str, JsonValue],
    ) -> None:
        self._entry_script = _relative_file(entry_script)
        self._result_file = _relative_file(result_file)
        self._expected_result = dict(expected_result)

    async def verify(
        self,
        request: CodingRunRequest,
        runner: Abaqus2024Runner,
        initial_snapshot: WorkspaceSnapshot,
        executions: Sequence[AbaqusExecutionResult],
        decision: ReadyForVerificationDecision | None = None,
    ) -> ApiRepairVerificationAttempt:
        del runner, decision
        current = WorkspaceResolver(captured_cwd=request.workspace).snapshot(
            request.workspace
        )
        artifacts, freshness = artifact_evidence_from_snapshots(
            initial_snapshot,
            current,
        )
        before = initial_snapshot.by_path().get(self._entry_script)
        after = current.by_path().get(self._entry_script)
        script_modified = (
            before is not None and after is not None and before.sha256 != after.sha256
        )
        result_path = request.workspace / self._result_file
        payload = _load_json_object(result_path)
        result_fresh = freshness.get(self._result_file, False)
        result_from_cae = _successful_cae_created_result(
            executions,
            self._result_file,
        )
        result_matches = payload is not None and all(
            payload.get(key) == expected
            for key, expected in self._expected_result.items()
        )

        findings: list[str] = []
        if before is None:
            findings.append("entry script was absent from the initial workspace")
        if not script_modified:
            findings.append("entry script was not modified from the seeded bad case")
        if not result_fresh:
            findings.append("result artifact is missing or stale")
        if not result_from_cae:
            findings.append(
                "no successful CAE execution created or modified the result artifact"
            )
        if not result_matches:
            findings.append(
                "result JSON does not match the hidden expected postcondition"
            )

        passed = not findings
        attempt = ApiRepairVerificationAttempt(
            passed=passed,
            feedback=(
                "Independent API repair verification passed."
                if passed
                else "Independent API repair verification failed: "
                + "; ".join(findings)
            ),
            artifacts=artifacts,
            failure_stage=None if passed else "api_repair_verification",
            result_payload=payload,
            script_modified=script_modified,
            result_created_by_successful_cae=result_from_cae,
        )
        (request.state_dir / "api_repair_verification.json").write_text(
            attempt.model_dump_json(indent=2),
            encoding="utf-8",
        )
        return attempt


def _relative_file(value: str) -> str:
    normalized = value.replace("\\", "/")
    path = PurePosixPath(normalized)
    windows_path = PureWindowsPath(normalized)
    if (
        not normalized
        or normalized == "."
        or path.is_absolute()
        or bool(windows_path.drive)
        or ".." in path.parts
    ):
        raise ValueError("API repair paths must stay relative to the workspace")
    return path.as_posix()


def _load_json_object(path: Path) -> dict[str, JsonValue] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        return None
    return value


def _successful_cae_created_result(
    executions: Sequence[AbaqusExecutionResult],
    result_file: str,
) -> bool:
    for execution in executions:
        stage_succeeded = (
            execution.invocation is AbaqusInvocation.CAE_NOGUI
            and execution.succeeded
            and "Abaqus Error:" not in execution.stdout
            and "Traceback (most recent call last)" not in execution.stderr
        )
        if not stage_succeeded:
            continue
        if any(
            change.path.replace("\\", "/") == result_file and change.after is not None
            for change in execution.artifact_changes
        ):
            return True
    return False
