"""Kappa Detector: Knowledge DB + graph + embeddings -> violations (SPEC.md, Violations)."""

import builtins
import difflib
import importlib.util
import json
import math
import re

RESOURCE_KINDS = {"file", "socket", "db"}
IDENTIFIER_RE = re.compile(r"[A-Za-z_]\w*\Z")
BUILTIN_NAMES = frozenset(dir(builtins))
SEVERITY = {
    "undefined_call": "error",
    "undefined_name_read": "error",
    "missing_module": "error",
    "resource_leak": "error",
    "unreachable_code": "error",
    "dead_store": "warning",
    "unused_local": "warning",
    "env_without_default": "warning",
    "eval_use": "warning",
    "exec_use": "warning",
    "pickle_use": "warning",
    "shell_true": "warning",
    "hardcoded_secret": "warning",
    "context_mismatch": "warning",
}


def _root(text):
    part = str(text or "").strip()
    for sep in (".", "[", "("):
        part = part.split(sep, 1)[0]
    return part.strip()


def _provided_names(entry):
    provided = set()
    module = str(entry.get("module") or "")
    names = entry.get("names") or []
    alias = entry.get("alias")
    if names:
        for name in names:
            provided.add(str(name).split(" as ")[-1].strip())
    if alias:
        provided.add(str(alias))
    if not names and module:
        provided.add(module)
        provided.add(_root(module))
        parts = module.split(".")
        if len(parts) > 1:
            provided.add(parts[-1])
    return {name for name in provided if name}


def _closest(defined, name):
    best, best_score = None, 0.0
    for candidate in defined:
        score = difflib.SequenceMatcher(None, name, candidate).ratio()
        if score > best_score:
            best, best_score = candidate, score
    if best is None:
        return None
    return best, round(best_score, 3)


def _cosine(a, b):
    num = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return num / (norm_a * norm_b)


def _mean(vectors):
    size = len(vectors[0])
    return [sum(vec[i] for vec in vectors) / len(vectors) for i in range(size)]


def _dead_lines_after(source_lines: list[str], stop_line: int, is_java: bool) -> set[int]:
    """Statement lines made unreachable by a return/raise at stop_line (1-indexed).

    A terminator only kills straight-line flow inside its OWN block. A return
    nested in an if/loop does not make the enclosing block's continuation
    unreachable. Python: a dedent below the terminator's indent exits the dead
    region. Java: the first closing brace that drops below the terminator's
    brace depth exits it (checked left-to-right so `} else {` stops the scan
    and else-branches are never flagged). False negatives are acceptable;
    false positives delete live code.
    """
    dead: set[int] = set()
    if not source_lines or stop_line < 1 or stop_line > len(source_lines):
        return dead
    if is_java:
        depth = 0
        for i in range(stop_line):
            depth += source_lines[i].count("{") - source_lines[i].count("}")
        base = depth
        for i in range(stop_line, len(source_lines)):
            dropped = False
            for ch in source_lines[i]:
                if ch == "{":
                    depth += 1
                elif ch == "}":
                    depth -= 1
                    if depth < base:
                        dropped = True
                        break
            if dropped:
                break
            stripped = source_lines[i].strip()
            if stripped and not stripped.startswith(("//", "*", "/*")):
                dead.add(i + 1)
        return dead

    def indent(line: str) -> int:
        return len(line) - len(line.lstrip())

    base = indent(source_lines[stop_line - 1])
    for i in range(stop_line, len(source_lines)):
        stripped = source_lines[i].strip()
        if not stripped or stripped.startswith("#"):
            continue
        if indent(source_lines[i]) < base:
            break
        dead.add(i + 1)
    return dead


_ASSIGN_PATTERN = re.compile(r"(?<![=!<>+\-*/])(=)(?!=)")
_HEADER_STARTS = ("for", "while", "catch", "else", "try", "switch", "do")


