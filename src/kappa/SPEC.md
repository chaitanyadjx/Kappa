# Kappa Pipeline — Implementation Spec (frozen contracts)

Package: `src/kappa/`. Python 3.12, stdlib-only EXCEPT `Embedding.py` may optionally
import `torch`/`transformers` (graceful fallback required). Pipeline (Method.md):
Parser -> Knowledge DB -> Constructor (graph) -> Detector -> Patcher -> Updater (loop)
-> Integrater -> independent Tester. Deterministic and lightweight; NO LLM calls.

Style rules for all modules: no comments except where logic is genuinely non-obvious;
all public functions from this spec must exist with these exact names/signatures;
modules import each other as `from kappa.X import y`; every module must run its own
smoke test under `if __name__ == "__main__":`; print progress with flush=True in CLIs.

## Demo fixture (used for smoke tests)

`examples/demo_violations.py`:

```python
import os


def sum_values(values):
    return sum(values)


def collect(values):
    total = 0
    for v in values:
        total = total + sum_value(v)
    handle = open("/tmp/data.txt")
    debug = os.environ["DEBUG"]
    eval(debug)
    return total + len(handle.read())
    print("done")


def main():
    print(collect([1, 2, 3]))
```

Expected violations: `sum_value` undefined call (suggestion: `sum_values`),
resource leak (`open` without with/close), env read without default,
eval usage (security warning), unreachable `print("done")`.

## Knowledge DB (Parser.py)

JSON-serializable dict:

```json
{
  "file": "demo_violations.py",
  "source_hash": "<sha256 of source>",
  "symbols": {"defined": [{"name": "sum_values", "kind": "function", "line": 4}]},
  "categories": {
    "calls":          [{"id": "calls:L10:C20", "line": 10, "col": 20, "name": "sum_value",
                        "receiver": null, "args": ["v"], "in_function": "collect"}],
    "library_usage":  [{"id": "import:L1", "line": 1, "module": "os", "names": [], "alias": null}],
    "dataflow":       [{"id": "dataflow:L9", "line": 9, "kind": "assign", "name": "total", "in_function": "collect"},
                       {"id": "resource:L12", "line": 12, "kind": "file", "op": "open", "name": "handle", "closed": false}],
    "configuration":  [{"id": "config:L13", "line": 13, "kind": "env", "name": "DEBUG", "has_default": false}],
    "control_flow":   [{"id": "flow:L8", "line": 8, "kind": "for", "in_function": "collect"},
                       {"id": "flow:L16", "line": 16, "kind": "return", "in_function": "collect"}],
    "security":       [{"id": "security:L14", "line": 14, "rule": "eval_use", "detail": "eval() call"}]
  },
  "lines": 22
}
```

Ids are unique, deterministic, formatted `<category>:L<line>[:C<col>]`. Record one
entry per: call site, import statement, assignment/aug-assignment/for-target/parameter
(dataflow), file/socket/db-ish resource (`open(...)`, `connect(...)`, `socket(...)`,
`session(...)` marked closed=True only under `with` or when `.close()` is called on
the bound name in the same function), env/config read (`os.environ[...]`,
`os.environ.get`, `os.getenv`, attribute reads on `config`/`settings` objects),
branch/loop/try/return statement, and security-sensitive construct
(`eval`, `exec`, `pickle.loads/dumps`, `subprocess` with `shell=True`,
literal assignments to names matching `(?i)(password|secret|token|api_key)\b`).

Required API:

```python
CATEGORIES = ["calls", "library_usage", "dataflow", "configuration", "control_flow", "security"]
def parse_source(source: str, filename: str = "<source>") -> dict
def parse_file(path) -> dict
def save_kb(kb: dict, path) -> None
def load_kb(path) -> dict
```

## Graph (Constructor.py)

