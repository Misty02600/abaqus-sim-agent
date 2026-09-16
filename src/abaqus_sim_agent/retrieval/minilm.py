"""固定本地 MiniLM 编码器。

均值池化、256 token padding/truncation 与归一化语义适配自 Chroma 1.5.9
`onnx_mini_lm_l6_v2.py`（Apache-2.0）；移除了下载、重试和 Chroma 接口。
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from importlib.metadata import version
from pathlib import Path
from typing import Any

import numpy as np


class MiniLMEncoder:
    MODEL_NAME = "sentence-transformers/all-MiniLM-L6-v2"
    DIMENSIONS = 384
    MAX_TOKENS = 256

    def __init__(self, directory: str | Path) -> None:
        import onnxruntime as ort
        from tokenizers import Tokenizer

        self.directory = Path(directory).expanduser().resolve(strict=True)
        self.model_path = self.directory / "model.onnx"
        self.tokenizer_path = self.directory / "tokenizer.json"
        if not self.model_path.is_file() or not self.tokenizer_path.is_file():
            raise FileNotFoundError(
                "MiniLM encoder directory requires model.onnx and tokenizer.json"
            )
        self.binding = {
            "model": self.MODEL_NAME,
            "dimensions": self.DIMENSIONS,
            "pooling": "attention-mask-mean-l2",
            "max_tokens": self.MAX_TOKENS,
            "model_sha256": _sha256_file(self.model_path),
            "tokenizer_sha256": _sha256_file(self.tokenizer_path),
            "onnxruntime": version("onnxruntime"),
            "tokenizers": version("tokenizers"),
        }
        self.tokenizer = Tokenizer.from_file(str(self.tokenizer_path))
        self.tokenizer.enable_truncation(max_length=self.MAX_TOKENS)
        self.tokenizer.enable_padding(
            pad_id=0,
            pad_token="[PAD]",
            length=self.MAX_TOKENS,
        )
        options = ort.SessionOptions()
        options.log_severity_level = 3
        options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        self.session = ort.InferenceSession(
            str(self.model_path),
            sess_options=options,
            providers=["CPUExecutionProvider"],
        )

    @classmethod
    def from_binding(cls, binding: dict[str, Any]) -> MiniLMEncoder:
        encoder = cls(str(binding["directory"]))
        expected = dict(binding)
        expected.pop("directory", None)
        if encoder.binding != expected:
            raise ValueError(
                "MiniLM encoder binding does not match local files/runtime"
            )
        return encoder

    def manifest_binding(self) -> dict[str, Any]:
        return {"directory": str(self.directory), **self.binding}

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return self.encode(texts).tolist()

    def embed_query(self, text: str) -> list[float]:
        return self.encode([text])[0].tolist()

    def encode(self, texts: Sequence[str], *, batch_size: int = 32) -> np.ndarray:
        if not texts:
            return np.empty((0, self.DIMENSIONS), dtype=np.float32)
        batches: list[np.ndarray] = []
        for start in range(0, len(texts), batch_size):
            encoded = self.tokenizer.encode_batch(
                list(texts[start : start + batch_size])
            )
            values = {
                "input_ids": np.asarray([item.ids for item in encoded], dtype=np.int64),
                "attention_mask": np.asarray(
                    [item.attention_mask for item in encoded], dtype=np.int64
                ),
                "token_type_ids": np.zeros(
                    (len(encoded), self.MAX_TOKENS), dtype=np.int64
                ),
            }
            output = self.session.run(
                None,
                {item.name: values[item.name] for item in self.session.get_inputs()},
            )[0]
            if not isinstance(output, np.ndarray) or output.ndim != 3:
                raise ValueError("MiniLM encoder must return token embeddings")
            mask = values["attention_mask"][..., None]
            pooled = (output * mask).sum(axis=1) / np.maximum(mask.sum(axis=1), 1e-9)
            pooled /= np.maximum(np.linalg.norm(pooled, axis=1, keepdims=True), 1e-12)
            batches.append(pooled.astype(np.float32))
        result = np.concatenate(batches)
        if result.shape[1] != self.DIMENSIONS:
            raise ValueError(
                "MiniLM embedding dimension differs from the bound contract"
            )
        return result


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
