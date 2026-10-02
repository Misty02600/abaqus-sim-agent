"""API修复测评的参数解析与退出码映射。"""

import argparse
import asyncio
from collections.abc import Sequence
from pathlib import Path

from abaqus_sim_agent.evaluation import api_repair as repair


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Run the Abaqus 2024 multi-API repair benchmark."
    )
    parser.add_argument("--run-root", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, default=repair.DEFAULT_MANIFEST)
    parser.add_argument(
        "--model-provider",
        type=repair.DeepSeekGateway,
        choices=list(repair.DeepSeekGateway),
        default=repair.DeepSeekGateway.OFFICIAL,
    )
    parser.add_argument("--model", default=repair.DEEPSEEK_V4_FLASH)
    parser.add_argument(
        "--http-retries", type=int, default=repair.DEFAULT_MODEL_HTTP_RETRIES
    )
    parser.add_argument(
        "--api-catalog-mode",
        choices=(
            "off",
            "full",
            "automatic_verified_only",
            "automatic_exact_signature",
        ),
        default="off",
    )
    parser.add_argument("--api-catalog-dir", type=Path)
    parser.add_argument("--static-preflight-mode", choices=("off", "on"), default="off")
    parser.add_argument("--abqpy-static-wheel", type=Path)
    parser.add_argument("--static-preflight-cache-dir", type=Path)
    parser.add_argument("--repetition", type=int, default=1)
    parser.add_argument(
        "--case-id",
        action="append",
        dest="case_ids",
        help="Run only this case; may be repeated",
    )
    args = parser.parse_args(argv)
    gate = asyncio.run(
        repair.run_evaluation(
            args.run_root.resolve(),
            manifest_path=args.manifest,
            model_gateway=args.model_provider,
            model_name=args.model,
            http_retries=args.http_retries,
            api_catalog_mode=args.api_catalog_mode,
            api_catalog_dir=args.api_catalog_dir,
            case_ids=tuple(args.case_ids) if args.case_ids else None,
            static_preflight_mode=args.static_preflight_mode,
            abqpy_static_wheel=args.abqpy_static_wheel,
            static_preflight_cache_dir=args.static_preflight_cache_dir,
            repetition=args.repetition,
        )
    )
    raise SystemExit(0 if gate.passed else 1)
