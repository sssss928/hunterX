#!/usr/bin/env python3
"""Inventory broad exception handlers around browser interaction.

This is a review aid and regression primitive, not an automatic source
rewriter.  Expected DOM absence can legitimately use a local fallback, while
transport/target loss must reach the lifecycle owner.  The report therefore
keeps unclassified handlers visible instead of guessing that they are safe.
"""

from __future__ import annotations

import argparse
import ast
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE_PATHS = (
    REPO_ROOT / "src" / "nodriver_common.py",
    REPO_ROOT / "src" / "nodriver_tixcraft.py",
)
BROWSER_METHODS = frozenset(
    {
        "activate",
        "apply",
        "click",
        "close",
        "evaluate",
        "find",
        "get",
        "get_content",
        "get_html",
        "query_selector",
        "query_selector_all",
        "reload",
        "send",
        "send_keys",
        "update_targets",
        "wait_for",
    }
)
TERMINAL_CLASSIFIERS = frozenset(
    {
        "classify_browser_exception",
        "is_browser_connection_closed_error",
        "raise_if_terminal_browser_error",
    }
)


@dataclass(frozen=True, slots=True)
class BrowserExceptionFinding:
    path: str
    function: str
    try_line: int
    handler_line: int
    exception_type: str
    interactions: tuple[str, ...]
    disposition: str


def _call_name(call: ast.Call) -> str:
    function = call.func
    if isinstance(function, ast.Attribute):
        return function.attr
    if isinstance(function, ast.Name):
        return function.id
    return ""


def _root_name(expression: ast.AST) -> str:
    current = expression
    while isinstance(current, (ast.Attribute, ast.Subscript)):
        current = current.value
    return current.id if isinstance(current, ast.Name) else ""


class _DirectBrowserInteractionVisitor(ast.NodeVisitor):
    """Visit one try body without attributing nested try handlers twice."""

    def __init__(self) -> None:
        self.interactions: set[str] = set()

    def visit_Try(self, _node: ast.Try) -> None:  # noqa: N802
        return

    def visit_Await(self, node: ast.Await) -> None:  # noqa: N802
        call = node.value
        if isinstance(call, ast.Call):
            method = _call_name(call)
            root = ""
            if isinstance(call.func, ast.Attribute):
                root = _root_name(call.func.value)
            if (
                root in {"tab", "driver"}
                or method in BROWSER_METHODS
                or method in {"guarded_get", "guarded_reload"}
            ):
                self.interactions.add(f"await:{root or 'object'}.{method}")
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:  # noqa: N802
        if isinstance(node.func, ast.Attribute):
            root = _root_name(node.func.value)
            if root == "cdp":
                self.interactions.add(f"cdp:{node.func.attr}")
        self.generic_visit(node)


def _handler_exception_type(handler: ast.ExceptHandler) -> str:
    if handler.type is None:
        return "bare"
    try:
        return ast.unparse(handler.type)
    except (AttributeError, ValueError):
        return type(handler.type).__name__


def _is_broad_handler(handler: ast.ExceptHandler) -> bool:
    if handler.type is None:
        return True
    if isinstance(handler.type, ast.Name):
        return handler.type.id in {"Exception", "BaseException"}
    if isinstance(handler.type, ast.Tuple):
        names = {
            item.id
            for item in handler.type.elts
            if isinstance(item, ast.Name)
        }
        return bool(names & {"Exception", "BaseException"})
    return False


def _handler_disposition(handler: ast.ExceptHandler) -> str:
    module = ast.Module(body=handler.body, type_ignores=[])
    calls = {
        _call_name(node)
        for node in ast.walk(module)
        if isinstance(node, ast.Call)
    }
    if calls & TERMINAL_CLASSIFIERS:
        return "terminal_classifier"
    if any(isinstance(node, ast.Raise) for node in ast.walk(module)):
        return "explicit_reraise"
    if any("submit_outcome_unknown" in name for name in calls):
        return "submit_fail_closed"
    return "manual_review_required"


def _function_for_node(
    node: ast.AST,
    parents: dict[ast.AST, ast.AST],
) -> str:
    current = parents.get(node)
    while current is not None:
        if isinstance(current, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return current.name
        current = parents.get(current)
    return "<module>"


def audit_source(path: Path, *, repo_root: Path = REPO_ROOT) -> list[BrowserExceptionFinding]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    parents = {
        child: parent
        for parent in ast.walk(tree)
        for child in ast.iter_child_nodes(parent)
    }
    try:
        relative = path.resolve().relative_to(repo_root.resolve()).as_posix()
    except ValueError:
        relative = path.resolve().as_posix()
    findings: list[BrowserExceptionFinding] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Try):
            continue
        visitor = _DirectBrowserInteractionVisitor()
        for statement in node.body:
            visitor.visit(statement)
        if not visitor.interactions:
            continue
        function = _function_for_node(node, parents)
        for handler in node.handlers:
            if not _is_broad_handler(handler):
                continue
            findings.append(
                BrowserExceptionFinding(
                    path=relative,
                    function=function,
                    try_line=node.lineno,
                    handler_line=handler.lineno,
                    exception_type=_handler_exception_type(handler),
                    interactions=tuple(sorted(visitor.interactions)),
                    disposition=_handler_disposition(handler),
                )
            )
    return findings


def default_source_paths() -> tuple[Path, ...]:
    platform_paths = tuple(sorted((REPO_ROOT / "src" / "platforms").glob("*.py")))
    return DEFAULT_SOURCE_PATHS + platform_paths


def audit_paths(paths: Iterable[Path]) -> list[BrowserExceptionFinding]:
    findings: list[BrowserExceptionFinding] = []
    for path in paths:
        findings.extend(audit_source(path))
    return sorted(
        findings,
        key=lambda item: (item.path, item.handler_line, item.function),
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("paths", nargs="*", type=Path)
    args = parser.parse_args()
    findings = audit_paths(args.paths or default_source_paths())
    payload = {
        "schema": "hunterx-browser-exception-audit-v1",
        "finding_count": len(findings),
        "disposition_counts": {
            disposition: sum(
                finding.disposition == disposition for finding in findings
            )
            for disposition in sorted({finding.disposition for finding in findings})
        },
        "findings": [asdict(finding) for finding in findings],
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
