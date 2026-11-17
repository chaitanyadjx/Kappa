import ast
import hashlib
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

CATEGORIES = ["calls", "library_usage", "dataflow", "configuration", "control_flow", "security"]

_RESOURCE_OPS = {
    "open": "file",
    "urlopen": "file",
    "socket": "socket",
    "create_connection": "socket",
    "connect": "db",
    "session": "db",
    "client": "db",
    "connection": "db",
    "cursor": "db",
}
_CONFIG_ROOTS = {"config", "settings", "conf", "cfg"}
_SECRET_NAME = re.compile(r"(?i)(password|secret|token|api_key)\b")
_SUBPROCESS_FUNCS = {"run", "call", "check_call", "check_output", "Popen", "getoutput", "getstatusoutput"}
_SCOPE_BARRIERS = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)

_JAVA_RESOURCE_KINDS = {
    "file": "file",
    "fileinputstream": "file",
    "fileoutputstream": "file",
    "filereader": "file",
    "filewriter": "file",
    "bufferedreader": "file",
    "bufferedwriter": "file",
    "printwriter": "file",
    "printstream": "file",
    "scanner": "file",
    "inputstreamreader": "file",
    "randomaccessfile": "file",
    "connection": "db",
    "statement": "db",
    "preparedstatement": "db",
    "resultset": "db",
    "socket": "socket",
    "serversocket": "socket",
    "httpurlconnection": "socket",
}
_JAVA_ASSIGN_OPS = {"=", "+=", "-=", "*=", "/=", "%=", "&=", "|=", "^=", "<<=", ">>=", ">>>="}


def _dotted(node):
    parts = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
        return ".".join(reversed(parts))
    return None


def _root_of(dotted):
    return dotted.split(".")[0] if dotted else None


def _store_names(node):
    if isinstance(node, ast.Name):
        yield node
    elif isinstance(node, ast.Starred):
        yield from _store_names(node.value)
    elif isinstance(node, (ast.Tuple, ast.List)):
        for elt in node.elts:
            yield from _store_names(elt)


def _all_params(args):
    params = list(args.posonlyargs) + list(args.args) + list(args.kwonlyargs)
    if args.vararg:
        params.append(args.vararg)
    if args.kwarg:
        params.append(args.kwarg)
    return params


def _scope_nodes(stmts):
    out = []

    def rec(node):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, _SCOPE_BARRIERS):
                continue
            out.append(child)
            rec(child)

    for stmt in stmts:
        if isinstance(stmt, _SCOPE_BARRIERS):
            continue
        out.append(stmt)
        rec(stmt)
    return out


def _stores_below(node):
    for child in ast.iter_child_nodes(node):
        if isinstance(child, _SCOPE_BARRIERS):
            continue
        if isinstance(child, ast.Name) and isinstance(child.ctx, ast.Store):
            yield child
        yield from _stores_below(child)


class _KBCore:
    def __init__(self):
        self.symbols = []
        self._symbol_names = set()
        self.categories = {name: [] for name in CATEGORIES}
        self._seq = 0

    def _add(self, category, prefix, line, col, fields):
        entry = {"_prefix": prefix, "_col": col, "_seq": self._seq, "line": line}
        entry.update(fields)
        self._seq += 1
        self.categories[category].append(entry)
        return entry

    def _add_symbol(self, name, kind, line):
        if name in self._symbol_names:
            return
        self._symbol_names.add(name)
        self.symbols.append({"name": name, "kind": kind, "line": line})

    def finalize(self):
        categories = {}
        for category in CATEGORIES:
            entries = sorted(self.categories[category], key=lambda e: (e["line"], e["_col"], e["_seq"]))
            used = set()
            out = []
            for e in entries:
                prefix, line, col = e["_prefix"], e["line"], e["_col"]
                base = f"{prefix}:L{line}"
                if prefix == "calls":
                    base = f"{base}:C{col}"
                if base in used:
                    # same line can host several entries; keep ids unique but deterministic
                    alt = f"{base}:C{col}" if col is not None else None
                    if alt is not None and alt not in used:
                        base = alt
                    else:
                        n = 2
                        while f"{base}:{n}" in used:
                            n += 1
                        base = f"{base}:{n}"
                used.add(base)
                entry = {"id": base}
                for key, value in e.items():
                    if not key.startswith("_"):
                        entry[key] = value
                out.append(entry)
            categories[category] = out
        return categories


