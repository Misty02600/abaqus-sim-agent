"""测评命令拥有的 Phoenix 生命周期；不复用服务，不修改默认数据目录。"""

from __future__ import annotations

import logging
import os
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
from collections import deque
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

from abaqus_sim_agent import telemetry
from abaqus_sim_agent.execution.environment import subprocess_environment

_logger = logging.getLogger(__name__)
PHOENIX_SERVER_VERSION = "20.16.0"
STARTUP_TIMEOUT_SECONDS = 120
SHUTDOWN_TIMEOUT_SECONDS = 15


def _server_environment() -> dict[str, str]:
    env = {
        key: value
        for key, value in subprocess_environment().items()
        if not key.startswith(("PHOENIX_", "OTEL_", "LOGFIRE_"))
        and key not in {"PYTHONPATH", "PYTHONHOME"}
    }
    env.update(
        PHOENIX_HOST="127.0.0.1",
        PHOENIX_DISCOVER_CONFIG="false",
        PHOENIX_TELEMETRY_ENABLED="false",
        PHOENIX_ALLOW_EXTERNAL_RESOURCES="false",
        PYTHONIOENCODING="utf-8",
    )
    # 不设置 PHOENIX_PORT、PHOENIX_WORKING_DIR 或 SQL 地址，使用原生默认值。
    return env


def _check_port_available() -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        if os.name == "nt":
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        try:
            listener.bind(("127.0.0.1", 6006))
        except OSError as exc:
            raise RuntimeError(
                "Phoenix port 6006 is occupied. Stop the existing service before "
                "running this evaluation; it will not be reused or terminated."
            ) from exc


def _stop(process: subprocess.Popen[bytes]) -> None:
    """先通过 EOF 请求官方 close_app；超时才终止本次受控进程树。"""
    if process.stdin is not None:
        try:
            process.stdin.close()
        except OSError:
            pass
    try:
        process.wait(timeout=SHUTDOWN_TIMEOUT_SECONDS)
        return
    except subprocess.TimeoutExpired:
        _logger.warning("Phoenix did not close in time; terminating its process tree")
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW,
            timeout=10,
            check=True,
        )
    else:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    process.wait(timeout=5)


@contextmanager
def phoenix_server() -> Iterator[None]:
    """直接启动本次本机服务并在退出时关闭；不接管现有实例或删除数据库。

    Note:
        服务在 uv 工具隔离环境运行，首次可能下载依赖。就绪信号来自本次子进程，
        不把别的服务的 HTTP 响应当成启动成功。父进程退出导致 stdin 关闭时，
        服务宿主会调用官方 close_app；不保证系统断电或强杀后的协作式收尾。
    """
    _check_port_available()
    uvx = shutil.which("uvx")
    if uvx is None:
        raise RuntimeError("Phoenix evaluation startup requires uv (uvx not found)")
    token = f"abaqus-phoenix-ready-{uuid4().hex}"
    ready = threading.Event()
    output: deque[str] = deque(maxlen=25)
    command = [
        uvx,
        "--python",
        "3.12",
        "--from",
        f"arize-phoenix=={PHOENIX_SERVER_VERSION}",
        "python",
        "-u",
        str(Path(__file__).with_name("_phoenix_server_host.py")),
        token,
    ]
    print(
        f"Starting Phoenix at {telemetry.DEFAULT_ENDPOINT} (first use may download server dependencies)",
        file=sys.stderr,
        flush=True,
    )
    process = subprocess.Popen(
        command,
        cwd=tempfile.gettempdir(),
        env=_server_environment(),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        creationflags=(
            subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
            if os.name == "nt"
            else 0
        ),
        start_new_session=os.name != "nt",
    )

    def read_output() -> None:
        assert process.stdout is not None
        for raw in process.stdout:
            line = raw.decode("utf-8", errors="replace").strip()
            if line == token:
                ready.set()
            else:
                output.append(line)

    reader = threading.Thread(target=read_output, name="phoenix-startup", daemon=True)
    reader.start()
    failed = False
    try:
        deadline = time.monotonic() + STARTUP_TIMEOUT_SECONDS
        while not ready.wait(0.1):
            if process.poll() is not None or time.monotonic() >= deadline:
                raise RuntimeError(
                    "Phoenix could not start; no evaluation was started.\n"
                    + "\n".join(output)
                )
        if process.poll() is not None:
            raise RuntimeError("Phoenix exited before evaluation startup")
        print(
            f"Phoenix ready: {telemetry.DEFAULT_ENDPOINT}", file=sys.stderr, flush=True
        )
        yield
        if process.poll() is not None:
            raise RuntimeError(
                "Phoenix exited during evaluation; local results are retained"
            )
    except BaseException:
        failed = True
        raise
    finally:
        try:
            _stop(process)
            if process.returncode and not failed:
                raise RuntimeError(
                    f"Phoenix shutdown failed (exit {process.returncode})"
                )
        except Exception:
            if not failed:
                raise
            _logger.exception(
                "Phoenix cleanup failed; preserving the evaluation exception"
            )
        finally:
            reader.join(timeout=2)
            if process.stdout is not None and not reader.is_alive():
                process.stdout.close()


@contextmanager
def evaluation_telemetry_session(enabled: bool) -> Iterator[None]:
    """测评专用：先启动服务，再初始化追踪；最后先关闭导出器，再关闭服务。"""
    if not enabled:
        with telemetry.telemetry_session(False, environment="evaluation"):
            yield
        return
    with phoenix_server():
        previous = os.environ.get("PHOENIX_COLLECTOR_ENDPOINT")
        os.environ["PHOENIX_COLLECTOR_ENDPOINT"] = telemetry.DEFAULT_ENDPOINT
        try:
            with telemetry.telemetry_session(True, environment="evaluation"):
                yield
        finally:
            if previous is None:
                os.environ.pop("PHOENIX_COLLECTOR_ENDPOINT", None)
            else:
                os.environ["PHOENIX_COLLECTOR_ENDPOINT"] = previous
