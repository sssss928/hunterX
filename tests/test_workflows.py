from __future__ import annotations

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def test_release_workflow_has_required_triggers_permissions_and_artifact() -> None:
    workflow = (REPO_ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8")

    assert 'tags:' in workflow
    assert '"v*-rc3"' in workflow
    assert "workflow_dispatch:" in workflow
    assert "contents: read" in workflow
    assert "contents: write" in workflow
    assert '"HunterX v$Version RC3"' in workflow
    assert "--prerelease" in workflow
    assert 'Tag="v$Version-rc3"' in workflow
    assert "artifact-name" in workflow
    assert "checksum-name" in workflow
    assert "validate-project-version" in workflow
    assert "--platform source" in workflow
    assert "publish-release" in workflow
    assert "fetch-depth: 0" in workflow
    assert "scripts/build_source_archive.py" in workflow
    assert "verify_release_archive.py pair" in workflow
    assert "--windows-archive" in workflow
    assert "--source-archive" in workflow
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
    assert "rc2_release_tag:" in workflow
    assert '$BaseReleaseTag = "v0.5.2-rc2"' in workflow
    assert "gh release download $BaseReleaseTag" in workflow
    assert "hunterX_windows_0.5.2_rc2.zip" in workflow
    assert "47747a962cf5c4ae49654aec574ca64ac52c27032fc5b1ec1f70d83c3d09da48" in workflow
    assert '-BaseArchive "dist/base/hunterX_windows_0.5.2_rc2.zip"' in workflow
    assert '-Commit "${{ steps.commit.outputs.release_commit }}"' in workflow
    assert "-Qualifier rc3" in workflow
    assert "hunterX_windows_0.5.1.zip" not in workflow
    assert "hunterX_windows_0.4.9.zip" not in workflow
    assert "RELEASE_INPUT_VERSION: ${{ inputs.version }}" in workflow
    assert '--input-version "${{ inputs.version }}"' not in workflow
    assert "0.1.0" not in workflow

    build_start = workflow.index("  build-assets:")
    publish_start = workflow.index("  publish-release:")
    build_section = workflow[build_start:publish_start]
    publish_section = workflow[publish_start:]

    # The Windows runner owns only the Windows binary.  Building the source
    # archive on the Ubuntu publish runner avoids cross-OS git-archive byte
    # conversion mismatches while still pinning everything to release_commit.
    assert "Build source package" not in build_section
    assert "write_release_checksums.py" not in build_section
    assert "verified-windows-release-" in build_section
    assert "Build source package from exact release commit" in publish_section
    assert "Write release checksum manifest" in publish_section
    assert "Verify RC3 release assets" in publish_section
    assert publish_section.index("Build source package from exact release commit") < publish_section.index(
        "Write release checksum manifest"
    ) < publish_section.index("Verify RC3 release assets")

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
    assert "gh release download v0.5.2-rc2" in workflow
    assert "hunterX_windows_0.5.2_rc2.zip" in workflow
    assert "47747a962cf5c4ae49654aec574ca64ac52c27032fc5b1ec1f70d83c3d09da48" in workflow
    assert "hunterX_windows_0.5.1.zip" not in workflow
    assert "--qualifier rc3" in workflow
    assert workflow.count("continue-on-error: true") == 3
    assert workflow.count("retention-days: 3") == 3
    assert "0.1.0" not in workflow

def test_windows_build_script_requires_metadata_version_match() -> None:
    script = (REPO_ROOT / "scripts/build_windows.ps1").read_text(encoding="utf-8")

    validation = script.index("validate-project-version")
    baseline_check = script.index("Test-Path -LiteralPath $ResolvedBaseArchive")
    first_build = script.index("python scripts/build_windows_from_base.py")

    assert "--metadata src/hunter_metadata.py" in script
    assert "[string] $BaseArchive" in script
    assert validation < first_build
    assert baseline_check < first_build
    assert "--base-archive $ResolvedBaseArchive" in script
    assert "--commit $Commit" in script
    assert "--qualifier $Qualifier" in script
    assert "verify_windows_shell_zip.js" in script

    baseline_builder = (REPO_ROOT / "scripts/build_windows_from_base.py").read_text(
        encoding="utf-8"
    )
    assert "ROUND1_RC_ARCHIVE_NAME" in baseline_builder
    assert "ROUND1_RC_SHA256" in baseline_builder
    assert "require_candidate_qualifier" in baseline_builder
    assert "resolve_clean_commit" in baseline_builder
    assert "write_rc3_provenance" in baseline_builder
    assert "verify_archive_package" in baseline_builder
    assert "stage_application_source" in baseline_builder
    assert "repack_entrypoints" in baseline_builder
    assert "verify_windows_archive" in baseline_builder

    build_and_test = (REPO_ROOT / "build_scripts/build_and_test.bat").read_text(
        encoding="utf-8"
    )
    assert "--require-hashes -r requirements-lock-windows-py311.txt" in build_and_test
