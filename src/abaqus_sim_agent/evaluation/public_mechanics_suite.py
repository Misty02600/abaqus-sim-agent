"""公开材料力学端到端评测；独立 Case 工作区和宿主 oracle。"""

import asyncio
import json
import os
import shutil
import subprocess
import sys
from datetime import UTC, datetime
from importlib.metadata import distribution, distributions
from pathlib import Path
from typing import Literal
from uuid import uuid4

import httpx
from pydantic_evals.reporting import EvaluationReportAdapter

from abaqus_sim_agent import telemetry
from abaqus_sim_agent.api_catalog import ApiCatalog
from abaqus_sim_agent.coding.agent import create_abaqus_coding_agent
from abaqus_sim_agent.coding.clarification import ScriptedClarificationProvider
from abaqus_sim_agent.coding.contracts import (
    ApiCatalogBinding,
    CodingRunBudget,
    CodingRunRequest,
    RuntimeProfile,
)
from abaqus_sim_agent.coding.dependencies import CodingAgentDependencies
from abaqus_sim_agent.coding.model import (
    DEEPSEEK_V4_FLASH,
    DeepSeekGateway,
    ModelExecutionConfig,
    create_deepseek_chat_model,
    new_run_execution_config,
    take_gateway_api_key_from_environment,
)
from abaqus_sim_agent.coding.resume import load_initial_snapshot
from abaqus_sim_agent.coding.run_lock import CodingRunFileLock
from abaqus_sim_agent.coding.service import CodingRunService
from abaqus_sim_agent.coding.workspace import WorkspaceResolver
from abaqus_sim_agent.evaluation.behavior_measurement import file_digest
from abaqus_sim_agent.evaluation.mechanics_grading import (
    build_mechanics_dataset,
    record_observation,
)
from abaqus_sim_agent.evaluation.mechanics_progress import progress_lifecycle
from abaqus_sim_agent.evaluation.mechanics_submissions import (
    SUBMISSION_POLICY,
    MechanicsSubmissionVerifier,
)
from abaqus_sim_agent.evaluation.mechanics_telemetry import trace_attempt
from abaqus_sim_agent.evaluation.public_mechanics_cases import (
    BASIC_LINEAR_CASE_IDS,
    PUBLIC_MECHANICS_PROTOCOL,
    get_case,
    load_frozen_cases,
    selected_case_ids,
)
from abaqus_sim_agent.evaluation.public_mechanics_control import (
    ObservedTransport,
    append_event,
    build_report,
    build_report_async,
    read_case_observation,
    read_json,
    run_until_pause,
    save_json,
)
from abaqus_sim_agent.evaluation.public_mechanics_runner import PublicMechanicsRunner
from abaqus_sim_agent.evaluation.serial_runtime import SerialRuntime
from abaqus_sim_agent.execution import (
    DEFAULT_ABAQUS_2024_LAUNCHER,
    Abaqus2024Runner,
    WindowsProcessStrategy,
)
from abaqus_sim_agent.skills import skill_library_path
from abaqus_sim_agent.verification.public_mechanics_run import (
    VERIFICATION_POLICY,
    PublicMechanicsRunVerifier,
)

ROOT = Path(__file__).parents[3]
VERSION = PUBLIC_MECHANICS_PROTOCOL


def _eval_api_catalog_binding(
    catalog: ApiCatalog | None,
) -> ApiCatalogBinding | None:
    if catalog is None:
        return None
    manifest = catalog.manifest
    if manifest is None:
        raise ValueError("persisted API catalog is missing its manifest")
    return ApiCatalogBinding(
        schema_version=manifest.schema_version,
        cards_sha256=manifest.cards_sha256,
        card_count=manifest.card_count,
        member_count=manifest.member_count,
        members_sha256=manifest.members_sha256,
        runtime_release=manifest.runtime_release,
    )


def _write(path, value):
    with path.open("x", encoding="utf-8") as f:
        json.dump(value, f, ensure_ascii=False, indent=2)