def _dead_store_scan(source_lines: list[str], assigns: list[dict], is_java: bool,
                     function_starts: dict[str, list[int]]) -> list[tuple[int, str, dict]]:
    """Assigns overwritten before any read (dead_store) or never read
    (unused_local). Text-scoped to the enclosing function body: any later
    occurrence of the name counts as a read unless the line is itself a
    plain re-assignment whose RHS does not mention the name. Header lines
    (for/while/catch/...), multi-target and augmented assignments are
    skipped; a kill only counts when no read sits between the two writes.
    """
    by_key: dict[tuple[str, str], list[tuple[int, dict]]] = {}
    for entry in assigns:
        if entry.get("kind") != "assign":
            continue
        name = str(entry.get("name") or "")
        function = entry.get("in_function")
        line = int(entry.get("line") or 0)
        if not function or not IDENTIFIER_RE.fullmatch(name):
            continue
        if line < 1 or line > len(source_lines):
            continue
        by_key.setdefault((function, name), []).append((line, entry))

    def function_end_java(start_line: int) -> int:
        depth = 0
        for i in range(start_line):
            depth += source_lines[i].count("{") - source_lines[i].count("}")
        # the def line's opening brace is already counted, so statements sit
        # at exactly this depth; the body ends when depth drops below it
        body_depth = depth
        for i in range(start_line, len(source_lines)):
            depth += source_lines[i].count("{") - source_lines[i].count("}")
            if depth < body_depth:
                return i
        return len(source_lines)

    def function_end_python(start_line: int) -> int:
        def_line = source_lines[start_line - 1]
        def_indent = len(def_line) - len(def_line.lstrip())
        for i in range(start_line, len(source_lines)):
            text = source_lines[i]
            stripped = text.strip()
            if not stripped or stripped.startswith("#"):
                continue
            if stripped.startswith(("def ", "class ")):
                continue
            if len(text) - len(text.lstrip()) <= def_indent:
                return i
        return len(source_lines)

    flagged: list[tuple[int, str, dict]] = []
    reported: set[int] = set()
    for (function, name), entries in by_key.items():
        starts = [s for s in function_starts.get(function, []) if s]
        if not starts:
            continue
        func_start = max(s for s in starts if s <= max(line for line, _ in entries)) if \
            any(s <= max(line for line, _ in entries) for s in starts) else min(starts)
        end = function_end_java(func_start) if is_java else function_end_python(func_start)
        kill_re = re.compile(
            rf"^\s*(?:[A-Za-z_][\w<>\[\],\.]*\s+)?{re.escape(name)}\s*=[^=]")
        for line, entry in sorted(entries):
            if line in reported:
                continue
            stripped = source_lines[line - 1].strip()
            if stripped.startswith(_HEADER_STARTS):
                continue
            lhs = stripped.split("=", 1)[0]
            # multi-target / unpacking lines bind several names at once —
            # deleting or rewriting them would drop live bindings
            if "," in lhs or len(_ASSIGN_PATTERN.findall(stripped)) != 1:
                continue
            verdict = "none"
            for m in range(line, end):
                text = source_lines[m]
                if not re.search(rf"\b{re.escape(name)}\b", text):
                    continue
                is_kill = bool(kill_re.match(text)) and name not in text.split("=", 1)[1]
                verdict = "kill" if is_kill else "read"
                break
            if verdict == "read":
                continue
            rule = "dead_store" if verdict == "kill" else "unused_local"
            flagged.append((line, rule, entry))
            reported.add(line)
    return flagged


