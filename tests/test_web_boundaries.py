from uuid import uuid4

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient
from PIL import Image
from tests.test_web_workbench import (
    answer_body,
    configured_app,
    create,
    login,
    settled,
    start,
)

from abaqus_sim_agent.coding.contracts import CodingRunRequest
from abaqus_sim_agent.web.contracts import CommandReceipt


def test_wrong_revision_and_immutable_request_are_rejected(tmp_path):
    app, calls = configured_app(tmp_path)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        login(client)
        rid = create(client)
        wait = start(client, rid)
        body = answer_body(wait)
        assert (
            client.post(
                f"/api/v1/runs/{rid}/answers",
                json={**body, "expected_task_revision": 1},
            ).status_code
            == 409
        )
        state = tmp_path / "web/states" / rid
        request = CodingRunRequest.model_validate_json(
            (state / "request.json").read_bytes()
        )
        (state / "request.json").write_text(
            request.model_copy(update={"goal": "tampered"}).model_dump_json(),
            encoding="utf-8",
        )
        assert (
            client.get(f"/api/v1/runs/{rid}").json()["view"]["status"] == "unavailable"
        )
        assert client.post(f"/api/v1/runs/{rid}/answers", json=body).status_code == 409
        assert len(calls) == 1


def test_uncertain_dispatch_does_not_replay_on_read_or_start(tmp_path):
    app, calls = configured_app(tmp_path)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        login(client)
        rid = create(client)
        receipt = CommandReceipt(
            command_id=uuid4(), digest="fixture", action="start", state="accepted"
        )
        commands = tmp_path / "web/registry" / rid / "commands"
        commands.mkdir()
        (commands / f"{receipt.command_id.hex}.json").write_text(
            receipt.model_dump_json(), encoding="utf-8"
        )
        snapshot = client.get(f"/api/v1/runs/{rid}").json()
        assert snapshot["command_uncertain"]
        assert not snapshot["can_start"]
        assert (
            client.post(
                f"/api/v1/runs/{rid}/start", json={"command_id": str(uuid4())}
            ).status_code
            == 409
        )
        assert not calls


def test_png_preview_html_download_and_symlink_rejection(tmp_path):
    app, _ = configured_app(tmp_path)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        login(client)
        rid = create(client)
        workspace = app.state.controller.registry.load(rid).request.workspace
        Image.new("RGB", (10, 10), "red").save(workspace / "image.png")
        (workspace / "bad.png").write_bytes(b"not png")
        (workspace / "page.html").write_text(
            "<script>alert(1)</script>", encoding="utf-8"
        )
        wait = start(client, rid)
        files = {f["relative_path"]: f for f in wait["view"]["files"]}

        def url(name, preview=True):
            return f"/api/v1/runs/{rid}/artifacts/{files[name]['id']}?preview={str(preview).lower()}"

        assert client.get(url("image.png")).headers["content-type"] == "image/png"
        assert client.get(url("bad.png")).status_code == 415
        assert client.get(url("page.html")).status_code == 415
        downloaded = client.get(url("page.html", False))
        assert downloaded.status_code == 200
        assert downloaded.headers["content-disposition"].startswith("attachment;")
        assert downloaded.headers["content-type"] == "application/octet-stream"
        image = workspace / "image.png"
        image.unlink()
        target = tmp_path / "outside.png"
        Image.new("RGB", (10, 10), "blue").save(target)
        try:
            image.symlink_to(target)
        except OSError:
            pytest.skip("symlink unavailable")
        assert client.get(url("image.png")).status_code == 400


