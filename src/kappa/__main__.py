"""Pipeline CLI for the Kappa APR system (SPEC.md: Pipeline CLI)."""

from __future__ import annotations

import argparse
import ast
import json
import os
import sys
import traceback
from pathlib import Path

_PACKAGE_PARENT = str(Path(__file__).resolve().parent.parent)
if _PACKAGE_PARENT not in sys.path:
    sys.path.insert(0, _PACKAGE_PARENT)


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="kappa",
        description="Kappa APR pipeline: parse -> graph -> embeddings -> update loop -> integrate -> test.",
        epilog="Exit codes: 0 repaired (accepted/partial), 1 rejected, 2 input not parseable, 3 pipeline error.",
    )
    parser.add_argument("input", help="Python source file to repair")
    parser.add_argument("--out", default=None, help="repaired output path (default <input stem>.repaired.py)")
    parser.add_argument("--max-iters", type=int, default=3, help="maximum update-loop iterations (default 3)")
    parser.add_argument("--threshold", type=float, default=0.2, help="context-mismatch cosine threshold (default 0.2)")
    parser.add_argument("--min-confidence", type=float, default=0.6, help="minimum suggestion confidence (default 0.6)")
    parser.add_argument("--report", default=None, help="write a JSON report (updater + tester + integration)")
    parser.add_argument("--backend", choices=["unixcoder", "hash"], default=None, help="embedding backend (default auto)")
    parser.add_argument(
        "--parser-backend",
        choices=["ast", "tree-sitter", "libcst", "spoon"],
        default=None,
        help="parser backend (default auto: ast for Python, tree-sitter for Java)",
    )
    parser.add_argument("--context", default=None, help="full-file context source (raises scope to file level)")
    parser.add_argument("--repo-dir", default=None, help="directory of sibling modules (raises scope to repo level)")
    return parser


def _say(message: str) -> None:
    print(message, flush=True)


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    input_path = Path(args.input)
    out_path = (
        Path(args.out)
        if args.out
        else input_path.with_name(f"{input_path.stem}.repaired.py")
    )
    if args.backend:
        os.environ["KAPPA_EMBEDDING"] = args.backend
    _say(f"[kappa] repairing {input_path}")
    try:
        source = input_path.read_text(encoding="utf-8")
    except OSError as exc:
        _say(f"[kappa] error: cannot read {input_path}: {exc}")
        return 2
    if input_path.suffix == ".py":
        try:
            ast.parse(source, filename=str(input_path))
        except SyntaxError as exc:
            _say(f"[kappa] error: {input_path} is not valid Python: line {exc.lineno}: {exc.msg}")
            return 2
    try:
        return _run(args, input_path, out_path, source)
    except SyntaxError as exc:
        _say(f"[kappa] error: {input_path} is not valid Python: line {exc.lineno}: {exc.msg}")
        return 2
    except Exception as exc:
        _say(f"[kappa] error: {type(exc).__name__}: {exc}")
        traceback.print_exc()
        return 3


def _run(args: argparse.Namespace, input_path: Path, out_path: Path, source: str) -> int:
    from kappa.Constructor import build_graph
    from kappa.Embedding import Encoder
    from kappa.Integrater import integrate
    from kappa.Parser import build_context, parse_source
    from kappa.Tester import test
    from kappa.Updater import embed_graph, update_loop

    context = None
    if args.context or args.repo_dir:
        context = build_context(
            Path(args.context).read_text(encoding="utf-8") if args.context else None,
            context_name=Path(args.context).name if args.context else "<context>",
            repo_dir=args.repo_dir,
            backend=args.parser_backend,
        )
        file_symbols = len(context.get("file", {}).get("defined", []))
        repo_symbols = len(context.get("repo", {}).get("defined", []))
        _say(f"[kappa] context loaded: {file_symbols} file-level symbols, {repo_symbols} repo-level symbols")

    language = "java" if input_path.suffix == ".java" else None

    _say("[kappa] stage 1/6 parsing source")
    kb = parse_source(source, filename=str(input_path), backend=args.parser_backend,
                      language=language)
    if context:
        kb["context"] = context
    defined = kb.get("symbols", {}).get("defined", []) if isinstance(kb, dict) else []
    categories = kb.get("categories", {}) if isinstance(kb, dict) else {}
    entry_count = sum(len(v) for v in categories.values() if isinstance(v, list))
    _say(f"[kappa]   kb: {len(defined)} defined symbols, {entry_count} entries")

    _say("[kappa] stage 2/6 building graph")
    graph = build_graph(kb)
    _say(
        f"[kappa]   graph: {len(graph.get('nodes', []))} nodes, "
        f"{len(graph.get('edges', []))} edges"
    )

    _say("[kappa] stage 3/6 computing embeddings")
    encoder = Encoder(args.backend)
    embeddings = embed_graph(graph, encoder)
    _say(f"[kappa]   backend={encoder.backend} dim={encoder.dim} vectors={len(embeddings)}")

    _say(
        f"[kappa] stage 4/6 update loop (max-iters={args.max_iters}, "
        f"threshold={args.threshold}, min-confidence={args.min_confidence})"
    )
    language = "java" if input_path.suffix == ".java" else None
    updater = update_loop(
        source,
        max_iters=args.max_iters,
        threshold=args.threshold,
        min_confidence=args.min_confidence,
        log=_say,
        parser_backend=args.parser_backend,
        context=context,
        language=language,
    )
    _say(
        f"[kappa]   iters_run={updater['iters_run']} "
        f"remaining_violations={len(updater['remaining_violations'])}"
    )

    _say(f"[kappa] stage 5/6 integration -> {out_path}")
    integration = integrate(input_path, updater["final_source"], out_path)
    _say(
        f"[kappa]   written={integration['written']} changed={integration['changed']} "
        f"diff_lines={len(integration['diff'].splitlines())}"
    )

    is_python = input_path.suffix == ".py"
    _say("[kappa] stage 6/6 independent test")
    tester = test(source, updater["final_source"], parser_backend=args.parser_backend, context=context,
                  introduced=set(updater.get("introduced") or []), is_python=(language is None))
    _say(
        f"[kappa]   compiles={tester['compiles']} violations "
        f"{tester['violations_before']} -> {tester['violations_after']} "
        f"(removed {tester['removed']}, new {len(tester['new_violations'])})"
    )
    _say(f"[kappa]   tests={tester['tests']['status']} verdict={tester['verdict']}")

    _say("[kappa] summary:")
    for violation in updater["remaining_violations"]:
        _say(
            f"[kappa]   remaining: {violation.get('rule')} at line {violation.get('line')}: "
            f"{violation.get('message', '')}"
        )
    _say(f"[kappa] verdict: {tester['verdict']}")

    if args.report:
        report_path = Path(args.report)
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report = {"updater": updater, "tester": tester, "integration": integration}
        report_path.write_text(
            json.dumps(report, indent=2, default=str), encoding="utf-8"
        )
        _say(f"[kappa] report written to {report_path}")
    return 0 if tester["verdict"] in ("accepted", "partial") else 1


if __name__ == "__main__":
    raise SystemExit(main())