def _files(path):
    return {
        p.relative_to(path).as_posix(): file_digest(p)
        for p in path.rglob("*")
        if p.is_file() and "__pycache__" not in p.parts
    }


def prepare(
    root: Path,
    catalog: Path,
    case_ids: list[str] | None = None,
    foundation: bool = True,
    *,
    api_catalog_mode: Literal["full", "full_with_discovery"] = "full",
    knowledge_resources: Path | None = None,
    max_task_concurrency: int = 1,
    max_runtime_concurrency: int = 1,
    http_timeout_seconds: float = 300.0,
    http_retries: int = 4,
):
    if root.exists():
        raise FileExistsError(root)
    if knowledge_resources is not None:
        raise ValueError(
            "v31 does not attach external reference folders; their benchmark answers are not audited"
        )
    case_ids = list(selected_case_ids(case_ids))
    cases = [get_case(k) for k in case_ids]
    ApiCatalog.open(catalog)
    if max_task_concurrency not in (1, 2):
        raise ValueError("max_task_concurrency must be 1 or 2")
    if max_runtime_concurrency not in (1, 2):
        raise ValueError("max_runtime_concurrency must be 1 or 2")
    if max_runtime_concurrency > max_task_concurrency:
        raise ValueError("runtime concurrency cannot exceed task concurrency")
    root.mkdir(parents=True)
    snap = root / "snapshot"
    snap.mkdir()
    shutil.copytree(
        ROOT / "src",
        snap / "src",
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )
    for name in ("pyproject.toml", "uv.lock"):
        shutil.copy2(ROOT / name, snap / name)
    shutil.copytree(
        catalog, root / "catalog", ignore=shutil.ignore_patterns("__pycache__")
    )
    _write(root / "cases.json", [c.model_dump(mode="json") for c in cases])
    _write(root / "goals.json", {c.case_id: c.goal() for c in cases})
    config = new_run_execution_config(
        model_provider=DeepSeekGateway.OPENCODE_GO,
        model_name=DEEPSEEK_V4_FLASH,
        planning_enabled=False,
        http_timeout_seconds=http_timeout_seconds,
        http_retries=http_retries,
    )
    _write(
        root / "manifest.json",
        {
            "version": VERSION,
            "batch_id": uuid4().hex[:10],
            "created_at": datetime.now(UTC).isoformat(),
            "case_ids": case_ids,
            "scoring_case_ids": list(BASIC_LINEAR_CASE_IDS),
            "foundation": foundation,
            "execution_config": config.model_dump(mode="json"),
            "catalog_mode": api_catalog_mode,
            "knowledge_resources": None,
            "docs_mode": "off",
            "question_protocol": "legacy_decision",
            "repetitions": 1,
            "max_explicit_resumes": 3,
            "provider_failure_policy": "stop_batch",
            "verification_policy": VERIFICATION_POLICY,
            "submission_policy": SUBMISSION_POLICY,
            "purpose": (
                "knowledge_and_api_profile_qualification"
                if api_catalog_mode == "full_with_discovery"
                else "case qualification, not Foundation A/B benefit"
            ),
            "max_task_concurrency": max_task_concurrency,
            "max_runtime_concurrency": max_runtime_concurrency,
            "budgets": {
                c.case_id: c.run_budget().model_dump(mode="json") for c in cases
            },
        },
    )
    worker = root / "worker.py"
    worker.write_text(
        "import sys\nfrom pathlib import Path\nsys.dont_write_bytecode=True\nsys.path.insert(0,str(Path(__file__).resolve().parent/'snapshot/src'))\nfrom abaqus_sim_agent.evaluation.public_mechanics_suite import main\nmain()\n",
        encoding="utf-8",
    )
    _write(
        root / "seal.json",
        {
            "files": _files(root),
            "runtime_files": {
                str(path): file_digest(path)
                for name in (
                    "pydantic-ai-harness",
                    "pydantic-ai-slim",
                    "pydantic-evals",
                    "pydantic",
                    "pydantic-core",
                    "openai",
                    "httpx",
                    "httpx2",
                    "httpcore",
                    "anyio",
                    "sniffio",
                    "typing-extensions",
                )
                for item in (distribution(name).files or [])
                if (
                    path := Path(str(distribution(name).locate_file(item))).resolve()
                ).is_file()
                and path.suffix.lower() in {".py", ".pyd", ".dll"}
            },
            "python_executable_sha256": file_digest(Path(sys.executable)),
            "abaqus_launcher_sha256": file_digest(DEFAULT_ABAQUS_2024_LAUNCHER),
            "installed_versions": {
                d.metadata["Name"]: d.version for d in distributions()
            },
            "python_version": sys.version,
        },
    )


