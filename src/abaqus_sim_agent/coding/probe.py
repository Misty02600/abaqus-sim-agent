from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Literal

from abaqus_sim_agent.coding.contracts import (
    CodingRunRequest,
    ExecutionPurpose,
    ProbeAdoptionTrace,
    ProbeConclusionStatus,
    ProbeConclusionTrace,
    ProbeResultInput,
)
from abaqus_sim_agent.execution import AbaqusExecutionResult, AbaqusInvocation

LiteralInvocation = Literal["cae_nogui", "abaqus_python"]
ProbeProtocolMode = Literal["off", "typed"]
PROBE_PROTOCOL_VERSION = "typed-probe-convergence-v2"

__all__ = [
    "PROBE_PROTOCOL_VERSION",
    "ProbeProtocolMode",
    "ProbeProtocolState",
]


@dataclass(frozen=True, slots=True)
class _PendingProbeAssessment:
    probe_id: str
    diagnostic_script: str
    invocation: LiteralInvocation
    runtime_stage_succeeded: bool
    target_hashes_before: dict[str, str]


@dataclass(frozen=True, slots=True)
class _PendingProbeAdoption:
    conclusion: ProbeConclusionTrace


@dataclass(slots=True)
class ProbeProtocolState:
    """维护单次 CodingRun 内 probe 结论与 production 收敛门禁。

    Note:
        原始诊断脚本仍位于 Agent workspace；本状态只在 workspace 外追加
        typed 结论与采用记录，不把经验自动晋升到 Skill 或 Catalog。
    """

    request: CodingRunRequest
    allowed_target_scripts: frozenset[str] | None = None
    sequence: int = 0
    pending_assessment: _PendingProbeAssessment | None = None
    pending_adoption: _PendingProbeAdoption | None = None

    def before_script_execution(
        self,
        *,
        purpose: ExecutionPurpose,
        script_path: str,
    ) -> dict[str, object] | None:
        if self.pending_assessment is not None:
            return _blocked(
                "Call record_probe_result for "
                f"{self.pending_assessment.probe_id} before another Abaqus runtime call"
            )
        adoption = self.pending_adoption
        if adoption is None:
            return None
        conclusion = adoption.conclusion
        if purpose != "production":
            return _blocked(
                f"{conclusion.probe_id} is validated; apply it to "
                f"{conclusion.target_script} before another diagnostic call"
            )
        try:
            relative, digest = self._script_identity(script_path, must_exist=True)
        except (OSError, ValueError) as exc:
            return _blocked(str(exc))
        if relative != conclusion.target_script:
            return _blocked(
                f"{conclusion.probe_id} targets {conclusion.target_script}; "
                f"production execution of {relative} cannot adopt it"
            )
        if digest == conclusion.target_sha256_before:
            return _blocked(
                f"{conclusion.target_script} has not changed since "
                f"{conclusion.probe_id} was validated"
            )
        return None

    def before_solver_execution(self) -> dict[str, object] | None:
        if self.pending_assessment is not None:
            return _blocked(
                "A diagnostic result is awaiting record_probe_result before Solver"
            )
        if self.pending_adoption is not None:
            conclusion = self.pending_adoption.conclusion
            return _blocked(
                f"{conclusion.probe_id} must be adopted by a production run of "
                f"{conclusion.target_script} before Solver"
            )
        return None

    def register_diagnostic_execution(
        self,
        script_path: str,
        result: AbaqusExecutionResult,
        *,
        runtime_stage_succeeded: bool,
    ) -> dict[str, object]:
        relative, _digest = self._script_identity(script_path, must_exist=True)
        self.sequence += 1
        assessment = _PendingProbeAssessment(
            probe_id=f"probe-{self.sequence:03d}",
            diagnostic_script=relative,
            invocation=_probe_invocation(result.invocation),
            runtime_stage_succeeded=runtime_stage_succeeded,
            target_hashes_before=self._target_hashes(),
        )
        self.pending_assessment = assessment
        return {
            "probe_id": assessment.probe_id,
            "diagnostic_script": assessment.diagnostic_script,
            "runtime_stage_succeeded": assessment.runtime_stage_succeeded,
            "required_action": (
                "Inspect the diagnostic evidence, then call record_probe_result "
                "before any other Abaqus runtime tool"
            ),
        }

    def record_result(
        self,
        *,
        status: ProbeConclusionStatus,
        claim: str,
        evidence: str,
        target_script: str | None,
    ) -> dict[str, object]:
        assessment = self.pending_assessment
        if assessment is None:
            return _invalid("No diagnostic execution is awaiting a probe result")
        normalized_claim = claim.strip()
        normalized_evidence = evidence.strip()
        if not normalized_claim or not normalized_evidence:
            return _invalid("claim and evidence must not be blank")

        target_relative: str | None = None
        target_digest: str | None = None
        if status == "validated":
            if not assessment.runtime_stage_succeeded:
                return _invalid(
                    "A failed diagnostic runtime stage cannot validate a candidate"
                )
            if target_script is None:
                return _invalid("validated probe requires target_script")
            try:
                target_relative, _current_digest = self._script_identity(
                    target_script,
                    must_exist=False,
                )
            except (OSError, ValueError) as exc:
                return _invalid(str(exc))
            if (
                self.allowed_target_scripts is not None
                and target_relative not in self.allowed_target_scripts
            ):
                allowed = ", ".join(sorted(self.allowed_target_scripts))
                return _invalid(f"validated probe target must be one of: {allowed}")
            target_digest = assessment.target_hashes_before.get(target_relative)
        elif target_script is not None:
            return _invalid(
                "rejected or inconclusive probe must not declare target_script"
            )

        conclusion = ProbeConclusionTrace(
            probe_id=assessment.probe_id,
            diagnostic_script=assessment.diagnostic_script,
            invocation=assessment.invocation,
            runtime_stage_succeeded=assessment.runtime_stage_succeeded,
            status=status,
            claim=normalized_claim,
            target_script=target_relative,
            evidence=normalized_evidence,
            target_sha256_before=target_digest,
        )
        self._append_trace(conclusion)
        self.pending_assessment = None
        if status == "validated":
            self.pending_adoption = _PendingProbeAdoption(conclusion=conclusion)
            next_action = (
                f"Modify {target_relative}, then run it with purpose=production. "
                "Further diagnostics and Solver are blocked until that run."
            )
        else:
            next_action = (
                "The diagnostic has no adoptable candidate; another evidenced repair "
                "or diagnostic is allowed."
            )
        return {
            "status": "recorded",
            "probe": conclusion.model_dump(mode="json"),
            "next_action": next_action,
        }

    def record_input(self, result: ProbeResultInput) -> dict[str, object]:
        return self.record_result(
            status=result.status,
            claim=result.claim,
            evidence=result.evidence,
            target_script=result.target_script,
        )

    def register_production_execution(
        self,
        script_path: str,
        result: AbaqusExecutionResult,
        *,
        runtime_stage_succeeded: bool,
    ) -> dict[str, object] | None:
        adoption = self.pending_adoption
        if adoption is None:
            return None
        conclusion = adoption.conclusion
        relative, digest = self._script_identity(script_path, must_exist=False)
        if relative != conclusion.target_script:
            return None
        trace = ProbeAdoptionTrace(
            probe_id=conclusion.probe_id,
            target_script=relative,
            target_sha256_before=conclusion.target_sha256_before,
            target_sha256_after=digest,
            production_invocation=_probe_invocation(result.invocation),
            production_stage_succeeded=runtime_stage_succeeded,
        )
        self._append_trace(trace)
        self.pending_adoption = None
        return trace.model_dump(mode="json")

    def unresolved_feedback(self) -> str | None:
        if self.pending_assessment is not None:
            return (
                f"{self.pending_assessment.probe_id} has no typed conclusion. "
                "Call record_probe_result before requesting verification."
            )
        if self.pending_adoption is not None:
            conclusion = self.pending_adoption.conclusion
            return (
                f"{conclusion.probe_id} is validated but not adopted. Modify and run "
                f"{conclusion.target_script} with purpose=production before verification."
            )
        return None

    def _script_identity(
        self,
        raw_path: str,
        *,
        must_exist: bool,
    ) -> tuple[str, str | None]:
        """规范化工作区 Python 路径并返回当前内容哈希。

        Args:
            raw_path: Agent 提供的相对脚本路径。
            must_exist: 是否要求脚本在检查时已经存在。

        Returns:
            POSIX 风格相对路径，以及文件存在时的 SHA-256。

        Raises:
            FileNotFoundError: 要求存在的脚本尚未创建。
            ValueError: 路径逃逸、使用 symlink、后缀错误或目标不是文件。
        """
        normalized = raw_path.replace("\\", "/")
        if (
            PurePosixPath(normalized).is_absolute()
            or PureWindowsPath(normalized).is_absolute()
            or ".." in PurePosixPath(normalized).parts
        ):
            raise ValueError("probe script paths must stay within the workspace")
        unresolved = self.request.workspace / Path(normalized)
        if unresolved.is_symlink():
            raise ValueError("probe script paths must not be symlinks")
        try:
            resolved = unresolved.resolve(strict=must_exist)
        except FileNotFoundError as exc:
            raise FileNotFoundError(
                f"probe script does not exist: {normalized}"
            ) from exc
        if not resolved.is_relative_to(self.request.workspace):
            raise ValueError("probe script paths must stay within the workspace")
        if resolved.suffix.casefold() != ".py":
            raise ValueError("probe target must be a Python script")
        if resolved.exists() and not resolved.is_file():
            raise ValueError("probe target must be a file")
        digest = _sha256_file(resolved) if resolved.is_file() else None
        return resolved.relative_to(self.request.workspace).as_posix(), digest

    def _append_trace(
        self,
        trace: ProbeConclusionTrace | ProbeAdoptionTrace,
    ) -> None:
        self.request.state_dir.mkdir(parents=True, exist_ok=True)
        with (self.request.state_dir / "probe_results.jsonl").open(
            "a",
            encoding="utf-8",
            newline="\n",
        ) as stream:
            stream.write(trace.model_dump_json())
            stream.write("\n")

    def _target_hashes(self) -> dict[str, str]:
        if self.allowed_target_scripts is None:
            candidates = tuple(
                path.relative_to(self.request.workspace).as_posix()
                for path in self.request.workspace.rglob("*.py")
                if path.is_file() and not path.is_symlink()
            )
        else:
            candidates = tuple(self.allowed_target_scripts)
        hashes: dict[str, str] = {}
        for candidate in candidates:
            try:
                relative, digest = self._script_identity(candidate, must_exist=False)
            except (OSError, ValueError):
                continue
            if digest is not None:
                hashes[relative] = digest
        return hashes


def _probe_invocation(invocation: AbaqusInvocation) -> LiteralInvocation:
    if invocation is AbaqusInvocation.CAE_NOGUI:
        return "cae_nogui"
    if invocation is AbaqusInvocation.ABAQUS_PYTHON:
        return "abaqus_python"
    raise ValueError(f"unsupported probe invocation: {invocation.value}")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _blocked(message: str) -> dict[str, object]:
    return {
        "succeeded": False,
        "stage_succeeded": False,
        "blocked": True,
        "error_type": "ProbeProtocolBlocked",
        "error": message,
    }


def _invalid(message: str) -> dict[str, object]:
    return {
        "status": "invalid",
        "error": message,
    }
