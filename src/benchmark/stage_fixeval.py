"""Stage 10 small FixEval pairs (full files, not hunks) for Kappa vs Gemma."""
import json
import sys
from pathlib import Path

sys.path.insert(0, "/home/chaitanya/Projects/final-year-project/src")
from benchmark.score_fixeval import detokenize

SP = Path("/tmp/commandcode-1000/-home-chaitanya-Projects-final-year-project/b1ee6e53-9b63-4455-b19d-1a2e0ebcc083/scratchpad")
data_dir = Path("/home/chaitanya/Projects/final-year-project/src/benchmark/FixEval/data/data/python/processed")

records = json.load(open(data_dir / "test.jsonl"))
print(f"total test pairs: {len(records)}")

# pick 10 with small token counts (fast to process, likely simple bugs)
small = sorted((r for r in records if 20 <= len(r["src"]) <= 80), key=lambda r: len(r["src"]))
picked = small[:10]

out_dir = SP / "fixeval-c1"
out_dir.mkdir(exist_ok=True)
pairs = []
for rec in picked:
    pair_id = rec["src_id"]
    code = detokenize(rec["src"])
    (out_dir / f"{pair_id}.py").write_text(code)
    pairs.append({"id": pair_id, "file_path": str(out_dir / f"{pair_id}.py")})
    print(f"  {pair_id}: {len(rec['src'])} tokens, verdict={rec.get('src_verdict', '?')}, {len(code)} chars")

pairs_path = SP / "fixeval-pairs-buggy.jsonl"
pairs_path.write_text("\n".join(json.dumps(p) for p in pairs) + "\n")
print(f"\nstaged {len(pairs)} full buggy files -> {pairs_path}")
