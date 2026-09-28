import argparse
import asyncio
import webbrowser
from pathlib import Path

from ..coding.model import (
    take_gateway_api_keys_from_environment,
)
from ..coding.preference_arguments import (
    add_preference_arguments,
    preferences_from_args,
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
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-open", action="store_true")
    add_preference_arguments(parser)
    return parser.parse_args(argv)


def main() -> None:
    args = parse_args()
    try:
        _serve(args)
    except (ValueError, RuntimeError) as exc:
        raise SystemExit(str(exc)) from None


def _serve(args: argparse.Namespace) -> None:
    try:
        import uvicorn

        from .app import create_app
    except ImportError as exc:
        raise SystemExit("请安装Web依赖：uv sync --extra web --all-groups") from exc
    config = ServerConfig(
        root=args.root,
        overrides=preferences_from_args(args),
    )
    keys = take_gateway_api_keys_from_environment()
    app = create_app(config, api_keys=keys)
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
