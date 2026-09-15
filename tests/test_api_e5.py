from types import SimpleNamespace

import numpy as np
import pytest

from abaqus_sim_agent.api_catalog.e5 import E5Encoder


class Tokenizer:
    def __init__(self):
        self.seen = []
        self.long = False

    def encode_batch(self, texts):
        self.seen.extend(texts)
        size = 513 if self.long else 2
        return [
            SimpleNamespace(ids=[1] * size, attention_mask=[1, 0], type_ids=[0] * size)
            for _ in texts
        ]


class Session:
    def get_inputs(self):
        return [
            SimpleNamespace(name="input_ids"),
            SimpleNamespace(name="attention_mask"),
        ]

    def run(self, _outputs, values):
        assert set(values) == {"input_ids", "attention_mask"}
        return [
            np.asarray(
                [[[3, 4], [100, 100]]] * len(values["input_ids"]), dtype=np.float32
            )
        ]


def test_e5_prefix_mask_pooling_normalization_and_overlength():
    encoder = E5Encoder.__new__(E5Encoder)
    encoder.tokenizer, encoder.session = Tokenizer(), Session()
    encoded = encoder.encode(["中文"], query=True)
    np.testing.assert_allclose(encoded, [[0.6, 0.8]])
    encoder.encode(["document"], query=False)
    assert encoder.tokenizer.seen == ["query: 中文", "passage: document"]
    encoder.tokenizer.long = True
    with pytest.raises(ValueError, match="512 tokens"):
        encoder.encode(["中文"], query=True)
