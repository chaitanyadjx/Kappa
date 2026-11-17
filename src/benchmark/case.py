from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Case:
    name: str
    path: Path
    target: Path
    label: str | None = None
    test_entry: str | None = None
    expected_checks: list[str] = field(default_factory=list)
    source_code: str | None = None
    code_after: str | None = None
    test_code: str | None = None
    test_data: dict | None = None
    test_mode: str | None = None
    entry_point: str | None = None
    category: str | None = None


def load_jsonl(path: Path) -> list[Case]:
    cases = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{path}:{line_number}: invalid JSON: {exc.msg}") from exc
        name = str(record.get("id", record.get("task_id", line_number)))
        code = record.get("code", record.get("deal_response"))
        if not isinstance(code, str):
            raise ValueError(f"{path}:{line_number}: expected string field 'code'")
        label = record.get("label")
        if label not in (None, "good", "bad"):
            raise ValueError(f"{path}:{line_number}: label must be 'good', 'bad', or omitted")
        test_code = record.get("tests", record.get("test_code"))
        if test_code is not None and not isinstance(test_code, str):
            raise ValueError(f"{path}:{line_number}: 'tests' must be a Python source string")
        test_data = record.get("input_output")
        if isinstance(test_data, str):
            try:
                test_data = json.loads(test_data)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}:{line_number}: 'input_output' is not valid JSON") from exc
        if test_data is not None and not isinstance(test_data, dict):
            raise ValueError(f"{path}:{line_number}: 'input_output' must be a JSON object")
        cases.append(
            Case(
                name=name,
                path=path.parent,
                target=path,
                label=label,
                source_code=code,
                code_after=record.get("code_after", record.get("code_repaired")),
                test_code=test_code,
                test_data=test_data,
                test_mode=record.get("run"),
                entry_point=record.get("entry_point", test_data.get("fn_name") if test_data else None),
                category=record.get("category", record.get("error_type")),
            )
        )
    if not cases:
        raise ValueError(f"no benchmark records found in {path}")
    return cases


def load_cases(root: Path) -> list[Case]:
    if root.is_file() and root.suffix == ".jsonl":
        return load_jsonl(root)
    if root.is_file():
        return [Case(name=root.stem, path=root.parent, target=root)]
    if not root.is_dir():
        raise ValueError(f"target must be a Python file, JSONL file, or directory: {root}")

    cases: list[Case] = []
    py_files: list[Path] = []
    for child in sorted(root.iterdir()):
        if child.is_dir() and (child / "case.json").is_file():
            meta = json.loads((child / "case.json").read_text(encoding="utf-8"))
            target = child / meta.get("target", "target.py")
            if not target.is_file():
                raise FileNotFoundError(f"case {child.name}: target not found: {target}")
            cases.append(
                Case(
                    name=child.name,
                    path=child,
                    target=target,
                    label=meta.get("label"),
                    test_entry=meta.get("test_entry"),
                    expected_checks=list(meta.get("expected_checks", [])),
                )
            )
        elif child.is_file() and child.suffix == ".py":
            py_files.append(child)

    if not cases:
        cases = [Case(name=p.stem, path=p.parent, target=p) for p in py_files]
    if not cases:
        raise ValueError(f"no cases found under {root}")
    return cases
