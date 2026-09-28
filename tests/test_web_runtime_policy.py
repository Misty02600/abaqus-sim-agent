import threading
import time
from uuid import uuid4

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient
from pydantic import SecretStr
from pydantic_ai.messages import ModelResponse, ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import FunctionModel

from abaqus_sim_agent.coding.amendments import load_amendment_ledger
from abaqus_sim_agent.coding.model import DeepSeekGateway
from abaqus_sim_agent.coding.preferences import RunPreferences
from abaqus_sim_agent.coding.resume import load_resume_state, write_resume_state
from abaqus_sim_agent.coding.run_lock import CodingRunFileLock
from abaqus_sim_agent.web.app import create_app
from abaqus_sim_agent.web.controller import ServerConfig
from tests.support.web import (
    Client,
    answer_body,
    api_catalog_dir,
    configured_app,
    create,
    login,
    settled,
    start,
)
from tests.support.web_solver import _DeferredSuccessfulSolver


def policy_body(
    snapshot,
    *,
    command_id=None,
    abaqus=None,
    solver=None,
    amendment_sequence=None,
    task_revision=None,
):
    return {
        "command_id": str(command_id or uuid4()),
        "expected_task_revision": snapshot["view"]["task_revision"]
        if task_revision is None
        else task_revision,
        "expected_amendment_sequence": snapshot["view"]["amendment_sequence"]
        if amendment_sequence is None
        else amendment_sequence,
        "abaqus": abaqus or snapshot["runtime_policy"]["abaqus"],
        "solver": solver or snapshot["runtime_policy"]["solver"],
    }


def update_policy(client, run_id, body):
    return client.post(f"/api/v1/runs/{run_id}/runtime-policy", json=body)


def test_waiting_question_keeps_question_and_applies_policy_on_answer(tmp_path):
    app, calls = configured_app(tmp_path)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        login(client)
        run_id = create(client)
        waiting = start(client, run_id)
        usage_before = waiting["view"]["usage"]
        command_id = uuid4()
        body = policy_body(
            waiting,
            command_id=command_id,
            solver={"mode": "allow_with_limit", "limit": 2},
        )

        response = update_policy(client, run_id, body)
        assert response.status_code == 200, response.text
        updated = response.json()
        assert updated["view"]["status"] == "waiting_for_user"
        assert updated["view"]["question"] == waiting["view"]["question"]
        assert updated["view"]["amendment_sequence"] == 1
        assert updated["view"]["task_revision"] == 0
        assert updated["view"]["usage"] == usage_before
        assert updated["can_answer"]
        assert updated["runtime_policy"]["solver"] == {
            "mode": "allow_with_limit",
            "limit": 2,
        }
        assert len(calls) == 1

        replay = update_policy(client, run_id, body)
        assert replay.status_code == 200
        ledger = load_amendment_ledger(
            app.state.controller.registry.state_dir(run_id), run_id=run_id
        )
        assert ledger.sequence == 1
        assert ledger.amendments[0].operation_id == command_id.hex
        conflict = update_policy(
            client,
            run_id,
            {**body, "solver": {"mode": "deny"}},
        )
        assert conflict.status_code == 409

        answer = client.post(
            f"/api/v1/runs/{run_id}/answers", json=answer_body(updated)
        )
        assert answer.status_code == 202, answer.text
        done = settled(client, run_id, status="completed")
        assert done["runtime_policy"]["solver"]["limit"] == 2
        assert done["view"]["question"]["answer"] == "1 mm"
        assert done["view"]["usage"]["model_requests"] > usage_before["model_requests"]

    restarted, _ = configured_app(tmp_path)
    with TestClient(restarted, base_url="http://127.0.0.1") as client:
        login(client)
        restored = client.get(f"/api/v1/runs/{run_id}").json()
        assert restored["runtime_policy"]["solver"]["limit"] == 2
        assert restored["view"]["usage"] == done["view"]["usage"]


