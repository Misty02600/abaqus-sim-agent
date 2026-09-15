"""读取已保存会话，不重跑Agent；引用检查与技术审查分开。"""

import argparse
import hashlib
import json
from pathlib import Path

from pydantic_ai import ModelMessagesTypeAdapter
from pydantic_ai.messages import ModelResponse, ToolCallPart

from abaqus_sim_agent.api_catalog import ApiCatalog


def observe(root):
    seal = json.loads((root / "seal.json").read_bytes())
    project = Path(__file__).resolve().parents[2]
    catalog = ApiCatalog.open(
        project / "var/api-catalog/abaqus-2024-reference-context-v2"
    )
    by_id = {c.card_id: c for c in (*catalog.cards, *catalog.members)}
    rows = []
    for case in seal["cases"]:
        folder = root / case["id"]
        result = json.loads((folder / "result.json").read_bytes())
        history_path = folder / "evidence/history.json"
        messages = (
            ModelMessagesTypeAdapter.validate_json(history_path.read_bytes())
            if history_path.exists()
            else []
        )
        calls = [
            p
            for m in messages
            if isinstance(m, ModelResponse)
            for p in m.parts
            if isinstance(p, ToolCallPart)
        ]
        responses = [m for m in messages if isinstance(m, ModelResponse)]
        path = folder / "evidence/api_lookups.jsonl"
        traces = (
            [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
            if path.exists()
            else []
        )
        read_ids = {
            cid
            for t in traces
            if t["status"] == "found" and not t["query"].startswith("discovery:")
            for cid in t["card_ids"]
        }
        record_ids = {
            r.record_id
            for cid in read_ids
            for r in getattr(by_id[cid], "abqpy_records", ())
        }
        delivered_notes = {
            note for trace in traces for note in trace.get("delivered_note_ids", [])
        }
        file = Path(result["workspace"]) / "api_plan.json"
        plan_error = None
        try:
            plan = json.loads(file.read_bytes()) if file.exists() else None
        except ValueError as exc:
            plan, plan_error = None, type(exc).__name__
        selected = []
        selected_ids = set()
        if plan:
            for selection in plan["selected"]:
                resolved = catalog.lookup(selection["api"])
                references = selection["evidence_card_ids"]
                ids = {c.card_id for c in (*resolved.cards, *resolved.members)}
                selected_ids.update(ids)
                selected.append(
                    {
                        "api": selection["api"],
                        "resolved": resolved.status,
                        "selected_card_read": bool(ids & read_ids),
                        "unread_card_refs": [
                            x
                            for x in references
                            if x not in read_ids
                            and x not in record_ids
                            and x not in delivered_notes
                        ],
                        "record_refs_not_card_ids": [
                            x for x in references if x in record_ids
                        ],
                        "runtime_note_refs_not_card_ids": [
                            x for x in references if x in delivered_notes
                        ],
                        "has_read_primary_citation": bool(
                            ids & set(references) & read_ids
                        ),
                    }
                )
        postprocessing_only = (
            result["error"] == "TypeError"
            and "agent_output" in result
            and (
                result.get("deferred_question")
                or result.get("file_verification", {}).get("passed")
            )
        )
        rows.append(
            {
                "case_id": case["id"],
                "original_result_sha256": hashlib.sha256(
                    (folder / "result.json").read_bytes()
                ).hexdigest(),
                "recorded_error": result["error"],
                "confirmed_usage_property_finalizer_error": bool(postprocessing_only),
                "agent_output": result.get("agent_output"),
                "recorded_runtime_usage": result.get("usage"),
                "plan_is_completed_file_proposal": result.get("agent_output", {}).get(
                    "status"
                )
                == "ready_for_verification"
                and result.get("file_verification", {}).get("passed", False),
                "file_evidence_passed": result.get("file_verification", {}).get(
                    "passed"
                ),
                "deferred_question": result.get("deferred_question", False),
                "selected": selected,
                "expected_apis_selected": all(
                    n in {x["api"] for x in selected} for n in case["expected"]
                )
                if case.get("expected")
                else None,
                "declared_group_coverage": [
                    any(
                        bool(
                            {
                                c.card_id
                                for c in (
                                    *catalog.lookup(name).cards,
                                    *catalog.lookup(name).members,
                                )
                            }
                            & selected_ids
                        )
                        for name in group
                    )
                    for group in case.get("expected_groups", [])
                ],
                "declared_groups_are_exhaustive": False,
                "plan_parse_error": plan_error,
                "seed_files_unchanged": result.get("seed_files_unchanged"),
                "lookup_trace": traces,
                "native_tool_names": [c.tool_name for c in calls],
                "history_observed_responses": len(responses),
                "history_observed_input_tokens": sum(
                    m.usage.input_tokens for m in responses
                ),
                "history_observed_output_tokens": sum(
                    m.usage.output_tokens for m in responses
                ),
                "api_budget_used": result["api_calls"],
                "abaqus_calls": result["abaqus_calls"],
                "elapsed_seconds": result["elapsed_seconds"],
                "plan_sha256": hashlib.sha256(file.read_bytes()).hexdigest()
                if file.exists()
                else None,
            }
        )
    with (root / "observations.json").open("x", encoding="utf-8") as f:
        json.dump(
            {
                "observation_note": "Original result.json retained. Only rows explicitly marked confirmed_usage_property_finalizer_error describe the old property-call defect. Current runs may have no such defect. History-observed statistics are separate from recorded runtime usage; no Agent rerun. Declared route groups are not an exhaustive correctness oracle.",
                "rows": rows,
            },
            f,
            ensure_ascii=False,
            indent=2,
        )
    for r in rows:
        print(
            json.dumps(
                {
                    k: r[k]
                    for k in [
                        "case_id",
                        "file_evidence_passed",
                        "deferred_question",
                        "expected_apis_selected",
                        "api_budget_used",
                        "history_observed_responses",
                        "selected",
                    ]
                },
                ensure_ascii=False,
            )
        )


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--root", type=Path, required=True)
    observe(p.parse_args().root.resolve())
