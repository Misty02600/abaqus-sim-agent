"""运行统一检索候选的显式JSON请求批次，所有结果写入新目录。"""

import argparse
import time
from collections import Counter
from pathlib import Path

from . import evidence_candidate as driver
from .unified_candidate_backend import BoundCandidateBackend, FixedRanker
from .unified_retrieval import (
    PROFILE,
    QUERY_ADAPTER,
    RECALL_PROFILE,
    CandidateBudget,
    UnifiedRetrievalCandidate,
)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate-directory", type=Path, required=True)
    parser.add_argument("--symbol-index-directory", type=Path, required=True)
    parser.add_argument("--scope-rules", type=Path, required=True)
    parser.add_argument("--requests", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--reranker-directory", type=Path, required=True)
    parser.add_argument(
        "--retrieval-mode", choices=("dense", "hybrid"), default="dense"
    )
    parser.add_argument("--candidates", type=int, default=64)
    parser.add_argument(
        "--query-text-mode",
        choices=("question_context", "question_only", "rerank_question_only"),
        default="question_context",
    )
    parser.add_argument(
        "--pool-policy", choices=("flat", "page_lexical"), default="flat"
    )
    parser.add_argument("--unit-body-tokens", type=int, default=1200)
    parser.add_argument("--context-tokens", type=int, default=4000)
    args = parser.parse_args()
    if args.pool_policy == "page_lexical" and args.retrieval_mode != "hybrid":
        parser.error("--pool-policy page_lexical requires --retrieval-mode hybrid")
    cases = driver.read(args.requests)["cases"]
    requests = [QUERY_ADAPTER.validate_python(c["request"]) for c in cases]
    if not cases or len({c["id"] for c in cases}) != len(cases):
        parser.error("requests require unique case IDs")
    if any(not c["id"].replace("-", "").replace("_", "").isalnum() for c in cases):
        parser.error("unsafe case ID")
    budget = CandidateBudget(
        candidates=args.candidates,
        unit_body_tokens=args.unit_body_tokens,
        context_tokens=args.context_tokens,
    )
    args.output.mkdir(parents=True, exist_ok=False)
    started = time.perf_counter()
    backend = BoundCandidateBackend(
        args.candidate_directory, args.symbol_index_directory, args.scope_rules
    )
    ranker = FixedRanker(args.reranker_directory)
    split = backend.split

    def delivery_count(value):
        return len(split.tokenizer.encode(value, add_special_tokens=False).ids)

    service = UnifiedRetrievalCandidate(
        backend,
        ranker,
        backend.parents,
        backend.scopes,
        backend.verified_sources,
        embedding_count=split.count,
        delivery_count=delivery_count,
        delivery_tokenizer="MiniLM WordPiece proxy, NOT DeepSeek billing tokenizer",
        budget=budget,
    )
    files = [
        Path(__file__),
        Path(__file__).with_name("unified_retrieval.py"),
        Path(__file__).with_name("unified_candidate_backend.py"),
    ]
    manifest = {
        "profile": PROFILE
        if args.query_text_mode == "question_context" and args.pool_policy == "flat"
        else RECALL_PROFILE,
        "requests_sha256": driver.sha(args.requests),
        "scope_rules_sha256": driver.sha(args.scope_rules),
        "dataset": backend.dataset_binding,
        "candidate_manifest": driver.sha(args.candidate_directory / "manifest.json"),
        "model": ranker.acquisition,
        "budgets": budget.model_dump(mode="json"),
        "delivery_tokenizer": service.delivery_tokenizer,
        "retrieval_mode": args.retrieval_mode,
        "query_text_mode": args.query_text_mode,
        "pool_policy": args.pool_policy,
        "implementation": {str(p.resolve()): driver.sha(p) for p in files},
        "cases": cases,
        "startup_seconds": time.perf_counter() - started,
        "scope": "fixed development batch; integrated profile, not single-factor causal evidence; default product unchanged",
    }
    driver.write_new(args.output / "manifest.json", manifest)
    rows = []
    for case, request in zip(cases, requests, strict=True):
        t = time.perf_counter()
        try:
            result = service.search(
                request,
                retrieval_mode=args.retrieval_mode,
                query_text_mode=args.query_text_mode,
                pool_policy=args.pool_policy,
            )
        except Exception as exc:
            result = {
                "status": "execution_error",
                "error_type": type(exc).__name__,
                "error": str(exc),
                "evidence": [],
            }
        required = []
        for requirement in case.get("requirements", []):
            key = requirement["unit_id"]
            target = backend.parents[key]
            required.append(
                {
                    "need": requirement["need"],
                    "unit_id": key,
                    "delivered_complete": any(
                        e["id"] == key and e["body"] == target.body
                        for e in result["evidence"]
                    ),
                }
            )
        expected = case.get("expected_status")
        contract_ok = result["status"] != "execution_error" and (
            expected is None or result["status"] == expected
        )
        if request.kind == "question":
            contract_ok = contract_ok and all(
                e["body_scope"] == "complete_stored_evidence_unit"
                for e in result["evidence"]
            )
        else:
            contract_ok = contract_ok and all(
                e["body_scope"] == "indexed_symbol_card" for e in result["evidence"]
            )
        contract_ok = (
            contract_ok and result.get("context_tokens", 0) <= budget.context_tokens
        )
        contract_ok = contract_ok and all(
            service._scope(e["id"]).permits(request.product) for e in result["evidence"]
        )
        row = {
            "id": case["id"],
            "kind": request.kind,
            "status": result["status"],
            "contract_ok": contract_ok,
            "required_units": required,
            "context_tokens": result.get("context_tokens", 0),
            "evidence_units": len(result["evidence"]),
            "unknown_scope_units": sum(
                e["applicability"]["origin"] == "unknown" for e in result["evidence"]
            ),
            "rejected": result.get("trace", {}).get("rejected", []),
            "elapsed_seconds": time.perf_counter() - t,
            "selected_ids": [e["id"] for e in result["evidence"]],
        }
        driver.write_new(
            args.output / (case["id"] + ".json"), {**row, "result": result}
        )
        rows.append(row)
        print(
            case["id"],
            result["status"],
            "contract",
            contract_ok,
            "tokens",
            row["context_tokens"],
            "needs",
            [r["delivered_complete"] for r in required],
            result.get("error", ""),
            flush=True,
        )
    backend.assert_unchanged()
    if (
        driver.sha(args.requests) != manifest["requests_sha256"]
        or driver.sha(args.scope_rules) != manifest["scope_rules_sha256"]
        or any(driver.sha(Path(p)) != h for p, h in manifest["implementation"].items())
    ):
        raise ValueError("batch inputs or implementation changed")
    driver._check_binding(args.candidate_directory)
    summary = {
        "manifest": manifest,
        "results": rows,
        "contract_passes": sum(r["contract_ok"] for r in rows),
        "cases": len(rows),
        "status_counts": dict(Counter(r["status"] for r in rows)),
        "required_complete": sum(
            n["delivered_complete"] for r in rows for n in r["required_units"]
        ),
        "required_checks": sum(len(r["required_units"]) for r in rows),
        "symbol_index_binding": backend.symbol_binding,
        "raw_sha256": {p.name: driver.sha(p) for p in args.output.glob("*.json")},
    }
    driver.write_new(args.output / "summary.json", summary)
    print(
        "COMPLETE",
        summary["contract_passes"],
        "/",
        len(rows),
        "requirements",
        summary["required_complete"],
        "/",
        summary["required_checks"],
        flush=True,
    )


if __name__ == "__main__":
    main()