class _Analyzer(_KBCore):
    def __init__(self):
        super().__init__()
        self._pending = []
        self._imported_modules = set()
        self._imported_from = {}

    def collect_imports(self, tree):
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    root = alias.name.split(".")[0]
                    self._imported_modules.add(root)
                    if alias.asname:
                        self._imported_from[alias.asname] = root
            elif isinstance(node, ast.ImportFrom):
                if node.module and node.level == 0:
                    root = node.module.split(".")[0]
                    self._imported_modules.add(root)
                    for alias in node.names:
                        self._imported_from[alias.asname or alias.name] = root

    def run(self, tree):
        for stmt in tree.body:
            if isinstance(stmt, _SCOPE_BARRIERS):
                continue
            for name_node in _stores_below(stmt):
                self._add_symbol(name_node.id, "variable", name_node.lineno)
        mark = len(self._pending)
        for stmt in tree.body:
            self._stmt(stmt, None)
        self._close_pending(mark, tree.body)

    def _stmt(self, stmt, func):
        if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
            self._function(stmt, func)
        elif isinstance(stmt, ast.ClassDef):
            self._add_symbol(stmt.name, "class", stmt.lineno)
            for dec in stmt.decorator_list:
                self._expr(dec, func)
            for base in stmt.bases:
                self._expr(base, func)
            for kw in stmt.keywords:
                self._expr(kw.value, func)
            for child in stmt.body:
                self._stmt(child, func)
        elif isinstance(stmt, ast.Assign):
            self._assign(stmt.targets, stmt.value, func)
        elif isinstance(stmt, ast.AnnAssign):
            if stmt.value is not None:
                self._assign([stmt.target], stmt.value, func)
            self._expr(stmt.annotation, func)
        elif isinstance(stmt, ast.AugAssign):
            if isinstance(stmt.target, ast.Name):
                self._add("dataflow", "dataflow", stmt.target.lineno, stmt.target.col_offset + 1,
                          {"kind": "aug", "name": stmt.target.id, "in_function": func})
            self._expr(stmt.value, func)
        elif isinstance(stmt, (ast.For, ast.AsyncFor)):
            self._add("control_flow", "flow", stmt.lineno, stmt.col_offset + 1,
                      {"kind": "for", "in_function": func})
            for name_node in _store_names(stmt.target):
                self._add("dataflow", "dataflow", name_node.lineno, name_node.col_offset + 1,
                          {"kind": "for", "name": name_node.id, "in_function": func})
            self._expr(stmt.iter, func)
            for child in stmt.body:
                self._stmt(child, func)
            self._orelse_block(stmt.orelse, func)
        elif isinstance(stmt, ast.While):
            self._add("control_flow", "flow", stmt.lineno, stmt.col_offset + 1,
                      {"kind": "while", "in_function": func})
            self._expr(stmt.test, func)
            for child in stmt.body:
                self._stmt(child, func)
            self._orelse_block(stmt.orelse, func)
        elif isinstance(stmt, ast.If):
            self._branch(stmt, func, "if")
        elif isinstance(stmt, (ast.Try, ast.TryStar)):
            self._try(stmt, func)
        elif isinstance(stmt, (ast.With, ast.AsyncWith)):
            for item in stmt.items:
                self._with_item(item, func)
            for child in stmt.body:
                self._stmt(child, func)
        elif isinstance(stmt, ast.Match):
            self._expr(stmt.subject, func)
            for case in stmt.cases:
                if case.guard is not None:
                    self._expr(case.guard, func)
                for child in case.body:
                    self._stmt(child, func)
        elif isinstance(stmt, ast.Return):
            self._add("control_flow", "flow", stmt.lineno, stmt.col_offset + 1,
                      {"kind": "return", "in_function": func})
            self._expr(stmt.value, func)
        elif isinstance(stmt, ast.Raise):
            self._add("control_flow", "flow", stmt.lineno, stmt.col_offset + 1,
                      {"kind": "raise", "in_function": func})
            self._expr(stmt.exc, func)
            self._expr(stmt.cause, func)
        elif isinstance(stmt, (ast.Import, ast.ImportFrom)):
            self._import(stmt)
        elif isinstance(stmt, ast.Expr):
            self._expr(stmt.value, func)
        else:
            for child in ast.iter_child_nodes(stmt):
                if isinstance(child, ast.stmt):
                    self._stmt(child, func)
                elif isinstance(child, ast.expr):
                    self._expr(child, func)

    def _orelse_block(self, orelse, func):
        if not orelse:
            return
        self._add("control_flow", "flow", orelse[0].lineno, orelse[0].col_offset + 1,
                  {"kind": "else", "in_function": func})
        for child in orelse:
            self._stmt(child, func)

    def _function(self, stmt, func):
        self._add_symbol(stmt.name, "function", stmt.lineno)
        for dec in stmt.decorator_list:
            self._expr(dec, func)
        for default in list(stmt.args.defaults) + [d for d in stmt.args.kw_defaults if d is not None]:
            self._expr(default, func)
        for param in _all_params(stmt.args):
            self._add("dataflow", "dataflow", param.lineno, param.col_offset + 1,
                      {"kind": "param", "name": param.arg, "in_function": stmt.name})
            if param.annotation is not None:
                self._annotation(param.annotation, func)
        if stmt.returns is not None:
            self._annotation(stmt.returns, func)
        mark = len(self._pending)
        for child in stmt.body:
            self._stmt(child, stmt.name)
        self._close_pending(mark, stmt.body)

    def _annotation(self, node, func):
        if node is None:
            return
        if isinstance(node, ast.Name):
            self._add("dataflow", "dataflow", node.lineno, node.col_offset + 1,
                      {"kind": "annotation", "name": node.id, "in_function": func})
        elif isinstance(node, ast.Attribute):
            self._expr(node, func)
        elif isinstance(node, (ast.Subscript, ast.BinOp, ast.Constant, ast.String)):
            for child in ast.walk(node):
                if isinstance(child, ast.Name):
                    self._add("dataflow", "dataflow", child.lineno, child.col_offset + 1,
                              {"kind": "annotation", "name": child.id, "in_function": func})

    def _branch(self, stmt, func, kind):
        self._add("control_flow", "flow", stmt.lineno, stmt.col_offset + 1,
                  {"kind": kind, "in_function": func})
        self._expr(stmt.test, func)
        for child in stmt.body:
            self._stmt(child, func)
        orelse = stmt.orelse
        # an elif is a nested If in orelse; unroll the chain without re-emitting "if"
        while len(orelse) == 1 and isinstance(orelse[0], ast.If):
            elif_stmt = orelse[0]
            self._add("control_flow", "flow", elif_stmt.lineno, elif_stmt.col_offset + 1,
                      {"kind": "elif", "in_function": func})
            self._expr(elif_stmt.test, func)
            for child in elif_stmt.body:
                self._stmt(child, func)
            orelse = elif_stmt.orelse
        self._orelse_block(orelse, func)

    def _try(self, stmt, func):
        self._add("control_flow", "flow", stmt.lineno, stmt.col_offset + 1,
                  {"kind": "try", "in_function": func})
        for child in stmt.body:
            self._stmt(child, func)
        for handler in stmt.handlers:
            self._add("control_flow", "flow", handler.lineno, handler.col_offset + 1,
                      {"kind": "except", "in_function": func})
            if handler.type is not None:
                self._expr(handler.type, func)
            for child in handler.body:
                self._stmt(child, func)
        self._orelse_block(stmt.orelse, func)
        if stmt.finalbody:
            self._add("control_flow", "flow", stmt.finalbody[0].lineno, stmt.finalbody[0].col_offset + 1,
                      {"kind": "finally", "in_function": func})
            for child in stmt.finalbody:
                self._stmt(child, func)

    def _import(self, stmt):
        if isinstance(stmt, ast.Import):
            for alias in stmt.names:
                self._add("library_usage", "import", alias.lineno, alias.col_offset + 1,
                          {"module": alias.name, "names": [], "alias": alias.asname})
        else:
            module = ("." * stmt.level) + (stmt.module or "")
            names = [alias.name for alias in stmt.names]
            alias = stmt.names[0].asname if len(stmt.names) == 1 else None
            self._add("library_usage", "import", stmt.lineno, stmt.col_offset + 1,
                      {"module": module, "names": names, "alias": alias})

    def _assign(self, targets, value, func):
        for target in targets:
            if isinstance(target, ast.Name) and isinstance(value, ast.Constant) and _SECRET_NAME.search(target.id):
                self._add("security", "security", value.lineno, value.col_offset + 1,
                          {"rule": "hardcoded_secret", "detail": f"literal assigned to '{target.id}'"})
        resource = self._resource_call(value)
        if resource is not None and len(targets) == 1 and isinstance(targets[0], ast.Name):
            op, kind = resource
            entry = self._add("dataflow", "resource", value.lineno, value.col_offset + 1,
                              {"kind": kind, "op": op, "name": targets[0].id, "closed": False})
            self._pending.append((targets[0].id, entry))
        else:
            for target in targets:
                for name_node in _store_names(target):
                    self._add("dataflow", "dataflow", name_node.lineno, name_node.col_offset + 1,
                              {"kind": "assign", "name": name_node.id, "in_function": func})
        self._expr(value, func)

    def _resource_call(self, expr):
        if not isinstance(expr, ast.Call):
            return None
        dotted = _dotted(expr.func)
        if dotted is None:
            return None
        op = dotted.rsplit(".", 1)[-1]
        kind = _RESOURCE_OPS.get(op.lower())
        if kind is None:
            return None
        return op, kind

    def _with_item(self, item, func):
        resource = self._resource_call(item.context_expr)
        if resource is not None:
            op, kind = resource
            name = item.optional_vars.id if isinstance(item.optional_vars, ast.Name) else None
            self._add("dataflow", "resource", item.context_expr.lineno, item.context_expr.col_offset + 1,
                      {"kind": kind, "op": op, "name": name, "closed": True})
        self._expr(item.context_expr, func)

    def _close_pending(self, mark, scope_stmts):
        pending = self._pending[mark:]
        del self._pending[mark:]
        if not pending:
            return
        close_names, with_names = set(), set()
        for node in _scope_nodes(scope_stmts):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "close":
                dotted = _dotted(node.func.value)
                if dotted is not None:
                    close_names.add(dotted)
            elif isinstance(node, (ast.With, ast.AsyncWith)):
                for item in node.items:
                    if isinstance(item.context_expr, ast.Name):
                        with_names.add(item.context_expr.id)
                    if isinstance(item.optional_vars, ast.Name):
                        with_names.add(item.optional_vars.id)
        for name, entry in pending:
            if name is not None and (name in close_names or name in with_names):
                entry["closed"] = True

    def _expr(self, node, func, call_func=False):
        if node is None or isinstance(node, (ast.Name, ast.Constant)):
            return
        if isinstance(node, ast.Call):
            self._call(node, func)
        elif isinstance(node, ast.NamedExpr):
            self._walrus(node, func)
        elif isinstance(node, ast.Subscript):
            self._env_subscript(node)
            self._expr(node.value, func)
            self._expr(node.slice, func)
        elif isinstance(node, ast.Attribute):
            self._attr_read(node, call_func)
            self._expr(node.value, func)
        elif isinstance(node, ast.Lambda):
            for default in node.args.defaults:
                self._expr(default, func)
            self._expr(node.body, func)
        elif isinstance(node, (ast.ListComp, ast.SetComp, ast.GeneratorExp, ast.DictComp)):
            if isinstance(node, ast.DictComp):
                self._expr(node.key, func)
                self._expr(node.value, func)
            else:
                self._expr(node.elt, func)
            for gen in node.generators:
                for name_node in _store_names(gen.target):
                    self._add("dataflow", "dataflow", name_node.lineno, name_node.col_offset + 1,
                              {"kind": "for", "name": name_node.id, "in_function": func})
                self._expr(gen.iter, func)
                for cond in gen.ifs:
                    self._expr(cond, func)
        else:
            for child in ast.iter_child_nodes(node):
                if isinstance(child, ast.expr):
                    self._expr(child, func)

    def _walrus(self, node, func):
        resource = self._resource_call(node.value)
        if resource is not None and isinstance(node.target, ast.Name):
            op, kind = resource
            entry = self._add("dataflow", "resource", node.value.lineno, node.value.col_offset + 1,
                              {"kind": kind, "op": op, "name": node.target.id, "closed": False})
            self._pending.append((node.target.id, entry))
        else:
            self._add("dataflow", "dataflow", node.target.lineno, node.target.col_offset + 1,
                      {"kind": "assign", "name": node.target.id, "in_function": func})
        self._expr(node.value, func)

    def _call(self, node, func):
        dotted = _dotted(node.func)
        root = _root_of(dotted)
        if isinstance(node.func, ast.Name):
            name, receiver = node.func.id, None
        elif isinstance(node.func, ast.Attribute):
            name = node.func.attr
            receiver = dotted[: -(len(name) + 1)] if dotted and dotted != name else None
        else:
            name, receiver = ast.unparse(node.func), None
        args = [ast.unparse(arg) for arg in node.args]
        for kw in node.keywords:
            args.append(ast.unparse(kw) if kw.arg is None else f"{kw.arg}={ast.unparse(kw.value)}")
        self._add("calls", "calls", node.lineno, node.col_offset + 1,
                  {"col": node.col_offset + 1, "name": name, "receiver": receiver,
                   "args": args, "in_function": func})
        self._security_call(node, name, dotted, root)
        self._env_call(node)
        self._expr(node.func, func, call_func=True)
        for arg in node.args:
            self._expr(arg, func)
        for kw in node.keywords:
            self._expr(kw.value, func)

    def _security_call(self, node, name, dotted, root):
        line, col = node.lineno, node.col_offset + 1
        if name in ("eval", "exec") and (dotted is None or dotted == name or root == "builtins"):
            self._add("security", "security", line, col,
                      {"rule": f"{name}_use", "detail": f"{name}() call"})
        elif name in ("loads", "dumps") and (
            (dotted is not None and root == "pickle")
            or (dotted is None and self._imported_from.get(name) == "pickle")
        ):
            detail = f"{dotted}() call" if dotted else f"pickle.{name}() call"
            self._add("security", "security", line, col, {"rule": "pickle_use", "detail": detail})
        else:
            shell = any(kw.arg == "shell" and isinstance(kw.value, ast.Constant) and kw.value.value is True
                        for kw in node.keywords)
            if shell and ((dotted is not None and root == "subprocess")
                          or (dotted is None and name in _SUBPROCESS_FUNCS
                              and "subprocess" in self._imported_modules)):
                self._add("security", "security", line, col,
                          {"rule": "shell_true", "detail": "subprocess call with shell=True"})

    def _env_call(self, node):
        func = node.func
        is_environ_get = (isinstance(func, ast.Attribute) and func.attr == "get"
                          and isinstance(func.value, ast.Attribute) and func.value.attr == "environ"
                          and isinstance(func.value.value, ast.Name) and func.value.value.id == "os")
        is_getenv = (isinstance(func, ast.Attribute) and func.attr == "getenv"
                     and isinstance(func.value, ast.Name) and func.value.id == "os")
        is_bare_getenv = (isinstance(func, ast.Name) and func.id == "getenv"
                          and self._imported_from.get("getenv") == "os")
        if not (is_environ_get or is_getenv or is_bare_getenv):
            return
        self._add("configuration", "config", node.lineno, node.col_offset + 1,
                  {"kind": "env", "name": self._env_name(node),
                   "has_default": len(node.args) >= 2 or any(kw.arg == "default" for kw in node.keywords)})

    @staticmethod
    def _env_name(node):
        if not node.args:
            return "<unknown>"
        first = node.args[0]
        if isinstance(first, ast.Constant) and isinstance(first.value, str):
            return first.value
        return ast.unparse(first)

    def _env_subscript(self, node):
        value = node.value
        if not (isinstance(node.ctx, ast.Load) and isinstance(value, ast.Attribute)
                and value.attr == "environ" and isinstance(value.value, ast.Name)
                and value.value.id == "os"):
            return
        sl = node.slice
        name = sl.value if isinstance(sl, ast.Constant) and isinstance(sl.value, str) else ast.unparse(sl)
        self._add("configuration", "config", node.lineno, node.col_offset + 1,
                  {"kind": "env", "name": name, "has_default": False})

    def _attr_read(self, node, call_func):
        if call_func or not isinstance(node.ctx, ast.Load):
            return
        if isinstance(node.value, ast.Attribute) and isinstance(node.value.ctx, ast.Load):
            return
        dotted = _dotted(node)
        if dotted is not None and _root_of(dotted) in _CONFIG_ROOTS:
            self._add("configuration", "config", node.lineno, node.col_offset + 1,
                      {"kind": "attr", "name": node.attr, "has_default": False})


class _TSCore(_KBCore):
    def __init__(self, source_bytes):
        super().__init__()
        self._src = source_bytes

    def _text(self, node):
        return self._src[node.start_byte:node.end_byte].decode("utf-8", "replace")

    def _line_col(self, node):
        return node.start_point[0] + 1, node.start_point[1] + 1

    def _add_parse_errors(self, root):
        for region in self._error_regions(root):
            line, col = self._line_col(region)
            snippet = self._text(region).strip().replace("\n", " ")
            if len(snippet) > 60:
                snippet = snippet[:60] + "..."
            detail = f"unparsed region: {snippet!r}" if snippet else f"syntax error at line {line}"
            self._add("control_flow", "flow", line, col,
                      {"kind": "parse_error", "in_function": None, "detail": detail})

    def _error_regions(self, root):
        regions = []

        def collect(node):
            for child in node.children:
                if child.type == "ERROR" or getattr(child, "is_missing", False):
                    regions.append(child)
                else:
                    collect(child)

        collect(root)
        if not regions and root.has_error:
            # grammars can mark errors only via missing tokens / has_error flags
            stack = [root]
            while stack:
                node = stack.pop()
                err_children = [c for c in node.children
                                if c.has_error and c.type != "ERROR" and not getattr(c, "is_missing", False)]
                if err_children:
                    stack.extend(err_children)
                elif node is not root:
                    regions.append(node)
        regions.sort(key=lambda n: (n.start_point, n.start_byte))
        return regions


