import asyncio
import hashlib
import json
import shutil
import zipfile
from collections.abc import Sequence
from pathlib import Path

import pytest

from abaqus_sim_agent.coding.static_preflight import (
    AbqpyBasedPyrightPreflight,
    prepare_abqpy_source,
)
from abaqus_sim_agent.execution import ProcessOutput


class _FakeProcessStrategy:
    def __init__(self, output: ProcessOutput) -> None:
        self.output = output
        self.calls: list[tuple[tuple[str, ...], Path, float]] = []

    async def run(
        self,
        command: Sequence[str],
        *,
        cwd: Path,
        timeout_seconds: float,
    ) -> ProcessOutput:
        self.calls.append((tuple(command), cwd, timeout_seconds))
        return self.output


def _write_abqpy_wheel(path: Path, *, unsafe_name: str | None = None) -> None:
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(
            "abqpy-2024.10.dist-info/METADATA",
            "Metadata-Version: 2.1\nName: abqpy\nVersion: 2024.10\n",
        )
        archive.writestr("abaqus/__init__.py", "from .Section import *\n")
        archive.writestr("abaqus/Section.py", "class BeamSection: ...\n")
        if unsafe_name is not None:
            archive.writestr(unsafe_name, "escape\n")


def _diagnostic_output() -> ProcessOutput:
    payload = {
        "version": "1.1.407",
        "generalDiagnostics": [
            {
                "file": "build_model.py",
                "severity": "warning",
                "message": "Wildcard import from a library is not allowed",
                "range": {
                    "start": {"line": 0, "character": 0},
                    "end": {"line": 0, "character": 20},
                },
                "rule": "reportWildcardImportFromLibrary",
            },
            {
                "file": "build_model.py",
                "severity": "error",
                "message": 'No parameter named "orientation"',
                "range": {
                    "start": {"line": 8, "character": 12},
                    "end": {"line": 8, "character": 23},
                },
                "rule": "reportCallIssue",
            },
        ],
        "summary": {
            "filesAnalyzed": 1,
            "errorCount": 1,
            "warningCount": 1,
            "informationCount": 0,
            "timeInSec": 0.1,
        },
    }
    return ProcessOutput(
        exit_code=1,
        stdout=json.dumps(payload).encode(),
        stderr=b"",
    )


