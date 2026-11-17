#!/usr/bin/env python3
"""Convert the PyTyDefects dataset (ICSE 2024) into benchmark runner JSONL.

Dataset
-------
PyTyDefects ships inside the PyTy repository (cloned to benchmark/PyTy):

    benchmark/PyTy/src/Input/dd_out_<project>_<commit>.json   (4,515 files)

Each JSON file holds a LIST of records; each record is one Pyre type warning
that a commit fixed, located by delta debugging (see
benchmark/PyTy/src/delta_debugging/README.md).  Record fields:

    project                  GitHub "owner/repo" of the commit.
    commit                   fixing commit SHA.
    filename                 repo-relative path of the buggy file.
    filename_after_commit    absolute path on the authors' machine (provenance only).
    file_hunks_size          number of diff hunks in the whole file's commit diff.
    min_patch_found          delta debugging isolated a minimal fixing hunk set.
    single_hunk              the minimal patch is a single hunk.
    fit_TFix                 single hunk small enough for the TFix window format.
    delete_only_patch        every minimal hunk only deletes lines.
    has_suppression_all_hunks every minimal hunk adds a suppression comment.
    full_warning_msg         raw Pyre report line: "path:line:col Rule [id]: message".
    message                  Pyre message text (without the rule name).
    rule_id                  Pyre error type, e.g. "Incompatible parameter type [6]".
    warning_line_no          line of the warning in the FULL buggy file (1-based).
    warning_line             source text of that line.
    min_patch                list of minimal fixing hunks; per hunk:
        source_code          BUGGY code (context + '-' lines)   -> C1  ("code")
        target_code          FIXED code (context + '+' lines)   -> C2  ("code_after")
        diff_format          unified hunk header @@ -s,l +t,m @@ (positions in the
                             full buggy file; s = first line of source_code there).
        inside_window        warning_line_no lies within this hunk's source range.
        delete_only / has_suppression / hunk_fit_TFix / *_with_indent / *_len

Some records instead carry "dd_fail"/"exception" (delta debugging failed) and
are skipped, as are records with min_patch_found == false.

Important: source_code/target_code are hunk windows, not whole files, and
warning_line_no is absolute in the original repo file.  Only hunks with
inside_window == true (or a text match of warning_line inside the hunk) carry
the buggy line, so those are the only pairs a standalone static checker can
reproduce.  By default this converter emits exactly those pairs; pass
--all-hunks to keep the rest (their "line" meta field will be null).

Output
------
One JSON object per hunk, runner-compatible:
    {"id", "code" (C1), "code_after" (C2), "category" (Pyre rule), "label": "bad"}
plus scorer metadata: line (1-based warning line within the hunk), pyre_message,
warning_line_no (absolute), project, commit, filename, hunk_start_src,
inside_window, delete_only, has_suppression.  No "tests" are shipped: the
oracle is a static type checker (see benchmark/score_pyty.py), not unit tests.

Usage
-----
    python -m benchmark.convert_pyty [--split {all,train,dev,test}] [--limit N]
                                     [--out PATH] [--all-hunks]
                                     [--require-parseable] [--dataset DIR]

Defaults: --dataset benchmark/PyTy/src/Input, --out benchmark/data/pyty_defects.jsonl
--split train/dev/test is a deterministic 70/20/10 hash split on the record id.

Hunk windows keep the original file's indentation; the scorer dedents them
before checking.  Some windows are not valid standalone Python at all (diff
context can cut mid-docstring/class body) — PyTy never checked windows
standalone (it re-ran Pyre on the full patched repo file), so mypy scores such
pairs NOT_REPRODUCIBLE.  Pass --require-parseable to emit only pairs whose
dedented C1 and C2 both parse with ast.parse (recommended for a clean suite;
measured: 3,949/8,004 = 49.3% of inside-window pairs survive).
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import re
import sys
import textwrap
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATASET = REPO_ROOT / "benchmark" / "PyTy" / "src" / "Input"
DEFAULT_OUT = REPO_ROOT / "benchmark" / "data" / "pyty_defects.jsonl"

HUNK_HEADER_RE = re.compile(r"^@@ -(\d+),(\d+) \+(\d+),(\d+) @@")


def sanitize(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", text).strip("-")


def compute_id(project: str, commit: str, filename: str, hunk_idx: int,
               used: dict[str, int]) -> str:
    """Stable, human-readable id; disambiguate repeats with a counter."""
    base = f"{sanitize(project)}_{commit[:8]}_{sanitize(Path(filename).stem)}_{hunk_idx}"
    n = used.get(base, 0)
    used[base] = n + 1
    return base if n == 0 else f"{base}_{n + 1}"


def split_of(record_id: str) -> str:
    """Deterministic 70/20/10 train/dev/test split by id hash."""
    h = int(hashlib.md5(record_id.encode("utf-8")).hexdigest(), 16) % 100
    if h < 70:
        return "train"
    if h < 90:
        return "dev"
    return "test"


def parses_standalone(code: str) -> bool:
    """True if the dedented hunk window is valid standalone Python."""
    try:
        ast.parse(textwrap.dedent(code))
        return True
    except SyntaxError:
        return False


def warning_line_in_hunk(rec: dict, hunk: dict) -> int | None:
    """1-based position of the warning line within the hunk's source_code, or None."""
    diff = hunk.get("diff_format", "")
    m = HUNK_HEADER_RE.match(diff)
    warn_no = rec.get("warning_line_no")
    if m and isinstance(warn_no, int):
        startsrc, linessrc = int(m.group(1)), int(m.group(2))
        if hunk.get("inside_window") and startsrc <= warn_no <= startsrc + linessrc - 1:
            return warn_no - startsrc + 1
    # Fallback: locate warning_line by text inside the hunk window.
    warn_line = (rec.get("warning_line") or "").strip()
    if warn_line:
        lines = hunk.get("source_code", "").splitlines()
        for i, line in enumerate(lines):
            if line.strip() == warn_line:
                return i + 1
    return None


