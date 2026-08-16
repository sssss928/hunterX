from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from build_windows_from_base import (
    BASELINE_ARCHIVE_NAME,
    extract_verified_baseline,
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
