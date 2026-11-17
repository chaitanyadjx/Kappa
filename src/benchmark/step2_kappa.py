"""Step 2: Run Kappa on each C1 file (tree-sitter parser + repo context)."""
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, "/home/chaitanya/Projects/final-year-project/src")

SRC = Path("/home/chaitanya/Projects/final-year-project/src")
SP = Path("/tmp/commandcode-1000/-home-chaitanya-Projects-final-year-project/b1ee6e53-9b63-4455-b19d-1a2e0ebcc083/scratchpad")
REPO_DIR = SRC / "kappa" / "examples" / "repo_demo"
pairs_path = SP / "kscope-pairs-buggy.jsonl"
pairs = [json.loads(l) for l in pairs_path.read_text().splitlines() if l.strip()]

kappa_dir = SP / "kscope-kappa"
kappa_dir.mkdir(exist_ok=True)
for old in kappa_dir.glob("*.py"):
    old.unlink()

for pair in pairs:
    pair_id = pair["id"]
    out_file = kappa_dir / f"{pair_id}.py"
    r = subprocess.run(
        ["conda", "run", "-n", "benchmark", "python", "-m", "kappa",
         pair["file_path"], "--parser-backend", "tree-sitter",
         "--repo-dir", str(REPO_DIR), "--out", str(out_file), "--max-iters", "3"],
        capture_output=True, text=True, timeout=300, cwd=str(SRC),
    )
    if out_file.exists():
        print(f"  {pair_id}: repaired")
    else:
        print(f"  {pair_id}: unchanged (no applicable patches)")

outputs = sorted(kappa_dir.glob("*.py"))
kappa_pairs = SP / "kscope-pairs-kappa.jsonl"
kappa_pairs.write_text(
    "\n".join(json.dumps({"id": p.stem, "file_path": str(p)}) for p in outputs) + "\n"
)
print(f"\n{len(outputs)} repaired files -> {kappa_pairs}")
