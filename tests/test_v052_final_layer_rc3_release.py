from __future__ import annotations

import json
from pathlib import Path

import pytest

import release_utils
from build_windows_from_base import (
    RC2_WINDOWS_BASE_NAME,
    RC2_WINDOWS_BASE_SHA256,
    RC3_PROVENANCE_NAME,
    RC3_REQUIRED_DOCUMENTS,
    extract_verified_baseline,
    overlay_release_files,
    write_rc3_provenance,
)
from write_release_checksums import verify_rc3_checksums, write_checksums


VERSION = "0.5.2"


def test_rc3_names_and_tag_are_exact() -> None:
    assert release_utils.require_rc3_qualifier("RC3") == "rc3"
    assert (
        release_utils.resolve_version("push", "v0.5.2-rc3", qualifier="rc3")
        == VERSION
    )
    assert (
        release_utils.artifact_name(VERSION, "windows", "rc3")
        == "hunterX_windows_0.5.2_rc3.zip"
    )
    assert (
        release_utils.artifact_name(VERSION, "source", "rc3")
        == "hunterX_source_0.5.2_rc3.zip"
    )
    assert release_utils.checksum_name(VERSION, "rc3") == "SHA256SUMS_v0.5.2_RC3.txt"


@pytest.mark.parametrize("qualifier", [None, "rc", "rc2", "final"])
def test_rc3_profile_rejects_every_other_qualifier(qualifier: str | None) -> None:
    with pytest.raises(ValueError, match="requires qualifier 'rc3'"):
        release_utils.require_rc3_qualifier(qualifier)


def test_rc3_baseline_rejects_round1_before_hash_check(tmp_path: Path) -> None:
    archive = tmp_path / "hunterX_windows_0.5.2_rc.zip"
    archive.write_bytes(b"not-used")
    with pytest.raises(ValueError, match="RC3 builds require the verified Windows base"):
        extract_verified_baseline(archive, tmp_path / "out", qualifier="rc3")


def test_rc3_baseline_contract_is_exact_name_and_hash() -> None:
    assert RC2_WINDOWS_BASE_NAME == "hunterX_windows_0.5.2_rc2.zip"
    assert RC2_WINDOWS_BASE_SHA256 == (
        "47747a962cf5c4ae49654aec574ca64ac52c27032fc5b1ec1f70d83c3d09da48"
    )


def test_rc3_provenance_records_both_unverified_eight_hour_gates(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    base = tmp_path / RC2_WINDOWS_BASE_NAME
    base.write_bytes(b"immutable RC2 fixture")
    monkeypatch.setattr(
        "build_windows_from_base.sha256_file",
        lambda _path: RC2_WINDOWS_BASE_SHA256,
    )
    provenance_path = write_rc3_provenance(
        tmp_path,
        version=VERSION,
        source_commit="a" * 40,
        base_archive=base,
    )
    assert provenance_path.name == RC3_PROVENANCE_NAME
    payload = json.loads(provenance_path.read_text(encoding="ascii"))
    assert payload["qualifier"] == "rc3"
    assert payload["windows_base_name"] == RC2_WINDOWS_BASE_NAME
    assert payload["windows_base_sha256"] == RC2_WINDOWS_BASE_SHA256
    assert payload["eight_hour_single_instance_soak_verified"] is False
    assert payload["eight_hour_three_named_instances_soak_verified"] is False
    assert payload["eight_hour_soak_verified"] is False
    assert payload["final_eligible"] is False


def test_rc3_overlay_requires_every_final_layer_report(tmp_path: Path) -> None:
    project = tmp_path / "project"
    package = tmp_path / "package"
    (project / "src").mkdir(parents=True)
    package.mkdir()
    with pytest.raises(FileNotFoundError, match="RC3 required release report"):
        overlay_release_files(project, package, qualifier="rc3")
    assert len(RC3_REQUIRED_DOCUMENTS) == 25
    assert "FINAL_GITHUB_ACTIONS_AUDIT_v0.5.2.md" in RC3_REQUIRED_DOCUMENTS
    assert "FINAL_USER_DICTIONARY_ACCEPTANCE_v0.5.2.md" in RC3_REQUIRED_DOCUMENTS
    assert "FINAL_FAILURE_FIX_LOG_v0.5.2.md" in RC3_REQUIRED_DOCUMENTS


def test_rc3_checksum_manifest_requires_exact_two_assets(tmp_path: Path) -> None:
    windows = tmp_path / "hunterX_windows_0.5.2_rc3.zip"
    source = tmp_path / "hunterX_source_0.5.2_rc3.zip"
    windows.write_bytes(b"windows")
    source.write_bytes(b"source")
    manifest = tmp_path / "SHA256SUMS_v0.5.2_RC3.txt"
    write_checksums([windows, source], manifest)
    assert verify_rc3_checksums(manifest, tmp_path, VERSION) == [windows, source]

    extra = tmp_path / "unrelated.zip"
    extra.write_bytes(b"extra")
    write_checksums([windows, source, extra], manifest)
    with pytest.raises(ValueError, match="RC3 checksum assets mismatch"):
        verify_rc3_checksums(manifest, tmp_path, VERSION)
