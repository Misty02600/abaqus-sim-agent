"""固定 RAG 基线的显式请求批次；输出真实证据、质量分数和独立工程状态。"""

import argparse
import platform
import time
from pathlib import Path

from . import evidence_candidate as io
from .rag_evidence_baseline import (
    BUDGET,
    FIXED_HASHES,
    OPTIONS,
    PROFILE,
    BaselineBatch,
    RagEvidenceBaseline,
    aggregate_scores,
    score_case,
)

_IMPLEMENTATIONS = (
    "rag_evidence_baseline.py",
    "rag_evidence_baseline_cli.py",
    "unified_retrieval.py",
    "unified_candidate_backend.py",
    "cross_encoder.py",
    "passage_selection.py",
    "evidence_candidate.py",
    "ingestion_fixes_candidate.py",
    "equation_context_delivery.py",
)


def run_batch(
    *,
    candidate: Path,
    symbol_index: Path,
    scope_rules: Path,
    ranker_directory: Path,
    requests: Path,
    output: Path,
) -> dict:
    """用同一组合入口执行所有请求；不吞掉样本失败或把无标签当成满分。

    Note:
        输出目录必须全新且不能位于输入索引/模型内。原始结果先落盘再评分；样本
        错误保留分母并继续有限批次。加载/最终绑定错误使整批invalid；可捕获中断
        尽力保存incomplete标记。硬终止或落盘失败时，没有有效completion即未完成。
        任何错误均不触发自动重试或索引重建。
    """
    resolved = output.resolve()
    if any(
        resolved.is_relative_to(p.resolve())
        for p in (candidate, symbol_index, ranker_directory)
    ):
        raise ValueError("output must be outside immutable input directories")
    output.mkdir(parents=True, exist_ok=False)
    rows = []
    batch = None
    stage = "input_validation"
    started = time.perf_counter()
    try:
        batch = BaselineBatch.model_validate_json(requests.read_bytes())
        implementations = {
            str(Path(__file__).with_name(name).resolve()): io.sha(
                Path(__file__).with_name(name)
            )
            for name in _IMPLEMENTATIONS
        }
        manifest = {
            "profile": PROFILE,
            "requests_sha256": io.sha(requests),
            "cases": batch.model_dump(mode="json")["cases"],
            "fixed_content": dict(FIXED_HASHES),
            "options": dict(OPTIONS),
            "budget": BUDGET.model_dump(mode="json"),
            "input_paths": {
                k: str(p.resolve())
                for k, p in {
                    "candidate": candidate,
                    "symbols": symbol_index,
                    "scopes": scope_rules,
                    "ranker": ranker_directory,
                }.items()
            },
            "implementation_sha256": implementations,
            "python_version": platform.python_version(),
            "scope": "development regression scores; no overall quality gate, generation, Abaqus or default promotion",
        }
        io.write_new(output / "manifest.json", manifest)
        stage = "load_binding"
        service = RagEvidenceBaseline(
            candidate, symbol_index, scope_rules, ranker_directory
        )
        io.write_new(
            output / "ready.json",
            {
                "binding": service.binding,
                "startup_seconds": time.perf_counter() - started,
            },
        )
        stage = "requests"
        for case in batch.cases:
            try:
                result = service.search(case.request)
            except Exception as exc:
                result = {
                    "status": "execution_error",
                    "evidence": [],
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                }
            # Preserve the actual output even when the independent scorer rejects it.
            io.write_new(output / (case.id + ".result.json"), result)
            try:
                score = score_case(case, result, service.units, service.child_to_parent)
            except Exception as exc:
                error = {
                    "status": "execution_error",
                    "evidence": [],
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                }
                io.write_new(output / (case.id + ".scoring-error.json"), error)
                score = score_case(case, error, service.units, service.child_to_parent)
            rows.append(score)
            io.write_new(output / (case.id + ".score.json"), score)
            print(
                case.id,
                score["status"],
                "quality",
                score["requirement_coverage"],
                flush=True,
            )
        stage = "final_binding"
        service.assert_unchanged()
        if io.sha(requests) != manifest["requests_sha256"] or any(
            io.sha(Path(p)) != h for p, h in implementations.items()
        ):
            raise ValueError("batch input or implementation changed")
        scores = aggregate_scores(rows)
        failed = (
            scores["execution_errors"] > 0
            or scores["guard_checks"] != scores["guard_passes"]
        )
        summary = {
            "profile": PROFILE,
            "batch_status": "failed" if failed else "complete",
            "engineering_status": "failed" if failed else "passed",
            "quality": scores,
            "results": rows,
            "manifest_sha256": io.sha(output / "manifest.json"),
            "raw_sha256": {p.name: io.sha(p) for p in output.glob("*.json")},
            "elapsed_seconds": time.perf_counter() - started,
        }
        io.write_new(output / "summary.json", summary)
        io.write_new(
            output / "completion.json",
            {
                "status": summary["batch_status"],
                "summary_sha256": io.sha(output / "summary.json"),
                "processed_cases": len(rows),
                "expected_cases": len(batch.cases),
            },
        )
        return summary
    except BaseException as exc:
        io.write_new(
            output / "completion.json",
            {
                "status": "invalid" if isinstance(exc, Exception) else "incomplete",
                "stage": stage,
                "error_type": type(exc).__name__,
                "error": str(exc),
                "processed_cases": len(rows),
                "expected_cases": len(batch.cases) if batch else None,
                "remaining_cases": [c.id for c in batch.cases[len(rows) :]]
                if batch
                else None,
                "partial_scores_not_valid_for_acceptance": aggregate_scores(rows),
            },
        )
        raise


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate-directory", type=Path, required=True)
    parser.add_argument("--symbol-index-directory", type=Path, required=True)
    parser.add_argument("--scope-rules", type=Path, required=True)
    parser.add_argument("--reranker-directory", type=Path, required=True)
    parser.add_argument("--requests", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = run_batch(
        candidate=args.candidate_directory,
        symbol_index=args.symbol_index_directory,
        scope_rules=args.scope_rules,
        ranker_directory=args.reranker_directory,
        requests=args.requests,
        output=args.output,
    )
    if result["engineering_status"] != "passed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
