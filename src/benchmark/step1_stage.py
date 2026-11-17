"""Step 1: Stage dedented C1 files from Kappa-scope PyTyDefects tasks."""
import json
import sys
from pathlib import Path

sys.path.insert(0, "/home/chaitanya/Projects/final-year-project/src")

SP = Path("/tmp/commandcode-1000/-home-chaitanya-Projects-final-year-project/b1ee6e53-9b63-4455-b19d-1a2e0ebcc083/scratchpad")
SUITE = Path("/home/chaitanya/Projects/final-year-project/src/benchmark/data/pyty_defects_mypy_reproducible.jsonl")

records = {}
for line in SUITE.read_text().splitlines():
    if line.strip():
        rec = json.loads(line)
        records[rec["id"]] = rec

kappa_scope = [r for r in records.values() if "unbound name" in r.get("category", "").lower()][:8]
print(f"Kappa-scope tasks: {len(kappa_scope)}")


def dedent_window(code: str) -> str:
    lines = code.splitlines()
    indents = [len(l) - len(l.lstrip()) for l in lines if l.strip()]
    shift = min(indents) if indents else 0
    if shift == 0:
        return code
    return "\n".join(l[shift:] if l.strip() else l for l in lines)


c1_dir = SP / "kscope-c1"
c1_dir.mkdir(exist_ok=True)
pairs = []
for rec in kappa_scope:
    pair_id = rec["id"]
    (c1_dir / f"{pair_id}.py").write_text(dedent_window(rec["code"]))
    pairs.append({"id": pair_id, "file_path": str(c1_dir / f"{pair_id}.py")})
    print(f"  {pair_id} ({rec['category']})")

pairs_path = SP / "kscope-pairs-buggy.jsonl"
pairs_path.write_text("\n".join(json.dumps(p) for p in pairs) + "\n")
print(f"\nstaged {len(pairs)} C1 files -> {pairs_path}")