def test_workspace_browse_search_preview_and_path_boundaries(tmp_path):
    app, _ = configured_app(tmp_path)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        login(client)
        rid = create(client)
        workspace = app.state.controller.registry.load(rid).request.workspace
        source = workspace / "src"
        source.mkdir()
        (source / "build_model.py").write_text("print('model')\n", encoding="utf-8")
        (workspace / "notes.txt").write_text("workspace note", encoding="utf-8")
        (workspace / "abaqus.rpy").write_bytes(
            "# -*- coding: mbcs -*-\r\n#: 模型已创建。\r\n".encode("cp936")
        )
        (workspace / "unsupported.txt").write_bytes(b"\x81")
        (workspace / "opaque.bin").write_bytes(b"\x00\x01")
        Image.new("RGB", (8, 8), "red").save(workspace / "view.png")

        root = client.get(f"/api/v1/runs/{rid}/workspace")
        assert root.status_code == 200, root.text
        assert [item["name"] for item in root.json()["items"]][:1] == ["src"]
        assert {item["name"] for item in root.json()["items"]} >= {
            "notes.txt",
            "opaque.bin",
            "view.png",
        }

        nested = client.get(
            f"/api/v1/runs/{rid}/workspace", params={"path": "src"}
        ).json()
        assert nested["path"] == "src"
        assert nested["items"] == [
            {
                "relative_path": "src/build_model.py",
                "name": "build_model.py",
                "kind": "file",
                "size_bytes": (source / "build_model.py").stat().st_size,
            }
        ]

        search = client.get(
            f"/api/v1/runs/{rid}/workspace/search", params={"q": "model"}
        )
        assert search.status_code == 200, search.text
        assert [item["relative_path"] for item in search.json()["items"]] == [
            "src/build_model.py"
        ]
        assert (
            client.get(
                f"/api/v1/runs/{rid}/workspace/file",
                params={"path": "src/build_model.py"},
            ).content
            == (source / "build_model.py").read_bytes()
        )
        replay = client.get(
            f"/api/v1/runs/{rid}/workspace/file", params={"path": "abaqus.rpy"}
        )
        assert replay.status_code == 200
        assert replay.headers["content-type"].startswith("text/plain; charset=utf-8")
        assert replay.content.decode("utf-8").endswith("#: 模型已创建。\r\n")
        assert (
            client.get(
                f"/api/v1/runs/{rid}/workspace/file",
                params={"path": "unsupported.txt"},
            ).status_code
            == 415
        )
        image = client.get(
            f"/api/v1/runs/{rid}/workspace/file", params={"path": "view.png"}
        )
        assert image.status_code == 200
        assert image.headers["content-type"] == "image/png"
        assert (
            client.get(
                f"/api/v1/runs/{rid}/workspace/file",
                params={"path": "opaque.bin"},
            ).status_code
            == 415
        )
        assert (
            client.get(
                f"/api/v1/runs/{rid}/workspace", params={"path": "../states"}
            ).status_code
            == 400
        )
        assert (
            client.get(
                f"/api/v1/runs/{rid}/workspace/file",
                params={"path": "C:/Windows/win.ini"},
            ).status_code
            == 400
        )

        target = tmp_path / "outside.txt"
        target.write_text("outside", encoding="utf-8")
        linked = workspace / "linked.txt"
        try:
            linked.symlink_to(target)
        except OSError:
            return
        names = {
            item["name"]
            for item in client.get(f"/api/v1/runs/{rid}/workspace").json()["items"]
        }
        assert "linked.txt" not in names
        assert (
            client.get(
                f"/api/v1/runs/{rid}/workspace/file", params={"path": "linked.txt"}
            ).status_code
            == 400
        )


def test_empty_lock_is_not_repaired_by_get(tmp_path):
    app, _ = configured_app(tmp_path)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        login(client)
        rid = create(client)
        lock = tmp_path / "web/states" / rid / "coding_run.lock"
        lock.write_bytes(b"")
        assert (
            client.get(f"/api/v1/runs/{rid}").json()["view"]["status"] == "unavailable"
        )
        assert lock.read_bytes() == b""


def test_observer_failure_does_not_change_host_result(tmp_path, monkeypatch):
    app, _ = configured_app(tmp_path)

    def broken(*args):
        raise RuntimeError("broken observer")

    monkeypatch.setattr(app.state.controller, "_publish", broken)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        login(client)
        rid = create(client)
        wait = start(client, rid)
        client.post(f"/api/v1/runs/{rid}/answers", json=answer_body(wait))
        assert settled(client, rid, status="completed")["view"]["verification"][
            "passed"
        ]


def test_plain_local_session_endpoint_initializes_browser_cookie(tmp_path):
    app, _ = configured_app(tmp_path)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        assert client.get("/api/v1/runs").status_code == 401
        opened = client.get("/api/v1/session")
        assert opened.status_code == 200, opened.text
        client.headers["x-csrf-token"] = opened.json()["csrf_token"]
        assert client.get("/api/v1/runs").status_code == 200
        assert client.post("/api/v1/session", json={}).status_code == 405


def test_second_server_cannot_start_same_root(tmp_path):
    app, _ = configured_app(tmp_path)
    other, _ = configured_app(tmp_path)
    with TestClient(app, base_url="http://127.0.0.1"):
        with pytest.raises(RuntimeError, match="already active"):
            with TestClient(other, base_url="http://127.0.0.1"):
                pass


