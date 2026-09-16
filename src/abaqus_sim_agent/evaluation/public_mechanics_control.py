"""公开力学评测的传输观测与可重建报告，不修改原始 CodingRun outcome。"""

import asyncio
import hashlib
import json
import os
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from typing import Any
from uuid import uuid4

import httpx

from abaqus_sim_agent.coding.persistence import write_text_atomic


def save_json(path: Path, value):
    write_text_atomic(path, json.dumps(value, ensure_ascii=False, indent=2))


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def append_event(path: Path, event: str, **values):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as stream:
        stream.write(
            json.dumps({"event": event, "at": datetime.now(UTC).isoformat(), **values})
            + "\n"
        )
        stream.flush()
        os.fsync(stream.fileno())


class ObservedTransport(httpx.AsyncBaseTransport):
    """记录每个实际 HTTP 尝试，包括响应体读取失败；仅用于非流式评测。

    Note:
        不保存请求/响应原文、凭据或任意响应头。强制终止时未结束的尝试保持 unknown。
    """

    def __init__(self, path: Path, inner: httpx.AsyncBaseTransport | None = None):
        self.path = path
        self.inner = inner or httpx.AsyncHTTPTransport()

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        attempt = uuid4().hex
        started = perf_counter()
        append_event(
            self.path,
            "http_started",
            attempt=attempt,
            retry=request.headers.get("x-stainless-retry-count"),
            body_sha256=hashlib.sha256(request.content).hexdigest(),
        )
        response = None
        try:
            response = await self.inner.handle_async_request(request)
            append_event(
                self.path, "http_headers", attempt=attempt, status=response.status_code
            )
            await response.aread()
            append_event(
                self.path,
                "http_completed",
                attempt=attempt,
                status=response.status_code,
                seconds=perf_counter() - started,
                response_bytes=len(response.content),
            )
            return response
        except BaseException as exc:
            append_event(
                self.path,
                "http_failed",
                attempt=attempt,
                error_type=type(exc).__name__,
                seconds=perf_counter() - started,
            )
            raise
        finally:
            if response is not None:
                await response.aclose()

    async def aclose(self):
        await self.inner.aclose()


def transport_summary(path: Path):
    if not path.exists():
        return {"attempts": None, "completed": None, "failed": None, "unknown": None}
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    starts = {r["attempt"] for r in rows if r["event"] == "http_started"}
    completed = {r["attempt"] for r in rows if r["event"] == "http_completed"}
    failed = {r["attempt"] for r in rows if r["event"] == "http_failed"}
    return {
        "attempts": len(starts),
        "completed": len(completed),
        "failed": len(failed),
        "unknown": len(starts - completed - failed),
    }


async def run_until_pause(service, marker: Path):
    """取消由 service 持有的任务，让它记录 interrupted 并清理 typed runtime。"""
    task = asyncio.create_task(service.run())

    async def watch():
        # A cross-process pause marker requires polling, not an in-process Event.
        while not marker.exists():  # noqa: ASYNC110, ASYNC240
            await asyncio.sleep(0.2)

    watcher = asyncio.create_task(watch())
    try:
        done, _ = await asyncio.wait(
            (task, watcher), return_when=asyncio.FIRST_COMPLETED
        )
        if task in done:
            return await task
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        return None
    finally:
        for pending in (task, watcher):
            if not pending.done():
                pending.cancel()
        await asyncio.gather(task, watcher, return_exceptions=True)