_TS_PY_LEAVES = {"comment", "identifier", "string_start", "string_content", "string_end",
                 "integer", "float", "true", "false", "none"}


class _TSPython(_TSCore):
    def __init__(self, source_bytes):
        super().__init__(source_bytes)
        self._pending = []
        self._imported_modules = set()
        self._imported_from = {}

    def run(self, root):
        for stmt in root.named_children:
            if stmt.type == "ERROR":
                continue
            for name_node in self._store_identifier_nodes(stmt):
                self._add_symbol(self._text(name_node), "variable", name_node.start_point[0] + 1)
        mark = len(self._pending)
        for stmt in root.named_children:
            self._walk(stmt, None)
        self._close_pending(mark, [root])

    def _walk(self, node, func, call_func=False):
        if node is None:
            return
        t = node.type
        if t in _TS_PY_LEAVES or t == "ERROR":
            return
        if t == "function_definition":
            self._function(node, func)
        elif t == "class_definition":
            self._class(node, func)
        elif t == "import_statement":
            self._import(node)
        elif t == "import_from_statement":
            self._import_from(node)
        elif t == "assignment":
            self._assign(node, func)
        elif t == "augmented_assignment":
            for name_node in self._store_nodes(node.child_by_field_name("left")):
                self._add("dataflow", "dataflow", *self._line_col(name_node),
                          {"kind": "aug", "name": self._text(name_node), "in_function": func})
            self._walk(node.child_by_field_name("right"), func)
        elif t == "named_expression":
            self._walrus(node, func)
        elif t == "for_statement":
            self._for(node, func)
        elif t == "while_statement":
            self._flow(node, "while", func)
            self._walk(node.child_by_field_name("condition"), func)
            self._block(node.child_by_field_name("body"), func)
            self._else_of(node.child_by_field_name("alternative"), func)
        elif t == "if_statement":
            self._branch(node, func)
        elif t == "try_statement":
            self._try(node, func)
        elif t == "with_statement":
            self._with(node, func)
        elif t == "return_statement":
            self._flow(node, "return", func)
            for child in node.named_children:
                self._walk(child, func)
        elif t == "raise_statement":
            self._flow(node, "raise", func)
            for child in node.named_children:
                self._walk(child, func)
        elif t == "match_statement":
            self._walk(node.child_by_field_name("subject"), func)
            for case in node.named_children:
                if case.type == "case":
                    for child in case.named_children:
                        if child.type == "block":
                            self._block(child, func)
                        else:
                            self._walk(child, func)
        elif t == "call":
            self._call(node, func)
        elif t == "subscript":
            self._env_subscript(node)
            self._walk(node.child_by_field_name("value"), func)
            self._walk(node.child_by_field_name("subscript"), func)
        elif t == "attribute":
            self._attr_read(node, call_func)
            self._walk(node.child_by_field_name("object"), func)
        elif t == "lambda":
            params = node.child_by_field_name("parameters")
            if params is not None:
                for p in params.named_children:
                    value = p.child_by_field_name("value") if p.type.endswith("parameter") else None
                    if value is not None:
                        self._walk(value, func)
            self._walk(node.child_by_field_name("body"), func)
        elif t == "string":
            for child in node.named_children:
                if child.type == "interpolation":
                    self._walk(child, func)
        elif t in ("list_comprehension", "set_comprehension", "generator_expression", "dict_comprehension"):
            for child in node.named_children:
                if child.type == "for_in_clause":
                    self._for_in_clause(child, func)
                elif child.type == "if_clause":
                    self._walk(child.child_by_field_name("condition"), func)
                else:
                    self._walk(child, func)
        elif t == "for_in_clause":
            self._for_in_clause(node, func)
        else:
            for child in node.named_children:
                self._walk(child, func)

    def _flow(self, node, kind, func):
        self._add("control_flow", "flow", *self._line_col(node), {"kind": kind, "in_function": func})

    def _block(self, block, func):
        if block is None:
            return
        for child in block.named_children:
            self._walk(child, func)

    def _else_of(self, alternative, func):
        if alternative is None:
            return
        if alternative.type == "elif_clause":
            self._flow(alternative, "elif", func)
            self._walk(alternative.child_by_field_name("condition"), func)
            self._block(alternative.child_by_field_name("consequence"), func)
            self._else_of(alternative.child_by_field_name("alternative"), func)
        elif alternative.type == "else_clause":
            body = alternative.child_by_field_name("body")
            anchor = body if body is not None else alternative
            self._add("control_flow", "flow", *self._line_col(anchor), {"kind": "else", "in_function": func})
            self._block(body, func)
        else:
            self._walk(alternative, func)

    def _for(self, node, func):
        self._flow(node, "for", func)
        for name_node in self._store_nodes(node.child_by_field_name("left")):
            self._add("dataflow", "dataflow", *self._line_col(name_node),
                      {"kind": "for", "name": self._text(name_node), "in_function": func})
        self._walk(node.child_by_field_name("right"), func)
        self._block(node.child_by_field_name("body"), func)
        self._else_of(node.child_by_field_name("alternative"), func)

    def _for_in_clause(self, node, func):
        for name_node in self._store_nodes(node.child_by_field_name("left")):
            self._add("dataflow", "dataflow", *self._line_col(name_node),
                      {"kind": "for", "name": self._text(name_node), "in_function": func})
        self._walk(node.child_by_field_name("right"), func)
        for child in node.named_children:
            if child.type == "if_clause":
                self._walk(child.child_by_field_name("condition"), func)

    def _branch(self, node, func):
        self._flow(node, "if", func)
        self._walk(node.child_by_field_name("condition"), func)
        self._block(node.child_by_field_name("consequence"), func)
        self._else_of(node.child_by_field_name("alternative"), func)

    def _try(self, node, func):
        self._flow(node, "try", func)
        self._block(node.child_by_field_name("body"), func)
        for child in node.named_children:
            if child.type == "except_clause":
                self._flow(child, "except", func)
                for c in child.named_children:
                    if c.type == "block":
                        self._block(c, func)
                    else:
                        self._walk(c, func)
            elif child.type == "else_clause":
                self._else_of(child, func)
            elif child.type == "finally_clause":
                body = child.child_by_field_name("body")
                anchor = body if body is not None else child
                self._add("control_flow", "flow", *self._line_col(anchor), {"kind": "finally", "in_function": func})
                self._block(body, func)

    def _with(self, node, func):
        mark = len(self._pending)
        witems = []
        for child in node.named_children:
            if child.type == "with_item":
                witems.append(child)
            elif child.type == "with_clause":
                witems.extend(c for c in child.named_children if c.type == "with_item")
        for item in witems:
            value, target = self._with_item_parts(item)
            resource = self._resource_call(value)
            if resource is not None:
                op, kind = resource
                self._add("dataflow", "resource", *self._line_col(value),
                          {"kind": kind, "op": op, "name": target, "closed": True})
            self._walk(value, func)
        self._block(node.child_by_field_name("body"), func)
        self._close_pending(mark, [node.child_by_field_name("body")])

    def _with_item_parts(self, item):
        value, target = None, None
        for child in item.named_children:
            if child.type == "as_pattern":
                for c in child.named_children:
                    if c.type == "as_pattern_target":
                        names = self._collect_stores(c)
                        target = self._text(names[0]) if names else None
                    elif value is None:
                        value = c
            elif value is None:
                value = child
        return value, target

    @staticmethod
    def _collect_stores(node):
        if node is None:
            return []
        t = node.type
        if t == "identifier":
            return [node]
        if t in ("pattern_list", "tuple_pattern", "list_pattern", "as_pattern_target",
                 "list_splat_pattern", "dictionary_splat_pattern"):
            out = []
            for c in node.named_children:
                out.extend(_TSPython._collect_stores(c))
            return out
        return []

    def _store_nodes(self, node):
        yield from self._collect_stores(node)

    def _function(self, node, func):
        name_node = node.child_by_field_name("name")
        fname = self._text(name_node) if name_node is not None else "<anonymous>"
        line = name_node.start_point[0] + 1 if name_node is not None else node.start_point[0] + 1
        self._add_symbol(fname, "function", line)
        params = node.child_by_field_name("parameters")
        if params is not None:
            for p in params.named_children:
                pname = self._param_name(p)
                if pname is None:
                    continue
                self._add("dataflow", "dataflow", *self._line_col(p),
                          {"kind": "param", "name": pname, "in_function": fname})
                default = p.child_by_field_name("value") if p.type.endswith("parameter") else None
                if default is not None:
                    self._walk(default, func)
                annotation = p.child_by_field_name("type")
                if annotation is not None:
                    self._annotation_reads(annotation, fname, annotation.start_point[0] + 1,
                                           annotation.start_point[1] + 1)
        return_type = node.child_by_field_name("return_type")
        if return_type is not None:
            self._annotation_reads(return_type, fname, return_type.start_point[0] + 1,
                                   return_type.start_point[1] + 1)
        mark = len(self._pending)
        self._block(node.child_by_field_name("body"), fname)
        self._close_pending(mark, [node.child_by_field_name("body")])

    def _annotation_reads(self, node, fname, line, col):
        """Record identifier roots inside a type annotation as dataflow reads
        (kind 'annotation') so Detector can flag unbound annotation names."""
        if node is None:
            return
        if node.type == "identifier":
            self._add("dataflow", "dataflow", line, col,
                      {"kind": "annotation", "name": self._text(node), "in_function": fname})
            return
        if node.type == "attribute":
            obj = node.child_by_field_name("object")
            if obj is not None:
                self._annotation_reads(obj, fname, line, col)
            return
        for child in node.named_children:
            self._annotation_reads(child, fname, child.start_point[0] + 1, child.start_point[1] + 1)

    def _param_name(self, node):
        if node is None:
            return None
        t = node.type
        if t == "identifier":
            return self._text(node)
        if t in ("default_parameter", "typed_default_parameter", "typed_parameter",
                 "list_splat_pattern", "dictionary_splat_pattern"):
            if t in ("default_parameter", "typed_default_parameter"):
                name_node = node.child_by_field_name("name")
                if name_node is not None:
                    return self._text(name_node)
            for c in node.named_children:
                if c.type == "identifier":
                    return self._text(c)
        return None

    def _class(self, node, func):
        name_node = node.child_by_field_name("name")
        if name_node is not None:
            self._add_symbol(self._text(name_node), "class", name_node.start_point[0] + 1)
        for child in node.named_children:
            if child.type == "block":
                self._block(child, func)
            else:
                self._walk(child, func)

    def _import(self, node):
        for child in node.named_children:
            if child.type == "dotted_name":
                self._record_import(self._text(child), None, child)
            elif child.type == "aliased_import":
                dname = child.child_by_field_name("name")
                alias = child.child_by_field_name("alias")
                if dname is not None:
                    self._record_import(self._text(dname),
                                        self._text(alias) if alias is not None else None, child)

    def _record_import(self, module, alias, pos_node):
        self._add("library_usage", "import", *self._line_col(pos_node),
                  {"module": module, "names": [], "alias": alias})
        root = module.split(".")[0]
        self._imported_modules.add(root)
        if alias:
            self._imported_from[alias] = root

    def _import_from(self, node):
        module_node = node.child_by_field_name("module_name")
        module = self._text(module_node) if module_node is not None else ""
        names, pairs = [], []
        alias = None
        for child in node.named_children:
            if child is module_node or child.type == "import":
                continue
            if child.type == "dotted_name":
                names.append(self._text(child))
                pairs.append((self._text(child), None))
            elif child.type == "aliased_import":
                dname = child.child_by_field_name("name")
                asname = child.child_by_field_name("alias")
                name = self._text(dname) if dname is not None else self._text(child)
                names.append(name)
                alias = self._text(asname) if asname is not None else None
                pairs.append((name, alias))
            elif child.type == "wildcard_import":
                names.append("*")
        if not names:
            return
        self._add("library_usage", "import", *self._line_col(node),
                  {"module": module, "names": names, "alias": alias if len(pairs) == 1 and pairs[0][1] else None})
        if module_node is not None and module_node.type == "dotted_name":
            root = module.split(".")[0]
            for name, asname in pairs:
                self._imported_from[asname or name] = root

    def _assign(self, node, func):
        left = node.child_by_field_name("left")
        right = node.child_by_field_name("right")
        left_names = list(self._store_nodes(left))
        if (len(left_names) == 1 and right is not None and self._is_literal(right)
                and _SECRET_NAME.search(self._text(left_names[0]))):
            self._add("security", "security", *self._line_col(right),
                      {"rule": "hardcoded_secret", "detail": f"literal assigned to '{self._text(left_names[0])}'"})
        resource = self._resource_call(right)
        if resource is not None and len(left_names) == 1:
            op, kind = resource
            entry = self._add("dataflow", "resource", *self._line_col(right),
                              {"kind": kind, "op": op, "name": self._text(left_names[0]), "closed": False})
            self._pending.append((self._text(left_names[0]), entry))
        else:
            for name_node in left_names:
                self._add("dataflow", "dataflow", *self._line_col(name_node),
                          {"kind": "assign", "name": self._text(name_node), "in_function": func})
        if right is not None:
            self._walk(right, func)

    @staticmethod
    def _is_literal(node):
        return node is not None and node.type in ("string", "integer", "float", "true", "false", "none",
                                                  "concatenation")

    def _resource_call(self, value):
        if value is None or value.type != "call":
            return None
        dotted = self._dotted_ts(value.child_by_field_name("function"))
        if dotted is None:
            return None
        op = dotted.rsplit(".", 1)[-1]
        kind = _RESOURCE_OPS.get(op.lower())
        if kind is None:
            return None
        return op, kind

    def _walrus(self, node, func):
        target = node.child_by_field_name("left")
        value = node.child_by_field_name("right")
        resource = self._resource_call(value)
        if resource is not None and target is not None and target.type == "identifier":
            op, kind = resource
            entry = self._add("dataflow", "resource", *self._line_col(value),
                              {"kind": kind, "op": op, "name": self._text(target), "closed": False})
            self._pending.append((self._text(target), entry))
        elif target is not None and target.type == "identifier":
            self._add("dataflow", "dataflow", *self._line_col(target),
                      {"kind": "assign", "name": self._text(target), "in_function": func})
        self._walk(value, func)

    def _call(self, node, func):
        fnode = node.child_by_field_name("function")
        args_node = node.child_by_field_name("arguments")
        dotted = self._dotted_ts(fnode)
        root = _root_of(dotted)
        if fnode is not None and fnode.type == "identifier":
            name, receiver = self._text(fnode), None
        elif fnode is not None and fnode.type == "attribute":
            attr = fnode.child_by_field_name("attribute")
            name = self._text(attr) if attr is not None else "<unknown>"
            receiver = self._dotted_ts(fnode.child_by_field_name("object"))
        else:
            name, receiver = (self._text(fnode) if fnode is not None else "<unknown>"), None
        line, col = self._line_col(node)
        arg_nodes = [a for a in args_node.named_children if a.type != "comment"] if args_node is not None else []
        args = [self._text(a) for a in arg_nodes]
        self._add("calls", "calls", line, col,
                  {"col": col, "name": name, "receiver": receiver, "args": args, "in_function": func})
        self._security_call(node, name, dotted, root, arg_nodes)
        self._env_call(node, fnode, arg_nodes)
        self._walk(fnode, func, call_func=True)
        for a in arg_nodes:
            self._walk(a, func)

    def _dotted_ts(self, node):
        if node is None:
            return None
        if node.type == "identifier":
            return self._text(node)
        if node.type == "attribute":
            obj = self._dotted_ts(node.child_by_field_name("object"))
            attr = node.child_by_field_name("attribute")
            if obj is None or attr is None:
                return None
            return f"{obj}.{self._text(attr)}"
        return None

    def _security_call(self, node, name, dotted, root, arg_nodes):
        line, col = self._line_col(node)
        if name in ("eval", "exec") and (dotted is None or dotted == name or root == "builtins"):
            self._add("security", "security", line, col, {"rule": f"{name}_use", "detail": f"{name}() call"})
            return
        if name in ("loads", "dumps") and ((dotted is not None and root == "pickle")
                                           or (dotted is None and self._imported_from.get(name) == "pickle")):
            detail = f"{dotted}() call" if dotted else f"pickle.{name}() call"
            self._add("security", "security", line, col, {"rule": "pickle_use", "detail": detail})
            return
        shell = False
        for a in arg_nodes:
            if a.type == "keyword_argument":
                kwname = a.child_by_field_name("name")
                kwval = a.child_by_field_name("value")
                if (kwname is not None and self._text(kwname) == "shell"
                        and kwval is not None and kwval.type == "true"):
                    shell = True
        if shell and ((dotted is not None and root == "subprocess")
                      or (dotted is None and name in _SUBPROCESS_FUNCS
                          and "subprocess" in self._imported_modules)):
            self._add("security", "security", line, col,
                      {"rule": "shell_true", "detail": "subprocess call with shell=True"})

    def _env_call(self, node, fnode, arg_nodes):
        if fnode is None:
            return
        is_environ_get = is_getenv = is_bare_getenv = False
        if fnode.type == "attribute":
            attr = fnode.child_by_field_name("attribute")
            attr_name = self._text(attr) if attr is not None else None
            obj = fnode.child_by_field_name("object")
            if attr_name == "get" and obj is not None and obj.type == "attribute":
                inner_attr = obj.child_by_field_name("attribute")
                inner_obj = obj.child_by_field_name("object")
                is_environ_get = (inner_attr is not None and self._text(inner_attr) == "environ"
                                  and inner_obj is not None and inner_obj.type == "identifier"
                                  and self._text(inner_obj) == "os")
            elif attr_name == "getenv" and obj is not None and obj.type == "identifier":
                is_getenv = self._text(obj) == "os"
        elif fnode.type == "identifier":
            is_bare_getenv = (self._text(fnode) == "getenv"
                              and self._imported_from.get("getenv") == "os")
        if not (is_environ_get or is_getenv or is_bare_getenv):
            return
        has_kw_default = any(a.type == "keyword_argument" and a.child_by_field_name("name") is not None
                             and self._text(a.child_by_field_name("name")) == "default" for a in arg_nodes)
        self._add("configuration", "config", *self._line_col(node),
                  {"kind": "env", "name": self._env_name(arg_nodes),
                   "has_default": len(arg_nodes) >= 2 or has_kw_default})

    def _string_content(self, node):
        parts = [c for c in node.named_children if c.type == "string_content"]
        if parts:
            return "".join(self._text(c) for c in parts)
        return None

    def _env_name(self, arg_nodes):
        if not arg_nodes:
            return "<unknown>"
        first = arg_nodes[0]
        if first.type == "string":
            content = self._string_content(first)
            if content is not None:
                return content
        return self._text(first)

    def _env_subscript(self, node):
        value = node.child_by_field_name("value")
        if value is None or value.type != "attribute":
            return
        attr = value.child_by_field_name("attribute")
        obj = value.child_by_field_name("object")
        if attr is None or obj is None or self._text(attr) != "environ":
            return
        if obj.type != "identifier" or self._text(obj) != "os":
            return
        sl = node.child_by_field_name("subscript")
        if sl is None:
            return
        if sl.type == "string":
            content = self._string_content(sl)
            name = content if content is not None else self._text(sl)
        else:
            name = self._text(sl)
        self._add("configuration", "config", *self._line_col(node),
                  {"kind": "env", "name": name, "has_default": False})

    def _attr_read(self, node, call_func):
        if call_func:
            return
        obj = node.child_by_field_name("object")
        attr = node.child_by_field_name("attribute")
        if obj is None or obj.type != "identifier" or attr is None:
            return
        if self._text(obj) in _CONFIG_ROOTS:
            self._add("configuration", "config", *self._line_col(node),
                      {"kind": "attr", "name": self._text(attr), "has_default": False})

    def _close_pending(self, mark, roots):
        pending = self._pending[mark:]
        del self._pending[mark:]
        if not pending:
            return
        close_names, with_names = set(), set()
        stack = list(roots)
        while stack:
            n = stack.pop()
            if n is None:
                continue
            for c in n.named_children:
                t = c.type
                if t in ("function_definition", "class_definition", "lambda"):
                    continue
                if t == "call":
                    f = c.child_by_field_name("function")
                    if f is not None and f.type == "attribute":
                        attr = f.child_by_field_name("attribute")
                        if attr is not None and self._text(attr) == "close":
                            dotted = self._dotted_ts(f.child_by_field_name("object"))
                            if dotted:
                                close_names.add(dotted)
                elif t == "with_item":
                    value, _target = self._with_item_parts(c)
                    if value is not None and value.type == "identifier":
                        with_names.add(self._text(value))
                stack.append(c)
        for name, entry in pending:
            if name is not None and (name in close_names or name in with_names):
                entry["closed"] = True

    def _store_identifier_nodes(self, node):
        t = node.type
        if t in ("function_definition", "class_definition", "lambda"):
            return
        if t in ("assignment", "augmented_assignment", "named_expression"):
            yield from self._store_nodes(node.child_by_field_name("left"))
            right = node.child_by_field_name("right")
            if right is not None:
                yield from self._store_identifier_nodes(right)
            return
        if t == "for_statement":
            yield from self._store_nodes(node.child_by_field_name("left"))
            for part in (node.child_by_field_name("right"), node.child_by_field_name("body"),
                         node.child_by_field_name("alternative")):
                if part is not None:
                    yield from self._store_identifier_nodes(part)
            return
        if t == "as_pattern_target":
            yield from self._store_nodes(node)
            return
        if t == "except_clause":
            seen_as = False
            for c in node.children:
                if c.type == "as":
                    seen_as = True
                elif seen_as and c.type == "identifier":
                    yield c
                    seen_as = False
            return
        for c in node.named_children:
            yield from self._store_identifier_nodes(c)