def test_user_can_approve_solver_while_waiting_then_resume_same_run(tmp_path):
    release = threading.Event()
    release.set()
    runners = []

    def model_factory(**kwargs):
        def model(messages, info):
            returns = [
                part
                for message in messages
                for part in message.parts
                if isinstance(part, ToolReturnPart)
            ]
            if not any(part.tool_name == "ask_user" for part in returns):
                return ModelResponse(
                    parts=[
                        ToolCallPart(
                            "ask_user",
                            {"question": "Approve the prepared Solver job?"},
                            tool_call_id="approve-solver",
                        )
                    ]
                )
            solver_returns = [
                part for part in returns if part.tool_name == "solver_job"
            ]
            if not solver_returns:
                return ModelResponse(
                    parts=[
                        ToolCallPart(
                            "solver_job",
                            {
                                "request": {
                                    "action": "submit",
                                    "job_name": "demo",
                                    "input_file": "demo.inp",
                                }
                            },
                            tool_call_id="solver-submit",
                        )
                    ]
                )
            latest = solver_returns[-1]
            assert isinstance(latest.content, dict)
            if latest.content.get("submitted") is True:
                return ModelResponse(
                    parts=[
                        ToolCallPart(
                            "solver_job",
                            {
                                "request": {
                                    "action": "wait",
                                    "execution_id": latest.content["execution_id"],
                                }
                            },
                            tool_call_id="solver-wait",
                        )
                    ]
                )
            name = next(
                tool.name
                for tool in info.output_tools
                if tool.name.endswith("ReadyForVerificationDecision")
            )
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        name,
                        {
                            "deliverables": [{"relative_path": "demo.odb"}],
                            "summary": "Approved Solver job completed.",
                        },
                        tool_call_id="final",
                    )
                ]
            )

        return FunctionModel(model), Client()

    def runner_factory(workspace, state, **kwargs):
        runner = _DeferredSuccessfulSolver(tmp_path, workspace, release)
        runners.append(runner)
        return runner

    app = create_app(
        ServerConfig(
            root=tmp_path / "web",
            overrides=RunPreferences(api_catalog_dir=api_catalog_dir(tmp_path)),
            stream_model=False,
        ),
        api_keys={DeepSeekGateway.OFFICIAL: SecretStr("fixture")},
        model_factory=model_factory,  # type: ignore[arg-type] - deterministic client fixture
        runner_factory=runner_factory,  # type: ignore[arg-type] - deterministic runner fixture
    )
    with TestClient(app, base_url="http://127.0.0.1") as client:
        login(client)
        run_id = create(client)
        registration = app.state.controller.registry.load(run_id)
        (registration.request.workspace / "demo.inp").write_text(
            "*HEADING\nfixture\n", encoding="ascii"
        )
        waiting = start(client, run_id)
        assert waiting["runtime_policy"]["solver"] == {"mode": "deny"}
        changed = update_policy(
            client,
            run_id,
            policy_body(
                waiting,
                solver={"mode": "allow_with_limit", "limit": 1},
            ),
        )
        assert changed.status_code == 200, changed.text
        approved = changed.json()
        assert approved["view"]["question"] == waiting["view"]["question"]
        answer = client.post(
            f"/api/v1/runs/{run_id}/answers",
            json={**answer_body(approved), "answer": "Approved"},
        )
        assert answer.status_code == 202, answer.text
        done = settled(client, run_id, status="completed")
        assert done["view"]["usage"]["solver_calls"] == 1
        assert done["runtime_policy"]["solver"] == {
            "mode": "allow_with_limit",
            "limit": 1,
        }
        assert len(runners) == 2
        assert sum(runner.calls for runner in runners) == 1


def test_terminal_policy_change_does_not_run_model_or_reset_usage(tmp_path):
    app, calls = configured_app(tmp_path)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        login(client)
        run_id = create(client)
        waiting = start(client, run_id)
        client.post(f"/api/v1/runs/{run_id}/answers", json=answer_body(waiting))
        done = settled(client, run_id, status="completed")
        calls_before = len(calls)
        usage_before = done["view"]["usage"]

        response = update_policy(
            client,
            run_id,
            policy_body(
                done,
                abaqus={"mode": "allow_with_limit", "limit": 5},
                solver={"mode": "allow_with_limit", "limit": 3},
            ),
        )
        assert response.status_code == 200, response.text
        updated = response.json()
        assert len(calls) == calls_before
        assert updated["view"]["usage"] == usage_before
        assert updated["view"]["task_revision"] == done["view"]["task_revision"]
        assert updated["view"]["amendment_sequence"] == 1
        assert updated["view"]["phase"] == "revision_pending"
        assert updated["can_continue"]
        assert updated["runtime_policy"]["abaqus"]["limit"] == 5
        assert updated["runtime_policy"]["solver"]["limit"] == 3


def test_waiting_policy_and_question_survive_service_restart(tmp_path):
    app, _ = configured_app(tmp_path)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        login(client)
        run_id = create(client)
        waiting = start(client, run_id)
        changed = update_policy(
            client,
            run_id,
            policy_body(waiting, solver={"mode": "allow"}),
        ).json()
        question = changed["view"]["question"]

    restarted, _ = configured_app(tmp_path)
    with TestClient(restarted, base_url="http://127.0.0.1") as client:
        login(client)
        restored = client.get(f"/api/v1/runs/{run_id}").json()
        assert restored["view"]["status"] == "waiting_for_user"
        assert restored["view"]["question"] == question
        assert restored["view"]["amendment_sequence"] == 1
        assert restored["runtime_policy"]["solver"] == {"mode": "allow"}
        assert restored["can_answer"]


