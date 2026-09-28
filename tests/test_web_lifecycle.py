"""通过真实HTTP/子进程验证连接与执行段的生命周期分离。"""

import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path
from uuid import uuid4

import pytest

pytest.importorskip("fastapi")
import httpx


def test_disconnect_and_graceful_shutdown_drain_active_segment(tmp_path):
    with socket.socket() as reserved:
        reserved.bind(("127.0.0.1", 0))
        port = reserved.getsockname()[1]
    script = tmp_path / "server.py"
    script.write_text(
        """
import sys, threading
from pathlib import Path
import uvicorn
from tests.support.web import configured_app
block = threading.Event()
app, calls = configured_app(Path(sys.argv[1]), block=block)
server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=int(sys.argv[2]), access_log=False, timeout_graceful_shutdown=1))
def commands():
    for line in sys.stdin:
        if line.strip() == "release": block.set()
        if line.strip() == "stop": server.should_exit = True
threading.Thread(target=commands, daemon=True).start()
server.run()
""",
        encoding="utf-8",
    )
    repo = Path(__file__).resolve().parents[1]
    env = dict(os.environ, PYTHONPATH=os.pathsep.join((str(repo / "src"), str(repo))))
    log = tmp_path / "server.log"
    with log.open("w", encoding="utf-8") as output:
        process = subprocess.Popen(
            [sys.executable, str(script), str(tmp_path), str(port)],
            stdin=subprocess.PIPE,
            stdout=output,
            stderr=output,
            text=True,
            cwd=repo,
            env=env,
        )
        try:
            with httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=2) as client:
                for _ in range(100):
                    try:
                        response = client.get("/api/v1/session")
                        if response.status_code == 200:
                            break
                    except httpx.TransportError:
                        pass
                    assert process.poll() is None, log.read_text(encoding="utf-8")
                    time.sleep(0.1)
                else:
                    pytest.fail("server did not start")
                client.headers["x-csrf-token"] = response.json()["csrf_token"]
                workspace = tmp_path / "selected-project"
                workspace.mkdir()
                created = client.post(
                    "/api/v1/runs",
                    json={
                        "idempotency_key": str(uuid4()),
                        "goal": "fixture",
                        "workspace": str(workspace),
                    },
                ).json()
                rid = created["view"]["run_id"]
                assert (
                    client.post(
                        f"/api/v1/runs/{rid}/start", json={"command_id": str(uuid4())}
                    ).status_code
                    == 202
                )
                with client.stream("GET", f"/api/v1/runs/{rid}/events") as stream:
                    assert next(stream.iter_lines()) == "event: sync"
                assert client.get(f"/api/v1/runs/{rid}").json()["active"]
                assert process.stdin is not None
                process.stdin.write("stop\n")
                process.stdin.flush()
                time.sleep(0.5)
                assert process.poll() is None, (
                    "shutdown must drain the blocked work segment"
                )
                process.stdin.write("release\n")
                process.stdin.flush()
                assert process.wait(timeout=15) == 0, log.read_text(encoding="utf-8")
                state = json.loads(
                    (tmp_path / "web/states" / rid / "resume_state.json").read_bytes()
                )
                assert state["status"] == "waiting_for_user"
                assert state["usage"]["requests"] == 1
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=10)