_TS_JAVA_LEAVES = {"line_comment", "block_comment", "text_block", "identifier", "type_identifier",
                   "void_type", "integral_type", "floating_point_type", "boolean_type",
                   "decimal_integer_literal", "hex_integer_literal", "octal_integer_literal",
                   "binary_integer_literal", "decimal_floating_point_literal",
                   "hex_floating_point_literal", "string_literal", "character_literal",
                   "true", "false", "null_literal", "modifiers", "marker_annotation"}


class _TSJava(_TSCore):
    def __init__(self, source_bytes):
        super().__init__(source_bytes)
        self._pending = []

    def run(self, root):
        for child in root.named_children:
            self._walk(child, None)

    def _walk(self, node, func):
        if node is None:
            return
        t = node.type
        if t in _TS_JAVA_LEAVES or t in ("ERROR", "annotation"):
            return
        if t == "import_declaration":
            module_node = None
            for child in node.named_children:
                if child.type.endswith("identifier"):
                    module_node = child
            if module_node is not None:
                module_text = self._text(module_node)
            else:
                module_text = self._text(node).replace("import", "", 1).replace("static", "", 1)
                module_text = module_text.strip().rstrip(";").strip()
            self._add("library_usage", "import", *self._line_col(node),
                      {"module": module_text, "names": [], "alias": None})
        elif t in ("class_declaration", "interface_declaration", "enum_declaration",
                   "record_declaration", "annotation_type_declaration"):
            name_node = node.child_by_field_name("name")
            if name_node is not None:
                self._add_symbol(self._text(name_node), "class", name_node.start_point[0] + 1)
            body = node.child_by_field_name("body")
            if body is not None:
                for child in body.named_children:
                    self._walk(child, None)
        elif t in ("method_declaration", "constructor_declaration", "compact_constructor_declaration"):
            name_node = node.child_by_field_name("name")
            fname = self._text(name_node) if name_node is not None else "<constructor>"
            if name_node is not None:
                self._add_symbol(fname, "function", name_node.start_point[0] + 1)
            params = node.child_by_field_name("parameters")
            if params is not None:
                for p in params.named_children:
                    pname = self._java_param_name(p)
                    if pname is None:
                        continue
                    self._add("dataflow", "dataflow", *self._line_col(p),
                              {"kind": "param", "name": pname, "in_function": fname})
            body = node.child_by_field_name("body")
            mark = len(self._pending)
            if body is not None:
                for child in body.named_children:
                    self._walk(child, fname)
            self._close_pending_java(mark, [body])
        elif t in ("field_declaration", "local_variable_declaration"):
            type_node = node.child_by_field_name("type")
            type_name = self._java_type_name(type_node)
            for vd in node.named_children:
                if vd.type == "variable_declarator":
                    self._java_declarator(vd, func, type_name)
        elif t == "if_statement":
            self._flow(node, "if", func)
            self._walk(node.child_by_field_name("condition"), func)
            self._walk(node.child_by_field_name("consequence"), func)
            alt = node.child_by_field_name("alternative")
            if alt is not None:
                kind = "elif" if alt.type == "if_statement" else "else"
                self._add("control_flow", "flow", *self._line_col(alt), {"kind": kind, "in_function": func})
                self._walk(alt, func)
        elif t == "for_statement":
            self._flow(node, "for", func)
            for child in node.named_children:
                self._walk(child, func)
        elif t == "enhanced_for_statement":
            self._flow(node, "for", func)
            name_node = node.child_by_field_name("name")
            if name_node is not None:
                self._add("dataflow", "dataflow", *self._line_col(name_node),
                          {"kind": "for", "name": self._text(name_node), "in_function": func})
            for child in node.named_children:
                if child is not name_node:
                    self._walk(child, func)
        elif t == "while_statement":
            self._flow(node, "while", func)
            for child in node.named_children:
                self._walk(child, func)
        elif t == "do_statement":
            self._flow(node, "do", func)
            for child in node.named_children:
                self._walk(child, func)
        elif t in ("switch_statement", "switch_expression"):
            self._flow(node, "switch", func)
            for child in node.named_children:
                self._walk(child, func)
        elif t in ("try_statement", "try_with_resources_statement"):
            self._flow(node, "try", func)
            for child in node.named_children:
                if child.type == "resource_specification":
                    for res in child.named_children:
                        if res.type == "resource":
                            self._java_resource(res, func)
                        else:
                            self._walk(res, func)
                elif child.type == "catch_clause":
                    self._flow(child, "catch", func)
                    for p in child.named_children:
                        if p.type == "catch_formal_parameter":
                            pname = self._java_param_name(p)
                            if pname is not None:
                                self._add("dataflow", "dataflow", *self._line_col(p),
                                          {"kind": "param", "name": pname, "in_function": func})
                        else:
                            self._walk(p, func)
                elif child.type == "finally_clause":
                    body = child.child_by_field_name("body")
                    anchor = body if body is not None else child
                    self._add("control_flow", "flow", *self._line_col(anchor),
                              {"kind": "finally", "in_function": func})
                    self._walk(child, func)
                else:
                    self._walk(child, func)
        elif t == "return_statement":
            self._flow(node, "return", func)
            for child in node.named_children:
                self._walk(child, func)
        elif t == "throw_statement":
            self._flow(node, "throw", func)
            for child in node.named_children:
                self._walk(child, func)
        elif t == "method_invocation":
            self._java_call(node, func)
        elif t == "object_creation_expression":
            self._java_object_creation(node, func)
        elif t == "explicit_constructor_invocation":
            self._java_explicit_ctor(node, func)
        elif t == "assignment_expression":
            self._java_assignment(node, func)
        else:
            for child in node.named_children:
                self._walk(child, func)

    def _flow(self, node, kind, func):
        self._add("control_flow", "flow", *self._line_col(node), {"kind": kind, "in_function": func})

    def _java_type_name(self, node):
        if node is None:
            return None
        text = self._text(node).split("<")[0].strip()
        return text.rsplit(".", 1)[-1].strip()

    def _java_param_name(self, node):
        if node is None:
            return None
        if node.type == "spread_parameter":
            for c in node.named_children:
                if c.type == "variable_declarator":
                    nm = c.child_by_field_name("name")
                    return self._text(nm) if nm is not None else None
        idents = [c for c in node.named_children if c.type == "identifier"]
        if idents:
            return self._text(idents[-1])
        return None

    def _java_declarator(self, vd, func, type_name):
        name_node = vd.child_by_field_name("name")
        if name_node is None:
            return
        name = self._text(name_node)
        value = vd.child_by_field_name("value")
        if value is not None and self._java_is_literal(value) and _SECRET_NAME.search(name):
            self._add("security", "security", *self._line_col(value),
                      {"rule": "hardcoded_secret", "detail": f"literal assigned to '{name}'"})
        kind = _JAVA_RESOURCE_KINDS.get((type_name or "").lower())
        if kind is not None and value is not None and value.type == "object_creation_expression":
            entry = self._add("dataflow", "resource", *self._line_col(value),
                              {"kind": kind, "op": type_name, "name": name, "closed": False})
            self._pending.append((name, entry))
        else:
            self._add("dataflow", "dataflow", *self._line_col(name_node),
                      {"kind": "assign", "name": name, "in_function": func})
        if value is not None:
            self._walk(value, func)

    @staticmethod
    def _java_is_literal(node):
        return node is not None and (node.type.endswith("_literal") or node.type in ("true", "false"))

    def _java_resource(self, res, func):
        name_node = res.child_by_field_name("name")
        if name_node is not None:
            type_node = res.child_by_field_name("type")
            value = res.child_by_field_name("value")
            type_name = self._java_type_name(type_node)
            kind = _JAVA_RESOURCE_KINDS.get((type_name or "").lower())
            anchor = value if value is not None else res
            self._add("dataflow", "resource", *self._line_col(anchor),
                      {"kind": kind or "resource", "op": type_name or "resource",
                       "name": self._text(name_node), "closed": True})
            if value is not None:
                self._walk(value, func)
        else:
            ident = next((c for c in res.named_children if c.type == "identifier"), None)
            if ident is not None:
                self._mark_closed_java(self._text(ident))

    def _java_assignment(self, node, func):
        left = node.child_by_field_name("left")
        right = node.child_by_field_name("right")
        ops = {c.type for c in node.children if not c.is_named}
        kind = "assign" if ops == {"="} else "aug"
        if left is not None and right is not None and left.type in ("identifier", "field_access", "array_access"):
            name = self._text(left)
            if _SECRET_NAME.search(name.split(".")[-1]) and right.type == "string_literal":
                self._add("security", "security", *self._line_col(right),
                          {"rule": "hardcoded_secret", "detail": f"literal assigned to '{name}'"})
            self._add("dataflow", "dataflow", *self._line_col(left),
                      {"kind": kind, "name": name, "in_function": func})
        if right is not None:
            self._walk(right, func)

    def _java_call(self, node, func):
        name_node = node.child_by_field_name("name")
        obj_node = node.child_by_field_name("object")
        args_node = node.child_by_field_name("arguments")
        name = self._text(name_node) if name_node is not None else "<unknown>"
        receiver = self._text(obj_node) if obj_node is not None else None
        arg_nodes = list(args_node.named_children) if args_node is not None else []
        args = [self._text(a) for a in arg_nodes]
        line, col = self._line_col(node)
        self._add("calls", "calls", line, col,
                  {"col": col, "name": name, "receiver": receiver, "args": args, "in_function": func})
        recv_base = receiver.rsplit(".", 1)[-1].lower() if receiver else ""
        if name == "exec" and receiver is not None and "runtime" in receiver.lower():
            self._add("security", "security", line, col, {"rule": "runtime_exec", "detail": "Runtime.exec() call"})
        if name in ("command", "start") and "processbuilder" in recv_base:
            self._add("security", "security", line, col,
                      {"rule": "process_builder", "detail": "ProcessBuilder command"})
        if name == "getenv" and recv_base == "system":
            self._add("configuration", "config", line, col,
                      {"kind": "env", "name": self._java_first_arg_string(args_node),
                       "has_default": len(arg_nodes) >= 2})
        elif name == "getProperty" and recv_base == "system":
            self._add("configuration", "config", line, col,
                      {"kind": "property", "name": self._java_first_arg_string(args_node),
                       "has_default": len(arg_nodes) >= 2})
        if name == "close" and receiver is not None:
            self._mark_closed_java(receiver.rsplit(".", 1)[-1])
        for child in node.named_children:
            self._walk(child, func)

    def _java_first_arg_string(self, args_node):
        if args_node is None:
            return "<unknown>"
        for child in args_node.named_children:
            text = self._text(child)
            if child.type == "string_literal" and len(text) >= 2 and text[0] == '"':
                return text[1:-1]
            return text
        return "<unknown>"

    def _java_object_creation(self, node, func):
        type_node = node.child_by_field_name("type")
        args_node = node.child_by_field_name("arguments")
        type_name = self._java_type_name(type_node)
        arg_nodes = list(args_node.named_children) if args_node is not None else []
        args = [self._text(a) for a in arg_nodes]
        line, col = self._line_col(node)
        self._add("calls", "calls", line, col,
                  {"col": col, "name": type_name or "<new>", "receiver": None, "args": args,
                   "in_function": func})
        if (type_name or "").lower() == "processbuilder":
            self._add("security", "security", line, col,
                      {"rule": "process_builder", "detail": "new ProcessBuilder(...)"})
        for child in node.named_children:
            if child is not type_node:
                self._walk(child, func)

    def _java_explicit_ctor(self, node, func):
        text = self._text(node)
        name = "this" if text.startswith("this") else "super"
        args_node = node.child_by_field_name("arguments")
        args = [self._text(a) for a in args_node.named_children] if args_node is not None else []
        self._add("calls", "calls", *self._line_col(node),
                  {"col": self._line_col(node)[1], "name": name, "receiver": None, "args": args,
                   "in_function": func})
        for child in node.named_children:
            self._walk(child, func)

    def _mark_closed_java(self, name):
        for pending_name, entry in self._pending:
            if pending_name == name:
                entry["closed"] = True

    def _close_pending_java(self, mark, roots):
        pending = self._pending[mark:]
        del self._pending[mark:]
        if not pending:
            return
        close_names = set()
        stack = list(roots)
        while stack:
            n = stack.pop()
            if n is None:
                continue
            for c in n.named_children:
                if c.type in ("method_declaration", "constructor_declaration", "class_declaration",
                              "interface_declaration", "enum_declaration", "record_declaration",
                              "lambda_expression"):
                    continue
                if c.type == "method_invocation":
                    name_node = c.child_by_field_name("name")
                    obj_node = c.child_by_field_name("object")
                    if (name_node is not None and self._text(name_node) == "close"
                            and obj_node is not None):
                        close_names.add(self._text(obj_node).rsplit(".", 1)[-1])
                stack.append(c)
        for name, entry in pending:
            if name in close_names:
                entry["closed"] = True


