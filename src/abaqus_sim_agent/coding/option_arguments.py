"""CLI 与 Web 服务命令共用的本次运行参数适配。"""

import argparse

from .model import DeepSeekGateway
from .options import RunOptions


def add_run_arguments(
    parser: argparse.ArgumentParser, *, include_model: bool = True
) -> None:
    if include_model:
        parser.add_argument(
            "--model-provider", type=DeepSeekGateway, choices=tuple(DeepSeekGateway)
        )
        parser.add_argument(
            "--model", help="新会话必须明确选择 --list-models 列表中的型号"
        )
    parser.add_argument("--http-retries", type=int)
    parser.add_argument("--http-timeout-seconds", type=float)
    parser.add_argument(
        "--reasoning-policy", choices=("provider_default", "enabled_high")
    )
    parser.add_argument(
        "--api-catalog-mode",
        choices=(
            "full",
            "full_with_discovery",
            "automatic_verified_only",
            "automatic_exact_signature",
        ),
    )
    parser.add_argument("--static-preflight-mode", choices=("off", "on"))
    knowledge = parser.add_mutually_exclusive_group()
    knowledge.add_argument("--knowledge-pack", action="append", metavar="PACK_ID")
    knowledge.add_argument(
        "--no-knowledge-packs",
        dest="knowledge_pack",
        action="store_const",
        const=[],
        help="本次不启用任何私有知识包",
    )


def options_from_args(args: argparse.Namespace) -> RunOptions:
    values = {
        name: getattr(args, argument)
        for name, argument in {
            "model_provider": "model_provider",
            "model_name": "model",
            "http_retries": "http_retries",
            "http_timeout_seconds": "http_timeout_seconds",
            "reasoning_policy": "reasoning_policy",
            "api_catalog_mode": "api_catalog_mode",
            "static_preflight_mode": "static_preflight_mode",
            "knowledge_pack_ids": "knowledge_pack",
        }.items()
        if getattr(args, argument, None) is not None
    }
    if "knowledge_pack_ids" in values:
        values["knowledge_pack_ids"] = tuple(values["knowledge_pack_ids"])
    return RunOptions.model_validate(values)
