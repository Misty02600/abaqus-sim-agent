import argparse
import secrets
import webbrowser
from pathlib import Path

from ..coding.model import (
    DEEPSEEK_V4_FLASH,
    DeepSeekGateway,
    new_run_execution_config,
    take_gateway_api_key_from_environment,
)
from .controller import ServerConfig


def main() -> None:
    parser = argparse.ArgumentParser(description="本地 Abaqus 任务工作台")
    parser.add_argument(
        "--root",
        type=Path,
        required=True,
        help="ASCII任务根目录（workspace与state分别建立）",
    )
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument(
        "--model-provider",
        type=DeepSeekGateway,
        choices=tuple(DeepSeekGateway),
        default=DeepSeekGateway.OFFICIAL,
    )
    parser.add_argument("--model", default=DEEPSEEK_V4_FLASH)
    parser.add_argument("--open", action="store_true")
    args = parser.parse_args()
    try:
        import uvicorn

        from .app import create_app
    except ImportError as exc:
        raise SystemExit("请安装Web依赖：uv sync --extra web --all-groups") from exc
    token = secrets.token_urlsafe(32)
    config = ServerConfig(
        root=args.root,
        execution=new_run_execution_config(
            model_provider=args.model_provider, model_name=args.model
        ),
    )
    key = take_gateway_api_key_from_environment(args.model_provider)
    app = create_app(config, api_key=key, bootstrap_token=token)
    url = f"http://127.0.0.1:{args.port}/#session={token}"
    print(f"本地工作台：{url}")
    if args.open:
        webbrowser.open(url)
    uvicorn.run(
        app,
        host="127.0.0.1",
        port=args.port,
        access_log=False,
        timeout_graceful_shutdown=2,
    )