def verify_binding(root):
    seal = json.loads((root / "seal.json").read_bytes())
    if ROOT.resolve() != (root / "snapshot").resolve():
        raise ValueError("run must use frozen snapshot worker")
    if sys.version != seal["python_version"]:
        raise ValueError("Python changed")
    if (
        file_digest(Path(sys.executable)) != seal["python_executable_sha256"]
        or file_digest(DEFAULT_ABAQUS_2024_LAUNCHER) != seal["abaqus_launcher_sha256"]
    ):
        raise ValueError("runtime executable/launcher changed")
    if {d.metadata["Name"]: d.version for d in distributions()} != seal[
        "installed_versions"
    ]:
        raise ValueError("installed versions changed")
    for name, sha in seal["files"].items():
        if file_digest(root / name) != sha:
            raise ValueError(f"bound input changed: {name}")
    for name, sha in seal["runtime_files"].items():
        if file_digest(Path(name)) != sha:
            raise ValueError(f"runtime library changed: {name}")
    manifest = read_json(root / "manifest.json")
    if manifest.get("verification_policy") != VERIFICATION_POLICY:
        raise ValueError(
            "verification policy is not the frozen declared-results policy"
        )
    if manifest.get("submission_policy") != SUBMISSION_POLICY:
        raise ValueError("submission policy differs from frozen worker")
    if manifest["version"] != VERSION:
        raise ValueError("use the original worker for historical versions")
    if manifest.get("catalog_mode") not in {"full", "full_with_discovery"}:
        raise ValueError("unsupported catalog mode")
    if manifest.get("max_task_concurrency") not in (1, 2):
        raise ValueError("invalid task concurrency")
    if manifest.get("max_runtime_concurrency") not in (1, 2):
        raise ValueError("invalid runtime concurrency")
    if manifest["max_runtime_concurrency"] > manifest["max_task_concurrency"]:
        raise ValueError("runtime concurrency exceeds task concurrency")
    if manifest.get("knowledge_resources") is not None:
        raise ValueError("v31 does not attach external reference folders")
    if read_json(root / "goals.json") != {
        name: get_case(name).goal() for name in manifest["case_ids"]
    }:
        raise ValueError("visible goals differ from frozen contracts")
    saved = load_frozen_cases(root, manifest["case_ids"])
    if saved != tuple(get_case(name) for name in manifest["case_ids"]):
        raise ValueError("serialized contracts differ from frozen executable contracts")
    if manifest.get("scoring_case_ids") != list(BASIC_LINEAR_CASE_IDS):
        raise ValueError("scoring case selection differs from frozen worker")


