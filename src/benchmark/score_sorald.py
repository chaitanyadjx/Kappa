#!/usr/bin/env python3
"""Sorald benchmark scorer: (ID / RULE-KEY(s), FILE_PATH) -> Score.

Oracle for SonarJava static-violation repair. Runs Sorald's violation
detection (the `mine` command) on the given Java file for the given Sonar
rule key(s) and reports how many violations are still present.

Verdicts
    CLEAN              -> 0 violations found (exit code 0)
    VIOLATIONS_PRESENT -> >0 violations found (exit code 1)
    ERROR              -> Sorald failed to run / file missing (exit code 2)

The oracle is used in two directions:
    C1 (before)  original buggy file  -> expect VIOLATIONS_PRESENT (exit 1)
    C2 (after)   repaired file        -> expect CLEAN              (exit 0)

CLI conventions mirror src/benchmark/score_fixeval.py:
    --id / --file            single-file mode (add --rule-keys)
    --pairs FILE.jsonl       batch mode: {"id", "file_path", "rule_keys"} per line
    --out FILE               write the JSON results to FILE (stdout gets them too)

Usage examples
    # single file
    python3 score_sorald.py --id demo-c1 \
        --file benchmark/examples/SoraldDemo.java \
        --rule-keys S1854,S2116,S4973

    # batch
    python3 score_sorald.py --pairs pairs.jsonl --out results.json

Requires: java (or SORALD_JAVA), and the Sorald jar-with-dependencies
(default: benchmark/sorald-0.8.6-jar-with-dependencies.jar, override with
--sorald-jar or SORALD_JAR).
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

# Sorald 0.8.6 is not silent: it prints SLF4J/no-op-logger lines to stderr.
SLF4J_NOISE_PREFIXES = ("SLF4J:", "Defaulting to no-op", "See https://www.slf4j.org")

DEFAULT_JAR_RELATIVE = Path(__file__).resolve().parent / "sorald-0.8.6-jar-with-dependencies.jar"

CLEAN = "CLEAN"
VIOLATIONS_PRESENT = "VIOLATIONS_PRESENT"
ERROR = "ERROR"

EXIT_CLEAN = 0
EXIT_VIOLATIONS = 1
EXIT_ERROR = 2


def find_sorald_jar(cli_value: str | None) -> Path:
    """Resolve the Sorald jar: --sorald-jar > SORALD_JAR > sibling of this script."""
    candidates = []
    if cli_value:
        candidates.append(Path(cli_value).expanduser())
    env_value = os.environ.get("SORALD_JAR")
    if env_value:
        candidates.append(Path(env_value).expanduser())
    candidates.append(DEFAULT_JAR_RELATIVE)
    for cand in candidates:
        if cand.is_file():
            return cand
    raise FileNotFoundError(
        "Sorald jar not found. Looked at: "
        + ", ".join(str(c) for c in candidates)
        + ". Get it from https://github.com/ASSERT-KTH/sorald/releases "
        "(sorald-<version>-jar-with-dependencies.jar)."
    )


def normalize_rule_keys(raw) -> list[str]:
    """Accept ['S1854', ...], 'S1854,S2116', repeated --rule-keys flags, or None.

    Commas are split inside every element, so ['S1854,S2116'] and
    ['S1854', 'S2116'] both normalize to ['S1854', 'S2116'].
    """
    if raw is None:
        return []
    if isinstance(raw, str):
        parts: list[str] = raw.split(",")
    else:
        parts = []
        for item in raw:
            parts.extend(str(item).split(","))
    return [k.strip() for k in parts if k.strip()]


def parse_warning_counts(stats_path: Path, rule_keys: list[str]) -> dict[str, int]:
    """Extract per-rule violation counts from a Sorald mine stats file.

    Sorald's stats JSON looks like:
        {"minedRules": [{"ruleKey": "S4973", "warningLocations": [...], ...}, ...]}
    Rules with zero violations are simply absent from minedRules.
    """
    data = json.loads(stats_path.read_text())
    found = {k: 0 for k in rule_keys}
    for mined in data.get("minedRules", []):
        key = mined.get("ruleKey")
        if key is None:
            continue
        count = len(mined.get("warningLocations") or [])
        found[key] = found.get(key, 0) + count
        if rule_keys and key not in rule_keys:
            # Sorald reported a rule we did not ask for (should not happen);
            # keep it so totals stay honest.
            found.setdefault(key, count)
    return found


def run_sorald_mine(jar: Path, java_bin: str, source: Path, rule_keys: list[str],
                    timeout: float, workdir: Path) -> dict[str, int]:
    """Run `sorald mine` on one source file/dir; return per-rule violation counts."""
    if not Path(source).exists():
        raise FileNotFoundError(f"source file not found: {source}")
    if not rule_keys:
        raise ValueError("no rule keys given (use --rule-keys or \"rule_keys\" in the pairs file)")

    stats_path = workdir / f"mine-stats-{os.getpid()}-{abs(hash(str(source))) % 100000}.json"
    cmd = [
        java_bin, "-jar", str(jar),
        "mine",
        "--source", str(source),
        "--rule-keys", ",".join(rule_keys),
        "--stats-output-file", str(stats_path),
    ]
    proc = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout,
        stdin=subprocess.DEVNULL,
    )
    stderr_tail = "\n".join(
        line for line in (proc.stderr or "").splitlines()
        if line and not line.startswith(SLF4J_NOISE_PREFIXES)
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"sorald mine exited {proc.returncode}: {stderr_tail.strip() or proc.stdout.strip()}"
        )
    if not stats_path.exists():
        raise RuntimeError(
            f"sorald mine produced no stats file. stdout: {proc.stdout.strip()} "
            f"stderr: {stderr_tail.strip()}"
        )
    try:
        return parse_warning_counts(stats_path, rule_keys)
    finally:
        stats_path.unlink(missing_ok=True)


def score_one(jar: Path, java_bin: str, pair_id: str, file_path: str,
              rule_keys: list[str], timeout: float) -> dict:
    """Score one (id, rule_keys, file_path) triple."""
    result = {
        "id": pair_id,
        "file_path": str(file_path),
        "rule_keys": rule_keys,
    }
    try:
        with tempfile.TemporaryDirectory(prefix="sorald-score-") as tmp:
            counts = run_sorald_mine(jar, java_bin, Path(file_path), rule_keys,
                                     timeout, Path(tmp))
        per_rule = {k: counts.get(k, 0) for k in rule_keys}
        # Keep any extra keys Sorald reported out of the per-rule view but in
        # the total, so the count is never understated.
        extra = {k: v for k, v in counts.items() if k not in per_rule and v > 0}
        total = sum(counts.values())
        result["per_rule"] = per_rule
        if extra:
            result["extra_rules_found"] = extra
        result["violations_found"] = total
        result["verdict"] = CLEAN if total == 0 else VIOLATIONS_PRESENT
    except (RuntimeError, FileNotFoundError, ValueError, subprocess.TimeoutExpired,
            json.JSONDecodeError) as exc:
        result["violations_found"] = None
        result["verdict"] = ERROR
        result["error"] = str(exc)
    return result


def load_pairs(pairs_path: Path, default_rule_keys: list[str]) -> list[dict]:
    """Read a JSONL batch file: {"id", "file_path", "rule_keys"} per line.

    rule_keys may be a JSON list or a comma-separated string; when a line
    omits it, the CLI --rule-keys default is used.
    """
    pairs = []
    with pairs_path.open() as fh:
        for lineno, line in enumerate(fh, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{pairs_path}:{lineno}: invalid JSON: {exc}") from exc
            missing = {"id", "file_path"} - set(item)
            if missing:
                raise ValueError(
                    f"{pairs_path}:{lineno}: missing required field(s): {sorted(missing)}"
                )
            keys = normalize_rule_keys(item.get("rule_keys")) or default_rule_keys
            pairs.append({"id": item["id"], "file_path": item["file_path"], "rule_keys": keys})
    return pairs


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="score_sorald.py",
        description="Sorald oracle: (ID/RULE-KEY + FILE_PATH) -> violation score. "
                    "Exit 0 iff every scored file is CLEAN.",
    )
    parser.add_argument("--id", help="pair id for single-file mode")
    parser.add_argument("--file", dest="file_path", help="Java file to score (single mode)")
    parser.add_argument(
        "--rule-keys",
        dest="rule_keys",
        action="append",
        default=[],
        metavar="KEY[,KEY...]",
        help="Sonar rule key(s), comma-separated and/or repeated "
             "(e.g. --rule-keys S1854,S2116,S4973)",
    )
    parser.add_argument("--pairs", type=Path, help="JSONL batch file: {\"id\", \"file_path\", \"rule_keys\"}")
    parser.add_argument("--out", type=Path, help="write JSON results to this file")
    parser.add_argument("--sorald-jar", default=None, help="path to sorald-*-jar-with-dependencies.jar")
    parser.add_argument("--java", dest="java_bin", default=os.environ.get("SORALD_JAVA", "java"),
                        help="java binary to run Sorald with (default: 'java' or $SORALD_JAVA)")
    parser.add_argument("--timeout", type=float, default=300.0,
                        help="per-file Sorald timeout in seconds (default: 300)")
    args = parser.parse_args(argv)

    try:
        jar = find_sorald_jar(args.sorald_jar)
    except FileNotFoundError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return EXIT_ERROR

    default_rule_keys = normalize_rule_keys(args.rule_keys)

    try:
        if args.pairs:
            if args.id or args.file_path:
                parser.error("--pairs cannot be combined with --id/--file")
            pairs = load_pairs(args.pairs, default_rule_keys)
            if not pairs:
                print(f"ERROR: no usable pairs in {args.pairs}", file=sys.stderr)
                return EXIT_ERROR
        elif args.id and args.file_path:
            if not default_rule_keys:
                parser.error("single-file mode needs --rule-keys")
            pairs = [{"id": args.id, "file_path": args.file_path, "rule_keys": default_rule_keys}]
        else:
            parser.error("give either --pairs FILE or both --id and --file")
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return EXIT_ERROR

    results = [
        score_one(jar, args.java_bin, p["id"], p["file_path"], p["rule_keys"], args.timeout)
        for p in pairs
    ]

    summary = {
        "sorald_jar": str(jar),
        "scored": len(results),
        "clean": sum(1 for r in results if r["verdict"] == CLEAN),
        "violations_present": sum(1 for r in results if r["verdict"] == VIOLATIONS_PRESENT),
        "errors": sum(1 for r in results if r["verdict"] == ERROR),
        "results": results,
    }
    output = json.dumps(summary, indent=2)

    if args.out:
        args.out.write_text(output + "\n")

    print(output)

    if summary["errors"]:
        return EXIT_ERROR
    if summary["violations_present"]:
        return EXIT_VIOLATIONS
    return EXIT_CLEAN


if __name__ == "__main__":
    sys.exit(main())
