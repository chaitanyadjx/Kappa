"""Update loop for the Kappa pipeline (SPEC.md: Loop)."""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

_PACKAGE_PARENT = str(Path(__file__).resolve().parent.parent)
if _PACKAGE_PARENT not in sys.path:
    sys.path.insert(0, _PACKAGE_PARENT)

from kappa.Constructor import build_graph
from kappa.Detector import detect
from kappa.Embedding import Encoder
from kappa.Patcher import patch
from kappa.Parser import parse_source


def embed_graph(graph: dict, encoder) -> dict[str, list[float]]:
    nodes = graph.get("nodes", []) if isinstance(graph, dict) else []
    unique: list[str] = []
    index_of: dict[str, int] = {}
    refs: list[tuple[str, int]] = []
    for node in nodes:
        if not isinstance(node, dict):
            continue
        node_id = node.get("id")
        if not node_id:
            continue
        self_text = node.get("self_text") or node.get("label") or ""
        expectation_text = node.get("expectation_text") or ""
        connected_text = node.get("connected_text") or ""
        for key, text in (
            (str(node_id), self_text),
            (f"{node_id}:self", self_text),
            (f"{node_id}:expectation", expectation_text),
            (f"{node_id}:connected", connected_text),
        ):
            if not text:
                continue
            if text not in index_of:
                index_of[text] = len(unique)
                unique.append(text)
            refs.append((key, index_of[text]))
    if not unique:
        return {}
    vectors = encoder.embed(unique)
    return {key: vectors[index] for key, index in refs}


def _build_match_table(kb: dict, graph: dict, encoder, embeddings: dict) -> object | None:
    try:
        from kappa.NodeEmbeddingMatchTable import NodeEmbeddingMatchTable
    except Exception:
        return None
    try:
        table = NodeEmbeddingMatchTable()
        for node in graph.get("nodes", []) if isinstance(graph, dict) else []:
            if not isinstance(node, dict):
                continue
            node_id = str(node.get("id"))
            vector = embeddings.get(node_id)
            if not vector:
                continue
            vectors = {}
            for role in ("self", "expectation", "connected"):
                role_vector = embeddings.get(f"{node_id}:{role}")
                if role_vector:
                    vectors[role] = role_vector
            table.add(node_id, str(node.get("label") or ""), vector, vectors=vectors or None)
        defined = kb.get("symbols", {}).get("defined", []) if isinstance(kb, dict) else []
        names = [s.get("name") for s in defined if isinstance(s, dict) and s.get("name")]
        local_names = {
            str(entry.get("name"))
            for entry in (kb.get("categories", {}) or {}).get("dataflow", []) or []
            if isinstance(entry, dict) and entry.get("name") and entry.get("in_function")
        }
        extra_names = [name for name in dict.fromkeys(names + sorted(local_names)) if name]
        if extra_names:
            for name, vector in zip(extra_names, encoder.embed(extra_names)):
                table.add_symbol(name, vector)
        return table
    except Exception:
        return None


def _compiles(source: str, language: str | None = None) -> bool:
    if language == "java":
        return True
    try:
        ast.parse(source)
        return True
    except SyntaxError:
        return False


def update_loop(
    source: str,
    max_iters: int = 3,
    threshold: float = 0.2,
    min_confidence: float = 0.6,
    log=print,
    parser_backend: str | None = None,
    context: dict | None = None,
    language: str | None = None,
) -> dict:
    log = log or (lambda _message: None)
    current = str(source)
    encoder = Encoder()
    iterations: list[dict] = []
    scanned: tuple[str, list[dict]] | None = None
    introduced: set[str] = set()
    for number in range(1, max(0, int(max_iters)) + 1):
        kb = parse_source(current, backend=parser_backend, language=language)
        if context:
            kb["context"] = context
        graph = build_graph(kb)
        embeddings = embed_graph(graph, encoder)
        table = _build_match_table(kb, graph, encoder, embeddings)
        violations = detect(kb, graph, embeddings, threshold=threshold, match_table=table,
                            introduced=introduced)
        scanned = (current, violations)
        if not violations:
            iterations.append({"iteration": number, "violations": 0, "patched": 0, "unresolved": 0})
            log(f"[kappa] iteration {number}: no violations, source is clean")
            break
        result = patch(
            current,
            kb,
            graph,
            violations,
            match_table=table,
            min_confidence=min_confidence,
            is_java=(language == "java"),
        )
        patches = result.get("patches") or []
        unresolved = result.get("unresolved") or []
        iterations.append(
            {
                "iteration": number,
                "violations": len(violations),
                "patched": len(patches),
                "unresolved": len(unresolved),
            }
        )
        log(
            f"[kappa] iteration {number}: {len(violations)} violations, "
            f"{len(patches)} patches applied, {len(unresolved)} unresolved"
        )
        if not patches:
            log("[kappa] no applicable patches, stopping")
            break
        new_source = result.get("new_source")
        if new_source is None or new_source == current:
            log("[kappa] source unchanged, stopping")
            break
        if not _compiles(new_source, language):
            log("[kappa] patched source does not compile, keeping previous source")
            break
        for patch_record in patches:
            if patch_record.get("kind") == "insert_import":
                statement = str(patch_record.get("statement") or f"import {patch_record.get('module', '')}")
                for token in statement.replace("import ", "").replace("from ", "").split():
                    introduced.add(token.strip(","))
                continue
            new_value = str(patch_record.get("new") or "")
            if new_value and re.fullmatch(r"[A-Za-z_]\w*(\.[A-Za-z_]\w*)*", new_value):
                introduced.add(new_value.split(".")[0])
        current = new_source
    final = current
    if scanned is not None and scanned[0] == final:
        remaining = scanned[1]
    else:
        kb = parse_source(final, backend=parser_backend, language=language)
        if context:
            kb["context"] = context
        graph = build_graph(kb)
        final_embeddings = embed_graph(graph, encoder)
        final_table = _build_match_table(kb, graph, encoder, final_embeddings)
        remaining = detect(kb, graph, final_embeddings, threshold=threshold, match_table=final_table,
                           introduced=introduced)
    return {
        "iterations": iterations,
        "final_source": final,
        "remaining_violations": remaining,
        "introduced": sorted(introduced),
        "iters_run": len(iterations),
    }


if __name__ == "__main__":
    demo = Path(__file__).resolve().parent / "examples" / "demo_violations.py"
    source = demo.read_text(encoding="utf-8")
    result = update_loop(
        source, max_iters=3, threshold=0.2, min_confidence=0.6,
        log=lambda message: print(message, flush=True),
    )
    for entry in result["iterations"]:
        print(
            f"iteration {entry['iteration']}: violations={entry['violations']} "
            f"patched={entry['patched']} unresolved={entry['unresolved']}",
            flush=True,
        )
    rules = sorted({v.get("rule") for v in result["remaining_violations"]})
    assert "undefined_call" not in rules, f"undefined_call still present: {rules}"
    print(
        "remaining violations:",
        [(v.get("rule"), v.get("line")) for v in result["remaining_violations"]],
        flush=True,
    )
    print(f"iters_run: {result['iters_run']}", flush=True)
    print("[kappa] Updater smoke test passed", flush=True)
