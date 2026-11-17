"""Stage diverse PyTyDefects tasks (NOT Unbound name) — different repair types."""
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, "/home/chaitanya/Projects/final-year-project/src")

SP = Path("/tmp/commandcode-1000/-home-chaitanya-Projects-final-year-project/b1ee6e53-9b63-4455-b19d-1a2e0ebcc083/scratchpad")
SUITE = Path("/home/chaitanya/Projects/final-year-project/src/benchmark/data/pyty_defects_mypy_reproducible.jsonl")

records = {}
for line in SUITE.read_text().splitlines():
    if line.strip():
        rec = json.loads(line)
        records[rec["id"]] = rec

# Exclude Unbound name (already tested) — pick from remaining categories
others = [r for r in records.values() if "unbound name" not in r.get("category", "").lower()]
cats = Counter(r["category"].rsplit("[", 1)[0].strip() for r in others)
print("available categories:")
for c, n in cats.most_common():
    print(f"  {n:5d}  {c}")

# Sample 2 per category, up to 8 total
def dedent_window(code):
    lines = code.splitlines()
    indents = [len(l) - len(l.lstrip()) for l in lines if l.strip()]
    shift = min(indents) if indents else 0
    if shift == 0:
        return code
    return "\n".join(l[shift:] if l.strip() else l for l in lines)

out_dir = SP / "diverse-c1"
out_dir.mkdir(exist_ok=True)
for old in out_dir.glob("*.py"):
    old.unlink()

per_cat = 2
by_cat = {}
for r in others:
    cat = r["category"].rsplit("[", 1)[0].strip()
    by_cat.setdefault(cat, []).append(r)

pairs = []
for cat, recs in sorted(by_cat.items()):
    for rec in recs[:per_cat]:
        pair_id = rec["id"]
        (out_dir / f"{pair_id}.py").write_text(dedent_window(rec["code"]))
        pairs.append({"id": pair_id, "file_path": str(out_dir / f"{pair_id}.py")})
        print(f"  {pair_id} ({cat})")
        if len(pairs) >= 10:
            break
    if len(pairs) >= 10:
        break

pairs_path = SP / "diverse-pairs-buggy.jsonl"
pairs_path.write_text("\n".join(json.dumps(p) for p in pairs) + "\n")
print(f"\nstaged {len(pairs)} diverse C1 files -> {pairs_path}")
