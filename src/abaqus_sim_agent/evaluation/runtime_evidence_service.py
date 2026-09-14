"""显式本地RAG适配器；未注册到Coder，不改变旧工具、默认模式或预算。"""

import asyncio
import hashlib
import math
import os
import shutil
import sys
import tempfile
import time
import traceback
from pathlib import Path

from pydantic import TypeAdapter, ValidationError

from abaqus_sim_agent.retrieval.evidence_contracts import (
    EvidenceResult,
    Rejected,
    SearchId,
    SearchRequest,
    Unavailable,
)

from .evidence_process import EvidenceProcess
from .evidence_receipt import (
    ModelFilesUnavailable,
    RuntimePaths,
    WorkerReceipt,
    WorkerRequest,
    canonical,
    capture_snapshot,
    check_deadline,
    digest,
    read,
    validate_evidence,
    validate_raw_receipt,
    verify_snapshot,
)


def worker_environment(state: Path) -> dict[str, str]:
    env = {
        k: os.environ[k]
        for k in ("SystemRoot", "WINDIR", "USERPROFILE")
        if k in os.environ
    }
    env.update(
        {
            "TEMP": str(state),
            "TMP": str(state),
            "PATH": str(Path(sys.executable).parent)
            + os.pathsep
            + str(Path(os.environ.get("SystemRoot", "C:/Windows")) / "System32"),
            "ANONYMIZED_TELEMETRY": "False",
            "HF_HUB_OFFLINE": "1",
            "TRANSFORMERS_OFFLINE": "1",
            "TOKENIZERS_PARALLELISM": "false",
        }
    )
    return env


def _cleanup_private_copies(state: Path) -> None:
    for name in ("private-candidate", "private-symbols"):
        private = state / name
        if private.exists():
            if (
                private.is_symlink()
                or private.is_junction()
                or private.resolve().parent != state.resolve()
            ):
                raise ValueError("unsafe private index cleanup target")
            shutil.rmtree(private)


