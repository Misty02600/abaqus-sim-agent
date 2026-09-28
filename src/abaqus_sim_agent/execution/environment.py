"""不向工作区 Shell 或 vendor 子进程继承宿主服务凭据。"""

import fnmatch
import os

PROVIDER_SECRET_PATTERNS = (
    "*API_KEY",
    "*_TOKEN",
    "AWS_ACCESS_KEY_ID",
    "AWS_SECRET_ACCESS_KEY",
    "AZURE_CLIENT_SECRET",
    "HEROKU_INFERENCE_KEY",
)


def subprocess_environment() -> dict[str, str]:
    return {
        name: value
        for name, value in os.environ.items()
        if not any(
            fnmatch.fnmatchcase(name.upper(), pattern)
            for pattern in PROVIDER_SECRET_PATTERNS
        )
    }
