import argparse
import asyncio
import webbrowser
from pathlib import Path

from ..coding.api_lookup import DEFAULT_PRODUCT_API_CATALOG_MODE
from ..coding.model import (
    DEEPSEEK_V4_FLASH,
    DeepSeekGateway,
    new_run_execution_config,
    take_gateway_api_key_from_environment,
)
from .controller import ServerConfig


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="本地 Abaqus 任务工作台")
    parser.add_argument(
        "--root",
        type=Path,
        required=True,
        help="ASCII任务根目录（workspace与state分别建立）",
    )
    parser.add_argument(
        "--api-catalog-mode",
        choices=(
            "full",
            "full_with_discovery",
            "automatic_verified_only",
            "automatic_exact_signature",
        ),
        default=DEFAULT_PRODUCT_API_CATALOG_MODE,
    )
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument(
        "--model-provider",
        type=DeepSeekGateway,
        choices=tuple(DeepSeekGateway),
        default=DeepSeekGateway.OFFICIAL,
    )
    parser.add_argument("--model", default=DEEPSEEK_V4_FLASH)
    parser.add_argument("--no-open", action="store_true")
    return parser.parse_args(argv)


def main() -> None:
    args = parse_args()
    try:
        import uvicorn

        from .app import create_app
    except ImportError as exc:
        raise SystemExit("请安装Web依赖：uv sync --extra web --all-groups") from exc
    config = ServerConfig(
        root=args.root,
        api_catalog_mode=args.api_catalog_mode,
        execution=new_run_execution_config(
            model_provider=args.model_provider, model_name=args.model
        ),
    )
    key = take_gateway_api_key_from_environment(args.model_provider)
    app = create_app(config, api_key=key)
    url = f"http://127.0.0.1:{args.port}/"
    print(f"本地工作台：{url}")
    server = uvicorn.Server(
        uvicorn.Config(
            app,
            host="127.0.0.1",
            port=args.port,
            access_log=False,
            timeout_graceful_shutdown=2,
        )
    )

    async def serve() -> None:
        task = asyncio.create_task(server.serve())
        while not server.started:
            if task.done():
                await task
                return
            await asyncio.sleep(0.05)
        if not args.no_open:
            try:
                webbrowser.open(url)
            except webbrowser.Error as exc:
                print(f"无法自动打开浏览器：{exc}")
        await task

    asyncio.run(serve())
