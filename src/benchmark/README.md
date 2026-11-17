# Benchmark Runner + Kappa Evaluation (Python focus)

Current focus: **Python code → Parser → Benchmark**. Two pieces live here:

1. **`runner.py`** — the T1/C1/C2/Score evaluator from `research/Method.md`: given a suite record with buggy code (C1) and repaired code (C2), it runs the same checks on both and reports the repair delta.
2. **`score_pyty.py` + `convert_pyty.py`** — the **PyTyDefects** benchmark (ICSE 2024, `benchmark/PyTy/`): 2,248+ real Python static-error→fix pairs, scored by "mypy error present on C1 → gone on C2".

Inactive for now (kept, not in use): `score_fixeval.py` (requires the FixEval dataset, currently not extracted).

## 1. PyTyDefects (Python static-violation benchmark)

Dataset already converted into runner-ready suites under `benchmark/data/`:

- `pyty_defects.jsonl` — 8,004 hunk pairs (C1 buggy / C2 dev fix, category = Pyre rule)
- `pyty_defects_mypy_reproducible.jsonl` — **1,402 pairs where mypy verifiably reproduces the target error on C1 (recommended scoring subset; oracle-valid)**

Score a candidate file against a pair's mypy oracle:

```bash
cd src
# single: ID, FILE_PATH -> Score (verdict ERROR_GONE iff the target error disappeared)
conda run -n benchmark python -m benchmark.score_pyty \
  --id <record-id> --file candidate.py

# with automatic before/after: also scores the record's own buggy code C1
conda run -n benchmark python -m benchmark.score_pyty \
  --id <record-id> --file candidate.py --suite benchmark/data/pyty_defects_mypy_reproducible.jsonl

# batch: pairs.jsonl = one {"id": ..., "file_path": ...} per line
conda run -n benchmark python -m benchmark.score_pyty \
  --pairs benchmark/suites/pyty-pairs.jsonl --out benchmark/suites/pyty-scores.jsonl
```

Verdicts: `ERROR_GONE` (exit 0, fixed) / `ERROR_PRESENT` (exit 1) / `NOT_REPRODUCIBLE` (exit 2, mypy cannot see the target error standalone — use the reproducible subset to avoid these) / `SCORE_ERROR` (exit 3).

Kappa end-to-end on PyTy pairs: run `python -m kappa repaired_candidate.py` stages or feed Kappa's patched output as the `--file`. A pair's own `code_after` is the reference C2 — scoring it must yield ERROR_GONE.

## 2. Runner (JSONL suites, repair-delta mode)

```bash
conda run -n benchmark python -m benchmark.runner benchmark/examples/delta.jsonl \
  --checks syntax,undefined_names,tests
```

Record format: `{"id" (T1), "code" (C1), "code_after" (C2), "label", "category", "tests" or "entry_point"+"input_output", "run"}` — see `benchmark/examples/delta.jsonl`. With `code_after` the runner prints per-case `delta: X -> Y violations (reduced|same|increased); tests fail -> pass` and aggregates a `repair_delta` metrics block. Full field reference and metric definitions are in the git history of this README; core semantics: exit 0 iff no BAD cases; `repair_delta` is the headline metric for repair pairs.

## 3. Kappa pipeline (sibling package `src/kappa/`)

```bash
conda run -n benchmark python -m kappa kappa/examples/demo_violations.py \
  --parser-backend tree-sitter --out benchmark/suites/demo-repaired.py
```

- `--parser-backend ast|tree-sitter|libcst|spoon` — Knowledge DB extraction backend (all three Python backends verified to produce identical KBs on the demo; tree-sitter is error-tolerant on buggy code; `spoon` falls back to tree-sitter-java without Maven)
- `--backend hash|unixcoder` — embedding backend (hash default until torch+transformers are installed)
- Verdict `accepted`/`partial` exits 0; `rejected` exits 1

To measure Kappa on PyTyDefects: for each pair, run Kappa on `code` (C1) → repaired file → `score_pyty --id <id> --file repaired.py` and/or the runner's `repair_delta` with `code_after` = Kappa's output.
