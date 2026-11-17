"""Patch strategies for Kappa violations (SPEC.md: Patch)."""

from __future__ import annotations

import importlib.util
import re

NAME_RULES = frozenset({"undefined_call", "undefined_name_read"})
REPORT_ONLY_RULES = frozenset(
    {
        "env_without_default",
        "missing_module",
        "context_mismatch",
        "eval_use",
        "exec_use",
        "pickle_use",
        "shell_true",
        "hardcoded_secret",
    }
)


def _word_pattern(name: str) -> str:
    return r"(?<!\w)" + re.escape(str(name)) + r"(?!\w)"


def _kb_entries(kb: dict) -> list[dict]:
    if not isinstance(kb, dict):
        return []
    categories = kb.get("categories")
    if not isinstance(categories, dict):
        return []
    entries: list[dict] = []
    for group in categories.values():
        if isinstance(group, list):
            entries.extend(item for item in group if isinstance(item, dict))
    return entries


def _kb_entry(kb: dict, graph: dict, violation: dict) -> dict | None:
    entries = _kb_entries(kb)
    node_id = violation.get("node")
    if node_id and isinstance(graph, dict):
        for node in graph.get("nodes", []):
            if isinstance(node, dict) and node.get("id") == node_id:
                ref = node.get("ref")
                if ref:
                    for entry in entries:
                        if entry.get("id") == ref:
                            return entry
                break
    rule = violation.get("rule")
    if rule not in NAME_RULES and rule != "resource_leak":
        return None
    line = violation.get("line")
    current = (violation.get("suggestion") or {}).get("current") if isinstance(
        violation.get("suggestion"), dict
    ) else None
    for entry in entries:
        if line is not None and entry.get("line") != line:
            continue
        if rule == "resource_leak" and entry.get("op") and entry.get("name"):
            return entry
        if rule in NAME_RULES and current and entry.get("name") == current:
            return entry
    return None


def _suggest_from_table(current: str, match_table) -> tuple[str, float] | None:
    try:
        from kappa.Embedding import Encoder

        vector = Encoder().embed([current])[0]
        hits = match_table.most_similar(
            vector, exclude_ids={current, f"symbol:{current}"}, top_k=5
        )
    except Exception:
        return None
    for hit in hits or []:
        if not isinstance(hit, dict):
            continue
        label = hit.get("label")
        if not label or label == current or not re.fullmatch(r"\w+", str(label)):
            continue
        try:
            score = float(hit.get("score", 0.0))
        except (TypeError, ValueError):
            continue
        return str(label), score
    return None


def _resource_target(kb: dict, graph: dict, violation: dict) -> tuple[str | None, int | None]:
    entry = _kb_entry(kb, graph, violation) or {}
    name = violation.get("name") or violation.get("resource") or entry.get("name")
    line = violation.get("line")
    if line is None:
        line = entry.get("line")
    if not name or line is None:
        return None, None
    try:
        return str(name), int(line)
    except (TypeError, ValueError):
        return None, None


def _enclosing_def(lines: list[str], line_number: int) -> int:
    for index in range(min(line_number, len(lines)) - 1, -1, -1):
        stripped = lines[index].strip()
        if stripped.startswith(("def ", "async def ", "class ")):
            return index + 1
    return 1


def _function_last_line(lines: list[str], def_line: int) -> int:
    end = len(lines)
    for index in range(def_line, len(lines)):
        text = lines[index]
        if text.strip() and not text[0].isspace():
            end = index
            break
    return end


def _last_use_line(lines: list[str], start_line: int, end_line: int, name: str) -> int | None:
    pattern = re.compile(_word_pattern(name))
    last = None
    for number in range(max(1, start_line), min(end_line, len(lines)) + 1):
        if pattern.search(lines[number - 1]):
            last = number
    return last


def patch(
    source: str,
    kb: dict,
    graph: dict,
    violations: list[dict],
    match_table=None,
    min_confidence: float = 0.6,
    is_java: bool = False,
) -> dict:
    try:
        return _patch(source, kb, graph, violations, match_table, min_confidence, is_java)
    except Exception:
        return {"patches": [], "unresolved": [], "new_source": source}


