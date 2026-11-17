#!/usr/bin/env python3
"""PyTyDefects scorer: ID, FILE_PATH -> Score for static type-error repair.

    single mode:  python -m benchmark.score_pyty --id <record id> --file candidate.py
    batch mode :  python -m benchmark.score_pyty --pairs pairs.jsonl --out scores.jsonl

Pairs come from benchmark/convert_pyty.py (default suite:
benchmark/data/pyty_defects.jsonl, override with --suite).

CHECKER DIFFERENCE (mypy vs Pyre) — read this before trusting scores
--------------------------------------------------------------------
PyTyDefects was *built* with Meta's Pyre: every record's rule_id, message and
warning_line_no come from `pyre` runs on the full repository at the buggy
commit (see benchmark/PyTy/src/delta_debugging/pyreDD.py).  This scorer runs
**mypy** instead (Pyre is not installable in this benchmark env; mypy is).
Consequences, in decreasing order of importance:

1. Different checker, different theory.  A Pyre rule is mapped to mypy error
   codes and message keywords (PYRE_RULE_MAP below).  E.g. Pyre "Incompatible
   parameter type [6]" -> mypy [arg-type]/[index].  Where the two checkers
   disagree — different inference power, different default strictness, Pyre-
   only rules like "Uninitialized instance attribute" — the target error may
   not be detectable by mypy at all.

2. Window, not repository.  PyTy validated fixes by patching the hunk into the
   *full repo file* and re-running Pyre.  We only have the hunk window (C1 ->
   C2), so mypy checks a standalone snippet: types imported from elsewhere
   become `Any`, and errors that depended on project types may disappear in
   BOTH the buggy and fixed versions.

3. Because of 1+2, a pair where mypy cannot reproduce the record's target
   error even in the record's own buggy code C1 is scored NOT_REPRODUCIBLE
   (exit 2) rather than silently ERROR_GONE.  Do not count these as repairs;
   measure them with --pairs batch mode and report the rate.

Scoring
-------
For the record, run `mypy --ignore-missing-imports --no-error-summary
--show-error-codes --follow-imports=skip <file>` with a fresh temp cache dir
per mypy invocation (mypy exit code: 0 = no type errors, 1 = errors, 2 =
operational failure).  Errors are parsed as "file:line:col: error: msg [code]".
The record's target error is the Pyre rule mapped through PYRE_RULE_MAP
(fallback: significant-word overlap with the Pyre rule name) appearing within
--line-tolerance (default 2) lines of the record's warning line in the hunk.
Candidate code (and C1 for the "before" count) is textwrap.dedent-ed into a
temp copy first, because hunk windows keep the original file's indentation and
would otherwise not parse as a standalone module.

    verdict ERROR_GONE       target error present in C1, absent in candidate   -> exit 0
    verdict ERROR_PRESENT    target error still present (or candidate stopped
                             parsing)                                         -> exit 1
    verdict NOT_REPRODUCIBLE mypy never reports the target error on C1        -> exit 2
    verdict SCORE_ERROR      missing record/file, mypy missing/crashed        -> exit 3

Single mode prints a human summary plus the JSON score line; batch mode writes
one JSON line per pair to --out and exits 0 iff every pair is ERROR_GONE.
Before/after error counts are always reported (before = the record's own C1
checked the same way).
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
import textwrap
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SUITE = REPO_ROOT / "benchmark" / "data" / "pyty_defects.jsonl"
DEFAULT_OUT = REPO_ROOT / "benchmark" / "data" / "pyty_scores.jsonl"

MYPY_TIMEOUT_S = 120
MYPY_BASE_CMD = ["--ignore-missing-imports", "--no-error-summary",
                 "--show-error-codes", "--follow-imports=skip"]

# mypy 2.x omits the column for syntax errors ("file:2: error:"), so col is optional.
MYPY_OUTPUT_RE = re.compile(
    r"^(?P<file>[^:]+):(?P<line>\d+):(?:(?P<col>\d+):)? (?P<sev>error|note): "
    r"(?P<msg>.*?)(?:  \[(?P<code>[\w.-]+)\])?$")

VERDICT_EXIT = {"ERROR_GONE": 0, "ERROR_PRESENT": 1, "NOT_REPRODUCIBLE": 2,
                "SCORE_ERROR": 3}

# Pyre rule name (lowercased, "[N]" suffix stripped) -> (mypy error codes,
# message keyword phrases).  Built from the PyTyDefects rule distribution and
# mypy's message catalogue; used to recognise "the same" error across checkers.
PYRE_RULE_MAP: dict[str, tuple[set[str], tuple[str, ...]]] = {
    "incompatible parameter type": ({"arg-type", "index"}, (
        "parameter type", "argument type", "argument for", "argument of",
        "has incompatible type")),
    "incompatible variable type": ({"assignment", "var-annotated"}, (
        "in assignment", "variable type", "annotation")),
    "incompatible return type": ({"return-value"}, (
        "return value", "return type")),
    "unbound name": ({"name-defined"}, (
        "is not defined", "undefined name")),
    "incompatible attribute type": ({"assignment", "attr-defined"}, (
        "attribute", "in assignment")),
    "invalid type": ({"valid-type", "type-var"}, (
        "invalid type", "not a valid type", "type application")),
    "inconsistent override": ({"override"}, (
        "override", "signature of", "incompatible with supertype")),
    "incompatible override": ({"override"}, (
        "override", "signature of", "incompatible with supertype")),
    "unsupported operand": ({"operator"}, (
        "unsupported operand", "left operand", "right operand", "operator")),
    "call error": ({"call-arg"}, (
        "too many arguments", "too few arguments", "missing positional",
        "unexpected keyword", "unexpected positional", "multiple values",
        "no arguments")),
    "undefined attribute": ({"attr-defined"}, (
        "has no attribute", "no attribute", "undefined attribute")),
    "too many arguments": ({"call-arg"}, (
        "too many arguments", "multiple values")),
    "too few arguments": ({"call-arg"}, (
        "too few arguments", "missing positional", "missing named",
        "missing required")),
    "unexpected keyword": ({"call-arg"}, (
        "unexpected keyword",)),
    "undefined import": ({"import", "import-untyped", "import-not-found"}, (
        "cannot find implementation", "missing library stub",
        "module has no attribute")),
    "uninitialized instance attribute": (set(), ()),
}
# Generic fallback stopwords for rules without a table entry above.
RULE_STOPWORDS = {"incompatible", "invalid", "type", "error", "possible"}


def normalize_rule(rule_id: str) -> str:
    """'Inconsistent override [15]' -> 'inconsistent override'."""
    return re.sub(r"\s*\[\d+\]\s*$", "", (rule_id or "").strip()).lower().strip()


def rule_matches(rule_id: str, code: str | None, msg: str) -> bool:
    """Does a mypy error (code, message) correspond to a Pyre rule?"""
    msg_l = msg.lower()
    entry = PYRE_RULE_MAP.get(normalize_rule(rule_id))
    if entry is not None:
        codes, phrases = entry
        return (code in codes) or any(p in msg_l for p in phrases)
    words = [w for w in re.findall(r"[a-z]+", normalize_rule(rule_id))
             if len(w) >= 5 and w not in RULE_STOPWORDS]
    return bool(words) and any(w in msg_l for w in words)


def parse_mypy_output(stdout: str) -> list[dict]:
    errors = []
    for raw in stdout.splitlines():
        m = MYPY_OUTPUT_RE.match(raw.strip())
        if not m:
            continue
        errors.append({
            "line": int(m.group("line")),
            "col": int(m.group("col") or 0),
            "severity": m.group("sev"),
            "message": m.group("msg"),
            "code": m.group("code"),
            "raw": raw.strip(),
        })
    return errors


def run_mypy(code: str, tmp_root: Path, label: str) -> dict:
    """Write `code` (dedented) to a temp file and check it with a fresh mypy
    cache dir per invocation.  Returns {rc, errors, fatal, cmd}."""
    workdir = tmp_root / label
    workdir.mkdir(parents=True, exist_ok=True)
    target = workdir / "candidate.py"
    target.write_text(textwrap.dedent(code), encoding="utf-8")
    cache = workdir / ".mypy_cache"  # fresh dir -> no cross-run caching
    cmd = [sys.executable, "-m", "mypy", *MYPY_BASE_CMD,
           "--cache-dir", str(cache), str(target)]
    try:
        proc = subprocess.run(cmd, cwd=workdir, capture_output=True,
                              text=True, timeout=MYPY_TIMEOUT_S)
        rc, stdout, stderr = proc.returncode, proc.stdout, proc.stderr
    except subprocess.TimeoutExpired:
        rc, stdout, stderr = 2, "", f"mypy timed out after {MYPY_TIMEOUT_S}s"
    except FileNotFoundError:
        rc, stdout, stderr = 2, "", "mypy is not installed for this interpreter"
    errors = parse_mypy_output(stdout)
    fatal = rc == 2 or (not errors and stderr.strip() and rc != 0)
    return {"rc": rc, "errors": errors, "fatal": fatal, "cmd": cmd,
            "stderr": stderr.strip()}


def matched_target_errors(rule_id: str, line: int | None, errors: list[dict],
                          tolerance: int) -> list[dict]:
    out = []
    for e in errors:
        if e["severity"] != "error":
            continue
        if line is not None and abs(e["line"] - line) > tolerance:
            continue
        if rule_matches(rule_id, e["code"], e["message"]):
            out.append(e)
    return out


def load_suite(path: Path) -> dict[str, dict]:
    suite: dict[str, dict] = {}
    with path.open(encoding="utf-8") as fh:
        for n, raw in enumerate(fh, 1):
            raw = raw.strip()
            if not raw:
                continue
            rec = json.loads(raw)
            if rec["id"] in suite:
                print(f"warning: duplicate id {rec['id']!r} in {path} "
                      f"(line {n}); keeping first", file=sys.stderr)
                continue
            suite[rec["id"]] = rec
    return suite


def score(record: dict | None, candidate_path: Path | None, tolerance: int,
          tmp_root: Path) -> dict:
    """Score one (record, candidate file) pair -> score dict with verdict."""
    start = time.time()
    if record is None:
        return {"verdict": "SCORE_ERROR", "exit_code": VERDICT_EXIT["SCORE_ERROR"],
                "error": "record id not found in suite"}
    if candidate_path is None or not Path(candidate_path).is_file():
        return {"verdict": "SCORE_ERROR", "exit_code": VERDICT_EXIT["SCORE_ERROR"],
                "error": "candidate file missing"}
    try:
        candidate_code = Path(candidate_path).read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return {"verdict": "SCORE_ERROR", "exit_code": VERDICT_EXIT["SCORE_ERROR"],
                "error": "candidate file not UTF-8"}

    before = run_mypy(record["code"], tmp_root, "before")
    after = run_mypy(candidate_code, tmp_root, "after")
    rule_id = record.get("category", "")
    target_line = record.get("line")
    matched_b = matched_target_errors(rule_id, target_line,
                                      before["errors"], tolerance)
    matched_a = matched_target_errors(rule_id, target_line,
                                      after["errors"], tolerance)
    before_syntax = any(e["code"] == "syntax" for e in before["errors"])
    after_syntax = any(e["code"] == "syntax" for e in after["errors"])

    if before["fatal"] or before_syntax or not matched_b:
        verdict = "NOT_REPRODUCIBLE"
    elif after["fatal"] or after_syntax or matched_a:
        verdict = "ERROR_PRESENT"  # includes "candidate no longer parses"
    else:
        verdict = "ERROR_GONE"

    def fmt(errs: list[dict]) -> list[str]:
        return [f'{e["line"]}:{e["col"]}: {e["message"]}'
                + (f" [{e['code']}]" if e["code"] else "") for e in errs]

    return {
        "verdict": verdict,
        "category": rule_id,
        "pyre_message": record.get("pyre_message", ""),
        "target_line": target_line,
        "before_error_count": sum(e["severity"] == "error"
                                  for e in before["errors"]),
        "after_error_count": sum(e["severity"] == "error"
                                 for e in after["errors"]),
        "matched_before": fmt(matched_b),
        "matched_after": fmt(matched_a),
        "before_errors": fmt([e for e in before["errors"]
                              if e["severity"] == "error"]),
        "after_errors": fmt([e for e in after["errors"]
                             if e["severity"] == "error"]),
        "exit_code": VERDICT_EXIT[verdict],
        "mypy_cmd": " ".join(after["cmd"]),
        "mypy_stderr": after["stderr"] or before["stderr"],
        "seconds": round(time.time() - start, 3),
    }


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Score a candidate fix against a PyTyDefects record with mypy.")
    ap.add_argument("--id", help="record id from the converted suite")
    ap.add_argument("--file", type=Path, help="candidate .py file to score")
    ap.add_argument("--pairs", type=Path,
                    help='batch JSONL with {"id", "file_path"} lines')
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT,
                    help=f"batch output JSONL (default: {DEFAULT_OUT})")
    ap.add_argument("--suite", type=Path, default=DEFAULT_SUITE,
                    help=f"converted suite JSONL (default: {DEFAULT_SUITE})")
    ap.add_argument("--line-tolerance", type=int, default=2,
                    help="lines around the warning line to accept a matching "
                         "error (default: 2)")
    args = ap.parse_args()

    if not args.pairs and not (args.id and args.file):
        ap.error("either --pairs, or both --id and --file, are required")
    if not args.suite.is_file():
        print(f"error: suite not found: {args.suite} "
              f"(run benchmark.convert_pyty first)", file=sys.stderr)
        return VERDICT_EXIT["SCORE_ERROR"]

    tmp_root = Path(tempfile.mkdtemp(prefix="score_pyty_"))
    try:
        if args.pairs:  # ---- batch mode ----
            suite = load_suite(args.suite)
            args.out.parent.mkdir(parents=True, exist_ok=True)
            counts: dict[str, int] = {}
            n = 0
            with args.pairs.open(encoding="utf-8") as fin, \
                    args.out.open("w", encoding="utf-8") as fout:
                for raw in fin:
                    raw = raw.strip()
                    if not raw:
                        continue
                    pair = json.loads(raw)
                    rec = suite.get(pair.get("id"))
                    result = score(rec, pair.get("file_path"),
                                   args.line_tolerance, tmp_root)
                    line = {"id": pair.get("id"),
                            "file_path": pair.get("file_path"), **result}
                    fout.write(json.dumps(line, ensure_ascii=False) + "\n")
                    counts[result["verdict"]] = counts.get(result["verdict"], 0) + 1
                    n += 1
            print(f"scored {n} pair(s) -> {args.out}")
            for v in ("ERROR_GONE", "ERROR_PRESENT", "NOT_REPRODUCIBLE",
                      "SCORE_ERROR"):
                if counts.get(v):
                    print(f"  {v}: {counts[v]}")
            return 0 if counts.get("ERROR_GONE", 0) == n and n > 0 else 1

        # ---- single mode ----
        suite = load_suite(args.suite)
        result = score(suite.get(args.id), args.file,
                       args.line_tolerance, tmp_root)
        print(json.dumps({"id": args.id, "file_path": str(args.file), **result},
                         ensure_ascii=False, indent=2))
        print(f"\nverdict: {result['verdict']} "
              f"(before errors: {result.get('before_error_count')}, "
              f"after errors: {result.get('after_error_count')})")
        return result.get("exit_code", VERDICT_EXIT["SCORE_ERROR"])
    finally:
        shutil.rmtree(tmp_root, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
