from uuid import uuid4

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient
from tests.test_web_workbench import answer_body, configured_app, login, settled, start

from abaqus_sim_agent.coding.resume import load_resume_state
from abaqus_sim_agent.web.contracts import Registration, WebError


def create_selected(client, workspace, *, key=None):
    return client.post(
        "/api/v1/runs",
        json={
            "idempotency_key": str(key or uuid4()),
            "goal": "Write out.txt after asking for thickness",
            "workspace": str(workspace),
        },
    )


def test_directory_browser_requires_session_and_has_no_file_side_effects(tmp_path):
    folder = tmp_path / "project"
    folder.mkdir()
    (folder / "subdir").mkdir()
    (folder / "existing.txt").write_text("keep", encoding="utf-8")
    app, calls = configured_app(tmp_path)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        assert client.get("/api/v1/directories").status_code == 401
        login(client)
        result = client.get("/api/v1/directories", params={"path": str(folder)})
        assert result.status_code == 200, result.text
        view = result.json()
        assert view["selectable"] is True
        assert view["path"] == str(folder.resolve())
        assert view["parent_path"] == str(tmp_path.resolve())
        assert view["directories"] == [
            {"name": "subdir", "path": str(folder / "subdir")}
        ]
        assert calls == []
        assert list((tmp_path / "web/registry").iterdir()) == []
        assert (folder / "existing.txt").read_text(encoding="utf-8") == "keep"
        assert sorted(p.name for p in folder.iterdir()) == ["existing.txt", "subdir"]


def test_selected_workspace_runs_and_resumes_without_copying_existing_files(tmp_path):
    folder = tmp_path / "existing-project"
    folder.mkdir()
    seed = folder / "reference.txt"
    seed.write_text("existing input", encoding="utf-8")
    app, _ = configured_app(tmp_path)
    key = uuid4()
    with TestClient(app, base_url="http://127.0.0.1") as client:
        login(client)
        created = create_selected(client, folder, key=key)
        assert created.status_code == 201, created.text
        rid = created.json()["view"]["run_id"]
        assert created.json()["workspace"] == str(folder.resolve())
        assert not (tmp_path / "web/workspaces" / rid).exists()
        registration = app.state.controller.registry.load(rid)
        assert registration.workspace_kind == "selected"
        assert registration.initial_snapshot.files[0].relative_path == "reference.txt"
        assert registration.request.state_dir == tmp_path / "web/states" / rid
        assert create_selected(client, folder, key=key).json()["view"]["run_id"] == rid
        other = tmp_path / "other"
        other.mkdir()
        assert create_selected(client, other, key=key).status_code == 409
        waiting = start(client, rid)
        assert seed.read_text(encoding="utf-8") == "existing input"

    restarted, _ = configured_app(tmp_path)
    with TestClient(restarted, base_url="http://127.0.0.1") as client:
        login(client)
        restored = client.get(f"/api/v1/runs/{rid}").json()
        assert restored["can_answer"]
        assert restored["workspace"] == str(folder.resolve())
        assert restored["view"]["question"] == waiting["view"]["question"]
        reply = client.post(f"/api/v1/runs/{rid}/answers", json=answer_body(restored))
        assert reply.status_code == 202, reply.text
        done = settled(client, rid, status="completed")
        assert done["view"]["verification"]["passed"]
        files = {item["relative_path"]: item for item in done["view"]["files"]}
        assert not files["reference.txt"]["fresh"]
        assert files["out.txt"]["fresh"]
        assert (folder / "out.txt").read_text(encoding="utf-8") == "result"
        assert seed.read_text(encoding="utf-8") == "existing input"
        assert (
            client.get(
                f"/api/v1/runs/{rid}/workspace/file", params={"path": "reference.txt"}
            ).text
            == "existing input"
        )
        assert (
            client.get(
                f"/api/v1/runs/{rid}/workspace/file", params={"path": "../web/states"}
            ).status_code
            == 400
        )


@pytest.mark.parametrize(
    "kind",
    ["relative", "missing", "file", "managed", "states", "ancestor", "unicode", "unc"],
)
def test_invalid_selection_does_not_create_registration(tmp_path, kind):
    app, calls = configured_app(tmp_path)
    file = tmp_path / "file.txt"
    file.write_text("keep", encoding="utf-8")
    unicode_dir = tmp_path / "中文目录"
    unicode_dir.mkdir()
    choices = {
        "relative": "project",
        "missing": tmp_path / "missing",
        "file": file,
        "managed": tmp_path / "web",
        "states": tmp_path / "web/states",
        "ancestor": tmp_path,
        "unicode": unicode_dir,
        "unc": r"\\server\share",
    }
    with TestClient(app, base_url="http://127.0.0.1") as client:
        login(client)
        result = create_selected(client, choices[kind])
        assert result.status_code in {400, 404}, result.text
        assert list((tmp_path / "web/registry").iterdir()) == []
        assert list((tmp_path / "web/workspaces").iterdir()) == []
        assert list((tmp_path / "web/states").iterdir()) == []
        assert calls == []