def _patch(
    source: str,
    kb: dict,
    graph: dict,
    violations: list[dict],
    match_table,
    min_confidence: float,
    is_java: bool = False,
) -> dict:
    lines = str(source).split("\n")
    patches: list[dict] = []
    unresolved: list[dict] = []
    ordered = sorted(
        (item for item in (violations or []) if isinstance(item, dict)),
        key=lambda item: (
            item.get("line") is None,
            item.get("line") or 0,
            str(item.get("id") or ""),
        ),
    )
    for violation in ordered:
        rule = str(violation.get("rule") or "")
        vid = str(violation.get("id") or "")
        try:
            record, reason = _strategy(
                lines, kb, graph, violation, match_table, min_confidence, len(patches), is_java
            )
        except Exception as exc:
            record, reason = None, f"patch strategy failed: {type(exc).__name__}: {exc}"
        if record is not None:
            patches.append(record)
        else:
            unresolved.append(
                {
                    "violation": vid,
                    "rule": rule,
                    "line": violation.get("line"),
                    "message": violation.get("message", ""),
                    "reason": reason
                    or ("report-only" if rule in REPORT_ONLY_RULES else "no applicable strategy"),
                }
            )
    return {
        "patches": patches,
        "unresolved": unresolved,
        "new_source": apply_patches(source, patches),
    }


def _strategy(
    lines: list[str],
    kb: dict,
    graph: dict,
    violation: dict,
    match_table,
    min_confidence: float,
    next_index: int,
    is_java: bool = False,
) -> tuple[dict | None, str | None]:
    rule = str(violation.get("rule") or "")
    vid = str(violation.get("id") or "")
    patch_id = f"P{next_index + 1:03d}"
    if is_java and rule in ("env_without_default", "missing_module", "eval_use", "exec_use", "pickle_use", "shell_true"):
        return None, "Python-only rule; skipped for Java"
    if rule in ("dead_store", "unused_local"):
        line_number = int(violation.get("line") or 0)
        if not (1 <= line_number <= len(lines)):
            return None, "line out of range"
        stripped = lines[line_number - 1].strip()
        if "=" not in stripped:
            return None, "no assignment on the flagged line"
        rhs = stripped.split("=", 1)[1].strip()
        if re.match(r"^(?:[A-Za-z_][\w.]*\s*\(|new\s)", rhs):
            # side-effectful initializer: keep the call, drop the binding
            terminator = ";" if is_java else ""
            replacement = rhs.rstrip(";").rstrip() + terminator
            return {"kind": "replace_name", "line": line_number,
                    "old": stripped, "new": replacement}, None
        return {"kind": "delete_lines", "lines": [line_number]}, None
    if rule in NAME_RULES:
        suggestion = violation.get("suggestion") if isinstance(violation.get("suggestion"), dict) else {}
        sug_kind = suggestion.get("kind", "")
        if sug_kind == "repo-import" and suggestion.get("module"):
            return _repo_import_patch_record(lines, suggestion, patch_id, vid, rule)
        record, reason = _name_patch_record(
            lines, violation, kb, graph, match_table, min_confidence, patch_id, vid, rule
        )
        if record is not None:
            # library-hint and qualified suggestions also need the import inserted
            if sug_kind in ("library-hint", "qualified") and record.get("kind") == "replace_name":
                import_stmt = suggestion.get("import_statement")
                if not import_stmt and "." in str(record.get("new", "")):
                    module_part = str(record["new"]).split(".")[0]
                    import_stmt = f"import {module_part}"
                if import_stmt:
                    last_import = 0
                    for number, text in enumerate(lines, 1):
                        if re.match(r"\s*(import\s|from\s)", text):
                            last_import = number
                    record["import_statement"] = import_stmt
                    record["import_after_line"] = last_import
                    record["kind"] = "replace_and_import"
            return record, None
        import_record, _import_reason = _import_patch_record(
            lines, kb, graph, violation, patch_id, vid
        )
        if import_record is not None:
            return import_record, None
        return None, reason
    if rule == "unreachable_code":
        return _delete_patch_record(lines, violation, patch_id, vid, rule)
    if rule == "resource_leak":
        return _close_patch_record(lines, kb, graph, violation, patch_id, vid)
    return None, None


