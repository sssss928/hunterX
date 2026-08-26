from __future__ import annotations

import json
import subprocess
import zipfile
from pathlib import Path

import pytest

from verify_release_archive import (
    RC2_PROVENANCE_NAME,
    RC2_REQUIRED_DOCUMENTS,
    RC2_WINDOWS_BASE_NAME,
    RC2_WINDOWS_BASE_SHA256,
    RC3_PROVENANCE_NAME,
    RC3_REQUIRED_DOCUMENTS,
    RC3_WINDOWS_BASE_NAME,
    RC3_WINDOWS_BASE_SHA256,
    verify_source_archive,
    verify_windows_archive,
)


VERSION = "0.4.7"
REQUIRED_WINDOWS_FILES = {
    "BUILD_INFO.txt": b"build",
    "CHANGELOG.md": b"changes",
    "CODEX_MASTER_PROMPT_v0.5.2.md": b"instructions",
    "FINAL_AUDIT_v0.5.2.md": b"final audit",
    "IMPLEMENTATION_DIFF_v0.5.2_RC.md": b"implementation diff",
    "LICENSE": b"license",
    "README.md": b"readme",
    "README_Release.txt": b"release readme",
    "LONG_RUN_STABILITY_REPORT_v0.5.2.md": b"long run",
    "PERFORMANCE_COMPARISON_v0.5.1_vs_v0.5.2.md": b"performance",
    "PLATFORM_COMPLETION_LATCH_AUDIT.md": b"latch audit",
    "REFRESH_OWNERSHIP_MATRIX_v0.5.2.md": b"refresh matrix",
    "RELEASE_NOTES_v0.5.2_RC.md": b"release notes",
    "REQUIREMENT_TEST_TRACEABILITY_v0.5.2.md": b"traceability",
    "TEST_REPORT_v0.5.2_RC.md": b"test report",
    "WINDOWS_PACKAGE_zh-TW.txt": b"windows package",
    "nodriver_tixcraft.exe": b"MZbot",
    "settings.exe": b"MZsettings",
    "www/css/settings.css": b"css",
    "www/favicon.ico": b"ico",
    "www/settings.html": b"HunterX (0.4.7)",
    "www/settings.js": b"HunterX (0.4.7)",
    "www/dist/jquery.min.js": b"jquery",
    "assets/icon.png": b"png",
    "_nodriver_internal/base_library.zip": b"base-a",
    "_nodriver_internal/app_src/hunter_metadata.py": b"APP_VERSION = '0.4.7'",
    "_nodriver_internal/app_src/nodriver_tixcraft.py": b"pass",
    "_nodriver_internal/app_src/platforms/ticketplus.py": b"pass",
    "_nodriver_internal/app_src/www/dist/jquery.min.js": b"jquery",
    "_nodriver_internal/certifi/cacert.pem": b"public-ca-a",
    "_nodriver_internal/python311.dll": b"dll-a",
    "_nodriver_internal/www/dist/jquery.min.js": b"jquery",
    "_settings_internal/base_library.zip": b"base-b",
    "_settings_internal/app_src/hunter_metadata.py": b"APP_VERSION = '0.4.7'",
    "_settings_internal/app_src/settings.py": b"pass",
    "_settings_internal/app_src/www/dist/jquery.min.js": b"jquery",
    "_settings_internal/certifi/cacert.pem": b"public-ca-b",
    "_settings_internal/python311.dll": b"dll-b",
    "_settings_internal/www/dist/jquery.min.js": b"jquery",
}


def _rc2_windows_files(*, source_commit: str = "a" * 40) -> dict[str, bytes]:
    files = dict(REQUIRED_WINDOWS_FILES)
    files.update({name: b"round-2 evidence" for name in RC2_REQUIRED_DOCUMENTS})
    files[RC2_PROVENANCE_NAME] = json.dumps(
        {
            "schema": 1,
            "version": VERSION,
            "qualifier": "rc2",
            "source_commit": source_commit,
            "windows_base_name": RC2_WINDOWS_BASE_NAME,
            "windows_base_sha256": RC2_WINDOWS_BASE_SHA256,
            "clean_committed_snapshot": True,
            "eight_hour_soak_verified": False,
            "final_eligible": False,
        }
    ).encode("utf-8")
    return files


