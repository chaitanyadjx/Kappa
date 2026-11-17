"""Repo-scope name resolution via Jedi (industry-standard Python static analysis).

Wraps jedi.Project to answer: "where is this name defined across the repo?"
Used by the Kappa Knowledge DB to raise scope from module-level to repo level
(SPEC.md: "add information of file and symbols also, so the scope increases
from module-level to repo level").

Falls back to None when jedi is unavailable — the per-file AST walk in
Parser.build_context covers the basic case.
"""

from __future__ import annotations

from pathlib import Path


class RepoScope:
    def __init__(self, repo_dir):
        import jedi

        self.repo_dir = Path(repo_dir).resolve()
        self.project = jedi.Project(str(self.repo_dir))

    def where_defined(self, name: str) -> list[dict]:
        results = []
        try:
            for hit in self.project.search(name):
                if hit.name != name or hit.type not in ("function", "class", "statement"):
                    continue
                module_path = getattr(hit, "module_path", None)
                if module_path is None:
                    continue
                module_path = Path(str(module_path))
                try:
                    module = module_path.relative_to(self.repo_dir).with_suffix("").as_posix().replace("/", ".")
                except ValueError:
                    module = module_path.stem
                results.append({
                    "name": name,
                    "module": module,
                    "path": str(module_path),
                    "line": hit.line,
                    "kind": hit.type,
                })
        except Exception:
            pass
        return results

    def resolve_import(self, module_name: str) -> str | None:
        """Resolve a module name to its file path within the repo."""
        try:
            for hit in self.project.search(module_name):
                if hit.type == "module":
                    module_path = getattr(hit, "module_path", None)
                    if module_path:
                        return str(module_path)
        except Exception:
            pass
        return None
