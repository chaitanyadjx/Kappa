"""Stage 20 Unbound-name tasks (Kappa's scope), run both systems, score with mypy."""
import json
import re
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, "/home/chaitanya/Projects/final-year-project/src")
from llm.router import generate

SP = Path("/tmp/commandcode-1000/-home-chaitanya-Projects-final-year-project/b1ee6e53-9b63-4455-b19d-1a2e0ebcc083/scratchpad")
SRC = Path("/home/chaitanya/Projects/final-year-project/src")
SUITE = SRC / "benchmark" / "data" / "pyty_defects_mypy_reproducible.jsonl"
MODEL = "google/gemma-4-31b-it-turbo"
N = 20

records = {}
for line in SUITE.read_text().splitlines():
    if line.strip():
        rec = json.loads(line)
        records[rec["id"]] = rec

pool = [r for r in records.values()
        if "unbound name" in r.get("category", "").lower()][:N]
print(f"staging {len(pool)} Unbound-name tasks")


def dedent(code):
    lines = code.splitlines()
    indents = [len(l) - len(l.lstrip()) for l in lines if l.strip()]
    shift = min(indents) if indents else 0
    return code if shift == 0 else "\n".join(l[shift:] if l.strip() else l for l in lines)


def extract(response):
    text = re.sub(r"<(?:thought|think|thinking)>.*?</(?:thought|think|thinking)>", "", response, flags=re.DOTALL).strip()
    if "```" in text:
        chunks = []
        for chunk in text.split("```")[1::2]:
            code = chunk.strip()
            first, _, rest = code.partition("\n")
            if first.strip() in ("python", "python3", "py"):
                code = rest
            if code.strip():
                chunks.append(code.strip())
        for code in reversed(chunks):
            try:
                compile(code, "<x>", "exec")
                return code
            except SyntaxError:
                continue
        return chunks[-1] if chunks else ""
    return text.strip()


c1_dir = SP / "ub20-c1"
c1_dir.mkdir(exist_ok=True)
kappa_dir = SP / "ub20-kappa"
kappa_dir.mkdir(exist_ok=True)
llm_dir = SP / "ub20-llm"
llm_dir.mkdir(exist_ok=True)
for d in (c1_dir, kappa_dir, llm_dir):
    for old in d.glob("*.py"):
        old.unlink()

ids = []
for rec in pool:
    pair_id = rec["id"]
    (c1_dir / f"{pair_id}.py").write_text(dedent(rec["code"]))
    ids.append(pair_id)

baseline_pairs = SP / "ub20-pairs-buggy.jsonl"
baseline_pairs.write_text("\n".join(json.dumps({"id": i, "file_path": str(c1_dir / f"{i}.py")}) for i in ids) + "\n")

# Step 1: score baseline
print("\n--- baseline ---")
r = subprocess.run(["conda", "run", "-n", "benchmark", "python", "-m", "benchmark.score_pyty",
                    "--pairs", str(baseline_pairs), "--suite", str(SUITE),
                    "--out", str(SP / "ub20-scores-buggy.jsonl")],
                   capture_output=True, text=True, timeout=600, cwd=str(SRC))
baseline = {}
if (SP / "ub20-scores-buggy.jsonl").exists():
    for line in (SP / "ub20-scores-buggy.jsonl").read_text().splitlines():
        if line.strip():
            s = json.loads(line)
            baseline[s["id"]] = s["verdict"]
print(f"baseline: {sum(v == 'ERROR_PRESENT' for v in baseline.values())}/{len(baseline)} ERROR_PRESENT")

# Step 2: Kappa repairs
print("\n--- kappa ---")
kappa_done = {}
for pair_id in ids:
    out = kappa_dir / f"{pair_id}.py"
    r = subprocess.run(["conda", "run", "-n", "benchmark", "python", "-m", "kappa",
                        str(c1_dir / f"{pair_id}.py"), "--parser-backend", "tree-sitter",
                        "--repo-dir", str(SRC / "kappa" / "examples" / "repo_demo"),
                        "--out", str(out), "--max-iters", "3"],
                       capture_output=True, text=True, timeout=300, cwd=str(SRC))
    if out.exists():
        kappa_done[pair_id] = str(out)
