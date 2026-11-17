import json
import math


def _cosine(a: list[float], b: list[float]) -> float:
    norm_a = math.sqrt(sum(v * v for v in a))
    norm_b = math.sqrt(sum(v * v for v in b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return sum(x * y for x, y in zip(a, b)) / (norm_a * norm_b)


class NodeEmbeddingMatchTable:
    def __init__(self) -> None:
        self.entries: list[dict] = []

    def add(self, node_id: str, label: str, vector: list[float], vectors: dict | None = None) -> None:
        entry = {"id": node_id, "label": label, "kind": "node", "vector": [float(v) for v in vector]}
        if vectors:
            entry["vectors"] = {
                role: [float(v) for v in vec] for role, vec in vectors.items() if vec
            }
        self.entries.append(entry)

    def add_symbol(self, name: str, vector: list[float]) -> None:
        self.entries.append(
            {"id": f"symbol:{name}", "label": name, "kind": "symbol", "vector": [float(v) for v in vector]}
        )

    def most_similar(
        self, vector: list[float], exclude_ids: set[str] | None = None, top_k: int = 1
    ) -> list[dict]:
        excluded = exclude_ids if exclude_ids else set()
        scored = [
            (_cosine(vector, entry["vector"]), entry["id"], entry["label"])
            for entry in self.entries
            if entry["id"] not in excluded
        ]
        scored.sort(key=lambda item: (-item[0], item[1]))
        return [
            {"id": entry_id, "label": label, "score": score}
            for score, entry_id, label in scored[: max(top_k, 0)]
        ]

    def most_similar_combined(
        self,
        vectors: dict[str, list[float]],
        weights: dict[str, float] | None = None,
        exclude_ids: set[str] | None = None,
        top_k: int = 1,
    ) -> list[dict]:
        """Weighted Expectation/Connected/Self query per Method.md's Violations mechanism."""
        weights = weights or {"expectation": 0.5, "connected": 0.3, "self": 0.2}
        excluded = exclude_ids if exclude_ids else set()
        scored = []
        for entry in self.entries:
            if entry["id"] in excluded:
                continue
            stored = entry.get("vectors") or {"self": entry.get("vector", [])}
            total_weight = 0.0
            total_score = 0.0
            for role, weight in weights.items():
                stored_vec = stored.get(role)
                query_vec = vectors.get(role)
                if not stored_vec or not query_vec:
                    continue
                total_weight += weight
                total_score += weight * _cosine(query_vec, stored_vec)
            if total_weight == 0.0:
                continue
            scored.append((total_score / total_weight, entry["id"], entry["label"]))
        scored.sort(key=lambda item: (-item[0], item[1]))
        return [
            {"id": entry_id, "label": label, "score": round(score, 4)}
            for score, entry_id, label in scored[: max(top_k, 0)]
        ]

    def save(self, path) -> None:
        with open(path, "w", encoding="utf-8") as handle:
            json.dump({"entries": self.entries}, handle)

    def load(self, path) -> None:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        raw = data.get("entries", []) if isinstance(data, dict) else data
        self.entries = [dict(entry) for entry in raw]

    def __len__(self) -> int:
        return len(self.entries)


if __name__ == "__main__":
    import os
    import sys
    import tempfile
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from kappa.Embedding import Encoder

    encoder = Encoder("hash")
    symbol_texts = [
        ("sum_values", "def sum_values(values): return sum(values)"),
        ("sum_all", "def sum_all(items): return sum(items)"),
        ("main", "def main(): print(collect([1, 2, 3]))"),
    ]
    table = NodeEmbeddingMatchTable()
    for name, text in symbol_texts:
        table.add_symbol(name, encoder.embed([text])[0])
    assert len(table) == 3
    assert all(entry["kind"] == "symbol" for entry in table.entries)
    assert {entry["id"] for entry in table.entries} == {"symbol:sum_values", "symbol:sum_all", "symbol:main"}

    query = encoder.embed(["sum_value"])[0]
    top = table.most_similar(query, top_k=2)
    assert top[0]["id"] == "symbol:sum_values", top
    assert top[0]["score"] > top[1]["score"]

    rest = table.most_similar(query, exclude_ids={"symbol:sum_values"}, top_k=2)
    assert rest[0]["id"] == "symbol:sum_all", rest
    assert all(result["id"] != "symbol:sum_values" for result in rest)

    table.add("node:001", "call sum_value(v)", encoder.embed(["call sum_value(v)"])[0])
    assert len(table) == 4
    assert table.entries[-1]["kind"] == "node"
    full = table.most_similar(query, top_k=4)
    assert len(full) == 4
    assert table.most_similar(query, exclude_ids={entry["id"] for entry in table.entries}) == []

    with tempfile.TemporaryDirectory() as tmp_dir:
        path = os.path.join(tmp_dir, "match_table.json")
        table.save(path)
        reloaded = NodeEmbeddingMatchTable()
        reloaded.load(path)
        assert len(reloaded) == len(table)
        assert reloaded.entries == table.entries
        assert reloaded.most_similar(query, top_k=4) == full

    print(f"[match-table] top-1 for 'sum_value': {top[0]['id']} (score={top[0]['score']:.4f})", flush=True)
    print(f"[match-table] with exclude_ids -> {rest[0]['id']} (score={rest[0]['score']:.4f})", flush=True)
    print(f"[match-table] top-2 overall: {[(r['id'], round(r['score'], 4)) for r in full[:2]]}", flush=True)
    print(f"[match-table] save/load roundtrip ok ({len(reloaded)} entries)", flush=True)
    print("[match-table] smoke test passed", flush=True)
