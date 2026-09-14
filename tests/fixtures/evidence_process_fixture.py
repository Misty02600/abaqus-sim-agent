"""Only used by process-boundary tests; never a production worker mode."""

import json
import os
import subprocess
import sys
import time
from pathlib import Path

from abaqus_sim_agent.evaluation.evidence_process import EvidenceProcess
from abaqus_sim_agent.evaluation.evidence_receipt import (
    WorkerReceipt,
    WorkerRequest,
    digest,
)
from abaqus_sim_agent.evaluation.runtime_evidence_service import worker_environment
from abaqus_sim_agent.retrieval.evidence_contracts import Unavailable

state = Path.cwd()
mode = sys.argv[1]
if mode == "parent":
    child_state = state / "child"
    child_state.mkdir()
    child = EvidenceProcess(
        [sys.executable, str(Path(__file__).resolve()), "tree"],
        cwd=child_state,
        env=worker_environment(child_state),
    )
    (state / "parent-ready.json").write_text(json.dumps({"pid": child.pid}))
    time.sleep(60)
elif mode == "tree":
    child = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)"],
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    (state / "tree.json").write_text(
        json.dumps({"pid": os.getpid(), "child": child.pid})
    )
    sys.stdout.buffer.write(b"stdout-before-timeout\xff\x00")
    sys.stdout.buffer.flush()
    sys.stderr.buffer.write(b"stderr-before-timeout\xfe\x00")
    sys.stderr.buffer.flush()
    time.sleep(60)
elif mode == "bad_json":
    (state / "receipt.json").write_bytes(b'{"protocol":')
elif mode == "missing":
    pass
else:
    request = WorkerRequest.model_validate_json((state / "request.json").read_bytes())
    result = Unavailable(search_id=request.search_id, reason_code="worker_failed")
    receipt = WorkerReceipt(
        request_sha256=digest(state / "request.json"), result=result, search_calls=0
    )
    raw = receipt.model_dump()
    if mode == "wrong_id":
        raw["result"]["search_id"] = "other"
    elif mode == "wrong_hash":
        raw["request_sha256"] = "0" * 64
    (state / "receipt.json").write_text(json.dumps(raw))