def _fixture_set_sha256(paths: list[Path], repository: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(paths):
        digest.update(path.relative_to(repository).as_posix().encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def test_prepare_abqpy_source_is_immutable_and_reusable(tmp_path: Path) -> None:
    wheel = tmp_path / "abqpy-2024.10-py3-none-any.whl"
    cache = tmp_path / "cache"
    _write_abqpy_wheel(wheel)

    first = prepare_abqpy_source(wheel, cache)
    second = prepare_abqpy_source(wheel, cache)

    assert first == second
    assert first.version == "2024.10"
    assert first.binding().policy_version == "abqpy-advisory-high-confidence-v2"
    enforced = first.binding("enforced_verified")
    assert enforced.policy_version == "abqpy-enforced-verified-v1"
    assert enforced.syntax_gate_version == "python-3.10-production-syntax-v1"
    assert len(first.wheel_sha256) == 64
    assert (first.root / "abaqus" / "__init__.py").is_file()
    config = json.loads(first.config_path.read_text(encoding="utf-8"))
    assert config["extraPaths"] == [str(first.root)]
    manifest = json.loads(
        (first.root / "static_source.json").read_text(encoding="utf-8")
    )
    assert manifest["wheel_sha256"] == first.wheel_sha256


def test_static_preflight_continues_invocation_sequence_after_restart(
    tmp_path: Path,
) -> None:
    wheel = tmp_path / "abqpy-2024.10-py3-none-any.whl"
    _write_abqpy_wheel(wheel)
    source = prepare_abqpy_source(wheel, tmp_path / "cache")
    workspace = tmp_path / "workspace"
    state_dir = tmp_path / "state"
    workspace.mkdir()
    (workspace / "build_model.py").write_text("value = 1\n", encoding="utf-8")
    (state_dir / "static_preflight" / "001").mkdir(parents=True)
    preflight = AbqpyBasedPyrightPreflight(
        source,
        state_dir,
        process_strategy=_FakeProcessStrategy(
            ProcessOutput(exit_code=0, stdout=b"{}", stderr=b"")
        ),
    )

    asyncio.run(preflight.inspect(workspace, "build_model.py"))

    assert (state_dir / "static_preflight" / "002").is_dir()


def test_static_preflight_v2_benchmark_matches_current_fixtures() -> None:
    repository = Path(__file__).resolve().parents[1]
    benchmark = json.loads(
        (
            repository
            / "benchmarks/coding/abqpy_static_preflight_policy_v2_20260829.json"
        ).read_text(encoding="utf-8")
    )
    positive_paths = list((repository / "tests/fixtures/abaqus2024").rglob("*.py"))
    negative_paths = [
        repository / case["fixture"] for case in benchmark["negative_set"]["cases"]
    ]

    assert len(positive_paths) == benchmark["positive_set"]["script_count"]
    assert (
        _fixture_set_sha256(positive_paths, repository)
        == (benchmark["positive_set"]["fixture_set_sha256"])
    )
    assert len(negative_paths) == benchmark["negative_set"]["script_count"]
    assert (
        _fixture_set_sha256(negative_paths, repository)
        == (benchmark["negative_set"]["fixture_set_sha256"])
    )


def test_enforced_aci_benchmark_matches_current_fixtures() -> None:
    repository = Path(__file__).resolve().parents[1]
    benchmark = json.loads(
        (
            repository / "benchmarks/coding/abaqus_aci_enforced_policy_v1_20260829.json"
        ).read_text(encoding="utf-8")
    )
    positive_paths = list((repository / "tests/fixtures/abaqus2024").rglob("*.py"))
    negative_paths = [repository / path for path in benchmark["negative_set"]["cases"]]

    assert len(positive_paths) == benchmark["positive_set"]["script_count"]
    assert (
        _fixture_set_sha256(positive_paths, repository)
        == (benchmark["positive_set"]["fixture_set_sha256"])
    )
    assert len(negative_paths) == benchmark["negative_set"]["script_count"]
    assert (
        _fixture_set_sha256(negative_paths, repository)
        == (benchmark["negative_set"]["fixture_set_sha256"])
    )


@pytest.mark.parametrize("unsafe_name", ["../escape.py", "C:/escape.py"])
def test_prepare_abqpy_source_rejects_wheel_path_escape(
    tmp_path: Path,
    unsafe_name: str,
) -> None:
    wheel = tmp_path / "abqpy-2024.10-py3-none-any.whl"
    _write_abqpy_wheel(wheel, unsafe_name=unsafe_name)

    with pytest.raises(ValueError, match="unsafe path"):
        prepare_abqpy_source(wheel, tmp_path / "cache")

    assert not (tmp_path / "escape.py").exists()


def test_preflight_records_actionable_diagnostics_and_ignores_import_noise(
    tmp_path: Path,
) -> None:
    wheel = tmp_path / "abqpy-2024.10-py3-none-any.whl"
    _write_abqpy_wheel(wheel)
    source = prepare_abqpy_source(wheel, tmp_path / "cache")
    workspace = tmp_path / "workspace"
    state_dir = tmp_path / "state"
    workspace.mkdir()
    (workspace / "build_model.py").write_text(
        "from abaqus import *\nmodel.BeamSection(orientation='x')\n",
        encoding="utf-8",
    )
    process = _FakeProcessStrategy(_diagnostic_output())
    preflight = AbqpyBasedPyrightPreflight(
        source,
        state_dir,
        process_strategy=process,
    )

    report = asyncio.run(preflight.inspect(workspace, "build_model.py"))

    assert report.status == "diagnostics"
    assert report.ignored_diagnostic_count == 1
    assert len(report.diagnostics) == 1
    diagnostic = report.diagnostics[0]
    assert diagnostic.rule == "reportCallIssue"
    assert diagnostic.line == 9
    assert diagnostic.column == 13
    assert "orientation" in diagnostic.message
    assert report.stdout_raw_path.read_bytes() == process.output.stdout
    assert report.stderr_raw_path.read_bytes() == b""
    trace = (state_dir / "static_preflight.jsonl").read_text(encoding="utf-8")
    assert report.script_sha256 in trace
    command, cwd, timeout = process.calls[0]
    assert command[-1] == "--outputjson"
    assert "basedpyright" in command
    assert cwd == workspace
    assert timeout == 20.0


def test_preflight_filters_unverified_noise_and_records_licensed_override(
    tmp_path: Path,
) -> None:
    wheel = tmp_path / "abqpy-2024.10-py3-none-any.whl"
    _write_abqpy_wheel(wheel)
    source = prepare_abqpy_source(wheel, tmp_path / "cache")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "build_model.py").write_text("pass\n", encoding="utf-8")
    payload = {
        "generalDiagnostics": [
            {
                "severity": "error",
                "message": (
                    'Cannot access attribute "writeInput" for class "Job"\n'
                    '  Attribute "writeInput" is unknown'
                ),
                "range": {
                    "start": {"line": 4, "character": 2},
                    "end": {"line": 4, "character": 12},
                },
                "rule": "reportAttributeAccessIssue",
            },
            {
                "severity": "error",
                "message": 'Object of type "None" cannot be used as iterable value',
                "range": {
                    "start": {"line": 9, "character": 1},
                    "end": {"line": 9, "character": 5},
                },
                "rule": "reportOptionalIterable",
            },
        ]
    }
    process = _FakeProcessStrategy(
        ProcessOutput(
            exit_code=1,
            stdout=json.dumps(payload).encode(),
            stderr=b"",
        )
    )
    preflight = AbqpyBasedPyrightPreflight(
        source,
        tmp_path / "state",
        process_strategy=process,
    )

    report = asyncio.run(preflight.inspect(workspace, "build_model.py"))

    assert report.status == "clean"
    assert report.diagnostics == ()
    assert report.ignored_diagnostic_count == 2
    assert report.applied_override_ids == ("licensed-abq2024-job-write-input-v1",)


def test_preflight_timeout_fails_open_with_preserved_raw_output(
    tmp_path: Path,
) -> None:
    wheel = tmp_path / "abqpy-2024.10-py3-none-any.whl"
    _write_abqpy_wheel(wheel)
    source = prepare_abqpy_source(wheel, tmp_path / "cache")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "extract_results.py").write_text("pass\n", encoding="utf-8")
    process = _FakeProcessStrategy(
        ProcessOutput(exit_code=None, stdout=b"partial", stderr=b"", timed_out=True)
    )
    preflight = AbqpyBasedPyrightPreflight(
        source,
        tmp_path / "state",
        process_strategy=process,
    )

    report = asyncio.run(preflight.inspect(workspace, "extract_results.py"))

    assert report.status == "timeout"
    assert report.timed_out is True
    assert report.error == "BasedPyright static preflight timed out"
    assert report.stdout_raw_path.read_bytes() == b"partial"


