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
    assert "validate-project-version" in workflow
    assert "--platform source" in workflow
    assert 'SOURCE_PREFIX="$(python scripts/release_utils.py source-prefix' in workflow
    assert '--prefix="$SOURCE_PREFIX"' in workflow
    assert "build-source" in workflow
    assert "verify_release_archive.py source" in workflow
    assert "needs.validate.outputs.source_artifact_name" in workflow
    assert "Download source package" in workflow
    assert "RELEASE_INPUT_VERSION: ${{ inputs.version }}" in workflow
    assert '--input-version "${{ inputs.version }}"' not in workflow
    assert "0.1.0" not in workflow


def test_ci_workflow_covers_required_branch_families() -> None:
    workflow = (REPO_ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")

    for branch in ["main", "feature/**", "fix/**", "chore/**", "refactor/**", "docs/**"]:
        assert branch in workflow
    assert 'python-version: "3.11.9"' in workflow
    assert "ruff check src tests scripts" in workflow
    assert "pip-audit -r requirement.txt" in workflow
    assert "project-version --metadata src/hunter_metadata.py" in workflow
    assert 'steps.project.outputs.version' in workflow
    assert 'steps.project.outputs.artifact_name' in workflow
    assert "0.1.0" not in workflow


def test_windows_build_script_requires_metadata_version_match() -> None:
    script = (REPO_ROOT / "scripts/build_windows.ps1").read_text(encoding="utf-8")

    validation = script.index("validate-project-version")
    first_build = script.index("python -m PyInstaller")
    first_delete = script.index("Remove-Item")

    assert "--metadata src/hunter_metadata.py" in script
    assert validation < first_build
    assert validation < first_delete
    assert "verify_release_archive.py windows" in script