def iter_records(dataset: Path, all_hunks: bool, require_parseable: bool = False):
    """Yield (source_file, converter_record) for every usable hunk."""
    used: dict[str, int] = {}
    files = sorted(dataset.glob("*.json"))
    if not files:
        raise SystemExit(f"error: no *.json files under {dataset}")
    for src in files:
        try:
            data = json.loads(src.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            print(f"warning: skipping unreadable {src.name}: {exc}", file=sys.stderr)
            continue
        for rec_i, rec in enumerate(data):
            if not isinstance(rec, dict) or "dd_fail" in rec:
                continue
            if not rec.get("min_patch_found") or not rec.get("min_patch"):
                continue
            for hunk_i, hunk in enumerate(rec["min_patch"]):
                line = warning_line_in_hunk(rec, hunk)
                if line is None and not all_hunks:
                    continue
                if require_parseable and not (
                        parses_standalone(hunk["source_code"])
                        and parses_standalone(hunk["target_code"])):
                    continue
                rid = compute_id(rec["project"], rec["commit"], rec["filename"],
                                 hunk_i, used)
                yield src, {
                    "id": rid,
                    "code": hunk["source_code"],
                    "code_after": hunk["target_code"],
                    "category": rec.get("rule_id", ""),
                    "label": "bad",
                    "line": line,
                    "pyre_message": (rec.get("message") or "").strip(),
                    "warning_line_no": rec.get("warning_line_no"),
                    "warning_line": rec.get("warning_line"),
                    "project": rec.get("project"),
                    "commit": rec.get("commit"),
                    "filename": rec.get("filename"),
                    "hunk_start_src": (lambda m: int(m.group(1)) if m else None)(
                        HUNK_HEADER_RE.match(hunk.get("diff_format", ""))),
                    "inside_window": bool(hunk.get("inside_window")),
                    "delete_only": bool(hunk.get("delete_only")),
                    "has_suppression": bool(hunk.get("has_suppression")),
                }


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Convert PyTyDefects (benchmark/PyTy/src/Input) to runner JSONL.")
    ap.add_argument("--dataset", type=Path, default=DEFAULT_DATASET,
                    help=f"PyTyDefects Input dir (default: {DEFAULT_DATASET})")
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT,
                    help=f"output JSONL (default: {DEFAULT_OUT})")
    ap.add_argument("--split", choices=["all", "train", "dev", "test"], default="all",
                    help="deterministic 70/20/10 id-hash split (default: all)")
    ap.add_argument("--limit", type=int, default=None,
                    help="emit at most N records (after split/filter)")
    ap.add_argument("--all-hunks", action="store_true",
                    help="also emit hunks whose warning line is outside the hunk "
                         "(line=null; a standalone checker usually cannot "
                         "reproduce those)")
    ap.add_argument("--require-parseable", action="store_true",
                    help="emit only pairs whose dedented C1 and C2 are valid "
                         "standalone Python (ast.parse check)")
    args = ap.parse_args()

    args.out.parent.mkdir(parents=True, exist_ok=True)
    n_seen = n_written = 0
    by_split = {"train": 0, "dev": 0, "test": 0}
    with args.out.open("w", encoding="utf-8") as fh:
        for _src, record in iter_records(args.dataset, args.all_hunks,
                                         args.require_parseable):
            n_seen += 1
            if args.split != "all" and split_of(record["id"]) != args.split:
                continue
            if args.limit is not None and n_written >= args.limit:
                break
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
            n_written += 1
            by_split[split_of(record["id"])] += 1

    print(f"dataset : {args.dataset}")
    print(f"hunks scanned : {n_seen}")
    print(f"written : {n_written} -> {args.out} "
          f"(split={args.split}, train/dev/test counts for written records: "
          f"{by_split['train']}/{by_split['dev']}/{by_split['test']})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
