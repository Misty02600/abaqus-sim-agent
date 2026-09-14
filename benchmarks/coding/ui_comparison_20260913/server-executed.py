"""三种前端共用的隔离宿主夹具；不提供产品Web能力。

Note:
    真实CodingRunService/Harness/问答/verifier配确定性FunctionModel。
    所有运行预算为0，runner拒绝任何进程；HTTP命令和AG-UI命令使用同一调度函数。
    注册、视图投影与命令回执仅覆盖本次夹具，不代替正式产品实现。
"""

from __future__ import annotations

import asyncio
import ctypes
import hashlib
import json
import mimetypes
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

PROJECT = Path(__file__).resolve().parents[2]
ROOT = PROJECT / "var/ui-comparison-20260913"
sys.path[:0] = [str(ROOT / "python-deps"), str(PROJECT / "src")]
mimetypes.init()
mimetypes.add_type("text/javascript", ".js")
mimetypes.add_type("text/javascript", ".mjs")
mimetypes.add_type("text/css", ".css")

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic_ai.messages import ModelResponse, ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import FunctionModel

from abaqus_sim_agent.coding.agent import create_abaqus_coding_agent
from abaqus_sim_agent.coding.clarification import ScriptedClarificationProvider
from abaqus_sim_agent.coding.contracts import CodingRunBudget, CodingRunRequest, RuntimeProfile
from abaqus_sim_agent.coding.dependencies import CodingAgentDependencies
from abaqus_sim_agent.coding.execution_verifier import ExecutionEvidenceVerifier
from abaqus_sim_agent.coding.launch import LaunchManifest
from abaqus_sim_agent.coding.model import DeepSeekGateway, new_run_execution_config
from abaqus_sim_agent.coding.persistence import write_text_atomic
from abaqus_sim_agent.coding.resume import create_step_store, load_effective_run_for_resume
from abaqus_sim_agent.coding.service import CodingRunService
from abaqus_sim_agent.coding.user_questions import UserAnswerInput, WaitingForUser, submit_answer
from abaqus_sim_agent.coding.workspace import WorkspaceResolver
from abaqus_sim_agent.execution import Abaqus2024Runner
from abaqus_sim_agent.skills import skill_library_path

POOL = ThreadPoolExecutor(max_workers=1, thread_name_prefix="ui-comparison")
COMMAND_LOCK = threading.Lock()
LOG_LOCK = threading.Lock()
FUTURES = {}
VIEW_LOCK = threading.RLock()
ACTIVE = set()
CACHED = {}


def save(path, value):
    write_text_atomic(path, json.dumps(value, ensure_ascii=False, indent=2))


def read(path, default=None):
    if os.name != "nt":
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default
    # Windows普通CRT读取可能阻止宿主原子replace；观察者允许共享删除，不持有产品写锁。
    import msvcrt
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    create = kernel.CreateFileW
    create.argtypes = [ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p]
    create.restype = ctypes.c_void_p
    handle = create(str(path), 0x80000000, 7, None, 3, 0x80, None)
    if handle == ctypes.c_void_p(-1).value:
        error = ctypes.get_last_error()
        if error in (2, 3):
            return default
        raise ctypes.WinError(error)
    try:
        fd = msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY)
    except BaseException:
        close = kernel.CloseHandle
        close.argtypes = [ctypes.c_void_p]
        close(handle)
        raise
    with os.fdopen(fd, "r", encoding="utf-8") as stream:
        return json.load(stream)


