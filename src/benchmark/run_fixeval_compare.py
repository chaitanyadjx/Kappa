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
C1 = SP / "fixeval-c1"
KD = SP / "fixeval-kappa"
GD = SP / "fixeval-gemma"
for d in (KD, GD):
    d.mkdir(exist_ok=True)

pairs = [json.loads(l) for l in (SP / "fixeval-pairs-buggy.jsonl").read_text().splitlines() if l.strip()]
print(f"{len(pairs)} tasks")


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


print("\n--- KAPPA ---")
for pair in pairs:
    pid = pair["id"]
    out = KD / f"{pid}.py"
    r = subprocess.run(
        ["conda", "run", "-n", "benchmark", "python", "-m", "kappa",
         pair["file_path"], "--out", str(out), "--max-iters", "3"],
        capture_output=True, text=True, timeout=120, cwd=str(SRC),
    )
    verdict = [l for l in r.stdout.splitlines() if "verdict" in l]
    print(f"  {pid}: {verdict[-1].strip()[:60] if verdict else 'no output'}")

print("\n--- GEMMA ---")
for pair in pairs:
    pid = pair["id"]
    code = Path(pair["file_path"]).read_text()
    prompt = f"The following Python 3 program has a bug. Fix it with a minimal change. Return ONLY the corrected full program in one ```python code block.\n\n{code}"
    started = time.perf_counter()
    try:
        response = generate(prompt, "google/gemma-4-31b-it-turbo", temperature=0.0, max_tokens=1024)
        fixed = extract(response)
        if fixed.strip():
            (GD / f"{pid}.py").write_text(fixed)
            print(f"  {pid}: {time.perf_counter() - started:.1f}s, {len(fixed)} chars")
        else:
            print(f"  {pid}: EMPTY extraction")
    except Exception as exc:
        print(f"  {pid}: FAILED: {exc}")