def test_browser_explains_unselectable_directories_and_bounds_listing(
    tmp_path, monkeypatch
):
    from abaqus_sim_agent.web import directories

    monkeypatch.setattr(directories, "MAX_DIRECTORY_ENTRIES", 2)
    folder = tmp_path / "folder"
    folder.mkdir()
    for name in ["a", "b", "c"]:
        (folder / name).mkdir()
    unicode_dir = tmp_path / "中文目录"
    unicode_dir.mkdir()
    app, _ = configured_app(tmp_path)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        login(client)
        result = client.get("/api/v1/directories", params={"path": str(folder)}).json()
        assert result["truncated"]
        assert len(result["directories"]) == 2
        for path in [tmp_path, unicode_dir]:
            result = client.get(
                "/api/v1/directories", params={"path": str(path)}
            ).json()
            assert not result["selectable"]
            assert result["selection_error"]
        assert (
            client.get(
                "/api/v1/directories", params={"path": str(tmp_path / "web/states")}
            ).status_code
            == 400
        )


def test_linked_workspace_and_browse_are_rejected(tmp_path):
    target = tmp_path / "target"
    target.mkdir()
    (target / "nested").mkdir()
    link = tmp_path / "link"
    try:
        link.symlink_to(target, target_is_directory=True)
    except OSError:
        pytest.skip("symlink creation unavailable")
    app, _ = configured_app(tmp_path)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        login(client)
        for path in [link, link / "nested"]:
            assert create_selected(client, path).status_code == 400
            assert (
                client.get(
                    "/api/v1/directories", params={"path": str(path)}
                ).status_code
                == 400
            )


def test_overlap_with_active_or_unreconciled_peer_is_blocked(tmp_path):
    folder = tmp_path / "project"
    folder.mkdir()
    child = folder / "child"
    child.mkdir()
    app, _ = configured_app(tmp_path)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        login(client)
        rid = create_selected(client, folder).json()["view"]["run_id"]
        start(client, rid)
        peer = create_selected(client, child).json()["view"]["run_id"]
        controller = app.state.controller
        with controller._mutex:
            controller._active = rid
        try:
            assert create_selected(client, folder).status_code == 409
            assert not client.get(f"/api/v1/runs/{peer}").json()["can_start"]
        finally:
            with controller._mutex:
                controller._active = None
        state = controller.registry.state_dir(rid)
        resume = load_resume_state(state)
        (state / "resume_state.json").write_text(
            resume.model_copy(update={"status": "needs_reconcile"}).model_dump_json(),
            encoding="utf-8",
        )
        assert create_selected(client, folder).status_code == 409
        assert not client.get(f"/api/v1/runs/{peer}").json()["can_start"]
        response = client.post(
            f"/api/v1/runs/{peer}/start", json={"command_id": str(uuid4())}
        )
        assert response.status_code == 409


def test_old_or_mismatched_registration_binding_is_rejected(tmp_path):
    app, _ = configured_app(tmp_path)
    folder = tmp_path / "project"
    folder.mkdir()
    with TestClient(app, base_url="http://127.0.0.1") as client:
        login(client)
        rid = create_selected(client, folder).json()["view"]["run_id"]
        registration = app.state.controller.registry.load(rid)
        path = app.state.controller.registry.directory(rid) / "registration.json"
        path.write_text(
            registration.model_copy(
                update={"workspace_kind": "managed"}
            ).model_dump_json(),
            encoding="utf-8",
        )
        with pytest.raises(WebError, match="托管工作区"):
            app.state.controller.registry.load(rid)
        old = registration.model_dump(mode="json")
        old["schema_version"] = "web-registration-v2"
        with pytest.raises(ValueError, match="web-registration-v3"):
            Registration.model_validate(old)


def test_peer_workspace_inspection_does_not_create_execution_directories(tmp_path):
    folder = tmp_path / "project"
    folder.mkdir()
    app, _ = configured_app(tmp_path)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        login(client)
        first = create_selected(client, folder).json()["view"]["run_id"]
        second = create_selected(client, folder).json()["view"]["run_id"]
        assert client.get(f"/api/v1/runs/{second}").status_code == 200
        assert not (
            app.state.controller.registry.directory(first) / "tool-executions"
        ).exists()
        assert not (
            app.state.controller.registry.directory(second) / "tool-executions"
        ).exists()


def test_readonly_workspace_is_not_selectable(tmp_path, monkeypatch):
    from abaqus_sim_agent.web import storage

    folder = tmp_path / "readonly"
    folder.mkdir()
    actual_access = storage.os.access
    monkeypatch.setattr(
        storage.os,
        "access",
        lambda path, mode: False if path == folder else actual_access(path, mode),
    )
    app, _ = configured_app(tmp_path)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        login(client)
        listing = client.get("/api/v1/directories", params={"path": str(folder)}).json()
        assert not listing["selectable"]
        assert "不可写" in listing["selection_error"]
        assert create_selected(client, folder).json()["code"] == "workspace_readonly"
