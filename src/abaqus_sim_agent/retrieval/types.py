from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass(slots=True)
class Document:
    page_content: str
    metadata: dict[str, Any] = field(default_factory=dict)
    id: str | None = None

    @classmethod
    def model_validate_json(cls, value: str | bytes) -> Document:
        data = json.loads(value)
        return cls(
            id=data.get("id"),
            page_content=str(data["page_content"]),
            metadata=dict(data.get("metadata", {})),
        )

    def model_dump_json(self) -> str:
        return json.dumps(
            {
                "id": self.id,
                "page_content": self.page_content,
                "metadata": self.metadata,
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )

    def model_copy(self, *, deep: bool = False) -> Document:
        return Document(
            id=self.id,
            page_content=self.page_content,
            metadata=deepcopy(self.metadata) if deep else dict(self.metadata),
        )


class Embeddings(Protocol):
    def embed_documents(self, texts: list[str]) -> list[list[float]]: ...

    def embed_query(self, text: str) -> list[float]: ...
