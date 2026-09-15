"""显式英文BM25分析；原文与Exact定位不经过词干变换。"""

import hashlib
import re
import threading
import unicodedata
from functools import lru_cache
from importlib.metadata import version
from pathlib import Path

from snowballstemmer.english_stemmer import EnglishStemmer

LEXICAL_ENGLISH = "english-snowball-v1"
_LOCAL = threading.local()
_CAMEL = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_WORDS = re.compile(
    r"[A-Za-z]+(?:'[A-Za-z]+)?|[0-9]+(?:\.[0-9]+)?|[^\W\d_]+", re.UNICODE
)
_SYMBOLS = re.compile(
    r"(?<![A-Za-z0-9_])(?:[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)+"
    r"|[a-z]+[A-Z][A-Za-z0-9_]*|[A-Z]{2,}[A-Z0-9_]*|[A-Za-z][A-Za-z0-9]*_[A-Za-z0-9_]+)(?![A-Za-z0-9_])"
)
_KEEP = frozenset({"no", "nor", "not", "cannot", "without", "only"})


def source_symbols(text: str) -> tuple[str, ...]:
    """保留可识别的符号形态，不声称识别了所有API或全部约束。"""
    return tuple(dict.fromkeys(m.group() for m in _SYMBOLS.finditer(text)))


@lru_cache(maxsize=32768)
def _stem(word: str) -> str:
    # Snowball instances are mutable and not safe to share across threads.
    if not hasattr(_LOCAL, "stemmer"):
        _LOCAL.stemmer = EnglishStemmer()
    return _LOCAL.stemmer.stemWord(word)


class EnglishLexicalAnalyzer:
    def __init__(self):
        package_version = version("snowballstemmer")
        if package_version != "3.0.1":
            raise ValueError("english-snowball-v1 requires snowballstemmer 3.0.1")
        raw = (Path(__file__).parent / "data/english_stopwords.txt").read_bytes()
        words = " ".join(
            line for line in raw.decode().splitlines() if not line.startswith("#")
        ).split()
        self.stop_words = frozenset(
            w for w in words if w not in _KEEP and not w.endswith("n't")
        )
        self.binding = {
            "profile": LEXICAL_ENGLISH,
            "stemmer": "snowball-english-python",
            "version": package_version,
            "stop_words_sha256": hashlib.sha256(raw).hexdigest(),
            "symbol_policy": "ascii-symbol-literal-plus-derived-words-v1",
            "negation_policy": "retain",
        }

    def __call__(self, text: str) -> list[str]:
        """生成符号字面项与普通检索词，不修改输入。

        Note:
            符号整体大小写保留；另生成用于意图发现的拆词。普通英文词才做NFKC、
            casefold和Snowball处理；这不是语义等价或API适用性校验。
        """
        result = ["symbol:" + s for s in source_symbols(text)]
        expanded = _CAMEL.sub(" ", unicodedata.normalize("NFKC", text)).replace(
            "’", "'"
        )
        expanded = re.sub(
            r"(?<=[\u3400-\u9fff])(?=[A-Za-z])|(?<=[A-Za-z])(?=[\u3400-\u9fff])",
            " ",
            expanded,
        )
        for match in _WORDS.finditer(expanded):
            word = match.group().casefold()
            if word in self.stop_words:
                continue
            if word.isascii() and word.isalpha() and word not in _KEEP:
                word = _stem(word)
            result.append("word:" + word)
        return result
