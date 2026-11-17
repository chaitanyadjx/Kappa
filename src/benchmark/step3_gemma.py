"""Step 3: Run Gemma on each C1 file (RapidRouter, lighter model)."""
import json
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, "/home/chaitanya/Projects/final-year-project/src")
from llm.router import generate

SP = Path("/tmp/commandcode-1000/-home-chaitanya-Projects-final-year-project/b1ee6e53-9b63-4455-b19d-1a2e0ebcc083/scratchpad")
MODEL = "google/gemma-4-31b-it-turbo"
pairs_path = SP / "kscope-pairs-buggy.jsonl"
pairs = [json.loads(l) for l in pairs_path.read_text().splitlines() if l.strip()]


def extract_code(response):
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


llm_dir = SP / "kscope-gemma"
llm_dir.mkdir(exist_ok=True)
for old in llm_dir.glob("*.py"):
    old.unlink()

for pair in pairs:
    pair_id = pair["id"]
    code = Path(pair["file_path"]).read_text()
    prompt = (
        "The following Python 3 snippet has a bug (unbound name, type error, or logic error).\n"
        "Fix it with a minimal change. Keep logic identical. Return ONLY the corrected snippet in one ```python code block.\n\n"
        + code
    )
    started = time.perf_counter()
    try:
        response = generate(prompt, MODEL, temperature=0.0, max_tokens=1024)
        fixed = extract_code(response)
        if fixed.strip():
            (llm_dir / f"{pair_id}.py").write_text(fixed)
            print(f"  {pair_id}: {time.perf_counter() - started:.1f}s, {len(fixed)} chars")
        else:
            print(f"  {pair_id}: EMPTY extraction ({time.perf_counter() - started:.1f}s)")
    except Exception as exc:
        print(f"  {pair_id}: FAILED: {exc}")

outputs = sorted(llm_dir.glob("*.py"))
gemma_pairs = SP / "kscope-pairs-gemma.jsonl"
gemma_pairs.write_text(
    "\n".join(json.dumps({"id": p.stem, "file_path": str(p)}) for p in outputs) + "\n"
)
print(f"\n{len(outputs)} gemma repairs -> {gemma_pairs}")
