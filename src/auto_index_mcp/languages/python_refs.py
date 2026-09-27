"""Scope-local Python references, including executable formatted strings."""
from __future__ import annotations

import ast

DEFINITIONS = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
BRANCHES = (ast.If, ast.IfExp, ast.For, ast.AsyncFor, ast.While, ast.ExceptHandler)


class References(ast.NodeVisitor):
    def __init__(self, aliases: dict[str, str], parameters: set[str] | None = None) -> None:
        self.aliases = dict(aliases)
        self.shadowed = set(parameters or ())
        self.calls: dict[str, None] = dict()
        self.refs: dict[str, None] = dict()
        self.complexity = 1

    def name(self, node: ast.AST) -> str:
        if isinstance(node, ast.Name):
            if node.id in self.shadowed:
                return ""
            return self.aliases.get(node.id, node.id)
        if isinstance(node, ast.Attribute):
            return node.attr
        return ""

    def visit_Call(self, node: ast.Call) -> None:
        name = self.name(node.func)
        if name:
            self.calls[name] = None
        self.generic_visit(node)

    def visit_Name(self, node: ast.Name) -> None:
        if isinstance(node.ctx, ast.Load):
            name = self.name(node)
            if name and name not in ("self", "cls"):
                self.refs[name] = None

    def visit_Attribute(self, node: ast.Attribute) -> None:
        if isinstance(node.ctx, ast.Load):
            self.refs[node.attr] = None
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        for alias in node.names:
            self.aliases[alias.asname or alias.name] = alias.name
            if alias.asname:
                self.refs[alias.name] = None

    def visit_Assign(self, node: ast.Assign) -> None:
        self.visit(node.value)
        name = self.name(node.value)
        for target in node.targets:
            if isinstance(target, ast.Name):
                if name:
                    self.aliases[target.id] = name
                    self.shadowed.discard(target.id)
                else:
                    self.shadowed.add(target.id)
                    self.aliases.pop(target.id, None)
            else:
                self.visit(target)

    def visit_FunctionDef(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        self.visit_header(node)
        if node.decorator_list:
            self.refs[node.name] = None

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self.visit_header(node)
        if node.decorator_list:
            self.refs[node.name] = None

    def visit_header(self, node: ast.AST) -> None:
        for field, value in ast.iter_fields(node):
            if field == "body":
                continue
            if isinstance(value, ast.AST):
                self.visit(value)
            elif isinstance(value, list):
                for child in value:
                    if isinstance(child, ast.AST):
                        self.visit(child)

    def generic_visit(self, node: ast.AST) -> None:
        if isinstance(node, BRANCHES):
            self.complexity += 1
        elif isinstance(node, ast.BoolOp):
            self.complexity += len(node.values) - 1
        super().generic_visit(node)


def scope_references(node: ast.AST, aliases: dict[str, str]) -> References:
    args = getattr(node, "args", None)
    parameters = {arg.arg for arg in ast.walk(args) if isinstance(arg, ast.arg)} if args else set()
    refs = References(aliases)
    refs.visit_header(node)
    refs.shadowed.update(parameters)
    for stmt in getattr(node, "body", []):
        refs.visit(stmt)
    return refs
