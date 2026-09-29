"""直接读取 models.dev，并缓存最近一次有效目录；不保存凭据或改写会话。"""

import logging
from functools import cache
from pathlib import Path
from threading import Lock

import httpx
from platformdirs import user_cache_path
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from .persistence import write_bytes_atomic

MODELS_URL = "https://models.dev/api.json"
logger = logging.getLogger(__name__)

_PROVIDER_IDS = {
    "openai-chat": "openai",
    "openai-responses": "openai",
    "bedrock": "amazon-bedrock",
    "fireworks": "fireworks-ai",
    "google-cloud": "google-vertex",
    "snowflake": "snowflake-cortex",
    "together": "togetherai",
}


class ModelCatalogUnavailable(ValueError):
    pass


class CatalogModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    context_window: int
    tools: bool
    text: bool
    deprecated: bool


class _Limits(BaseModel):
    context: int | None = Field(default=None, ge=0, strict=True)


class _Modalities(BaseModel):
    output: list[str] = Field(default_factory=list)


class _SourceModel(BaseModel):
    model_config = ConfigDict(strict=True)
    limit: _Limits = Field(default_factory=_Limits)
    modalities: _Modalities = Field(default_factory=_Modalities)
    tool_call: bool = False
    status: str | None = None


class _SourceProvider(BaseModel):
    models: dict[str, _SourceModel]


_source = TypeAdapter(dict[str, _SourceProvider])


def parse_catalog(payload: bytes) -> dict[str, dict[str, CatalogModel]]:
    providers = _source.validate_json(payload)
    result = {
        provider: {
            name: CatalogModel(
                context_window=model.limit.context,
                tools=model.tool_call,
                text="text" in model.modalities.output,
                deprecated=model.status == "deprecated",
            )
            for name, model in entry.models.items()
            if name.strip()
            and model.limit.context is not None
            and model.limit.context >= 1024
        }
        for provider, entry in providers.items()
    }
    if not any(result.values()):
        raise ValueError("Empty models.dev catalog")
    return result


def download_catalog() -> bytes:
    # No provider credentials or SDK clients participate in this public request.
    response = httpx.get(MODELS_URL, timeout=10, follow_redirects=False)
    response.raise_for_status()
    return response.content


class ModelCatalog:
    def __init__(self, path: Path):
        self.path = path
        self._models: dict[str, dict[str, CatalogModel]] | None = None
        self._attempted = False
        self._lock = Lock()

    def get(self, *, refresh: bool = False) -> dict[str, dict[str, CatalogModel]]:
        """每进程首次读取及显式刷新时联网，其余读取使用内存快照。

        Note:
            下载或数据校验失败时使用最近有效的内存/磁盘缓存；首次没有可用缓存
            时明确报错，下一次显式刷新可重试。成功数据先校验，再原子更新磁盘。
            缓存写入失败时仍可在当前进程使用新数据，并保留原缓存、记录警告。
        """
        with self._lock:
            if refresh or not self._attempted:
                self._attempted = True
                try:
                    payload = download_catalog()
                    models = parse_catalog(payload)
                except (httpx.HTTPError, OSError, ValueError):
                    if self._models is None:
                        try:
                            self._models = parse_catalog(self.path.read_bytes())
                        except (OSError, ValueError):
                            pass
                    if self._models is not None:
                        logger.warning(
                            "models.dev 获取失败，继续使用最近一次有效目录缓存。"
                        )
                else:
                    self._models = models
                    try:
                        write_bytes_atomic(self.path, payload)
                    except OSError:
                        logger.warning(
                            "模型目录已获取，但本机缓存写入失败；当前进程仍可使用。"
                        )
            if self._models is None:
                raise ModelCatalogUnavailable(
                    "无法获取 models.dev 模型目录，且没有可用缓存。请检查网络后刷新模型列表。"
                )
            return self._models


@cache
def catalog_store() -> ModelCatalog:
    return ModelCatalog(
        user_cache_path("abaqus-sim-agent", appauthor=False)
        / "models"
        / "models.dev.json"
    )


def catalog() -> dict[str, dict[str, CatalogModel]]:
    return catalog_store().get()


def provider_models(provider: str) -> dict[str, CatalogModel] | None:
    provider = provider.removeprefix("gateway/")
    if provider.startswith("opencode-"):
        return None  # the existing DeepSeek-only adapter owns this limited scope
    models = catalog().get(_PROVIDER_IDS.get(provider, provider))
    if models is not None and provider == "google-cloud":
        return {
            name: value for name, value in models.items() if name.startswith("gemini")
        }
    return models


def model_metadata(provider: str, name: str) -> CatalogModel | None:
    try:
        return (provider_models(provider) or {}).get(name)
    except ModelCatalogUnavailable:
        # Low-level profile lookup may continue without catalog metadata; product
        # selection still requires a match in the available catalog.
        return None