def test_corrupt_run_is_isolated_and_corrupt_receipt_fails_closed(tmp_path):
    app, calls = configured_app(tmp_path)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        login(client)
        first, second = create(client), create(client)
        registration = tmp_path / "web/registry" / first / "registration.json"
        registration.write_text("{}", encoding="utf-8")
        listing = client.get("/api/v1/runs").json()
        assert [r["view"]["run_id"] for r in listing["runs"]] == [second]
        assert listing["unavailable"][0]["run_id"] == first
        commands = tmp_path / "web/registry" / second / "commands"
        commands.mkdir()
        (commands / f"{uuid4().hex}.json").write_text("{}", encoding="utf-8")
        snapshot = client.get(f"/api/v1/runs/{second}").json()
        assert snapshot["view"]["status"] == "unavailable"
        assert not snapshot["can_start"]
        assert not calls


def test_timeline_order_paging_and_changed_version(tmp_path):
    app, _ = configured_app(tmp_path)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        login(client)
        rid = create(client)
        wait = start(client, rid)
        first = client.get(f"/api/v1/runs/{rid}/timeline?limit=1").json()
        assert first["total"] == 2
        assert first["next_offset"] == 1
        client.post(f"/api/v1/runs/{rid}/answers", json=answer_body(wait))
        done = settled(client, rid, status="completed")
        messages = done["view"]["messages"]
        assert [m.get("tool_name") or m["text"] for m in messages][1:] == [
            "ask_user",
            "1 mm",
            "write_file",
            "model claims complete",
        ]
        assert (
            client.get(
                f"/api/v1/runs/{rid}/timeline?version={first['version']}"
            ).status_code
            == 409
        )
        assert client.get(f"/api/v1/runs/{rid}/timeline?limit=201").status_code == 422
        files = client.get(f"/api/v1/runs/{rid}/artifacts").json()
        assert files["items"] == done["view"]["files"]
        assert files["next_offset"] is None


@pytest.mark.asyncio
async def test_answer_expected_revision_checked_by_host_under_lock(tmp_path):
    from abaqus_sim_agent.coding.user_questions import UserAnswerInput, submit_answer

    app, calls = configured_app(tmp_path)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        login(client)
        rid = create(client)
        wait = start(client, rid)
        with pytest.raises(ValueError, match="task revision changed"):
            await submit_answer(
                tmp_path / "web/states" / rid,
                UserAnswerInput(
                    question_id=wait["view"]["question"]["question_id"], answer="1 mm"
                ),
                source="web",
                expected_task_revision=1,
            )
        assert client.get(f"/api/v1/runs/{rid}").json()["can_answer"]
        assert len(calls) == 1


def test_missing_terminal_evidence_never_renders_completed(tmp_path):
    app, _ = configured_app(tmp_path)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        login(client)
        rid = create(client)
        wait = start(client, rid)
        client.post(f"/api/v1/runs/{rid}/answers", json=answer_body(wait))
        settled(client, rid, status="completed")
        state = tmp_path / "web/states" / rid
        (state / "outcome.json").unlink()
        snapshot = client.get(f"/api/v1/runs/{rid}").json()
        assert snapshot["view"]["status"] == "unavailable"
        assert snapshot["view"]["verification"] is None
        assert not snapshot["can_continue"]


def test_missing_initial_snapshot_is_a_readable_unavailable_run(tmp_path):
    app, _ = configured_app(tmp_path)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        login(client)
        rid = create(client)
        start(client, rid)
        (tmp_path / "web/states" / rid / "initial_snapshot.json").unlink()
        snapshot = client.get(f"/api/v1/runs/{rid}").json()
        assert snapshot["view"]["status"] == "unavailable"
        assert not snapshot["can_answer"]


def test_model_retry_and_invalid_arguments_remain_readable():
    from pydantic_ai import ModelMessagesTypeAdapter
    from pydantic_ai.messages import (
        ModelRequest,
        ModelResponse,
        RetryPromptPart,
        ToolCallPart,
    )

    from abaqus_sim_agent.coding.read_model import message_views

    raw = ModelMessagesTypeAdapter.dump_json(
        [
            ModelResponse(
                parts=[ToolCallPart("run_cae_script", "{broken", tool_call_id="bad")]
            ),
            ModelRequest(
                parts=[
                    RetryPromptPart(
                        "Invalid arguments",
                        tool_name="run_cae_script",
                        tool_call_id="bad",
                    )
                ]
            ),
        ]
    )
    view = message_views("fixture", "goal", raw, None)
    assert view[1].status == "failed"
    assert "{broken" in view[1].arguments
    assert view[1].output == "Invalid arguments"


