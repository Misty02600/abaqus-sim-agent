"""CLI 与 Web 服务命令共用的偏好参数适配；不负责会话恢复或文件读取。"""

import argparse
from pathlib import Path

from .model import DeepSeekGateway
from .preferences import RunPreferences, resolve_preference_paths


def add_preference_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--model-provider", type=DeepSeekGateway, choices=tuple(DeepSeekGateway)
    )
    parser.add_argument("--model", help="本次模型名称；未指定时读取项目/用户偏好")
    parser.add_argument(
        "--context-window", type=int, help="仅在窗口未知或自定义部署时指定"
    )
    parser.add_argument("--model-base-url", help="自定义模型 API 地址")
    parser.add_argument("--http-retries", type=int)
    parser.add_argument("--http-timeout-seconds", type=float)
    parser.add_argument(
        "--reasoning-policy", choices=("provider_default", "enabled_high")
    )
    parser.add_argument(
        "--launcher", type=Path, help="Abaqus launcher；未指定时读取偏好或内置路径"
    )
    parser.add_argument("--skills-dir", type=Path)
    parser.add_argument(
        "--api-catalog-dir", type=Path, help="必需的 Catalog，可由项目/用户偏好提供"
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
    parser.add_argument("--abqpy-static-wheel", type=Path)
    parser.add_argument("--static-preflight-cache-dir", type=Path)
    knowledge = parser.add_mutually_exclusive_group()
    knowledge.add_argument("--knowledge-pack", action="append", metavar="PACK_ID")
    knowledge.add_argument(
        "--no-knowledge-packs",
        dest="knowledge_pack",
        action="store_const",
        const=[],
        help="本次不启用任何私有知识包，覆盖偏好",
    )


def preferences_from_args(args: argparse.Namespace) -> RunPreferences:
    values = {
        name: getattr(args, argument)
        for name, argument in {
            "model_provider": "model_provider",
            "model_name": "model",
            "context_window": "context_window",
            "model_base_url": "model_base_url",
            "http_retries": "http_retries",
            "http_timeout_seconds": "http_timeout_seconds",
            "reasoning_policy": "reasoning_policy",
            "launcher_path": "launcher",
            "skill_library": "skills_dir",
            "api_catalog_dir": "api_catalog_dir",
            "api_catalog_mode": "api_catalog_mode",
            "static_preflight_mode": "static_preflight_mode",
            "abqpy_static_wheel": "abqpy_static_wheel",
            "static_preflight_cache_dir": "static_preflight_cache_dir",
            "knowledge_pack_ids": "knowledge_pack",
        }.items()
        if getattr(args, argument) is not None
    }
    if "knowledge_pack_ids" in values:
        values["knowledge_pack_ids"] = tuple(values["knowledge_pack_ids"])
    return resolve_preference_paths(RunPreferences.model_validate(values), Path.cwd())
