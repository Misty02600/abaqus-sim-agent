import asyncio
import threading
import time
from uuid import uuid4

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient
from pydantic import SecretStr
from pydantic_ai.messages import ModelResponse, TextPart, ThinkingPart, ToolCallPart
from pydantic_ai.models.function import DeltaThinkingPart, DeltaToolCall, FunctionModel
from tests.test_web_conversation import message_body
from tests.test_web_workbench import (
    Client,
    NoProcess,
    api_catalog_dir,
    login,
    settled,
)

from abaqus_sim_agent.coding.contracts import CodingRunBudget
from abaqus_sim_agent.coding.response_stream import public_response_text
from abaqus_sim_agent.execution import Abaqus2024Runner
from abaqus_sim_agent.web.app import create_app
from abaqus_sim_agent.web.controller import ServerConfig


class Gate:
    def __init__(self):
        self._lock = threading.Lock()
        self._set = False
        self._loop = None
        self._event = None

    def set(self):
        with self._lock:
            self._set = True
            if self._event is not None and not self._loop.is_closed():
                self._loop.call_soon_threadsafe(self._event.set)

    async def wait(self):
        with self._lock:
            self._loop = asyncio.get_running_loop()
            self._event = asyncio.Event()
            if self._set:
                self._event.set()
        await asyncio.wait_for(self._event.wait(), timeout=4)


class Flow:
    def __init__(self):
        self.next = Gate()
        self.finish = Gate()
        self.closed = threading.Event()
        self.calls = []


def streaming_app(tmp_path, flow, *, before_header=False):
    def factory(**kwargs):
        async def stream(messages, info):
            flow.calls.append(messages)
            try:
                if len(flow.calls) > 1:
                    yield {
                        1: DeltaToolCall(
                            name="final_result_ResponseDecision",
                            json_args='{"summary":"继续后的回答。"}',
                            tool_call_id="reply",
                        )
                    }
                    return
                if before_header:
                    await flow.next.wait()
                yield {0: DeltaThinkingPart(content="PRIVATE_REASONING_DO_NOT_DISPLAY")}
                yield {
                    1: DeltaToolCall(
                        name="final_result_ResponseDecision",
                        json_args='{"summary":"第一段',
                        tool_call_id="reply",
                    )
                }
                await flow.next.wait()
                yield {1: DeltaToolCall(json_args="，第二段")}
                await flow.finish.wait()
                yield {1: DeltaToolCall(json_args='。"}')}
            finally:
                flow.closed.set()

        return FunctionModel(stream_function=stream), Client()

    app = create_app(
        ServerConfig(
            root=tmp_path / "web",
            api_catalog_dir=api_catalog_dir(tmp_path),
            budget=CodingRunBudget(
                max_model_requests=8, max_abaqus_calls=0, max_solver_calls=0
            ),
        ),
        api_key=SecretStr("fixture"),
        model_factory=factory,
        runner_factory=lambda w, s, **kw: Abaqus2024Runner(
            w, s, process_strategy=NoProcess()
        ),
    )
    return app


def launch(client):
    key = uuid4()
    workspace = (
        client.app.state.controller.registry.root.parent
        / "selected-workspaces"
        / key.hex
    )
    workspace.mkdir(parents=True, exist_ok=True)
    response = client.post(
        "/api/v1/runs",
        json={
            "idempotency_key": str(key),
            "goal": "请解释，不生成文件。",
            "workspace": str(workspace),
        },
    )
    assert response.status_code == 201, response.text
    rid = response.json()["view"]["run_id"]
    client.post(f"/api/v1/runs/{rid}/start", json={"command_id": str(uuid4())})
    return rid


def poll(client, rid, predicate):
    for _ in range(150):
        response = client.get(f"/api/v1/runs/{rid}")
        assert response.status_code == 200, response.text
        value = response.json()
        if predicate(value):
            return value
        time.sleep(0.025)
    pytest.fail(str(value))