```json
{
  "nodes": [{"id": "node:001", "kind": "call|import|dataflow|resource|config|flow|security|function",
             "category": "calls", "line": 10, "ref": "calls:L10:C20",
             "label": "sum_value(v)",
             "expectation_text": "call sum_value(values) expects result usable as number; defined in this file or imported",
             "connected_text": "inside function collect; connected to: assignment total (L9), loop for (L8), return (L16)",
             "self_text": "sum_value(v)"}],
  "edges": [{"from": "node:001", "to": "node:002", "kind": "calls|flows_to|uses_import|guarded_by|contains"}]
}
```

One node per Knowledge DB entry plus one `function` node per defined function
(ref `function:L<line>`). Edges: function contains its statements; a dataflow def
node `flows_to` later nodes in the same function that read the same name; an import
node `uses_import` every call/attribute node whose root name it provides; resource
and config nodes connect `flows_to` the return of their function. Node ids are
`node:001`, `node:002`, ... in line order.

```python
def build_graph(kb: dict) -> dict
def save_graph(graph: dict, path) -> None
def load_graph(path) -> dict
```

## Embeddings (Embedding.py) and match table (NodeEmbeddingMatchTable.py)

```python
class Encoder:
    def __init__(self, backend: str | None = None)   # "unixcoder" | "hash" | None (auto)
    @property
    def backend(self) -> str
    @property
    def dim(self) -> int
    def embed(self, texts: list[str]) -> list[list[float]]   # L2-normalized vectors

def decode_nearest(vector: list[float], table) -> dict   # table.most_similar(...) top-1 passthrough
```

- backend `unixcoder`: only if `torch` and `transformers` import AND
  `KAPPA_EMBEDDING != "hash"`; load `microsoft/unixcoder-base` through
  `src/unixcoder/unixcoder.py::UniXcoder` (embed with `<encoder-only>` mean-pooled
  output). Any failure -> fall back to `hash` with a one-line warning.
- backend `hash` (default fallback): deterministic, no downloads — 256-dim bag of
  character 3-grams (lowercased text, hashed by `hashlib.sha1` into buckets, counted,
  L2-normalized). Similar texts must score higher than dissimilar ones.
- `decode_nearest` honors the Decoder role: maps a vector back to the most similar
  known node/label via the match table.

```python
class NodeEmbeddingMatchTable:
    def add(self, node_id: str, label: str, vector: list[float]) -> None
    def most_similar(self, vector: list[float], exclude_ids: set[str] | None = None, top_k: int = 1) -> list[dict]
        # [{"id": ..., "label": ..., "score": float}] cosine similarity, descending
    def save(self, path) -> None
    def load(self, path) -> None
    def __len__(self) -> int
```

`label` for match-table entries: for symbols, the defined name; for nodes, the
node label. Table must also expose `add_symbol(name, vector)` storing
`kind="symbol"` entries (used by Patcher for name suggestions).

## Violations (Detector.py)

```python
def detect(kb: dict, graph: dict, embeddings: dict[str, list[float]],
           threshold: float = 0.2) -> list[dict]
def save_violations(violations: list[dict], path) -> None
def load_violations(path) -> list[dict]
```

Violation record:

```json
{"id": "V001", "node": "node:004", "category": "calls", "rule": "undefined_call",
 "severity": "error", "line": 10, "message": "call to undefined name 'sum_value'",
 "suggestion": {"kind": "name", "current": "sum_value", "closest": "sum_values", "score": 0.83}}
```

Rules (all deterministic):
- `undefined_call` (error, calls): callee root name not in symbols.defined, not a
  builtin (`dir(builtins)`), not provided by imports. Suggestion: most similar
  defined name via match table symbols (score included).
- `undefined_name_read` (error, dataflow): name read never assigned/imported/param
  in the same function and not global/builtin (conservative: only within one function).
- `missing_module` (error, library_usage): `importlib.util.find_spec(module) is None`
  for top-level module (skip relative imports).
- `resource_leak` (error, dataflow): resource entry with `closed: false`.
- `env_without_default` (warning, configuration): `kind == "env"` and `has_default: false`.
- `unreachable_code` (error, control_flow): statements after `return`/`raise` in the
  same block at the same indent level (report each subsequent statement line once).
