from __future__ import annotations

import argparse
import json
import sys
import tempfile
import time
from dataclasses import asdict, dataclass
from pathlib import Path

from benchmark.case import Case, load_cases
from benchmark.checks import (
    check_bandit,
    check_mypy,
    check_ruff,
    check_syntax,
    check_tests,
    check_undefined_names,
)

ALL_CHECKS = ["syntax", "undefined_names", "mypy", "ruff", "bandit", "tests"]

STATIC_TOOLS = (("mypy", check_mypy), ("ruff", check_ruff), ("bandit", check_bandit))


@dataclass
class CaseResult:
    name: str
    category: str | None
    label: str | None
    verdict: str
    seconds: float
    violations: list[dict]
    notes: list[str]
    before: dict | None = None
    after: dict | None = None
    delta: int | None = None
    improvement: str | None = None


def _evaluate(source: str, target: Path, case: Case, checks: list[str], python: str, timeout: float) -> dict:
    violations: list = []
    notes: list[str] = []
    test_status = "skipped"

    if "syntax" in checks:
        outcome = check_syntax(source, target)
        violations += outcome.violations
        notes += outcome.notes

    if not violations and "undefined_names" in checks:
        outcome = check_undefined_names(source, target)
        violations += outcome.violations
        notes += outcome.notes

    if not violations:
        for name, fn in STATIC_TOOLS:
            if name in checks:
                outcome = fn(target, timeout)
                violations += outcome.violations
                notes += outcome.notes

    if not violations and "tests" in checks:
        outcome = check_tests(case, python, timeout, candidate_source=source)
        violations += outcome.violations
        notes += outcome.notes
        if outcome.violations:
            test_status = "fail"
        elif any("tests passed" in note for note in outcome.notes):
            test_status = "pass"

    if violations and any(v.check == "syntax" for v in violations):
        notes.append("syntax error; remaining checks skipped")

    by_check: dict[str, int] = {}
    for violation in violations:
        by_check[violation.check] = by_check.get(violation.check, 0) + 1
    return {
        "violations": violations,
        "notes": notes,
        "summary": {"violations": len(violations), "by_check": by_check, "tests": test_status},
    }


def run_case(case: Case, checks: list[str], python: str, timeout: float) -> CaseResult:
    start = time.perf_counter()
    source_before = case.source_code if case.source_code is not None else case.target.read_text(encoding="utf-8")

    with tempfile.TemporaryDirectory() as td:
        if case.source_code is not None:
            target_before = Path(td) / "candidate_before.py"
            target_before.write_text(source_before, encoding="utf-8")
        else:
            target_before = case.target
        before = _evaluate(source_before, target_before, case, checks, python, timeout)

        if case.code_after is None:
            violations = before["violations"]
            notes = before["notes"]
            verdict = "bad" if violations else "good"
            return CaseResult(
                case.name,
                case.category,
                case.label,
                verdict,
                time.perf_counter() - start,
                [v.as_dict() for v in violations],
                notes,
            )

        target_after = Path(td) / "candidate_after.py"
        target_after.write_text(case.code_after, encoding="utf-8")
        after = _evaluate(case.code_after, target_after, case, checks, python, timeout)

    delta = before["summary"]["violations"] - after["summary"]["violations"]
    improvement = "reduced" if delta > 0 else ("increased" if delta < 0 else "same")
    verdict = "good" if not after["violations"] else "bad"
    notes = [f"before: {note}" for note in before["notes"]] + [f"after: {note}" for note in after["notes"]]
    return CaseResult(
        case.name,
        case.category,
        case.label,
        verdict,
        time.perf_counter() - start,
        [v.as_dict() for v in after["violations"]],
        notes,
        before=before["summary"],
        after=after["summary"],
        delta=delta,
        improvement=improvement,
    )


def _metric_counts(results: list[CaseResult]) -> dict | None:
    labeled = [r for r in results if r.label in ("good", "bad")]
    if not labeled:
        return None
    tp = sum(r.label == "bad" and r.verdict == "bad" for r in labeled)
    fp = sum(r.label == "good" and r.verdict == "bad" for r in labeled)
    fn = sum(r.label == "bad" and r.verdict == "good" for r in labeled)
    tn = len(labeled) - tp - fp - fn
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {
        "total": len(results),
        "labeled": len(labeled),
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "tn": tn,
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "f1": round(f1, 4),
    }


