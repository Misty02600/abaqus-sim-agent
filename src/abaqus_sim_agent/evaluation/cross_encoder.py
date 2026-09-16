"""固定本地ONNX模型的离线重排；不进入产品Agent调用链。"""

import math
import time
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import onnxruntime as ort
from tokenizers import Tokenizer

from abaqus_sim_agent.retrieval.types import Document

from .passage_selection import body_first_excerpt


@dataclass(frozen=True)
class ScoredPassage:
    document: Document
    score: float
    pair_tokens: int
    truncated: bool


def candidate_union(*sources: Sequence[Document]) -> list[Document]:
    documents: dict[str, Document] = {}
    for source in sources:
        for document in source:
            if not document.id:
                raise ValueError("candidate must have a record ID")
            previous = documents.get(document.id)
            if previous is not None and (
                previous.page_content != document.page_content
                or previous.metadata != document.metadata
            ):
                raise ValueError("conflicting records with the same ID")
            documents[document.id] = document
    return list(documents.values())


def passage_text(document: Document) -> str:
    title = str(document.metadata.get("title", ""))
    path = document.metadata.get("heading_path")
    section = (
        " > ".join(str(item) for item in path)
        if isinstance(path, list) and path
        else str(document.metadata.get("section_title", ""))
    )
    body = body_first_excerpt(document, limit=len(document.page_content) + 1)
    return f"Title: {title}\nSection: {section}\n{body}"


def rank_scores(
    documents: Sequence[Document], scores: Sequence[float], tokens: Sequence[int]
) -> list[ScoredPassage]:
    if len(documents) != len(scores) or len(scores) != len(tokens):
        raise ValueError("score count must match candidate count")
    if not all(math.isfinite(score) for score in scores):
        raise ValueError("scores must be finite")
    rows = [
        ScoredPassage(document, score, length, length > 512)
        for document, score, length in zip(documents, scores, tokens, strict=True)
    ]
    return sorted(rows, key=lambda row: (-row.score, row.document.id or ""))


class OnnxPassageReranker:
    def __init__(self, model_directory: Path) -> None:
        """加载已核对来源和hash的本地模型，不联网或执行远端Python代码。

        Args:
            model_directory: 包含tokenizer.json和onnx/model.onnx的固定模型目录。

        Note:
            固定CPU、4线程、batch=8和512 token pair长度。返回原始logit，不是
            答案正确率或物理适用性置信度。模型文件的hash由实验driver负责核验。
        """
        self.tokenizer = Tokenizer.from_file(str(model_directory / "tokenizer.json"))
        self.full_tokenizer = Tokenizer.from_file(
            str(model_directory / "tokenizer.json")
        )
        self.full_tokenizer.no_truncation()
        self.full_tokenizer.no_padding()
        self.tokenizer.enable_truncation(max_length=512)
        self.tokenizer.enable_padding(pad_id=0, pad_token="[PAD]")
        options = ort.SessionOptions()
        options.intra_op_num_threads = 4
        options.inter_op_num_threads = 1
        options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
        self.session = ort.InferenceSession(
            str(model_directory / "onnx/model.onnx"),
            sess_options=options,
            providers=["CPUExecutionProvider"],
        )
        self.input_names = {item.name for item in self.session.get_inputs()}
        if self.input_names != {"input_ids", "attention_mask", "token_type_ids"}:
            raise ValueError("unexpected model inputs")

    def rank(
        self, query: str, documents: Sequence[Document]
    ) -> tuple[list[ScoredPassage], float]:
        if not query.strip():
            raise ValueError("query must not be empty")
        unique = candidate_union(documents)
        started = time.perf_counter()
        pairs = [(query, passage_text(document)) for document in unique]
        lengths = [len(self.full_tokenizer.encode(*pair).ids) for pair in pairs]
        scores: list[float] = []
        for offset in range(0, len(pairs), 8):
            encoded = self.tokenizer.encode_batch(pairs[offset : offset + 8])
            features = {
                "input_ids": np.array([item.ids for item in encoded], dtype=np.int64),
                "attention_mask": np.array(
                    [item.attention_mask for item in encoded], dtype=np.int64
                ),
                "token_type_ids": np.array(
                    [item.type_ids for item in encoded], dtype=np.int64
                ),
            }
            logits = np.asarray(self.session.run(None, features)[0])
            if logits.shape != (len(encoded), 1):
                raise ValueError("unexpected model output shape")
            scores.extend(float(value) for value in logits[:, 0])
        return rank_scores(unique, scores, lengths), time.perf_counter() - started
