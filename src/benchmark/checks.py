from __future__ import annotations

import ast
import builtins
import importlib.util
import json
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from benchmark.case import Case


@dataclass
class Violation:
    check: str
    message: str
    severity: str = "error"
    location: str = ""

    def as_dict(self) -> dict:
        return {
            "check": self.check,
            "message": self.message,
            "severity": self.severity,
            "location": self.location,
        }


@dataclass
class Outcome:
    violations: list[Violation] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


MODULE_DUNDERS = frozenset(
    {
        "__name__",
        "__file__",
        "__doc__",
        "__package__",
        "__spec__",
        "__loader__",
        "__builtins__",
        "__debug__",
        "__annotations__",
        "__dict__",
        "__class__",
        "__path__",
        "__cached__",
    }
)

BUILTIN_NAMES = frozenset(dir(builtins)) | MODULE_DUNDERS

MAX_REPORTED_NAMES = 25


def check_syntax(source: str, path: Path) -> Outcome:
    try:
        ast.parse(source)
    except SyntaxError as exc:
        return Outcome(
            violations=[
                Violation(
                    "syntax",
                    f"SyntaxError: {exc.msg}",
                    location=f"{path.name}:{exc.lineno or 0}",
                )
            ]
        )
    return Outcome()


def _collect_defined(tree: ast.Module) -> set[str] | None:
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
            names.add(node.id)
        elif isinstance(node, ast.arg):
            names.add(node.arg)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.asname or alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if any(alias.name == "*" for alias in node.names):
                return None
            for alias in node.names:
                names.add(alias.asname or alias.name)
        elif isinstance(node, (ast.Global, ast.Nonlocal)):
            names.update(node.names)
        elif isinstance(node, ast.ExceptHandler) and node.name:
            names.add(node.name)
        elif isinstance(node, ast.MatchAs) and node.name:
            names.add(node.name)
        elif isinstance(node, ast.MatchStar) and node.name:
            names.add(node.name)
        elif isinstance(node, ast.MatchMapping) and node.rest:
            names.add(node.rest)
    return names | BUILTIN_NAMES


def check_undefined_names(source: str, path: Path) -> Outcome:
    tree = ast.parse(source)
    defined = _collect_defined(tree)
    if defined is None:
        return Outcome(notes=["star import present; undefined-name check skipped"])

    used = [
        (node.id, node.lineno)
        for node in ast.walk(tree)
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load)
    ]
    unknown = sorted({(name, line) for name, line in used if name not in defined})
    violations = [
        Violation("undefined_names", f"undefined name '{name}'", location=f"{path.name}:{line}")
        for name, line in unknown[:MAX_REPORTED_NAMES]
    ]
    if len(unknown) > MAX_REPORTED_NAMES:
        violations.append(
            Violation(
                "undefined_names",
                f"+{len(unknown) - MAX_REPORTED_NAMES} more undefined names",
            )
        )
    return Outcome(violations=violations)


def _find_tool(tool: str) -> list[str] | None:
    if importlib.util.find_spec(tool) is not None:
        return [sys.executable, "-m", tool]
    exe = shutil.which(tool)
    if exe is not None:
        return [exe]
    return None


def _run(cmd: list[str], cwd: Path | None, timeout: float) -> subprocess.CompletedProcess | None:
    try:
        return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout)
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return None


def _loc(line: str, path: Path) -> str:
    parts = line.split(":")
    if len(parts) >= 2 and parts[0].endswith(path.name):
        return f"{path.name}:{parts[1]}"
    return ""


def check_mypy(path: Path, timeout: float) -> Outcome:
    cmd = _find_tool("mypy")
    if cmd is None:
        return Outcome(notes=["mypy not installed; check skipped"])
    with tempfile.TemporaryDirectory() as td:
        proc = _run(
            cmd + ["--incremental", f"--cache-dir={td}", "--no-error-summary", str(path)],
            cwd=None,
            timeout=timeout,
        )
    if proc is None:
        return Outcome(notes=["mypy timed out; check skipped"])
    if proc.returncode not in (0, 1):
        detail = (proc.stderr or proc.stdout).strip()[:200]
        return Outcome(notes=[f"mypy failed to run: {detail}"])
    violations = []
    for line in proc.stdout.splitlines():
        if ": error:" in line:
            violations.append(
                Violation(
                    "mypy",
                    line.split(": error:", 1)[1].strip(),
                    location=_loc(line, path),
                )
            )
    return Outcome(violations=violations)