def detect(kb: dict, graph: dict, embeddings: dict[str, list[float]],
           threshold: float = 0.2, match_table=None,
           introduced: set[str] | None = None) -> list[dict]:
    nodes = graph.get("nodes") or []
    edges = graph.get("edges") or []
    by_ref = {node.get("ref"): node for node in nodes if node.get("ref")}
    categories = kb.get("categories") or {}
    entry_by_ref = {}
    for items in categories.values():
        for entry in items or []:
            if entry.get("id"):
                entry_by_ref[entry["id"]] = entry
    by_id = {node["id"]: node for node in nodes}
    defined = [s["name"] for s in (kb.get("symbols") or {}).get("defined") or [] if s.get("name")]
    defined_set = set(defined)
    is_java = kb.get("language") == "java" or str(kb.get("file", "")).endswith(".java")
    JAVA_BUILTINS = {"System", "String", "Integer", "Long", "Double", "Float", "Boolean",
                     "Object", "Math", "Byte", "Short", "Character", "StringBuilder",
                     "StringBuffer", "Exception", "RuntimeException", "Throwable",
                     "Error", "Thread", "Runnable"} if is_java else set()
    known_names = defined_set | BUILTIN_NAMES | JAVA_BUILTINS
    provided = set()
    for entry in categories.get("library_usage") or []:
        provided |= _provided_names(entry)
    context = kb.get("context") or {}
    file_defined = [s.get("name") for s in context.get("file", {}).get("defined") or [] if s.get("name")]
    file_defined_set = set(file_defined)
    file_imports = context.get("file", {}).get("imports") or []
    file_provided = set()
    for entry in file_imports:
        file_provided |= _provided_names(entry)
    repo_defined = [s.get("name") for s in context.get("repo", {}).get("defined") or [] if s.get("name")]
    repo_defined_set = set(repo_defined)
    repo_modules = context.get("repo", {}).get("modules") or {}
    local_names = {}
    global_names = set()
    for entry in categories.get("dataflow") or []:
        name = entry.get("name")
        if not name or entry.get("kind") == "annotation":
            # annotations are type *reads*, not bindings — they must stay
            # flaggable as unbound names
            continue
        function = entry.get("in_function")
        if function:
            local_names.setdefault(function, set()).add(name)
        else:
            global_names.add(name)

    found = []
    module_cache: dict = {}

    def add(node, rule, line, message, suggestion=None):
        record = {
            "node": node["id"],
            "category": node.get("category"),
            "rule": rule,
            "severity": SEVERITY.get(rule, "warning"),
            "line": line,
            "message": message,
        }
        if suggestion is not None:
            record["suggestion"] = suggestion
        found.append(record)

    def suggestion_for(current, function=None, node_id=None):
        candidates: list[dict] = []
        close = _closest(defined, current)
        if close:
            candidates.append({"kind": "defined", "current": current, "closest": close[0], "score": close[1]})
        if not is_java:
            builtin_close = _closest(sorted(BUILTIN_NAMES), current)
            if builtin_close:
                candidates.append({"kind": "builtin", "current": current, "closest": builtin_close[0], "score": builtin_close[1]})
        for local in sorted(local_names.get(function, set())):
            score = round(difflib.SequenceMatcher(None, current, local).ratio(), 3)
            if score:
                candidates.append({"kind": "local", "current": current, "closest": local, "score": score})
        candidates.extend(_qualified_candidates(current, categories.get("library_usage") or []))
        candidates.extend(_qualified_candidates(current, file_imports))
        file_close = _closest(file_defined, current)
        if file_close:
            candidates.append({"kind": "file-symbol", "current": current, "closest": file_close[0], "score": file_close[1]})
        if current not in repo_defined_set:
            repo_close = _closest(repo_defined, current)
            if repo_close:
                candidates.append({"kind": "repo-symbol", "current": current, "closest": repo_close[0], "score": repo_close[1]})
        if current in repo_defined_set:
            for module_name, names in repo_modules.items():
                if current in names:
                    candidates.append({
                        "kind": "repo-import",
                        "current": current,
                        "closest": current,
                        "module": module_name,
                        "score": 1.0,
                    })
        try:
            if not is_java:
                from kappa.LibraryHints import lookup as _hint_lookup
                hint = _hint_lookup(current)
                if hint:
                    candidates.append(hint)
        except Exception:
            pass
        if match_table is not None and node_id:
            query_vectors = {}
            for role in ("self", "expectation", "connected"):
                vec = (embeddings or {}).get(f"{node_id}:{role}")
                if vec:
                    query_vectors[role] = vec
            if query_vectors:
                try:
                    hits = match_table.most_similar_combined(
                        query_vectors, exclude_ids={node_id}, top_k=1
                    )
                except Exception:
                    hits = []
                for hit in hits:
                    identifiers = re.findall(r"[A-Za-z_]\w*", hit.get("label") or "")
                    if not identifiers:
                        continue
                    ingredient = identifiers[0]
                    if ingredient == current:
                        continue
                    grounded = (
                        ingredient in defined_set
                        or ingredient in BUILTIN_NAMES
                        or ingredient in provided
                        or ingredient in global_names
                        or ingredient in local_names.get(function, set())
                    )
                    if not grounded:
                        continue
                    candidates.append({
                        "kind": "embedding",
                        "current": current,
                        "closest": ingredient,
                        "score": round(min(hit.get("score", 0.0), 0.99), 3),
                    })
        if not candidates:
            return None
        best = max(candidates, key=lambda item: item["score"])
        result = {"kind": best["kind"], "current": current, "closest": best["closest"], "score": best["score"]}
        if best.get("module"):
            result["module"] = best["module"]
        if best.get("import_statement"):
            result["import_statement"] = best["import_statement"]
        return result

    def _qualified_candidates(current, imports):
        suggestions: list[dict] = []
        for entry in imports:
            module = entry.get("module")
            if not module or entry.get("names"):
                continue
            alias = entry.get("alias")
            if _root(module) == current and alias and alias != current:
                suggestions.append({"kind": "qualified", "current": current, "closest": alias, "score": 1.0})
                continue
            if module in module_cache:
                module_obj = module_cache[module]
            else:
                try:
                    module_obj = importlib.import_module(module)
                except Exception:
                    module_obj = None
                module_cache[module] = module_obj
            if module_obj is not None and hasattr(module_obj, current):
                base = alias or module
                suggestions.append({
                    "kind": "qualified",
                    "current": current,
                    "closest": f"{base}.{current}",
                    "score": 0.9,
                })
            elif module_obj is None:
                # module not installed/importable here, but the full file
                # imports it — qualified suggestion with reduced confidence
                base = alias or module
                suggestions.append({
                    "kind": "qualified",
                    "current": current,
                    "closest": f"{base}.{current}",
                    "score": 0.7,
                })
        return suggestions

    def is_known(name, function):
        if is_java:
            return (name in defined_set or name in JAVA_BUILTINS or name in provided
                    or name in global_names or name in local_names.get(function, set())
                    or name in file_defined_set or name in repo_defined_set
                    or name in (introduced or set())
                    or "." in name)
        return (name in defined_set or name in BUILTIN_NAMES or name in provided
                or name in global_names or name in local_names.get(function, set())
                or name in (introduced or set()))

    for entry in categories.get("calls") or []:
        node = by_ref.get(entry.get("id"))
        if not node:
            continue
        function = entry.get("in_function")
        line = entry.get("line", node.get("line", 0))
        receiver = entry.get("receiver")
        callee_root = _root(receiver) if receiver else _root(entry.get("name"))
        if callee_root and IDENTIFIER_RE.fullmatch(callee_root) and not is_known(callee_root, function):
            add(node, "undefined_call", line, f"call to undefined name '{callee_root}'",
                suggestion_for(callee_root, function, node["id"]))
        reads = []
        if receiver:
            root = _root(receiver)
            if IDENTIFIER_RE.fullmatch(root or ""):
                reads.append(root)
        for arg in entry.get("args") or []:
            root = _root(arg)
            if root and IDENTIFIER_RE.fullmatch(root) and root not in reads:
                reads.append(root)
        for name in reads:
            if is_known(name, function):
                continue
            where = f" in function '{function}'" if function else ""
            add(node, "undefined_name_read", line, f"read of undefined name '{name}'{where}",
                suggestion_for(name, function, node["id"]))

    for entry in categories.get("dataflow") or []:
        kind = entry.get("kind")
        if kind not in ("aug_assign", "annotation"):
            continue
        node = by_ref.get(entry.get("id"))
        name = entry.get("name")
        if not node or not name:
            continue
        function = entry.get("in_function")
        if is_known(name, function):
            continue
        where = f" in function '{function}'" if function else ""
        add(node, "undefined_name_read", entry.get("line", node.get("line", 0)),
            f"read of undefined name '{name}'{where}", suggestion_for(name, function, node["id"]))

    is_java = kb.get("language") == "java" or str(kb.get("file", "")).endswith(".java")
    for entry in categories.get("library_usage") or []:
        if is_java:
            break
        node = by_ref.get(entry.get("id"))
        if not node:
            continue
        module = entry.get("module") or ""
        top_level = module.split(".")[0]
        try:
            present = importlib.util.find_spec(top_level) is not None
        except Exception:
            present = True
        if present:
            continue
        add(node, "missing_module", entry.get("line", node.get("line", 0)),
            f"module '{module}' not found in the environment")

    for entry in categories.get("dataflow") or []:
        if entry.get("kind") not in RESOURCE_KINDS or entry.get("closed"):
            continue
        node = by_ref.get(entry.get("id"))
        if not node:
            continue
        add(node, "resource_leak", entry.get("line", node.get("line", 0)),
            f"resource '{entry.get('name')}' ({entry.get('op')}) is never closed")

    for entry in categories.get("configuration") or []:
        if entry.get("kind") != "env" or entry.get("has_default"):
            continue
        node = by_ref.get(entry.get("id"))
        if not node:
            continue
        add(node, "env_without_default", entry.get("line", node.get("line", 0)),
            f"environment variable '{entry.get('name')}' is read without a default")

    function_lines = sorted(node["line"] for node in nodes if node.get("kind") == "function")
    is_java = kb.get("language") == "java" or str(kb.get("file", "")).endswith(".java")
    source_lines = str(kb.get("source") or "").split("\n")
    dead_cache: dict[int, set[int]] = {}
    reported: set[int] = set()
    for entry in categories.get("control_flow") or []:
        if entry.get("kind") not in ("return", "raise"):
            continue
        node = by_ref.get(entry.get("id"))
        if not node:
            continue
        function = entry.get("in_function")
        stop_line = entry.get("line", 0)
        dead = dead_cache.get(stop_line)
        if dead is None:
            dead = _dead_lines_after(source_lines, int(stop_line), is_java)
            dead_cache[stop_line] = dead
        for other in nodes:
            if other.get("kind") == "function":
                continue
            line = other["line"]
            if line not in dead or line in reported:
                continue
            source = entry_by_ref.get(other.get("ref"))
            if source is None or source.get("in_function") != function:
                continue
            reported.add(line)
            add(other, "unreachable_code", line,
                f"unreachable statement after {entry.get('kind')} at line {stop_line}")

    if source_lines and source_lines != [""]:
        function_starts: dict[str, list[int]] = {}
        for node in nodes:
            if node.get("kind") == "function":
                label = str(node.get("label") or "")
                if label.startswith("def "):
                    function_starts.setdefault(label[4:].strip(), []).append(int(node.get("line") or 0))
        for line, rule, entry in _dead_store_scan(
                source_lines, categories.get("dataflow") or [], is_java, function_starts):
            node = by_ref.get(entry.get("id"))
            if node is None:
                continue
            name = entry.get("name")
            if rule == "dead_store":
                add(node, "dead_store", line,
                    f"value assigned to '{name}' is overwritten before it is read")
            else:
                add(node, "unused_local", line,
                    f"variable '{name}' is assigned but never read")

    for entry in categories.get("security") or []:
        node = by_ref.get(entry.get("id"))
        if not node:
            continue
        rule = entry.get("rule") or "security"
        detail = entry.get("detail") or rule
        add(node, rule, entry.get("line", node.get("line", 0)),
            f"security-sensitive construct: {detail}")

    # Structural graph check: a call node with no calls-edge to a defined
    # function node is a violation regardless of embedding similarity.
    # This replaces the broken Expectation-vs-Connected similarity check.
    call_targets = set()
    for edge in edges:
        if edge.get("kind") == "calls":
            call_targets.add(edge.get("to"))
    function_nodes = {node["id"] for node in nodes if node.get("kind") == "function"}

    embedded = {node_id: vec for node_id, vec in (embeddings or {}).items()
                if node_id in by_id and vec}
    if len(embedded) >= 3 and match_table is not None:
        # embedding-based context_mismatch: only fires when the node's Self
        # embedding is close to a KNOWN symbol's embedding but the node has
        # no structural edge to any function — i.e. the name is semantically
        # similar to something that exists but structurally disconnected
        neighbors = {node["id"]: set() for node in nodes}
        for edge in edges:
            source, target = edge.get("from"), edge.get("to")
            if source in neighbors and target in neighbors and source != target:
                neighbors[source].add(target)
                neighbors[target].add(source)
        for node_id in sorted(embedded):
            node = by_id[node_id]
            if node.get("kind") != "call":
                continue
            entry = entry_by_ref.get(node.get("ref")) or {}
            callee_root = _root(entry.get("name") or "")
            if not callee_root or is_known(callee_root, entry.get("in_function")):
                continue
            # the callee is structurally unbound; check if the match table
            # can find a close known symbol (this is the "find the most
            # similar node" step from Method.md's Violations section)
            nearby = sorted(neighbors.get(node_id, ()) & set(embedded))
            if len(nearby) < 2:
                continue
            max_sim = max(_cosine(embedded[node_id], embedded[nid]) for nid in nearby)
            if max_sim < threshold:
                add(node, "context_mismatch", node["line"],
                    f"call to '{callee_root}' has no matching definition (max neighbor cosine {max_sim:.3f} < {threshold})")

    found.sort(key=lambda record: (record["line"], record["rule"], record["node"], record["category"]))
    return [{"id": f"V{index:03d}", **record} for index, record in enumerate(found, start=1)]


