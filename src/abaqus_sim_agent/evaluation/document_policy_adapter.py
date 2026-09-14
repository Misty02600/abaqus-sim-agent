"""真实证据端口与只读重验worker的宿主装配；不修改冻结检索算法。"""

import asyncio
import hashlib
import sys
import tempfile
import time
import traceback
from pathlib import Path
from typing import Literal

from abaqus_sim_agent.coding.document_policy_binding import (
    DocumentPolicyBinding,
    DocumentPolicySpec,
)
from abaqus_sim_agent.retrieval.evidence_contracts import (
    Binding,
    Contract,
    EvidenceResult,
    SearchRequest,
)

from .evidence_process import EvidenceProcess
from .evidence_receipt import (
    RuntimePaths,
    canonical,
    capture_snapshot,
    check_deadline,
    validate_evidence,
)
from .runtime_evidence_service import RuntimeEvidenceService, worker_environment


class ValidationRequest(Contract):
    protocol: Literal["document-policy-validation-v1"] = "document-policy-validation-v1"
    paths: RuntimePaths
    binding: Binding
    request: SearchRequest
    result: EvidenceResult
    deadline: float


class ValidationReceipt(Contract):
    protocol: Literal["document-policy-validation-v1"] = "document-policy-validation-v1"
    request_sha256: str
    validated: bool


def runtime_paths(spec: DocumentPolicySpec) -> RuntimePaths:
    return RuntimePaths(
        **{
            k: getattr(spec, k)
            for k in ("candidate", "symbol_index", "scope_rules", "ranker_directory")
        }
    )


def bind_document_policy(spec: DocumentPolicySpec) -> DocumentPolicyBinding:
    _, binding = capture_snapshot(runtime_paths(spec), time.monotonic() + 60)
    return DocumentPolicyBinding(spec=spec, evidence_binding=binding)


class RuntimeDocumentReceiptValidator:
    def __init__(self, paths: RuntimePaths, state_root: Path):
        self.paths, self.state_root = paths, state_root.resolve()

    async def validate(
        self,
        request: SearchRequest,
        result: EvidenceResult,
        *,
        binding: Binding,
        deadline: float,
    ) -> None:
        # Empty failures make no source claim. This must not silently retry a failed lookup.
        if result.status in {"unavailable", "rejected"}:
            if result.evidence:
                raise ValueError("failure receipt contains evidence")
            return
        deadline = min(deadline, time.monotonic() + 60)
        self.state_root.mkdir(parents=True, exist_ok=True)
        state = Path(tempfile.mkdtemp(prefix="validate-", dir=self.state_root))
        command = [
            sys.executable,
            "-I",
            "-B",
            "-u",
            "-m",
            "abaqus_sim_agent.evaluation.document_policy_adapter",
        ]
        payload = (
            ValidationRequest(
                paths=self.paths,
                binding=binding,
                request=request,
                result=result,
                deadline=deadline,
            )
            .model_dump_json()
            .encode()
        )
        (state / "request.json").write_bytes(payload)
        process = None
        try:
            check_deadline(deadline)
            process = EvidenceProcess(command, cwd=state, env=worker_environment(state))
            while process.poll() is None:
                check_deadline(deadline)
                await asyncio.sleep(0.025)
            if process.poll() != 0:
                raise ValueError("document evidence validation worker failed")
            raw = (state / "receipt.json").read_bytes()
            if len(raw) > 4096:
                raise ValueError("oversized validation receipt")
            receipt = ValidationReceipt.model_validate_json(raw)
            if (
                not receipt.validated
                or receipt.request_sha256 != hashlib.sha256(payload).hexdigest()
            ):
                raise ValueError(
                    "document evidence dependency/receipt validation failed"
                )
            check_deadline(deadline)
        finally:
            if process is not None:
                process.close()


class BoundRuntimeDocumentSearch:
    def __init__(self, paths: RuntimePaths, state_dir: Path, workspace: Path):
        self.paths, self.state_dir, self.workspace = paths, state_dir, workspace

    async def search(
        self, request: SearchRequest, *, search_id: str, deadline: float
    ) -> EvidenceResult:
        service = RuntimeEvidenceService(
            self.paths,
            state_root=self.state_dir / "document-evidence",
            workspace=self.workspace,
        )
        return await service.search(request, search_id=search_id, deadline=deadline)


def compose_document_backend(
    binding: DocumentPolicyBinding, *, state_dir: Path, workspace: Path
):
    paths = runtime_paths(binding.spec)
    port = BoundRuntimeDocumentSearch(paths, state_dir, workspace)
    validator = RuntimeDocumentReceiptValidator(
        paths, state_dir / "document-validation"
    )
    return port, validator


def main():
    if len(sys.argv) != 1:
        raise SystemExit("validation worker accepts no arguments")
    state = Path.cwd()
    raw = (state / "request.json").read_bytes()
    request = ValidationRequest.model_validate_json(raw)
    valid = False
    try:
        from .evidence_worker import _deny_network

        sys.addaudithook(_deny_network)
        check_deadline(request.deadline)
        _, actual = capture_snapshot(request.paths, request.deadline)
        if actual != request.binding or request.result.binding != request.binding:
            raise ValueError("document evidence binding changed")
        validate_evidence(request.request, request.result, request.paths)
        check_deadline(request.deadline)
        valid = True
    except Exception:
        traceback.print_exc()
    receipt = ValidationReceipt(
        request_sha256=hashlib.sha256(raw).hexdigest(), validated=valid
    )
    (state / "receipt.json").write_text(
        canonical(receipt.model_dump(mode="json")), encoding="utf-8"
    )


if __name__ == "__main__":
    main()