def test_actual_incremental_summary_and_final_replacement(tmp_path):
    flow = Flow()
    app = streaming_app(tmp_path, flow)
    try:
        with TestClient(app, base_url="http://127.0.0.1") as client:
            login(client)
            rid = launch(client)
            first = poll(
                client,
                rid,
                lambda x: (
                    x["view"].get("live_reply", {})
                    and x["view"]["live_reply"]["text"] == "第一段"
                ),
            )
            assert first["can_stop"]
            assert "PRIVATE_REASONING" not in str(first)
            assert first["view"]["outcome"] is None
            flow.next.set()
            second = poll(
                client,
                rid,
                lambda x: (
                    x["view"].get("live_reply", {})
                    and x["view"]["live_reply"]["text"] == "第一段，第二段"
                ),
            )
            assert second["active"]
            flow.finish.set()
            done = settled(client, rid, status="completed")
            assert done["view"]["live_reply"] is None
            assert [
                m["text"] for m in done["view"]["messages"] if m["role"] == "assistant"
            ] == ["第一段，第二段。"]
            assert len(flow.calls) == 1
            assert flow.closed.is_set()
    finally:
        flow.next.set()
        flow.finish.set()


@pytest.mark.parametrize("before_header", [False, True])
def test_stop_closes_request_preserves_partial_and_allows_followup(
    tmp_path, before_header
):
    flow = Flow()
    app = streaming_app(tmp_path, flow, before_header=before_header)
    try:
        with TestClient(app, base_url="http://127.0.0.1") as client:
            login(client)
            rid = launch(client)
            active = poll(
                client,
                rid,
                lambda x: (
                    x["can_stop"]
                    and x["model_request_id"] is not None
                    and (
                        before_header
                        or bool(
                            x["view"].get("live_reply")
                            and x["view"]["live_reply"]["text"]
                        )
                    )
                ),
            )
            body = {
                "command_id": str(uuid4()),
                "expected_operation_id": active["active_operation_id"],
            }
            assert client.post(f"/api/v1/runs/{rid}/stop", json=body).status_code == 202
            stopped = settled(client, rid, status="interrupted")
            assert stopped["view"]["phase"] == "stopped"
            assert stopped["view"]["outcome"] is None
            assert stopped["can_message"]
            assert flow.closed.wait(2)
            assert "PRIVATE_REASONING" not in str(stopped)
            partials = [
                m for m in stopped["view"]["messages"] if m["status"] == "interrupted"
            ]
            assert len(partials) == 1
            assert partials[0]["text"] == ("" if before_header else "第一段")
            assert client.post(f"/api/v1/runs/{rid}/stop", json=body).json()["replayed"]
            assert len(flow.calls) == 1
            workspace = app.state.controller.registry.load(rid).request.workspace
            assert list(workspace.iterdir()) == []
        restored_app = streaming_app(tmp_path, flow)
        with TestClient(restored_app, base_url="http://127.0.0.1") as client:
            login(client)
            restored = client.get(f"/api/v1/runs/{rid}").json()
            assert restored["view"] == stopped["view"]
            sent = client.post(
                f"/api/v1/runs/{rid}/messages",
                json=message_body(restored, "继续解释。"),
            )
            assert sent.status_code == 202, sent.text
            done = settled(client, rid, status="completed")
            assert (
                sum(m["status"] == "interrupted" for m in done["view"]["messages"]) == 1
            )
            assert done["view"]["messages"][-1]["text"] == "继续后的回答。"
            assert len(flow.calls) == 2
    finally:
        flow.next.set()
        flow.finish.set()


