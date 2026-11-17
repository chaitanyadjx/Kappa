"""Java benchmark: stage C1 buggy files, run Kappa, run Gemma, score with Sorald.

Pipeline:
  step 1 - stage buggy Java files from benchmark/examples/java_benchmark
  step 2 - run Kappa (tree-sitter backend) on each -> scratchpad/java-kappa
  step 3 - run Gemma (RapidRouter) on each      -> scratchpad/java-gemma
  step 4 - score all three sets with Sorald mine -> violation counts
"""
import json
import re
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, "/home/chaitanya/Projects/final-year-project/src")

SRC = Path("/home/chaitanya/Projects/final-year-project/src")
SP = Path("/tmp/commandcode-1000/-home-chaitanya-Projects-final-year-project/b1ee6e53-9b63-4455-b19d-1a2e0ebcc083/scratchpad")
BENCH = SRC / "benchmark" / "examples" / "java_benchmark"
JAR = SRC / "benchmark" / "sorald-0.8.6-jar-with-dependencies.jar"
MODEL = "google/gemma-4-31b-it-turbo"
KAPPA_RULES = ["S2095", "S1854", "S1481", "S1172", "S106", "S1116", "S1104", "S1488"]


def sorald_mine(file_path: Path, rule_keys: list[str] | None = None) -> tuple[int, dict]:
    cmd = ["java", "-jar", str(JAR), "mine", "--source", str(file_path)]
    if rule_keys:
        cmd += ["--rule-keys", ",".join(rule_keys)]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    counts = {}
    total = 0
    for line in (r.stdout or "").splitlines():
        if "=" in line and not line.startswith("SLF4J"):
            key, val = line.strip().split("=", 1)
            n = int(val)
            if n > 0:
                counts[key] = n
                total += n
    return total, counts


def extract_code(response: str) -> str:
    text = re.sub(r"<(?:thought|think|thinking)>.*?</(?:thought|think|thinking)>", "", response, flags=re.DOTALL).strip()
    if "```" in text:
        chunks = []
        for chunk in text.split("```")[1::2]:
            code = chunk.strip()
            first, _, rest = code.partition("\n")
            if first.strip() in ("java", "Java"):
                code = rest
            if code.strip():
                chunks.append(code.strip())
        return chunks[-1] if chunks else ""
    return text.strip()


print("=" * 60)
print("JAVA BENCHMARK: Kappa vs Gemma (Sorald oracle)")
print("=" * 60)

# ---- step 1: stage buggy files
print("\n[step 1] staging buggy Java files")
pairs = []
for f in sorted(BENCH.glob("*.java")):
    pairs.append({"id": f.stem, "file_path": str(f)})
    before, per_rule = sorald_mine(f, KAPPA_RULES)
    print(f"  {f.stem}: {before} Kappa-scope violations {per_rule}")
(SP / "java-pairs-buggy.jsonl").write_text("\n".join(json.dumps(p) for p in pairs) + "\n")

# ---- step 2: run Kappa
print("\n[step 2] running Kappa (tree-sitter backend)")
kappa_dir = SP / "java-kappa"
kappa_dir.mkdir(exist_ok=True)
for old in kappa_dir.glob("*.java"):
    old.unlink()
for pair in pairs:
    pair_id = pair["id"]
    out_file = kappa_dir / f"{pair_id}.java"
    r = subprocess.run(
        ["conda", "run", "-n", "benchmark", "python", "-m", "kappa",
         pair["file_path"], "--parser-backend", "tree-sitter",
         "--out", str(out_file), "--max-iters", "3"],
        capture_output=True, text=True, timeout=300, cwd=str(SRC),
    )
    if out_file.exists():
        after, _ = sorald_mine(out_file, KAPPA_RULES)
        before, _ = sorald_mine(pair["file_path"], KAPPA_RULES)
        print(f"  {pair_id}: violations {before} -> {after}")
    else:
        before, _ = sorald_mine(pair["file_path"], KAPPA_RULES)
        print(f"  {pair_id}: unchanged ({before} violations remain)")

# ---- step 3: run Gemma
print("\n[step 3] running Gemma (RapidRouter)")
from llm.router import generate
gemma_dir = SP / "java-gemma"
gemma_dir.mkdir(exist_ok=True)
for old in gemma_dir.glob("*.java"):
    old.unlink()
for pair in pairs:
    pair_id = pair["id"]
    code = Path(pair["file_path"]).read_text()
    prompt = (
        "The following Java class has SonarQube violations (resource leaks, unused "
        "assignments, unreachable code, unused variables, hardcoded secrets).\n"
        "Fix them with minimal changes. Keep the class logic identical. "
        "Return ONLY the corrected Java class in one ```java code block.\n\n"
        + code
    )
    started = time.perf_counter()
    try:
        response = generate(prompt, MODEL, temperature=0.0, max_tokens=1024)
        fixed = extract_code(response)
        if fixed.strip():
            (gemma_dir / f"{pair_id}.java").write_text(fixed)
            after, _ = sorald_mine(gemma_dir / f"{pair_id}.java", KAPPA_RULES)
            before, _ = sorald_mine(pair["file_path"], KAPPA_RULES)
            print(f"  {pair_id}: violations {before} -> {after}  ({time.perf_counter() - started:.1f}s)")
        else:
            print(f"  {pair_id}: EMPTY extraction")
    except Exception as exc:
        print(f"  {pair_id}: FAILED: {exc}")

# ---- step 4: summary
print("\n" + "=" * 60)
print("SUMMARY")
print("=" * 60)
rows = []
for pair in pairs:
    pid = pair["id"]
    before, _ = sorald_mine(pair["file_path"], KAPPA_RULES)
    k_after = before
    if (kappa_dir / f"{pid}.java").exists():
        k_after, _ = sorald_mine(kappa_dir / f"{pid}.java", KAPPA_RULES)
    g_after = before
    if (gemma_dir / f"{pid}.java").exists():
        g_after, _ = sorald_mine(gemma_dir / f"{pid}.java", KAPPA_RULES)
    rows.append({"id": pid, "before": before, "kappa_after": k_after, "gemma_after": g_after})
    print(f"  {pid:25s} before={before:2d}  kappa={k_after:2d}  gemma={g_after:2d}")

kb = sum(r["before"] for r in rows)
ka = sum(r["kappa_after"] for r in rows)
ga = sum(r["gemma_after"] for r in rows)
kappa_fixed = sum(r["before"] - r["kappa_after"] for r in rows)
gemma_fixed = sum(r["before"] - r["gemma_after"] for r in rows)
union_after_list = [min(r["kappa_after"], r["gemma_after"]) for r in rows]  # best of either per task
union_fixed = sum(r["before"] - u for r, u in zip(rows, union_after_list))
union_violations = sum(union_after_list)
print(f"\n  total violations : {kb}")
print(f"  kappa repaired   : {kappa_fixed} ({100 * kappa_fixed / max(kb, 1):.0f}%)  remaining={ka}")
print(f"  gemma repaired   : {gemma_fixed} ({100 * gemma_fixed / max(kb, 1):.0f}%)  remaining={ga}")
print(f"  UNION repaired   : {union_fixed} ({100 * union_fixed / max(kb, 1):.0f}%)  remaining={union_violations}")
per_task_union = [
    {**r, "union_after": min(r["kappa_after"], r["gemma_after"])} for r in rows
]
(SP / "java-summary.json").write_text(json.dumps(per_task_union, indent=2) + "\n")
print(f"\ndetails -> {SP / 'java-summary.json'}")
