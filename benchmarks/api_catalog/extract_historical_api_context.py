"""只重建已成功文件工具操作，不执行历史脚本；以历史hash和错误回报核对断面。"""

import argparse
import hashlib
import json
import re
from pathlib import Path


def normalized(text):
    return text.replace("\r\n", "\n").replace("\r", "")


def digest(data):
    return hashlib.sha256(data).hexdigest()


def extract(history_path, error_path):
    history = json.loads(history_path.read_bytes())
    errors = error_path.read_bytes()
    pending, files, file_hashes, executions = {}, {}, {}, {}
    for message_no, message in enumerate(history):
        for part in message.get("parts", []):
            kind = part.get("part_kind")
            call_id = part.get("tool_call_id")
            if kind == "tool-call":
                args = part.get("args") or {}
                args = json.loads(args) if isinstance(args, str) else args
                pending[call_id] = (part["tool_name"], args)
                if part["tool_name"] == "run_cae_script":
                    script = args["script_path"]
                    executions[call_id] = (
                        script,
                        files.get(script),
                        file_hashes.get(script),
                    )
            if kind != "tool-return" or call_id not in pending:
                continue
            tool, args = pending[call_id]
            content = part.get("content")
            if tool in {"write_file", "edit_file"}:
                if not isinstance(content, str) or not content.startswith(
                    ("Wrote ", "Edited ")
                ):
                    continue
                path = args["path"]
                if tool == "write_file":
                    value = args["content"]
                else:
                    if path not in files:
                        raise ValueError(
                            f"cannot reconstruct edit without prior full write: {path}"
                        )
                    old = args["old_text"]
                    value = files[path]
                    if old not in value:
                        value, old = normalized(value), normalized(old)
                    if value.count(old) != 1:
                        raise ValueError(f"ambiguous edit in {history_path}: {path}")
                    value = value.replace(old, args["new_text"], 1)
                match = re.search(r"\[hash:([0-9a-f]+)\]", content)
                if match is None:
                    raise ValueError("no file tool hash")
                accepted = match.group(1)
                candidates = [
                    value,
                    normalized(value),
                    normalized(value).replace("\n", "\r\n"),
                ]
                matching = [
                    candidate
                    for candidate in candidates
                    if digest(candidate.encode()).startswith(accepted)
                ]
                if not matching:
                    raise ValueError(
                        f"file tool hash does not match reconstruction: {path} {accepted}"
                    )
                files[path], file_hashes[path] = matching[0], accepted
            if tool != "run_cae_script":
                continue
            result = json.loads(content) if isinstance(content, str) else content
            encoding = result.get("stderr_encoding", "utf-8")
            if (
                normalized(result.get("stderr_tail", "")).strip()
                != normalized(errors.decode(encoding, errors="replace")).strip()
            ):
                continue
            script, value, file_hash = executions[call_id]
            if value is None:
                raise ValueError("runtime call has no reconstructed script")
            return value.encode(), {
                "history_path": str(history_path),
                "history_sha256": digest(history_path.read_bytes()),
                "error_path": str(error_path),
                "error_sha256": digest(errors),
                "message_index": message_no,
                "runtime_tool_call_id": call_id,
                "script_path": script,
                "script_sha256": digest(value.encode()),
                "last_file_tool_hash": file_hash,
                "error_encoding": encoding,
                "reconstruction": "Successful write_file/edit_file only; each tool-return hash checked. Runtime stderr matched full normalized text. Not a whole filesystem replay or runtime input-byte attestation.",
            }
    raise ValueError(f"no matching historical runtime return: {error_path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--history", type=Path, required=True)
    parser.add_argument("--error", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    code, evidence = extract(args.history, args.error)
    args.output.mkdir(parents=True, exist_ok=False)
    (args.output / "historical_build.py").write_bytes(code)
    (args.output / "runtime_error.txt").write_text(
        args.error.read_bytes().decode(evidence["error_encoding"], errors="replace"),
        encoding="utf-8",
    )
    (args.output / "provenance.json").write_text(
        json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(evidence, ensure_ascii=False))
