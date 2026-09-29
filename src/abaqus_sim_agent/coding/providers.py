"""模型提供商目录与依赖检查；列举时不导入可选 SDK 或读取凭据。"""

import os
from enum import StrEnum
from importlib.util import find_spec
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict


class ModelProvider(StrEnum):
    OFFICIAL = "deepseek"
    OPENCODE_ZEN = "opencode-zen"
    OPENCODE_GO = "opencode-go"
    OPENAI = "openai"
    OPENAI_CHAT = "openai-chat"
    OPENAI_RESPONSES = "openai-responses"
    ANTHROPIC = "anthropic"
    ALIBABA = "alibaba"
    GOOGLE = "google"
    GOOGLE_CLOUD = "google-cloud"
    AZURE = "azure"
    AZURE_RESPONSES = "azure-responses"
    BEDROCK = "bedrock"
    BEDROCK_MANTLE = "bedrock-mantle"
    CEREBRAS = "cerebras"
    COHERE = "cohere"
    CRUSOE = "crusoe"
    FIREWORKS = "fireworks"
    GITHUB = "github"
    GITHUB_COPILOT = "github-copilot"
    OPENAI_CODEX = "openai-codex"
    GROQ = "groq"
    HEROKU = "heroku"
    HUGGINGFACE = "huggingface"
    LITELLM = "litellm"
    MISTRAL = "mistral"
    MOONSHOTAI = "moonshotai"
    NEBIUS = "nebius"
    OLLAMA = "ollama"
    OPENROUTER = "openrouter"
    OVHCLOUD = "ovhcloud"
    SAMBANOVA = "sambanova"
    SNOWFLAKE = "snowflake"
    TOGETHER = "together"
    TYPESAFE = "typesafe"
    VERCEL = "vercel"
    VLLM = "vllm"
    ZAI = "zai"
    GATEWAY_OPENAI = "gateway/openai"
    GATEWAY_ANTHROPIC = "gateway/anthropic"
    GATEWAY_GOOGLE = "gateway/google"
    GATEWAY_GOOGLE_CLOUD = "gateway/google-cloud"
    GATEWAY_GROQ = "gateway/groq"
    GATEWAY_BEDROCK = "gateway/bedrock"


API_KEY_ENVIRONMENTS: dict[str, tuple[str, ...]] = {
    "deepseek": ("DEEPSEEK_API_KEY",),
    "opencode-go": ("OPENCODE_API_KEY",),
    "opencode-zen": ("OPENCODE_API_KEY",),
    "openai": ("OPENAI_API_KEY",),
    "anthropic": ("ANTHROPIC_API_KEY",),
    "alibaba": ("ALIBABA_API_KEY", "DASHSCOPE_API_KEY"),
    "google": ("GOOGLE_API_KEY", "GEMINI_API_KEY"),
    "google-cloud": ("GOOGLE_API_KEY", "GEMINI_API_KEY"),
    "azure": ("AZURE_OPENAI_API_KEY",),
    "bedrock": ("AWS_BEARER_TOKEN_BEDROCK",),
    "bedrock-mantle": ("AWS_BEARER_TOKEN_BEDROCK",),
    "cerebras": ("CEREBRAS_API_KEY",),
    "cohere": ("CO_API_KEY",),
    "crusoe": ("CRUSOE_API_KEY",),
    "fireworks": ("FIREWORKS_API_KEY",),
    "github": ("GITHUB_API_KEY",),
    "github-copilot": (
        "GITHUB_COPILOT_API_KEY",
        "GITHUB_COPILOT_API_TOKEN",
        "COPILOT_GITHUB_TOKEN",
    ),
    "groq": ("GROQ_API_KEY",),
    "heroku": ("HEROKU_INFERENCE_KEY",),
    "huggingface": ("HF_TOKEN",),
    "litellm": ("LITELLM_API_KEY",),
    "mistral": ("MISTRAL_API_KEY",),
    "moonshotai": ("MOONSHOTAI_API_KEY",),
    "nebius": ("NEBIUS_API_KEY",),
    "ollama": ("OLLAMA_API_KEY",),
    "openrouter": ("OPENROUTER_API_KEY",),
    "ovhcloud": ("OVHCLOUD_API_KEY",),
    "sambanova": ("SAMBANOVA_API_KEY",),
    "snowflake": ("SNOWFLAKE_TOKEN",),
    "together": ("TOGETHER_API_KEY",),
    "typesafe": ("TYPESAFE_API_KEY",),
    "vercel": ("VERCEL_AI_GATEWAY_API_KEY", "VERCEL_OIDC_TOKEN"),
    "vllm": ("VLLM_API_KEY",),
    "zai": ("ZAI_API_KEY",),
    "gateway": ("PYDANTIC_AI_GATEWAY_API_KEY", "PAIG_API_KEY"),
}

_SDK_MODULES = {
    "anthropic": "anthropic",
    "google": "google.genai",
    "google-cloud": "google.genai",
    "bedrock": "boto3",
    "cohere": "cohere",
    "groq": "groq",
    "huggingface": "huggingface_hub",
    "mistral": "mistralai",
    "typesafe": "typesafe_sdk",
}
LOCAL_AUTH_PROVIDERS = frozenset(
    {
        "bedrock",
        "bedrock-mantle",
        "google-cloud",
        "openai-codex",
        "ollama",
        "vllm",
        "litellm",
    }
)


def credential_family(provider: str) -> str:
    if provider.startswith("gateway/"):
        return "gateway"
    return {
        "openai-chat": "openai",
        "openai-responses": "openai",
        "azure-responses": "azure",
    }.get(provider, provider)


