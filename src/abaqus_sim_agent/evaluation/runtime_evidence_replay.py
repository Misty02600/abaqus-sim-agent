"""显式原16题工程等价回放，不重新定义检索质量门槛或调用生成模型。"""

import argparse
import asyncio
import json
import time
from pathlib import Path

from abaqus_sim_agent.retrieval.evidence_contracts import SearchRequest

from .evidence_receipt import RuntimePaths, canonical, digest, read
from .runtime_evidence_service import RuntimeEvidenceService


async def replay(
    *,
    paths: RuntimePaths,
    requests: Path,
    baseline_results: Path,
    output: Path,
    selected: tuple[str, ...] = (),
):
    output.mkdir(parents=True, exist_ok=False)  # noqa: ASYNC240 -- explicit sequential offline replay
    cases = read(requests)["cases"]
    if selected:
        cases = [c for c in cases if c["id"] in selected]
        if len(cases) != len(selected):
            raise ValueError("unknown or duplicate selected case")
    started = time.monotonic()
    rows = []
    for case in cases:
        state_root = output / case["id"] / "state"
        service = RuntimeEvidenceService(
            paths, state_root=state_root, workspace=output / case["id"] / "workspace"
        )
        request = SearchRequest.model_validate_json(
            json.dumps(
                {"query": case["request"], "constraints_origin": "user_confirmed"}
            )
        )
        t = time.monotonic()
        result = await service.search(request, search_id=case["id"], deadline=t + 60.0)
        elapsed = time.monotonic() - t
        old = read(baseline_results / (case["id"] + ".result.json"))
        # Only newly added provenance metadata is excluded from legacy equality.
        comparable = []
        for item in result.evidence:
            raw = item.model_dump(mode="json", exclude={"body_sha256", "member_xpaths"})
            if not raw["equation_context"]:
                raw.pop("equation_context")
            comparable.append(raw)
        expected = (
            "rejected"
            if old["status"] in {"query_too_long", "reranker_input_too_long"}
            else old["status"]
        )
        state = next(state_root.iterdir())
        new_raw_path = state / "baseline-result.json"
        new_raw = read(new_raw_path) if new_raw_path.exists() else None
        ranking_equal = new_raw is not None and new_raw.get("trace", {}).get(
            "ranking", []
        ) == old.get("trace", {}).get("ranking", [])
        row = {
            "case_id": case["id"],
            "status": result.status,
            "reason_code": getattr(result, "reason_code", None),
            "evidence_equal": comparable == old["evidence"],
            "ranking_equal": ranking_equal,
            "baseline_budget_equal": result.cost.baseline_evidence_tokens
            == old.get("context_tokens"),
            "status_mapping_equal": result.status == expected,
            "elapsed_seconds": elapsed,
            "cost": result.cost.model_dump(mode="json"),
            "result_sha256": digest(state / "result.json"),
            "old_result_sha256": digest(
                baseline_results / (case["id"] + ".result.json")
            ),
        }
        row["passed"] = (
            all(
                row[k]
                for k in (
                    "evidence_equal",
                    "ranking_equal",
                    "baseline_budget_equal",
                    "status_mapping_equal",
                )
            )
            and elapsed <= 60
        )
        rows.append(row)
        print(case["id"], result.status, row["passed"], round(elapsed, 2), flush=True)
    summary = {
        "profile": "doc-evidence-result-v1-runtime-replay",
        "requests_sha256": digest(requests),
        "rows": rows,
        "passed": all(r["passed"] for r in rows),
        "cases": len(rows),
        "elapsed_seconds": time.monotonic() - started,
        "claim": "Engineering equivalence only; not retrieval improvement, Agent benefit or 2024 compatibility.",
    }
    (output / "summary.json").write_text(canonical(summary), encoding="utf-8")
    return summary


def main():
    parser = argparse.ArgumentParser()
    for name in (
        "candidate",
        "symbol-index",
        "scope-rules",
        "ranker-directory",
        "requests",
        "baseline-results",
        "output",
    ):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--case", action="append", default=[])
    args = parser.parse_args()
    paths = RuntimePaths(
        **{
            k: getattr(args, k).resolve()
            for k in ("candidate", "symbol_index", "scope_rules", "ranker_directory")
        }
    )
    result = asyncio.run(
        replay(
            paths=paths,
            requests=args.requests,
            baseline_results=args.baseline_results,
            output=args.output,
            selected=tuple(args.case),
        )
    )
    raise SystemExit(0 if result["passed"] else 1)


if __name__ == "__main__":
    main()
