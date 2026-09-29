"""通过 SDK 解析本机认证来源；列表发现不刷新令牌或访问云元数据服务。"""

import json
import os
from pathlib import Path

from pydantic_ai.exceptions import UserError

from .providers import ModelProvider


def _aws_local_credentials() -> bool:
    from botocore.exceptions import BotoCoreError
    from botocore.session import Session

    try:
        session = Session()
        resolver = session.get_component("credential_provider")
        # Keep SDK ordering/profile resolution, but discovery must not run a
        # credential_process, call STS, or query ECS/EC2 metadata. Those sources
        # remain available to the full SDK chain when a model is explicitly used.
        for provider in tuple(resolver.providers):
            if provider.METHOD not in {"env", "shared-credentials-file", "config-file"}:
                resolver.remove(provider.METHOD)
        credentials = session.get_credentials()
        if credentials is None:
            return False
        value = credentials.get_frozen_credentials()
        return bool(value.access_key and value.secret_key)
    except (BotoCoreError, OSError, ValueError):
        return False


def _google_local_credentials() -> bool:
    from google.auth import _cloud_sdk, environment_vars
    from google.auth.exceptions import GoogleAuthError
    from google.oauth2.credentials import Credentials as UserCredentials
    from google.oauth2.service_account import Credentials as ServiceCredentials

    # The SDK owns the platform-specific gcloud location (including
    # CLOUDSDK_CONFIG). Its loader also validates the actual credential shape.
    filename = os.environ.get(environment_vars.CREDENTIALS)
    if not filename:
        filename = _cloud_sdk.get_application_default_credentials_path()
    try:
        info = json.loads(Path(filename).read_text(encoding="utf-8"))
        if not isinstance(info, dict):
            return False
        if info.get("type") == "authorized_user":
            UserCredentials.from_authorized_user_info(info)
            return True
        if info.get("type") == "service_account":
            ServiceCredentials.from_service_account_info(info)
            return True
        # Federated/executable/impersonated sources are resolved by the normal
        # SDK at use time; discovery must not refresh or execute those sources.
        return False
    except (GoogleAuthError, OSError, ValueError, TypeError):
        return False


def local_authenticated_providers() -> frozenset[ModelProvider]:
    """返回 SDK 可从本地解析凭据的提供商，不代表已通过远端认证。

    Note:
        AWS 使用 SDK 的环境、共享凭据和配置文件链，Google 使用 SDK 的 ADC 路径与解析。
        需要远端认证、角色交换或外部命令的来源不在列举时探测；明确选用模型时仍由原生
        SDK 的完整认证链处理，不能把本次检测未命中当作该提供商不受支持。
    """
    result: set[ModelProvider] = set()
    try:
        from pydantic_ai.providers.openai_codex import OpenAICodexCredentials

        directory = Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex")
        OpenAICodexCredentials.from_codex_cli_auth(
            json.loads((directory / "auth.json").read_text(encoding="utf-8"))
        )
        result.add(ModelProvider.OPENAI_CODEX)
    except (OSError, ValueError, ImportError, UserError):
        pass
    try:
        if _aws_local_credentials():
            result.update((ModelProvider.BEDROCK, ModelProvider.BEDROCK_MANTLE))
    except ImportError:
        pass
    try:
        if _google_local_credentials():
            result.add(ModelProvider.GOOGLE_CLOUD)
    except ImportError:
        pass
    return frozenset(result)
