from __future__ import annotations

import subprocess
import zipfile
from pathlib import Path

import pytest

from verify_release_archive import verify_source_archive, verify_windows_archive


VERSION = "0.4.7"
REQUIRED_WINDOWS_FILES = {
    "CHANGELOG.md": b"changes",
    "LICENSE": b"license",
    "README.md": b"readme",
    "README_Release.txt": b"release readme",
    "nodriver_tixcraft.exe": b"bot",
    "settings.exe": b"settings",
    "www/css/settings.css": b"css",
    "www/favicon.ico": b"ico",
    "www/settings.html": b"html",
    "www/settings.js": b"js",
    "www/dist/jquery.min.js": b"jquery",
    "assets/icon.png": b"png",
    "_nodriver_internal/base_library.zip": b"base-a",
    "_nodriver_internal/certifi/cacert.pem": b"public-ca-a",
    "_nodriver_internal/python311.dll": b"dll-a",
    "_nodriver_internal/www/dist/jquery.min.js": b"jquery-a",
    "_settings_internal/base_library.zip": b"base-b",
    "_settings_internal/certifi/cacert.pem": b"public-ca-b",
    "_settings_internal/python311.dll": b"dll-b",
    "_settings_internal/www/dist/jquery.min.js": b"jquery-b",
}


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
    for name, content in files.items():
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


def test_windows_archive_accepts_isolated_complete_layout(tmp_path: Path) -> None:
    archive_path = tmp_path / f"hunterX_windows_{VERSION}.zip"
    _write_zip(archive_path, REQUIRED_WINDOWS_FILES)

    result = verify_windows_archive(archive_path, VERSION)

    assert result["missing"] == 0
    assert result["crc"] == "ok"
    assert result["runtime_layout"] == "isolated"


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
