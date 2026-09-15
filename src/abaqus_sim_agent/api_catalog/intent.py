"""宿主持有原始需求，检索只使用单独的操作表达。"""

import hashlib

from pydantic import BaseModel, ConfigDict, Field, field_validator


class DiscoveryIntent(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")
    original_request: str = Field(min_length=1, max_length=8000)
    operation: str = Field(min_length=1, max_length=1000)

    @field_validator("original_request", "operation")
    @classmethod
    def nonblank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("intent fields must not be blank")
        return value

    @property
    def original_sha256(self) -> str:
        return hashlib.sha256(self.original_request.encode()).hexdigest()
