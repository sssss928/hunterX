from __future__ import annotations

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
RELEASE_WORKFLOW = REPO_ROOT / ".github/workflows/release.yml"


def test_only_one_active_release_workflow_exists() -> None:
    release_workflows = sorted(
        path.name for path in (REPO_ROOT / ".github/workflows").glob("release*.yml")
    )

    assert release_workflows == ["release.yml"]


def test_release_workflow_is_source_native_and_dry_run_by_default() -> None:
    workflow = RELEASE_WORKFLOW.read_text(encoding="utf-8")
    folded = workflow.casefold()

    assert "name: Release v0.5.2" in workflow
    assert "workflow_dispatch:" in workflow
    assert "publish:" in workflow
    assert "type: boolean" in workflow
    assert "default: false" in workflow
    assert "push:" not in workflow
    assert "environment: production-release" in workflow
    assert "scripts/build_windows_final.ps1" in workflow
    assert "scripts/build_source_archive.py" in workflow
    assert "verify_release_archive.py windows" in workflow
    assert "verify_release_archive.py source" in workflow
    assert workflow.count("verify_release_archive.py pair") >= 2
    assert "scripts/write_release_checksums.py" in workflow
    assert 'Tag="v$Version"' in workflow
    assert '"HunterX v$Version"' in workflow
    assert "--prerelease" not in workflow
    assert "gh release create" in workflow
    assert "gh release upload" not in workflow
    assert "refusing to replace an existing immutable release or tag" in folded
    assert "contents: write" in workflow
    assert "actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02" in workflow
    assert "actions/download-artifact@d3f86a106a0bac45b974a628896c90dbdf5c8093" in workflow

    for forbidden in (
        "gh release download",
        "basearchive",
        "rc2_release_tag",
        "rc3_release_tag",
        "hunterx_windows_0.5.2_rc2.zip",
        "hunterx_windows_0.5.2_rc3.zip",
    ):
        assert forbidden not in folded


def test_release_workflow_preserves_full_quality_and_qualification_gates() -> None:
    workflow = RELEASE_WORKFLOW.read_text(encoding="utf-8")

    assert "python -m compileall src tests scripts" in workflow
    assert "ruff check src tests scripts" in workflow
    assert "mypy" in workflow
    assert "python -m pytest --cov-fail-under=30" in workflow
    assert (
        "python -m pytest tests/benchmarks --benchmark-enable --benchmark-only"
        in workflow
    )
    assert "pip-audit -r requirement.txt" in workflow
    assert "bandit -r src scripts -lll -c pyproject.toml" in workflow
    assert "FINAL_8H_SINGLE_INSTANCE_SOAK.json" in workflow
    assert "FINAL_8H_THREE_NAMED_INSTANCES_SOAK.json" in workflow
    assert "FINAL_QUALIFICATION_CONTEXT.json" in workflow
    assert "FINAL_8H_SOAK_WAIVER.json" in workflow
    assert "scripts/final_qualification.py verify" in workflow
    assert "scripts/final_qualification.py verify-waiver" in workflow
    assert "exactly one complete mode" in workflow
    assert '--require-hashes -r requirements-lock-windows-py311.txt' in workflow
    assert 'python-version: "3.11.9"' in workflow
    assert "continue-on-error: true" not in workflow


def test_ci_workflow_covers_release_branches_and_builds_real_package() -> None:
    workflow = (REPO_ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")

    for branch in (
        "main",
        "feature/**",
        "fix/**",
        "chore/**",
        "refactor/**",
        "docs/**",
        "develop",
        "release/**",
        "hotfix/**",
    ):
        assert branch in workflow
    assert 'python-version: "3.11.9"' in workflow
    assert "ruff check src tests scripts" in workflow
    assert "pip-audit -r requirement.txt" in workflow
    assert "--require-hashes -r requirements-lock-windows-py311.txt" in workflow
    assert "python -m compileall src tests scripts" in workflow
    assert "python -m pytest --cov-fail-under=30" in workflow
    assert "Windows runtime and release-contract smoke" in workflow
    assert "Build source-native Windows package smoke" in workflow
    assert "scripts/build_windows_final.ps1" in workflow
    assert "python src/nodriver_tixcraft.py --version" in workflow
    assert "python src/settings.py --version" in workflow
    assert "gh release download" not in workflow
    assert "continue-on-error: true" not in workflow