async def preflight(root: Path):
    """只检查真实工厂的工具与合同装配，不调用 Provider 或 Abaqus。"""
    from pydantic_ai.messages import ModelResponse, ToolCallPart
    from pydantic_ai.models.function import FunctionModel

    verify_binding(root)
    manifest = json.loads((root / "manifest.json").read_bytes())
    catalog = ApiCatalog.open(root / "catalog")
    c = get_case(manifest["case_ids"][0])
    w = root / "preflight" / "workspace"
    s = w.parent / "state"
    w.mkdir(parents=True)
    s.mkdir()
    req = CodingRunRequest(
        run_id="public-mechanics-preflight",
        goal=c.goal(),
        workspace=w,
        state_dir=s,
        runtime=RuntimeProfile(launcher_path=DEFAULT_ABAQUS_2024_LAUNCHER.resolve()),
        api_catalog=_eval_api_catalog_binding(catalog),
        **c.run_budget().as_request_controls(),
    )
    observed = {}

    def fake(messages, info):
        observed["tools"] = sorted(t.name for t in info.function_tools)
        observed["capabilities"] = sorted(
            info.model_request_parameters.deferred_capability_ids or []
        )
        output = next(
            t for t in info.output_tools if t.name.endswith("NeedsInputDecision")
        )
        return ModelResponse(
            parts=[
                ToolCallPart(
                    output.name,
                    {
                        "status": "needs_input",
                        "summary": "Preflight schema only, not a task result",
                        "clarification": {
                            "field_id": "fixture",
                            "kind": "missing_physical_input",
                            "question": "Fixture?",
                            "impact": "No real task is executed.",
                        },
                    },
                )
            ]
        )

    config = ModelExecutionConfig.model_validate_json(
        json.dumps(manifest["execution_config"])
    )
    agent = create_abaqus_coding_agent(
        FunctionModel(fake),
        req,
        skill_library=skill_library_path(),
        api_catalog_mode=manifest["catalog_mode"],
        execution_config=config,
        foundation_skill_profile="linear_structural_fea_v1"
        if manifest["foundation"]
        else "off",
    )
    await agent.run(
        req.goal,
        deps=CodingAgentDependencies(
            request=req, runner=Abaqus2024Runner(w, s), api_catalog=catalog
        ),
    )
    if (
        "lookup_abaqus_api" not in observed["tools"]
        or "run_command" in observed["tools"]
        or any("shell" in t.lower() for t in observed["tools"])
    ):
        raise ValueError("unexpected tools")
    expected_discovery = manifest["catalog_mode"] == "full_with_discovery"
    if ("discover_abaqus_api" in observed["tools"]) != expected_discovery:
        raise ValueError("API discovery exposure mismatch")
    expected_knowledge = manifest.get("knowledge_resources") is not None
    knowledge_tools = {"knowledge_list_directory", "knowledge_read_file"}
    if knowledge_tools.issubset(observed["tools"]) != expected_knowledge:
        raise ValueError("knowledge tool exposure mismatch")
    if not {"run_cae_script", "run_solver_job", "run_abaqus_python"} <= set(
        observed["tools"]
    ):
        raise ValueError("runtime tools missing")
    if len(observed["capabilities"]) != (6 if manifest["foundation"] else 1):
        raise ValueError("capability exposure mismatch")
    _write(
        root / "preflight.json",
        {
            "passed": True,
            "observed": observed,
            "real_model_calls": 0,
            "abaqus_calls": 0,
            "seal_sha256": file_digest(root / "seal.json"),
        },
    )


def _config(root):
    return ModelExecutionConfig.model_validate_json(
        json.dumps(read_json(root / "manifest.json")["execution_config"])
    )


def _checked_preflight(root):
    verify_binding(root)
    checked = read_json(root / "preflight.json")
    if (
        not checked
        or not checked["passed"]
        or checked["seal_sha256"] != file_digest(root / "seal.json")
    ):
        raise ValueError("missing bound preflight")


def _ensure_health(root):
    result = read_json(root / "health.json")
    if (
        not result
        or not result["passed"]
        or result["seal_sha256"] != file_digest(root / "seal.json")
    ):
        raise ValueError("run doctor first; no successful bound provider check")
    age = (datetime.now(UTC) - datetime.fromisoformat(result["at"])).total_seconds()
    if not 0 <= age <= 600:
        raise ValueError("provider check expired; run doctor again")


