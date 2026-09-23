from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from abaqus_sim_agent.coding import amendments as amendments_module
from abaqus_sim_agent.coding.amendments import (
    AMENDMENT_LEDGER_NAME,
    CodingRunConfigPatch,
    RuntimeExecutionPolicyPatch,
    append_operational_amendment,
    append_task_revision,
    load_amendment_ledger,
)
from abaqus_sim_agent.coding.contracts import (
    CodingRunOutcome,
    CodingRunRequest,
    CodingRunStatus,
    RuntimeProfile,
)
from abaqus_sim_agent.coding.resume import (
    CodingRunResumeState,
    DependencyResumeState,
    RunUsageSnapshot,
    load_request_for_resume,
    load_resume_state,
    write_resume_state,
)
from abaqus_sim_agent.coding.run_lock import (
    CodingRunAlreadyLocked,
    CodingRunFileLock,
)
from abaqus_sim_agent.coding.workspace import WorkspaceResolver


def _persist_base_state(tmp_path: Path) -> CodingRunRequest:
    workspace = (tmp_path / "workspace").resolve()
    state_dir = (tmp_path / "state").resolve()
    workspace.mkdir()
    state_dir.mkdir()
    request = CodingRunRequest(
        run_id="amendment-test",
        goal="Create the original artifact",
        workspace=workspace,
        state_dir=state_dir,
        runtime=RuntimeProfile(
            launcher_path=Path(r"D:\SIMULIA\Commands\abq2024.bat").resolve()
        ),
    )
    snapshot = WorkspaceResolver(captured_cwd=workspace).snapshot(workspace)
    (state_dir / "request.json").write_text(
        request.model_dump_json(indent=2),
        encoding="utf-8",
    )
    (state_dir / "initial_snapshot.json").write_text(
        snapshot.model_dump_json(indent=2),
        encoding="utf-8",
    )
    (state_dir / "resume_state.json").write_text(
        CodingRunResumeState(
            run_id=request.run_id,
            status="interrupted",
            turn=1,
            revision_workspace_snapshot=snapshot,
            active_wall_time_seconds=1.0,
            workspace_snapshot=snapshot,
            usage=RunUsageSnapshot(),
            dependencies=DependencyResumeState(),
            updated_at=datetime.now(UTC),
        ).model_dump_json(indent=2),
        encoding="utf-8",
    )
    return request


def test_operational_amendment_is_strict_atomic_overlay(
    tmp_path: Path,
) -> None:
    request = _persist_base_state(tmp_path)
    original_request = (request.state_dir / "request.json").read_bytes()

    amendment = append_operational_amendment(
        request.state_dir,
        patch=CodingRunConfigPatch(
            runtime_policy=RuntimeExecutionPolicyPatch.model_validate(
                {
                    "solver": {"mode": "allow_with_limit", "limit": 2},
                    "solver_timeout": {"mode": "limited", "seconds": 30.0},
                }
            ),
            model_name="replacement-model",
            http_retries=1,
        ),
        reason="User changed runtime limits",
    )

    assert amendment.sequence == 1
    assert amendment.task_revision == 0
    assert (request.state_dir / "request.json").read_bytes() == original_request
    effective = load_request_for_resume(request.state_dir)
    assert effective.goal == request.goal
    assert effective.runtime_policy.solver.mode == "allow_with_limit"
    assert effective.runtime_policy.timeout_seconds("solver") == 30.0
    ledger = load_amendment_ledger(request.state_dir, run_id=request.run_id)
    assert ledger.sequence == 1
    assert ledger.task_revision == 0

    with pytest.raises(ValidationError, match="extra_forbidden"):
        CodingRunConfigPatch.model_validate({"unknown": 1})
    with pytest.raises(ValidationError, match="at least one"):
        RuntimeExecutionPolicyPatch()
    with pytest.raises(ValidationError, match="explicit null"):
        RuntimeExecutionPolicyPatch.model_validate({"solver_timeout": None})


def test_operational_amendment_operation_id_is_idempotent_and_sequence_checked(
    tmp_path: Path,
) -> None:
    request = _persist_base_state(tmp_path)
    patch = CodingRunConfigPatch(
        runtime_policy=RuntimeExecutionPolicyPatch.model_validate(
            {"solver": {"mode": "allow_with_limit", "limit": 2}}
        )
    )
    operation_id = "a" * 32

    first = append_operational_amendment(
        request.state_dir,
        patch=patch,
        reason="Web runtime permission change",
        operation_id=operation_id,
        expected_sequence=0,
    )
    replay = append_operational_amendment(
        request.state_dir,
        patch=patch,
        reason="Web runtime permission change",
        operation_id=operation_id,
        expected_sequence=0,
    )

    assert replay == first
    ledger = load_amendment_ledger(request.state_dir, run_id=request.run_id)
    assert ledger.schema_version == "coding-run-amendments-v3"
    assert ledger.sequence == 1
    with pytest.raises(ValueError, match="different amendment"):
        append_operational_amendment(
            request.state_dir,
            patch=CodingRunConfigPatch(
                runtime_policy=RuntimeExecutionPolicyPatch.model_validate(
                    {"solver": {"mode": "deny"}}
                )
            ),
            reason="Web runtime permission change",
            operation_id=operation_id,
        )
    with pytest.raises(ValueError, match="sequence changed"):
        append_operational_amendment(
            request.state_dir,
            patch=patch,
            reason="Another Web permission change",
            operation_id="b" * 32,
            expected_sequence=0,
        )
    assert load_amendment_ledger(request.state_dir, run_id=request.run_id).sequence == 1