def log(kind, **data):
    with LOG_LOCK:
        with (ROOT / "requests.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps({"time": datetime.now(UTC).isoformat(), "kind": kind, **data}, ensure_ascii=False) + "\n")


def directory(run_id):
    if not run_id.startswith("cmp-") or any(c not in "cmp-0123456789abcdef" for c in run_id):
        raise HTTPException(404)
    path = ROOT / "host-runs" / run_id
    if not (path / "meta.json").exists():
        raise HTTPException(404)
    return path


class NoProcess:
    async def run(self, *args, **kwargs):
        raise AssertionError("UI comparison cannot execute any process")


def call(name, args, call_id):
    return ModelResponse(parts=[ToolCallPart(name, args, tool_call_id=call_id)])


def make_service(base):
    meta = read(base / "meta.json")
    state = base / "state"
    resume = (state / "request.json").exists()
    if resume:
        effective = load_effective_run_for_resume(state)
        request, initial = effective.request, effective.initial_snapshot
    else:
        workspace = base / "workspace"
        workspace.mkdir(exist_ok=True)
        state.mkdir(exist_ok=True)
        request = CodingRunRequest(
            run_id=meta["run_id"], goal=meta["goal"], workspace=workspace.resolve(), state_dir=state.resolve(),
            runtime=RuntimeProfile(launcher_path=(state / "never-run.bat").resolve()),
            budget=CodingRunBudget(max_model_requests=8, max_abaqus_calls=0, max_solver_calls=0, wall_time_seconds=60.0),
        )
        initial = WorkspaceResolver().snapshot(workspace)
    config = new_run_execution_config(model_provider=DeepSeekGateway.OPENCODE_GO, model_name="deepseek-v4-flash")
    launch = LaunchManifest(schema_version="coding-run-launch-v4", question_protocol="ask_user_v1", skill_library=skill_library_path().resolve(), **config.model_dump())

    def reply(messages, info):
        returns = [p for m in messages for p in m.parts if isinstance(p, ToolReturnPart)]
        definitions = [{"name": t.name, "schema": t.parameters_json_schema} for t in info.function_tools]
        save(base / "tool-schema.json", definitions)
        log("model_fixture", run_id=request.run_id, tools=[d["name"] for d in definitions])
        if not any(p.tool_name == "ask_user" for p in returns):
            return call("ask_user", {"question": "Plate thickness? Please include units."}, request.run_id + "-q1")
        if not any(p.tool_name == "write_file" for p in returns):
            answer = next(p.content for p in returns if p.tool_name == "ask_user")
            if "SLOW" in meta["goal"]:
                time.sleep(8)
            return call("write_file", {"path": "out.txt", "content": f"UI fixture answer: {answer}\n"}, request.run_id + "-w1")
        ready = next(t.name for t in info.output_tools if t.name.endswith("ReadyForVerificationDecision"))
        return call(ready, {"status": "ready_for_verification", "summary": "MODEL_SAYS_COMPLETE", "deliverables": [{"relative_path": "missing.txt" if "FAIL" in meta["goal"] else "out.txt"}]}, request.run_id + "-ready")

    store = create_step_store(state)
    agent = create_abaqus_coding_agent(FunctionModel(reply, model_name="ui-comparison"), request, step_store=store, execution_config=config, question_protocol="ask_user_v1")
    runner = Abaqus2024Runner(request.workspace, request.state_dir, process_strategy=NoProcess())
    return CodingRunService(
        agent=agent, request=request, dependencies=CodingAgentDependencies(request=request, runner=runner, step_store=store),
        clarification_provider=ScriptedClarificationProvider({}), verifier=ExecutionEvidenceVerifier(), initial_snapshot=initial,
        step_store=store, question_protocol="ask_user_v1", launch_manifest_json=None if resume else launch.model_dump_json(),
        resume=resume, expected_amendment_sequence=effective.amendment_sequence if resume else None,
    )


def worker(base, action, payload):
    async def execute():
        if action == "answer":
            await submit_answer(base / "state", UserAnswerInput(question_id=payload["question_id"], answer=payload["answer"]))
        result = await make_service(base).run_step()
        if not isinstance(result, WaitingForUser):
            save(base / "result.json", {"outcome": result.outcome.model_dump(mode="json"), "verification": result.verification.model_dump(mode="json") if result.verification else None})
        return result
    try:
        result = asyncio.run(execute())
        log("settled", run_id=base.name, status="waiting_for_user" if isinstance(result, WaitingForUser) else result.outcome.status.value)
    except BaseException as exc:
        save(base / "error.json", {"type": type(exc).__name__, "message": str(exc)})
        log("worker_error", run_id=base.name, error=repr(exc))
    finally:
        with VIEW_LOCK:
            meta = read(base / "meta.json")
            meta["active"] = False
            save(base / "meta.json", meta)
            ACTIVE.discard(base.name)
            CACHED.pop(base.name, None)


def projections(meta, question, result, has_file):
    run_id = meta["run_id"]
    vercel, aui, agui = [], [], []

    def text(role, value, identity):
        vercel.append({"id": identity, "role": role, "parts": [{"type": "text", "text": value}]})
        aui.append({"id": identity, "role": role, "content": [{"type": "text", "text": value}]})
        agui.append({"id": identity, "role": role, "content": value})

    def tool(name, args, identity, value=None):
        part = {"type": "tool-" + name, "toolCallId": identity, "state": "input-available" if value is None else "output-available", "input": args}
        content = {"type": "tool-call", "toolCallId": identity, "toolName": name, "args": args, "argsText": json.dumps(args)}
        if value is not None:
            part["output"] = value
            content["result"] = value
        vercel.append({"id": identity + "-message", "role": "assistant", "parts": [part]})
        aui.append({"id": identity + "-message", "role": "assistant", "content": [content]})
        agui.append({"id": identity + "-message", "role": "assistant", "content": "", "toolCalls": [{"id": identity, "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}]})
        if value is not None:
            agui.append({"id": identity + "-return", "role": "tool", "toolCallId": identity, "content": json.dumps(value, ensure_ascii=False)})

    text("user", meta["goal"], run_id + "-goal")
    if question:
        tool("ask_user", {"question": question["question"]}, question["question_id"], question["answer"])
        if question["answer"]:
            text("user", question["answer"], run_id + "-answer")
    if has_file:
        tool("write_file", {"path": "out.txt"}, run_id + "-w1", {"path": "out.txt", "written": True})
    if result:
        text("assistant", "MODEL_SAYS_COMPLETE", run_id + "-model-final")
    return {"vercel": vercel, "aui": aui, "agui": agui}


def snapshot(base):
    with VIEW_LOCK:
        if base.name in ACTIVE:
            out = json.loads(json.dumps(CACHED[base.name]))
            out["status"] = "running"
            out["phase"] = "host executing; last settled evidence shown"
            out["view_version"] = "running-" + out["view_version"]
            return out
        return settled_snapshot(base)


def settled_snapshot(base):
    meta = read(base / "meta.json")
    state = read(base / "state/resume_state.json", {})
    ledger = read(base / "state/user_questions.json", {"questions": []})
    result = read(base / "result.json")
    question = None
    if ledger["questions"]:
        q = ledger["questions"][-1]
        question = {"question_id": q["question_id"], "question": q["question"], "answer": q.get("answer"), "pending": not bool(q.get("resolved_at"))}
    future = FUTURES.get(base.name)
    active = bool(meta.get("active")) and future is not None and not future.done()
    status = "running" if active else ("interrupted" if meta.get("active") else state.get("status", "registered"))
    if result and not active:
        status = result["outcome"]["status"]
    if (base / "error.json").exists() and not active:
        status = "interrupted"
    out = {
        "run_id": base.name, "arm": meta["arm"], "goal": meta["goal"], "status": status,
        "task_revision": state.get("task_revision", 0), "phase": "host executing" if active else status,
        "question": question, "outcome": result["outcome"] if result else None,
        "verification": result["verification"] if result else None,
        "usage": result["outcome"]["usage"] if result else state.get("usage", {}),
        "messages": projections(meta, question, result, (base / "workspace/out.txt").exists()),
    }
    out["view_version"] = hashlib.sha256(json.dumps(out, sort_keys=True).encode()).hexdigest()[:16]
    return out


def dispatch(base, action, payload, transport):
    command_id = payload.get("command_id")
    if not isinstance(command_id, str) or not command_id:
        raise HTTPException(422, "command_id required")
    if action not in ("start", "answer"):
        raise HTTPException(409, "Unsupported action")
    digest = hashlib.sha256(json.dumps({"action": action, "payload": payload}, sort_keys=True).encode()).hexdigest()
    receipt = base / "receipts" / (hashlib.sha256(command_id.encode()).hexdigest() + ".json")
    with COMMAND_LOCK:
        prior = read(receipt)
        if prior:
            if prior["digest"] != digest:
                raise HTTPException(409, "Conflicting command payload")
            log("duplicate", run_id=base.name, action=action, transport=transport)
            return snapshot(base)
        current = snapshot(base)
        if action == "answer":
            q = current["question"]
            if not q or q["question_id"] != payload.get("question_id") or payload.get("task_revision") != current["task_revision"]:
                raise HTTPException(409, "Wrong question or revision")
            if not isinstance(payload.get("answer"), str) or not payload["answer"].strip():
                raise HTTPException(422, "Answer required")
            if not q["pending"]:
                if q["answer"] != payload["answer"]:
                    raise HTTPException(409, "Answer cannot be overwritten")
                return current
        required = "registered" if action == "start" else "waiting_for_user"
        if current["status"] != required:
            raise HTTPException(409, "Host state does not permit this command")
        if any(not f.done() for f in FUTURES.values()):
            raise HTTPException(409, "Another work segment is active")
        save(receipt, {"digest": digest, "action": action, "transport": transport})
        with VIEW_LOCK:
            CACHED[base.name] = current
            ACTIVE.add(base.name)
            meta = read(base / "meta.json")
            meta["active"] = True
            save(base / "meta.json", meta)
            FUTURES[base.name] = POOL.submit(worker, base, action, payload)
        log("command", run_id=base.name, action=action, transport=transport, payload=payload)
    return snapshot(base)


app = FastAPI(title="Isolated UI comparison — no physical execution")


@app.get("/api/runs")
def list_runs(arm: str):
    return {"runs": [snapshot(p.parent) for p in sorted((ROOT / "host-runs").glob("*/meta.json")) if read(p)["arm"] == arm]}


@app.post("/api/runs")
async def create_run(request: Request):
    body = await request.json()
    if set(body) != {"arm", "goal"} or body["arm"] not in ("official", "external", "agui") or not isinstance(body["goal"], str) or not body["goal"].strip():
        raise HTTPException(422)
    run_id = "cmp-" + uuid4().hex[:16]
    base = ROOT / "host-runs" / run_id
    base.mkdir(parents=True)
    save(base / "meta.json", {"run_id": run_id, **body, "active": False})
    log("create", run_id=run_id, arm=body["arm"], goal=body["goal"])
    return snapshot(base)


@app.get("/api/runs/{run_id}")
def get_run(run_id: str):
    return snapshot(directory(run_id))


@app.post("/api/runs/{run_id}/{action}", status_code=202)
async def command(run_id: str, action: str, request: Request):
    body = await request.json()
    allowed = {"command_id"} if action == "start" else {"command_id", "question_id", "task_revision", "answer"}
    if set(body) != allowed:
        raise HTTPException(422)
    return dispatch(directory(run_id), action, body, "rest")


@app.get("/api/runs/{run_id}/events")
async def events(run_id: str, request: Request):
    base = directory(run_id)
    async def generate():
        last = None
        log("subscribe", run_id=run_id)
        try:
            while not await request.is_disconnected():
                version = snapshot(base)["view_version"]
                if version != last:
                    yield "event: " + ("sync" if last is None else "run.changed") + "\ndata: " + json.dumps({"run_id": run_id}) + "\n\n"
                    last = version
                await asyncio.sleep(0.25)
        finally:
            log("unsubscribe", run_id=run_id)
    return StreamingResponse(generate(), media_type="text/event-stream")


@app.post("/api/agui/{run_id}")
async def agui(run_id: str, request: Request):
    base = directory(run_id)
    body = await request.json()
    if body.get("threadId") != run_id or body.get("tools"):
        raise HTTPException(409, "Host identity and tool boundary must be preserved")
    log("agui_input", run_id=run_id, body=body)
    if body.get("resume"):
        if len(body["resume"]) != 1:
            raise HTTPException(422)
        entry = body["resume"][0]
        if entry.get("status") != "resolved":
            raise HTTPException(409, "Question cancellation not supported")
        answer = entry.get("payload", {})
        payload = {"command_id": body["runId"], "question_id": entry["interruptId"], "answer": answer.get("answer"), "task_revision": answer.get("task_revision")}
        action = "answer"
    else:
        payload, action = {"command_id": body.get("runId")}, "start"
    dispatch(base, action, payload, "agui")

    async def generate():
        def encode(data):
            return "data: " + json.dumps(data, ensure_ascii=False) + "\n\n"
        yield encode({"type": "RUN_STARTED", "threadId": run_id, "runId": body["runId"]})
        last = None
        try:
            while True:
                current = snapshot(base)
                if last != current["view_version"]:
                    yield encode({"type": "MESSAGES_SNAPSHOT", "messages": current["messages"]["agui"]})
                    yield encode({"type": "STATE_SNAPSHOT", "snapshot": current})
                    last = current["view_version"]
                if current["status"] != "running":
                    q = current["question"]
                    outcome = {"type": "success"}
                    if current["status"] == "waiting_for_user" and q and q["pending"]:
                        outcome = {"type": "interrupt", "interrupts": [{"id": q["question_id"], "reason": "input_required", "toolCallId": q["question_id"], "message": q["question"], "responseSchema": {"type": "object", "properties": {"answer": {"type": "string"}, "task_revision": {"type": "integer"}}, "required": ["answer", "task_revision"]}}]}
                    yield encode({"type": "RUN_FINISHED", "threadId": run_id, "runId": body["runId"], "outcome": outcome})
                    break
                await asyncio.sleep(0.15)
        finally:
            log("agui_stream_closed", run_id=run_id)
    return StreamingResponse(generate(), media_type="text/event-stream")


app.mount("/official/assets", StaticFiles(directory=ROOT / "official/dist/assets", check_dir=False), name="official-assets")
app.mount("/aui-assets", StaticFiles(directory=ROOT / "aui/dist", check_dir=False), name="aui-assets")


@app.get("/favicon.ico/")
@app.get("/favicon.ico")
def favicon():
    return Response(status_code=204)


@app.get("/{arm}")
@app.get("/{arm}/{path:path}")
def page(arm: str, path: str = ""):
    folder = "official" if arm == "official" else "aui" if arm in ("external", "agui") else None
    if folder is None:
        raise HTTPException(404)
    target = ROOT / folder / "dist/index.html"
    if not target.exists():
        raise HTTPException(503, "Frontend build pending")
    return FileResponse(target)


if __name__ == "__main__":
    import uvicorn
    ROOT.mkdir(parents=True, exist_ok=True)
    uvicorn.run(app, host="127.0.0.1", port=38800, log_level="warning", access_log=False)