def _kb_from_analyzer(analyzer, source, name):
    symbols = sorted(analyzer.symbols, key=lambda s: (s["line"], s["name"]))
    return {
        "file": name,
        "source_hash": hashlib.sha256(source.encode("utf-8")).hexdigest(),
        "symbols": {"defined": symbols},
        "categories": analyzer.finalize(),
        "lines": len(source.splitlines()),
    }


_TS_LANGUAGES = ("python", "java")
_ts_language_cache = {}


def _ts_language(language):
    if language in _ts_language_cache:
        return _ts_language_cache[language]
    try:
        from tree_sitter import Language
        if language == "python":
            import tree_sitter_python as grammar
        elif language == "java":
            import tree_sitter_java as grammar
        else:
            raise ValueError(f"unknown language: {language!r} (expected one of {_TS_LANGUAGES})")
    except ImportError as exc:
        raise ValueError(f"tree-sitter backend requires the tree-sitter {language} grammar: {exc}") from None
    lang = Language(grammar.language())
    _ts_language_cache[language] = lang
    return lang


def _parse_source_ast(source, name):
    try:
        tree = ast.parse(source, filename=name)
    except SyntaxError as exc:
        line = exc.lineno if exc.lineno is not None else 0
        raise ValueError(f"{name}:{line}: syntax error: {exc.msg}") from None
    except ValueError as exc:
        raise ValueError(f"{name}: cannot parse source: {exc}") from None
    analyzer = _Analyzer()
    analyzer.collect_imports(tree)
    analyzer.run(tree)
    return _kb_from_analyzer(analyzer, source, name)