async def doctor(root: Path):
    """一次小型真实 Provider 检查；不调用文件工具或 Abaqus，不构成能力评测。"""
    from pydantic_ai import Agent

    _checked_preflight(root)
    config = _config(root)
    key = take_gateway_api_key_from_environment(DeepSeekGateway.OPENCODE_GO)
    check_id = uuid4().hex
    path = root / "health" / (check_id + ".json")
    client = None
    http_client = None
    result = {
        "at": datetime.now(UTC).isoformat(),
        "passed": False,
        "seal_sha256": file_digest(root / "seal.json"),
        "abaqus_calls": 0,
        "probe_id": check_id,
        "scope": "small transport probe, not long-task availability",
    }
    try:
        if config.http_retries is None:
            raise ValueError(
                "This frozen transport probe requires explicit SDK retries"
            )
        http_client = httpx.AsyncClient(
            transport=ObservedTransport(root / "health" / (check_id + ".jsonl"))
        )
        model, client = create_deepseek_chat_model(
            gateway=config.model_provider,
            model_name=config.model_name,
            api_key=key,
            timeout_seconds=config.http_timeout_seconds,
            http_retries=config.http_retries,
            execution_config=config,
            conversation_id="public-mechanics-health-" + check_id,
            http_client=http_client,
        )
        async with asyncio.timeout(
            config.http_timeout_seconds * (config.http_retries + 1) + 30
        ):
            response = await Agent(model, model_settings=config.model_settings()).run(
                "Return exactly OK. Connectivity check only."
            )
        result.update(
            passed=response.output.strip() == "OK",
            received_usage={
                "requests": response.usage.requests,
                "input_tokens": response.usage.input_tokens,
                "output_tokens": response.usage.output_tokens,
            },
        )
    except Exception as exc:
        result["error_type"] = type(exc).__name__
    finally:
        try:
            if client is not None:
                await client.close()
        except Exception as exc:
            result["client_close_error_type"] = type(exc).__name__
            result["passed"] = False
        finally:
            if http_client is not None:
                try:
                    await http_client.aclose()
                except Exception as exc:
                    result["transport_close_error_type"] = type(exc).__name__
                    result["passed"] = False
        result["at"] = datetime.now(UTC).isoformat()
        save_json(path, result)
        save_json(root / "health.json", result)
    print(json.dumps(result, ensure_ascii=True), flush=True)
    return result


def _request(root, name, catalog):
    w = root / name / "workspace"
    s = root / name / "state"
    manifest = read_json(root / "manifest.json")
    return CodingRunRequest(
        run_id="pme2e-v5-" + manifest["batch_id"] + "-" + name,
        goal=read_json(root / "goals.json")[name],
        workspace=w,
        state_dir=s,
        runtime=RuntimeProfile(launcher_path=DEFAULT_ABAQUS_2024_LAUNCHER.resolve()),
        api_catalog=_eval_api_catalog_binding(catalog),
        **CodingRunBudget.model_validate(
            manifest["budgets"][name]
        ).as_request_controls(),
    )


def selected_cases(root, action, case_ids):
    manifest = read_json(root / "manifest.json")
    names = manifest["case_ids"]
    if case_ids and (
        len(set(case_ids)) != len(case_ids) or not set(case_ids) <= set(names)
    ):
        raise ValueError("invalid selected cases")
    if action == "run":
        if (root / "started.json").exists():
            raise FileExistsError("no implicit rerun; use continue or explicit resume")
        if case_ids:
            raise ValueError("run uses all frozen cases")
        return names
    if not (root / "started.json").exists():
        raise ValueError("batch has not started")
    if action == "continue":
        if case_ids:
            raise ValueError("continue selects only never-started cases")
        return [n for n in names if not (root / n).exists()]
    if action != "resume" or not case_ids:
        raise ValueError("resume requires explicit --case")
    for name in case_ids:
        state = root / name / "state"
        resume = read_json(state / "resume_state.json")
        if (
            (state / "outcome.json").exists()
            or not resume
            or resume["status"] not in {"interrupted", "running"}
        ):
            raise ValueError(
                "only interrupted/nonterminal runs can be resumed; reconcile remains blocked"
            )
        if (
            resume["active_wall_time_seconds"]
            >= get_case(name).run_budget().wall_time_seconds
        ):
            raise ValueError("no wall budget remaining")
        attempts = [read_json(p) for p in (state / "attempts").glob("*.json")]
        if (
            sum(a["mode"] == "resume" for a in attempts)
            >= manifest["max_explicit_resumes"]
        ):
            raise ValueError("explicit resume allowance exhausted")
    return case_ids


