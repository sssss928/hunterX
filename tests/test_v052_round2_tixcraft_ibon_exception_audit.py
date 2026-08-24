from __future__ import annotations

from pathlib import Path

import pytest

from scripts.audit_browser_exception_handlers import REPO_ROOT, audit_source


@pytest.mark.parametrize(
    ("source_path", "minimum_findings"),
    (
        (Path(REPO_ROOT, "src", "platforms", "tixcraft.py"), 55),
        (Path(REPO_ROOT, "src", "platforms", "ibon.py"), 77),
    ),
)
def test_platform_browser_handlers_propagate_terminal_errors_before_fallback(
    source_path: Path,
    minimum_findings: int,
) -> None:
    findings = audit_source(source_path, repo_root=REPO_ROOT)

    assert len(findings) >= minimum_findings
    unresolved = [
        (finding.function, finding.handler_line, finding.interactions)
        for finding in findings
        if finding.disposition == "manual_review_required"
    ]
    assert unresolved == []
    assert all(
        finding.disposition == "terminal_classifier" for finding in findings
    )
