"""非流式试点响应的有界元数据；不保存正文、参数或推理文本。"""

import hashlib
import json
from typing import Any

MAX_DIAGNOSTIC_BYTES = 1024 * 1024
MAX_DIAGNOSTIC_TOOLS = 16


def started_request_count(attempts: list[dict[str, Any]]) -> int | None:
    """按SDK的首次发送/重试序号计数；旧记录或不完整序列不按body hash猜测。

    Returns:
        进入HTTP发送流程的模型请求数，包含最终失败请求；缺少证据时为None。
    """
    count, previous = 0, None
    for attempt in attempts:
        status = attempt.get("status_code")
        if isinstance(status, int) and 300 <= status < 400:
            return None  # Redirects may repeat a retry index within one SDK request.
        retry = attempt.get("sdk_retry_count")
        if type(retry) is not int or retry < 0:
            return None
        if retry == 0:
            count += 1
        elif previous is None or retry != previous + 1:
            return None
        previous = retry
    return count


def response_diagnostic(raw: bytes, allowed_tools: set[str]) -> dict[str, Any]:
    """从SDK解析前的JSON响应提取元数据，不将内容或任意字段名写入报告。

    Note:
        此上限仅限制诊断解析；不限制模型输出，也不改变HTTP客户端的读取方式。
        响应正文只在内存中使用；工具名仅接受本次请求已声明的名称。
    """
    result: dict[str, Any] = {
        "schema_version": "response-diagnostic-v1",
        "body_bytes": len(raw),
        "body_sha256": hashlib.sha256(raw).hexdigest(),
    }
    if len(raw) > MAX_DIAGNOSTIC_BYTES:
        return {**result, "state": "body_limit"}
    try:
        body = json.loads(raw)
    except (ValueError, RecursionError):
        return {**result, "state": "invalid_json"}
    choices = body.get("choices") if isinstance(body, dict) else None
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        return {**result, "state": "unrecognized_shape"}
    choice = choices[0]
    finish = choice.get("finish_reason")
    result["finish_reason"] = (
        finish
        if finish in ("stop", "length", "tool_calls", "content_filter", "function_call")
        else "unknown"
    )
    message = choice.get("message")
    calls = message.get("tool_calls", []) if isinstance(message, dict) else None
    if not isinstance(calls, list):
        return {**result, "state": "unrecognized_shape"}
    result.update(
        state="parsed",
        tool_count=len(calls),
        tools_truncated=len(calls) > MAX_DIAGNOSTIC_TOOLS,
    )
    tools = []
    for call in calls[:MAX_DIAGNOSTIC_TOOLS]:
        fn = call.get("function") if isinstance(call, dict) else None
        if not isinstance(fn, dict):
            tools.append({"state": "unrecognized_shape"})
            continue
        name = fn.get("name")
        item: dict[str, Any] = {
            "name": name
            if isinstance(name, str) and name in allowed_tools
            else "unknown",
        }
        args = fn.get("arguments")
        if not isinstance(args, str):
            item["arguments_state"] = (
                "missing" if "arguments" not in fn else "non_string"
            )
        else:
            encoded = args.encode("utf-8", errors="surrogatepass")
            item.update(
                arguments_bytes=len(encoded),
                arguments_sha256=hashlib.sha256(encoded).hexdigest(),
            )
            try:
                parsed = json.loads(args)
            except (ValueError, RecursionError):
                item["arguments_state"] = "empty_string" if not args else "invalid_json"
            else:
                item["arguments_state"] = (
                    "empty_object"
                    if parsed == {}
                    else "object"
                    if isinstance(parsed, dict)
                    else "non_object"
                )
        tools.append(item)
    result["tools"] = tools
    return result