def api_key_environments(provider: str) -> tuple[str, ...]:
    return API_KEY_ENVIRONMENTS.get(credential_family(provider), ())


_CONNECTION_ENVIRONMENTS: dict[str, tuple[str, ...]] = {
    "openai": ("OPENAI_BASE_URL", "OPENAI_ORG_ID", "OPENAI_PROJECT_ID"),
    "azure": ("AZURE_OPENAI_ENDPOINT", "OPENAI_API_VERSION"),
    "google-cloud": ("GOOGLE_CLOUD_PROJECT", "GOOGLE_CLOUD_LOCATION"),
    "bedrock": (
        "AWS_REGION",
        "AWS_DEFAULT_REGION",
        "AWS_PROFILE",
        "AWS_DEFAULT_PROFILE",
    ),
    "bedrock-mantle": (
        "AWS_REGION",
        "AWS_DEFAULT_REGION",
        "AWS_PROFILE",
        "AWS_DEFAULT_PROFILE",
    ),
    "cohere": ("CO_BASE_URL",),
    "groq": ("GROQ_BASE_URL",),
    "heroku": ("HEROKU_INFERENCE_URL",),
    "ollama": ("OLLAMA_BASE_URL",),
    "vllm": ("VLLM_BASE_URL",),
    "snowflake": ("SNOWFLAKE_ACCOUNT",),
    "typesafe": ("TYPESAFE_BASE_URL",),
    "sambanova": ("SAMBANOVA_BASE_URL",),
    "github-copilot": (
        "GITHUB_COPILOT_BASE_URL",
        "COPILOT_API_URL",
        "GITHUB_COPILOT_API_BASE",
    ),
    "gateway": ("PYDANTIC_AI_GATEWAY_BASE_URL", "PAIG_BASE_URL"),
}


def connection_environment(provider: str) -> dict[str, str]:
    values = {
        name: os.environ.get(name, "")
        for name in _CONNECTION_ENVIRONMENTS.get(credential_family(provider), ())
    }
    for name, value in values.items():
        if value and (
            "URL" in name or "ENDPOINT" in name or name == "GITHUB_COPILOT_API_BASE"
        ):
            url = urlsplit(value)
            if url.username or url.password or url.query or url.fragment:
                raise ValueError(f"{name} 不得包含凭据、查询参数或 fragment。")
    return values


class ProviderUnavailable(ValueError):
    pass


SDK_DEFAULT_RETRY_PROVIDERS = frozenset(
    {"bedrock", "gateway/bedrock", "huggingface", "mistral", "typesafe"}
)


def require_provider_dependencies(provider: ModelProvider) -> None:
    name = provider.value.removeprefix("gateway/")
    module = _SDK_MODULES.get(name, "openai")
    try:
        available = find_spec(module) is not None
    except (ModuleNotFoundError, ValueError):
        available = False
    if not available:
        distribution = {
            "google.genai": "google-genai",
            "huggingface_hub": "huggingface-hub",
            "typesafe_sdk": "typesafe-sdk",
        }.get(module, module)
        raise ProviderUnavailable(f"缺少依赖：{distribution}，请先安装。")
    try:
        # Import only after selection, so one missing SDK cannot break the list.
        from pydantic_ai.providers import infer_provider_class

        infer_provider_class("deepseek" if name.startswith("opencode-") else name)
    except ImportError as exc:
        missing = getattr(exc, "name", None)
        if missing:
            raise ProviderUnavailable(f"缺少依赖：{missing}，请先安装。") from exc
        raise ProviderUnavailable(
            f"{provider.value} 的依赖版本不兼容，请重新同步依赖。"
        ) from exc


class ProviderOption(BaseModel):
    model_config = ConfigDict(frozen=True)
    id: ModelProvider
    label: str
    models: tuple[str, ...]
    api_key_environment: str | None
    local_auth: bool
    login: str | None = None


def provider_catalog(*, include_models: bool = True) -> tuple[ProviderOption, ...]:
    from .model_catalog import provider_models

    labels = {
        "deepseek": "DeepSeek",
        "opencode-go": "OpenCode Go",
        "opencode-zen": "OpenCode Zen",
        "openai": "OpenAI (Responses)",
        "openai-chat": "OpenAI (Chat Completions)",
        "google": "Google Gemini",
        "google-cloud": "Google Cloud",
        "openai-codex": "OpenAI Codex",
        "github-copilot": "GitHub Copilot",
    }
    result = []
    for provider in ModelProvider:
        models: tuple[str, ...] = ()
        if include_models and provider in (
            ModelProvider.OPENCODE_GO,
            ModelProvider.OPENCODE_ZEN,
        ):
            models = ("deepseek-v4-flash", "deepseek-v4-pro", "deepseek-v4.1-flash")
        metadata = provider_models(provider) if include_models else None
        if metadata is not None:
            models = tuple(
                sorted(
                    name
                    for name, entry in metadata.items()
                    if entry.tools and entry.text and not entry.deprecated
                )
            )
        environments = api_key_environments(provider)
        result.append(
            ProviderOption(
                id=provider,
                label=labels.get(provider, provider.value),
                models=models,
                api_key_environment=environments[0] if environments else None,
                local_auth=provider in LOCAL_AUTH_PROVIDERS,
                login=provider.value
                if provider
                in (ModelProvider.OPENAI_CODEX, ModelProvider.GITHUB_COPILOT)
                else None,
            )
        )
    return tuple(result)