async def _run_case(root, name, *, resume, key, catalog, runtime_gate=None):
    manifest = read_json(root / "manifest.json")
    config = _config(root)
    w, s = root / name / "workspace", root / name / "state"
    w.mkdir(parents=True, exist_ok=resume)
    s.mkdir(exist_ok=resume)
    attempt_path = (
        s / "attempts" / f"{len(list((s / 'attempts').glob('*.json'))) + 1:03}.json"
    )
    attempt = {
        "mode": "resume" if resume else "new",
        "started_at": datetime.now(UTC).isoformat(),
        "finished_at": None,
        "error_type": None,
    }
    save_json(attempt_path, attempt)
    with trace_attempt(
        root, name, manifest["batch_id"], attempt_path, attempt, get_case(name).goal()
    ):
        runner = PublicMechanicsRunner(
            w,
            s,
            process_strategy=(
                SerialRuntime(runtime_gate, WindowsProcessStrategy())
                if runtime_gate is not None
                else None
            ),
        )
        client = None
        http_client = None
        try:
            req = _request(root, name, catalog)
            telemetry.set_attributes(**{"session.id": req.run_id, "run_id": req.run_id})
            http_client = httpx.AsyncClient(
                transport=ObservedTransport(s / "transport.jsonl")
            )
            model, client = create_deepseek_chat_model(
                gateway=config.model_provider,
                model_name=config.model_name,
                api_key=key,
                timeout_seconds=config.http_timeout_seconds,
                http_retries=config.http_retries,
                execution_config=config,
                conversation_id=req.run_id,
                http_client=http_client,
            )
            deps = CodingAgentDependencies(
                request=req, runner=runner, api_catalog=catalog
            )
            agent = create_abaqus_coding_agent(
                model,
                req,
                skill_library=skill_library_path(),
                api_catalog_mode=manifest["catalog_mode"],
                execution_config=config,
                foundation_skill_profile="linear_structural_fea_v1"
                if manifest["foundation"]
                else "off",
                task_instructions="Complete the actual simulation and artifacts under the stated physical contract. Do not submit jobs inside the CAE script. Use API discovery, exact lookup, or optional reference material only when they resolve a real implementation uncertainty. No physical input is missing. Preserve the declared model/units/QoI and use the independent verifier feedback for in-scope repair.",
            )
            verifier = MechanicsSubmissionVerifier(
                req,
                PublicMechanicsRunVerifier(
                    get_case(name), policy=manifest["verification_policy"]
                ),
            )
            service = CodingRunService(
                agent=agent,
                request=req,
                dependencies=deps,
                clarification_provider=ScriptedClarificationProvider({}),
                verifier=verifier,
                verification_started=verifier.capture,
                resume=resume,
                initial_snapshot=load_initial_snapshot(s)
                if resume
                else WorkspaceResolver(captured_cwd=w).snapshot(w),
            )
            await run_until_pause(service, root / "pause.json")
        except Exception as exc:
            attempt["error_type"] = type(exc).__name__
            # Deliberately exclude arbitrary exception strings/headers that may contain credentials.
            attempt["cause_type"] = (
                type(exc.__cause__).__name__ if exc.__cause__ else None
            )
        except asyncio.CancelledError:
            attempt["error_type"] = "CancelledError"
            raise
        finally:
            if client is not None:
                try:
                    await client.close()
                except Exception as exc:
                    attempt["client_close_error_type"] = type(exc).__name__
            if http_client is not None:
                try:
                    await http_client.aclose()
                except Exception as exc:
                    attempt["transport_close_error_type"] = type(exc).__name__
            attempt["resume_state_snapshot"] = read_json(s / "resume_state.json")
            attempt["outcome_snapshot"] = read_json(s / "outcome.json")
            process_strategy = getattr(runner, "_process_strategy", None)
            if runtime_gate is not None and isinstance(process_strategy, SerialRuntime):
                attempt["runtime_queue_wait_seconds"] = process_strategy.wait_seconds
            attempt["finished_at"] = datetime.now(UTC).isoformat()
            save_json(attempt_path, attempt)


