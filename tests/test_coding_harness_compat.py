import asyncio
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from pydantic_ai.messages import ModelMessage, ModelResponse
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai_harness.filesystem import FileSystem, FileSystemToolset
from pydantic_ai_harness.shell import Shell, ShellToolset
from pydantic_ai_harness.step_persistence import FileStepStore, ToolEffectRecord

import abaqus_sim_agent.coding.agent as coding_agent
import abaqus_sim_agent.coding.harness_compat as harness_compat
from abaqus_sim_agent.coding.contracts import CodingRunRequest, RuntimeProfile
from abaqus_sim_agent.coding.harness_compat import (
    HARNESS_WINDOWS_JSONL_APPEND_PATCH_VERSION,
    HARNESS_WINDOWS_NEWLINE_HASH_PATCH_VERSION,
    HARNESS_WINDOWS_SHELL_TREE_PATCH_VERSION,
    apply_harness_windows_jsonl_append_patch,
    apply_harness_windows_newline_hash_patch,
    apply_harness_windows_shell_tree_patch,
)


class _FakeProcess:
    pid = 42


def _hash_from(text: str) -> str:
    match = re.search(r"hash:\[?\s*([0-9a-f]{12})", text)
    assert match is not None
    return match.group(1)


@pytest.mark.asyncio
async def test_harness_shell_patch_is_version_scoped_and_idempotent() -> None:
    class FakeShellToolset:
        pass

    terminated: list[int] = []
    assert (
        apply_harness_windows_shell_tree_patch(
            platform_name="posix",
            harness_version="0.24.0",
            shell_toolset_class=FakeShellToolset,
            tree_terminator=terminated.append,
        )
        is None
    )
    assert (
        apply_harness_windows_shell_tree_patch(
            platform_name="nt",
            harness_version="0.25.0",
            shell_toolset_class=FakeShellToolset,
            tree_terminator=terminated.append,
        )
        is None
    )
    assert (
        apply_harness_windows_shell_tree_patch(
            platform_name="nt",
            harness_version="0.33.0",
            shell_toolset_class=FakeShellToolset,
            tree_terminator=terminated.append,
        )
        == HARNESS_WINDOWS_SHELL_TREE_PATCH_VERSION
    )
    kill = getattr(  # noqa: B009 - the attribute exists only after the patch
        FakeShellToolset(),
        "_kill_process_group",
    )
    await kill(_FakeProcess())
    assert terminated == [42]
    assert (
        apply_harness_windows_shell_tree_patch(
            platform_name="nt",
            harness_version="0.33.0",
            shell_toolset_class=FakeShellToolset,
            tree_terminator=terminated.append,
        )
        == HARNESS_WINDOWS_SHELL_TREE_PATCH_VERSION
    )


def test_harness_jsonl_patch_serializes_parallel_append_methods() -> None:
    class FakeFileStepStore:
        active = 0
        maximum_active = 0

        def _record(self) -> None:
            type(self).active += 1
            type(self).maximum_active = max(
                type(self).maximum_active,
                type(self).active,
            )
            time.sleep(0.005)
            type(self).active -= 1

        def _sync_append_event(self, _event: object) -> None:
            self._record()

        def _sync_record_tool_effect(self, _record: object) -> None:
            self._record()

    assert (
        apply_harness_windows_jsonl_append_patch(
            platform_name="posix",
            harness_version="0.24.0",
            file_step_store_class=FakeFileStepStore,
        )
        is None
    )
    assert (
        apply_harness_windows_jsonl_append_patch(
            platform_name="nt",
            harness_version="0.25.0",
            file_step_store_class=FakeFileStepStore,
        )
        is None
    )
    assert (
        apply_harness_windows_jsonl_append_patch(
            platform_name="nt",
            harness_version="0.33.0",
            file_step_store_class=FakeFileStepStore,
        )
        == HARNESS_WINDOWS_JSONL_APPEND_PATCH_VERSION
    )
    store = FakeFileStepStore()

    def append_one(index: int) -> None:
        if index % 2:
            store._sync_append_event(object())
        else:
            store._sync_record_tool_effect(object())

    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = [pool.submit(append_one, index) for index in range(20)]
        for future in futures:
            future.result()

    assert FakeFileStepStore.maximum_active == 1
    assert (
        apply_harness_windows_jsonl_append_patch(
            platform_name="nt",
            harness_version="0.33.0",
            file_step_store_class=FakeFileStepStore,
        )
        == HARNESS_WINDOWS_JSONL_APPEND_PATCH_VERSION
    )


