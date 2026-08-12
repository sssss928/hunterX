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
    assert '"HunterX v$Version"' in workflow
    assert "artifact-name" in workflow
    assert "checksum-name" in workflow
    assert "validate-project-version" in workflow
    assert "--platform source" in workflow
    assert "publish-release" in workflow
    assert "fetch-depth: 0" in workflow
    assert "scripts/build_source_archive.py" in workflow
    assert "scripts/write_release_checksums.py" in workflow
    assert "needs.validate.outputs.source_artifact_name" in workflow
    assert "needs.validate.outputs.checksum_name" in workflow
    assert "gh release create" in workflow
    assert "gh release upload" in workflow
    assert "--clobber" in workflow
    assert "build-assets:" in workflow
    assert "actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02" in workflow
    assert "actions/download-artifact@d3f86a106a0bac45b974a628896c90dbdf5c8093" in workflow
    assert "needs.build-assets.outputs.release_commit" in workflow
    assert "--verify-manifest" in workflow
    assert "RELEASE_INPUT_VERSION: ${{ inputs.version }}" in workflow
    assert '--input-version "${{ inputs.version }}"' not in workflow
    assert "0.1.0" not in workflow

    source_builder = (REPO_ROOT / "scripts/build_source_archive.py").read_text(encoding="utf-8")
    assert "verify_source_archive" in source_builder


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
    assert "--require-hashes -r requirements-lock-windows-py311.txt" in workflow
    assert "pytest --cov-fail-under=30" in workflow
    assert workflow.count("continue-on-error: true") == 3
    assert workflow.count("retention-days: 3") == 3
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

    build_and_test = (REPO_ROOT / "build_scripts/build_and_test.bat").read_text(
        encoding="utf-8"
    )
    assert "--require-hashes -r requirements-lock-windows-py311.txt" in build_and_test
