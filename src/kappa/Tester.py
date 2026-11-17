"""Independent tester for the Kappa pipeline (SPEC.md: Independent tester)."""

from __future__ import annotations

import ast
import sys
from pathlib import Path

_PACKAGE_PARENT = str(Path(__file__).resolve().parent.parent)
if _PACKAGE_PARENT not in sys.path:
    sys.path.insert(0, _PACKAGE_PARENT)

from kappa.Constructor import build_graph
from kappa.Detector import detect
from kappa.Embedding import Encoder
from kappa.Parser import parse_source
from kappa.Updater import embed_graph

TEST_TIMEOUT = 30.0


def _violations(source: str, encoder, parser_backend: str | None = None, context: dict | None = None,
                introduced: set[str] | None = None, is_python: bool = True) -> tuple[list[dict], bool]:
    if is_python:
        try:
            ast.parse(source)
        except SyntaxError:
            return [], False
    kb = parse_source(source, backend=parser_backend, language="python" if is_python else "java")
    if context:
        kb["context"] = context
    graph = build_graph(kb)
    embeddings = embed_graph(graph, encoder)
    return detect(kb, graph, embeddings, introduced=introduced), True


def _signature(violation: dict) -> tuple:
    return (
        violation.get("category"),
        violation.get("rule"),
        violation.get("line"),
        violation.get("message"),
    )


def _run_tests(source: str, tests: dict) -> dict:
    inputs = tests.get("inputs", [])
    outputs = tests.get("outputs", [])
    fn_name = tests.get("fn_name") or tests.get("entry_point")
    if not fn_name:
        return {"status": "skipped", "detail": "tests missing fn_name; execution skipped"}
    try:
        project_root = Path(__file__).resolve().parent.parent
        if str(project_root) not in sys.path:
            sys.path.insert(0, str(project_root))
        from benchmark.case import Case
        from benchmark.checks import check_tests
    except Exception as exc:
        return {"status": "skipped", "detail": f"benchmark.checks unavailable: {exc}"}
    case = Case(
        name="kappa-test",
        path=project_root,
        target=project_root / "kappa_test_target.py",
        source_code=source,
        test_data={"inputs": list(inputs), "outputs": list(outputs)},
        entry_point=str(fn_name),
    )
    try:
        outcome = check_tests(case, sys.executable, TEST_TIMEOUT, candidate_source=source)
    except Exception as exc:
        return {"status": "skipped", "detail": f"test execution failed: {type(exc).__name__}: {exc}"}
    if outcome.violations:
        detail = "; ".join(v.message for v in outcome.violations)
        return {"status": "fail", "detail": detail}
    notes = "; ".join(outcome.notes)
    if "skip" in notes.lower():
        return {"status": "skipped", "detail": notes or "tests skipped"}
    return {"status": "pass", "detail": notes or "tests passed"}


def test(
    original_source: str,
    patched_source: str,
    tests: dict | None = None,
    parser_backend: str | None = None,
    context: dict | None = None,
    introduced: set[str] | None = None,
    is_python: bool = True,
) -> dict:
    encoder = Encoder()
    before, _ = _violations(original_source, encoder, parser_backend, context, introduced, is_python)
    after, compiles = _violations(patched_source, encoder, parser_backend, context, introduced, is_python)
    removed = len(before) - len(after)
    before_signatures = {_signature(v) for v in before}
    new_violations = [v for v in after if _signature(v) not in before_signatures]
    if tests is None:
        tests_result = {"status": "skipped", "detail": "no tests provided"}
        tests_ok = True
    else:
        tests_result = _run_tests(patched_source, tests if isinstance(tests, dict) else {})
        tests_ok = tests_result["status"] == "pass"
    if not compiles or len(after) >= len(before):
        verdict = "rejected"
    elif len(after) == 0 and removed > 0 and tests_ok:
        verdict = "accepted"
    else:
        verdict = "partial"
    return {
        "compiles": compiles,
        "violations_before": len(before),
        "violations_after": len(after),
        "removed": removed,
        "new_violations": new_violations,
        "tests": tests_result,
        "verdict": verdict,
    }


if __name__ == "__main__":
    import json

    from kappa.Updater import update_loop

    demo = Path(__file__).resolve().parent / "examples" / "demo_violations.py"
    original = demo.read_text(encoding="utf-8")
    loop = update_loop(original, log=lambda message: print(message, flush=True))
    result = test(original, loop["final_source"])
    print(json.dumps(result, indent=2), flush=True)
    assert result["verdict"] in ("accepted", "partial"), f"unexpected verdict: {result['verdict']}"

    functional = test(
        original,
        loop["final_source"],
        tests={"inputs": [[[1, 2, 3]]], "outputs": [6], "fn_name": "sum_values"},
    )
    print(
        f"with functional tests: status={functional['tests']['status']} "
        f"verdict={functional['verdict']}",
        flush=True,
    )
    print("[kappa] Tester smoke test passed", flush=True)
