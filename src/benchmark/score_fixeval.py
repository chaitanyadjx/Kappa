"""FixEval scorer:  ID, FILE_PATH -> Score.

Scores a Python file against the FixEval test cases for a task id
(e.g. p02547_s853646132 or just the problem id p02547).

Single score:
    python -m benchmark.score_fixeval --id p02547_s000853418 --file solution.py

Batch (one {"id": ..., "file_path": ...} per line):
    python -m benchmark.score_fixeval --pairs pairs.jsonl --out scores.jsonl

Optional baseline (scores the dataset's buggy code C1 for the same id and
reports the fail->pass repair delta):
    python -m benchmark.score_fixeval --id p02547_s853646132 --file solution.py --baseline

Output score: passed/total test cases plus verdict ACCEPTED / WRONG_ANSWER /
TLE / RUNTIME_ERROR / COMPILE_ERROR. Exit code 0 iff ACCEPTED.

Requires the FixEval dataset extracted at benchmark/FixEval/ (see README).
"""

from __future__ import annotations

import argparse
import glob
import json
import subprocess
import sys
import tempfile
import time
from collections import defaultdict
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parent / "FixEval"
PROBLEM_LIST = REPO_ROOT / "src" / "problem_list.csv"
TEST_CASES = REPO_ROOT / "data" / "data" / "atcoder_test_cases"
CONTEST_PREFIXES = {
    "AtCoder Regular Contest": "ARC",
    "AtCoder Beginner Contest": "ABC",
    "AtCoder Grand Contest": "AGC",
}


def build_problemid_to_tc() -> dict[str, str]:
    problems: dict[str, list[str]] = defaultdict(list)
    problem_list = pd.read_csv(PROBLEM_LIST)
    for _, row in problem_list.iterrows():
        if row["dataset"] != "AtCoder":
            continue
        for prefix, key in CONTEST_PREFIXES.items():
            if prefix in row["name"]:
                number = row["name"].split(" ")[3]
                problems[key + number].append(row["id"])
    folders = {p.name for p in TEST_CASES.iterdir() if p.is_dir()}
    problemid_to_tc: dict[str, str] = {}
    for key, ids in problems.items():
        folder = None
        for candidate in (key, key.lower()):
            if candidate in folders:
                letters = sorted(p.name for p in (TEST_CASES / candidate).iterdir() if p.is_dir())
                if len(letters) == len(ids):
                    folder = candidate
                    break
        if folder is None:
            continue
        letters = sorted(p.name for p in (TEST_CASES / folder).iterdir() if p.is_dir())
        for prob_id, letter in zip(ids, letters):
            problemid_to_tc[prob_id] = str(TEST_CASES / folder / letter)
    return problemid_to_tc


def time_limit_for(problem_id: str, problem_list: pd.DataFrame) -> float:
    row = problem_list[problem_list["id"] == problem_id]
    if row.empty:
        return 5.0
    return max(5.0, float(row.iloc[0]["time_limit"]) / 1000.0)


def only_digits(num: str) -> bool:
    return num.replace("-", "").replace("+", "").replace(".", "", 1).replace("E", "").isdigit()


def values_match(got: str, want: str) -> bool:
    if got == want:
        return True
    if (not only_digits(got)) or (not only_digits(want)):
        return False
    try:
        return abs(float(got) - float(want)) < 1e-6
    except ValueError:
        return False


def output_matches(actual: str, expected: str) -> bool:
    got_lines = actual.strip().split()
    want_lines = expected.strip().split()
    if len(got_lines) != len(want_lines):
        return False
    for got, want in zip(got_lines, want_lines):
        if got.strip() == want.strip():
            continue
        got_parts = got.strip().split(" ")
        want_parts = want.strip().split(" ")
        if len(got_parts) != len(want_parts):
            return False
        if not all(values_match(g, w) for g, w in zip(got_parts, want_parts)):
            return False
    return True


def score_file(file_path: Path, test_case_folder: str, timeout: float) -> dict:
    result = {"file": str(file_path), "tests_passed": 0, "tests_total": 0, "verdict": "COMPILE_ERROR", "first_failure": ""}
    source = Path(file_path).read_text(encoding="utf-8")
    in_files = sorted(glob.glob(test_case_folder + "/in/*"))
    if not in_files:
        result["verdict"] = "NO_TEST_CASES"
        return result
    result["tests_total"] = len(in_files)

    with tempfile.TemporaryDirectory() as td:
        candidate = Path(td) / "candidate.py"
        candidate.write_text(source, encoding="utf-8")
        compile_check = subprocess.run(  # noqa: S603
            [sys.executable, "-m", "py_compile", str(candidate)], capture_output=True, text=True, timeout=timeout
        )
        if compile_check.returncode != 0:
            result["first_failure"] = "compile: " + (compile_check.stderr or "").strip().splitlines()[-1][:200]
            return result

        result["verdict"] = "WRONG_ANSWER"
        for in_file in in_files:
            out_file = in_file.replace("/in/", "/out/", 1)
            expected = Path(out_file).read_text(encoding="utf-8")
            try:
                proc = subprocess.run(  # noqa: S603
                    [sys.executable, str(candidate)],
                    stdin=open(in_file, encoding="utf-8"),
                    capture_output=True,
                    text=True,
                    timeout=timeout,
                )
            except subprocess.TimeoutExpired:
                result["verdict"] = "TLE"
                result["first_failure"] = f"TLE on {Path(in_file).name}"
                return result
            if proc.returncode != 0:
                result["verdict"] = "RUNTIME_ERROR"
                tail = (proc.stderr or "").strip().splitlines()
                result["first_failure"] = f"runtime error on {Path(in_file).name}: {tail[-1][:160] if tail else ''}"
                return result
            if output_matches(proc.stdout, expected):
                result["tests_passed"] += 1
            else:
                got = proc.stdout.strip().splitlines()[:3]
                want = expected.strip().splitlines()[:3]
                result["first_failure"] = f"wrong output on {Path(in_file).name}: got {got!r} want {want!r}"
                return result
        result["verdict"] = "ACCEPTED"
    return result


