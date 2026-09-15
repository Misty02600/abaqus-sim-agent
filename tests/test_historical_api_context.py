import hashlib
import importlib.util
import json
from pathlib import Path

import pytest


def extractor():
    path = (
        Path(__file__).parents[1]
        / "benchmarks/api_catalog/extract_historical_api_context.py"
    )
    spec = importlib.util.spec_from_file_location("historical_extractor", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.extract


def test_uses_successful_pre_error_content_not_final_script(tmp_path):
    old = "bad_call()\n"
    fixed = "correct_call()\n"
    error = "AttributeError: bad_call\n"

    def call(name, id, args):
        return {
            "part_kind": "tool-call",
            "tool_name": name,
            "tool_call_id": id,
            "args": args,
        }

    def returned(name, id, content):
        return {
            "part_kind": "tool-return",
            "tool_name": name,
            "tool_call_id": id,
            "content": content,
        }

    history = [
        {
            "parts": [
                call("write_file", "w", {"path": "build_model.py", "content": old})
            ]
        },
        {
            "parts": [
                returned(
                    "write_file",
                    "w",
                    "Wrote file. [hash:"
                    + hashlib.sha256(old.encode()).hexdigest()[:12]
                    + "]",
                )
            ]
        },
        {
            "parts": [
                call(
                    "edit_file",
                    "failed",
                    {"path": "build_model.py", "old_text": old, "new_text": fixed},
                )
            ]
        },
        {
            "parts": [
                {
                    "part_kind": "retry-prompt",
                    "tool_name": "edit_file",
                    "tool_call_id": "failed",
                    "content": "hash mismatch",
                }
            ]
        },
        {"parts": [call("run_cae_script", "run", {"script_path": "build_model.py"})]},
        {
            "parts": [
                returned(
                    "run_cae_script",
                    "run",
                    {"stderr_tail": error, "stderr_encoding": "utf-8"},
                )
            ]
        },
        {
            "parts": [
                call(
                    "write_file", "final", {"path": "build_model.py", "content": fixed}
                )
            ]
        },
        {
            "parts": [
                returned(
                    "write_file",
                    "final",
                    "Wrote file. [hash:"
                    + hashlib.sha256(fixed.encode()).hexdigest()[:12]
                    + "]",
                )
            ]
        },
    ]
    hp = tmp_path / "history.json"
    hp.write_text(json.dumps(history), encoding="utf-8")
    ep = tmp_path / "stderr.bin"
    ep.write_bytes(error.encode())
    code, evidence = extractor()(hp, ep)
    assert code == old.encode()
    assert evidence["runtime_tool_call_id"] == "run"
    history[1]["parts"][0]["content"] = "Wrote file. [hash:000000000000]"
    hp.write_text(json.dumps(history), encoding="utf-8")
    with pytest.raises(ValueError, match="hash does not match"):
        extractor()(hp, ep)


def test_compiled_eight_cases_bind_history_and_no_answer_files():
    root = Path(__file__).parents[1] / "benchmarks/api_catalog"
    data = json.loads((root / "historical_api_feedback_cases_v1.json").read_bytes())
    assert len(data["cases"]) == 8
    for case in data["cases"]:
        assert case["scope"] == "historical_failure_feedback_development"
        assert set(case["seed_files"]) == {
            "historical_build.py",
            "original_task.json",
            "runtime_error.txt",
        }
        assert (
            hashlib.sha256(
                case["seed_files"]["historical_build.py"].encode()
            ).hexdigest()
            == case["provenance"]["script_sha256"]
        )
        assert case["expected_groups"]