def _parse_source_tree_sitter(source, name, language):
    source_bytes = source.encode("utf-8")
    from tree_sitter import Parser
    parser = Parser(_ts_language(language))
    root = parser.parse(source_bytes).root_node
    analyzer = _TSPython(source_bytes) if language == "python" else _TSJava(source_bytes)
    analyzer.run(root)
    analyzer._add_parse_errors(root)
    return _kb_from_analyzer(analyzer, source, name)


def _parse_source_libcst(source, name):
    try:
        import libcst as cst
        import libcst.metadata as cst_md
    except ImportError as exc:
        raise ValueError(f"libcst backend requires the libcst package: {exc}") from None
    try:
        module = cst.parse_module(source)
    except cst.ParserSyntaxError as exc:
        line = getattr(exc, "line", 0) or 0
        raise ValueError(f"{name}:{line}: syntax error: {exc}") from None

    analyzer = _KBCore()

    def dotted(expr):
        parts = []
        while isinstance(expr, cst.Attribute):
            parts.append(expr.attr.value)
            expr = expr.value
        if isinstance(expr, cst.Name):
            parts.append(expr.value)
            return ".".join(reversed(parts))
        return None

    def is_literal(expr):
        if isinstance(expr, (cst.SimpleString, cst.Integer, cst.Float, cst.Imaginary, cst.ConcatenatedString)):
            return True
        return isinstance(expr, cst.Name) and expr.value in ("True", "False", "None")

    def store_names(target):
        if isinstance(target, cst.Name):
            yield target
        elif isinstance(target, (cst.Tuple, cst.List)):
            for el in target.elements:
                yield from store_names(el.value)
        elif isinstance(target, cst.StarredElement):
            yield from store_names(target.value)

    class _LibCSTVisitor(cst.CSTVisitor):
        METADATA_DEPENDENCIES = (cst_md.PositionProvider,)

        def __init__(self):
            self.func_stack = []
            self.func_marks = []
            self.pending_entries = []
            self.elif_ids = set()
            self.imported_modules = set()
            self.imported_from = {}
            self.call_func_stack = []

        def pos(self, node):
            position = self.get_metadata(cst_md.PositionProvider, node)
            return position.start.line, position.start.column + 1

        def in_function(self):
            return self.func_stack[-1] if self.func_stack else None

        def add(self, category, prefix, node, fields):
            line, col = self.pos(node)
            return analyzer._add(category, prefix, line, col, fields)

        def visit_FunctionDef(self, node):
            analyzer._add_symbol(node.name.value, "function", self.pos(node.name)[0])
            for param in list(node.params.params) + list(node.params.posonly_params) + list(node.params.kwonly_params):
                self.add("dataflow", "dataflow", param.name,
                         {"kind": "param", "name": param.name.value, "in_function": node.name.value})
            if isinstance(node.params.star_arg, cst.Param):
                self.add("dataflow", "dataflow", node.params.star_arg.name,
                         {"kind": "param", "name": "*" + node.params.star_arg.name.value,
                          "in_function": node.name.value})
            if isinstance(node.params.star_kwarg, cst.Param):
                self.add("dataflow", "dataflow", node.params.star_kwarg.name,
                         {"kind": "param", "name": "**" + node.params.star_kwarg.name.value,
                          "in_function": node.name.value})
            self.func_stack.append(node.name.value)
            self.func_marks.append((len(self.pending_entries), node))
            return True

        def leave_FunctionDef(self, original_node):
            self.func_stack.pop()
            mark, node = self.func_marks.pop()
            self.resolve_pending(mark, node.body)

        def leave_Module(self, original_node):
            self.resolve_pending(0, original_node)

        def visit_ClassDef(self, node):
            analyzer._add_symbol(node.name.value, "class", self.pos(node.name)[0])
            return True

        def visit_Import(self, node):
            for alias in node.names:
                module_name = module.code_for_node(alias.name)
                alias_name = alias.asname.name.value if isinstance(alias.asname, cst.AsName) else None
                self.add("library_usage", "import", alias.name,
                         {"module": module_name, "names": [], "alias": alias_name})
                root = module_name.split(".")[0]
                self.imported_modules.add(root)
                if alias_name:
                    self.imported_from[alias_name] = root

        def visit_ImportFrom(self, node):
            module_name = module.code_for_node(node.module) if node.module is not None else ""
            names = [module.code_for_node(alias.name) for alias in node.names]
            alias = None
            if len(node.names) == 1 and isinstance(node.names[0].asname, cst.AsName):
                alias = node.names[0].asname.name.value
            self.add("library_usage", "import", node,
                     {"module": ("." * len(node.relative)) + module_name, "names": names, "alias": alias})
            if node.module is not None:
                root = module_name.split(".")[0]
                for alias_obj in node.names:
                    key = alias_obj.asname.name.value if isinstance(alias_obj.asname, cst.AsName) \
                        else module.code_for_node(alias_obj.name)
                    self.imported_from[key] = root

        def visit_Assign(self, node):
            self.handle_assignment([t.target for t in node.targets], node.value)

        def visit_AnnAssign(self, node):
            if node.value is not None:
                self.handle_assignment([node.target], node.value)

        def handle_assignment(self, targets, value):
            if len(targets) == 1 and isinstance(targets[0], cst.Name) and is_literal(value) \
                    and _SECRET_NAME.search(targets[0].value):
                self.add("security", "security", value,
                         {"rule": "hardcoded_secret", "detail": f"literal assigned to '{targets[0].value}'"})
            resource = self.resource_call(value)
            if resource is not None and len(targets) == 1 and isinstance(targets[0], cst.Name):
                op, kind = resource
                entry = self.add("dataflow", "resource", value,
                                 {"kind": kind, "op": op, "name": targets[0].value, "closed": False})
                self.pending_entries.append((targets[0].value, entry))
            else:
                for target in targets:
                    for name_node in store_names(target):
                        self.add("dataflow", "dataflow", name_node,
                                 {"kind": "assign", "name": name_node.value, "in_function": self.in_function()})

        def visit_AugAssign(self, node):
            if isinstance(node.target, cst.Name):
                self.add("dataflow", "dataflow", node.target,
                         {"kind": "aug", "name": node.target.value, "in_function": self.in_function()})

        def visit_NamedExpr(self, node):
            value = node.value
            resource = self.resource_call(value)
            if resource is not None and isinstance(node.target, cst.Name):
                op, kind = resource
                entry = self.add("dataflow", "resource", value,
                                 {"kind": kind, "op": op, "name": node.target.value, "closed": False})
                self.pending_entries.append((node.target.value, entry))
            elif isinstance(node.target, cst.Name):
                self.add("dataflow", "dataflow", node.target,
                         {"kind": "assign", "name": node.target.value, "in_function": self.in_function()})

        def visit_For(self, node):
            self.add("control_flow", "flow", node, {"kind": "for", "in_function": self.in_function()})
            for name_node in store_names(node.target):
                self.add("dataflow", "dataflow", name_node,
                         {"kind": "for", "name": name_node.value, "in_function": self.in_function()})

        def visit_While(self, node):
            self.add("control_flow", "flow", node, {"kind": "while", "in_function": self.in_function()})

        def visit_If(self, node):
            if id(node) not in self.elif_ids:
                self.add("control_flow", "flow", node, {"kind": "if", "in_function": self.in_function()})
            orelse = node.orelse
            if orelse is not None and orelse.body:
                first = orelse.body[0]
                if isinstance(first, cst.If):
                    self.elif_ids.add(id(first))
                    self.add("control_flow", "flow", first, {"kind": "elif", "in_function": self.in_function()})
                else:
                    self.add("control_flow", "flow", first, {"kind": "else", "in_function": self.in_function()})

        def visit_Try(self, node):
            self.add("control_flow", "flow", node, {"kind": "try", "in_function": self.in_function()})
            if node.orelse is not None and node.orelse.body:
                self.add("control_flow", "flow", node.orelse.body[0],
                         {"kind": "else", "in_function": self.in_function()})
            if node.finalbody:
                self.add("control_flow", "flow", node.finalbody[0],
                         {"kind": "finally", "in_function": self.in_function()})

        visit_TryStar = visit_Try

        def visit_ExceptHandler(self, node):
            self.add("control_flow", "flow", node, {"kind": "except", "in_function": self.in_function()})

        def visit_ForIn(self, node):
            for name_node in store_names(node.target):
                self.add("dataflow", "dataflow", name_node,
                         {"kind": "for", "name": name_node.value, "in_function": self.in_function()})

        def visit_With(self, node):
            for item in node.items:
                resource = self.resource_call(item.call)
                if resource is not None:
                    op, kind = resource
                    name = item.optional_vars.value if isinstance(item.optional_vars, cst.Name) else None
                    self.add("dataflow", "resource", item.call,
                             {"kind": kind, "op": op, "name": name, "closed": True})

        def visit_Return(self, node):
            self.add("control_flow", "flow", node, {"kind": "return", "in_function": self.in_function()})

        def visit_Raise(self, node):
            self.add("control_flow", "flow", node, {"kind": "raise", "in_function": self.in_function()})

        def visit_Call(self, node):
            func = node.func
            call_dotted = dotted(func)
            root = _root_of(call_dotted)
            if isinstance(func, cst.Name):
                name, receiver = func.value, None
            elif isinstance(func, cst.Attribute):
                name = func.attr.value
                receiver = dotted(func.value)
            else:
                name, receiver = module.code_for_node(func), None
            args = [module.code_for_node(a) for a in node.args]
            self.add("calls", "calls", node,
                     {"col": self.pos(node)[1], "name": name, "receiver": receiver,
                      "args": args, "in_function": self.in_function()})
            self.security_call(node, name, call_dotted, root)
            self.env_call(node, func)
            self.call_func_stack.append(func)
            return True

        def leave_Call(self, original_node):
            self.call_func_stack.pop()

        def visit_Attribute(self, node):
            if self.call_func_stack and node is self.call_func_stack[-1]:
                return True
            if isinstance(node.value, cst.Name) and node.value.value in _CONFIG_ROOTS:
                self.add("configuration", "config", node,
                         {"kind": "attr", "name": node.attr.value, "has_default": False})
            return True

        def visit_Subscript(self, node):
            if isinstance(node.value, cst.Attribute) and node.value.attr.value == "environ" \
                    and isinstance(node.value.value, cst.Name) and node.value.value.value == "os":
                sl = node.slice[0].slice if node.slice else None
                if isinstance(sl, cst.Index):
                    sl = sl.value
                if isinstance(sl, cst.SimpleString):
                    try:
                        name = ast.literal_eval(sl.value)
                    except ValueError:
                        name = sl.value
                elif sl is not None:
                    name = module.code_for_node(sl)
                else:
                    name = "<unknown>"
                self.add("configuration", "config", node,
                         {"kind": "env", "name": name, "has_default": False})
            return True

        def resource_call(self, expr):
            if not isinstance(expr, cst.Call):
                return None
            call_dotted = dotted(expr.func)
            if call_dotted is None:
                return None
            op = call_dotted.rsplit(".", 1)[-1]
            kind = _RESOURCE_OPS.get(op.lower())
            if kind is None:
                return None
            return op, kind

        def security_call(self, node, name, call_dotted, root):
            line, col = self.pos(node)
            if name in ("eval", "exec") and (call_dotted is None or call_dotted == name or root == "builtins"):
                analyzer._add("security", "security", line, col,
                              {"rule": f"{name}_use", "detail": f"{name}() call"})
            elif name in ("loads", "dumps") and (
                (call_dotted is not None and root == "pickle")
                or (call_dotted is None and self.imported_from.get(name) == "pickle")
            ):
                detail = f"{call_dotted}() call" if call_dotted else f"pickle.{name}() call"
                analyzer._add("security", "security", line, col, {"rule": "pickle_use", "detail": detail})
            else:
                shell = any(a.keyword is not None and a.keyword.value == "shell"
                            and isinstance(a.value, cst.Name) and a.value.value == "True"
                            for a in node.args)
                if shell and ((call_dotted is not None and root == "subprocess")
                              or (call_dotted is None and name in _SUBPROCESS_FUNCS
                                  and "subprocess" in self.imported_modules)):
                    analyzer._add("security", "security", line, col,
                                  {"rule": "shell_true", "detail": "subprocess call with shell=True"})

        def env_call(self, node, func):
            is_environ_get = (isinstance(func, cst.Attribute) and func.attr.value == "get"
                              and isinstance(func.value, cst.Attribute) and func.value.attr.value == "environ"
                              and isinstance(func.value.value, cst.Name) and func.value.value.value == "os")
            is_getenv = (isinstance(func, cst.Attribute) and func.attr.value == "getenv"
                         and isinstance(func.value, cst.Name) and func.value.value == "os")
            is_bare_getenv = (isinstance(func, cst.Name) and func.value == "getenv"
                              and self.imported_from.get("getenv") == "os")
            if not (is_environ_get or is_getenv or is_bare_getenv):
                return
            positional = [a.value for a in node.args if a.keyword is None]
            name = "<unknown>"
            if positional and isinstance(positional[0], cst.SimpleString):
                try:
                    name = ast.literal_eval(positional[0].value)
                except ValueError:
                    name = positional[0].value
            elif positional:
                name = module.code_for_node(positional[0])
            has_default = len(node.args) >= 2 or any(
                a.keyword is not None and a.keyword.value == "default" for a in node.args)
            self.add("configuration", "config", node,
                     {"kind": "env", "name": name, "has_default": has_default})

        def resolve_pending(self, mark, scope):
            scoped = [(name, entry) for name, entry in self.pending_entries[mark:]]
            del self.pending_entries[mark:]
            if not scoped:
                return
            close_names, with_names = set(), set()
            stack = list(scope.children) if hasattr(scope, "children") else []
            while stack:
                cur = stack.pop()
                if isinstance(cur, (cst.FunctionDef, cst.ClassDef, cst.Lambda)):
                    continue
                if isinstance(cur, cst.Call) and isinstance(cur.func, cst.Attribute) \
                        and cur.func.attr.value == "close":
                    close_dotted = dotted(cur.func.value)
                    if close_dotted:
                        close_names.add(close_dotted)
                elif isinstance(cur, cst.With):
                    for item in cur.items:
                        if isinstance(item.call, cst.Name):
                            with_names.add(item.call.value)
                        if isinstance(item.optional_vars, cst.Name):
                            with_names.add(item.optional_vars.value)
                stack.extend(cur.children)
            for name, entry in scoped:
                if name is not None and (name in close_names or name in with_names):
                    entry["closed"] = True

    visitor = _LibCSTVisitor()
    wrapper = cst_md.MetadataWrapper(module)
    wrapper.visit(visitor)
    return _kb_from_analyzer(analyzer, source, name)


