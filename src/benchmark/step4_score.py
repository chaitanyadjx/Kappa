"""Step 4: Score baseline, Kappa, and Gemma repairs with the mypy oracle."""
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, "/home/chaitanya/Projects/final-year-project/src")

SRC = Path("/home/chaitanya/Projects/final-year-project/src")
SP = Path("/tmp/commandcode-1000/-home-chaitanya-Projects-final-year-project/b1ee6e53-9b63-4455-b19d-1a2e0ebcc083/scratchpad")
SUITE = SRC / "benchmark" / "data" / "pyty_defects_mypy_reproducible.jsonl"


def score(pairs_path, out_name):
    out = SP / out_name
    r = subprocess.run(
        ["conda", "run", "-n", "benchmark", "python", "-m", "benchmark.score_pyty",
         "--pairs", str(pairs_path), "--suite", str(SUITE), "--out", str(out)],
        capture_output=True, text=True, timeout=600, cwd=str(SRC),
    )
    scores = {}
    if out.exists():
        for line in out.read_text().splitlines():
            if line.strip():
                s = json.loads(line)
                scores[s["id"]] = s["verdict"]
    return scores


baseline = score(SP / "kscope-pairs-buggy.jsonl", "kscope-scores-buggy.jsonl")
kappa_pairs = SP / "kscope-pairs-kappa.jsonl"
kappa_scores = score(kappa_pairs, "kscope-scores-kappa.jsonl") if kappa_pairs.exists() else {}
gemma_pairs = SP / "kscope-pairs-gemma.jsonl"
gemma_scores = score(gemma_pairs, "kscope-scores-gemma.jsonl") if gemma_pairs.exists() else {}

all_ids = sorted(set(baseline) | set(kappa_scores) | set(gemma_scores))
kappa_stems = {json.loads(l)["id"] for l in kappa_pairs.read_text().splitlines() if l.strip()} if kappa_pairs.exists() else set()
gemma_stems = {json.loads(l)["id"] for l in gemma_pairs.read_text().splitlines() if l.strip()} if gemma_pairs.exists() else set()
kappa_fixed = gemma_fixed = 0
print(f"{'task':<58} {'C1':<16} {'Kappa':<16} {'Gemma':<16}")
print("-" * 108)
for pair_id in all_ids:
    base = baseline.get(pair_id, "?")
    k = kappa_scores.get(pair_id, "no-change" if pair_id not in kappa_stems else "?")
    k = k if k != "?" else "unchanged"
    g = gemma_scores.get(pair_id, "n/a")
    k_mark = f"✅ {k}" if k == "ERROR_GONE" else (f"❌ {k}" if k != "unchanged" else "— unchanged")
    g_mark = f"✅ {g}" if g == "ERROR_GONE" else (f"❌ {g}" if g != "n/a" else "— n/a")
    if k == "ERROR_GONE":
        kappa_fixed += 1
    if g == "ERROR_GONE":
        gemma_fixed += 1
    print(f"{pair_id:<58} {base:<16} {k_mark:<24} {g_mark}")
print("-" * 108)
print(f"Kappa fixed: {kappa_fixed}/{len(all_ids)} | Gemma fixed: {gemma_fixed}/{len(all_ids)}")
