from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from build_source_archive import build_source_archive
from write_release_checksums import write_checksums


def test_write_release_checksums_records_each_asset(tmp_path: Path) -> None:
    windows_asset = tmp_path / "hunterX_windows_0.4.5.zip"
    source_asset = tmp_path / "hunterX_source_0.4.5.zip"
    manifest = tmp_path / "SHA256SUMS_v0.4.5.txt"
    windows_asset.write_bytes(b"windows")
    source_asset.write_bytes(b"source")

    write_checksums([windows_asset, source_asset], manifest)

    contents = manifest.read_text(encoding="ascii")
    assert "hunterX_windows_0.4.5.zip" in contents
    assert "hunterX_source_0.4.5.zip" in contents
    assert len(contents.splitlines()) == 2


def test_write_release_checksums_fails_closed_for_missing_asset(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="Release asset is missing"):
        write_checksums([tmp_path / "missing.zip"], tmp_path / "SHA256SUMS_v0.4.5.txt")


def test_build_source_archive_verifies_exact_git_commit(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, stdout=subprocess.PIPE)
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
    (repo / "README.md").write_text("release source\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "test source archive"], cwd=repo, check=True, stdout=subprocess.PIPE)
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()

    archive = build_source_archive(
        version="0.4.5",
        output=tmp_path / "hunterX_source_0.4.5.zip",
        repo_root=repo,
        commit=commit,
    )

    assert archive.is_file()
