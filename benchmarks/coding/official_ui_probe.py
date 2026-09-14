"""官方 ai-chat-ui 2.1.0 的隔离兼容实验，不是产品 Web 入口。

Note:
    使用真实 CodingRunService/Harness/问答/verifier 与确定性 FunctionModel。
    runner 拒绝所有进程调用；官方前端 bytes 由单独下载步骤保留原样。
    bridge 的首消息 ID 绑定只用于暴露兼容缺口，不是正式会话身份协议。
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sys
from pathlib import Path
from uuid import uuid4

PROJECT = Path(__file__).resolve().parents[2]
ROOT = PROJECT / "var/official-ui-probe-20260913"
sys.path[:0] = [str(ROOT / "python-deps"), str(PROJECT / "src")]

from pydantic_ai import Agent, CallDeferred, DeferredToolRequests
from pydantic_ai.messages import ModelResponse, ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import DeltaToolCall, FunctionModel
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, StreamingResponse
from starlette.routing import Route

from abaqus_sim_agent.coding.agent import create_abaqus_coding_agent
from abaqus_sim_agent.coding.clarification import ScriptedClarificationProvider
from abaqus_sim_agent.coding.contracts import CodingRunBudget, CodingRunRequest, RuntimeProfile
from abaqus_sim_agent.coding.dependencies import CodingAgentDependencies
from abaqus_sim_agent.coding.execution_verifier import ExecutionEvidenceVerifier
from abaqus_sim_agent.coding.launch import LaunchManifest
from abaqus_sim_agent.coding.model import DeepSeekGateway, new_run_execution_config
from abaqus_sim_agent.coding.resume import create_step_store, load_effective_run_for_resume
from abaqus_sim_agent.coding.service import CodingRunService
from abaqus_sim_agent.coding.user_questions import UserAnswerInput, WaitingForUser, load_questions, submit_answer
from abaqus_sim_agent.coding.workspace import WorkspaceResolver
from abaqus_sim_agent.execution import Abaqus2024Runner
from abaqus_sim_agent.skills import skill_library_path


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def append(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(value, ensure_ascii=False) + "\n")


class NoProcess:
    async def run(self, *args, **kwargs):
        raise AssertionError("This UI experiment must never launch Abaqus or another process")


def call(name, args, call_id):
    return ModelResponse(parts=[ToolCallPart(name, args, tool_call_id=call_id)])


def make_service(run_id, *, resume, goal):
    base = ROOT / "host-runs" / run_id
    state = base / "state"
    if resume:
        effective = load_effective_run_for_resume(state)
        request = effective.request
        initial = effective.initial_snapshot
    else:
        workspace = base / "workspace"
        workspace.mkdir(parents=True)
        state.mkdir()
        request = CodingRunRequest(
            run_id=run_id, goal=goal, workspace=workspace.resolve(), state_dir=state.resolve(),
            runtime=RuntimeProfile(launcher_path=(state / "never-run.bat").resolve()),
            budget=CodingRunBudget(max_model_requests=8, max_abaqus_calls=0, max_solver_calls=0, wall_time_seconds=60.0),
        )
        initial = WorkspaceResolver().snapshot(workspace)
    config = new_run_execution_config(
        model_provider=DeepSeekGateway.OPENCODE_GO, model_name="deepseek-v4-flash",
    )
    launch = LaunchManifest(
        schema_version="coding-run-launch-v4", question_protocol="ask_user_v1",
        skill_library=skill_library_path().resolve(), **config.model_dump(),
    )

    def reply(messages, info):
        parts = [part for message in messages for part in message.parts]
        returns = [p for p in parts if isinstance(p, ToolReturnPart)]
        if not any(p.tool_name == "ask_user" for p in returns):
            return call("ask_user", {"question": "Plate thickness? Please include units."}, "q-thickness")
        if not any(p.tool_name == "write_file" for p in returns):
            answer = next(p.content for p in returns if p.tool_name == "ask_user")
            return call("write_file", {"path": "out.txt", "content": f"UI fixture answer: {answer}\n"}, "w-fixture")
        ready_name = next(t.name for t in info.output_tools if t.name.endswith("ReadyForVerificationDecision"))
        if any(isinstance(p, ToolCallPart) and p.tool_name == ready_name for p in parts):
            failed_name = next(t.name for t in info.output_tools if t.name.endswith("FailedDecision"))
            return call(failed_name, {"status": "failed", "summary": "Host verifier rejected the missing deliverable.", "failure_stage": "verification"}, "f-failed")
        return call(ready_name, {
            "status": "ready_for_verification", "summary": "MODEL_SAYS_COMPLETE",
            "deliverables": [{"relative_path": "missing.txt" if "FAIL" in goal else "out.txt"}],
        }, "f-ready")

    store = create_step_store(state)
    agent = create_abaqus_coding_agent(
        FunctionModel(reply, model_name="ui-probe"), request, step_store=store,
        execution_config=config, question_protocol="ask_user_v1",
    )
    runner = Abaqus2024Runner(request.workspace, request.state_dir, process_strategy=NoProcess())
    return CodingRunService(
        agent=agent, request=request,
        dependencies=CodingAgentDependencies(request=request, runner=runner, step_store=store),
        clarification_provider=ScriptedClarificationProvider({}), verifier=ExecutionEvidenceVerifier(),
        initial_snapshot=initial, step_store=store, question_protocol="ask_user_v1",
        launch_manifest_json=None if resume else launch.model_dump_json(), resume=resume,
        expected_amendment_sequence=effective.amendment_sequence if resume else None,
    )


def step_in_thread(run_id, goal, answer=None):
    async def execute():
        state = ROOT / "host-runs" / run_id / "state"
        resume = (state / "request.json").exists()
        if answer is not None:
            ledger = load_questions(state, run_id)
            question = ledger.questions[-1]
            await submit_answer(state, UserAnswerInput(question_id=question.question_id, answer=answer))
        return await make_service(run_id, resume=resume, goal=goal).run_step()
    return asyncio.run(execute())


def text_chunks(value):
    identity = uuid4().hex
    return [
        {"type": "text-start", "id": identity},
        {"type": "text-delta", "id": identity, "delta": value},
        {"type": "text-end", "id": identity},
    ]


def stream(chunks):
    async def generate():
        for chunk in chunks:
            yield "data: " + json.dumps(chunk, ensure_ascii=False) + "\n\n"
        yield "data: [DONE]\n\n"
    return StreamingResponse(generate(), media_type="text/event-stream", headers={"x-vercel-ai-ui-message-stream": "v1", "Cache-Control": "no-cache"})


class Recorder:
    def __init__(self, app, mode):
        self.app = app
        self.mode = mode

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        incoming, outgoing = bytearray(), bytearray()
        code = None
        capture = scope["path"].endswith("/chat")

        async def read():
            message = await receive()
            if capture and message["type"] == "http.request":
                incoming.extend(message.get("body", b""))
            return message

        async def write(message):
            nonlocal code
            if message["type"] == "http.response.start":
                code = message["status"]
            if capture and message["type"] == "http.response.body":
                outgoing.extend(message.get("body", b""))
            await send(message)

        try:
            await self.app(scope, read, write)
        finally:
            append(ROOT / f"{self.mode}-http.jsonl", {
                "method": scope["method"], "path": scope["path"], "status": code,
                "referer": dict(scope["headers"]).get(b"referer", b"").decode(),
                "request": incoming.decode(errors="replace"), "response": outgoing.decode(errors="replace"),
            })


def native_app():
    async def fake_stream(messages, info):
        yield {0: DeltaToolCall(name="ask_user", json_args='{"question":"Plate thickness?"}', tool_call_id="native-question")}

    agent = Agent(FunctionModel(stream_function=fake_stream, model_name="ui-probe"), output_type=[str, DeferredToolRequests])

    @agent.tool_plain
    def ask_user(question: str) -> str:
        raise CallDeferred

    return Recorder(agent.to_web(html_source=ROOT / "official-2.1.0.html"), "native")


def bridge_app():
    async def configure(request):
        return JSONResponse({"models": [{"id": "function:ui-probe", "name": "Deterministic UI probe", "builtinTools": []}], "builtinTools": []})

    async def chat(request: Request):
        body = await request.json()
        users = [m for m in body["messages"] if m["role"] == "user"]
        first, last = users[0], users[-1]
        goal = "\n".join(p["text"] for p in first["parts"] if p["type"] == "text")
        answer = "\n".join(p["text"] for p in last["parts"] if p["type"] == "text")
        run_id = "ui-" + hashlib.sha256(first["id"].encode()).hexdigest()[:16]
        state = ROOT / "host-runs" / run_id / "state"
        start = {"type": "start", "messageId": uuid4().hex}
        if goal.startswith("FILES"):
            return stream([start,
                {"type": "file", "url": "/probe-image.png?kind=file", "mediaType": "image/png"},
                {"type": "data-host-state", "data": {"status": "DATA_CHUNK_VISIBLE_MARKER"}},
                *text_chunks("Native file and host data chunks precede this text.\n\n![Markdown fixture](/probe-image.png?kind=markdown)\n\n[Download fixture](/probe-image.png?kind=download)"),
                {"type": "finish", "finishReason": "stop"}])
        append(ROOT / "bridge-bindings.jsonl", {"chat_id": body.get("id"), "run_id": run_id, "first_user_id": first["id"], "last_user_id": last["id"], "user_count": len(users)})
        receipt = state.parent / "ui-receipts" / (hashlib.sha256(last["id"].encode()).hexdigest() + ".json")
        if receipt.exists():
            return stream(json.loads(receipt.read_text(encoding="utf-8")))
        if (state / "outcome.json").exists():
            return JSONResponse({"error": "Terminal host run cannot be restarted by chat retry"}, status_code=409)
        try:
            result = await asyncio.to_thread(step_in_thread, run_id, goal, answer if len(users) > 1 else None)
        except Exception as exc:
            append(ROOT / "bridge-errors.jsonl", {"run_id": run_id, "error": repr(exc)})
            raise
        if isinstance(result, WaitingForUser):
            payload = {"run_id": run_id, "status": "waiting_for_user", "question_id": result.question.question_id}
            chunks = [start,
                {"type": "tool-input-available", "toolCallId": result.question.question_id, "toolName": "ask_user", "input": {"question": result.question.question}},
                {"type": "data-host-state", "data": payload},
                *text_chunks(f"HOST WAITING — run {run_id}\n\n{result.question.question}\n\nFor this experiment, type the answer in the ordinary chat box."),
            ]
        else:
            payload = result.outcome.model_dump(mode="json")
            chunks = [start,
                {"type": "data-host-state", "data": payload},
                *text_chunks(f"HOST RESULT: {result.outcome.status.value}\n\nScope: {result.outcome.verification_scope}\n\nRun: {run_id}\n\n{result.outcome.summary}\n\nThis fixture does not perform physical validation."),
            ]
            save(state.parent / "probe-result.json", {"outcome": payload, "verification": result.verification.model_dump(mode="json") if result.verification else None})
        chunks.append({"type": "finish", "finishReason": "stop"})
        save(receipt, chunks)
        return stream(chunks)

    async def index(request):
        return FileResponse(ROOT / "official-2.1.0.html", media_type="text/html")

    async def image(request):
        return FileResponse(ROOT / "fixture.png", media_type="image/png")

    return Recorder(Starlette(routes=[
        Route('/api/configure', configure), Route('/api/chat', chat, methods=['POST']),
        Route('/probe-image.png', image), Route('/{path:path}', index),
    ]), "bridge")


if __name__ == "__main__":
    import uvicorn
    parser = argparse.ArgumentParser()
    parser.add_argument('--mode', choices=['native', 'bridge'], required=True)
    parser.add_argument('--port', type=int, required=True)
    args = parser.parse_args()
    uvicorn.run(native_app() if args.mode == 'native' else bridge_app(), host='127.0.0.1', port=args.port, log_level='warning', access_log=False)
