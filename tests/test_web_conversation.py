import json
from uuid import uuid4

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient
from pydantic import SecretStr
from pydantic_ai import RequestUsage
from pydantic_ai.messages import (
    ModelResponse,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.models.function import FunctionModel

from abaqus_sim_agent.coding.contracts import CodingRunBudget
from abaqus_sim_agent.coding.model import DeepSeekGateway
from abaqus_sim_agent.coding.options import RunOptions
from abaqus_sim_agent.execution import Abaqus2024Runner
from abaqus_sim_agent.web.app import create_app
from abaqus_sim_agent.web.controller import ServerConfig
from tests.support.web import (
    Client,
    NoProcess,
    login,
    message_body,
    settled,
)


def conversation_app(
    tmp_path,
    *,
    failure=None,
    block=None,
    usage_tokens: int | None = None,
    context_warning_threshold_tokens: int | None = 100_000,
):
    observed = []

    def response(*, parts):
        return ModelResponse(
            parts=parts,
            usage=RequestUsage(input_tokens=usage_tokens or 0),
        )

    def factory(**kwargs):
        def model(messages, info):
            prompts = [
                (i, p.content)
                for i, m in enumerate(messages)
                for p in m.parts
                if isinstance(p, UserPromptPart)
            ]
            index, latest = prompts[-1]
            assert isinstance(latest, str)
            observed.append(messages)
            if block is not None and len(observed) == 2:
                assert block.wait(8)
            if failure and len(observed) == 2:
                raise ConnectionError("injected failure after new message saved")
            if "write one file" in latest:
                wrote = any(
                    isinstance(p, ToolReturnPart) and p.tool_name == "write_file"
                    for m in messages[index:]
                    for p in m.parts
                )
                if not wrote:
                    return response(
                        parts=[
                            ToolCallPart(
                                "write_file",
                                {"path": "one.txt", "content": "ALPHA"},
                                tool_call_id="write-one",
                            )
                        ]
                    )
                return response(
                    parts=[
                        ToolCallPart(
                            "final_result_ReadyForVerificationDecision",
                            {
                                "summary": "File delivered",
                                "deliverables": [{"relative_path": "one.txt"}],
                            },
                        )
                    ]
                )
            assert any("ALPHA" in str(text) for _, text in prompts), (
                "prior user context was lost"
            )
            reply = (
                "ALPHA is remembered"
                if len(prompts) == 1
                else "The earlier code is ALPHA"
            )
            return response(
                parts=[
                    ToolCallPart("final_result_ResponseDecision", {"summary": reply})
                ]
            )

        return FunctionModel(model), Client()

    config = ServerConfig(
        root=tmp_path / "web",
        overrides=RunOptions(),
        stream_model=False,
        context_warning_threshold_tokens=context_warning_threshold_tokens,
        **CodingRunBudget(
            max_model_requests=10, max_abaqus_calls=0, max_solver_calls=0
        ).as_request_controls(),
    )
    return create_app(
        config,
        api_keys={DeepSeekGateway.OFFICIAL: SecretStr("fixture")},
        model_factory=factory,
        runner_factory=lambda w, s, **kw: Abaqus2024Runner(
            w, s, process_strategy=NoProcess()
        ),
    ), observed


def test_snapshot_reports_latest_request_context_usage_and_advisory_threshold(tmp_path):
    app, _ = conversation_app(
        tmp_path, usage_tokens=4321, context_warning_threshold_tokens=4000
    )
    with TestClient(app, base_url="http://127.0.0.1") as client:
        login(client)
        _, snapshot = first_message(client)
        assert snapshot["context_usage"] == {
            "latest_input_tokens": 4321,
            "warning_threshold_tokens": 4000,
            "warning": True,
        }
        assert snapshot["view"]["latest_model_input_tokens"] == 4321


def first_message(client, goal="Remember ALPHA"):
    key = uuid4()
    workspace = (
        client.app.state.controller.registry.root.parent
        / "selected-workspaces"
        / key.hex
    )
    workspace.mkdir(parents=True, exist_ok=True)
    result = client.post(
        "/api/v1/runs",
        json={
            "idempotency_key": str(key),
            "goal": goal,
            "workspace": str(workspace),
        },
    )
    assert result.status_code == 201, result.text
    rid = result.json()["view"]["run_id"]
    assert (
        client.post(
            f"/api/v1/runs/{rid}/start", json={"command_id": str(uuid4())}
        ).status_code
        == 202
    )
    return rid, settled(client, rid, status="completed")


def test_three_turns_keep_context_order_budget_and_survive_restart(tmp_path):
    app, observed = conversation_app(tmp_path)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        login(client)
        rid, first = first_message(client)
        base = (tmp_path / "web/states" / rid / "request.json").read_bytes()
        assert first["can_message"]
        body = message_body(first, "What code did I give you?")
        sent = client.post(f"/api/v1/runs/{rid}/messages", json=body)
        assert sent.status_code == 202, sent.text
        assert sent.json()["message_saved"]
        second = settled(client, rid, status="completed")
        assert second["view"]["task_revision"] == 0
        assert second["view"]["amendment_sequence"] == 0
        assert second["view"]["usage"]["model_requests"] == 2
        assert second["title"] == "Remember ALPHA"
        assert [m["text"] for m in second["view"]["messages"]] == [
            "Remember ALPHA",
            "ALPHA is remembered",
            "What code did I give you?",
            "The earlier code is ALPHA",
        ]
        assert client.post(f"/api/v1/runs/{rid}/messages", json=body).json()["replayed"]
        assert len(observed) == 2
        assert (
            client.post(
                f"/api/v1/runs/{rid}/messages", json={**body, "text": "changed"}
            ).status_code
            == 409
        )
        assert (tmp_path / "web/states" / rid / "request.json").read_bytes() == base
    app2, later = conversation_app(tmp_path)
    with TestClient(app2, base_url="http://127.0.0.1") as client:
        login(client)
        restored = client.get(f"/api/v1/runs/{rid}").json()
        assert restored["view"] == second["view"]
        assert later == []
        third_text = "Explain that previous answer once more"
        assert (
            client.post(
                f"/api/v1/runs/{rid}/messages", json=message_body(restored, third_text)
            ).status_code
            == 202
        )
        third = settled(client, rid, status="completed")
        assert third["view"]["usage"]["model_requests"] == 3
        assert third["view"]["task_revision"] == 0
        assert third["view"]["amendment_sequence"] == 0
        assert [m["text"] for m in third["view"]["messages"]][-2:] == [
            third_text,
            "The earlier code is ALPHA",
        ]
        assert len(later) == 1
        assert not (tmp_path / "web/states" / rid / "amendments.json").exists()
        thread = json.loads(
            (tmp_path / "web/registry" / rid / "thread.json").read_bytes()
        )
        assert len(thread["messages"]) == 2
        assert [m["execution_task_revision"] for m in thread["messages"]] == [0, 0]
        assert [m["execution_amendment_sequence"] for m in thread["messages"]] == [0, 0]
        assert [m["execution_continuation_id"] for m in thread["messages"]] == [
            m["message_id"] for m in thread["messages"]
        ]
        invocation_files = sorted(
            (tmp_path / "web/registry" / rid / "invocations").glob("*.json")
        )
        invocations = [json.loads(path.read_bytes()) for path in invocation_files]
        assert len(invocations) == 3
        by_message = {
            item["source_message_id"]: item
            for item in invocations
            if item["source_message_id"] is not None
        }
        assert [m["execution_invocation_id"] for m in thread["messages"]] == [
            by_message[m["message_id"]]["invocation_id"] for m in thread["messages"]
        ]
        assert all(
            message["execution_invocation_id"] != message["message_id"]
            for message in thread["messages"]
        )


def test_new_message_survives_dispatch_failure_without_advancing_execution_boundary(
    tmp_path, monkeypatch
):
    app, _ = conversation_app(tmp_path)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        login(client)
        rid, first = first_message(client, "ALPHA: write one file")
        assert first["view"]["verification"]["passed"]
        submit = app.state.controller._pool.submit
        monkeypatch.setattr(
            app.state.controller._pool,
            "submit",
            lambda *args: (_ for _ in ()).throw(RuntimeError("dispatch unavailable")),
        )
        body = message_body(first, "Explain the existing file only")
        result = client.post(f"/api/v1/runs/{rid}/messages", json=body)
        assert result.status_code == 202, result.text
        assert result.json()["message_saved"]
        assert not result.json()["execution_accepted"]
        pending = client.get(f"/api/v1/runs/{rid}").json()
        assert pending["view"]["phase"] == "completed"
        assert pending["view"]["task_revision"] == first["view"]["task_revision"]
        assert (
            pending["view"]["amendment_sequence"] == first["view"]["amendment_sequence"]
        )
        assert pending["view"]["verification"] == first["view"]["verification"]
        assert pending["view"]["outcome"] == first["view"]["outcome"]
        assert pending["view"]["messages"][-1]["text"] == body["text"]
        assert pending["can_continue"]
        assert not pending["can_message"]
        thread = json.loads(
            (tmp_path / "web/registry" / rid / "thread.json").read_bytes()
        )
        assert thread["messages"][-1]["execution_task_revision"] is None
        monkeypatch.setattr(app.state.controller._pool, "submit", submit)
        assert (
            client.post(
                f"/api/v1/runs/{rid}/continue", json={"command_id": str(uuid4())}
            ).status_code
            == 202
        )
        done = settled(client, rid, status="completed")
        assert done["view"]["outcome"]["completion_kind"] == "response"
        assert done["view"]["task_revision"] == first["view"]["task_revision"]
        assert done["view"]["amendment_sequence"] == first["view"]["amendment_sequence"]
        assert done["view"]["verification"] is None
        assert done["view"]["files"][0]["fresh"] is False
        thread = json.loads(
            (tmp_path / "web/registry" / rid / "thread.json").read_bytes()
        )
        assert thread["messages"][-1]["execution_task_revision"] == 0
        assert thread["messages"][-1]["execution_amendment_sequence"] == 0
        assert (
            thread["messages"][-1]["execution_continuation_id"]
            == thread["messages"][-1]["message_id"]
        )


def test_receipt_loss_does_not_duplicate_saved_message(tmp_path):
    app, observed = conversation_app(tmp_path)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        login(client)
        rid, first = first_message(client)
        key = uuid4()
        body = message_body(first, "Repeat the code", key)
        client.post(f"/api/v1/runs/{rid}/messages", json=body)
        settled(client, rid, status="completed")
        (tmp_path / "web/registry" / rid / "commands" / f"{key.hex}.json").unlink()
        replay = client.post(f"/api/v1/runs/{rid}/messages", json=body)
        assert replay.status_code == 202, replay.text
        assert replay.json()["message_saved"]
        assert replay.json()["replayed"]
        assert len(observed) == 2
        snapshot = client.get(f"/api/v1/runs/{rid}").json()
        assert snapshot["view"]["task_revision"] == 0
        assert snapshot["view"]["amendment_sequence"] == 0


def test_terminal_continuation_recovers_missing_thread_link_without_reexecution(
    tmp_path, monkeypatch
):
    app, observed = conversation_app(tmp_path)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        login(client)
        rid, first = first_message(client)
        mark_execution = app.state.controller.threads.mark_execution

        def fail_link(*args, **kwargs):
            raise OSError("injected thread-link write failure")

        monkeypatch.setattr(app.state.controller.threads, "mark_execution", fail_link)
        body = message_body(first, "Explain the earlier code")
        assert client.post(f"/api/v1/runs/{rid}/messages", json=body).status_code == 202
        future = app.state.controller._future
        assert future is not None
        future.result(timeout=8)
        pending = client.get(f"/api/v1/runs/{rid}").json()
        assert pending["view"]["status"] == "completed"
        assert pending["can_continue"]
        thread = json.loads(
            (tmp_path / "web/registry" / rid / "thread.json").read_bytes()
        )
        assert thread["messages"][-1]["execution_task_revision"] is None
        assert len(observed) == 2
        invocations_before = sorted(
            (tmp_path / "web/registry" / rid / "invocations").glob("*.json")
        )
        assert len(invocations_before) == 2
        message_invocation = next(
            json.loads(path.read_bytes())
            for path in invocations_before
            if json.loads(path.read_bytes())["source_message_id"]
            == thread["messages"][-1]["message_id"]
        )
        assert message_invocation["status"] == "completed"

        monkeypatch.setattr(
            app.state.controller.threads, "mark_execution", mark_execution
        )
        assert (
            client.post(
                f"/api/v1/runs/{rid}/continue", json={"command_id": str(uuid4())}
            ).status_code
            == 202
        )
        future = app.state.controller._future
        assert future is not None
        future.result(timeout=8)
        recovered = client.get(f"/api/v1/runs/{rid}").json()
        assert recovered["view"]["status"] == "completed"
        assert not recovered["can_continue"]
        assert len(observed) == 2
        thread = json.loads(
            (tmp_path / "web/registry" / rid / "thread.json").read_bytes()
        )
        assert thread["messages"][-1]["execution_task_revision"] == 0
        assert thread["messages"][-1]["execution_amendment_sequence"] == 0
        assert (
            thread["messages"][-1]["execution_continuation_id"]
            == thread["messages"][-1]["message_id"]
        )
        assert (
            thread["messages"][-1]["execution_invocation_id"]
            == message_invocation["invocation_id"]
        )
        assert len(
            list((tmp_path / "web/registry" / rid / "invocations").glob("*.json"))
        ) == len(invocations_before)


def test_provider_failure_keeps_new_message_and_explicit_continue(tmp_path):
    app, _ = conversation_app(tmp_path, failure=True)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        login(client)
        rid, first = first_message(client)
        body = message_body(first, "Explain the earlier code")
        assert client.post(f"/api/v1/runs/{rid}/messages", json=body).json()[
            "message_saved"
        ]
        interrupted = settled(client, rid, status="interrupted")
        assert any(m["text"] == body["text"] for m in interrupted["view"]["messages"])
        thread = json.loads(
            (tmp_path / "web/registry" / rid / "thread.json").read_bytes()
        )
        assert thread["messages"][-1]["execution_task_revision"] is None
        invocations_before = [
            json.loads(path.read_bytes())
            for path in (tmp_path / "web/registry" / rid / "invocations").glob("*.json")
        ]
        interrupted_invocation = next(
            item
            for item in invocations_before
            if item["source_message_id"] == thread["messages"][-1]["message_id"]
        )
        assert interrupted_invocation["status"] == "interrupted"
        client.post(f"/api/v1/runs/{rid}/continue", json={"command_id": str(uuid4())})
        done = settled(client, rid, status="completed")
        assert done["view"]["task_revision"] == 0
        assert done["view"]["amendment_sequence"] == 0
        assert sum(m["text"] == body["text"] for m in done["view"]["messages"]) == 1
        thread = json.loads(
            (tmp_path / "web/registry" / rid / "thread.json").read_bytes()
        )
        assert thread["messages"][-1]["execution_task_revision"] == 0
        assert thread["messages"][-1]["execution_amendment_sequence"] == 0
        assert (
            thread["messages"][-1]["execution_continuation_id"]
            == thread["messages"][-1]["message_id"]
        )
        assert (
            thread["messages"][-1]["execution_invocation_id"]
            == interrupted_invocation["invocation_id"]
        )
        resumed_invocation = json.loads(
            (
                tmp_path
                / "web/registry"
                / rid
                / "invocations"
                / f"{interrupted_invocation['invocation_id']}.json"
            ).read_bytes()
        )
        assert resumed_invocation["status"] == "completed"
        assert resumed_invocation["resume_count"] == 1


def test_pending_question_and_reconcile_cannot_be_bypassed_with_message(tmp_path):
    from tests.support.web import configured_app, create, start

    app, observed = configured_app(tmp_path)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        login(client)
        rid = create(client)
        waiting = start(client, rid)
        assert (
            client.post(
                f"/api/v1/runs/{rid}/messages",
                json=message_body(waiting, "ignore the question"),
            ).status_code
            == 409
        )
        assert len(observed) == 1
        state_path = tmp_path / "web/states" / rid / "resume_state.json"
        state = json.loads(state_path.read_bytes())
        state["status"] = "needs_reconcile"
        state_path.write_text(json.dumps(state), encoding="utf-8")
        unavailable = client.get(f"/api/v1/runs/{rid}").json()
        assert not unavailable["can_message"]
        assert (
            client.post(
                f"/api/v1/runs/{rid}/messages",
                json=message_body(unavailable, "new goal"),
            ).status_code
            == 409
        )


def test_duplicate_send_while_reply_active_starts_one_segment(tmp_path):
    import threading

    gate = threading.Event()
    app, observed = conversation_app(tmp_path, block=gate)
    try:
        with TestClient(app, base_url="http://127.0.0.1") as client:
            login(client)
            rid, first = first_message(client)
            body = message_body(first, "Repeat ALPHA")
            assert client.post(f"/api/v1/runs/{rid}/messages", json=body).json()[
                "message_saved"
            ]
            assert client.get(f"/api/v1/runs/{rid}").json()["active"]
            duplicate = client.post(f"/api/v1/runs/{rid}/messages", json=body)
            assert duplicate.json()["replayed"]
            current = client.get(f"/api/v1/runs/{rid}").json()
            assert (
                client.post(
                    f"/api/v1/runs/{rid}/messages",
                    json=message_body(current, "Another message"),
                ).status_code
                == 409
            )
            gate.set()
            settled(client, rid, status="completed")
            assert len(observed) == 2
    finally:
        gate.set()


def test_saved_message_survives_ack_write_failure_without_auto_execution(
    tmp_path, monkeypatch
):
    from abaqus_sim_agent.web import controller

    app, observed = conversation_app(tmp_path)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        login(client)
        rid, first = first_message(client)
        key = uuid4()
        body = message_body(first, "Repeat the earlier code", key)
        save = controller.save_model

        def fail_ack(path, model):
            if path.name == f"{key.hex}.json" and getattr(
                model, "message_saved", False
            ):
                raise OSError("injected acknowledgement write failure")
            return save(path, model)

        monkeypatch.setattr(controller, "save_model", fail_ack)
        assert client.post(f"/api/v1/runs/{rid}/messages", json=body).status_code == 409
        pending = client.get(f"/api/v1/runs/{rid}").json()
        assert pending["view"]["messages"][-1]["text"] == body["text"]
        assert pending["can_continue"]
        assert len(observed) == 1
        retry = client.post(f"/api/v1/runs/{rid}/messages", json=body).json()
        assert retry["message_saved"]
        assert not retry["execution_accepted"]
        assert len(observed) == 1
        monkeypatch.setattr(controller, "save_model", save)
        client.post(f"/api/v1/runs/{rid}/continue", json={"command_id": str(uuid4())})
        settled(client, rid, status="completed")
        assert len(observed) == 2