def _name_patch_record(
    lines: list[str],
    violation: dict,
    kb: dict,
    graph: dict,
    match_table,
    min_confidence: float,
    patch_id: str,
    vid: str,
    rule: str,
) -> tuple[dict | None, str | None]:
    entry = _kb_entry(kb, graph, violation) or {}
    suggestion = violation.get("suggestion")
    suggestion = suggestion if isinstance(suggestion, dict) else {}
    current = suggestion.get("current") or violation.get("name") or entry.get("name")
    closest = suggestion.get("closest")
    score = suggestion.get("score")
    if closest is None and match_table is not None and current:
        found = _suggest_from_table(str(current), match_table)
        if found is not None:
            closest, score = found
    if not current or not closest:
        return None, "no name suggestion available"
    if str(closest) == str(current):
        return None, "suggestion equals the current name"
    try:
        score_value = float(score) if score is not None else 0.0
    except (TypeError, ValueError):
        score_value = 0.0
    if score_value < min_confidence:
        return None, f"suggestion score {score_value:.3f} is below min_confidence {min_confidence:.3f}"
    line = violation.get("line") if violation.get("line") is not None else entry.get("line")
    if line is None:
        return None, "violation has no line to patch"
    try:
        line_number = int(line)
    except (TypeError, ValueError):
        return None, "violation line is not an integer"
    if not 1 <= line_number <= len(lines):
        return None, f"line {line_number} is outside the source"
    if not re.search(_word_pattern(str(current)), lines[line_number - 1]):
        return None, f"name '{current}' is not present on line {line_number}"
    return (
        {
            "id": patch_id,
            "violation": vid,
            "rule": rule,
            "kind": "replace_name",
            "line": line_number,
            "old": str(current),
            "new": str(closest),
            "confidence": round(score_value, 4),
        },
        None,
    )


def _delete_patch_record(
    lines: list[str], violation: dict, patch_id: str, vid: str, rule: str
) -> tuple[dict | None, str | None]:
    targets: list[int] = []
    for value in violation.get("lines") or [violation.get("line")]:
        try:
            number = int(value)
        except (TypeError, ValueError):
            continue
        if 1 <= number <= len(lines):
            targets.append(number)
    if not targets:
        return None, "no deletable lines on the reported line numbers"
    return (
        {
            "id": patch_id,
            "violation": vid,
            "rule": rule,
            "kind": "delete_lines",
            "lines": targets,
            "confidence": 1.0,
        },
        None,
    )


def _import_patch_record(
    lines: list[str], kb: dict, graph: dict, violation: dict, patch_id: str, vid: str
) -> tuple[dict | None, str | None]:
    entry = _kb_entry(kb, graph, violation) or {}
    message = violation.get("message") or ""
    match = re.search(r"undefined name '([^']+)'", str(message))
    undefined = match.group(1) if match else entry.get("name")
    if not undefined:
        return None, "no undefined name to import"
    receiver = entry.get("receiver") or ""
    if not receiver or str(receiver).split(".")[0] != undefined:
        return None, "undefined name is not used as a module receiver"
    try:
        spec = importlib.util.find_spec(str(undefined))
    except Exception:
        spec = None
    if spec is None:
        return None, f"'{undefined}' is not an importable module"
    last_import = 0
    for number, text in enumerate(lines, 1):
        if re.match(r"\s*(import\s|from\s)", text):
            last_import = number
    return (
        {
            "id": patch_id,
            "violation": vid,
            "rule": violation.get("rule"),
            "kind": "insert_import",
            "line": last_import,
            "module": str(undefined),
            "confidence": 0.9,
        },
        None,
    )


