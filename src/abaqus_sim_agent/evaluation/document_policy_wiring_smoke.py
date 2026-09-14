"""确定性FunctionModel走真实Coder/RAG/重验；不是生成式模型行为或物理评测。"""

import argparse
import asyncio
import json
import time
from pathlib import Path

from pydantic_ai.messages import ModelResponse, ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import FunctionModel

from abaqus_sim_agent.coding.agent import create_abaqus_coding_agent
from abaqus_sim_agent.coding.clarification import ScriptedClarificationProvider
from abaqus_sim_agent.coding.contracts import (
    CodingRunBudget,
    CodingRunRequest,
    RuntimeProfile,
)
from abaqus_sim_agent.coding.dependencies import CodingAgentDependencies
from abaqus_sim_agent.coding.document_policy_binding import load_document_policy_spec
from abaqus_sim_agent.coding.execution_verifier import ExecutionEvidenceVerifier
from abaqus_sim_agent.coding.resume import create_step_store, load_resume_state
from abaqus_sim_agent.coding.service import CodingRunService
from abaqus_sim_agent.coding.workspace import WorkspaceResolver
from abaqus_sim_agent.execution import Abaqus2024Runner

from .document_policy_adapter import bind_document_policy, compose_document_backend


class NeverRunAbaqus(Abaqus2024Runner):
    async def run_cae_script(self, *args, **kwargs):
        raise AssertionError("this qualification must not invoke Abaqus")

    run_solver_job = run_cae_script
    run_abaqus_python = run_cae_script


def _return(messages, name) -> dict:
    for message in reversed(messages):
        for part in reversed(message.parts):
            if isinstance(part, ToolReturnPart) and part.tool_name == name:
                if not isinstance(part.content, dict):
                    raise AssertionError("expected an object tool receipt")
                return part.content
    raise AssertionError("expected tool receipt: " + name)


async def run(output: Path, spec_file: Path):
    output.mkdir(parents=True, exist_ok=False)  # noqa: ASYNC240 -- standalone sequential local qualification
    workspace, state = output / "workspace", output / "state"
    workspace.mkdir()
    state.mkdir()
    started = time.monotonic()
    binding = bind_document_policy(load_document_policy_spec(spec_file))
    request = CodingRunRequest(
        run_id="document-wiring-smoke",
        goal="Check the documented unit convention and write an integration receipt, not a generated scientific answer.",
        workspace=workspace,
        state_dir=state,
        runtime=RuntimeProfile(launcher_path=output / "never-run.bat"),
        document_policy=binding,
        budget=CodingRunBudget(max_model_requests=6, wall_time_seconds=300.0),
    )
    snapshot = WorkspaceResolver(captured_cwd=workspace).snapshot(workspace)
    calls = 0
    checked_search: dict | None = None

    def respond(messages, info):
        nonlocal calls, checked_search
        calls += 1

        def call(name, args):
            return ModelResponse(parts=[ToolCallPart(name, json.dumps(args))])

        if calls == 1:
            return call(
                "search_abaqus_docs",
                {
                    "query": {
                        "kind": "first",
                        "question": "Does Abaqus/Standard impose a built-in system of physical units, or must model inputs use a consistent unit system chosen by the analyst?",
                        "observed_context": "Documentation question only; do not choose physical values.",
                        "proposed_needs": [
                            {
                                "condition": "unit convention",
                                "critical": True,
                                "affects_step": "integration report",
                            }
                        ],
                    }
                },
            )
        if calls == 2:
            checked_search = _return(messages, "search_abaqus_docs")
            if checked_search["result"]["status"] != "found":
                raise AssertionError("real backend failed: " + str(checked_search))
            item = checked_search["result"]["evidence"][0]
            return call(
                "record_document_assessment",
                {
                    "protocol": "document-assessment-v1",
                    "assessment": {
                        "search_id": checked_search["result"]["search_id"],
                        "scope": binding.spec.scope.model_dump(mode="json"),
                        "needs": [
                            {
                                "need_id": checked_search["needs"][0]["id"],
                                "status": "supported",
                                "explanation": "Deterministic mechanical wiring test only; no semantic adoption claim.",
                                "citations": [
                                    {
                                        "search_id": checked_search["result"][
                                            "search_id"
                                        ],
                                        "evidence_id": item["id"],
                                        "quote": item["body"][:80],
                                        "applicability": "unknown"
                                        if item["applicability"]["origin"] == "unknown"
                                        else "as_documented",
                                    }
                                ],
                            }
                        ],
                        "next_action": "continue",
                    },
                },
            )
        if calls == 3:
            if _return(messages, "record_document_assessment")["status"] != "recorded":
                raise AssertionError("assessment was rejected")
            return call(
                "write_file",
                {
                    "path": "report.txt",
                    "content": "Deterministic Coder/RAG wiring receipt. No generated answer or physical simulation.\n",
                },
            )
        return call(
            "final_result_ReadyForVerificationDecision",
            {
                "status": "ready_for_verification",
                "summary": "Deterministic integration receipt written",
                "deliverables": [{"relative_path": "report.txt"}],
            },
        )

    port, validator = compose_document_backend(
        binding, state_dir=state, workspace=workspace
    )
    deps = CodingAgentDependencies(
        request=request,
        runner=NeverRunAbaqus(workspace, state, launcher=request.runtime.launcher_path),
        document_port=port,
        document_validator=validator,
    )
    steps = create_step_store(state)
    agent = create_abaqus_coding_agent(
        FunctionModel(respond),
        request,
        concept_retrieval_mode="tool",
        planning_enabled=False,
        task_instructions="Document-only deterministic integration test; no Abaqus calls.",
        step_store=steps,
    )
    result = await CodingRunService(
        agent=agent,
        request=request,
        dependencies=deps,
        clarification_provider=ScriptedClarificationProvider({}),
        verifier=ExecutionEvidenceVerifier(),
        initial_snapshot=snapshot,
        step_store=steps,
    ).run()
    summary = {
        "profile": "document-policy-wiring-smoke-v1",
        "passed": result.outcome.status.value == "completed",
        "model_backend": "FunctionModel deterministic callback, not a generative provider",
        "function_model_requests": calls,
        "real_retrieval_calls": deps.concept_lookup_calls,
        "abaqus_calls": deps.abaqus_calls,
        "verification_scope": result.outcome.verification_scope,
        "resume_schema": load_resume_state(state).schema_version,
        "elapsed_seconds": time.monotonic() - started,
        "validation_receipts": len(
            list((state / "document-validation").glob("*/receipt.json"))
        ),
        "evidence_ids": [e["id"] for e in checked_search["result"]["evidence"]]
        if checked_search
        else [],
        "binding": binding.model_dump(mode="json"),
        "limitation": "Proves actual composition only, not retrieval accuracy, model reasoning, semantic support or physical acceptance.",
    }
    (output / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return summary


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--spec", required=True, type=Path)
    args = parser.parse_args()
    result = asyncio.run(run(args.output.resolve(), args.spec.resolve()))
    raise SystemExit(0 if result["passed"] else 1)


if __name__ == "__main__":
    main()
