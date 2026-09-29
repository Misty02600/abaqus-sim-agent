"""模型目录的最小离线夹具；不是产品型号目录。"""

import json


def catalog_payload() -> bytes:
    names = {
        "deepseek": {
            "deepseek-flash": 1_000_000,
            "deepseek-v4-flash": 1_000_000,
            "deepseek-v4-pro": 1_000_000,
        },
        "openai": {"gpt-4.1": 1_047_576, "gpt-4o": 128_000},
        "anthropic": {"claude-sonnet-4-5": 1_000_000},
        "google": {"gemini-2.5-flash": 1_048_576},
        "google-vertex": {"gemini-2.5-flash": 1_048_576, "claude-sonnet-4-5": 200_000},
        "github-copilot": {"gpt-5.4": 1_050_000},
        "openrouter": {"openai/gpt-4.1": 1_047_576},
    }
    return json.dumps(
        {
            provider: {
                "models": {
                    name: {
                        "limit": {"context": window},
                        "tool_call": True,
                        "modalities": {"output": ["text"]},
                    }
                    for name, window in models.items()
                }
            }
            for provider, models in names.items()
        }
    ).encode()