def summarize_case(root: Path, name: str):
    state = root / name / "state"
    outcome = read_json(state / "outcome.json")
    resume = read_json(state / "resume_state.json")
    attempts = [read_json(p) for p in sorted((state / "attempts").glob("*.json"))]
    last = attempts[-1] if attempts else {}
    if outcome:
        classification = (
            "formal_pass" if outcome["status"] == "completed" else "task_failed"
        )
    elif resume:
        classification = {
            "running": "interrupted_unknown",
            "interrupted": "interrupted",
            "needs_reconcile": "needs_reconcile",
        }.get(resume["status"], resume["status"])
        if resume.get("last_error_type") in {"ModelAPIError", "ModelHTTPError"}:
            classification = "provider_interrupted"
    elif last.get("error_type"):
        classification = "setup_error"
    elif state.exists():
        classification = "interrupted_unknown"
    else:
        classification = "not_started"
    usage = resume.get("usage") if resume else None
    deps = resume.get("dependencies", {}) if resume else {}
    counts = Counter()
    for path in (state / "step-persistence").glob("*/events.jsonl"):
        for line in path.read_text(encoding="utf-8").splitlines():
            counts[json.loads(line).get("kind")] += 1
    return {
        "case_id": name,
        "classification": classification,
        "outcome": outcome,
        "persisted_status": resume.get("status") if resume else None,
        "error_type": last.get("error_type"),
        "last_error_type": resume.get("last_error_type") if resume else None,
        "received_usage": usage,
        "model_request_events": {
            key: counts[key]
            for key in (
                "model_request_started",
                "model_request_completed",
                "model_request_failed",
            )
        },
        "http": transport_summary(state / "transport.jsonl"),
        "abaqus_calls": deps.get("abaqus_calls"),
        "solver_calls": deps.get("solver_calls"),
        "api_lookup_calls": deps.get("api_lookup_calls"),
        "persisted_active_wall_seconds": resume.get("active_wall_time_seconds")
        if resume
        else None,
        "attempts": attempts,
        "evidence_path": str(state),
        "billed_cost_usd": None,
    }


def build_report(root: Path, *, bindings_unchanged: bool):
    manifest = read_json(root / "manifest.json")
    rows = []
    for name in manifest["case_ids"]:
        try:
            rows.append(summarize_case(root, name))
        except (ValueError, KeyError, TypeError, OSError) as exc:
            rows.append(
                {
                    "case_id": name,
                    "classification": "invalid_evidence",
                    "evidence_error_type": type(exc).__name__,
                    "received_usage": None,
                    "http": {"attempts": None, "failed": None, "unknown": None},
                    "abaqus_calls": None,
                    "solver_calls": None,
                }
            )
    report = {
        "schema_version": "public-mechanics-report-v5",
        "verification_policy": manifest.get("verification_policy"),
        "profile": {
            "foundation": manifest.get("foundation"),
            "catalog_mode": manifest.get("catalog_mode"),
            "knowledge_resources": manifest.get("knowledge_resources"),
            "max_task_concurrency": manifest.get("max_task_concurrency", 1),
            "max_runtime_concurrency": manifest.get("max_runtime_concurrency", 1),
        },
        "script_review_status": "not_recorded_by_automatic_report",
        "at": datetime.now(UTC).isoformat(),
        "trials": rows,
        "bindings_unchanged": bindings_unchanged,
        "seal_sha256": hashlib.sha256((root / "seal.json").read_bytes()).hexdigest(),
        "note": "Derived view. Interrupted, unknown and posthoc results are not formal passes. HTTP usage is not billing.",
    }
    save_json(root / "report.json", report)
    lines = [
        "# 公开力学评测运行报告",
        "",
        "自动派生视图，不修改原 outcome；收到的 usage 不包含失败调用账单。自动结果检查与评测者脚本审查分开记录。",
        "",
        "| 案例 | 状态 | 收到响应 | HTTP尝试/失败/未知 | Abaqus/Solver |",
        "|---|---|---:|---|---|",
    ]
    for row in rows:
        u = row["received_usage"] or {}
        h = row["http"]
        lines.append(
            f"| {row['case_id']} | {row['classification']} | {u.get('requests', 'unknown')} | "
            f"{h['attempts']}/{h['failed']}/{h['unknown']} | {row['abaqus_calls']}/{row['solver_calls']} |"
        )
    lines.extend(
        [
            "",
            "无运行证据的计数为 unknown，不推断为0。not_started 不构成失败。",
            "恢复前的 attempt、失败HTTP与累计 usage 全部保留；未完成的工具作用必须由产品恢复门禁处理。",
            "私有 oracle 和 posthoc 与正式成绩分开；本报告不宣称 Foundation 因果收益。",
        ]
    )
    write_text_atomic(root / "report.md", "\n".join(lines) + "\n")
    return report