def test_same_policy_is_noop_and_stale_snapshot_is_rejected(tmp_path):
    app, _ = configured_app(tmp_path)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        login(client)
        run_id = create(client)
        waiting = start(client, run_id)
        no_op_body = policy_body(waiting, command_id=uuid4())
        no_op = update_policy(client, run_id, no_op_body)
        assert no_op.status_code == 200
        assert no_op.json()["view"]["amendment_sequence"] == 0
        assert update_policy(client, run_id, no_op_body).status_code == 200
        state = app.state.controller.registry.state_dir(run_id)
        assert load_amendment_ledger(state, run_id=run_id).sequence == 0

        changed = update_policy(
            client,
            run_id,
            policy_body(waiting, solver={"mode": "allow"}),
        )
        assert changed.status_code == 200
        assert (
            update_policy(
                client,
                run_id,
                policy_body(
                    waiting,
                    solver={"mode": "deny"},
                    amendment_sequence=0,
                ),
            ).status_code
            == 409
        )
        assert (
            update_policy(
                client,
                run_id,
                policy_body(
                    changed.json(),
                    task_revision=1,
                ),
            ).status_code
            == 409
        )


def test_policy_limit_is_total_allowance_and_usage_is_not_reset(tmp_path):
    app, _ = configured_app(tmp_path)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        login(client)
        run_id = create(client)
        start(client, run_id)
        state_dir = app.state.controller.registry.state_dir(run_id)
        state = load_resume_state(state_dir)
        write_resume_state(
            state_dir,
            state.model_copy(
                update={
                    "dependencies": state.dependencies.model_copy(
                        update={"abaqus_calls": 2, "solver_calls": 2}
                    )
                }
            ),
        )
        current = client.get(f"/api/v1/runs/{run_id}").json()
        assert current["view"]["usage"]["solver_calls"] == 2

        updated = update_policy(
            client,
            run_id,
            policy_body(
                current,
                abaqus={"mode": "allow_with_limit", "limit": 4},
                solver={"mode": "allow_with_limit", "limit": 3},
            ),
        ).json()
        assert updated["view"]["usage"]["abaqus_calls"] == 2
        assert updated["view"]["usage"]["solver_calls"] == 2
        assert updated["runtime_policy"]["solver"]["limit"] == 3


def test_policy_update_is_blocked_before_start_during_run_and_reconcile(tmp_path):
    block = threading.Event()
    app, _ = configured_app(tmp_path, block=block)
    try:
        with TestClient(app, base_url="http://127.0.0.1") as client:
            login(client)
            run_id = create(client)
            registered = client.get(f"/api/v1/runs/{run_id}").json()
            assert not registered["can_update_runtime_policy"]
            assert (
                update_policy(
                    client,
                    run_id,
                    policy_body(registered, solver={"mode": "allow"}),
                ).status_code
                == 409
            )

            start_response = client.post(
                f"/api/v1/runs/{run_id}/start",
                json={"command_id": str(uuid4())},
            )
            assert start_response.status_code == 202
            active = None
            for _ in range(100):
                candidate = client.get(f"/api/v1/runs/{run_id}").json()
                if candidate["active"]:
                    active = candidate
                    break
                time.sleep(0.02)
            assert active is not None
            assert not active["can_update_runtime_policy"]
            assert (
                update_policy(
                    client,
                    run_id,
                    policy_body(active, solver={"mode": "allow"}),
                ).status_code
                == 409
            )
            block.set()
            waiting = settled(client, run_id, status="waiting_for_user")
            state_dir = app.state.controller.registry.state_dir(run_id)
            with CodingRunFileLock(state_dir):
                assert (
                    update_policy(
                        client,
                        run_id,
                        policy_body(waiting, solver={"mode": "allow"}),
                    ).status_code
                    == 409
                )
            state = load_resume_state(state_dir)
            write_resume_state(
                state_dir, state.model_copy(update={"status": "needs_reconcile"})
            )
            reconcile = client.get(f"/api/v1/runs/{run_id}").json()
            assert not reconcile["can_update_runtime_policy"]
            assert (
                update_policy(
                    client,
                    run_id,
                    policy_body(reconcile, solver={"mode": "allow"}),
                ).status_code
                == 409
            )
            assert load_amendment_ledger(state_dir, run_id=run_id).sequence == 0
            assert waiting["view"]["usage"]["model_requests"] == 1
    finally:
        block.set()


def test_policy_update_requires_auth_and_csrf(tmp_path):
    app, _ = configured_app(tmp_path)
    run_id = "web-" + uuid4().hex
    body = {
        "command_id": str(uuid4()),
        "expected_task_revision": 0,
        "expected_amendment_sequence": 0,
        "abaqus": {"mode": "allow"},
        "solver": {"mode": "deny"},
    }
    with TestClient(app, base_url="http://127.0.0.1") as client:
        assert update_policy(client, run_id, body).status_code == 401
        login(client)
        assert (
            client.post(
                f"/api/v1/runs/{run_id}/runtime-policy",
                json=body,
                headers={"x-csrf-token": "wrong"},
            ).status_code
            == 403
        )
