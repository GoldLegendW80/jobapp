"""Conservative AST proof of the click and mail transport call boundaries.

Resolve imports, lexical scopes and direct aliases instead of searching source text. This is
a source contract, not a Python sandbox: runtime reflection is outside its declared scope.
"""

import ast
from dataclasses import dataclass
from importlib.util import resolve_name


@dataclass(frozen=True)
class Call:
    module: str
    scope: str
    line: int
    kind: str


class Scan(ast.NodeVisitor):
    def __init__(self, module: str, exports: dict[str, str] | None = None):
        self.module = module
        self.scopes: list[str] = []
        self.names: list[dict[str, str]] = [{}]
        self.calls: list[Call] = []
        self.exports = exports or {}

    def resolve(self, name: str) -> str:
        seen = set()
        while name not in seen:
            seen.add(name)
            parts = name.split(".")
            for count in range(len(parts), 0, -1):
                prefix = ".".join(parts[:count])
                if prefix in self.exports:
                    name = ".".join([self.exports[prefix], *parts[count:]])
                    break
            else:
                return name
        return name

    def value(self, node: ast.AST) -> str:
        if isinstance(node, ast.Name):
            return self.resolve(
                next((s[node.id] for s in reversed(self.names) if node.id in s), node.id)
            )
        if isinstance(node, ast.Attribute):
            return self.resolve(f"{self.value(node.value)}.{node.attr}")
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return node.value
        if isinstance(node, ast.JoinedStr):
            return "".join(self.value(v) for v in node.values)
        if isinstance(node, ast.Call):
            if self.value(node.func) in {"getattr", "builtins.getattr"} and len(node.args) >= 2:
                return f"{self.value(node.args[0])}.{self.value(node.args[1])}"
            if self.value(node.func) == "functools.partial" and node.args:
                return self.value(node.args[0])
            return self.value(node.func)
        return ""

    def visit_Import(self, node: ast.Import) -> None:
        for name in node.names:
            self.names[-1][name.asname or name.name.split(".")[0]] = (
                name.name if name.asname else name.name.split(".")[0]
            )

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        module = node.module or ""
        if node.level:
            module = resolve_name("." * node.level + module, self.module.rpartition(".")[0])
        for name in node.names:
            self.names[-1][name.asname or name.name] = f"{module}.{name.name}"

    def bind(self, target: ast.AST, value: ast.AST) -> None:
        if isinstance(target, (ast.Tuple, ast.List)) and isinstance(value, (ast.Tuple, ast.List)):
            for left, right in zip(target.elts, value.elts, strict=False):
                self.bind(left, right)
        elif isinstance(target, (ast.Name, ast.Attribute)):
            key = target.id if isinstance(target, ast.Name) else self.value(target)
            self.names[-1][key] = self.value(value)

    def visit_Assign(self, node: ast.Assign) -> None:
        self.visit(node.value)
        for target in node.targets:
            self.bind(target, node.value)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        if node.value:
            self.visit(node.value)
            self.bind(node.target, node.value)

    def visit_FunctionDef(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        for item in [*node.decorator_list, *node.args.defaults, *node.args.kw_defaults]:
            if item:
                self.visit(item)
        self.scopes.append(node.name)
        # Parameters shadow imports in their lexical scope.
        args = [*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs]
        self.names.append(
            {arg.arg: self.value(arg.annotation) if arg.annotation else arg.arg for arg in args}
        )
        for item in node.body:
            self.visit(item)
        self.names.pop()
        self.scopes.pop()

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self.visit_FunctionDef(node)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self.names[-1][node.name] = f"{self.module}.{node.name}"
        self.scopes.append(node.name)
        self.names.append({})
        self.generic_visit(node)
        self.names.pop()
        self.scopes.pop()

    def visit_Call(self, node: ast.Call) -> None:
        name = self.value(node.func)
        name = next((s[name] for s in reversed(self.names) if name in s), name)
        values = [self.value(v) for v in node.args]
        values += [self.value(v.value) for v in node.keywords]
        kind = ""
        if name.rsplit(".", 1)[-1] == "gate_click":
            kind = "click"
        elif name.endswith(".call") or name.endswith(".call_tool"):
            if "send_message" in values:
                kind = "mail"
        elif name.endswith(".send_message") and not name.startswith(
            ("app.agents.", "app.documents.")
        ):
            kind = "mail"
        if name.startswith("app.gmailmcp.") and name.endswith((".send", ".send_message")):
            kind = "provider"
        if any("/messages/send" in value for value in values):
            kind = "provider"
        if kind:
            self.calls.append(Call(self.module, ".".join(self.scopes), node.lineno, kind))
        self.generic_visit(node)


def globals_of(visitor: Scan, tree: ast.Module) -> None:
    # Module globals are available when a function runs, including imports declared after it.
    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            visitor.visit(node)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                visitor.bind(target, node.value)


def scan(source: str, module: str, exports: dict[str, str] | None = None) -> list[Call]:
    visitor = Scan(module, exports)
    tree = ast.parse(source)
    globals_of(visitor, tree)
    visitor.visit(tree)
    return visitor.calls


def scan_package(sources: dict[str, str]) -> list[Call]:
    exports = {}
    for module, source in sources.items():
        visitor = Scan(module)
        globals_of(visitor, ast.parse(source))
        exports.update({f"{module}.{name}": value for name, value in visitor.names[0].items()})
    return [call for module, source in sources.items() for call in scan(source, module, exports)]


def permitted(call: Call) -> bool:
    if call.kind == "click":
        return call.module in {"app.apply.gate", "app.apply.profile_gate"} or (
            call.module == "app.browser.tools" and call.scope == "gate_click"
        )
    if call.kind == "mail":
        return call.module == "app.mail.gate"
    return call.module == "app.gmailmcp.server" and call.scope in {
        "Service.send",
        "build_app.send_message",
    }
