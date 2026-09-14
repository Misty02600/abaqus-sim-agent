from pathlib import Path

from abaqus_sim_agent.coding.workspace import WorkspaceResolver
from abaqus_sim_agent.verification.model_change_evidence import (
    build_workspace_change_report,
)


def test_change_report_preserves_crlf_when_a_seed_file_is_unchanged(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    build_seed = "print('old')\r\n"
    extract_seed = "print('extract')\r\n"
    (workspace / "build_model.py").write_bytes(build_seed.encode("utf-8"))
    (workspace / "extract_results.py").write_bytes(extract_seed.encode("utf-8"))
    resolver = WorkspaceResolver(captured_cwd=workspace)
    initial = resolver.snapshot(workspace)
    (workspace / "build_model.py").write_bytes(b"print('new')\r\n")
    current = resolver.snapshot(workspace)

    report = build_workspace_change_report(
        initial,
        current,
        source_run_id="source-run",
        seed_texts={
            "build_model.py": build_seed,
            "extract_results.py": extract_seed,
        },
        workspace=workspace,
    )

    assert report.build_model_patch
    assert report.extract_results_patch == ""
    assert [(change.path, change.kind) for change in report.changes] == [
        ("build_model.py", "modified")
    ]