def _repo_import_patch_record(
    lines: list[str], suggestion: dict, patch_id: str, vid: str, rule: str
) -> tuple[dict | None, str | None]:
    module = suggestion.get("module")
    name = suggestion.get("closest")
    if not module or not name:
        return None, "repo-import suggestion missing module or name"
    statement = f"from {module} import {name}"
    last_import = 0
    for number, text in enumerate(lines, 1):
        if re.match(r"\s*(import\s|from\s)", text):
            last_import = number
    return (
        {
            "id": patch_id,
            "violation": vid,
            "rule": rule,
            "kind": "insert_import",
            "line": last_import,
            "module": str(module),
            "statement": statement,
            "confidence": float(suggestion.get("score") or 1.0),
        },
        None,
    )


def _close_patch_record(
    lines: list[str], kb: dict, graph: dict, violation: dict, patch_id: str, vid: str
) -> tuple[dict | None, str | None]:
    name, resource_line = _resource_target(kb, graph, violation)
    if not name or resource_line is None or not 1 <= resource_line <= len(lines):
        return None, "could not identify the leaked resource name"
    def_line = _enclosing_def(lines, resource_line)
    end_line = _function_last_line(lines, def_line)
    span = range(def_line, min(end_line, len(lines)) + 1)
    close_pattern = re.compile(_word_pattern(name) + r"\s*\.\s*close\s*\(")
    if any(close_pattern.search(lines[number - 1]) for number in span):
        return None, f".close() is already called on '{name}' in the enclosing function"
    last_use = _last_use_line(lines, def_line, end_line, name) or resource_line
    last_use_text = lines[last_use - 1].strip() if 1 <= last_use <= len(lines) else ""
    if last_use_text.startswith(("return", "raise")):
        return None, "resource last used inside a return/raise expression; with-statement conversion required"
    return (
        {
            "id": patch_id,
            "violation": vid,
            "rule": "resource_leak",
            "kind": "insert_close",
            "line": last_use,
            "name": name,
            "confidence": 1.0,
        },
        None,
    )


def apply_patches(source: str, patches: list[dict]) -> str:
    try:
        return _apply_patches(str(source), list(patches or []))
    except Exception:
        return source


def _apply_patches(source: str, patches: list[dict]) -> str:
    lines = source.split("\n")
    replacements: dict[int, list[tuple[str, str]]] = {}
    deletions: set[int] = set()
    inserts: dict[int, list[str]] = {}
    for record in patches:
        try:
            _collect_edit(record, lines, replacements, deletions, inserts)
        except Exception:
            continue
    output: list[str] = []
    for fragment in inserts.get(0, []):
        output.append(fragment)
    for number, text in enumerate(lines, 1):
        if number in deletions:
            continue
        for old, new in replacements.get(number, []):
            text = re.sub(_word_pattern(old), new.replace("\\", "\\\\"), text)
        output.append(text)
        for fragment in inserts.get(number, []):
            indent = re.match(r"[ \t]*", text).group(0)
            output.append(indent + fragment)
    return "\n".join(output)