async def run(root: Path, action="run", case_ids=None):
    """由原生Dataset调度Case，恢复与副作用仍交给现有单题Service。

    Note:
        单题取消会取消整个本次调用并等待在途任务收尾；完成的Case不重新执行。
        正常结束保留原生评分失败；取消或绑定变化时只读重建全题集报告。
    """
    _checked_preflight(root)
    with CodingRunFileLock(root / "control"):
        manifest = read_json(root / "manifest.json")
        names = selected_cases(root, action, case_ids)
        _ensure_health(root)
        key = take_gateway_api_key_from_environment(DeepSeekGateway.OPENCODE_GO)
        if not key.get_secret_value():
            raise ValueError("provider credential unavailable")
        catalog = ApiCatalog.open(root / "catalog")
        task_concurrency = manifest["max_task_concurrency"]
        runtime_gate = asyncio.Semaphore(manifest["max_runtime_concurrency"])
        if action == "run":
            _write(
                root / "started.json",
                {
                    "at": datetime.now(UTC).isoformat(),
                    "seal_sha256": file_digest(root / "seal.json"),
                },
            )
        # Explicit continue/resume acknowledges a prior cooperative pause; archive the request.
        if action != "run" and (root / "pause.json").exists():
            (root / "pause.json").rename(
                root / ("pause-consumed-" + uuid4().hex + ".json")
            )
        append_event(
            root / "control/events.jsonl",
            "batch_started",
            action=action,
            cases=names,
            pid=os.getpid(),
        )
        cases = load_frozen_cases(root, manifest["case_ids"])
        by_id = {case.case_id: case for case in cases}
        selected = set(names)
        scoring_ids = tuple(manifest["scoring_case_ids"])
        invocation_id = uuid4().hex
        native = None
        stopped = False
        batch_task = asyncio.current_task()

        def stop_batch():
            nonlocal stopped
            stopped = True
            save_json(
                root / "health.json",
                {
                    "passed": False,
                    "at": datetime.now(UTC).isoformat(),
                    "seal_sha256": file_digest(root / "seal.json"),
                    "reason": "batch_interrupted",
                },
            )

        async def execute_or_observe(case_id: str):
            execute = (
                case_id in selected
                and not stopped
                and not (root / "pause.json").exists()
            )
            if execute:
                try:
                    verify_binding(root)
                    print("START " + case_id, flush=True)
                    await _run_case(
                        root,
                        case_id,
                        resume=action == "resume",
                        key=key,
                        catalog=catalog,
                        runtime_gate=runtime_gate,
                    )
                except asyncio.CancelledError:
                    stop_batch()
                    # Propagate child cancellation to the owner: Evals 2.51 must not treat a cancelled child as a result.
                    if batch_task is not None and not batch_task.cancelling():
                        batch_task.cancel()
                    raise
                except Exception as exc:
                    stop_batch()
                    # Keep exceptions outside the Service boundary visible without leaking arbitrary details.
                    raise RuntimeError(
                        f"Case execution failed: {type(exc).__name__}"
                    ) from None
            try:
                observation = read_case_observation(root, by_id[case_id], manifest)
            except Exception as exc:
                stop_batch()
                raise RuntimeError(
                    f"Case evidence failed: {type(exc).__name__}"
                ) from None
            if execute:
                print("END " + case_id + " " + observation.status, flush=True)
                if observation.status not in {"completed", "task_failed"}:
                    stop_batch()
            return record_observation(
                observation,
                included_in_score=case_id in scoring_ids,
                duration_scope="case_execution_and_observation"
                if execute
                else "saved_result_projection_only",
            )

        try:
            dataset = build_mechanics_dataset(
                cases, scoring_ids, name="basic-linear-case-execution"
            )
            native = await dataset.evaluate(
                execute_or_observe,
                name=f"{manifest['batch_id']}-{action}-{invocation_id}",
                max_concurrency=task_concurrency,
                progress=False,
                retry_task=None,
                retry_evaluators=None,
                repeat=1,
                lifecycle=progress_lifecycle(
                    root,
                    tuple(by_id),
                    tuple(names),
                    invocation_id=invocation_id,
                    action=action,
                ),
                metadata={
                    "protocol": manifest["version"],
                    "root": str(root),
                    "action": action,
                    "selected_case_ids": names,
                    "scoring_case_ids": list(scoring_ids),
                    "invocation_id": invocation_id,
                },
            )
            # Preserve this invocation's original task/evaluator failures even if later report rebuilding changes views.
            save_json(
                root / "control" / f"evaluation-{invocation_id}.json",
                json.loads(EvaluationReportAdapter.dump_json(native)),
            )
        finally:
            unchanged = False
            try:
                try:
                    verify_binding(root)
                    unchanged = True
                finally:
                    await build_report_async(
                        root,
                        bindings_unchanged=unchanged,
                        native_report=native if unchanged else None,
                    )
            finally:
                append_event(
                    root / "control/events.jsonl",
                    "batch_stopped",
                    action=action,
                    evaluation_invocation_id=invocation_id,
                )