- `eval_use`, `exec_use`, `pickle_use`, `shell_true`, `hardcoded_secret` (warning, security).
- `context_mismatch` (warning, any category): node expectation embedding vs connected
  embedding cosine < threshold (only when node has both embeddings). Skip when fewer
  than 3 nodes have embeddings.

Order violations by (line, id); ids V001, V002, ... in that order.

## Patch (Patcher.py)

```python
def patch(source: str, kb: dict, graph: dict, violations: list[dict],
          match_table=None, min_confidence: float = 0.6) -> dict
def apply_patches(source: str, patches: list[dict]) -> str
```

Patch record:

```json
{"id": "P001", "violation": "V001", "rule": "undefined_call", "kind": "replace_name",
 "line": 10, "old": "sum_value", "new": "sum_values", "confidence": 0.83}
{"id": "P002", "violation": "V005", "rule": "unreachable_code", "kind": "delete_lines",
 "lines": [17], "confidence": 1.0}
```

Strategies (only these; everything else stays unresolved):
- `undefined_call` / `undefined_name_read`: replace the name with the suggestion's
  closest name when suggestion score >= min_confidence; else leave unresolved.
- `unreachable_code`: delete the reported lines.
- `resource_leak`: if the resource is `name = open(...)` and `.close()` never called,
  append `name.close()` after the last use of `name` in the same function
  (kind `insert_close`, fields `line`, `name`).
- `env_without_default`, `missing_module`, security rules, `context_mismatch`:
  report-only (no patch), returned in `unresolved` with their violation ids.

`patch()` returns `{"patches": [...], "unresolved": [...], "new_source": apply_patches(source, patches)}`
(empty patches list if none apply — never raise). `apply_patches` applies
`replace_name` (exact string replace on the given line only), `delete_lines`
(1-based, descending), `insert_close` (insert indented `name.close()` after line).

## Loop (Updater.py)

```python
def update_loop(source: str, max_iters: int = 3, threshold: float = 0.2,
                min_confidence: float = 0.6, log=print) -> dict
```

Each iteration: parse_source -> build_graph -> embed node texts -> detect -> patch.
Stops when: no violations, no applicable patches, source unchanged, or max_iters.
Returns `{"iterations": [{"iteration": 1, "violations": N, "patched": M, "unresolved": K}],
"final_source": str, "remaining_violations": [...], "iters_run": n}`. All sub-steps
imported from the other modules; log one line per iteration.

## Integration (Integrater.py)

```python
def integrate(original_path, new_source: str, out_path=None) -> dict
```

Writes `new_source` to `out_path` or overwrites `original_path` when out_path is None;
returns `{"written": str, "changed": bool, "diff": "<unified diff original vs new>"}`.
Never writes when `new_source == original file content`.

## Independent tester (Tester.py)

```python
def test(original_source: str, patched_source: str, tests: dict | None = None) -> dict
```

Uses Detector on both versions (parse_source -> build_graph -> embed -> detect):
`{"compiles": bool, "violations_before": int, "violations_after": int,
"removed": int, "new_violations": [violation dicts present only in after],
"tests": {"status": "pass|fail|skipped", "detail": str},
"verdict": "accepted" | "partial" | "rejected"}`
- accepted: compiles AND violations_after == 0 AND (tests pass or no tests given)
  AND removed > 0
- partial: compiles AND violations_after < violations_before
- rejected: does not compile OR violations_after >= violations_before
If `tests` provided (same shape as benchmark `input_output`: inputs/outputs +
fn_name) run them via `benchmark.checks.check_tests` (insert project root into
sys.path; benchmark package lives at `src/benchmark`).

## Pipeline CLI (kappa/__main__.py)

```python
python -m kappa examples/demo_violations.py --out repaired.py --max-iters 3 --report report.json
```

Flags: `--out` (default `<input>.repaired.py`), `--max-iters`, `--threshold`,
`--min-confidence`, `--report` (JSON: updater result + tester result + integrate info),
`--backend` (unixcoder|hash). Streams one progress line per stage with flush=True.
Exit code 0 iff tester verdict is accepted or partial.
