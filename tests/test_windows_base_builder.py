from __future__ import annotations

import json
import subprocess
import zipfile
from pathlib import Path

import pytest

from build_windows_from_base import (
    BASELINE_ARCHIVE_NAME,
    RC2_PROVENANCE_NAME,
    REQUIRED_BASE_FILES,
    ROUND1_RC_ARCHIVE_NAME,
    ROUND1_RC_SHA256,
    build_from_verified_baseline,
    extract_verified_baseline,
    overlay_release_files,
    promote_staged_package,
    snapshot_release_source,
    stage_application_source,
    write_rc2_provenance,
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

    with pytest.raises(ValueError, match="must be one of"):
        extract_verified_baseline(archive_path, tmp_path / "output")


def test_rc2_baseline_rejects_legacy_v051_archive_before_hash_check(
    tmp_path: Path,
) -> None:
    archive_path = tmp_path / BASELINE_ARCHIVE_NAME
    archive_path.write_bytes(b"legacy")

    with pytest.raises(ValueError, match="RC2 builds require the Round-1 Windows base"):
        extract_verified_baseline(archive_path, tmp_path / "output", qualifier="rc2")


def test_rc2_baseline_accepts_only_verified_round1_layout(
    tmp_path: Path,
    monkeypatch,
) -> None:
    archive_path = tmp_path / ROUND1_RC_ARCHIVE_NAME
    with zipfile.ZipFile(archive_path, "w") as archive:
        for name in sorted(REQUIRED_BASE_FILES):
            content = b"MZfixture" if name.endswith(".exe") else b"fixture"
            archive.writestr(name, content)
    monkeypatch.setattr(
        "build_windows_from_base.sha256_file",
        lambda _path: ROUND1_RC_SHA256,
    )

    destination = tmp_path / "output"
    extract_verified_baseline(archive_path, destination, qualifier="rc2")

    assert (destination / "settings.exe").read_bytes().startswith(b"MZ")
    assert (destination / "nodriver_tixcraft.exe").read_bytes().startswith(b"MZ")


def test_windows_builder_rejects_unqualified_output_early(
    tmp_path: Path,
) -> None:
    arguments = {
        "version": "0.5.2",
        "base_archive": tmp_path / ROUND1_RC_ARCHIVE_NAME,
        "package_dir": tmp_path / "package",
        "project_root": tmp_path / "project",
        "commit": "a" * 40,
    }
    with pytest.raises(ValueError, match="output must be named"):
        build_from_verified_baseline(
            **arguments,
            output=tmp_path / "hunterX_windows_0.5.2.zip",
            qualifier="rc2",
        )


def _create_committed_fixture(tmp_path: Path) -> tuple[Path, Path, str, bytes]:
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
    commit = subprocess.check_output(
        ["git", "rev-parse", "HEAD"],
        cwd=project_root,
        text=True,
    ).strip()
    return project_root, source, commit, committed


def test_snapshot_release_source_uses_exact_clean_commit_bytes(tmp_path: Path) -> None:
    project_root, source, commit, committed = _create_committed_fixture(tmp_path)

    snapshot = snapshot_release_source(project_root, tmp_path / "snapshot", commit)

    assert (snapshot / "release.txt").read_bytes() == committed
    assert (snapshot / "release.txt").read_bytes() == source.read_bytes()


@pytest.mark.parametrize("dirty_kind", ["tracked", "untracked"])
def test_snapshot_release_source_rejects_dirty_tree(
    tmp_path: Path,
    dirty_kind: str,
) -> None:
    project_root, source, commit, _committed = _create_committed_fixture(tmp_path)
    if dirty_kind == "tracked":
        source.write_bytes(b"dirty\n")
    else:
        (project_root / "untracked.txt").write_text("dirty\n", encoding="utf-8")

    with pytest.raises(ValueError, match="release repository must be clean"):
        snapshot_release_source(project_root, tmp_path / "snapshot", commit)


def test_snapshot_release_source_requires_full_explicit_commit(tmp_path: Path) -> None:
    project_root, _source, commit, _committed = _create_committed_fixture(tmp_path)

    with pytest.raises(ValueError, match="full 40-hex"):
        snapshot_release_source(project_root, tmp_path / "snapshot", commit[:12])


def test_snapshot_release_source_rejects_non_git_source_tree(tmp_path: Path) -> None:
    project_root = tmp_path / "uploaded-source-without-git"
    project_root.mkdir()

    with pytest.raises(ValueError, match="Git release snapshot check failed"):
        snapshot_release_source(
            project_root,
            tmp_path / "snapshot",
            "a" * 40,
        )


def test_release_overlay_fails_closed_when_required_report_is_missing(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    package_root = tmp_path / "package"
    project_root.mkdir()
    package_root.mkdir()

    with pytest.raises(FileNotFoundError, match="FINAL required release report"):
        overlay_release_files(project_root, package_root, qualifier="final")
    with pytest.raises(FileNotFoundError, match="ROUND2_FINAL_CROSS_AUDIT"):
        overlay_release_files(project_root, package_root, qualifier="rc2")


def test_rc2_provenance_records_exact_base_and_non_final_status(
    tmp_path: Path,
    monkeypatch,
) -> None:
    package_root = tmp_path / "package"
    package_root.mkdir()
    base_archive = tmp_path / ROUND1_RC_ARCHIVE_NAME
    base_archive.write_bytes(b"fixture")
    monkeypatch.setattr(
        "build_windows_from_base.sha256_file",
        lambda _path: ROUND1_RC_SHA256,
    )

    provenance_path = write_rc2_provenance(
        package_root,
        version="0.5.2",
        source_commit="a" * 40,
        base_archive=base_archive,
    )
    provenance = json.loads(provenance_path.read_text(encoding="ascii"))

    assert provenance_path.name == RC2_PROVENANCE_NAME
    assert provenance["source_commit"] == "a" * 40
    assert provenance["windows_base_name"] == ROUND1_RC_ARCHIVE_NAME
    assert provenance["windows_base_sha256"] == ROUND1_RC_SHA256
    assert provenance["clean_committed_snapshot"] is True
    assert provenance["eight_hour_soak_verified"] is False
    assert provenance["final_eligible"] is False


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