def detokenize(tokens: list[str]) -> str:
    code_lines: list[str] = []
    current = ""
    indent = 0
    for token in tokens:
        if token == "NEW_LINE":
            code_lines.append("    " * indent + current.strip())
            current = ""
        elif token == "INDENT":
            indent += 1
        elif token == "DEDENT":
            indent = max(0, indent - 1)
        else:
            current += token + " "
    if current.strip():
        code_lines.append("    " * indent + current.strip())
    return "\n".join(code_lines) + "\n"


def load_record(record_id: str, split: str, verdict_mode: bool) -> tuple[str, str, str] | None:
    folder = "processed_with_verdict" if verdict_mode else "processed"
    path = REPO_ROOT / "data" / "data" / "python" / folder / f"{split}.jsonl"
    if not path.is_file():
        path = REPO_ROOT / "data" / "python" / folder / f"{split}.jsonl"
    print(f"loading {path} (one-time, large) ...", file=sys.stderr, flush=True)
    with open(path, encoding="utf8") as handle:
        records = json.load(handle)
    for record in records:
        if record["src_id"] == record_id or record["tgt_id"] == record_id or record["src_id"].split("_")[0] == record_id:
            return record["src_id"], record["src"], record["tgt"]
    return None


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--id", help="task id: pair id (p02547_s853646132) or problem id (p02547)")
    parser.add_argument("--file", type=Path, help="candidate Python file to score")
    parser.add_argument("--pairs", type=Path, help='batch JSONL: {"id", "file_path"} per line')
    parser.add_argument("--out", type=Path, help="write batch scores to this JSONL")
    parser.add_argument("--baseline", action="store_true", help="also score the dataset buggy code and report the repair delta")
    parser.add_argument("--split", default="test", choices=["train", "valid", "test"])
    parser.add_argument("--verdict-mode", action="store_true", help="read from processed_with_verdict")
    parser.add_argument("--timeout", type=float, help="per-test timeout override (seconds)")
    args = parser.parse_args(argv)

    problemid_to_tc = build_problemid_to_tc()
    problem_list = pd.read_csv(PROBLEM_LIST)

    if args.pairs:
        rows = []
        for line in args.pairs.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            pair = json.loads(line)
            problem_id = pair["id"].split("_")[0]
            if problem_id not in problemid_to_tc:
                rows.append({"id": pair["id"], "file": pair["file_path"], "verdict": "NO_TEST_CASES", "score": 0.0})
                continue
            result = score_file(
                Path(pair["file_path"]),
                problemid_to_tc[problem_id],
                args.timeout or time_limit_for(problem_id, problem_list),
            )
            result["id"] = pair["id"]
            result["score"] = round(result["tests_passed"] / max(1, result["tests_total"]), 4)
            rows.append(result)
            print(f"{pair['id']}: {result['verdict']} {result['tests_passed']}/{result['tests_total']}", flush=True)
        if args.out:
            args.out.parent.mkdir(parents=True, exist_ok=True)
            args.out.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
            print(f"scores written to {args.out}")
        return 0

    if not args.id or not args.file:
        parser.error("--id and --file are required (or use --pairs)")

    problem_id = args.id.split("_")[0]
    if problem_id not in problemid_to_tc:
        print(f"no test cases found for problem {problem_id}")
        return 2
    timeout = args.timeout or time_limit_for(problem_id, problem_list)
    result = score_file(Path(args.file), problemid_to_tc[problem_id], timeout)
    result["id"] = args.id
    result["score"] = round(result["tests_passed"] / max(1, result["tests_total"]), 4)

    print(f"id: {args.id}")
    print(f"verdict: {result['verdict']}")
    print(f"score: {result['score']} ({result['tests_passed']}/{result['tests_total']} test cases)")
    if result["first_failure"]:
        print(f"first failure: {result['first_failure']}")

    if args.baseline:
        record = load_record(args.id, args.split, args.verdict_mode)
        if record is None:
            print(f"baseline: record {args.id} not found in {args.split} split")
            return 0
        src_id, src_tokens, tgt_tokens = record
        with tempfile.TemporaryDirectory() as td:
            buggy = Path(td) / "buggy.py"
            buggy.write_text(detokenize(src_tokens), encoding="utf-8")
            before = score_file(buggy, problemid_to_tc[problem_id], timeout)
            fixed = Path(td) / "fixed.py"
            fixed.write_text(detokenize(tgt_tokens), encoding="utf-8")
            reference = score_file(fixed, problemid_to_tc[problem_id], timeout)
        print(
            f"repair delta: buggy C1 {before['verdict']} {before['tests_passed']}/{before['tests_total']}"
            f" -> candidate {result['verdict']} {result['tests_passed']}/{result['tests_total']}"
            f" (reference fix: {reference['verdict']} {reference['tests_passed']}/{reference['tests_total']})"
        )
        flipped = before["verdict"] != "ACCEPTED" and result["verdict"] == "ACCEPTED"
        print(f"fail -> pass flip: {'YES' if flipped else 'NO'}")
    return 0 if result["verdict"] == "ACCEPTED" else 1


if __name__ == "__main__":
    sys.exit(main())
