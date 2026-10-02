"""公开力学测评的参数解析和命令分发。"""

import argparse
import asyncio
from collections.abc import Sequence
from pathlib import Path

from abaqus_sim_agent import telemetry
from abaqus_sim_agent.evaluation import public_mechanics_suite as suite


def main(argv: Sequence[str] | None = None):
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "mode",
        choices=(
            "prepare",
            "preflight",
            "doctor",
            "run",
            "continue",
            "resume",
            "pause",
            "report",
            "launch",
        ),
    )
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--catalog", type=Path)
    parser.add_argument(
        "--case",
        action="append",
        dest="cases",
        help="prepare默认选择15个评分Case；可显式选择其他Case作非计分诊断",
    )
    parser.add_argument("--control", action="store_true")
    parser.add_argument(
        "--api-catalog-mode",
        choices=("full", "full_with_discovery"),
        default="full",
    )
    parser.add_argument("--knowledge-resources", type=Path)
    parser.add_argument("--max-task-concurrency", type=int, choices=(1, 2), default=1)
    parser.add_argument(
        "--max-runtime-concurrency", type=int, choices=(1, 2), default=1
    )
    parser.add_argument("--http-timeout-seconds", type=float)
    parser.add_argument("--http-retries", type=int)
    parser.add_argument(
        "--action", choices=("run", "continue", "resume"), default="run"
    )
    telemetry.add_phoenix_argument(parser)
    args = parser.parse_args(argv)
    if args.phoenix and args.mode not in {
        "run",
        "continue",
        "resume",
        "report",
        "launch",
    }:
        parser.error("--phoenix is supported for run/continue/resume/report/launch")
    with telemetry.telemetry_session(
        args.phoenix and args.mode != "launch", environment="evaluation"
    ):
        _dispatch(args, parser)


def _dispatch(args: argparse.Namespace, parser: argparse.ArgumentParser):
    root = args.root.resolve()
    if args.mode != "prepare" and (
        args.http_timeout_seconds is not None
        or args.http_retries is not None
        or args.control
        or args.api_catalog_mode != "full"
        or args.knowledge_resources is not None
        or args.max_task_concurrency != 1
        or args.max_runtime_concurrency != 1
    ):
        parser.error(
            "execution configuration is frozen; these options are prepare-only"
        )
    if args.mode == "prepare":
        if args.catalog is None:
            parser.error("prepare requires --catalog")
        suite.prepare(
            root,
            args.catalog.resolve(),
            args.cases,
            not args.control,
            api_catalog_mode=args.api_catalog_mode,
            knowledge_resources=args.knowledge_resources.resolve()
            if args.knowledge_resources is not None
            else None,
            max_task_concurrency=args.max_task_concurrency,
            max_runtime_concurrency=args.max_runtime_concurrency,
            http_timeout_seconds=args.http_timeout_seconds
            if args.http_timeout_seconds is not None
            else 300.0,
            http_retries=args.http_retries if args.http_retries is not None else 4,
        )
    elif args.mode == "preflight":
        asyncio.run(suite.preflight(root))
    elif args.mode == "doctor":
        result = asyncio.run(suite.doctor(root))
        if not result["passed"]:
            raise SystemExit(2)
    elif args.mode == "report":
        suite.report(root)
    elif args.mode == "pause":
        suite.pause(root)
    elif args.mode == "launch":
        suite.launch(root, args.action, args.cases, phoenix=args.phoenix)
    else:
        asyncio.run(suite.run(root, args.mode, args.cases))
