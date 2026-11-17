import hashlib
import math
import os
import re

_HASH_DIM = 256
_EPSILON = 1e-8
_UNIXCODER_MODEL = "microsoft/unixcoder-base"


def _l2_normalize(vector: list[float]) -> list[float]:
    norm = math.sqrt(sum(v * v for v in vector))
    if norm == 0.0:
        # all-zero input (e.g. empty text) would make downstream norm divisions
        # divide by zero; emit a tiny epsilon vector instead
        return [_EPSILON] * len(vector)
    return [v / norm for v in vector]


def _tokenize(text: str) -> list[str]:
    tokens: list[str] = []
    for raw in text.replace("NEW_LINE", " ").split():
        parts = re.findall(r"[A-Za-z_][A-Za-z0-9_]*|\d+", raw)
        for part in parts:
            tokens.append(part.lower())
            for piece in re.findall(r"[A-Z]+(?![a-z])|[A-Z][a-z]+|[a-z]+|\d+", part):
                tokens.append(piece.lower())
    return tokens


def _hash_vector(text: str) -> list[float]:
    counts = [0.0] * _HASH_DIM

    def bump(feature: str, weight: float) -> None:
        bucket = int(hashlib.sha1(feature.encode()).hexdigest(), 16) % _HASH_DIM
        counts[bucket] += weight

    lowered = " ".join(_tokenize(text))
    for token in _tokenize(text):
        bump("w:" + token, 2.0)
    for i in range(len(lowered) - 2):
        bump(lowered[i : i + 3], 1.0)
    return _l2_normalize(counts)


def _cosine(a: list[float], b: list[float]) -> float:
    norm_a = math.sqrt(sum(v * v for v in a))
    norm_b = math.sqrt(sum(v * v for v in b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return sum(x * y for x, y in zip(a, b)) / (norm_a * norm_b)


class Encoder:
    def __init__(self, backend: str | None = None):
        self._backend = "hash"
        self._dim = _HASH_DIM
        self._model = None
        self._torch = None
        requested = backend or "auto"
        if requested == "unixcoder" and os.environ.get("KAPPA_EMBEDDING") == "hash":
            print("[kappa] KAPPA_EMBEDDING=hash overrides requested unixcoder backend", flush=True)
            return
        if requested == "hash" or os.environ.get("KAPPA_EMBEDDING") == "hash":
            return
        if requested not in ("unixcoder", "auto"):
            print(f"[kappa] unknown embedding backend '{requested}'; using hash", flush=True)
            return
        if self._load_unixcoder():
            self._backend = "unixcoder"

    def _load_unixcoder(self) -> bool:
        try:
            import torch
            import transformers
            from unixcoder.unixcoder import UniXcoder

            model = UniXcoder(_UNIXCODER_MODEL)
            model.eval()
        except Exception as exc:
            print(
                f"[kappa] unixcoder backend unavailable ({type(exc).__name__}: {exc}); falling back to hash",
                flush=True,
            )
            return False
        self._torch = torch
        self._model = model
        self._dim = int(model.config.hidden_size)
        return True

    @property
    def backend(self) -> str:
        return self._backend

    @property
    def dim(self) -> int:
        return self._dim

    def embed(self, texts: list[str]) -> list[list[float]]:
        if isinstance(texts, str):
            texts = [texts]
        if not texts:
            return []
        if self._backend == "unixcoder":
            try:
                return self._embed_unixcoder(texts)
            except Exception as exc:
                print(
                    f"[kappa] unixcoder embedding failed ({type(exc).__name__}: {exc}); falling back to hash",
                    flush=True,
                )
                self._backend = "hash"
                self._dim = _HASH_DIM
                self._model = None
                self._torch = None
        return [_hash_vector(text) for text in texts]

    def _embed_unixcoder(self, texts: list[str]) -> list[list[float]]:
        torch = self._torch
        token_ids = self._model.tokenize(texts, mode="<encoder-only>", max_length=512, padding=True)
        source_ids = torch.tensor(token_ids, dtype=torch.long)
        with torch.no_grad():
            token_embeddings, _ = self._model(source_ids)
            mask = source_ids.ne(self._model.config.pad_token_id)
            pooled = (token_embeddings * mask.unsqueeze(-1)).sum(1) / mask.sum(-1).clamp(min=1).unsqueeze(-1)
        return [_l2_normalize(row) for row in pooled.to(torch.float32).cpu().tolist()]


def decode_nearest(vector: list[float], table) -> dict:
    return table.most_similar(vector, top_k=1)[0]


if __name__ == "__main__":
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from kappa.NodeEmbeddingMatchTable import NodeEmbeddingMatchTable

    encoder = Encoder()
    texts = [
        "def total(values): return sum(values)",
        "def total(vals): return sum(vals)",
        "import os\nos.environ['X']",
    ]
    vectors = encoder.embed(texts)
    assert encoder.backend == "hash", f"expected hash backend in this env, got {encoder.backend}"
    assert encoder.dim == _HASH_DIM == len(vectors[0])
    assert all(abs(sum(v * v for v in vec) - 1.0) < 1e-6 for vec in vectors)
    assert encoder.embed(texts) == vectors, "hash embeddings must be deterministic"
    empty = encoder.embed([""])[0]
    assert all(abs(v) < 1e-6 for v in empty) and sum(v * v for v in empty) > 0

    similar = _cosine(vectors[0], vectors[1])
    dissimilar = _cosine(vectors[0], vectors[2])
    assert similar > dissimilar, f"similar {similar} <= dissimilar {dissimilar}"

    table = NodeEmbeddingMatchTable()
    table.add("node:001", "def total(values): return sum(values)", vectors[0])
    table.add("node:002", "import os; os.environ['X']", vectors[2])
    decoded = decode_nearest(vectors[1], table)
    assert decoded["id"] == "node:001", decoded

    print(f"[embedding] backend={encoder.backend} dim={encoder.dim}", flush=True)
    print(f"[embedding] cos(similar)={similar:.4f} > cos(dissimilar)={dissimilar:.4f}", flush=True)
    print(f"[embedding] decode_nearest -> {decoded}", flush=True)
    print("[embedding] smoke test passed", flush=True)
