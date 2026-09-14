from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from abaqus_sim_agent.evaluation.retrieval_input_inventory import (
    CoverageMatrixRow,
    RetrievalInputInventoryReport,
    _assert_report_values_safe,
    build_inventory,
    main,
    write_report_create_new,
)


def _write_jsonl(path: Path, records: list[object], *, malformed: bool = False) -> None:
    lines = [json.dumps(record, ensure_ascii=False) for record in records]
    if malformed:
        lines.append("{broken")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _make_run(
    root: Path,
    name: str,
    *,
    duplicate_concept_query: bool,
    solver_node: int,
) -> None:
    run_dir = root / name
    state = run_dir / "state"
    workspace = run_dir / "workspace"
    state.mkdir(parents=True)
    workspace.mkdir()
    goal = (
        "同时用 CPS4R 和 *STATIC 建板，不要提交 job；参考 "
        r"C:\Users\Alice\secret\model.py，run "
        "123e4567-e89b-12d3-a456-426614174000，"
        "api_key=sk-examplecredential12345，"
        "AWS_ACCESS_KEY_ID=AKIAIOSFODNN7EXAMPLE，"
        "credential=supersecretvalue，"
        r"path='C:\Users\Alice Smith\private\model.py'，"
        r"unc=\\server\share\Alice\secret.py"
    )
    (state / "request.json").write_text(
        json.dumps(
            {
                "run_id": name,
                "goal": goal,
                "workspace": str(workspace.resolve()),
                "state_dir": str(state.resolve()),
                "runtime": {
                    "name": "abaqus-2024",
                    "version": "2024",
                    "launcher_path": str(
                        Path(r"D:\SIMULIA\Commands\abq2024.bat").resolve()
                    ),
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    if duplicate_concept_query:
        (state / "amendments.json").write_text(
            json.dumps(
                {
                    "schema_version": "coding-run-amendments-v1",
                    "run_id": name,
                    "amendments": [
                        {
                            "sequence": 1,
                            "kind": "task_revision",
                            "reason": "fixture revision",
                            "goal": "Revise the task but preserve the original files.",
                            "task_revision": 1,
                            "revision_workspace_snapshot": {"files": []},
                            "revision_execution_start_index": 0,
                            "created_at": "2026-09-04T00:00:00Z",
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )

    concept_records: list[object] = [
        {
            "query": "Why is CPS4R suitable for plane stress?",
            "exact_terms": ["CPS4R"],
            "products": ["Abaqus/Standard"],
            "status": "found",
            "document_ids": ["doc-1"],
            "ranked_hits": [
                {"rank": 1, "document_id": "doc-1", "match": "exact"},
                {"rank": 2, "document_id": "doc-2", "match": "semantic"},
            ],
            "counted": True,
            "payload_bytes": 10,
            "context": {
                "trigger_source": "task_start",
                "trigger_outcome": "none",
            },
        }
    ]
    if duplicate_concept_query:
        legacy_duplicate = {
            **concept_records[0],  # type: ignore[dict-item]
            "counted": False,
            "status": "duplicate",
        }
        legacy_duplicate.pop("context")
        legacy_duplicate.pop("ranked_hits")
        concept_records.append(legacy_duplicate)
    _write_jsonl(
        state / "concept_lookups.jsonl",
        concept_records,
        malformed=duplicate_concept_query,
    )
    _write_jsonl(
        state / "api_lookups.jsonl",
        [
            {
                "source": "active",
                "query": "Part.BaseSolidBox depth keyword",
                "status": "found",
                "card_ids": ["card-1"],
                "counted": True,
                "payload_bytes": 10,
            }
        ],
    )
    if duplicate_concept_query:
        _write_jsonl(
            state / "diagnostic_retrievals.jsonl",
            [
                {
                    "schema_version": "diagnostic-retrieval-trace-v1",
                    "run_id": name,
                    "recorded_at": "2026-09-04T00:00:00Z",
                    "turn": 2,
                    "amendment_sequence": 1,
                    "task_revision": 1,
                    "execution_index": 0,
                    "diagnostic_level": "warning",
                    "diagnostic_source": "job.msg",
                    "diagnostic_line": 1,
                    "diagnostic_sha256": "a" * 64,
                    "formulation_version": "goal-identifier-window-v1",
                    "query": "CPS4R RF3 plane stress output DOF active",
                    "query_sha256": "b" * 64,
                    "retriever": "rrf",
                    "top_k": 4,
                    "fetch_k": 20,
                    "rrf_k": 60,
                    "index": {
                        "release": "2026 FD02",
                        "collection_name": "fixture",
                        "dense_manifest_sha256": "c" * 64,
                        "bm25_manifest_sha256": "d" * 64,
                        "bm25_database_sha256": "e" * 64,
                        "semantic_corpus_sha256": "f" * 64,
                    },
                    "status": "found",
                    "counted": True,
                    "ranked_hits": [
                        {
                            "rank": 1,
                            "document_id": "simaelm-r-2delem",
                            "section_id": "plane-stress",
                            "section_title": "Plane Stress Elements",
                            "match": "hybrid",
                        }
                    ],
                    "evidence_id": "1" * 64,
                    "payload_bytes": 100,
                    "elapsed_ms": 1.0,
                }
            ],
        )

    _write_jsonl(
        state / "events.jsonl",
        [
            {
                "event": "clarification",
                "data": {
                    "field_id": "plate_thickness",
                    "kind": "missing_physical_input",
                    "question": "板厚是多少？",
                    "impact": "改变截面刚度",
                    "answer": "must not be copied",
                },
            },
            {
                "event": "verification",
                "data": {
                    "passed": False,
                    "failure_stage": "odb_physics",
                    "feedback": "RF1 sign violates the task contract",
                },
            },
            {
                "event": "infrastructure_error",
                "data": {
                    "type": "ModelAPIError",
                    "message": "gateway timed out after 10 seconds",
                },
            },
        ],
    )

    stderr_lines = [
        "AbaqusException: Unknown element type C3D999 is not supported for this analysis",
        "AttributeError: 'Part' object has no attribute 'BaseSolidBox'",
        r"FileNotFoundError: [Errno 2] No such file or directory: C:\Users\Alice\secret.py",
        "RuntimeError: mysterious failure near CPS4R node 99",
        "TimeoutError: Gateway timed out; Authorization=Bearer abcdef123456",
    ]
    (state / "raw" / "call-1").mkdir(parents=True)
    (state / "raw" / "call-1" / "stderr.bin").write_bytes(
        ("\n".join(stderr_lines) + "\n").encode("utf-8")
    )

    solver_lines = [
        f"***ERROR: TOO MANY ATTEMPTS MADE FOR THIS INCREMENT AT NODE {solver_node}",
    ]
    if duplicate_concept_query:
        solver_lines.append("***ERROR: COULD NOT OBTAIN A LICENSE FROM LICENSE SERVER")
    (workspace / f"job-{solver_node}.msg").write_text(
        "\n".join(solver_lines) + "\n",
        encoding="utf-8",
    )
    _write_jsonl(
        state / "execution_results.jsonl",
        [
            {
                "invocation": "solver_job",
                "command": ["abq2024.bat", "job=fixture"],
                "workspace": str(workspace.resolve()),
                "exit_code": 0,
                "timed_out": False,
                "duration_seconds": 1.0,
                "stdout": "",
                "stderr": "",
                "stdout_encoding": "utf-8",
                "stderr_encoding": "utf-8",
                "stdout_raw_path": str(
                    (state / "raw" / "call-1" / "stdout.bin").resolve()
                ),
                "stderr_raw_path": str(
                    (state / "raw" / "call-1" / "stderr.bin").resolve()
                ),
                "diagnostics": [
                    {
                        "level": "error",
                        "source": f"job-{solver_node}.msg",
                        "line": 1,
                        "message": solver_lines[0],
                    }
                ],
            }
        ],
    )


def _matrix(report: RetrievalInputInventoryReport) -> dict[str, CoverageMatrixRow]:
    return {row.surface: row for row in report.coverage_matrix}


def test_inventory_covers_real_input_lanes_and_deduplicates_families(
    tmp_path: Path,
) -> None:
    run_root = tmp_path / "runs"
    run_root.mkdir()
    _make_run(
        run_root,
        "run-a",
        duplicate_concept_query=True,
        solver_node=101,
    )
    _make_run(
        run_root,
        "run-b",
        duplicate_concept_query=False,
        solver_node=202,
    )
    (run_root / "run-b" / "workspace" / "request.json").write_text(
        '{"goal":"not a CodingRun request"}',
        encoding="utf-8",
    )
    copied_state = run_root / "copied-state"
    copied_state.mkdir()
    copied_state.joinpath("request.json").write_bytes(
        (run_root / "run-b" / "state" / "request.json").read_bytes()
    )
    comparison_benchmark = tmp_path / "comparison.jsonl"
    _write_jsonl(
        comparison_benchmark,
        [
            {
                "case_id": "concept",
                "query_kind": "concept",
                "query": "concept query",
            },
            {
                "case_id": "symbol",
                "query_kind": "identifier_lookup",
                "query": "CPS4R",
            },
            {
                "case_id": "multi",
                "query_kind": "concept",
                "query": "multi query",
                "requires_multiple_documents": True,
            },
        ],
    )
    diagnostic_benchmark = tmp_path / "diagnostic.jsonl"
    _write_jsonl(
        diagnostic_benchmark,
        [
            {
                "dataset_id": "diagnostic-v1",
                "case_id": "diagnostic-1",
                "need_group_id": "diagnostic-group",
                "query": "raw diagnostic",
            },
            {
                "dataset_id": "diagnostic-v1",
                "case_id": "diagnostic-2",
                "need_group_id": "diagnostic-group",
                "query": "normalized diagnostic",
            },
        ],
    )

    report = build_inventory(
        run_root,
        benchmark_paths=(comparison_benchmark, diagnostic_benchmark),
    )

    assert report.summary.runs == 2
    assert report.summary.valid_requests == 2
    assert report.summary.unique_goals == 2
    assert report.summary.task_revision_goals == 1
    assert report.summary.non_ascii_goals == 2
    assert report.summary.negated_goals == 2
    assert report.summary.constrained_goals == 3
    assert report.summary.composite_goals == 2
    assert report.summary.runtime_version_qualified_goals == 0
    assert report.summary.concept_query_occurrences == 3
    assert report.summary.unique_concept_queries == 1
    assert report.concept_queries[0].counted_occurrences == 2
    assert report.concept_queries[0].trigger_sources == ("task_start",)
    assert report.concept_queries[0].trigger_outcomes == ("none",)
    assert report.concept_queries[0].match_types == ("exact", "semantic")
    assert report.concept_queries[0].legacy_context_occurrences == 1
    assert report.summary.api_lookup_occurrences == 2
    assert report.summary.unique_api_queries == 1
    assert report.summary.diagnostic_retrieval_occurrences == 1
    assert report.summary.unique_diagnostic_retrieval_queries == 1
    assert report.diagnostic_retrieval_queries[0].task_revisions == (1,)

    # Different node labels are distinct exact observations but one diagnostic family.
    assert report.summary.solver_diagnostic_occurrences == 2
    assert report.summary.solver_exact_signatures == 2
    assert report.summary.solver_normalized_families == 1
    assert report.solver_diagnostic_families[0].expected_lane == "bm25_diagnostic"
    assert report.solver_diagnostic_families[0].rag_candidate

    owners = {item.owner: item for item in report.summary.runtime_owner_counts}
    assert owners["concept_candidate"].occurrences == 2
    assert owners["api_catalog"].occurrences == 2
    assert owners["local_code"].occurrences == 2
    assert owners["unknown"].occurrences == 2
    by_owner = {item.owner: item for item in report.runtime_exception_families}
    assert by_owner["concept_candidate"].rag_candidate
    assert by_owner["concept_candidate"].expected_lane == "concept_html"
    assert not by_owner["api_catalog"].rag_candidate
    assert by_owner["api_catalog"].exclusion_reason == "api_exception"
    assert not by_owner["local_code"].rag_candidate
    assert by_owner["local_code"].exclusion_reason == "missing_file"
    assert by_owner["unknown"].expected_lane == "unknown"

    assert report.summary.verification_failures == 2
    assert report.summary.clarifications == 2
    # Two event timeouts, two stderr timeouts, and one solver-license failure.
    assert report.summary.infrastructure_events == 5
    assert report.summary.scan_issues == 3
    assert {issue.code for issue in report.issues}.issuperset(
        {"invalid_request_json", "request_state_dir_mismatch"}
    )
    assert len(report.benchmarks) == 2
    assert sum(item.case_count for item in report.benchmarks) == 5
    assert sum(item.need_group_count for item in report.benchmarks) == 4

    matrix = _matrix(report)
    assert matrix["concept_lookup"].expected_lane == "concept_html"
    assert matrix["concept_lookup"].benchmark_case_count == 2
    assert matrix["exact_symbol_lookup"].expected_lane == "exact_symbol"
    assert matrix["exact_symbol_lookup"].observation_count == 3
    assert matrix["exact_symbol_lookup"].unique_signatures == 1
    assert matrix["exact_symbol_lookup"].benchmark_case_count == 1
    assert matrix["multi_document_lookup"].benchmark_case_count == 1
    assert matrix["api_lookup"].expected_lane == "api_catalog"
    assert matrix["diagnostic_advisory"].expected_lane == "diagnostic_hybrid"
    assert matrix["diagnostic_advisory"].observation_count == 1
    assert matrix["solver_diagnostic"].expected_lane == "bm25_diagnostic"
    assert matrix["solver_diagnostic"].benchmark_case_count == 2
    assert matrix["runtime_local_code_exception"].expected_lane == "runtime_evidence"
    assert matrix["runtime_api_exception"].runs_observed == 2
    assert matrix["runtime_local_code_exception"].runs_observed == 2
    assert matrix["verification_failure"].expected_lane == "task_oracle"
    assert matrix["infrastructure"].expected_lane == "none"
    assert matrix["clarification"].expected_lane == "none"
    assert matrix["runtime_unknown_exception"].expected_lane == "unknown"
    assert matrix["scan_unknown"].expected_lane == "unknown"
    assert matrix["semantic_goal"].expected_lane == "query_formulation"
    assert matrix["semantic_goal"].status == "non_rag_observed"
    assert matrix["negated_goal"].observation_count == 2
    assert matrix["constraint_heavy_goal"].observation_count == 3
    assert matrix["task_revision_goal"].observation_count == 1
    assert matrix["runtime_version_qualified_goal"].status == "not_observed"
    assert matrix["cross_language_concept_lookup"].observation_count == 2
    assert matrix["cross_language_concept_lookup"].unique_signatures == 1

    serialized = report.model_dump_json()
    assert str(run_root.resolve()) not in serialized
    assert r"C:\Users\Alice" not in serialized
    assert "123e4567-e89b-12d3-a456-426614174000" not in serialized
    assert "sk-examplecredential12345" not in serialized
    assert "abcdef123456" not in serialized
    assert "AKIAIOSFODNN7EXAMPLE" not in serialized
    assert "supersecretvalue" not in serialized
    assert "Alice Smith" not in serialized
    assert "server\\share" not in serialized
    assert "CPS4R" in serialized
    assert "RF1" in serialized
    assert "*STATIC" in serialized
    assert all(
        "Bearer" not in symbol for goal in report.goals for symbol in goal.exact_symbols
    )
    assert all(not Path(item.path).is_absolute() for item in report.artifacts)
    assert all(not Path(item.request_path).is_absolute() for item in report.runs)


def test_report_is_strict_and_cli_writes_create_new(tmp_path: Path) -> None:
    run_root = tmp_path / "runs"
    run_root.mkdir()
    _make_run(
        run_root,
        "run-a",
        duplicate_concept_query=False,
        solver_node=1,
    )
    output = tmp_path / "evidence" / "inventory.json"

    assert main(["--run-root", str(run_root), "--output", str(output)]) == 0
    persisted = RetrievalInputInventoryReport.model_validate_json(
        output.read_text(encoding="utf-8")
    )
    assert persisted.summary.runs == 1
    assert len(persisted.run_root_sha256) == 64

    with pytest.raises(FileExistsError):
        write_report_create_new(persisted, output)
    forbidden_output = run_root / "new" / "nested" / "must-not-pollute.json"
    with pytest.raises(ValueError, match="outside run_root"):
        main(
            [
                "--run-root",
                str(run_root),
                "--output",
                str(forbidden_output),
            ]
        )
    assert not (run_root / "new").exists()
    with pytest.raises(ValidationError):
        RetrievalInputInventoryReport.model_validate(
            {**persisted.model_dump(), "credential": "must be rejected"}
        )


@pytest.mark.parametrize(
    "value",
    [
        "run/token=plaincredential123456/file",
        "Use *STATIC Bearer abcdefghijklmnopqrstuvwxyz123456",
        r"relative/C:\Users\Alice\private.py",
    ],
)
def test_output_level_deny_scan_rejects_sensitive_values(value: str) -> None:
    with pytest.raises(ValueError, match="unredacted sensitive"):
        _assert_report_values_safe({"future_field": value})
