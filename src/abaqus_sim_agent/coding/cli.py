from __future__ import annotations

import argparse
import asyncio
import getpass
import io
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, cast
from uuid import uuid4

from pydantic import SecretStr

from abaqus_sim_agent.api_catalog import ApiCatalog
from abaqus_sim_agent.coding.amendments import (
    CodingRunAmendmentLedger,
    CodingRunConfigPatch,
    RuntimeExecutionPolicyPatch,
    append_operational_amendment,
    append_task_revision,
)
from abaqus_sim_agent.coding.api_lookup import (
    DEFAULT_PRODUCT_API_CATALOG_MODE,
    ProductApiCatalogMode,
    bind_api_catalog,
    load_product_api_catalog,
)
from abaqus_sim_agent.coding.clarification import (
    ConsoleClarificationProvider,
    ScriptedClarificationProvider,
)
from abaqus_sim_agent.coding.composition import open_coding_run
from abaqus_sim_agent.coding.concept_lookup import (
    ConceptRetrievalMode,
    concept_index_binding,
    open_concept_index,
)
from abaqus_sim_agent.coding.context_policy import (
    bind_filesystem_skills,
)
from abaqus_sim_agent.coding.contracts import (
    ApiCatalogBinding,
    CodingRunRequest,
    CodingRunStatus,
    ConceptIndexBinding,
    LimitedDuration,
    RuntimeAllowed,
    RuntimeAllowedWithLimit,
    RuntimeDenied,
    RuntimeExecutionPolicy,
    RuntimeScriptPolicy,
)
from abaqus_sim_agent.coding.credential_store import CredentialStore
from abaqus_sim_agent.coding.deferred_runtime import WaitingForToolExecution
from abaqus_sim_agent.coding.diagnostic_retrieval import DiagnosticRetrievalMode
from abaqus_sim_agent.coding.document_policy_binding import (
    DocumentPolicyBinding,
    load_document_policy_spec,
)
from abaqus_sim_agent.coding.domain_knowledge import (
    discover_domain_knowledge_packs,
    prepare_domain_knowledge_bundle,
)
from abaqus_sim_agent.coding.edit_scope import ReferenceEditScope
from abaqus_sim_agent.coding.execution_verifier import ExecutionEvidenceVerifier
from abaqus_sim_agent.coding.launch import LaunchManifest as _LaunchManifest
from abaqus_sim_agent.coding.launch import apply_launch_amendments
from abaqus_sim_agent.coding.model import (
    read_gateway_api_keys_from_environment,
    take_gateway_api_key_from_environment,
)
from abaqus_sim_agent.coding.model_connections import (
    create_model as create_deepseek_chat_model,
)
from abaqus_sim_agent.coding.model_selection import (
    available_models,
    require_model_selection,
    select_model,
)
from abaqus_sim_agent.coding.option_arguments import (
    add_run_arguments,
    options_from_args,
)
from abaqus_sim_agent.coding.options import resolve_run_config
from abaqus_sim_agent.coding.providers import ModelProvider as DeepSeekGateway
from abaqus_sim_agent.coding.resume import (
    load_effective_run_for_resume,
)
from abaqus_sim_agent.coding.static_preflight import (
    prepare_static_preflight_source as _prepare_static_preflight_source,
)
from abaqus_sim_agent.coding.user_questions import (
    UserAnswerInput,
    UserQuestion,
    WaitingForUser,
    cancel_question,
    inspect_question,
    submit_answer,
)
from abaqus_sim_agent.coding.workspace import WorkspaceResolver
from abaqus_sim_agent.execution import (
    Abaqus2024Runner,
)
from abaqus_sim_agent.retrieval import AbaqusDocumentIndex
from abaqus_sim_agent.telemetry import add_phoenix_argument, telemetry_session


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="abaqus-sim-agent",
        description="Run one Abaqus 2024 coding task in the selected workspace.",
    )
    parser.add_argument("--goal", help="Natural-language coding goal")
    authentication = parser.add_mutually_exclusive_group()
    authentication.add_argument(
        "--login",
        type=DeepSeekGateway,
        choices=list(DeepSeekGateway),
        help="登录或保存 API Key，供 CLI/Web 共用",
    )
    authentication.add_argument(
        "--logout",
        type=DeepSeekGateway,
        choices=list(DeepSeekGateway),
        help="删除本应用保存的提供商凭据",
    )
    parser.add_argument(
        "--oauth-client-id", help="仅用于 GitHub Copilot 设备登录的 OAuth 应用 ID"
    )
    parser.add_argument(
        "--list-models",
        action="store_true",
        help="列出凭据与模型元数据已就绪的可选模型",
    )
    parser.add_argument(
        "--list-model-providers", action="store_true", help="列出提供商与认证方式"
    )
    parser.add_argument(
        "--answer-file", type=Path, help="UTF-8 question_id/answer JSON for --resume"
    )
    parser.add_argument(
        "--cancel-question",
        metavar="QUESTION_ID",
        help="Explicitly cancel a pending question on --resume",
    )
    parser.add_argument(
        "--document-policy-spec",
        type=Path,
        help="Explicit strict document spec; its schema selects the document policy",
    )
    parser.add_argument(
        "--request-file",
        type=Path,
        help="UTF-8 text or JSON file containing a `goal` field",
    )
    parser.add_argument(
        "--resume",
        type=Path,
        metavar="STATE_DIR",
        help="Continue or revise a CodingRun from its state directory",
    )
    parser.add_argument(
        "--workspace",
        type=Path,
        help="Workspace root; defaults to the CLI startup directory",
    )
    parser.add_argument(
        "--state-dir",
        type=Path,
        help="State/evidence directory outside the Agent workspace",
    )
    parser.add_argument(
        "--answers",
        type=Path,
        help="JSON mapping used for non-interactive clarification answers",
    )
    parser.add_argument("--run-id", help="Stable ASCII run identifier")
    parser.add_argument(
        "--list-knowledge-packs",
        action="store_true",
        help="List private domain knowledge packs installed for this user and exit",
    )
    parser.add_argument(
        "--concept-index-dir",
        type=Path,
        help="Optional local Abaqus modeling-document index outside the workspace",
    )
    parser.add_argument(
        "--concept-retrieval-mode",
        choices=("off", "tool"),
        default=None,
        help=(
            "Concept documentation behavior; explicitly select tool together "
            "with --concept-index-dir to expose search_abaqus_docs"
        ),
    )
    parser.add_argument(
        "--diagnostic-retrieval-mode",
        choices=("off", "advisory"),
        default=None,
        help=(
            "Attach bounded RRF documentation evidence to eligible Solver "
            "diagnostics; requires --concept-index-dir"
        ),
    )
    parser.add_argument(
        "--allowed-cae-script",
        action="append",
        help="Repeatable production CAE Python path for the runtime allowlist",
    )
    parser.add_argument(
        "--allowed-abaqus-python-script",
        action="append",
        help="Repeatable production extraction Python path for the runtime allowlist",
    )
    parser.add_argument(
        "--allowed-solver-input",
        action="append",
        help="Repeatable production INP path for the runtime allowlist",
    )
    parser.add_argument(
        "--edit-scope-file",
        type=Path,
        help="Trusted single-reference-edit-v1 JSON; pure editing, no execution",
    )
    parser.add_argument(
        "--abaqus-permission",
        choices=("deny", "allow", "allow_with_limit"),
    )
    parser.add_argument("--abaqus-limit", type=int)
    parser.add_argument(
        "--solver-permission",
        choices=("deny", "allow", "allow_with_limit"),
    )
    parser.add_argument("--solver-limit", type=int)
    parser.add_argument("--cae-timeout-seconds", type=float)
    parser.add_argument("--abaqus-python-timeout-seconds", type=float)
    parser.add_argument("--solver-timeout-seconds", type=float)
    parser.add_argument(
        "--amendment-reason",
        help="Audit reason for resume-time configuration or task changes",
    )
    add_run_arguments(parser)
    add_phoenix_argument(parser)
    return parser