def _rc3_windows_files(*, source_commit: str = "a" * 40) -> dict[str, bytes]:
    files = dict(REQUIRED_WINDOWS_FILES)
    files.update({name: b"final-layer evidence" for name in RC3_REQUIRED_DOCUMENTS})
    files[RC3_PROVENANCE_NAME] = json.dumps(
        {
            "schema": 1,
            "version": VERSION,
            "qualifier": "rc3",
            "source_commit": source_commit,
            "windows_base_name": RC3_WINDOWS_BASE_NAME,
            "windows_base_sha256": RC3_WINDOWS_BASE_SHA256,
            "clean_committed_snapshot": True,
            "eight_hour_single_instance_soak_verified": False,
            "eight_hour_three_named_instances_soak_verified": False,
            "eight_hour_soak_verified": False,
            "final_eligible": False,
        }
    ).encode("utf-8")
    return files


def _write_zip(path: Path, files: dict[str, bytes]) -> None:
    with zipfile.ZipFile(path, "w") as archive:
        for name, content in files.items():
            archive.writestr(name, content)


def _create_git_source_archive(
    tmp_path: Path,
    files: dict[str, bytes],
) -> tuple[Path, Path, str]:
    repo = tmp_path / "repo"
    repo.mkdir()
    release_files = {
        "src/hunter_metadata.py": f'APP_VERSION = "{VERSION}"\n'.encode(),
        **files,
    }
    for name, content in release_files.items():
        destination = repo / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(content)

    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(
        ["git", "config", "user.email", "release-test@example.invalid"],
        cwd=repo,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Release Test"],
        cwd=repo,
        check=True,
    )
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(
        ["git", "commit", "-q", "-m", "fixture"],
        cwd=repo,
        check=True,
    )
    commit = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()

    archive_path = tmp_path / f"hunterX_source_{VERSION}.zip"
    subprocess.run(
        [
            "git",
            "-c",
            "core.autocrlf=false",
            "-c",
            "core.eol=lf",
            "archive",
            "--format=zip",
            f"--prefix=hunterX-{VERSION}/",
            f"--output={archive_path}",
            commit,
        ],
        cwd=repo,
        check=True,
    )
    return archive_path, repo, commit


def test_source_verifier_handles_archive_larger_than_pipe_buffer(
    tmp_path: Path,
) -> None:
    archive_path, repo, commit = _create_git_source_archive(
        tmp_path,
        {"docs/large-release-evidence.bin": b"release-evidence" * 200_000},
    )

    result = verify_source_archive(archive_path, VERSION, repo, commit)

    assert result["crc"] == "ok"
    assert result["mismatch"] == 0


def test_windows_archive_accepts_isolated_complete_layout(tmp_path: Path) -> None:
    archive_path = tmp_path / f"hunterX_windows_{VERSION}.zip"
    _write_zip(archive_path, REQUIRED_WINDOWS_FILES)

    result = verify_windows_archive(archive_path, VERSION)

    assert result["missing"] == 0
    assert result["crc"] == "ok"
    assert result["runtime_layout"] == "isolated"


def test_windows_archive_accepts_explicit_rc_qualified_name(tmp_path: Path) -> None:
    archive_path = tmp_path / f"hunterX_windows_{VERSION}_rc.zip"
    _write_zip(archive_path, REQUIRED_WINDOWS_FILES)

    result = verify_windows_archive(archive_path, VERSION, qualifier="rc")

    assert result["missing"] == 0


def test_windows_archive_accepts_explicit_rc2_qualified_name(tmp_path: Path) -> None:
    archive_path = tmp_path / f"hunterX_windows_{VERSION}_rc2.zip"
    _write_zip(archive_path, _rc2_windows_files())

    result = verify_windows_archive(archive_path, VERSION, qualifier="rc2")

    assert result["missing"] == 0


def test_windows_archive_rc2_requires_every_round2_report(tmp_path: Path) -> None:
    archive_path = tmp_path / f"hunterX_windows_{VERSION}_rc2.zip"
    files = _rc2_windows_files()
    missing_document = sorted(RC2_REQUIRED_DOCUMENTS)[0]
    files.pop(missing_document)
    _write_zip(archive_path, files)

    with pytest.raises(ValueError, match="missing required documents") as error:
        verify_windows_archive(archive_path, VERSION, qualifier="rc2")
    assert missing_document in str(error.value)