_SPOON_DIR = Path(__file__).resolve().parent / "spoon"
_SPOON_JAR = "kappa-spoon-extractor.jar"
_spoon_state = {"mvn_attempted": False}


def _spoon_fallback_warning(reason):
    print(f"Parser: spoon backend unavailable ({reason}); using tree-sitter fallback",
          file=sys.stderr, flush=True)


def _ensure_spoon_jar():
    jar = _SPOON_DIR / "target" / _SPOON_JAR
    if jar.exists():
        return jar
    if _spoon_state["mvn_attempted"]:
        return None
    _spoon_state["mvn_attempted"] = True
    mvn = shutil.which("mvn")
    if mvn is None:
        return None
    try:
        subprocess.run([mvn, "-q", "-f", str(_SPOON_DIR / "pom.xml"), "-DskipTests", "package"],
                       check=True, timeout=600, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        return None
    return jar if jar.exists() else None


def _parse_source_spoon(source, name):
    jar = _ensure_spoon_jar()
    java = shutil.which("java")
    if jar is not None and java is not None:
        tmp_path = None
        try:
            with tempfile.NamedTemporaryFile("w", suffix=".java", delete=False, encoding="utf-8") as tmp:
                tmp.write(source)
                tmp_path = tmp.name
            proc = subprocess.run([java, "-jar", str(jar), tmp_path, name],
                                  capture_output=True, text=True, timeout=180)
            if proc.returncode == 0 and proc.stdout.strip():
                kb = json.loads(proc.stdout)
                kb["file"] = name
                kb["source_hash"] = hashlib.sha256(source.encode("utf-8")).hexdigest()
                if set(kb) == {"file", "source_hash", "symbols", "categories", "lines"} \
                        and list(kb["categories"]) == CATEGORIES:
                    return kb
                raise ValueError("unexpected spoon KB shape")
            reason = f"exit={proc.returncode} {proc.stderr.strip().splitlines()[-1][:120] if proc.stderr.strip() else 'no stderr'}"
        except Exception as exc:
            reason = f"{type(exc).__name__}: {exc}"[:160]
        finally:
            if tmp_path is not None:
                Path(tmp_path).unlink(missing_ok=True)
        _spoon_fallback_warning(reason)
    else:
        _spoon_fallback_warning("mvn not found and prebuilt extractor jar missing")
    return _parse_source_tree_sitter(source, name, "java")


_BACKENDS = ("ast", "tree-sitter", "libcst", "spoon")


def _resolve_language(language, filename):
    if language is None:
        return "java" if Path(filename).suffix.lower() == ".java" else "python"
    if language not in _TS_LANGUAGES:
        raise ValueError(f"unknown language: {language!r} (expected 'python' or 'java')")
    return language


def parse_source(source: str, filename: str = "<source>", backend: str | None = None,
                 language: str | None = None) -> dict:
    name = Path(filename).name
    lang = _resolve_language(language, filename)
    if backend is None:
        backend = "ast" if lang == "python" else "tree-sitter"
    kb = None
    if backend == "ast":
        if lang != "python":
            raise ValueError("ast backend supports Python sources only")
        kb = _parse_source_ast(source, name)
    elif backend == "tree-sitter":
        kb = _parse_source_tree_sitter(source, name, lang)
    elif backend == "libcst":
        if lang != "python":
            raise ValueError("libcst backend supports Python sources only")
        kb = _parse_source_libcst(source, name)
    elif backend == "spoon":
        if lang != "java":
            raise ValueError("spoon backend supports Java sources only")
        kb = _parse_source_spoon(source, name)
    else:
        raise ValueError(f"unknown backend: {backend!r} (expected one of {_BACKENDS})")
    kb["language"] = lang
    kb["source"] = source
    return kb


def parse_file(path, backend: str | None = None, language: str | None = None) -> dict:
    file_path = Path(path)
    return parse_source(file_path.read_text(encoding="utf-8"), filename=file_path.name,
                        backend=backend, language=language)


def _context_defined_and_imports(source: str, filename: str, backend: str | None,
                                 language: str | None) -> tuple[list[dict], list[dict]] | None:
    try:
        context_kb = parse_source(source, filename=filename, backend=backend, language=language)
    except Exception:
        return None
    defined = [
        {"name": s.get("name"), "kind": s.get("kind"), "line": s.get("line")}
        for s in (context_kb.get("symbols") or {}).get("defined") or []
        if s.get("name")
    ]
    imports = [
        {"module": entry.get("module"), "names": entry.get("names") or [], "alias": entry.get("alias")}
        for entry in (context_kb.get("categories") or {}).get("library_usage") or []
    ]
    return defined, imports


def build_context(context_source: str | None = None, context_name: str = "<context>",
                  repo_dir=None, backend: str | None = None, language: str | None = None) -> dict:
    """Parse the full-file context (and optionally sibling modules) into a
    repo-scope symbol/import table that Detector joins against (SPEC: scope
    increases from module-level to repo level)."""
    context: dict = {
        "file": {"defined": [], "imports": []},
        "repo": {"defined": [], "modules": {}},
        "repo_scope_available": False,
    }
    if context_source is not None:
        parsed = _context_defined_and_imports(context_source, context_name, backend, language)
        if parsed:
            context["file"]["defined"], context["file"]["imports"] = parsed
    if repo_dir is not None:
        repo_path = Path(repo_dir)
        modules: dict[str, list[str]] = {}
        all_defined: list[dict] = []

        try:
            from kappa.RepoScope import RepoScope
            scope = RepoScope(repo_path)
            context["repo_scope_available"] = True
        except Exception:
            scope = None

        if scope is not None:
            seen_names = set()
            for py in sorted(repo_path.glob("*.py"))[:100]:
                try:
                    sibling_kb = parse_source(py.read_text(encoding="utf-8"),
                                              filename=py.name, backend=backend, language=language)
                except Exception:
                    continue
                names = [s.get("name") for s in (sibling_kb.get("symbols") or {}).get("defined") or []
                         if s.get("name")]
                modules[py.stem] = names
                for name in names:
                    if name not in seen_names:
                        seen_names.add(name)
                        hits = scope.where_defined(name)
                        module = hits[0]["module"] if hits else py.stem
                        all_defined.append({"name": name, "kind": "module-symbol", "module": module})
        else:
            for sibling in sorted(repo_path.glob("*.py"))[:100]:
                try:
                    sibling_kb = parse_source(sibling.read_text(encoding="utf-8"),
                                              filename=sibling.name, backend=backend, language=language)
                except Exception:
                    continue
                names = [s.get("name") for s in (sibling_kb.get("symbols") or {}).get("defined") or []
                         if s.get("name")]
                modules[sibling.stem] = names
                for name in names:
                    all_defined.append({"name": name, "kind": "module-symbol", "module": sibling.stem})
        context["repo"]["modules"] = modules
        context["repo"]["defined"] = all_defined
    return context


def attach_context(kb: dict, context: dict) -> dict:
    kb["context"] = context
    return kb


def save_kb(kb: dict, path) -> None:
    Path(path).write_text(json.dumps(kb, indent=2) + "\n", encoding="utf-8")


def load_kb(path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def libcst_replace_name(source: str, line: int, old: str, new: str) -> str:
    try:
        import libcst as cst
        import libcst.metadata as cst_md
    except ImportError as exc:
        raise ValueError(f"libcst_replace_name requires the libcst package: {exc}") from None
    if not isinstance(line, int) or line < 1:
        raise ValueError("line must be a 1-based integer")

    class _Renamer(cst.CSTTransformer):
        METADATA_DEPENDENCIES = (cst_md.PositionProvider,)

        def __init__(self):
            self.found = False

        def leave_Name(self, original_node, updated_node):
            position = self.get_metadata(cst_md.PositionProvider, original_node)
            if position.start.line == line and original_node.value == old:
                self.found = True
                return updated_node.with_changes(value=new)
            return updated_node

    try:
        module = cst.parse_module(source)
    except cst.ParserSyntaxError as exc:
        raise ValueError(f"libcst_replace_name: cannot parse source: {exc}") from None
    renamer = _Renamer()
    new_module = cst_md.MetadataWrapper(module).visit(renamer)
    if not renamer.found:
        return source
    return new_module.code


if __name__ == "__main__":
    demo_path = Path(__file__).resolve().parent / "examples" / "demo_violations.py"
    kb = parse_file(demo_path)
    print(f"parsed {kb['file']}: lines={kb['lines']} sha256={kb['source_hash'][:16]}", flush=True)
    for category in CATEGORIES:
        print(f"  {category}: {len(kb['categories'][category])} entries", flush=True)
    assert all(kb["categories"][category] for category in CATEGORIES), "every category needs >=1 entry"
    assert any(e["name"] == "sum_value" for e in kb["categories"]["calls"]), "sum_value call not recorded"
    handle_entries = [e for e in kb["categories"]["dataflow"]
                      if e.get("name") == "handle" and e.get("op") == "open"]
    assert handle_entries and handle_entries[0]["closed"] is False, "resource handle must be closed=false"
    env_debug = [e for e in kb["categories"]["configuration"] if e.get("name") == "DEBUG"]
    assert env_debug and env_debug[0]["kind"] == "env" and env_debug[0]["has_default"] is False
    assert any(e["kind"] == "return" and e["line"] == 15 for e in kb["categories"]["control_flow"])
    assert not any(e["line"] == 16 for e in kb["categories"]["control_flow"]), "unreachable print needs no flow entry"
    assert any(e["rule"] == "eval_use" for e in kb["categories"]["security"])
    assert {"sum_values", "collect", "main"} <= {s["name"] for s in kb["symbols"]["defined"]}
    try:
        parse_source("def broken(:\n    pass\n", filename="broken.py")
    except ValueError as exc:
        assert ":1:" in str(exc), f"error message must include the line number, got: {exc}"
    else:
        raise AssertionError("syntax-broken source must raise ValueError")
    with tempfile.TemporaryDirectory() as tmp:
        kb_path = Path(tmp) / "kb.json"
        save_kb(kb, kb_path)
        assert load_kb(kb_path) == kb, "save_kb/load_kb roundtrip mismatch"

    schema_keys = {"file", "source_hash", "symbols", "categories", "lines"}
    for backend in ("ast", "tree-sitter", "libcst"):
        kb_b = parse_file(demo_path, backend=backend)
        assert set(kb_b) == schema_keys and list(kb_b["categories"]) == CATEGORIES
        assert set(kb_b["symbols"]) == {"defined"}
        counts = {category: len(kb_b["categories"][category]) for category in CATEGORIES}
        print(f"backend={backend}: " + " ".join(f"{c}={n}" for c, n in counts.items()), flush=True)
        assert any(e["name"] == "sum_value" for e in kb_b["categories"]["calls"]), f"{backend}: sum_value call"
        h = [e for e in kb_b["categories"]["dataflow"] if e.get("name") == "handle" and e.get("op") == "open"]
        assert h and h[0]["closed"] is False, f"{backend}: resource handle closed=false"
        env_d = [e for e in kb_b["categories"]["configuration"] if e.get("name") == "DEBUG"]
        assert env_d and env_d[0]["kind"] == "env" and env_d[0]["has_default"] is False, f"{backend}: env DEBUG"
        assert any(e["rule"] == "eval_use" for e in kb_b["categories"]["security"]), f"{backend}: eval_use"
        for category in CATEGORIES:
            for entry in kb_b["categories"][category]:
                assert "id" in entry and "line" in entry, f"{backend}: {category} entry missing id/line"
        assert parse_file(demo_path, backend=backend) == kb_b, f"{backend}: parse not deterministic"
    bad_py = parse_source("def ok():\n    return 1\n\ndef broken(:\n    pass\n",
                          filename="broken.py", backend="tree-sitter")
    assert any(e["kind"] == "parse_error" for e in bad_py["categories"]["control_flow"]), \
        "tree-sitter python must report parse_error instead of raising"
    assert any(s["name"] == "ok" for s in bad_py["symbols"]["defined"]), "non-ERROR regions must still be extracted"

    hello_path = demo_path.parent / "Hello.java"
    jkb = parse_file(hello_path, backend="tree-sitter")
    jcounts = {category: len(jkb["categories"][category]) for category in CATEGORIES}
    print("backend=tree-sitter(java Hello.java): "
          + " ".join(f"{c}={n}" for c, n in jcounts.items()), flush=True)
    assert set(jkb) == schema_keys and list(jkb["categories"]) == CATEGORIES
    assert any(e["name"] == "helper" for e in jkb["categories"]["calls"]), "java helper() call"
    assert len(jkb["categories"]["library_usage"]) >= 1, "java import"
    assert any(e["kind"] == "env" and e.get("name") == "KEY" for e in jkb["categories"]["configuration"]), \
        "java System.getenv KEY"
    resources = [e for e in jkb["categories"]["dataflow"] if e.get("kind") in ("file", "socket", "db")]
    assert any(e.get("name") == "fin" and e.get("closed") is False for e in resources), "java unclosed resource"
    assert any(e["kind"] == "if" for e in jkb["categories"]["control_flow"]), "java if"
    assert any(e["rule"] == "runtime_exec" for e in jkb["categories"]["security"]), "java Runtime.exec"
    assert any(e["rule"] == "hardcoded_secret" for e in jkb["categories"]["security"]), "java hardcoded secret"
    assert parse_file(hello_path, backend="tree-sitter") == jkb, "java parse not deterministic"
    bad_java = parse_source("class A {\n    void m() {\n        int x = ;\n    }\n}\n",
                            filename="Bad.java", backend="tree-sitter")
    assert any(e["kind"] == "parse_error" for e in bad_java["categories"]["control_flow"]), \
        "tree-sitter java must report parse_error instead of raising"

    line_src = "def f(values):\n    total = total + sum_value(values)\n    return total\n"
    replaced = libcst_replace_name(line_src, 2, "sum_value", "sum_values")
    assert replaced.splitlines()[1] == "    total = total + sum_values(values)", "codemod replaces name on line"
    assert replaced.splitlines()[0] == "def f(values):" and replaced.splitlines()[2] == "    return total", \
        "codemod preserves all other lines"
    assert libcst_replace_name(line_src, 2, "absent", "x") == line_src, "absent name returns source unchanged"
    fixed = libcst_replace_name(demo_path.read_text(encoding="utf-8"), 11, "sum_value", "sum_values")
    assert fixed.splitlines()[10] == "        total = total + sum_values(v)", "codemod on line 11"
    original_lines = demo_path.read_text(encoding="utf-8").splitlines()
    assert [l for i, l in enumerate(fixed.splitlines()) if i != 10] == [l for i, l in enumerate(original_lines) if i != 10], \
        "codemod must preserve all other lines byte-for-byte"

    spkb = parse_file(hello_path, backend="spoon")
    spcounts = {category: len(spkb["categories"][category]) for category in CATEGORIES}
    print("backend=spoon (fallback expected): "
          + " ".join(f"{c}={n}" for c, n in spcounts.items()), flush=True)
    assert set(spkb) == schema_keys and list(spkb["categories"]) == CATEGORIES
    assert any(e["name"] == "helper" for e in spkb["categories"]["calls"]), "spoon fallback: helper() call"
    assert _spoon_state["mvn_attempted"] and not (_SPOON_DIR / "target" / _SPOON_JAR).exists(), \
        "mvn was attempted once and no jar is available"
    try:
        parse_source("x = 1\n", backend="tree-sitter", language="klingon")
    except ValueError:
        pass
    else:
        raise AssertionError("unknown language must raise")
    try:
        parse_source("x = 1\n", backend="libcst", language="java")
    except ValueError:
        pass
    else:
        raise AssertionError("libcst on java must raise")
    print("Parser smoke test OK", flush=True)