def test_harness_jsonl_patch_serializes_first_install(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeFileStepStore:
        pass

    active = 0
    maximum_active = 0

    def observed_install(_store_class: type[object]) -> str:
        nonlocal active, maximum_active
        active += 1
        maximum_active = max(maximum_active, active)
        time.sleep(0.005)
        active -= 1
        return HARNESS_WINDOWS_JSONL_APPEND_PATCH_VERSION

    monkeypatch.setattr(
        harness_compat,
        "_install_jsonl_append_patch",
        observed_install,
    )
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = tuple(
            pool.map(
                lambda _index: apply_harness_windows_jsonl_append_patch(
                    platform_name="nt",
                    harness_version="0.33.0",
                    file_step_store_class=FakeFileStepStore,
                ),
                range(20),
            )
        )

    assert set(results) == {HARNESS_WINDOWS_JSONL_APPEND_PATCH_VERSION}
    assert maximum_active == 1


@pytest.mark.skipif(os.name != "nt", reason="Windows JSONL append regression")
@pytest.mark.asyncio
async def test_file_step_store_parallel_tool_effect_records_remain_valid_jsonl(
    tmp_path: Path,
) -> None:
    assert (
        apply_harness_windows_jsonl_append_patch()
        == HARNESS_WINDOWS_JSONL_APPEND_PATCH_VERSION
    )
    store = FileStepStore(tmp_path)
    run_id = "parallel-effects"
    records = tuple(
        ToolEffectRecord(
            tool_call_id=f"call-{index}",
            tool_name="read_file",
            run_id=run_id,
            status="started",
        )
        for index in range(40)
    )

    await asyncio.gather(*(store.record_tool_effect(record) for record in records))

    unresolved = await store.list_unresolved_tool_effects(run_id=run_id)
    assert {effect.tool_call_id for effect in unresolved} == {
        record.tool_call_id for record in records
    }


def test_coding_agent_applies_hash_patch_before_building_coder(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = (tmp_path / "workspace").resolve()
    state_dir = (tmp_path / "state").resolve()
    workspace.mkdir()
    state_dir.mkdir()
    request = CodingRunRequest(
        run_id="harness-hash-integration",
        goal="Build a test agent without running it.",
        workspace=workspace,
        state_dir=state_dir,
        runtime=RuntimeProfile(
            launcher_path=Path(r"D:\SIMULIA\Commands\abq2024.bat").resolve()
        ),
    )
    calls: list[str] = []

    def apply_hash_patch() -> str:
        calls.append("hash")
        return HARNESS_WINDOWS_NEWLINE_HASH_PATCH_VERSION

    def apply_shell_patch() -> str:
        calls.append("shell")
        return HARNESS_WINDOWS_SHELL_TREE_PATCH_VERSION

    def apply_jsonl_patch() -> str:
        calls.append("jsonl")
        return HARNESS_WINDOWS_JSONL_APPEND_PATCH_VERSION

    def model_function(
        _messages: list[ModelMessage],
        _info: AgentInfo,
    ) -> ModelResponse:
        return ModelResponse(parts=[])

    monkeypatch.setattr(
        coding_agent,
        "apply_harness_windows_newline_hash_patch",
        apply_hash_patch,
    )
    monkeypatch.setattr(
        coding_agent,
        "apply_harness_windows_shell_tree_patch",
        apply_shell_patch,
    )
    monkeypatch.setattr(
        coding_agent,
        "apply_harness_windows_jsonl_append_patch",
        apply_jsonl_patch,
    )

    coding_agent.create_abaqus_coding_agent(FunctionModel(model_function), request)

    assert calls == ["hash", "shell", "jsonl"]


@pytest.mark.skipif(os.name != "nt", reason="Windows newline translation regression")
@pytest.mark.asyncio
async def test_filesystem_hash_is_stable_across_windows_write_read_and_edit(
    tmp_path: Path,
) -> None:
    assert apply_harness_windows_newline_hash_patch() is None
    assert apply_harness_windows_newline_hash_patch() is None

    toolset = FileSystem(
        root_dir=tmp_path,
        protected_patterns=(),
    ).get_toolset()
    assert isinstance(toolset, FileSystemToolset)

    write_result = await toolset.write_file("example.py", "first\nsecond\n")
    assert (tmp_path / "example.py").read_bytes() == b"first\nsecond\n"

    read_result = await toolset.read_file("example.py")
    info_result = await toolset.file_info("example.py")
    content_hash = _hash_from(read_result)
    assert content_hash == _hash_from(write_result)
    assert content_hash == _hash_from(info_result)

    edit_result = await toolset.edit_file(
        "example.py",
        "second",
        "changed",
        expected_hash=content_hash,
    )

    assert "changed" in (tmp_path / "example.py").read_text(encoding="utf-8")
    assert _hash_from(edit_result) == _hash_from(await toolset.read_file("example.py"))


@pytest.mark.skipif(os.name != "nt", reason="Windows process-tree regression")
@pytest.mark.asyncio
async def test_shell_timeout_uses_windows_process_tree_termination(
    tmp_path: Path,
) -> None:
    assert (
        apply_harness_windows_shell_tree_patch()
        == HARNESS_WINDOWS_SHELL_TREE_PATCH_VERSION
    )
    toolset = ShellToolset(
        cwd=tmp_path,
        allowed_commands=("python",),
        denied_commands=(),
        denied_operators=(),
        default_timeout=0.1,
        max_output_chars=2000,
        persist_cwd=False,
        allow_interactive=False,
    )

    result = await toolset.run_command(
        'python -c "import time; time.sleep(60)"',
        timeout_seconds=0.1,
    )

    assert result == "[Command timed out after 0.1s]"


@pytest.mark.skipif(os.name != "nt", reason="Windows process-tree regression")
@pytest.mark.asyncio
async def test_run_scoped_shell_cleans_up_background_process_on_exit(
    tmp_path: Path,
) -> None:
    apply_harness_windows_shell_tree_patch()
    toolset = Shell(cwd=tmp_path).get_toolset()

    async with toolset:
        await toolset.start_command('python -c "import time; time.sleep(60)"')
        processes = [bg.proc for bg in toolset._background.values()]
        assert len(processes) == 1
        assert processes[0].returncode is None

    assert processes[0].returncode is not None