def test_rename_while_streaming_is_cosmetic_versioned_and_persistent(tmp_path):
    flow = Flow()
    app = streaming_app(tmp_path, flow)
    try:
        with TestClient(app, base_url="http://127.0.0.1") as client:
            login(client)
            rid = launch(client)
            active = poll(client, rid, lambda x: x["can_stop"])
            base = (tmp_path / "web/states" / rid / "request.json").read_bytes()
            body = {
                "command_id": str(uuid4()),
                "title": "新的会话名称",
                "expected_title_version": active["title_version"],
            }
            renamed = client.post(f"/api/v1/runs/{rid}/title", json=body)
            assert renamed.status_code == 200, renamed.text
            assert renamed.json()["title"] == "新的会话名称"
            assert renamed.json()["title_version"] == 1
            assert renamed.json()["view"]["goal"] == active["view"]["goal"]
            assert (
                client.post(f"/api/v1/runs/{rid}/title", json=body).status_code == 200
            )
            assert (
                client.post(
                    f"/api/v1/runs/{rid}/title", json={**body, "title": "冲突"}
                ).status_code
                == 409
            )
            assert (
                client.post(
                    f"/api/v1/runs/{rid}/title",
                    json={**body, "command_id": str(uuid4())},
                ).status_code
                == 409
            )
            assert (tmp_path / "web/states" / rid / "request.json").read_bytes() == base
            assert len(flow.calls) == 1
            flow.next.set()
            flow.finish.set()
            settled(client, rid, status="completed")
        app2 = streaming_app(tmp_path, flow)
        with TestClient(app2, base_url="http://127.0.0.1") as client:
            login(client)
            assert client.get(f"/api/v1/runs/{rid}").json()["title"] == "新的会话名称"
            assert len(flow.calls) == 1
    finally:
        flow.next.set()
        flow.finish.set()


def test_public_projection_ignores_reasoning_and_ordinary_tool_arguments():
    response = ModelResponse(
        parts=[
            ThinkingPart("secret thinking"),
            ToolCallPart("write_file", {"path": "script.py", "content": "secret code"}),
            TextPart("公开前言"),
            ToolCallPart("final_result_ResponseDecision", '{"summary":"公开正文'),
        ]
    )
    assert public_response_text(response, partial=True) == "公开前言\n\n公开正文"
    assert public_response_text(response, partial=False) == "公开前言"


@pytest.mark.parametrize("stop_early", [False, True])
def test_go_http_streaming_adapter_retry_and_transport_close(
    tmp_path, monkeypatch, stop_early
):
    import json

    import httpx
    from openai import AsyncOpenAI

    from abaqus_sim_agent.coding.model import DeepSeekGateway, new_run_execution_config

    gate = Gate()
    closed = threading.Event()
    bodies = []
    headers = []

    class Wire(httpx.AsyncByteStream):
        async def __aiter__(self):
            def chunk(delta, finish=None):
                return (
                    "data: "
                    + json.dumps(
                        {
                            "id": "mock-chat",
                            "object": "chat.completion.chunk",
                            "created": 1,
                            "model": "deepseek-v4-flash",
                            "choices": [
                                {"index": 0, "delta": delta, "finish_reason": finish}
                            ],
                        }
                    )
                    + "\n\n"
                ).encode()

            yield chunk({"role": "assistant", "reasoning_content": "PRIVATE_REASONING"})
            yield chunk(
                {
                    "tool_calls": [
                        {
                            "index": 0,
                            "id": "answer",
                            "type": "function",
                            "function": {
                                "name": "final_result_ResponseDecision",
                                "arguments": '{"summary":"HTTP第一段',
                            },
                        }
                    ]
                }
            )
            await gate.wait()
            yield chunk(
                {"tool_calls": [{"index": 0, "function": {"arguments": '，完成。"}'}}]},
                "tool_calls",
            )
            yield b"data: [DONE]\n\n"

        async def aclose(self):
            closed.set()

    async def handler(request):
        bodies.append(json.loads(request.content))
        headers.append(request.headers)
        if len(bodies) == 1:
            return httpx.Response(
                503,
                headers={"retry-after-ms": "1"},
                json={"error": {"message": "injected retry"}},
            )
        return httpx.Response(
            200, headers={"content-type": "text/event-stream"}, stream=Wire()
        )

    def client_factory(**kwargs):
        return AsyncOpenAI(
            **kwargs,
            http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        )

    monkeypatch.setattr("abaqus_sim_agent.coding.model.AsyncOpenAI", client_factory)
    config = ServerConfig(
        root=tmp_path / "web",
        api_catalog_dir=api_catalog_dir(tmp_path),
        execution=new_run_execution_config(
            model_provider=DeepSeekGateway.OPENCODE_GO, model_name="deepseek-v4-flash"
        ),
        budget=CodingRunBudget(
            max_model_requests=8, max_abaqus_calls=0, max_solver_calls=0
        ),
    )
    app = create_app(
        config,
        api_key=SecretStr("fixture"),
        runner_factory=lambda w, s, **kw: Abaqus2024Runner(
            w, s, process_strategy=NoProcess()
        ),
    )
    try:
        with TestClient(app, base_url="http://127.0.0.1") as client:
            login(client)
            rid = launch(client)
            active = poll(
                client,
                rid,
                lambda x: bool(
                    x["view"].get("live_reply") and x["view"]["live_reply"]["text"]
                ),
            )
            assert active["view"]["live_reply"]["text"] == "HTTP第一段"
            assert "PRIVATE_REASONING" not in str(active)
            if stop_early:
                assert (
                    client.post(
                        f"/api/v1/runs/{rid}/stop",
                        json={
                            "command_id": str(uuid4()),
                            "expected_operation_id": active["active_operation_id"],
                        },
                    ).status_code
                    == 202
                )
                result = settled(client, rid, status="interrupted")
                assert result["view"]["phase"] == "stopped"
            else:
                gate.set()
                result = settled(client, rid, status="completed")
                assert result["view"]["messages"][-1]["text"] == "HTTP第一段，完成。"
            assert closed.wait(2)
            assert len(bodies) == 2
            assert bodies[0] == bodies[1]
            assert bodies[1]["stream"] is True
            assert bodies[1]["thinking"] == {"type": "enabled"}
            assert bodies[1]["reasoning_effort"] == "high"
            assert headers[0]["x-opencode-session"] == headers[1]["x-opencode-session"]
            assert rid not in headers[0]["x-opencode-session"]
    finally:
        gate.set()


