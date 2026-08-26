from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

import build_windows_final
from build_windows_final import (
    EXPECTED_PYINSTALLER,
    REQUIREMENTS_LOCK_NAME,
    _stage_built_runtimes,
    build_windows_final as build_final,
    write_source_native_provenance,
)
from build_windows_from_base import FINAL_AUDIT_DIRECTORY
from final_qualification import WAIVER_NAME


VERSION = "0.5.2"
COMMIT = "a" * 40
REPO_ROOT = Path(__file__).resolve().parents[1]


def _git(repo: Path, *arguments: str) -> str:
    return subprocess.run(
        ["git", *arguments],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def test_stage_built_runtimes_preserves_isolated_layout(tmp_path: Path) -> None:
    pyinstaller_dist = tmp_path / "dist"
    for entry_name, internal_name in (
        ("nodriver_tixcraft", "_nodriver_internal"),
        ("settings", "_settings_internal"),
    ):
        build_root = pyinstaller_dist / entry_name
        (build_root / internal_name).mkdir(parents=True)
        (build_root / f"{entry_name}.exe").write_bytes(b"MZfixture")
        (build_root / internal_name / "python311.dll").write_bytes(b"runtime")

    package = tmp_path / "package"
    _stage_built_runtimes(pyinstaller_dist, package)

    assert (package / "nodriver_tixcraft.exe").read_bytes().startswith(b"MZ")
    assert (package / "settings.exe").read_bytes().startswith(b"MZ")
    assert (package / "_nodriver_internal/python311.dll").is_file()
    assert (package / "_settings_internal/python311.dll").is_file()
    assert not (package / "_internal").exists()


def test_stage_built_runtimes_rejects_missing_internal_directory(
    tmp_path: Path,
) -> None:
    pyinstaller_dist = tmp_path / "dist"
    build_root = pyinstaller_dist / "nodriver_tixcraft"
    build_root.mkdir(parents=True)
    (build_root / "nodriver_tixcraft.exe").write_bytes(b"MZfixture")

    with pytest.raises(ValueError, match="isolated runtime"):
        _stage_built_runtimes(pyinstaller_dist, tmp_path / "package")


def test_wrong_output_name_is_rejected_before_build(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="output must be named"):
        build_final(
            version=VERSION,
            output=tmp_path / "wrong.zip",
            package_dir=tmp_path / "package",
            project_root=tmp_path,
            commit=COMMIT,
        )


def test_pyinstaller_failure_does_not_publish_partial_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_root = tmp_path / "repo"
    project_root.mkdir()
    output = tmp_path / "release" / "hunterX_windows_0.5.2_final.zip"
    package_dir = tmp_path / "package"

    monkeypatch.setattr(
        build_windows_final.release_utils,
        "resolve_clean_commit",
        lambda _root, _commit: COMMIT,
    )
    monkeypatch.setattr(
        build_windows_final,
        "_installed_pyinstaller_version",
        lambda: EXPECTED_PYINSTALLER,
    )

    def fake_snapshot(_root: Path, destination: Path, _commit: str) -> Path:
        (destination / "src").mkdir(parents=True)
        (destination / "src/hunter_metadata.py").write_text(
            'APP_VERSION = "0.5.2"\n', encoding="utf-8"
        )
        (destination / "build_scripts").mkdir()
        return destination

    def fail_pyinstaller(*_args: object, **_kwargs: object) -> Path:
        raise subprocess.CalledProcessError(1, ["PyInstaller"])

    monkeypatch.setattr(build_windows_final, "snapshot_release_source", fake_snapshot)
    monkeypatch.setattr(build_windows_final, "_run_pyinstaller", fail_pyinstaller)

    with pytest.raises(subprocess.CalledProcessError):
        build_final(
            version=VERSION,
            output=output,
            package_dir=package_dir,
            project_root=project_root,
            commit=COMMIT,
        )

    assert not output.exists()
    assert not package_dir.exists()


def test_source_native_provenance_records_exact_commit_and_no_rc_base(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "source-builder@example.invalid")
    _git(repo, "config", "user.name", "Source Builder Test")
    _git(repo, "config", "core.autocrlf", "false")
    (repo / "src/hunter_metadata.py").write_text(
        'APP_VERSION = "0.5.2"\n', encoding="utf-8"
    )
    (repo / REQUIREMENTS_LOCK_NAME).write_text("locked\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "source snapshot")
    commit = _git(repo, "rev-parse", "HEAD")

    package = tmp_path / "package"
    audit = package / FINAL_AUDIT_DIRECTORY
    audit.mkdir(parents=True)
    (audit / WAIVER_NAME).write_bytes((REPO_ROOT / WAIVER_NAME).read_bytes())

    path = write_source_native_provenance(
        package,
        release_root=repo,
        repo_root=repo,
        version=VERSION,
        source_commit=commit,
        pyinstaller_version=EXPECTED_PYINSTALLER,
    )
    payload = json.loads(path.read_text(encoding="ascii"))

    assert payload["schema"] == 2
    assert payload["build_mode"] == "source_native"
    assert payload["source_commit"] == commit
    assert payload["runtime_source_commit"] == commit
    assert payload["windows_base_name"] is None
    assert payload["windows_base_sha256"] is None
    assert payload["eight_hour_soak_verified"] is False
    assert payload["user_approved_final_without_eight_hour_soak"] is True
