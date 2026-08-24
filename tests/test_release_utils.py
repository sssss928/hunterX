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


def test_resolve_version_from_rc2_tag_is_exact() -> None:
    assert release_utils.resolve_version("push", "v0.5.2-rc2", qualifier="rc2") == "0.5.2"
    with pytest.raises(ValueError, match="must end"):
        release_utils.resolve_version("push", "v0.5.2-final", qualifier="rc2")


def test_manual_dispatch_rejects_leading_v() -> None:
    with pytest.raises(ValueError):
        release_utils.resolve_version("workflow_dispatch", "main", "v0.1.0")


def test_artifact_name_is_safe() -> None:
    assert release_utils.artifact_name("0.1.0") == "hunterX_windows_0.1.0.zip"
    assert release_utils.artifact_name("0.1.0", "source") == "hunterX_source_0.1.0.zip"
    assert " " not in release_utils.artifact_name("0.1.0")
    assert "-" not in release_utils.artifact_name("0.1.0")
    assert (
        release_utils.artifact_name("0.5.2", qualifier="rc2")
        == "hunterX_windows_0.5.2_rc2.zip"
    )
    assert (
        release_utils.artifact_name("0.5.2", "source", "rc2")
        == "hunterX_source_0.5.2_rc2.zip"
    )


def test_checksum_name_is_versioned_and_safe() -> None:
    assert release_utils.checksum_name("0.1.0") == "SHA256SUMS_v0.1.0.txt"
    with pytest.raises(ValueError):
        release_utils.checksum_name("v0.1.0")
    assert release_utils.checksum_name("0.5.2", "rc2") == "SHA256SUMS_v0.5.2_RC2.txt"


def test_rc2_profile_rejects_final_or_missing_qualifier() -> None:
    assert release_utils.require_rc2_qualifier("RC2") == "rc2"
    for qualifier in (None, "rc", "final"):
        with pytest.raises(ValueError, match="requires qualifier 'rc2'"):
            release_utils.require_rc2_qualifier(qualifier)


def test_source_archive_prefix_has_one_versioned_root() -> None:
    assert release_utils.source_archive_prefix("0.4.4") == "hunterX-0.4.4/"


def test_project_version_reads_literal_without_importing(tmp_path: Path) -> None:
    metadata = tmp_path / "hunter_metadata.py"
    metadata.write_text(
        'raise RuntimeError("must not execute")\nAPP_VERSION = "0.4.4"\n',
        encoding="utf-8",
    )

    assert release_utils.project_version(metadata) == "0.4.4"


@pytest.mark.parametrize(
    "source",
    [
        "",
        'APP_VERSION = "0.4.4"\nAPP_VERSION = "0.4.7"\n',
        'APP_VERSION = ".".join(("0", "4", "4"))\n',
        "APP_VERSION = 44\n",
        'APP_VERSION = "v0.4.4"\n',
        'APP_VERSION = " 0.4.4 "\n',
    ],
)
def test_project_version_fails_closed_for_ambiguous_or_invalid_metadata(
    tmp_path: Path,
    source: str,
) -> None:
    metadata = tmp_path / "hunter_metadata.py"
    metadata.write_text(source, encoding="utf-8")

    with pytest.raises(ValueError):
        release_utils.project_version(metadata)


def test_validate_project_version_requires_exact_match(tmp_path: Path) -> None:
    metadata = tmp_path / "hunter_metadata.py"
    metadata.write_text('APP_VERSION = "0.4.4"\n', encoding="utf-8")

    assert release_utils.validate_project_version("0.4.4", metadata) == "0.4.4"
    with pytest.raises(ValueError, match="Release version mismatch"):
        release_utils.validate_project_version("0.4.3", metadata)


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
