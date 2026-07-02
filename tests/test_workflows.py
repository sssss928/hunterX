from __future__ import annotations

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def test_release_workflow_has_required_triggers_permissions_and_artifact() -> None:
    workflow = (REPO_ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8")

    assert 'tags:' in workflow
    assert '"v*"' in workflow
    assert "workflow_dispatch:" in workflow
    assert "contents: read" in workflow
    assert "contents: write" in workflow
    assert "HunterX v${{ needs.validate.outputs.version }}" in workflow
    assert "artifact-name" in workflow


def test_ci_workflow_covers_required_branch_families() -> None:
    workflow = (REPO_ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")

    for branch in ["main", "feature/**", "fix/**", "chore/**", "refactor/**", "docs/**"]:
        assert branch in workflow
    assert 'python-version: "3.11.9"' in workflow
    assert "ruff check src tests scripts" in workflow
    assert "pip-audit -r requirement.txt" in workflow