def test_latest_model_input_tokens_uses_latest_recorded_response():
    from pydantic_ai import ModelMessagesTypeAdapter, RequestUsage
    from pydantic_ai.messages import ModelResponse, TextPart

    from abaqus_sim_agent.coding.read_model import latest_model_input_tokens

    raw = ModelMessagesTypeAdapter.dump_json(
        [
            ModelResponse(
                parts=[TextPart("first")], usage=RequestUsage(input_tokens=120)
            ),
            ModelResponse(parts=[TextPart("unreported")]),
            ModelResponse(
                parts=[TextPart("latest")], usage=RequestUsage(input_tokens=240)
            ),
        ]
    )
    assert latest_model_input_tokens(raw) == 240
    assert latest_model_input_tokens(None) is None


def test_thread_context_checkpoint_is_atomic_idempotent_and_keeps_raw_messages(
    tmp_path,
):
    from datetime import UTC, datetime
    from uuid import uuid4

    from abaqus_sim_agent.web.contracts import ThreadContextCheckpoint, WebError
    from abaqus_sim_agent.web.storage import RunRegistry, ThreadStore

    root = tmp_path / "web"
    registry = RunRegistry(root)
    registry.initialize()
    run_id = "web-" + uuid4().hex
    (root / "registry" / run_id).mkdir()
    store = ThreadStore(registry)
    now = datetime.now(UTC)
    store.initialize(run_id, created_at=now)
    message = store.append_message(
        run_id,
        message_id=uuid4().hex,
        text="keep this raw message",
        submitted_after_turn=1,
        base_task_revision=0,
        base_amendment_sequence=0,
        created_at=now,
    )
    checkpoint = ThreadContextCheckpoint(
        through_message_id=message.message_id,
        through_sequence=message.sequence,
        summary="durable conversation summary",
        created_at=now,
        source_history_sha256="a" * 64,
    )
    store.set_context_checkpoint(run_id, checkpoint)
    first_bytes = (root / "registry" / run_id / "thread.json").read_bytes()
    store.set_context_checkpoint(run_id, checkpoint)
    assert (root / "registry" / run_id / "thread.json").read_bytes() == first_bytes
    restored = store.load(run_id)
    assert restored is not None
    assert restored.context_checkpoint == checkpoint
    assert [item.text for item in restored.messages] == ["keep this raw message"]

    invalid = checkpoint.model_copy(
        update={"through_message_id": uuid4().hex, "source_history_sha256": "b" * 64}
    )
    with pytest.raises(WebError, match="上下文检查点与会话消息不一致"):
        store.set_context_checkpoint(run_id, invalid)


def test_tool_summary_uses_structured_runtime_facts_before_output_truncation():
    from pydantic_ai import ModelMessagesTypeAdapter
    from pydantic_ai.messages import (
        ModelRequest,
        ModelResponse,
        ToolCallPart,
        ToolReturnPart,
    )

    from abaqus_sim_agent.coding.read_model import message_views

    output = {
        "succeeded": False,
        "stage_succeeded": False,
        "duration_seconds": 12.5,
        "timed_out": False,
        "stdout_tail": "x" * 20_000,
        "diagnostics": [
            {"level": "warning", "source": "job.dat", "line": 2, "message": "warn"},
            {
                "level": "error",
                "source": "job.msg",
                "line": 3,
                "message": "missing section",
            },
        ],
        "artifact_changes": [
            {"kind": "created", "path": "job.odb"},
            {"kind": "modified", "path": "job.msg"},
            {"kind": "deleted", "path": "scratch.tmp"},
        ],
    }
    raw = ModelMessagesTypeAdapter.dump_json(
        [
            ModelResponse(
                parts=[
                    ToolCallPart(
                        "run_solver_job",
                        {"job_name": "job", "input_file": "job.inp"},
                        tool_call_id="solver",
                    )
                ]
            ),
            ModelRequest(
                parts=[
                    ToolReturnPart(
                        "run_solver_job",
                        output,
                        tool_call_id="solver",
                    )
                ]
            ),
        ]
    )

    tool = message_views("fixture", "goal", raw, None)[1]
    assert tool.status == "failed"
    assert tool.output is not None
    assert tool.output.endswith("…（详情已截断）")
    assert tool.tool_summary is not None
    assert tool.tool_summary.target == "job.inp"
    assert tool.tool_summary.stage_succeeded is False
    assert tool.tool_summary.duration_seconds == 12.5
    assert tool.tool_summary.warning_count == 1
    assert tool.tool_summary.error == "missing section"
    assert tool.tool_summary.changed_paths == ("job.odb", "job.msg")
    assert tool.tool_summary.changed_count == 2