def _collect_edit(
    record: dict,
    lines: list[str],
    replacements: dict[int, list[tuple[str, str]]],
    deletions: set[int],
    inserts: dict[int, list[str]],
) -> None:
    if not isinstance(record, dict):
        return
    kind = record.get("kind")
    if kind == "replace_name":
        line_number = int(record.get("line") or 0)
        old = record.get("old")
        new = record.get("new")
        if old and new is not None and 1 <= line_number <= len(lines):
            replacements.setdefault(line_number, []).append((str(old), str(new)))
    elif kind == "delete_lines":
        for value in record.get("lines") or []:
            number = int(value)
            if 1 <= number <= len(lines):
                deletions.add(number)
    elif kind == "insert_close":
        line_number = int(record.get("line") or 0)
        name = record.get("name")
        is_java = record.get("is_java", False)
        if name and 1 <= line_number <= len(lines):
            terminator = ";" if is_java else ""
            fragment = f"{name}.close(){terminator}"
            fragments = inserts.setdefault(line_number, [])
            if fragment not in fragments:
                fragments.append(fragment)
    elif kind == "insert_import":
        module = record.get("module")
        statement = record.get("statement")
        if module or statement:
            line_number = int(record.get("line") or 0)
            fragments = inserts.setdefault(line_number, [])
            fragment = str(statement) if statement else f"import {module}"
            if fragment not in fragments:
                fragments.append(fragment)
    elif kind == "replace_and_import":
        line_number = int(record.get("line") or 0)
        old = record.get("old")
        new = record.get("new")
        import_stmt = record.get("import_statement")
        import_after = int(record.get("import_after_line") or 0)
        if old and new is not None and 1 <= line_number <= len(lines):
            replacements.setdefault(line_number, []).append((str(old), str(new)))
        if import_stmt:
            target = import_after if import_after > 0 else 0
            fragments = inserts.setdefault(target, [])
            if fragment_text := str(import_stmt):
                if fragment_text not in fragments:
                    fragments.append(fragment_text)


if __name__ == "__main__":
    import difflib
    import json
    import sys
    from pathlib import Path

    package_parent = str(Path(__file__).resolve().parent.parent)
    if package_parent not in sys.path:
        sys.path.insert(0, package_parent)
    from kappa.Parser import parse_file

    demo = Path(__file__).resolve().parent / "examples" / "demo_violations.py"
    source = demo.read_text(encoding="utf-8")
    kb = parse_file(demo)

    call = next(
        (e for e in kb["categories"]["calls"] if e.get("name") == "sum_value"), None
    )
    resource = next(
        (e for e in kb["categories"]["dataflow"] if e.get("name") == "handle" and e.get("op")),
        None,
    )
    collect_returns = [
        e["line"]
        for e in kb["categories"]["control_flow"]
        if e.get("kind") == "return" and e.get("in_function") == "collect"
    ]
    assert call and resource and collect_returns, "demo KB is missing expected entries"

    violations = [
        {
            "id": "V001",
            "node": "node:000",
            "category": "calls",
            "rule": "undefined_call",
            "severity": "error",
            "line": call["line"],
            "message": "call to undefined name 'sum_value'",
            "suggestion": {
                "kind": "name",
                "current": "sum_value",
                "closest": "sum_values",
                "score": 0.9,
            },
        },
        {
            "id": "V002",
            "node": "node:000",
            "category": "control_flow",
            "rule": "unreachable_code",
            "severity": "error",
            "line": max(collect_returns) + 1,
            "message": "unreachable statement after return",
        },
        {
            "id": "V003",
            "node": "node:000",
            "category": "dataflow",
            "rule": "resource_leak",
            "severity": "error",
            "line": resource["line"],
            "message": "resource 'handle' is opened but never closed",
            "name": "handle",
        },
    ]

    result = patch(source, kb, None, violations, min_confidence=0.6)
    new_source = result["new_source"]
    assert "sum_values(v)" in new_source, "rename patch did not apply"
    assert "sum_value(v)" not in new_source, "old call name still present"
    assert 'print("done")' not in new_source, "unreachable line was not deleted"
    assert "handle.close()" not in new_source, (
        "insert_close must not apply when the resource's last use is a return expression"
    )
    unresolved_rules = {item["rule"]: item["reason"] for item in result["unresolved"]}
    assert "resource_leak" in unresolved_rules, "resource_leak should be unresolved for return-expression last use"

    print(json.dumps({"patches": result["patches"], "unresolved": result["unresolved"]}, indent=2))
    print(
        "".join(
            difflib.unified_diff(
                source.splitlines(keepends=True),
                new_source.splitlines(keepends=True),
                fromfile="demo_violations.py",
                tofile="demo_violations.repaired.py",
            )
        )
    )
    print("[kappa] Patcher smoke test passed", flush=True)