def main() -> None:
    _configure_cli_output()
    raise SystemExit(run_cli())


def _configure_cli_output(*streams: io.TextIOWrapper) -> None:
    """让重定向/宿主捕获的CLI文本使用UTF-8，交互式Windows Console保持原编码。"""
    selected = streams or tuple(
        stream
        for stream in (sys.stdout, sys.stderr)
        if isinstance(stream, io.TextIOWrapper)
    )
    for stream in selected:
        if isinstance(stream, io.TextIOWrapper) and not stream.isatty():
            stream.reconfigure(encoding="utf-8", errors="backslashreplace")


@dataclass
class _ConsoleQuestion:
    state_dir: Path
    question: UserQuestion
    api_key: SecretStr | None


def run_cli(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        with telemetry_session(args.phoenix, environment="cli"):
            return _run_cli_session(args)
    except (ValueError, RuntimeError) as exc:
        print(f"error: {exc}")
        return 1


def _authenticate_cli(args: argparse.Namespace) -> int:
    from .credential_store import CredentialStoreError
    from .model_login import LoginUnavailable, start_login
    from .providers import LOCAL_AUTH_PROVIDERS, require_provider_dependencies

    store = CredentialStore()
    if args.logout is not None:
        store.remove(args.logout)
        print("已删除本应用保存的凭据；环境变量和其他应用登录不受影响。")
        return 0
    provider = args.login
    require_provider_dependencies(provider)
    if provider is DeepSeekGateway.OPENAI_CODEX or (
        provider is DeepSeekGateway.GITHUB_COPILOT and args.oauth_client_id
    ):

        async def login() -> None:
            challenge = await start_login(
                provider, store, client_id=args.oauth_client_id
            )
            print(f"请打开登录页面：{challenge.url}")
            if challenge.user_code:
                print(f"授权码：{challenge.user_code}")
            async with asyncio.timeout(600):
                await challenge.complete()

        try:
            asyncio.run(login())
        except KeyboardInterrupt:
            print("登录已取消。")
            return 1
        except (CredentialStoreError, LoginUnavailable) as exc:
            print(f"error: {exc}")
            return 1
        except Exception:
            # SDK errors may contain tokens or remote response bodies.
            print("登录或凭据保存未完成，请检查网络、文件权限及登录信息后重试。")
            return 1
    elif provider in LOCAL_AUTH_PROVIDERS:
        raise ValueError("请使用该提供商的本机认证工具，或通过环境变量提供凭据。")
    else:
        try:
            key = SecretStr(getpass.getpass(f"{provider.value} API Key: "))
        except (EOFError, KeyboardInterrupt, OSError):
            print("凭据输入已取消，未保存。")
            return 1
        store.save_key(provider, key)
    print("凭据已保存，CLI/Web 可共用；使用 --list-models 查看候选模型。")
    return 0


def _run_cli_session(args: argparse.Namespace) -> int:
    if args.oauth_client_id and args.login is not DeepSeekGateway.GITHUB_COPILOT:
        raise ValueError("--oauth-client-id 只用于 --login github-copilot")
    if args.login is not None or args.logout is not None:
        if args.goal or args.resume or args.request_file:
            raise ValueError("登录/退出命令不能同时执行任务。")
        return _authenticate_cli(args)
    if args.list_models:
        print(
            json.dumps(
                [m.model_dump(mode="json") for m in available_models()],
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    if args.list_model_providers:
        from .providers import provider_catalog

        print(
            json.dumps(
                [
                    p.model_dump(mode="json")
                    for p in provider_catalog(include_models=False)
                ],
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    if args.list_knowledge_packs:
        return _print_knowledge_packs()
    key = None
    while True:
        result = _run_cli_once(args, key)
        if isinstance(result, int):
            return result
        print(
            json.dumps(
                {
                    "status": "waiting_for_user",
                    "question_id": result.question.question_id,
                    "question": result.question.question,
                },
                ensure_ascii=False,
            )
        )
        try:
            answer = input("Answer (empty to leave pending): ").strip()
        except (EOFError, KeyboardInterrupt, OSError):
            return 2
        if not answer:
            return 2
        try:
            asyncio.run(
                submit_answer(
                    result.state_dir,
                    UserAnswerInput(
                        question_id=result.question.question_id, answer=answer
                    ),
                    source="console",
                )
            )
        except (OSError, ValueError, RuntimeError) as exc:
            print(f"error: {exc}")
            return 1
        key = result.api_key
        args = build_parser().parse_args(["--resume", str(result.state_dir)])


def _run_cli_once(
    args: argparse.Namespace, retained_api_key: SecretStr | None
) -> int | _ConsoleQuestion:
    try:
        resuming = args.resume is not None
        if not resuming and (
            args.answer_file is not None or args.cancel_question is not None
        ):
            raise ValueError("question answers/cancellation require --resume")
        if args.answer_file is not None and (
            args.cancel_question or args.goal or args.request_file
        ):
            raise ValueError(
                "submit an answer separately from cancellation or a new goal"
            )
        request: CodingRunRequest | None = None
        launch: _LaunchManifest | None = None
        execution = None
        settings = None
        fresh_goal: str | None = None
        fresh_run_id: str | None = None
        expected_amendment_sequence: int | None = None
        static_preflight_mode: Literal["off", "on"]
        if resuming:
            _validate_resume_arguments(args)
            assert args.resume is not None
            if (
                args.answers is not None
                and _load_launch_manifest(args.resume).question_protocol is not None
            ):
                raise ValueError(
                    "ask_user runs use --answer-file, not the legacy --answers mapping"
                )
            if args.cancel_question:
                asyncio.run(cancel_question(args.resume, args.cancel_question))
            if args.answer_file:
                answer = UserAnswerInput.model_validate_json(
                    args.answer_file.read_bytes()
                )
                asyncio.run(submit_answer(args.resume, answer))
            _append_requested_amendments(args, args.resume)
            if args.cancel_question and not (args.goal or args.request_file):
                return 0
            pending = asyncio.run(inspect_question(args.resume))
            if pending is not None:
                return _ConsoleQuestion(args.resume, pending, retained_api_key)
            effective = load_effective_run_for_resume(args.resume)
            request = effective.request
            expected_amendment_sequence = effective.amendment_sequence
            launch = _apply_launch_amendments(
                _load_launch_manifest(request.state_dir),
                effective.ledger,
            )
            resolved = WorkspaceResolver().resolve_interactive(
                run_id=request.run_id,
                workspace=request.workspace,
                state_dir=request.state_dir,
            )
            initial_snapshot = effective.initial_snapshot
            model_provider = launch.model_provider
            model_name = launch.model_name
            http_retries = launch.http_retries
            skill_library = launch.skill_library
            api_catalog_dir = launch.api_catalog_dir
            requested_api_catalog_mode = cast(
                ProductApiCatalogMode,
                launch.api_catalog_mode,
            )
            concept_index_dir = launch.concept_index_dir
            requested_concept_mode = launch.concept_retrieval_mode
            diagnostic_retrieval_mode = launch.diagnostic_retrieval_mode
            static_preflight_mode = launch.static_preflight_mode
            static_wheel = launch.abqpy_static_wheel
            static_cache_dir = launch.static_preflight_cache_dir
            document_binding = launch.document_policy
            if document_binding != request.document_policy:
                raise ValueError(
                    "launch document policy differs from immutable request"
                )
            document_policy_mode = document_binding.mode if document_binding else "off"
            domain_knowledge = launch.domain_knowledge
        else:
            fresh_goal = _resolve_goal(args.goal, args.request_file)
            fresh_run_id = args.run_id or f"run-{uuid4().hex[:12]}"
            assert fresh_run_id is not None
            resolved = WorkspaceResolver().resolve_interactive(
                run_id=fresh_run_id,
                workspace=args.workspace,
                state_dir=args.state_dir,
            )
            initial_snapshot = resolved.initial_snapshot
            print(f"Workspace: {resolved.workspace}")
            print(f"State: {resolved.state_dir}")
            require_model_selection(args.model_provider, args.model)
            keys = CredentialStore().api_keys(read_gateway_api_keys_from_environment())
            if retained_api_key is not None and args.model_provider is not None:
                keys[args.model_provider] = retained_api_key
            if (
                args.model_provider is not None
                and not keys[args.model_provider].get_secret_value()
                and sys.stdin.isatty()
            ):
                from .providers import LOCAL_AUTH_PROVIDERS

                if args.model_provider not in LOCAL_AUTH_PROVIDERS:
                    retained_api_key = SecretStr(
                        getpass.getpass(f"{args.model_provider.value} API Key: ")
                    )
                    CredentialStore().save_key(args.model_provider, retained_api_key)
                    keys[args.model_provider] = retained_api_key
            settings = resolve_run_config(
                resolved.workspace,
                options_from_args(args),
                models=available_models(keys),
            )
            execution = settings.execution
            model_provider = execution.model_provider
            model_name = execution.model_name
            http_retries = execution.http_retries
            skill_library = settings.skill_library
            api_catalog_dir = settings.api_catalog_dir
            requested_api_catalog_mode = settings.api_catalog_mode
            concept_index_dir = args.concept_index_dir
            if (
                args.document_policy_spec is not None
                and args.concept_retrieval_mode == "off"
            ):
                raise ValueError(
                    "document policy spec conflicts with concept retrieval off"
                )
            requested_concept_mode = cast(
                ConceptRetrievalMode,
                "tool"
                if args.document_policy_spec is not None
                else args.concept_retrieval_mode or "off",
            )
            diagnostic_retrieval_mode = cast(
                DiagnosticRetrievalMode,
                args.diagnostic_retrieval_mode or "off",
            )
            static_preflight_mode = settings.static_preflight_mode
            static_wheel = settings.abqpy_static_wheel
            static_cache_dir = settings.static_preflight_cache_dir
            document_policy_mode = (
                "bounded_v1" if args.document_policy_spec is not None else "off"
            )
            document_binding = None
            domain_knowledge = None

        if resuming:
            print(f"Workspace: {resolved.workspace}")
            print(f"State: {resolved.state_dir}")

        api_catalog = _load_api_catalog(api_catalog_dir)
        assert api_catalog_dir is not None
        api_catalog_mode = _resolve_api_catalog_mode(
            requested_api_catalog_mode,
            api_catalog,
        )
        if document_policy_mode == "bounded_v1":
            if requested_concept_mode != "tool":
                raise ValueError("bounded_v1 requires --concept-retrieval-mode tool")
            if concept_index_dir is not None and diagnostic_retrieval_mode == "off":
                raise ValueError(
                    "bounded documents use the policy spec; --concept-index-dir is only needed for separate diagnostic advisory"
                )
            concept_index = (
                _load_concept_index(concept_index_dir, workspace=resolved.workspace)
                if diagnostic_retrieval_mode != "off"
                else None
            )
            if diagnostic_retrieval_mode != "off" and concept_index is None:
                raise ValueError(
                    "diagnostic advisory requires its explicit concept index"
                )
            concept_retrieval_mode = "tool"
            if document_binding is None:
                assert args.document_policy_spec is not None
                document_binding = _bind_document_policy(args.document_policy_spec)
        else:
            concept_index = _load_concept_index(
                concept_index_dir, workspace=resolved.workspace
            )
            concept_retrieval_mode = _resolve_concept_retrieval_mode(
                requested_concept_mode, diagnostic_retrieval_mode, concept_index
            )
        static_preflight_source = _prepare_static_preflight_source(
            mode=static_preflight_mode,
            wheel=static_wheel,
            cache_dir=static_cache_dir,
            workspace=resolved.workspace,
        )
        if static_preflight_source is None:
            static_binding = None
        else:
            if static_preflight_mode == "off":
                raise AssertionError("off mode cannot prepare a static source")
            static_binding = static_preflight_source.binding()
        if resuming:
            assert request is not None
            if request.api_catalog != _api_catalog_binding(api_catalog):
                raise ValueError("resume API catalog does not match request.json")
            if request.concept_index != _concept_index_binding(concept_index):
                raise ValueError("resume concept index does not match request.json")
            if request.static_preflight != static_binding:
                raise ValueError(
                    "resume static preflight source does not match request.json"
                )
        else:
            assert fresh_run_id is not None
            assert fresh_goal is not None
            runtime_script_policy = _runtime_script_policy(
                allowed_cae_scripts=args.allowed_cae_script,
                allowed_abaqus_python_scripts=args.allowed_abaqus_python_script,
                allowed_solver_inputs=args.allowed_solver_input,
            )
            assert settings is not None
            runtime = settings.runtime
            request = CodingRunRequest(
                run_id=fresh_run_id,
                goal=fresh_goal,
                workspace=resolved.workspace,
                state_dir=resolved.state_dir,
                runtime=runtime,
                runtime_script_policy=runtime_script_policy,
                api_catalog=_api_catalog_binding(api_catalog),
                concept_index=_concept_index_binding(concept_index),
                document_policy=document_binding,
                static_preflight=static_binding,
                edit_scope=(
                    ReferenceEditScope.model_validate_json(
                        args.edit_scope_file.read_bytes()
                    )
                    if args.edit_scope_file is not None
                    else None
                ),
                runtime_policy=_effective_fresh_runtime_policy(args),
            )
            use_questions = not (
                args.answers
                or request.edit_scope
                or document_binding
                or request.concept_index
            )
            domain_knowledge = prepare_domain_knowledge_bundle(
                settings.knowledge_pack_ids,
                state_dir=request.state_dir,
                workspace=request.workspace,
            )
            schema_version = "coding-run-launch-v10"
            resolved_skill_library = skill_library.resolve(strict=True)
            assert execution is not None
            launch = _LaunchManifest(
                schema_version=schema_version,
                **({"question_protocol": "ask_user_v1"} if use_questions else {}),
                **(
                    {
                        "response_enabled": use_questions,
                        "domain_knowledge": domain_knowledge,
                        "capability_definitions": bind_filesystem_skills(
                            resolved_skill_library,
                            ("abaqus-2024-coding",),
                        ),
                        "context_policy_version": "model-window-tiered-summary-v1",
                    }
                ),
                document_policy=document_binding,
                model_provider=model_provider,
                model_name=model_name,
                http_retries=http_retries,
                **execution.model_dump(
                    mode="python",
                    exclude={"model_provider", "model_name", "http_retries"},
                ),
                skill_library=resolved_skill_library,
                api_catalog_dir=api_catalog_dir.resolve(strict=True),
                api_catalog_mode=api_catalog_mode,
                concept_index_dir=(
                    concept_index_dir.resolve(strict=True)
                    if concept_index_dir is not None
                    else None
                ),
                concept_retrieval_mode=concept_retrieval_mode,
                diagnostic_retrieval_mode=diagnostic_retrieval_mode,
                static_preflight_mode=static_preflight_mode,
                abqpy_static_wheel=(
                    static_wheel.resolve(strict=True)
                    if static_wheel is not None
                    else None
                ),
                static_preflight_cache_dir=(
                    static_cache_dir.resolve() if static_cache_dir is not None else None
                ),
            )

        assert request is not None
        assert launch is not None
        execution_config = launch.effective_execution(
            pure_edit=request.edit_scope is not None
        )
        clarification_provider = (
            ScriptedClarificationProvider(_load_answers(args.answers))
            if args.answers is not None
            else ConsoleClarificationProvider()
        )
        if not skill_library.is_dir():
            raise FileNotFoundError(f"skill library does not exist: {skill_library}")
        environment_key = take_gateway_api_key_from_environment(model_provider)
        api_key = retained_api_key or CredentialStore().api_keys(
            {model_provider: environment_key}
        ).get(model_provider, SecretStr(""))
        from .providers import LOCAL_AUTH_PROVIDERS, require_provider_dependencies

        require_provider_dependencies(model_provider)
        if (
            not api_key
            and model_provider not in LOCAL_AUTH_PROVIDERS
            and sys.stdin.isatty()
        ):
            api_key = SecretStr(getpass.getpass(f"{model_provider.value} API Key: "))
            CredentialStore().save_key(model_provider, api_key)
        verifier = _product_verifier()
        print(
            f"Model: {model_provider.value}/{model_name} "
            f"(http_retries={http_retries}, "
            f"http_timeout_seconds={execution_config.http_timeout_seconds:g}, "
            f"reasoning_policy={execution_config.reasoning_policy})"
        )
        print(f"Question protocol: {launch.question_protocol or 'legacy_decision'}")
        if domain_knowledge is not None:
            print(
                "Knowledge packs: "
                + ", ".join(
                    f"{pack.pack_id}@{pack.release}" for pack in domain_knowledge.packs
                )
            )

        async def execute() -> int | _ConsoleQuestion:
            async with open_coding_run(
                request,
                initial_snapshot,
                launch,
                api_key=api_key,
                verifier=verifier,
                clarification_provider=clarification_provider,
                resume=resuming,
                expected_amendment_sequence=expected_amendment_sequence,
                api_catalog=api_catalog,
                concept_index=concept_index,
                static_source=static_preflight_source,
                progress_sink=lambda message: print(f"[run] {message}"),
                model_factory=create_deepseek_chat_model,
                runner_factory=Abaqus2024Runner,
            ) as service:
                result = (
                    await service.run_step()
                    if launch.question_protocol
                    else await service.run()
                )
            if isinstance(result, WaitingForUser):
                return _ConsoleQuestion(request.state_dir, result.question, api_key)
            if isinstance(result, WaitingForToolExecution):
                raise RuntimeError(
                    "deferred Solver execution requires the Web controller"
                )
            print(result.outcome.model_dump_json(indent=2))
            return 0 if result.outcome.status is CodingRunStatus.COMPLETED else 1

        return asyncio.run(execute())
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"error: {exc}")
        return 1


def _validate_resume_arguments(args: argparse.Namespace) -> None:
    conflicts = {
        "--reasoning-policy": args.reasoning_policy,
        "--http-timeout-seconds": args.http_timeout_seconds,
        "--max-tokens": args.max_tokens,
        "--document-policy-spec": args.document_policy_spec,
        "--workspace": args.workspace,
        "--state-dir": args.state_dir,
        "--run-id": args.run_id,
        "--edit-scope-file": args.edit_scope_file,
        "--knowledge-pack": args.knowledge_pack,
        "--list-knowledge-packs": args.list_knowledge_packs or None,
        "--api-catalog-mode": args.api_catalog_mode,
        "--concept-index-dir": args.concept_index_dir,
        "--concept-retrieval-mode": args.concept_retrieval_mode,
        "--diagnostic-retrieval-mode": args.diagnostic_retrieval_mode,
        "--static-preflight-mode": args.static_preflight_mode,
        "--allowed-cae-script": args.allowed_cae_script,
        "--allowed-abaqus-python-script": args.allowed_abaqus_python_script,
        "--allowed-solver-input": args.allowed_solver_input,
    }
    supplied = [name for name, value in conflicts.items() if value is not None]
    if supplied:
        raise ValueError(
            "--resume keeps run identity and evidence bindings; remove: "
            + ", ".join(supplied)
        )


def _bind_document_policy(path: Path) -> DocumentPolicyBinding:
    from abaqus_sim_agent.evaluation.document_policy_adapter import bind_document_policy

    return bind_document_policy(load_document_policy_spec(path))


def _append_requested_amendments(
    args: argparse.Namespace,
    state_dir: Path,
) -> None:
    config = _config_patch_from_args(args)
    if config is not None and (
        config.model_provider is not None or config.model_name is not None
    ):
        effective = load_effective_run_for_resume(state_dir)
        launch = apply_launch_amendments(
            _load_launch_manifest(state_dir), effective.ledger
        )
        if launch.context_window is not None:
            values = {name: getattr(config, name) for name in config.model_fields_set}
            provider = config.model_provider or launch.model_provider
            name = config.model_name or launch.model_name
            if (provider, name) != (launch.model_provider, launch.model_name):
                values["context_window"] = select_model(
                    available_models(), provider, name
                ).context_window
            else:
                values["context_window"] = launch.context_window
            if (
                config.model_provider is not None
                and config.model_provider != launch.model_provider
            ):
                from .providers import connection_environment

                values["provider_environment"] = connection_environment(
                    config.model_provider
                )
            config = CodingRunConfigPatch.model_validate(values)
    goal_requested = args.goal is not None or args.request_file is not None
    if args.amendment_reason is not None and config is None and not goal_requested:
        raise ValueError("--amendment-reason requires a configuration or task change")
    reason = args.amendment_reason or "User requested a continuation change"
    if config is not None and not goal_requested:
        append_operational_amendment(
            state_dir,
            patch=config,
            reason=reason,
        )
    if not goal_requested:
        return
    append_task_revision(
        state_dir,
        goal=_resolve_goal(args.goal, args.request_file),
        reason=reason,
        config=config,
    )


def _config_patch_from_args(
    args: argparse.Namespace,
) -> CodingRunConfigPatch | None:
    values: dict[str, object] = {}
    runtime_policy = _runtime_policy_values(args)
    if runtime_policy:
        values["runtime_policy"] = RuntimeExecutionPolicyPatch.model_validate(
            runtime_policy
        )
    if args.model_provider is not None:
        values["model_provider"] = args.model_provider
    if args.model is not None:
        values["model_name"] = args.model
    if args.http_retries is not None:
        values["http_retries"] = args.http_retries
    if not values:
        return None
    return CodingRunConfigPatch.model_validate(values)


def _effective_fresh_runtime_policy(args: argparse.Namespace) -> RuntimeExecutionPolicy:
    values = _runtime_policy_values(args)
    if not values:
        return RuntimeExecutionPolicy()
    return RuntimeExecutionPolicyPatch.model_validate(values).apply(
        RuntimeExecutionPolicy()
    )


def _runtime_policy_values(args: argparse.Namespace) -> dict[str, object]:
    values: dict[str, object] = {}
    for name in ("abaqus", "solver"):
        permission = getattr(args, f"{name}_permission")
        limit = getattr(args, f"{name}_limit")
        if limit is not None:
            if permission not in {None, "allow_with_limit"}:
                raise ValueError(f"--{name}-limit conflicts with --{name}-permission")
            values[name] = RuntimeAllowedWithLimit(limit=limit)
        elif permission == "allow_with_limit":
            raise ValueError(
                f"--{name}-permission=allow_with_limit requires --{name}-limit"
            )
        elif permission == "allow":
            values[name] = RuntimeAllowed()
        elif permission == "deny":
            values[name] = RuntimeDenied()
    for field, argument in (
        ("cae_timeout", "cae_timeout_seconds"),
        ("abaqus_python_timeout", "abaqus_python_timeout_seconds"),
        ("solver_timeout", "solver_timeout_seconds"),
    ):
        seconds = getattr(args, argument)
        if seconds is not None:
            values[field] = LimitedDuration(seconds=seconds)
    return values


def _apply_launch_amendments(
    launch: _LaunchManifest,
    ledger: CodingRunAmendmentLedger,
) -> _LaunchManifest:
    return apply_launch_amendments(launch, ledger)


def _load_launch_manifest(state_dir: Path) -> _LaunchManifest:
    path = state_dir / "launch.json"
    if not path.is_file():
        raise ValueError("run predates resumable CLI state: launch.json is missing")
    launch = _LaunchManifest.model_validate_json(path.read_text(encoding="utf-8"))
    if launch.schema_version != "coding-run-launch-v10":
        raise ValueError("pre-release CodingRun launch schema is not supported")
    return launch


def _print_knowledge_packs() -> int:
    try:
        inventory = discover_domain_knowledge_packs()
    except (OSError, ValueError) as exc:
        print(f"error: {exc}")
        return 1
    print(f"Knowledge pack root: {inventory.root}")
    if not inventory.packs:
        print("No valid knowledge packs installed.")
    for pack in inventory.packs:
        compatibility = ", ".join(pack.compatibility) or "unspecified"
        print(
            f"- {pack.pack_id}@{pack.release}: {pack.summary} "
            f"[compatibility: {compatibility}]"
        )
    for issue in inventory.issues:
        print(f"! {issue.directory}: {issue.error}")
    return 1 if inventory.issues else 0


def _load_api_catalog(directory: Path | None) -> ApiCatalog:
    if directory is None:
        raise ValueError("product runs require an API catalog")
    resolved = directory.resolve(strict=True)
    catalog = load_product_api_catalog(resolved)
    print(
        f"API catalog: {resolved} "
        f"({len(catalog.cards)} cards, runtime={catalog.runtime_release})"
    )
    return catalog


def _product_verifier() -> ExecutionEvidenceVerifier:
    return ExecutionEvidenceVerifier()


def _resolve_api_catalog_mode(
    requested: ProductApiCatalogMode | None,
    catalog: ApiCatalog | None,
) -> ProductApiCatalogMode:
    if catalog is None:
        raise ValueError("product runs require an API catalog")
    return requested or DEFAULT_PRODUCT_API_CATALOG_MODE


def _load_concept_index(
    directory: Path | None,
    *,
    workspace: Path,
) -> AbaqusDocumentIndex | None:
    if directory is None:
        return None
    index = open_concept_index(directory, forbidden_root=workspace)
    manifest = index.read_manifest()
    print(
        f"Concept index: {index.persist_directory} "
        f"({manifest.documents_indexed} documents, release={manifest.release})"
    )
    return index


def _resolve_concept_retrieval_mode(
    requested: ConceptRetrievalMode,
    diagnostic_mode: DiagnosticRetrievalMode,
    index: AbaqusDocumentIndex | None,
) -> ConceptRetrievalMode:
    if requested == "off" and diagnostic_mode == "off" and index is not None:
        raise ValueError("off retrieval modes must not receive --concept-index-dir")
    if (requested == "tool" or diagnostic_mode == "advisory") and index is None:
        raise ValueError("active retrieval mode requires --concept-index-dir")
    if diagnostic_mode == "advisory":
        assert index is not None
        index.read_bm25_manifest()
        index.verify_bm25_corpus()
    return requested


def _runtime_script_policy(
    *,
    allowed_cae_scripts: list[str] | None,
    allowed_abaqus_python_scripts: list[str] | None,
    allowed_solver_inputs: list[str] | None,
) -> RuntimeScriptPolicy:
    return RuntimeScriptPolicy(
        allowed_cae_scripts=(
            tuple(allowed_cae_scripts) if allowed_cae_scripts is not None else None
        ),
        allowed_abaqus_python_scripts=(
            tuple(allowed_abaqus_python_scripts)
            if allowed_abaqus_python_scripts is not None
            else None
        ),
        allowed_solver_inputs=(
            tuple(allowed_solver_inputs) if allowed_solver_inputs is not None else None
        ),
    )


def _api_catalog_binding(catalog: ApiCatalog | None) -> ApiCatalogBinding | None:
    if catalog is None:
        return None
    return bind_api_catalog(catalog)


def _concept_index_binding(
    index: AbaqusDocumentIndex | None,
) -> ConceptIndexBinding | None:
    if index is None:
        return None
    return concept_index_binding(index)


def _resolve_goal(goal: str | None, request_file: Path | None) -> str:
    if goal and request_file is not None:
        raise ValueError("use either --goal or --request-file, not both")
    if goal:
        resolved = goal.strip()
    elif request_file is not None:
        text = request_file.read_text(encoding="utf-8")
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            resolved = text.strip()
        else:
            if not isinstance(payload, dict) or not isinstance(
                payload.get("goal"), str
            ):
                raise ValueError("request JSON must contain a string `goal` field")
            resolved = payload["goal"].strip()
    else:
        resolved = input("Task: ").strip()
    if not resolved:
        raise ValueError("coding goal must not be empty")
    return resolved


def _load_answers(path: Path) -> dict[str, str]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or not all(
        isinstance(key, str) and isinstance(value, str)
        for key, value in payload.items()
    ):
        raise ValueError("answers JSON must be an object mapping field ids to strings")
    return payload
