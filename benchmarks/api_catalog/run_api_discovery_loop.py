"""评测专用单Coder发现入口；没有普通CLI/resume接线或CodingRunOutcome承诺。"""

import argparse
import asyncio
import hashlib
import json
import shutil
from pathlib import Path
from time import perf_counter

from pydantic import TypeAdapter
from pydantic_ai import (
    DeferredToolRequests,
    ModelMessagesTypeAdapter,
    RunContext,
    UsageLimits,
)

from abaqus_sim_agent.api_catalog import ApiCatalog
from abaqus_sim_agent.api_catalog.discovery import CatalogDiscovery
from abaqus_sim_agent.api_catalog.e5 import E5Encoder
from abaqus_sim_agent.api_catalog.intent import DiscoveryIntent
from abaqus_sim_agent.coding.agent import create_abaqus_coding_agent
from abaqus_sim_agent.coding.api_lookup import _record_api_discovery
from abaqus_sim_agent.coding.contracts import (
    CodingRunBudget,
    CodingRunRequest,
    ReadyForVerificationDecision,
    RuntimeProfile,
)
from abaqus_sim_agent.coding.dependencies import CodingAgentDependencies
from abaqus_sim_agent.coding.execution_verifier import ExecutionEvidenceVerifier
from abaqus_sim_agent.coding.model import (
    DeepSeekGateway,
    create_deepseek_chat_model,
    new_run_execution_config,
    take_gateway_api_key_from_environment,
)
from abaqus_sim_agent.coding.workspace import WorkspaceResolver
from abaqus_sim_agent.execution import DEFAULT_ABAQUS_2024_LAUNCHER, Abaqus2024Runner

PROJECT = Path(__file__).resolve().parents[2]
INSTRUCTIONS = """这是API知识查证任务，不是完整建模或求解任务。
结合用户原始目标和已知事实，决定要查什么API。允许运用已有知识提出方法名、参数名或技术步骤作为候选，再用工具核实；不要把候选假说当用户已经给定的事实。
已知/怀疑具体名字时直接lookup_abaqus_api；不知道名字时用discover_abaqus_api按操作意图搜索。查询表达由你组织，语言不限，不要求翻译整句，也不强制每题都用语义搜索。
发现结果只是候选目录。选用前用lookup_abaqus_api读实际卡片，必要时用Method.parameter或Method.__doc__核对参数/限制。不要只因为排第一就采用。
这是用户明确要求的查证任务，即使你熟悉名字也需要核实证据；对普通建模的熟悉API不预查建议不替代本任务要求。共用API查询预算，查不到可换问题或报告证据不足，不重复同一查询。
缺少真正会改变物理模型的阻塞用户信息时调用ask_user。仅核实API不需要用户先提供与接口选择无关的实际数值；不得从资料补值。
不得执行任何程序或Abaqus，不创建实际模型或提交Job。运行预算为零。
若可以给出接口方案，写UTF-8 api_plan.json：selected数组，每项含api、reason、key_parameters数组、evidence_card_ids数组；rejected数组，每项含api和reason；unresolved为字符串数组。可拒绝不合适的候选或报告未解决事项。
引用你实际读到的卡片id，简要说明正确对象、为何适用与关键限制。检查文件并提交ready_for_verification。这个ready不是物理验收。"""


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_new(path, value):
    with path.open("x", encoding="utf-8") as f:
        json.dump(value, f, ensure_ascii=False, indent=2)


def register_discovery(agent, index, encoder, question):
    @agent.tool
    async def discover_abaqus_api(
        ctx: RunContext[CodingAgentDependencies], operation: str
    ) -> dict:
        """Discover possible Abaqus API names from an operation or technical question.

        Candidates are not verified applicability. Read names with lookup_abaqus_api before selecting.
        Original user facts remain authoritative; no missing physical values may be invented.
        """
        query = "discovery:" + operation.strip()

        def finish(payload, counted=False):
            _record_api_discovery(
                ctx.deps,
                receiver=None,
                query=operation.strip() or None,
                limit=min(100, max(1, len(payload.get("candidates", [])) or 5)),
                payload=payload,
                counted=counted,
            )
            with (ctx.deps.request.state_dir / "discovery_results.jsonl").open(
                "a", encoding="utf-8"
            ) as f:
                f.write(json.dumps(payload, ensure_ascii=False) + "\n")
            return payload

        if not operation.strip() or len(operation) > 1000:
            return finish(
                {
                    "status": "invalid",
                    "error": "Use a nonblank operation up to 1000 characters.",
                }
            )
        if not ctx.deps.register_api_discovery_query(query):
            return finish({"status": "duplicate", "query": operation})
        exhausted = ctx.deps.reserve_api_lookup()
        if exhausted:
            return finish({"status": "unavailable", "error": exhausted})
        result = await asyncio.to_thread(
            index.search_intent,
            DiscoveryIntent(original_request=question, operation=operation),
            strategy="dense",
            encoder=encoder,
        )
        return finish(result, True)