def save_violations(violations: list[dict], path) -> None:
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(violations, handle, indent=2)


def load_violations(path) -> list[dict]:
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


if __name__ == "__main__":
    import hashlib
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from kappa.Constructor import build_graph
    from kappa.Parser import parse_file

    demo = Path(__file__).resolve().parent / "examples" / "demo_violations.py"
    kb = parse_file(demo)
    graph = build_graph(kb)

    def hash_vector(text, dim=64):
        counts = [0.0] * dim
        lowered = text.lower()
        for i in range(max(1, len(lowered) - 2)):
            gram = lowered[i:i + 3]
            bucket = int(hashlib.sha1(gram.encode("utf-8")).hexdigest(), 16) % dim
            counts[bucket] += 1.0
        norm = math.sqrt(sum(c * c for c in counts)) or 1.0
        return [c / norm for c in counts]

    embeddings = {node["id"]: hash_vector(node["self_text"]) for node in graph["nodes"]}
    violations = detect(kb, graph, embeddings)

    print(f"violations: {len(violations)}", flush=True)
    for violation in violations:
        extra = ""
        if "suggestion" in violation:
            sug = violation["suggestion"]
            extra = f" -> did you mean '{sug['closest']}' ({sug['score']})?"
        print(f"  {violation['id']} {violation['rule']:20s} L{violation['line']:<4d} "
              f"{violation['severity']:7s} {violation['message']}{extra}", flush=True)

    rules = {violation["rule"] for violation in violations}
    assert any(v["rule"] == "undefined_call"
               and (v.get("suggestion") or {}).get("current") == "sum_value"
               and v["suggestion"]["closest"] == "sum_values" for v in violations), \
        "expected undefined_call for sum_value with suggestion sum_values"
    assert any(v["rule"] == "resource_leak" and "handle" in v["message"] for v in violations), \
        "expected resource_leak for handle"
    assert any(v["rule"] == "env_without_default" and "DEBUG" in v["message"] for v in violations), \
        "expected env_without_default for DEBUG"
    assert "eval_use" in rules, "expected eval_use"
    done_nodes = [node for node in graph["nodes"]
                  if node["kind"] == "call" and "done" in node["label"]]
    assert done_nodes, "missing node for print(done)"
    assert any(v["rule"] == "unreachable_code" and v["line"] == done_nodes[0]["line"] for v in violations), \
        "expected unreachable_code for print(done)"
    assert "missing_module" not in rules, "os exists, missing_module must not fire"
    print("Detector smoke test OK", flush=True)
