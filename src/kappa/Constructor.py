"""Kappa Constructor: turns the Knowledge DB into the program graph (SPEC.md, Graph)."""

import json

RESOURCE_KINDS = {"file", "socket", "db"}
DEF_KINDS = {"assign", "aug_assign", "for_target", "parameter"} | RESOURCE_KINDS
BRANCH_KINDS = {"if", "elif", "else", "for", "while", "try", "except", "finally", "with"}
KIND_BY_CATEGORY = {
    "calls": "call",
    "library_usage": "import",
    "configuration": "config",
    "control_flow": "flow",
    "security": "security",
}
FLOW_LABELS = {"for": "loop for", "while": "loop while"}
DATAFLOW_LABELS = {
    "assign": "assignment",
    "aug_assign": "aug-assignment",
    "for_target": "for-target",
    "parameter": "parameter",
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
    return {name for name in provided if name}


def _entry_root(entry):
    for key in ("receiver", "root", "expr"):
        value = entry.get(key)
        if value:
            return _root(value)
    if entry.get("name"):
        return _root(entry["name"])
    return None


def _label(category, entry):
    if category == "calls":
        name = entry.get("name") or "?"
        receiver = entry.get("receiver")
        args = ", ".join(str(arg) for arg in entry.get("args") or [])
        full = f"{receiver}.{name}" if receiver else name
        return f"{full}({args})"
    if category == "library_usage":
        module = entry.get("module") or "?"
        names = entry.get("names") or []
        alias = entry.get("alias")
        if names:
            return f"from {module} import {', '.join(str(name) for name in names)}"
        if alias:
            return f"import {module} as {alias}"
        return f"import {module}"
    if category == "dataflow":
        kind = entry.get("kind") or "assign"
        if kind in RESOURCE_KINDS:
            return f"{entry.get('op') or kind} {entry.get('name') or '?'}"
        return f"{DATAFLOW_LABELS.get(kind, kind)} {entry.get('name') or '?'}"
    if category == "configuration":
        return f"{entry.get('kind') or 'config'} {entry.get('name') or '?'}"
    if category == "control_flow":
        return FLOW_LABELS.get(entry.get("kind"), entry.get("kind") or "flow")
    if category == "security":
        return entry.get("detail") or entry.get("rule") or "security"
    return f"def {entry.get('name') or '?'}"


def _expectation_text(category, entry, function):
    if category == "calls":
        name = entry.get("name") or "?"
        receiver = entry.get("receiver")
        full = f"{receiver}.{name}" if receiver else name
        return f"call {full} expects result usable as value; defined in this file or imported"
    if category == "library_usage":
        return f"import {entry.get('module')} provides names that dependent nodes may use"
    if category == "dataflow":
        kind = entry.get("kind") or "assign"
        name = entry.get("name") or "?"
        if kind in RESOURCE_KINDS:
            return f"{entry.get('op') or kind} {name} opens a resource that must be closed after use"
        if kind == "parameter":
            return f"parameter {name} provides an input value to {function}"
        if kind == "for_target":
            return f"loop target {name} binds each iteration value in {function}"
        if kind == "aug_assign":
            return f"aug-assignment updates {name} from its previous value in {function}"
        return f"assignment to {name} defines a value read later in {function}"
    if category == "configuration":
        return f"configuration {entry.get('kind')} {entry.get('name')} should be guaranteed or read with a default"
    if category == "control_flow":
        return f"{_label(category, entry)} statement directs control flow in {function}"
    if category == "security":
        return f"security-sensitive construct {entry.get('rule')} requires review"
    return f"function {entry.get('name')} contains the statements of its body"


def build_graph(kb: dict) -> dict:
    categories = kb.get("categories") or {}
    collected = []
    for category, items in categories.items():
        for entry in items or []:
            collected.append((category, entry))
    for symbol in (kb.get("symbols") or {}).get("defined") or []:
        if symbol.get("kind") == "function" and symbol.get("name"):
            collected.append(("symbols", {
                "id": f"function:L{symbol.get('line', 0)}",
                "line": symbol.get("line", 0),
                "name": symbol["name"],
            }))
    collected.sort(key=lambda item: (
        item[1].get("line", 0),
        0 if item[0] == "symbols" else item[1].get("col", 0),
        item[0],
        str(item[1].get("id", "")),
    ))

    nodes = []
    category_of = {}
    entry_of = {}
    function_of = {}
    for index, (category, entry) in enumerate(collected, start=1):
        node_id = f"node:{index:03d}"
        if category == "symbols":
            kind = "function"
        elif category == "dataflow":
            kind = "resource" if entry.get("kind") in RESOURCE_KINDS else "dataflow"
        else:
            kind = KIND_BY_CATEGORY.get(category, "dataflow")
        function = entry.get("in_function")
        label = _label(category, entry)
        nodes.append({
            "id": node_id,
            "kind": kind,
            "category": category,
            "line": entry.get("line", 0),
            "ref": entry.get("id"),
            "label": label,
            "expectation_text": _expectation_text(category, entry, function),
            "connected_text": "",
            "self_text": label,
        })
        category_of[node_id] = category
        entry_of[node_id] = entry
        function_of[node_id] = function

    by_id = {node["id"]: node for node in nodes}
    node_id_by_ref = {node["ref"]: node["id"] for node in nodes if node.get("ref")}
    edges = []
    seen_edges = set()

    def link(source, target, kind):
        key = (source, target, kind)
        if source != target and key not in seen_edges:
            seen_edges.add(key)
            edges.append({"from": source, "to": target, "kind": kind})

    function_node_by_name = {
        node["label"].split(" ", 1)[1]: node["id"]
        for node in nodes if node["kind"] == "function" and " " in node["label"]
    }
    for node_id, function in function_of.items():
        if function and function in function_node_by_name:
            link(function_node_by_name[function], node_id, "contains")

    reader_lines = {}
    def_lines = {}
    for node in nodes:
        node_id = node["id"]
        entry = entry_of[node_id]
        category = category_of[node_id]
        function = function_of[node_id]
        line = entry.get("line", 0)
        if category == "calls":
            names = set()
            if entry.get("receiver"):
                names.add(_root(entry["receiver"]))
            elif entry.get("name"):
                names.add(_root(entry["name"]))
            for arg in entry.get("args") or []:
                root = _root(arg)
                if root:
                    names.add(root)
            for name in names:
                reader_lines.setdefault((function, name), set()).add(line)
        elif category == "dataflow" and entry.get("name"):
            kind = entry.get("kind")
            name = entry["name"]
            if kind in DEF_KINDS:
                def_lines.setdefault((function, name), []).append(line)
            if kind == "aug_assign":
                reader_lines.setdefault((function, name), set()).add(line)
    for lines in def_lines.values():
        lines.sort()
    for (function, name), lines in def_lines.items():
        # a re-binding assignment reads the previous value (total = total + ...);
        # the KB stores no right-hand sides, so lines are the finest granularity
        for line in lines[1:]:
            reader_lines.setdefault((function, name), set()).add(line)

    for node in nodes:
        node_id = node["id"]
        entry = entry_of[node_id]
        category = category_of[node_id]
        function = function_of[node_id]
        line = entry.get("line", 0)
        if category == "dataflow" and entry.get("kind") in DEF_KINDS and entry.get("name"):
            name = entry["name"]
            for other in nodes:
                if other["line"] > line and function_of[other["id"]] == function \
                        and other["line"] in reader_lines.get((function, name), set()):
                    link(node_id, other["id"], "flows_to")
        if (category == "dataflow" and entry.get("kind") in RESOURCE_KINDS) or category == "configuration":
            for other in nodes:
                if category_of[other["id"]] == "control_flow" \
                        and entry_of[other["id"]].get("kind") == "return" \
                        and other["line"] > line and function_of[other["id"]] == function:
                    link(node_id, other["id"], "flows_to")
        if category == "library_usage":
            provided = _provided_names(entry)
            for other in nodes:
                if category_of[other["id"]] not in ("calls", "configuration"):
                    continue
                root = _entry_root(entry_of[other["id"]])
                if root and root in provided:
                    link(node_id, other["id"], "uses_import")

    branch_nodes = {}
    for node in nodes:
        if node["kind"] == "flow" and entry_of[node["id"]].get("kind") in BRANCH_KINDS:
            branch_nodes.setdefault(function_of[node["id"]], []).append((node["line"], node["id"]))
    for lines in branch_nodes.values():
        lines.sort()
    for node in nodes:
        if node["kind"] == "function":
            continue
        candidates = [(line, branch_id) for line, branch_id in branch_nodes.get(function_of[node["id"]], [])
                      if line < node["line"]]
        if candidates:
            # nearest branch/loop start above the statement; the KB carries no block extents
            link(node["id"], candidates[-1][1], "guarded_by")

    adjacent = {node["id"]: set() for node in nodes}
    for edge in edges:
        adjacent[edge["from"]].add(edge["to"])
        adjacent[edge["to"]].add(edge["from"])
    for node in nodes:
        function = function_of[node["id"]]
        base = f"inside function {function}" if function else "module level"
        connected = sorted(adjacent[node["id"]], key=lambda nid: (by_id[nid]["line"], by_id[nid]["label"]))
        listing = ", ".join(f"{by_id[nid]['label']} (L{by_id[nid]['line']})" for nid in connected)
        node["connected_text"] = f"{base}; connected to: {listing or 'nothing'}"

    return {"nodes": nodes, "edges": edges}


def save_graph(graph: dict, path) -> None:
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(graph, handle, indent=2)


def load_graph(path) -> dict:
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


if __name__ == "__main__":
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from kappa.Parser import parse_file

    demo = Path(__file__).resolve().parent / "examples" / "demo_violations.py"
    kb = parse_file(demo)
    graph = build_graph(kb)
    nodes, edges = graph["nodes"], graph["edges"]

    print(f"file: {kb.get('file')}", flush=True)
    print(f"nodes: {len(nodes)}", flush=True)
    print(f"edges: {len(edges)}", flush=True)

    kb_refs = {entry["id"] for items in kb["categories"].values() for entry in items}
    node_refs = {node["ref"] for node in nodes}
    missing = kb_refs - node_refs
    assert not missing, f"KB entries without a node: {sorted(missing)}"
    assert len(nodes) >= 10, f"expected at least 10 nodes, got {len(nodes)}"
    function_labels = {node["label"] for node in nodes if node["kind"] == "function"}
    for name in ("sum_values", "collect", "main"):
        assert f"def {name}" in function_labels, f"missing function node for {name}"

    print("sample nodes:", flush=True)
    for node in nodes[:5]:
        print(f"  {node['id']} {node['kind']:9s} L{node['line']:<3d} {node['label']}", flush=True)
    edge_kinds = {}
    for edge in edges:
        edge_kinds[edge["kind"]] = edge_kinds.get(edge["kind"], 0) + 1
    print(f"edge kinds: {edge_kinds}", flush=True)
    print("Constructor smoke test OK", flush=True)
