"""Score Kappa, Gemma, and C1 (buggy) on FixEval tasks using mypy."""
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, "/home/chaitanya/Projects/final-year-project/src")

SP = Path("/tmp/commandcode-1000/-home-chaitanya-Projects-final-year-project/b1ee6e53-9b63-4455-b19d-1a2e0ebcc083/scratchpad")
SRC = Path("/home/chaitanya/Projects/final-year-project/src")


def mypy_check(file_path):
    r = subprocess.run(
        [sys.executable, "-m", "mypy",
         "--ignore-missing-imports", "--no-error-summary",
         "--show-error-codes", "--follow-imports=skip", str(file_path)],
        capture_output=True, text=True, timeout=60,
        env={"HOME": str(Path.home()), "PATH": "/usr/bin:/bin:/usr/local/bin",
             "PYTHONPATH": ""},
    )
    errors = []
    for line in (r.stdout or "").splitlines():
        if ": error:" in line or ": note:" in line:
            errors.append(line.split(": ", 2)[-1])
    return errors


pairs = [json.loads(l) for l in (SP / "fixeval-pairs-buggy.jsonl").read_text().splitlines() if l.strip()]

print(f"{'task':<28} {'C1 (buggy)':<30} {'Kappa':<30} {'Gemma':<30}")
print("-" * 120)
kappa_fixed = gemma_fixed = 0
for pair in pairs:
    pid = pair["id"]
    c1 = Path(pair["file_path"])
    kappa_file = SP / "fixeval-kappa" / f"{pid}.py"
    gemma_file = SP / "fixeval-gemma" / f"{pid}.py"

    c1_errors = mypy_check(c1)
    c1_status = "CLEAN" if not c1_errors else f"{len(c1_errors)} err"

    if kappa_file.exists():
        k_errors = mypy_check(kappa_file)
        k_status = "CLEAN" if not k_errors else f"{len(k_errors)} err: {k_errors[0][:25]}"
        if not k_errors and c1_errors:
            kappa_fixed += 1
    else:
        k_status = "no-change"
        k_errors = c1_errors

    if gemma_file.exists():
        g_errors = mypy_check(gemma_file)
        g_status = "CLEAN" if not g_errors else f"{len(g_errors)} err: {g_errors[0][:25]}"
        if not g_errors and c1_errors:
            gemma_fixed += 1
    else:
        g_status = "not attempted"
        g_errors = []

    print(f"{pid:<28} {c1_status:<30} {k_status:<30} {g_status:<30}")
    if k_errors:
        print(f"  kappa errors: {[e[:60] for e in k_errors[:2]]}")
    if g_errors:
        print(f"  gemma errors: {[e[:60] for e in g_errors[:2]]}")

print("-" * 120)
print(f"Kappa fixed: {kappa_fixed} | Gemma fixed: {gemma_fixed}")