def check_ruff(path: Path, timeout: float) -> Outcome:
    cmd = _find_tool("ruff")
    if cmd is None:
        return Outcome(notes=["ruff not installed; check skipped"])
    proc = _run(cmd + ["check", "--output-format", "concise", str(path)], cwd=None, timeout=timeout)
    if proc is None:
        return Outcome(notes=["ruff timed out; check skipped"])
    if proc.returncode not in (0, 1):
        detail = (proc.stderr or proc.stdout).strip()[:200]
        return Outcome(notes=[f"ruff failed to run: {detail}"])
    violations = []
    for line in proc.stdout.splitlines():
        parts = line.split(":")
        if len(parts) >= 4 and parts[1].isdigit() and parts[2].isdigit():
            message = ": ".join(parts[3:]).strip()
            violations.append(Violation("ruff", message, location=f"{path.name}:{parts[1]}"))
    return Outcome(violations=violations)


def check_bandit(path: Path, timeout: float) -> Outcome:
    cmd = _find_tool("bandit")
    if cmd is None:
        return Outcome(notes=["bandit not installed; check skipped"])
    proc = _run(cmd + ["-q", "-f", "json", str(path)], cwd=None, timeout=timeout)
    if proc is None:
        return Outcome(notes=["bandit timed out; check skipped"])
    if proc.returncode not in (0, 1):
        detail = (proc.stderr or proc.stdout).strip()[:200]
        return Outcome(notes=[f"bandit failed to run: {detail}"])
    try:
        data = json.loads(proc.stdout or "{}")
    except json.JSONDecodeError:
        return Outcome(notes=["bandit produced unparseable output; check skipped"])
    violations = [
        Violation(
            "bandit",
            f"{item.get('test_id', 'B???')}: {item.get('issue_text', '')}",
            severity=str(item.get("issue_severity", "MEDIUM")).lower(),
            location=f"{path.name}:{item.get('line', 0)}",
        )
        for item in data.get("results", [])
    ]
    notes = [f"bandit error: {err}" for err in data.get("errors", [])]
    return Outcome(violations=violations, notes=notes)


def check_tests(case: Case, python: str, timeout: float, candidate_source: str | None = None) -> Outcome:
    test_code = case.test_code
    if test_code is None and case.test_data is not None:
        if not case.entry_point:
            return Outcome(notes=["input_output has no function name; execution check skipped"])
        test_code = (
            f"inputs = {case.test_data.get('inputs', [])!r}\n"
            f"outputs = {case.test_data.get('outputs', [])!r}\n"
            "assert len(inputs) == len(outputs), 'input/output count mismatch'\n"
            f"function = globals()[{case.entry_point!r}]\n"
            "for args, expected in zip(inputs, outputs):\n"
            "    actual = function(*args)\n"
            "    assert actual == expected, f'expected {expected!r}, got {actual!r}'\n"
        )

    if test_code is not None:
        with tempfile.TemporaryDirectory() as td:
            dst = Path(td)
            candidate = dst / "candidate.py"
            test_file = dst / "test_case.py"
            candidate.write_text(
                candidate_source if candidate_source is not None else (case.source_code or ""),
                encoding="utf-8",
            )
            if (case.test_mode or "module") == "script":
                test_file.write_text(test_code, encoding="utf-8")
            else:
                test_file.write_text(
                    "import runpy\n"
                    f"namespace = runpy.run_path({str(candidate)!r})\n"
                    f"exec(compile({test_code!r}, '<benchmark-tests>', 'exec'), namespace, namespace)\n",
                    encoding="utf-8",
                )
            proc = _run([python, "-I", "-B", str(test_file)], cwd=dst, timeout=timeout)
        test_description = "inline tests"
    elif case.test_entry:
        with tempfile.TemporaryDirectory() as td:
            dst = Path(td) / "case"
            shutil.copytree(case.path, dst, ignore=shutil.ignore_patterns("__pycache__"))
            proc = _run([python, "-B", case.test_entry], cwd=dst, timeout=timeout)
        test_description = case.test_entry
    else:
        return Outcome(notes=["no tests provided; execution check skipped"])

    if proc is None:
        return Outcome(
            violations=[
                Violation("tests", f"{test_description} timed out after {timeout}s")
            ]
        )
    if proc.returncode != 0:
        tail = "\n".join((proc.stderr or proc.stdout).strip().splitlines()[-6:])
        return Outcome(
            violations=[
                Violation("tests", f"{test_description} failed (exit {proc.returncode}):\n{tail}")
            ]
        )
    return Outcome(notes=[f"tests passed: {test_description}"])