print(f"kappa outputs: {len(kappa_done)}")

kappa_pairs = SP / "ub20-pairs-kappa.jsonl"
kappa_pairs.write_text("\n".join(json.dumps({"id": k, "file_path": v}) for k, v in sorted(kappa_done.items())) + "\n")
if kappa_done:
    r = subprocess.run(["conda", "run", "-n", "benchmark", "python", "-m", "benchmark.score_pyty",
                        "--pairs", str(kappa_pairs), "--suite", str(SUITE),
                        "--out", str(SP / "ub20-scores-kappa.jsonl")],
                       capture_output=True, text=True, timeout=600, cwd=str(SRC))
kappa_scores = {}
if (SP / "ub20-scores-kappa.jsonl").exists():
    for line in (SP / "ub20-scores-kappa.jsonl").read_text().splitlines():
        if line.strip():
            s = json.loads(line)
            kappa_scores[s["id"]] = s["verdict"]

# Step 3: Gemma repairs
print("\n--- gemma ---")
gemma_done = {}
for pair_id in ids:
    code = (c1_dir / f"{pair_id}.py").read_text()
    prompt = (
        "The following Python 3 snippet has a bug (unbound name or type error).\n"
        "Fix it with a minimal change. Keep logic identical. Return ONLY the corrected snippet in one ```python code block.\n\n"
        + code
    )
    started = time.perf_counter()
    try:
        response = generate(prompt, MODEL, temperature=0.0, max_tokens=1024)
        fixed = extract(response)
        if fixed.strip():
            (llm_dir / f"{pair_id}.py").write_text(fixed)
            gemma_done[pair_id] = str(llm_dir / f"{pair_id}.py")
            print(f"  {pair_id}: {time.perf_counter() - started:.1f}s")
    except Exception as exc:
        print(f"  {pair_id}: FAILED: {exc}", flush=True)

gemma_pairs = SP / "ub20-pairs-gemma.jsonl"
gemma_pairs.write_text("\n".join(json.dumps({"id": k, "file_path": v}) for k, v in sorted(gemma_done.items())) + "\n")
if gemma_done:
    r = subprocess.run(["conda", "run", "-n", "benchmark", "python", "-m", "benchmark.score_pyty",
                        "--pairs", str(gemma_pairs), "--suite", str(SUITE),
                        "--out", str(SP / "ub20-scores-gemma.jsonl")],
                       capture_output=True, text=True, timeout=600, cwd=str(SRC))
gemma_scores = {}
if (SP / "ub20-scores-gemma.jsonl").exists():
    for line in (SP / "ub20-scores-gemma.jsonl").read_text().splitlines():
        if line.strip():
            s = json.loads(line)
            gemma_scores[s["id"]] = s["verdict"]

# Final comparison
print("\n" + "=" * 90)
print(f"{'task':<58} {'C1':<16} {'Kappa':<16} {'Gemma':<16}")
print("-" * 90)
kf = gf = 0
for pair_id in ids:
    base = baseline.get(pair_id, "?")
    k = kappa_scores.get(pair_id, "unchanged" if pair_id not in kappa_done else "?")
    g = gemma_scores.get(pair_id, "not-attempted" if pair_id not in gemma_done else "?")
    k_mark = f"✅ {k}" if k == "ERROR_GONE" else (f"❌ {k}" if k != "unchanged" else "— unchanged")
    g_mark = f"✅ {g}" if g == "ERROR_GONE" else (f"❌ {g}" if g != "not-attempted" else "— n/a")
    if k == "ERROR_GONE":
        kf += 1
    if g == "ERROR_GONE":
        gf += 1
    print(f"{pair_id:<58} {k_mark:<24} {g_mark}")
print("-" * 90)
print(f"Kappa fixed: {kf}/{len(ids)} | Gemma fixed: {gf}/{len(gemma_done) if gemma_done else 0}")

# save for analysis
(SP / "ub20-comparison.json").write_text(json.dumps({
    "baseline": baseline, "kappa_scores": kappa_scores, "gemma_scores": gemma_scores,
    "kappa_fixed": kf, "gemma_fixed": gf, "total": len(ids),
}, indent=2))
