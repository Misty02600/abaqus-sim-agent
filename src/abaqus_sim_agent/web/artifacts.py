from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from PIL import Image, UnidentifiedImageError

from .contracts import Registration, RunSnapshot, WebError
from .controller import RunController
from .storage import plain_path

TEXT_LIMIT = 256 * 1024
DOWNLOAD_LIMIT = 200 * 1024 * 1024
TEXT_SUFFIXES = {
    ".py",
    ".txt",
    ".json",
    ".md",
    ".inp",
    ".log",
    ".dat",
    ".msg",
    ".sta",
    ".csv",
}


@dataclass(frozen=True)
class ArtifactContent:
    path: Path
    filename: str
    media_type: str


def snapshot_file(
    controller: RunController, run_id: str, file_id: str, *, preview: bool
) -> ArtifactContent:
    """复制已记录产物并核对hash后才返回；读取与本服务启动/恢复互斥。"""

    def copy(registration: Registration, snapshot: RunSnapshot) -> ArtifactContent:
        ref = next((f for f in snapshot.view.files if f.id == file_id), None)
        if ref is None:
            raise WebError("artifact_not_found", "此修订中没有该产物", 404)
        relative = ref.relative_path
        if (
            "\\" in relative
            or ":" in relative
            or Path(relative).is_absolute()
            or ".." in Path(relative).parts
        ):
            raise WebError("unsafe_artifact", "产物路径无效", 400)
        source = plain_path(registration.request.workspace / relative, directory=False)
        if (
            not source.is_relative_to(registration.request.workspace)
            or not source.is_file()
        ):
            raise WebError("artifact_missing", "产物已移动或不存在", 404)
        suffix = source.suffix.lower()
        if ref.size_bytes > DOWNLOAD_LIMIT:
            raise WebError(
                "artifact_too_large",
                "该产物超过网页读取上限，请从本地工作目录读取",
                413,
            )
        if preview and suffix not in TEXT_SUFFIXES | {".png"}:
            raise WebError("preview_not_supported", "此类型仅支持下载", 415)
        if preview and suffix in TEXT_SUFFIXES and ref.size_bytes > TEXT_LIMIT:
            raise WebError("preview_too_large", "文本超过预览上限，请下载查看", 413)
        cache = plain_path(controller.registry.root / "cache", directory=True)
        # Active responses own their copies; do not evict them while checking the bounded cache.
        for old in cache.glob("view-*.tmp"):
            if old.is_symlink() or old.is_junction():
                raise WebError("unsafe_cache", "产物缓存路径异常")
        if (
            sum(p.stat().st_size for p in cache.glob("view-*.tmp")) + ref.size_bytes
            > 512 * 1024 * 1024
        ):
            raise WebError(
                "cache_full", "临时下载缓存已满，请等待其他下载结束或检查本地缓存", 503
            )
        target = cache / f"view-{uuid4().hex}.tmp"
        sha = hashlib.sha256()
        size = 0
        try:
            with source.open("rb") as incoming, target.open("xb") as outgoing:
                os.set_inheritable(outgoing.fileno(), False)
                while block := incoming.read(1024 * 1024):
                    size += len(block)
                    if size > DOWNLOAD_LIMIT or size > ref.size_bytes:
                        raise WebError(
                            "artifact_changed", "产物内容已改变，不能沿用旧验收"
                        )
                    sha.update(block)
                    outgoing.write(block)
            if size != ref.size_bytes or sha.hexdigest() != ref.sha256:
                raise WebError("artifact_changed", "产物内容已改变，不能沿用旧验收")
            media_type = "application/octet-stream"
            if preview:
                media_type = "text/plain; charset=utf-8"
                if suffix == ".png":
                    try:
                        with Image.open(target) as image:
                            if image.format != "PNG":
                                raise WebError("invalid_image", "PNG格式无效", 415)
                            if image.width * image.height > 20_000_000:
                                raise WebError(
                                    "image_too_large", "图片尺寸超过预览上限", 413
                                )
                            image.verify()
                    except (
                        UnidentifiedImageError,
                        OSError,
                        SyntaxError,
                        Image.DecompressionBombError,
                    ) as exc:
                        raise WebError("invalid_image", "PNG格式无效", 415) from exc
                    media_type = "image/png"
            return ArtifactContent(target, source.name, media_type)
        except BaseException:
            target.unlink(missing_ok=True)
            raise

    result = controller.with_idle_run(run_id, copy)
    assert isinstance(result, ArtifactContent)
    return result