def test_windows_archive_accepts_exact_rc3_profile(tmp_path: Path) -> None:
    archive_path = tmp_path / f"hunterX_windows_{VERSION}_rc3.zip"
    _write_zip(archive_path, _rc3_windows_files())

    result = verify_windows_archive(archive_path, VERSION, qualifier="rc3")

    assert result["provenance"] == "verified"
    assert result["missing"] == 0


def test_windows_archive_rc3_requires_every_final_layer_report(tmp_path: Path) -> None:
    archive_path = tmp_path / f"hunterX_windows_{VERSION}_rc3.zip"
    files = _rc3_windows_files()
    files.pop(sorted(RC3_REQUIRED_DOCUMENTS)[0])
    _write_zip(archive_path, files)

    with pytest.raises(ValueError, match="RC3 Windows archive missing required documents"):
        verify_windows_archive(archive_path, VERSION, qualifier="rc3")


def test_windows_archive_rc3_cannot_claim_eight_hour_or_final(tmp_path: Path) -> None:
    archive_path = tmp_path / f"hunterX_windows_{VERSION}_rc3.zip"
    files = _rc3_windows_files()
    manifest = json.loads(files[RC3_PROVENANCE_NAME])
    manifest["eight_hour_soak_verified"] = True
    manifest["final_eligible"] = True
    files[RC3_PROVENANCE_NAME] = json.dumps(manifest).encode("utf-8")
    _write_zip(archive_path, files)

    with pytest.raises(ValueError, match="RC3 provenance field"):
        verify_windows_archive(archive_path, VERSION, qualifier="rc3")


def test_windows_archive_rejects_final_name_without_final_gate_provenance(
    tmp_path: Path,
) -> None:
    archive_path = tmp_path / f"hunterX_windows_{VERSION}_final.zip"
    _write_zip(archive_path, REQUIRED_WINDOWS_FILES)

    with pytest.raises(ValueError, match="Windows archive missing required files"):
        verify_windows_archive(archive_path, VERSION, qualifier="final")


def test_windows_archive_rejects_unrequested_rc_qualified_name(tmp_path: Path) -> None:
    archive_path = tmp_path / f"hunterX_windows_{VERSION}_rc.zip"
    _write_zip(archive_path, REQUIRED_WINDOWS_FILES)

    with pytest.raises(ValueError, match="does not match"):
        verify_windows_archive(archive_path, VERSION)


@pytest.mark.parametrize(
    "unsafe_name",
    [
        "../settings.json",
        "profiles/default/settings.json",
        "instances/live/heartbeat.txt",
        "_internal/python311.dll",
    ],
)
def test_windows_archive_rejects_unsafe_or_legacy_paths(
    tmp_path: Path,
    unsafe_name: str,
) -> None:
    archive_path = tmp_path / f"hunterX_windows_{VERSION}.zip"
    files = dict(REQUIRED_WINDOWS_FILES)
    files[unsafe_name] = b"secret"
    _write_zip(archive_path, files)

    with pytest.raises(ValueError):
        verify_windows_archive(archive_path, VERSION)


def test_windows_archive_rejects_case_insensitive_duplicates(tmp_path: Path) -> None:
    archive_path = tmp_path / f"hunterX_windows_{VERSION}.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        for name, content in REQUIRED_WINDOWS_FILES.items():
            archive.writestr(name, content)
        archive.writestr("README.MD", b"duplicate")

    with pytest.raises(ValueError, match="Duplicate ZIP path"):
        verify_windows_archive(archive_path, VERSION)


@pytest.mark.parametrize(
    "unsafe_name",
    [
        "dist/generated.zip",
        "assets/dist/generated.bin",
        "_nodriver_internal/dist/generated.bin",
        "other/www/dist/generated.bin",
    ],
)
def test_windows_archive_only_allows_known_www_vendor_dist_paths(
    tmp_path: Path,
    unsafe_name: str,
) -> None:
    archive_path = tmp_path / f"hunterX_windows_{VERSION}.zip"
    files = dict(REQUIRED_WINDOWS_FILES)
    files[unsafe_name] = b"generated"
    _write_zip(archive_path, files)

    with pytest.raises(ValueError, match="Denied release path"):
        verify_windows_archive(archive_path, VERSION)


