import json

import pytest

from abaqus_sim_agent.evaluation.response_diagnostic import (
    MAX_DIAGNOSTIC_BYTES,
    MAX_DIAGNOSTIC_TOOLS,
    response_diagnostic,
    started_request_count,
)


def payload(arguments, *, name="write_file", count=1, finish="tool_calls"):
    return json.dumps(
        {
            "secret": "credential-sentinel",
            "choices": [
                {
                    "finish_reason": finish,
                    "message": {
                        "content": "content-sentinel",
                        "reasoning_content": "reasoning-sentinel",
                        "tool_calls": [
                            {"function": {"name": name, "arguments": arguments}}
                        ]
                        * count,
                    },
                }
            ],
        }
    ).encode()


@pytest.mark.parametrize(
    ("arguments", "state"),
    [
        ("{}", "empty_object"),
        ("", "empty_string"),
        ("{", "invalid_json"),
        ('{"path":"secret-file","content":"private-source"}', "object"),
        ("[]", "non_object"),
        (None, "non_string"),
        ({}, "non_string"),
    ],
)
def test_argument_states_and_private_content(arguments, state):
    result = response_diagnostic(payload(arguments), {"write_file"})
    assert result["tools"][0]["arguments_state"] == state
    assert result["finish_reason"] == "tool_calls"
    serialized = json.dumps(result)
    for private in (
        "credential-sentinel",
        "content-sentinel",
        "reasoning-sentinel",
        "secret-file",
        "private-source",
    ):
        assert private not in serialized


def test_bounded_structure_and_unknown_names():
    result = response_diagnostic(
        payload("{}", name="private-name", count=20, finish="private-finish"),
        {"write_file"},
    )
    assert len(result["tools"]) == MAX_DIAGNOSTIC_TOOLS
    assert result["tool_count"] == 20
    assert result["tools_truncated"]
    assert result["finish_reason"] == "unknown"
    assert "private" not in json.dumps(result)
    assert (
        response_diagnostic(b" " * (MAX_DIAGNOSTIC_BYTES + 1), set())["state"]
        == "body_limit"
    )
    assert response_diagnostic(b"{", set())["state"] == "invalid_json"
    assert response_diagnostic(b"[]", set())["state"] == "unrecognized_shape"


def test_missing_arguments_distinguished_from_empty_object():
    raw = (
        b'{"choices":[{"message":{"tool_calls":[{"function":{"name":"write_file"}}]}}]}'
    )
    assert (
        response_diagnostic(raw, {"write_file"})["tools"][0]["arguments_state"]
        == "missing"
    )


def test_started_request_count_distinguishes_retries_from_identical_new_requests():
    records = [
        {"sdk_retry_count": n, "body_sha256": "identical"} for n in (0, 1, 2, 0, 0, 1)
    ]
    assert started_request_count(records) == 3
    assert started_request_count([]) == 0
    assert started_request_count([{"body_sha256": "legacy"}]) is None
    assert started_request_count([{"sdk_retry_count": 1}]) is None
    assert (
        started_request_count([{"sdk_retry_count": 0}, {"sdk_retry_count": 2}]) is None
    )
    assert started_request_count([{"sdk_retry_count": True}]) is None
    assert (
        started_request_count(
            [
                {"sdk_retry_count": 0, "status_code": 302},
                {"sdk_retry_count": 0, "status_code": 200},
            ]
        )
        is None
    )
