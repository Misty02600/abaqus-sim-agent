"""在独立 uv 环境启动 Phoenix；父进程关闭 stdin 后正常关闭服务，保留数据库。"""

import sys
from contextlib import asynccontextmanager
from importlib import import_module


@asynccontextmanager
async def _without_grpc(*args, **kwargs):
    # Phoenix 20.16.0 的 gRPC 默认监听 [::]，且没有 HTTP-only 配置。
    # 本项目只用 HTTP，在这个独立服务进程中跳过未使用的 gRPC 接收器。
    yield


def main() -> None:
    phoenix = import_module("phoenix")
    app = import_module("phoenix.server.app")
    setattr(app, "GrpcServer", _without_grpc)  # noqa: B010 - pinned HTTP-only service adapter
    try:
        session = phoenix.launch_app(host="127.0.0.1", use_temp_dir=False)
        if session is None:
            raise RuntimeError("Phoenix did not start")
        print(sys.argv[1], flush=True)
        sys.stdin.buffer.read()
    finally:
        phoenix.close_app()


if __name__ == "__main__":
    main()
