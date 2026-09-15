"""使用固定本地ONNX与tokenizer执行E5；不下载、不调用生成模型。"""

import json
import sys
from collections.abc import Sequence
from importlib.metadata import version
from pathlib import Path

import numpy as np

from .discovery import sha


class E5Encoder:
    def __init__(self, directory: Path):
        import onnxruntime as ort
        from tokenizers import Tokenizer

        metadata = json.loads((directory / "binding.json").read_bytes())
        required = ("onnx/model.onnx", "tokenizer.json")
        for name in required:
            if sha((directory / name).read_bytes()) != metadata["files"][name]:
                raise ValueError(f"E5 model checksum mismatch: {name}")
        self.binding = {
            "model": metadata["model"],
            "revision": metadata["revision"],
            "onnx_sha256": metadata["files"][required[0]],
            "tokenizer_sha256": metadata["files"][required[1]],
            "pooling": "attention-mask-mean-l2",
            "prefixes": "query: /passage: ",
            "max_tokens": "512",
            "onnxruntime": version("onnxruntime"),
            "tokenizers": version("tokenizers"),
        }
        self.tokenizer = Tokenizer.from_file(str(directory / "tokenizer.json"))
        self.tokenizer.no_truncation()
        self.tokenizer.enable_padding(pad_id=1, pad_token="<pad>")
        options = ort.SessionOptions()
        options.intra_op_num_threads = 4
        self.session = ort.InferenceSession(
            str(directory / "onnx/model.onnx"),
            sess_options=options,
            providers=["CPUExecutionProvider"],
        )

    def encode(self, texts: Sequence[str], *, query: bool) -> np.ndarray:
        batches = []
        prefix = "query: " if query else "passage: "
        for start in range(0, len(texts), 16):
            encoded = self.tokenizer.encode_batch(
                [prefix + t for t in texts[start : start + 16]]
            )
            if any(len(e.ids) > 512 for e in encoded):
                raise ValueError(
                    f"E5 input exceeds 512 tokens in batch {start}; shorten the index view or query explicitly"
                )
            values = {
                "input_ids": np.asarray([e.ids for e in encoded], dtype=np.int64),
                "attention_mask": np.asarray(
                    [e.attention_mask for e in encoded], dtype=np.int64
                ),
                "token_type_ids": np.asarray(
                    [e.type_ids for e in encoded], dtype=np.int64
                ),
            }
            output = self.session.run(
                None, {i.name: values[i.name] for i in self.session.get_inputs()}
            )[0]
            if not isinstance(output, np.ndarray) or output.ndim != 3:
                raise ValueError("E5 must return token embeddings")
            mask = values["attention_mask"][..., None]
            pooled = (output * mask).sum(axis=1) / mask.sum(axis=1)
            pooled /= np.maximum(np.linalg.norm(pooled, axis=1, keepdims=True), 1e-12)
            batches.append(pooled.astype(np.float32))
            if not query and (start % 256 == 0 or start + 16 >= len(texts)):
                print(
                    f"E5 encoded {min(start + 16, len(texts))}/{len(texts)}",
                    file=sys.stderr,
                    flush=True,
                )
        return np.concatenate(batches)