class RuntimeEvidenceService:
    def __init__(self, paths: RuntimePaths, *, state_root: Path, workspace: Path):
        """创建只读查询适配器；宿主路径不属于模型输入。

        Args:
            paths: 明确选择的固定索引、范围及本地MiniLM模型。
            state_root: 应用宿主管理的日志根目录，必须在模型工作区之外。
            workspace: 用于排除state路径的模型工作区。

        Note:
            不预加载模型、不改索引；每次search启动一个最多60秒的worker。
            仅保证受信任本地组件的生命周期与交付完整性，不构成恶意租户隔离。
        """
        self.paths = RuntimePaths(
            **{key: path.resolve() for key, path in paths.model_dump().items()}
        )
        self.interpreter = str(Path(sys.executable).resolve())
        self.state_root = state_root.resolve()
        self.workspace = workspace.resolve()
        if self.state_root.is_relative_to(
            self.workspace
        ) or self.workspace.is_relative_to(self.state_root):
            raise ValueError("state and workspace must be disjoint")
        for path in paths.model_dump().values():
            resolved = path.resolve()
            if resolved.is_relative_to(self.workspace) or resolved.is_relative_to(
                self.state_root
            ):
                raise ValueError(
                    "read-only evidence inputs must be outside mutable workspace/state"
                )
        self.state_root.mkdir(parents=True, exist_ok=True)

    async def search(
        self, request: SearchRequest, *, search_id: str, deadline: float
    ) -> EvidenceResult:
        started = time.monotonic()
        TypeAdapter(SearchId).validate_python(search_id)
        if not math.isfinite(deadline):
            return Rejected(
                search_id=search_id,
                reason_code="invalid_request",
                message="A finite host monotonic deadline is required.",
            )
        deadline = min(deadline, started + 60.0)
        try:
            request = SearchRequest.model_validate(request)
        except ValidationError as exc:
            reason = (
                "unsupported_query_shape"
                if any(e["type"] == "union_tag_invalid" for e in exc.errors())
                else "invalid_request"
            )
            return Rejected(
                search_id=search_id,
                reason_code=reason,
                message="Request does not match the strict question/symbol contract.",
            )
        # mkdtemp does not interpolate search_id or query into a path.
        state = Path(tempfile.mkdtemp(prefix="evidence-", dir=self.state_root))
        process = None
        phase = "snapshot"
        snapshot_seconds = 0.0
        host_validation_seconds = 0.0
        try:
            check_deadline(deadline)
            snapshot, binding = capture_snapshot(self.paths, deadline)
            snapshot_seconds = time.monotonic() - started
            worker_request = WorkerRequest(
                search_id=search_id,
                request=request,
                paths=self.paths,
                snapshot=snapshot,
                binding=binding,
                deadline=deadline,
            )
            request_bytes = worker_request.model_dump_json().encode()
            (state / "request.json").write_bytes(request_bytes)
            command = [
                self.interpreter,
                "-I",
                "-B",
                "-u",
                "-m",
                "abaqus_sim_agent.evaluation.evidence_worker",
            ]
            process = EvidenceProcess(command, cwd=state, env=worker_environment(state))
            phase = "worker"
            (state / "process.json").write_text(
                canonical(
                    {"pid": process.pid, "search_id": search_id, "deadline": deadline}
                ),
                encoding="utf-8",
            )
            while process.poll() is None:
                check_deadline(deadline)
                await asyncio.sleep(min(0.025, max(0.0, deadline - time.monotonic())))
            if process.poll() != 0:
                raise RuntimeError("worker exited without a valid receipt")
            process.close()
            process = None
            validation_started = time.monotonic()
            phase = "receipt"
            check_deadline(deadline)
            receipt_path = state / "receipt.json"
            if receipt_path.stat().st_size > 1024 * 1024:
                raise ValueError("receipt exceeds host byte limit")
            receipt = WorkerReceipt.model_validate_json(receipt_path.read_bytes())
            if (
                receipt.protocol != "doc-evidence-worker-v1"
                or receipt.request_sha256 != hashlib.sha256(request_bytes).hexdigest()
                or receipt.result.search_id != search_id
            ):
                raise ValueError("receipt identity mismatch")
            result = receipt.result
            if result.status != "unavailable":
                if result.binding != binding or receipt.search_calls != 1:
                    raise ValueError("receipt binding/search count mismatch")
                if receipt.raw_result_sha256 != digest(state / "baseline-result.json"):
                    raise ValueError("raw baseline receipt mismatch")
                validate_raw_receipt(read(state / "baseline-result.json"), result)
                verify_snapshot(snapshot, deadline)
                validate_evidence(request, result, self.paths)
            check_deadline(deadline)
            host_validation_seconds = time.monotonic() - validation_started
        except Exception as exc:
            (state / "host-error.txt").write_text(
                traceback.format_exc(), encoding="utf-8"
            )
            if isinstance(exc, TimeoutError):
                reason = "worker_timeout"
            elif isinstance(exc, (ModelFilesUnavailable, ImportError)):
                reason = "model_unavailable"
            elif isinstance(exc, (ValueError, KeyError)) or (
                isinstance(exc, FileNotFoundError) and phase == "snapshot"
            ):
                reason = "integrity_mismatch"
            else:
                reason = "worker_failed"
            result = Unavailable(
                search_id=search_id,
                reason_code=reason,
                message="No validated evidence delivered. Host retained raw diagnostics; no retry or fallback was attempted.",
            )
        finally:
            if process is not None:
                process.close()
            # Also on caller cancellation; raw receipts and logs are never removed.
            _cleanup_private_copies(state)
        if time.monotonic() >= deadline and result.status != "unavailable":
            result = Unavailable(
                search_id=search_id,
                reason_code="worker_timeout",
                message="The end-to-end delivery deadline expired; no evidence delivered.",
            )
        (state / "result.json").write_text(result.model_dump_json(), encoding="utf-8")
        (state / "host-cost.json").write_text(
            canonical(
                {
                    "elapsed_seconds": time.monotonic() - started,
                    "deadline": deadline,
                    "snapshot_seconds": snapshot_seconds,
                    "host_validation_seconds": host_validation_seconds,
                }
            ),
            encoding="utf-8",
        )
        return result


def main():
    import argparse

    parser = argparse.ArgumentParser(
        description="Explicit offline evidence query; does not invoke Coder or Abaqus"
    )
    for name in (
        "candidate",
        "symbol-index",
        "scope-rules",
        "ranker-directory",
        "state-root",
        "workspace",
        "request",
    ):
        parser.add_argument("--" + name, required=True, type=Path)
    parser.add_argument("--search-id", required=True)
    args = parser.parse_args()
    paths = RuntimePaths(
        **{
            k: getattr(args, k).resolve()
            for k in ("candidate", "symbol_index", "scope_rules", "ranker_directory")
        }
    )
    service = RuntimeEvidenceService(
        paths, state_root=args.state_root, workspace=args.workspace
    )
    request = SearchRequest.model_validate_json(args.request.read_bytes())
    result = asyncio.run(
        service.search(
            request, search_id=args.search_id, deadline=time.monotonic() + 60
        )
    )
    print(result.model_dump_json(indent=2))


if __name__ == "__main__":
    main()