def compute_metrics(results: list[CaseResult]) -> dict | None:
    overall: dict = {}
    counts = _metric_counts(results)
    if counts:
        overall.update(counts)
        categories = {
            category: _metric_counts([r for r in results if r.category == category])
            for category in sorted({r.category for r in results if r.category})
        }
        overall["by_category"] = categories

    delta_cases = [r for r in results if r.delta is not None]
    if delta_cases:
        overall["repair_delta"] = {
            "cases": len(delta_cases),
            "violations_before": sum(r.before["violations"] for r in delta_cases),
            "violations_after": sum(r.after["violations"] for r in delta_cases),
            "reduced": sum(r.improvement == "reduced" for r in delta_cases),
            "same": sum(r.improvement == "same" for r in delta_cases),
            "increased": sum(r.improvement == "increased" for r in delta_cases),
            "tests_fail_to_pass": sum(
                r.before["tests"] == "fail" and r.after["tests"] == "pass" for r in delta_cases
            ),
            "tests_pass_to_fail": sum(
                r.before["tests"] == "pass" and r.after["tests"] == "fail" for r in delta_cases
            ),
        }
    return overall or None


def print_case(result: CaseResult) -> None:
    flag = "ok  " if result.verdict == "good" else "BAD "
    label = f"label={result.label}" if result.label else ""
    category = f"category={result.category}" if result.category else ""
    print(f"{flag} {result.name:<28} {label:<12} {category:<28} {result.seconds:5.2f}s", flush=True)
    for v in result.violations:
        loc = f" [{v['location']}]" if v["location"] else ""
        print(f"       {v['check']}: {v['message']}{loc}", flush=True)
    for n in result.notes:
        print(f"       note: {n}", flush=True)
    if result.delta is not None:
        print(
            f"       delta: {result.before['violations']} -> {result.after['violations']} violations "
            f"({result.improvement}); tests {result.before['tests']} -> {result.after['tests']}",
            flush=True,
        )


def print_metrics(metrics: dict | None) -> None:
    if not metrics:
        return
    if "precision" in metrics:
        m = metrics
        print(
            f"\nmetrics on {m['labeled']}/{m['total']} labeled cases: "
            f"precision={m['precision']} recall={m['recall']} f1={m['f1']} "
            f"(tp={m['tp']} fp={m['fp']} fn={m['fn']} tn={m['tn']})",
            flush=True,
        )
        for category, stats in (m.get("by_category") or {}).items():
            print(
                f"  {category:<32} precision={stats['precision']:<8} recall={stats['recall']:<8} f1={stats['f1']} (n={stats['labeled']})",
                flush=True,
            )
    if "repair_delta" in metrics:
        d = metrics["repair_delta"]
        print(
            f"repair delta on {d['cases']} pair(s): violations {d['violations_before']} -> {d['violations_after']} "
            f"(reduced={d['reduced']} same={d['same']} increased={d['increased']}); "
            f"tests fail->pass {d['tests_fail_to_pass']}, pass->fail {d['tests_pass_to_fail']}",
            flush=True,
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="benchmark.runner",
        description="Evaluate fixed Python code records in JSONL, a single file, or case directories.",
    )
    parser.add_argument(
        "target",
        type=Path,
        help="a JSONL suite (one record per line), a .py file, or a directory of cases",
    )
    parser.add_argument(
        "--checks",
        default=",".join(ALL_CHECKS),
        help=f"comma-separated list from: {','.join(ALL_CHECKS)}",
    )
    parser.add_argument("--python", default=sys.executable, help="interpreter used to run test entries")
    parser.add_argument("--timeout", type=float, default=30.0, help="per-command timeout in seconds")
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("benchmark-results.json"),
        help="JSON report path (empty string disables)",
    )
    args = parser.parse_args(argv)
    output_path = args.output if str(args.output) not in ("", ".") else None

    checks = [c.strip() for c in args.checks.split(",") if c.strip()]
    unknown = [c for c in checks if c not in ALL_CHECKS]
    if unknown:
        parser.error(f"unknown checks: {unknown}")
    root = args.target.resolve()
    if not root.exists():
        parser.error(f"target not found: {root}")

    cases = load_cases(root)
    print(f"evaluating {len(cases)} case(s) with checks: {','.join(checks)}", flush=True)
    results = []
    for case in cases:
        result = run_case(case, checks, args.python, args.timeout)
        print_case(result)
        results.append(result)
    metrics = compute_metrics(results)

    print_metrics(metrics)
    if output_path:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "target": str(root),
            "checks": checks,
            "python": args.python,
            "cases": [asdict(r) for r in results],
            "metrics": metrics,
        }
        output_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        print(f"\nresults written to {output_path}", flush=True)
    return 1 if any(r.verdict == "bad" for r in results) else 0


if __name__ == "__main__":
    raise SystemExit(main())
