"""程序管理的本机凭据文件；CLI/Web 共用，不属于偏好或会话状态。"""

import json
import os
from collections.abc import Mapping
from pathlib import Path
from typing import Literal
from uuid import uuid4

from filelock import FileLock, Timeout
from platformdirs import user_data_path
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    TypeAdapter,
    model_validator,
)
from pydantic_ai.providers.openai_codex import OpenAICodexCredentials

from .providers import ModelProvider, credential_family


def credential_path() -> Path:
    return user_data_path("abaqus-sim-agent", appauthor=False) / "auth.json"


class CredentialStoreError(ValueError):
    pass


class Credential(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True, frozen=True)
    kind: Literal["api_key", "codex_oauth"]
    secret: SecretStr = Field(min_length=1)
    refresh_token: SecretStr | None = None
    account_id: str | None = None
    revision: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_kind(self):
        if not self.secret.get_secret_value().strip():
            raise ValueError("Empty credential")
        if self.kind == "codex_oauth":
            if (
                not self.account_id
                or not self.refresh_token
                or not self.refresh_token.get_secret_value().strip()
            ):
                raise ValueError("Incomplete OAuth credential")
        elif self.refresh_token is not None or self.account_id is not None:
            raise ValueError("API key cannot contain OAuth fields")
        return self

    def disk_value(self) -> dict:
        value = self.model_dump(mode="json")
        value["secret"] = self.secret.get_secret_value()
        if self.refresh_token is not None:
            value["refresh_token"] = self.refresh_token.get_secret_value()
        return value


_records = TypeAdapter(dict[str, Credential])


class CredentialStore:
    def __init__(self, path: Path | None = None):
        self.path = path if path is not None else credential_path()

    def read(self) -> dict[str, Credential]:
        # Writers replace the entire file atomically; readers see old or new bytes.
        try:
            return _records.validate_json(self.path.read_bytes())
        except FileNotFoundError:
            return {}
        except (OSError, ValueError):
            raise CredentialStoreError(
                "无法读取本机凭据文件，请检查 auth.json 的权限或内容；未覆盖原文件。"
            ) from None

    def _update(
        self,
        provider: str,
        value: Credential | None,
        *,
        expected: Credential | None = None,
    ) -> None:
        """持锁合并单项更新；刷新使用比较后写入，防止复活已删除/替换的登录。"""
        temporary = self.path.with_name(f".{self.path.name}.{uuid4().hex}.tmp")
        try:
            self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            with FileLock(str(self.path) + ".lock", timeout=5, mode=0o600):
                records = self.read()
                if expected is not None and records.get(provider) != expected:
                    raise CredentialStoreError(
                        "凭据已在其他操作中更新或删除，请重新连接。"
                    )
                if value is None:
                    records.pop(provider, None)
                else:
                    records[provider] = value
                payload = (
                    json.dumps(
                        {name: record.disk_value() for name, record in records.items()},
                        ensure_ascii=False,
                        indent=2,
                    )
                    + "\n"
                )
                descriptor = os.open(
                    temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600
                )
                with os.fdopen(
                    descriptor, "w", encoding="utf-8", newline="\n"
                ) as stream:
                    stream.write(payload)
                    stream.flush()
                    os.fsync(stream.fileno())
                temporary.replace(self.path)
        except (OSError, Timeout):
            raise CredentialStoreError(
                "本机凭据未能保存，请检查文件权限或稍后重试。"
            ) from None
        finally:
            temporary.unlink(missing_ok=True)

    def save_key(self, provider: ModelProvider, key: SecretStr) -> None:
        if provider is ModelProvider.OPENAI_CODEX:
            raise CredentialStoreError("此提供商使用账号登录，不接受 API Key。")
        if not key.get_secret_value().strip():
            raise CredentialStoreError("请填写 API Key。")
        self._update(
            credential_family(provider),
            Credential(kind="api_key", secret=key, revision=uuid4().hex),
        )

    def save_codex(
        self, credentials: OpenAICodexCredentials, *, expected: Credential | None = None
    ) -> Credential:
        record = Credential(
            kind="codex_oauth",
            secret=SecretStr(credentials.access_token),
            refresh_token=SecretStr(credentials.refresh_token),
            account_id=credentials.account_id,
            revision=uuid4().hex,
        )
        self._update(
            ModelProvider.OPENAI_CODEX,
            record,
            expected=expected,
        )
        return record

    def remove(self, provider: ModelProvider) -> None:
        self._update(credential_family(provider), None)

    def api_keys(
        self, environment: Mapping[ModelProvider, SecretStr]
    ) -> dict[ModelProvider, SecretStr]:
        records = self.read()
        keys = dict(environment)
        for provider in ModelProvider:
            record = records.get(credential_family(provider))
            if record is not None and record.kind == "api_key":
                keys[provider] = record.secret
        return keys

    def authenticated_providers(self) -> frozenset[ModelProvider]:
        record = self.read().get(ModelProvider.OPENAI_CODEX)
        return (
            frozenset({ModelProvider.OPENAI_CODEX})
            if record is not None and record.kind == "codex_oauth"
            else frozenset()
        )

    def codex_source(self) -> "StoredCodexCredentials | None":
        if ModelProvider.OPENAI_CODEX in self.authenticated_providers():
            return StoredCodexCredentials(self)
        return None


class StoredCodexCredentials:
    def __init__(self, store: CredentialStore):
        self.store = store
        self._loaded: Credential | None = None

    async def load(self) -> OpenAICodexCredentials:
        record = self.store.read().get(ModelProvider.OPENAI_CODEX)
        if (
            record is None
            or record.kind != "codex_oauth"
            or record.refresh_token is None
            or not record.account_id
        ):
            raise CredentialStoreError("Codex 登录凭据缺失，请重新登录。")
        self._loaded = record
        return OpenAICodexCredentials(
            access_token=record.secret.get_secret_value(),
            refresh_token=record.refresh_token.get_secret_value(),
            account_id=record.account_id,
        )

    async def save(self, credentials: OpenAICodexCredentials) -> None:
        if self._loaded is None:
            raise CredentialStoreError("刷新前必须先读取登录凭据。")
        self._loaded = self.store.save_codex(credentials, expected=self._loaded)