def test_preflight_detects_script_change_during_check(tmp_path: Path) -> None:
    wheel = tmp_path / "abqpy-2024.10-py3-none-any.whl"
    _write_abqpy_wheel(wheel)
    source = prepare_abqpy_source(wheel, tmp_path / "cache")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    script = workspace / "build_model.py"
    script.write_text("old\n", encoding="utf-8")

    class _MutatingProcess(_FakeProcessStrategy):
        async def run(
            self,
            command: Sequence[str],
            *,
            cwd: Path,
            timeout_seconds: float,
        ) -> ProcessOutput:
            script.write_text("new\n", encoding="utf-8")
            return await super().run(
                command,
                cwd=cwd,
                timeout_seconds=timeout_seconds,
            )

    process = _MutatingProcess(
        ProcessOutput(
            exit_code=0,
            stdout=b'{"generalDiagnostics": []}',
            stderr=b"",
        )
    )
    preflight = AbqpyBasedPyrightPreflight(
        source,
        tmp_path / "state",
        process_strategy=process,
    )

    report = asyncio.run(preflight.inspect(workspace, "build_model.py"))

    assert report.status == "changed"
    assert report.error == "Production script changed during static preflight"
    assert report.stdout_raw_path.read_bytes() == process.output.stdout


def test_real_basedpyright_policy_does_not_pause_known_passing_b31_reference(
    tmp_path: Path,
) -> None:
    script_name = "build_model.py"
    repository = Path(__file__).resolve().parents[1]
    wheel = repository / "var/api-catalog/sources/abqpy-2024.10-py3-none-any.whl"
    fixture = repository / "tests/fixtures/abaqus2024/beam_b31" / script_name
    if not wheel.is_file() or not fixture.is_file():
        pytest.skip("fixed abqpy source or licensed reference is unavailable")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    shutil.copyfile(fixture, workspace / script_name)
    source = prepare_abqpy_source(wheel, tmp_path / "cache")
    preflight = AbqpyBasedPyrightPreflight(source, tmp_path / "state")

    report = asyncio.run(preflight.inspect(workspace, script_name))

    assert report.status == "clean"
    assert report.diagnostics == ()
    assert report.ignored_diagnostic_count > 0