def test_invalid_runtime_policy_patch_does_not_replace_committed_ledger(
    tmp_path: Path,
) -> None:
    request = _persist_base_state(tmp_path)
    append_operational_amendment(
        request.state_dir,
        patch=CodingRunConfigPatch(
            runtime_policy=RuntimeExecutionPolicyPatch.model_validate(
                {"solver": {"mode": "allow"}}
            )
        ),
        reason="First valid amendment",
    )
    ledger_path = request.state_dir / AMENDMENT_LEDGER_NAME
    committed = ledger_path.read_bytes()

    with pytest.raises(ValidationError, match="greater than or equal to 1"):
        RuntimeExecutionPolicyPatch.model_validate(
            {"solver": {"mode": "allow_with_limit", "limit": 0}}
        )

    assert ledger_path.read_bytes() == committed


def test_amendment_fails_without_writing_while_run_lock_is_held(
    tmp_path: Path,
) -> None:
    request = _persist_base_state(tmp_path)
    ledger_path = request.state_dir / AMENDMENT_LEDGER_NAME

    with CodingRunFileLock(request.state_dir):
        with pytest.raises(CodingRunAlreadyLocked):
            append_operational_amendment(
                request.state_dir,
                patch=CodingRunConfigPatch(
                    runtime_policy=RuntimeExecutionPolicyPatch.model_validate(
                        {"solver": {"mode": "allow"}}
                    )
                ),
                reason="Concurrent amendment",
            )

    assert not ledger_path.exists()


def test_amendment_cannot_bypass_runtime_reconciliation(tmp_path: Path) -> None:
    request = _persist_base_state(tmp_path)
    persisted = load_resume_state(request.state_dir)
    write_resume_state(
        request.state_dir,
        persisted.model_copy(update={"status": "needs_reconcile"}),
    )

    with pytest.raises(ValueError, match="requires runtime reconciliation"):
        append_operational_amendment(
            request.state_dir,
            patch=CodingRunConfigPatch(
                runtime_policy=RuntimeExecutionPolicyPatch.model_validate(
                    {"solver": {"mode": "allow"}}
                )
            ),
            reason="Must not bypass reconciliation",
        )

    assert not (request.state_dir / AMENDMENT_LEDGER_NAME).exists()


def test_failed_ledger_commit_keeps_current_verification_projection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = _persist_base_state(tmp_path)
    verification_path = request.state_dir / "verification.json"
    verification = b'{"passed": true}'
    verification_path.write_bytes(verification)

    def fail_ledger_commit(path: Path, value: str) -> None:
        del value
        assert path.name == AMENDMENT_LEDGER_NAME
        raise OSError("simulated atomic replace failure")

    monkeypatch.setattr(
        amendments_module,
        "_write_text_atomic",
        fail_ledger_commit,
    )

    with pytest.raises(OSError, match="atomic replace failure"):
        append_task_revision(
            request.state_dir,
            goal="A revised physical task",
            reason="User changed the task",
        )

    assert verification_path.read_bytes() == verification
    assert not (request.state_dir / AMENDMENT_LEDGER_NAME).exists()


def test_post_commit_verification_cleanup_failure_does_not_duplicate_amendment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = _persist_base_state(tmp_path)
    verification_path = request.state_dir / "verification.json"
    verification_path.write_bytes(b'{"passed": true}')
    original_unlink = Path.unlink

    def deny_verification_unlink(
        path: Path,
        missing_ok: bool = False,
    ) -> None:
        if path == verification_path:
            raise PermissionError("simulated sharing violation")
        original_unlink(path, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "unlink", deny_verification_unlink)

    amendment = append_operational_amendment(
        request.state_dir,
        patch=CodingRunConfigPatch(
            runtime_policy=RuntimeExecutionPolicyPatch.model_validate(
                {"solver": {"mode": "allow"}}
            )
        ),
        reason="Committed despite stale projection cleanup",
    )

    assert amendment.sequence == 1
    assert verification_path.exists()
    assert (
        load_amendment_ledger(
            request.state_dir,
            run_id=request.run_id,
        ).sequence
        == 1
    )


def test_repeated_pending_amendments_archive_by_outcome_sequence(
    tmp_path: Path,
) -> None:
    request = _persist_base_state(tmp_path)
    (request.state_dir / "outcome.json").write_text(
        CodingRunOutcome(
            run_id=request.run_id,
            status=CodingRunStatus.COMPLETED,
            summary="Original outcome",
        ).model_dump_json(indent=2),
        encoding="utf-8",
    )

    for seconds in (60.0, 120.0):
        append_operational_amendment(
            request.state_dir,
            patch=CodingRunConfigPatch(
                runtime_policy=RuntimeExecutionPolicyPatch.model_validate(
                    {
                        "solver_timeout": {
                            "mode": "limited",
                            "seconds": seconds,
                        }
                    }
                )
            ),
            reason=f"Set wall time to {seconds}",
        )

    archive = request.state_dir / "revisions" / "task-0000"
    assert (archive / "sequence-0000-outcome.json").is_file()
    assert not (archive / "sequence-0001-outcome.json").exists()
