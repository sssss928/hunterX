from __future__ import annotations

import subprocess
import zipfile
from pathlib import Path

import pytest

from build_windows_from_base import (
    BASELINE_ARCHIVE_NAME,
    extract_verified_baseline,
    promote_staged_package,
    snapshot_release_source,
    stage_application_source,
)


def test_stage_application_source_copies_only_runtime_inputs(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    source_root = project_root / "src"
    source_root.mkdir(parents=True)
    for name in ("hunter_metadata.py", "nodriver_tixcraft.py", "settings.py", "util.py"):
        (source_root / name).write_text("pass\n", encoding="utf-8")
    for directory_name in ("platforms", "assets", "www"):
        directory = source_root / directory_name
        directory.mkdir()
        (directory / "kept.txt").write_text("kept\n", encoding="utf-8")
    (source_root / "logs").mkdir()
    (source_root / "logs" / "runtime.log").write_text("local\n", encoding="utf-8")
    (source_root / "instances").mkdir()
    (source_root / "instances" / "state.json").write_text("{}\n", encoding="utf-8")

    destination = tmp_path / "app_src"
    stage_application_source(project_root, destination)

    assert (destination / "hunter_metadata.py").is_file()
    assert (destination / "platforms" / "kept.txt").is_file()
    assert (destination / "assets" / "kept.txt").is_file()
    assert (destination / "www" / "kept.txt").is_file()
    assert not (destination / "logs").exists()
    assert not (destination / "instances").exists()


def test_extract_verified_baseline_rejects_unapproved_bytes(tmp_path: Path) -> None:
    archive_path = tmp_path / BASELINE_ARCHIVE_NAME
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("settings.exe", b"MZ")

    with pytest.raises(ValueError, match="SHA-256 mismatch"):
        extract_verified_baseline(archive_path, tmp_path / "output")


def test_extract_verified_baseline_rejects_wrong_filename(tmp_path: Path) -> None:
    archive_path = tmp_path / "unapproved.zip"
    archive_path.write_bytes(b"not a release")

    with pytest.raises(ValueError, match="must be named"):
        extract_verified_baseline(archive_path, tmp_path / "output")


def test_snapshot_release_source_uses_exact_committed_bytes(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=project_root, check=True)
    subprocess.run(
        ["git", "config", "user.email", "release-test@example.invalid"],
        cwd=project_root,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Release Test"],
        cwd=project_root,
        check=True,
    )
    subprocess.run(
        ["git", "config", "core.autocrlf", "false"],
        cwd=project_root,
        check=True,
    )
    source = project_root / "release.txt"
    source.write_bytes(b"committed\r\nbytes\r\n")
    subprocess.run(["git", "add", "release.txt"], cwd=project_root, check=True)
    subprocess.run(
        ["git", "commit", "-q", "-m", "fixture"],
        cwd=project_root,
        check=True,
    )
    committed = subprocess.check_output(
        ["git", "show", "HEAD:release.txt"],
        cwd=project_root,
    )
    source.write_bytes(b"working-tree\nbytes\n")

    snapshot = snapshot_release_source(project_root, tmp_path / "snapshot")

    assert (snapshot / "release.txt").read_bytes() == committed
    assert (snapshot / "release.txt").read_bytes() != source.read_bytes()


def test_promote_staged_package_retries_transient_windows_lock(
    tmp_path: Path,
    monkeypatch,
) -> None:
    source = tmp_path / "staged"
    destination = tmp_path / "package"
    source.mkdir()
    (source / "settings.exe").write_bytes(b"MZ")
    original_replace = Path.replace
    calls: list[int] = []

    def flaky_replace(path: Path, target: Path) -> Path:
        calls.append(1)
        if len(calls) < 3:
            raise PermissionError("transient scanner lock")
        return original_replace(path, target)

    monkeypatch.setattr(Path, "replace", flaky_replace)
    monkeypatch.setattr("build_windows_from_base.time.sleep", lambda _seconds: None)
    promote_staged_package(source, destination)
    assert len(calls) == 3
    assert (destination / "settings.exe").read_bytes() == b"MZ"
