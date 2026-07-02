from __future__ import annotations

from pathlib import Path

import pytest

import release_utils


def test_semver_validation_accepts_expected_version() -> None:
    assert release_utils.validate_semver("0.1.0") == "0.1.0"


@pytest.mark.parametrize("version", ["v 1.0", "v-1.0", "-1.0", "v1.0.0", "1.0"])
def test_semver_validation_rejects_invalid_versions(version: str) -> None:
    with pytest.raises(ValueError):
        release_utils.validate_semver(version)


def test_resolve_version_from_tag() -> None:
    assert release_utils.resolve_version("push", "v0.1.0") == "0.1.0"


def test_manual_dispatch_rejects_leading_v() -> None:
    with pytest.raises(ValueError):
        release_utils.resolve_version("workflow_dispatch", "main", "v0.1.0")


def test_artifact_name_is_safe() -> None:
    assert release_utils.artifact_name("0.1.0") == "hunterX_windows_0.1.0.zip"
    assert " " not in release_utils.artifact_name("0.1.0")
    assert "-" not in release_utils.artifact_name("0.1.0")


def test_changelog_notes_extract_matching_section(tmp_path: Path) -> None:
    changelog = tmp_path / "CHANGELOG.md"
    changelog.write_text(
        "# Changelog\n\n## v0.1.0\n\n- first fork release\n\n## v0.0.1\n\n- old\n",
        encoding="utf-8",
    )

    assert release_utils.extract_changelog("0.1.0", changelog) == "- first fork release"


def test_changelog_notes_fallback_does_not_fail(tmp_path: Path) -> None:
    notes = release_utils.extract_changelog("0.1.0", tmp_path / "missing.md")
    assert "HunterX v0.1.0" in notes
