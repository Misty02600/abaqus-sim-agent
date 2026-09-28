"""Harness 0.33.0 / 0.36.0 前台 Shell 的 Windows 取消收尾。

Adapted from pydantic/pydantic-ai-harness, MIT, commit
1747bc28cec32eba1afba46b36a08b1eb882b9da (v0.33.0),
pydantic_ai_harness/shell/_toolset.py::ShellToolset.run_command.
仅增加异常/取消时先终止进程树及 shield 清理；其余命令、输出与 cwd 语义保留。
已核对 v0.36.0（220ddcadf2d29b85de9b4455f48c1ba5c140db4e）的相同路径；
新增 max_file_bytes 仅支持 POSIX，Windows 保持未启用，不影响此适配。
许可证随包保存在 harness_shell.LICENSE；安装入口精确约束平台与版本。
"""

import asyncio
import subprocess
from typing import Any

import anyio
from pydantic_ai_harness.shell import ShellToolset


async def run_command(
    self: ShellToolset[Any], command: str, *, timeout_seconds: float | None = None
) -> str:
    self._check_command(command)
    timeout = timeout_seconds if timeout_seconds is not None else self._default_timeout
    actual_command, cwd_file = self._build_cwd_capture(command)
    try:
        proc = await anyio.open_process(
            actual_command,
            cwd=self._cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
            env=self._resolve_env(),
        )
        stdout_chunks: list[bytes] = []
        stderr_chunks: list[bytes] = []
        try:
            assert proc.stdout is not None
            assert proc.stderr is not None

            async def read_stdout() -> None:
                assert proc.stdout is not None
                async for chunk in proc.stdout:
                    stdout_chunks.append(chunk)

            async def read_stderr() -> None:
                assert proc.stderr is not None
                async for chunk in proc.stderr:
                    stderr_chunks.append(chunk)

            with anyio.fail_after(timeout):
                async with anyio.create_task_group() as tg:
                    tg.start_soon(read_stdout)
                    tg.start_soon(read_stderr)
                # The pipe-reader group's exit can consume external task cancellation.
                # Do not wait for an otherwise still-running command in that case.
                task = asyncio.current_task()
                if task is not None and task.cancelling():
                    raise asyncio.CancelledError
                await proc.wait()
        except TimeoutError:
            with anyio.CancelScope(shield=True):
                await self._kill_process_group(proc)
                await proc.wait()
                await self._drain_with_timeout(stdout_chunks, stderr_chunks, proc)
            return f"[Command timed out after {timeout}s]"
        except BaseException:
            # Closing pipes/awaiting aclose alone leaves a foreground process alive.
            with anyio.CancelScope(shield=True):
                await self._kill_process_group(proc)
                await proc.wait()
            raise
        finally:
            with anyio.CancelScope(shield=True):
                await proc.aclose()

        stdout = b"".join(stdout_chunks).decode("utf-8", errors="replace")
        stderr = b"".join(stderr_chunks).decode("utf-8", errors="replace")
        parts: list[str] = []
        if stdout:
            parts.append(f"[stdout]\n{stdout}")
        if stderr:
            parts.append(f"[stderr]\n{stderr}")
        output = "\n".join(parts) if parts else "(no output)"
        exit_code = proc.returncode if proc.returncode is not None else 0
        if cwd_file is not None and exit_code == 0:
            self._apply_captured_cwd(cwd_file)
        if exit_code != 0:
            output = f"{output}\n[exit code: {exit_code}]"
        return output
    finally:
        if cwd_file is not None:
            cwd_file.unlink(missing_ok=True)