def test_windows_archive_rejects_unapproved_private_key_file(tmp_path: Path) -> None:
    archive_path = tmp_path / f"hunterX_windows_{VERSION}.zip"
    files = dict(REQUIRED_WINDOWS_FILES)
    files["www/private.pem"] = b"private"
    _write_zip(archive_path, files)

    with pytest.raises(ValueError, match="Denied private-key file"):
        verify_windows_archive(archive_path, VERSION)


def test_windows_archive_requires_both_runtime_trees(tmp_path: Path) -> None:
    archive_path = tmp_path / f"hunterX_windows_{VERSION}.zip"
    files = dict(REQUIRED_WINDOWS_FILES)
    files.pop("_settings_internal/base_library.zip")
    _write_zip(archive_path, files)

    with pytest.raises(ValueError, match="missing required"):
        verify_windows_archive(archive_path, VERSION)


def test_windows_archive_rejects_embedded_runtime_version_mismatch(tmp_path: Path) -> None:
    archive_path = tmp_path / f"hunterX_windows_{VERSION}.zip"
    files = dict(REQUIRED_WINDOWS_FILES)
    files["_settings_internal/app_src/hunter_metadata.py"] = b"APP_VERSION = '9.9.9'"
    _write_zip(archive_path, files)

    with pytest.raises(ValueError, match="runtime version mismatch"):
        verify_windows_archive(archive_path, VERSION)


def test_source_archive_accepts_tracked_nested_vendor_dist(tmp_path: Path) -> None:
    archive_path, repo, commit = _create_git_source_archive(
        tmp_path,
        {
            "README.md": b"source readme",
            "src/www/dist/jquery.min.js": b"jquery",
            "src/www/dist/bootstrap/bootstrap.min.css": b"bootstrap",
        },
    )

    result = verify_source_archive(archive_path, VERSION, repo, commit)

    assert result["missing"] == 0
    assert result["extra"] == 0
    assert result["mismatch"] == 0
    assert result["prefix"] == f"hunterX-{VERSION}/"


def test_source_archive_rejects_commit_metadata_version_mismatch(tmp_path: Path) -> None:
    archive_path, repo, commit = _create_git_source_archive(
        tmp_path,
        {"src/hunter_metadata.py": b'APP_VERSION = "9.9.9"\n'},
    )

    with pytest.raises(ValueError, match="commit declares 9.9.9"):
        verify_source_archive(archive_path, VERSION, repo, commit)


def test_source_archive_accepts_explicit_rc_qualified_name(tmp_path: Path) -> None:
    normal_path, repo, commit = _create_git_source_archive(
        tmp_path,
        {"README.md": b"source readme"},
    )
    archive_path = normal_path.with_name(f"hunterX_source_{VERSION}_rc.zip")
    normal_path.rename(archive_path)

    result = verify_source_archive(
        archive_path,
        VERSION,
        repo,
        commit,
        qualifier="rc",
    )

    assert result["missing"] == 0


def test_source_archive_accepts_explicit_rc2_qualified_name(tmp_path: Path) -> None:
    normal_path, repo, commit = _create_git_source_archive(
        tmp_path,
        {"README.md": b"source readme"},
    )
    archive_path = normal_path.with_name(f"hunterX_source_{VERSION}_rc2.zip")
    normal_path.rename(archive_path)

    result = verify_source_archive(
        archive_path,
        VERSION,
        repo,
        commit,
        qualifier="rc2",
    )

    assert result["missing"] == 0


def test_source_archive_final_profile_requires_a_real_archive(
    tmp_path: Path,
) -> None:
    archive_path = tmp_path / f"hunterX_source_{VERSION}_final.zip"

    with pytest.raises(FileNotFoundError):
        verify_source_archive(
            archive_path,
            VERSION,
            tmp_path,
            "0" * 40,
            qualifier="final",
        )


@pytest.mark.parametrize(
    "denied_path",
    [
        "build/output.bin",
        "dist/generated.zip",
        "logs/runtime.log",
        "profiles/default.json",
    ],
)
def test_source_archive_rejects_top_level_generated_or_sensitive_directories(
    tmp_path: Path,
    denied_path: str,
) -> None:
    archive_path, repo, commit = _create_git_source_archive(
        tmp_path,
        {
            "README.md": b"source readme",
            denied_path: b"must not ship",
        },
    )

    with pytest.raises(ValueError, match="Denied source top-level directory"):
        verify_source_archive(archive_path, VERSION, repo, commit)
