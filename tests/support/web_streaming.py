"""跨线程控制的流式响应场景。"""

import asyncio
import threading
import time
from uuid import uuid4

import pytest
from pydantic import SecretStr
from pydantic_ai.models.function import DeltaThinkingPart, DeltaToolCall, FunctionModel

from abaqus_sim_agent.coding.contracts import CodingRunBudget
from abaqus_sim_agent.execution import Abaqus2024Runner
from abaqus_sim_agent.web.app import create_app
from abaqus_sim_agent.web.controller import ServerConfig
from tests.support.web import Client, NoProcess, api_catalog_dir


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
            **CodingRunBudget(
                max_model_requests=8, max_abaqus_calls=0, max_solver_calls=0
            ).as_request_controls(),
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
