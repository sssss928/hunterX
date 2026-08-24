from __future__ import annotations

import ast
from collections import Counter
import importlib.util
from pathlib import Path
import sys


REPO_ROOT = Path(__file__).resolve().parents[1]
AUDIT_PATH = REPO_ROOT / "scripts" / "audit_browser_exception_handlers.py"
TICKETPLUS_PATH = REPO_ROOT / "src" / "platforms" / "ticketplus.py"
COMMON_PATH = REPO_ROOT / "src" / "nodriver_common.py"
GLOBAL_MINIMUM_FINDINGS = {
    "src/nodriver_common.py": 20,
    "src/nodriver_tixcraft.py": 6,
    "src/platforms/cityline.py": 26,
    "src/platforms/facebook.py": 1,
    "src/platforms/famiticket.py": 9,
    "src/platforms/fansigo.py": 10,
    "src/platforms/funone.py": 18,
    "src/platforms/hkticketing.py": 67,
    "src/platforms/ibon.py": 77,
    "src/platforms/kham.py": 103,
    "src/platforms/kktix.py": 44,
    "src/platforms/ticketplus.py": 30,
    "src/platforms/tixcraft.py": 55,
}
CITYLINE_PATH = REPO_ROOT / "src" / "platforms" / "cityline.py"
FAMITICKET_PATH = REPO_ROOT / "src" / "platforms" / "famiticket.py"


def _load_audit_module():
    spec = importlib.util.spec_from_file_location(
        "hunterx_browser_exception_audit",
        AUDIT_PATH,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _handler_returns_success(handler: ast.ExceptHandler) -> bool:
    for node in ast.walk(ast.Module(body=handler.body, type_ignores=[])):
        if not isinstance(node, ast.Return) or node.value is None:
            continue
        if isinstance(node.value, ast.Constant) and node.value.value is True:
            return True
        if not isinstance(node.value, ast.Dict):
            continue
        for key, value in zip(node.value.keys, node.value.values):
            if (
                isinstance(key, ast.Constant)
                and str(key.value).casefold() in {"success", "submitted", "completed"}
                and isinstance(value, ast.Constant)
                and value.value is True
            ):
                return True
    return False


def test_ticketplus_browser_interaction_handlers_have_terminal_escalation() -> None:
    """P1-R2-07 lint: DOM fallbacks remain, terminal loss cannot be swallowed."""

    audit = _load_audit_module()
    findings = audit.audit_source(TICKETPLUS_PATH)
    assert len(findings) >= 25, "audit stopped discovering TicketPlus browser handlers"
    unresolved = [
        (item.function, item.handler_line, item.interactions)
        for item in findings
        if item.disposition == "manual_review_required"
    ]
    assert unresolved == []


def test_common_browser_interaction_handlers_have_terminal_escalation() -> None:
    audit = _load_audit_module()
    findings = audit.audit_source(COMMON_PATH)
    assert len(findings) >= 20, "audit stopped discovering shared browser handlers"
    unresolved = [
        (item.function, item.handler_line, item.interactions)
        for item in findings
        if item.disposition == "manual_review_required"
    ]
    assert unresolved == []


def test_reviewed_platform_browser_handlers_have_terminal_escalation() -> None:
    audit = _load_audit_module()
    for path, minimum in ((CITYLINE_PATH, 26), (FAMITICKET_PATH, 9)):
        findings = audit.audit_source(path)
        assert len(findings) >= minimum
        unresolved = [
            (item.function, item.handler_line, item.interactions)
            for item in findings
            if item.disposition == "manual_review_required"
        ]
        assert unresolved == [], path.name


def test_browser_exception_fallback_never_returns_literal_success() -> None:
    """Programming errors must not be silently converted into a success result."""

    tree = ast.parse(TICKETPLUS_PATH.read_text(encoding="utf-8"))
    offenders = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Try):
            continue
        for handler in node.handlers:
            if _handler_returns_success(handler):
                offenders.append(handler.lineno)
    assert offenders == []


def test_global_browser_exception_audit_has_no_unclassified_handler() -> None:
    """Round 2 closure gate for every source path enumerated by the auditor."""

    audit = _load_audit_module()
    findings = audit.audit_paths(audit.default_source_paths())
    assert len(findings) >= 466

    counts = Counter(finding.path for finding in findings)
    assert all(
        counts[path] >= minimum
        for path, minimum in GLOBAL_MINIMUM_FINDINGS.items()
    )
    assert [
        (finding.path, finding.function, finding.handler_line)
        for finding in findings
        if finding.disposition == "manual_review_required"
    ] == []
    assert all(finding.disposition == "terminal_classifier" for finding in findings)


def test_global_audited_fallbacks_never_return_literal_success() -> None:
    audit = _load_audit_module()
    findings = audit.audit_paths(audit.default_source_paths())
    findings_by_path: dict[str, list] = {}
    for finding in findings:
        findings_by_path.setdefault(finding.path, []).append(finding)

    offenders = []
    for relative_path, path_findings in findings_by_path.items():
        source_path = REPO_ROOT / Path(relative_path)
        tree = ast.parse(
            source_path.read_text(encoding="utf-8"),
            filename=str(source_path),
        )
        handlers = {
            handler.lineno: handler
            for node in ast.walk(tree)
            if isinstance(node, ast.Try)
            for handler in node.handlers
        }
        for finding in path_findings:
            handler = handlers[finding.handler_line]
            if _handler_returns_success(handler):
                offenders.append(
                    (relative_path, finding.function, finding.handler_line)
                )
    assert offenders == []