def launch(root, action, cases, *, phoenix=False):
    _checked_preflight(root)
    with CodingRunFileLock(root / "control"):
        selected_cases(root, action, cases)
        _ensure_health(root)
        launch_id = uuid4().hex
        logs = root / "control" / launch_id
        logs.mkdir()
        command = [
            sys.executable,
            "-I",
            str(root / "worker.py"),
            action,
            "--root",
            str(root),
        ]
        if phoenix:
            command.append("--phoenix")
        for name in cases or []:
            command.extend(["--case", name])
    flags = (
        (
            subprocess.CREATE_NO_WINDOW
            | subprocess.DETACHED_PROCESS
            | subprocess.CREATE_NEW_PROCESS_GROUP
        )
        if os.name == "nt"
        else 0
    )
    with (
        (logs / "stdout.log").open("wb") as out,
        (logs / "stderr.log").open("wb") as err,
    ):
        process = subprocess.Popen(
            command,
            cwd=root,
            stdin=subprocess.DEVNULL,
            stdout=out,
            stderr=err,
            creationflags=flags,
            close_fds=True,
        )
    save_json(
        logs / "launch.json",
        {
            "pid": process.pid,
            "command": command,
            "at": datetime.now(UTC).isoformat(),
            "note": "Independent worker; PID is informational, pause via marker.",
        },
    )
    print(json.dumps({"pid": process.pid, "logs": str(logs)}), flush=True)


def report(root: Path):
    if read_json(root / "manifest.json")["version"] != VERSION:
        raise ValueError("historical reports must not be overwritten by this worker")
    with CodingRunFileLock(root / "control"):
        unchanged = False
        try:
            verify_binding(root)
            unchanged = True
        finally:
            build_report(root, bindings_unchanged=unchanged)


def pause(root: Path):
    verify_binding(root)
    save_json(
        root / "pause.json",
        {"at": datetime.now(UTC).isoformat(), "reason": "operator_requested_pause"},
    )


def main():
    from abaqus_sim_agent.evaluation.public_mechanics_cli import main as cli_main

    cli_main()


if __name__ == "__main__":
    main()
