"""宿主固定入口：只接收state中的typed请求，不接受脚本、命令或算法覆盖。"""

import shutil
import sys
import time
import traceback
from pathlib import Path

from abaqus_sim_agent.retrieval.evidence_contracts import (
    Cost,
    DeliveryLimit,
    Found,
    NotFound,
    Rejected,
    Unavailable,
)

from .evidence_receipt import (
    RuntimePaths,
    WorkerReceipt,
    WorkerRequest,
    attach_provenance,
    canonical,
    check_deadline,
    digest,
    source_permitted,
    verify_snapshot,
)


def adapt_result(
    request: WorkerRequest,
    raw: dict,
    baseline,
    load_seconds: float,
    search_seconds: float,
):
    trace = raw.get("trace", {})
    common = {"search_id": request.search_id, "binding": request.binding}
    cost = Cost(
        stage="validating",
        load_seconds=load_seconds,
        search_seconds=search_seconds,
        candidate_count=len(trace.get("pool_ids", [])),
        scored_count=len(trace.get("ranking", [])),
        baseline_evidence_tokens=raw.get("context_tokens"),
    )
    if raw["status"] in {"query_too_long", "reranker_input_too_long"}:
        return Rejected(
            **common,
            cost=cost,
            reason_code="query_too_long",
            message="Query or query/passage pair exceeds the fixed tokenizer limit; no evidence delivered.",
        )
    if raw["status"] not in {"found", "not_found"}:
        raise ValueError("unknown baseline result")
    limits = [DeliveryLimit(**row) for row in trace.get("rejected", [])]
    evidence = []
    for row in raw["evidence"]:
        if not source_permitted(row["source"], request.request):
            limits.append(DeliveryLimit(id=row["id"], reason="source_constraint"))
            continue
        item = attach_provenance(row, baseline)
        if (
            baseline.count(
                canonical([e.model_dump(mode="json") for e in [*evidence, item]])
            )
            > 4000
        ):
            limits.append(
                DeliveryLimit(id=item.id, reason="serialized_protocol_evidence_budget")
            )
        else:
            evidence.append(item)
    cost = cost.model_copy(
        update={
            "body_tokens": tuple(baseline.count(e.body) for e in evidence),
            "evidence_tokens": baseline.count(
                canonical([e.model_dump(mode="json") for e in evidence])
            ),
        }
    )
    common.update(cost=cost, limits=tuple(limits))
    if evidence:
        return Found(
            **common,
            evidence=tuple(evidence),
            message="Candidates only; relevance, sufficiency and runtime compatibility are not verified. Unknown applicability remains unknown.",
        )
    release = request.request.query.source_release
    if release is not None and release not in {
        p.release for p in baseline.backend.parents.values()
    }:
        reason = "requested_release_not_indexed"
    elif any(r.reason != "source_constraint" for r in limits):
        reason = "no_unit_fits_budget"
    elif limits or trace.get("excluded_parents", 0) == len(baseline.backend.parents):
        reason = "scope_filtered"
    else:
        reason = "no_candidates"
    return NotFound(
        **common,
        reason_code=reason,
        message="No evidence delivered under this request's fixed scope and budgets; this does not prove the information is absent from documentation.",
    )


def _deny_network(event, args):
    if event in {"socket.connect", "socket.bind", "socket.getaddrinfo"}:
        raise OSError("network disabled in local evidence worker")


def private_index_copies(request: WorkerRequest, state: Path) -> RuntimePaths:
    """复制已绑定的存储bytes；Chroma的读取侧记账只可写入这些短生命周期副本。

    Note:
        不创建/重建collection，不改变向量或metadata内容。源库在复制前后及交付前
        都按宿主snapshot检查；不支持并发更新源库。不能用hardlink共享可写SQLite。
    """
    candidate = state / "private-candidate"
    symbols = state / "private-symbols"
    for original, target in (
        (request.paths.candidate, candidate),
        (request.paths.symbol_index, symbols),
    ):
        original = original.resolve()
        for filename, expected in request.snapshot.items():
            source = Path(filename)
            if not source.is_relative_to(original):
                continue
            check_deadline(request.deadline)
            copied = target / source.relative_to(original)
            copied.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, copied)
            if digest(copied) != expected:
                raise ValueError("private index copy differs from bound original")
    verify_snapshot(request.snapshot, request.deadline)
    return RuntimePaths(
        candidate=candidate,
        symbol_index=symbols,
        scope_rules=request.paths.scope_rules,
        ranker_directory=request.paths.ranker_directory,
    )


def run(state: Path) -> None:
    request = WorkerRequest.model_validate_json((state / "request.json").read_bytes())
    sys.addaudithook(_deny_network)
    started = time.monotonic()
    stage = "loading"
    calls = 0
    raw_sha = None
    try:
        check_deadline(request.deadline)
        verify_snapshot(request.snapshot, request.deadline)
        from .rag_evidence_baseline import RagEvidenceBaseline
        from .unified_retrieval import QUERY_ADAPTER

        private = private_index_copies(request, state)
        baseline = RagEvidenceBaseline(
            private.candidate,
            private.symbol_index,
            request.paths.scope_rules,
            request.paths.ranker_directory,
        )
        loaded = time.monotonic()
        stage = "searching"
        check_deadline(request.deadline)
        query = QUERY_ADAPTER.validate_json(request.request.query.model_dump_json())
        calls += 1
        raw = baseline.search(query)
        searched = time.monotonic()
        stage = "validating"
        result = adapt_result(
            request, raw, baseline, loaded - started, searched - loaded
        )
        baseline.assert_unchanged()
        verify_snapshot(request.snapshot, request.deadline)
        check_deadline(request.deadline)
        (state / "baseline-result.json").write_text(canonical(raw), encoding="utf-8")
        raw_sha = digest(state / "baseline-result.json")
        result = result.model_copy(
            update={
                "cost": result.cost.model_copy(
                    update={
                        "stage": "delivered",
                        "validation_seconds": time.monotonic() - searched,
                    }
                )
            }
        )
    except Exception as exc:
        traceback.print_exc()
        if isinstance(exc, TimeoutError):
            reason = "worker_timeout"
        elif isinstance(exc, (ValueError, KeyError)):
            reason = "integrity_mismatch"
        elif stage == "loading":
            reason = "model_unavailable"
        else:
            reason = "worker_failed"
        result = Unavailable(
            search_id=request.search_id,
            reason_code=reason,
            cost=Cost(stage=stage),
            message="Local evidence worker did not produce validated evidence; raw diagnostics are retained by the host.",
        )
    receipt = WorkerReceipt(
        request_sha256=digest(state / "request.json"),
        result=result,
        raw_result_sha256=raw_sha,
        search_calls=calls,
    )
    temporary = state / "receipt.partial"
    temporary.write_text(receipt.model_dump_json(), encoding="utf-8")
    temporary.replace(state / "receipt.json")


def main():
    if len(sys.argv) != 1:
        raise SystemExit("evidence worker accepts no command-line arguments")
    run(Path.cwd())


if __name__ == "__main__":
    main()