async def run(root, cases_path=None):
    root.mkdir(parents=True, exist_ok=False)
    cases_path = (
        cases_path
        or PROJECT / "benchmarks/api_catalog/api_discovery_loop_cases_v1.json"
    )
    cases = json.loads(cases_path.read_bytes())["cases"]
    catalog = ApiCatalog.open(
        PROJECT / "var/api-catalog/abaqus-2024-reference-context-v2"
    )
    index = CatalogDiscovery.open(
        catalog, PROJECT / "var/api-catalog/discovery-reference-context-v2-e5"
    )
    encoder = E5Encoder(PROJECT / "var/models/multilingual-e5-base-d128750")
    shutil.copytree(
        PROJECT / "src/abaqus_sim_agent/skills/abaqus-2024-coding",
        root / "skills/abaqus-2024-coding",
    )
    config = new_run_execution_config(
        model_provider=DeepSeekGateway.OPENCODE_GO, model_name="deepseek-v4-flash"
    )
    hashes = {
        str(p.relative_to(PROJECT)): sha(p)
        for p in (PROJECT / "src/abaqus_sim_agent").rglob("*.py")
    }
    write_new(
        root / "seal.json",
        {
            "cases": cases,
            "cases_path": str(cases_path),
            "cases_sha256": sha(cases_path),
            "task_instructions": INSTRUCTIONS,
            "model_config": config.model_dump(mode="json"),
            "source_hashes": hashes,
            "driver_sha256": sha(Path(__file__)),
            "index": index.manifest.model_dump(mode="json"),
            "uv_lock_sha256": sha(PROJECT / "uv.lock"),
            "skills": {
                str(p.relative_to(root / "skills")): sha(p)
                for p in (root / "skills").rglob("*")
                if p.is_file()
            },
            "protocol": "Single-session native Agent evaluation, existing product factory plus experiment-only discovery tool; no CodingRunService/CLI launch or resume. Not a production resumable run. Shared lookup budget 8, model budget24, wall900, runtime0. API hypotheses allowed; selected cards must have been read. No ranking feedback from host reviewer.",
        },
    )
    key = take_gateway_api_key_from_environment(DeepSeekGateway.OPENCODE_GO)
    for case in cases:
        assert all(sha(PROJECT / p) == h for p, h in hashes.items()), "source changed"
        folder = root / case["id"]
        folder.mkdir()
        workspace = Path("D:/abaqus-runs") / root.name / case["id"] / "workspace"
        workspace.mkdir(parents=True, exist_ok=False)
        seeds = case.get("seed_files", {})
        for name, content in seeds.items():
            if (
                Path(name).name != name
                or ":" in name
                or "\\" in name
                or name in {".", ".."}
            ):
                raise ValueError("seed files must have plain relative names")
            (workspace / name).write_bytes(content.encode("utf-8"))
        state = folder / "evidence"
        state.mkdir()
        request = CodingRunRequest(
            run_id=root.name + "-" + case["id"],
            goal=case["question"],
            workspace=workspace.resolve(),
            state_dir=state.resolve(),
            runtime=RuntimeProfile(
                launcher_path=DEFAULT_ABAQUS_2024_LAUNCHER.resolve()
            ),
            budget=CodingRunBudget(
                max_model_requests=24,
                max_api_lookup_calls=8,
                max_abaqus_calls=0,
                max_solver_calls=0,
                wall_time_seconds=900.0,
            ),
        )
        model, client = create_deepseek_chat_model(
            gateway=config.model_provider,
            model_name=config.model_name,
            api_key=key,
            conversation_id=request.run_id,
            execution_config=config,
        )
        deps = CodingAgentDependencies(
            request=request,
            runner=Abaqus2024Runner(workspace, state),
            api_catalog=catalog,
        )
        initial = WorkspaceResolver(captured_cwd=workspace).snapshot(workspace)
        agent = create_abaqus_coding_agent(
            model,
            request,
            skill_library=root / "skills",
            api_catalog_mode="full",
            execution_config=config,
            question_protocol="ask_user_v1",
            foundation_skill_profile="off",
            task_instructions=INSTRUCTIONS,
            immutable_workspace_paths=tuple(seeds),
        )
        register_discovery(agent, index, encoder, case["question"])
        print("START", case["id"], flush=True)
        started = perf_counter()
        record = {
            "case_id": case["id"],
            "workspace": str(workspace),
            "api_calls": 0,
            "error": None,
        }
        try:
            async with asyncio.timeout(request.budget.wall_time_seconds):
                result = await agent.run(
                    case["question"],
                    deps=deps,
                    usage_limits=UsageLimits(request_limit=24),
                )
            (state / "history.json").write_bytes(
                ModelMessagesTypeAdapter.dump_json(result.all_messages(), indent=2)
            )
            output = result.output
            record["agent_output"] = TypeAdapter(type(output)).dump_python(
                output, mode="json"
            )
            record["deferred_question"] = isinstance(output, DeferredToolRequests)
            if isinstance(output, ReadyForVerificationDecision):
                verification = await ExecutionEvidenceVerifier().verify(
                    request, deps.runner, initial, deps.executions, output
                )
                record["file_verification"] = verification.model_dump(mode="json")
            usage = result.usage
            record["usage"] = {
                "requests": usage.requests,
                "input_tokens": usage.input_tokens,
                "output_tokens": usage.output_tokens,
                "tool_calls": usage.tool_calls,
            }
        except Exception as exc:
            record["error"] = type(exc).__name__
        finally:
            await client.close()
        record.update(
            api_calls=deps.api_lookup_calls,
            abaqus_calls=deps.abaqus_calls,
            elapsed_seconds=perf_counter() - started,
        )
        record["seed_files_unchanged"] = all(
            (workspace / name).read_bytes() == content.encode("utf-8")
            for name, content in seeds.items()
        )
        write_new(folder / "result.json", record)
        print(
            "END",
            case["id"],
            record["error"],
            record.get("deferred_question"),
            record["api_calls"],
            flush=True,
        )
    assert all(sha(PROJECT / p) == h for p, h in hashes.items()), "source changed"


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--cases-file", type=Path)
    args = parser.parse_args()
    asyncio.run(
        run(args.root.resolve(), args.cases_file.resolve() if args.cases_file else None)
    )