def test_real_basedpyright_catches_missing_orientation_arguments_from_v1_trace(
    tmp_path: Path,
) -> None:
    repository = Path(__file__).resolve().parents[1]
    wheel = repository / "var/api-catalog/sources/abqpy-2024.10-py3-none-any.whl"
    if not wheel.is_file():
        pytest.skip("fixed abqpy source is unavailable")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "build_model.py").write_text(
        "from abaqus import mdb\n"
        "part = mdb.models['Model-1'].parts['Part-1']\n"
        "part.assignBeamSectionOrientation()\n",
        encoding="utf-8",
    )
    source = prepare_abqpy_source(wheel, tmp_path / "cache")
    preflight = AbqpyBasedPyrightPreflight(source, tmp_path / "state")

    report = asyncio.run(preflight.inspect(workspace, "build_model.py"))

    assert report.status == "diagnostics"
    assert any(
        diagnostic.rule == "reportCallIssue"
        and "Arguments missing for parameters" in diagnostic.message
        for diagnostic in report.diagnostics
    )


def test_enforced_preflight_has_no_blocker_on_passing_b31_reference(
    tmp_path: Path,
) -> None:
    script_name = "build_model.py"
    repository = Path(__file__).resolve().parents[1]
    wheel = repository / "var/api-catalog/sources/abqpy-2024.10-py3-none-any.whl"
    fixture = repository / "tests/fixtures/abaqus2024/beam_b31" / script_name
    if not wheel.is_file() or not fixture.is_file():
        pytest.skip("fixed abqpy source or licensed reference is unavailable")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    shutil.copyfile(fixture, workspace / script_name)
    source = prepare_abqpy_source(wheel, tmp_path / "cache")
    preflight = AbqpyBasedPyrightPreflight(
        source,
        tmp_path / "state",
        mode="enforced_verified",
    )

    report = asyncio.run(preflight.inspect(workspace, script_name))

    assert all(diagnostic.disposition != "block" for diagnostic in report.diagnostics)


def test_enforced_preflight_keeps_unverified_missing_method_as_warning(
    tmp_path: Path,
) -> None:
    repository = Path(__file__).resolve().parents[1]
    wheel = repository / "var/api-catalog/sources/abqpy-2024.10-py3-none-any.whl"
    if not wheel.is_file():
        pytest.skip("fixed abqpy source is unavailable")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "build_model.py").write_text(
        "from abaqus import mdb\n"
        "model = mdb.models['Model-1']\n"
        "model.HomogeneousBeamSection(name='Section')\n",
        encoding="utf-8",
    )
    source = prepare_abqpy_source(wheel, tmp_path / "cache")
    preflight = AbqpyBasedPyrightPreflight(
        source,
        tmp_path / "state",
        mode="enforced_verified",
    )

    report = asyncio.run(preflight.inspect(workspace, "build_model.py"))

    warnings = [
        diagnostic
        for diagnostic in report.diagnostics
        if diagnostic.disposition == "warn"
    ]
    assert warnings
    assert any("HomogeneousBeamSection" in item.message for item in warnings)
    assert not any(item.disposition == "block" for item in report.diagnostics)


@pytest.mark.parametrize(
    ("source_text", "expected_rule_id"),
    [
        (
            "from abaqus import mdb\n"
            "model = mdb.models['Model-1']\n"
            "model.BeamSection(name='S', createStepName='Initial', "
            "profile='P', material='M')\n",
            "verified-model-beam-section-keywords-v1",
        ),
        (
            "from abaqus import mdb\n"
            "part = mdb.models['Model-1'].parts['Part-1']\n"
            "part.seedPartByNumber(number=20)\n",
            "verified-attribute-part-seed-part-by-number-v1",
        ),
        (
            "from abaqus import mdb\n"
            "model = mdb.models['Model-1']\n"
            "model.writeInput(fileName='beam_tension')\n",
            "verified-attribute-model-write-input-v1",
        ),
        (
            "from abaqus import mdb\n"
            "part = mdb.models['Model-1'].parts['Part-1']\n"
            "part.assignBeamSectionOrientation()\n",
            "verified-beam-orientation-call-contract-v1",
        ),
    ],
)
def test_enforced_preflight_blocks_real_b31_v1_failure_families(
    tmp_path: Path,
    source_text: str,
    expected_rule_id: str,
) -> None:
    repository = Path(__file__).resolve().parents[1]
    wheel = repository / "var/api-catalog/sources/abqpy-2024.10-py3-none-any.whl"
    if not wheel.is_file():
        pytest.skip("fixed abqpy source is unavailable")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "build_model.py").write_text(source_text, encoding="utf-8")
    source = prepare_abqpy_source(wheel, tmp_path / "cache")
    preflight = AbqpyBasedPyrightPreflight(
        source,
        tmp_path / "state",
        mode="enforced_verified",
    )

    report = asyncio.run(preflight.inspect(workspace, "build_model.py"))

    blockers = [
        diagnostic
        for diagnostic in report.diagnostics
        if diagnostic.disposition == "block"
    ]
    assert any(item.policy_rule_id == expected_rule_id for item in blockers)