def test_streamed_file_tool_runs_once_and_keeps_host_verification(tmp_path):
    calls = []

    def factory(**kwargs):
        async def stream(messages, info):
            from pydantic_ai.messages import ToolReturnPart

            calls.append(messages)
            wrote = any(
                isinstance(p, ToolReturnPart) and p.tool_name == "write_file"
                for m in messages
                for p in m.parts
            )
            if not wrote:
                yield {
                    0: DeltaToolCall(
                        name="write_file",
                        json_args='{"path":"result.py","content":"value = 1\\n"}',
                        tool_call_id="write",
                    )
                }
            else:
                yield {
                    0: DeltaToolCall(
                        name="final_result_ReadyForVerificationDecision",
                        json_args='{"summary":"已交付脚本","deliverables":[{"relative_path":"result.py"}]}',
                        tool_call_id="final",
                    )
                }

        return FunctionModel(stream_function=stream), Client()

    app = create_app(
        ServerConfig(
            root=tmp_path / "web",
            api_catalog_dir=api_catalog_dir(tmp_path),
            budget=CodingRunBudget(
                max_model_requests=5, max_abaqus_calls=0, max_solver_calls=0
            ),
        ),
        api_key=SecretStr("fixture"),
        model_factory=factory,
        runner_factory=lambda w, s, **kw: Abaqus2024Runner(
            w, s, process_strategy=NoProcess()
        ),
    )
    with TestClient(app, base_url="http://127.0.0.1") as client:
        login(client)
        rid = launch(client)
        done = settled(client, rid, status="completed")
        assert len(calls) == 2
        assert done["view"]["verification"]["passed"]
        assert done["view"]["outcome"]["verification_scope"] == "execution_evidence"
        assert done["view"]["usage"]["model_requests"] == 2
        workspace = app.state.controller.registry.load(rid).request.workspace
        assert (workspace / "result.py").read_text() == "value = 1\n"
