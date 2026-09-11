"""문장 임베딩 백엔드. 사건–기사 연결(link)에서 규칙 게이트 뒤의 유사도 점수에 쓴다.

EMBEDDING_BACKEND=st   : sentence-transformers (jhgan/ko-sroberta-multitask 등), `pip install -e ".[embeddings]"`
EMBEDDING_BACKEND=hash : 문자 2~3-gram 해싱 (의존성 없음, 예선·테스트 기본값)
"""

from __future__ import annotations

import hashlib
import logging
from typing import Protocol

import numpy as np

from ..config import Settings

log = logging.getLogger("migyeol.embeddings")


class EmbeddingBackend(Protocol):
    name: str
    dim: int

    def encode(self, texts: list[str]) -> np.ndarray: ...


class HashEmbedding:
    name = "hash"

    def __init__(self, dim: int = 4096):
        self.dim = dim

    @staticmethod
    def _grams(text: str) -> list[str]:
        t = "".join(ch for ch in (text or "").lower() if not ch.isspace())
        grams = [t[i : i + 2] for i in range(len(t) - 1)] + [t[i : i + 3] for i in range(len(t) - 2)]
        return grams or [t]

    def encode(self, texts: list[str]) -> np.ndarray:
        out = np.zeros((len(texts), self.dim), dtype=np.float32)
        for i, text in enumerate(texts):
            for g in self._grams(text):
                h = int.from_bytes(hashlib.blake2b(g.encode("utf-8"), digest_size=8).digest(), "little")
                idx = h % self.dim
                sign = 1.0 if (h >> 63) & 1 else -1.0
                out[i, idx] += sign
        norms = np.linalg.norm(out, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        return out / norms


class SentenceTransformerEmbedding:
    name = "st"

    def __init__(self, model_name: str):
        from sentence_transformers import SentenceTransformer  # lazy import (무거움)

        self.model = SentenceTransformer(model_name)
        self.model_name = model_name
        self.dim = int(self.model.get_sentence_embedding_dimension() or 768)

    def encode(self, texts: list[str]) -> np.ndarray:
        vecs = self.model.encode(texts, normalize_embeddings=True, convert_to_numpy=True, show_progress_bar=False)
        return np.asarray(vecs, dtype=np.float32)


def build_embedding(settings: Settings) -> EmbeddingBackend:
    if settings.embedding_backend == "st":
        try:
            return SentenceTransformerEmbedding(settings.embedding_model)
        except Exception as exc:  # pragma: no cover - 환경 의존
            log.warning("sentence-transformers 사용 불가(%s). hash 백엔드로 대체합니다.", exc)
    return HashEmbedding()


def cosine(a: np.ndarray, b: np.ndarray) -> float:
    return float(np.dot(a, b))


def to_bytes(vec: np.ndarray) -> bytes:
    return np.asarray(vec, dtype=np.float32).tobytes()


def from_bytes(raw: bytes) -> np.ndarray:
    return np.frombuffer(raw, dtype=np.float32)
