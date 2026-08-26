from __future__ import annotations

import json
import stat
import subprocess
import sys
import zipfile
from dataclasses import dataclass
from pathlib import Path

import pytest

import v052_packaged_smoke
from verify_release_archive import (
    RC2_PROVENANCE_NAME,
    RC2_REQUIRED_DOCUMENTS,
    RC2_WINDOWS_BASE_NAME,
    RC2_WINDOWS_BASE_SHA256,
    verify_release_pair,
)


VERSION = "0.5.2"


@dataclass
class ReleasePairFixture:
    windows_path: Path
    source_path: Path
    repo: Path
    commit: str
    windows_files: dict[str, bytes]


def _write_zip(path: Path, files: dict[str, bytes]) -> None:
    with zipfile.ZipFile(path, "w") as archive:
        for name, content in files.items():
            archive.writestr(name, content)


def _manifest(commit: str, **overrides: object) -> bytes:
    value: dict[str, object] = {
        "schema": 1,
        "version": VERSION,
        "qualifier": "rc2",
        "source_commit": commit,
        "windows_base_name": RC2_WINDOWS_BASE_NAME,
        "windows_base_sha256": RC2_WINDOWS_BASE_SHA256,
        "clean_committed_snapshot": True,
        "eight_hour_soak_verified": False,
        "final_eligible": False,
    }
    value.update(overrides)
    return json.dumps(value, sort_keys=True).encode("utf-8")


def _commit_source(repo: Path, files: dict[str, bytes]) -> str:
    for name, content in files.items():
        destination = repo / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(content)
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(
        ["git", "config", "user.email", "rc2-pair-test@example.invalid"],
        cwd=repo,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "RC2 Pair Test"],
        cwd=repo,
        check=True,
    )
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "fixture"], cwd=repo, check=True)
    return subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


@pytest.fixture
def release_pair(tmp_path: Path) -> ReleasePairFixture:
    repo = tmp_path / "repo"
    repo.mkdir()
    source_files = {
        "README.md": b"source readme",
        "src/hunter_metadata.py": b"APP_VERSION = '0.5.2'\n",
        "src/nodriver_tixcraft.py": b"def main():\n    return 0\n",
        "src/settings.py": b"def main():\n    return 0\n",
        "src/platforms/ticketplus.py": b"PLATFORM = 'ticketplus'\n",
        "src/assets/icon.png": b"rc2-icon",
        "src/www/css/settings.css": b"body { color: black; }",
        "src/www/favicon.ico": b"rc2-favicon",
        "src/www/settings.html": b"<title>HunterX (0.5.2)</title>",
        "src/www/settings.js": b"const title = 'HunterX (0.5.2)';",
    }
    commit = _commit_source(repo, source_files)
    source_path = tmp_path / f"hunterX_source_{VERSION}_rc2.zip"
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
            f"--output={source_path}",
            commit,
        ],
        cwd=repo,
        check=True,
    )

    runtime_files: dict[str, bytes] = {}
    with zipfile.ZipFile(source_path) as source_archive:
        source_prefix = f"hunterX-{VERSION}/src/"
        for name in source_archive.namelist():
            if name.startswith(source_prefix) and not name.endswith("/"):
                runtime_files[name.removeprefix(source_prefix)] = source_archive.read(name)
    windows_files = {
        "BUILD_INFO.txt": b"rc2 build",
        "CHANGELOG.md": b"changes",
        "CODEX_MASTER_PROMPT_v0.5.2.md": b"requirements",
        "FINAL_AUDIT_v0.5.2.md": b"round-1 audit",
        "IMPLEMENTATION_DIFF_v0.5.2_RC.md": b"round-1 diff",
        "LICENSE": b"license",
        "README.md": b"readme",
        "README_Release.txt": b"release readme",
        "LONG_RUN_STABILITY_REPORT_v0.5.2.md": b"round-1 long run",
        "PERFORMANCE_COMPARISON_v0.5.1_vs_v0.5.2.md": b"round-1 performance",
        "PLATFORM_COMPLETION_LATCH_AUDIT.md": b"round-1 latch",
        "REFRESH_OWNERSHIP_MATRIX_v0.5.2.md": b"round-1 refresh",
        "RELEASE_NOTES_v0.5.2_RC.md": b"round-1 notes",
        "REQUIREMENT_TEST_TRACEABILITY_v0.5.2.md": b"round-2 traceability",
        "TEST_REPORT_v0.5.2_RC.md": b"round-1 test report",
        "WINDOWS_PACKAGE_zh-TW.txt": b"windows package",
        "nodriver_tixcraft.exe": b"MZbot",
        "settings.exe": b"MZsettings",
        "_nodriver_internal/base_library.zip": b"base-a",
        "_nodriver_internal/python311.dll": b"dll-a",
        "_settings_internal/base_library.zip": b"base-b",
        "_settings_internal/python311.dll": b"dll-b",
        RC2_PROVENANCE_NAME: _manifest(commit),
    }
    windows_files.update({name: b"round-2 evidence" for name in RC2_REQUIRED_DOCUMENTS})
    for relative_name, content in runtime_files.items():
        windows_files[f"_nodriver_internal/app_src/{relative_name}"] = content
        windows_files[f"_settings_internal/app_src/{relative_name}"] = content
    for directory_name in ("assets", "www"):
        for relative_name, content in runtime_files.items():
            prefix = f"{directory_name}/"
            if not relative_name.startswith(prefix):
                continue
            child_name = relative_name.removeprefix(prefix)
            windows_files[f"{directory_name}/{child_name}"] = content
            windows_files[f"_nodriver_internal/{directory_name}/{child_name}"] = content
            windows_files[f"_settings_internal/{directory_name}/{child_name}"] = content

    windows_path = tmp_path / f"hunterX_windows_{VERSION}_rc2.zip"
    _write_zip(windows_path, windows_files)
    return ReleasePairFixture(
        windows_path=windows_path,
        source_path=source_path,
        repo=repo,
        commit=commit,
        windows_files=windows_files,
    )


def _verify(fixture: ReleasePairFixture, *, qualifier: str = "rc2") -> dict[str, object]:
    return verify_release_pair(
        fixture.windows_path,
        fixture.source_path,
        VERSION,
        fixture.repo,
        fixture.commit,
        qualifier=qualifier,
    )


def test_release_pair_proves_all_staged_runtime_copies(release_pair: ReleasePairFixture) -> None:
    result = _verify(release_pair)

    assert result["parity"] == "ok"
    assert result["parity_targets"] == 8
    assert result["source_commit"] == release_pair.commit
    assert result["provenance"] == "verified"


def test_release_pair_rejects_dirty_verification_checkout(
    release_pair: ReleasePairFixture,
) -> None:
    (release_pair.repo / "untracked-release-state.txt").write_text(
        "dirty\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="release repository must be clean"):
        _verify(release_pair)


@pytest.mark.parametrize(
    ("mutation", "expected_fragment"),
    [
        ("tamper", "mismatch=['platforms/ticketplus.py']"),
        ("missing", "missing=['settings.py']"),
        ("extra", "extra=['unexpected.py']"),
    ],
)
def test_release_pair_rejects_runtime_tamper_missing_and_extra(
    release_pair: ReleasePairFixture,
    mutation: str,
    expected_fragment: str,
) -> None:
    files = dict(release_pair.windows_files)
    if mutation == "tamper":
        files["_settings_internal/app_src/platforms/ticketplus.py"] = b"tampered"
    elif mutation == "missing":
        files.pop("_nodriver_internal/app_src/settings.py")
    else:
        files["_nodriver_internal/app_src/unexpected.py"] = b"unexpected"
    _write_zip(release_pair.windows_path, files)

    with pytest.raises(ValueError, match="Release pair parity failure") as error:
        _verify(release_pair)
    assert expected_fragment in str(error.value)


def test_release_pair_rejects_provenance_source_commit_mismatch(
    release_pair: ReleasePairFixture,
) -> None:
    files = dict(release_pair.windows_files)
    files[RC2_PROVENANCE_NAME] = _manifest("f" * 40)
    _write_zip(release_pair.windows_path, files)

    with pytest.raises(ValueError, match="does not match resolved source commit"):
        _verify(release_pair)


def test_release_pair_rejects_root_and_runtime_www_divergence(
    release_pair: ReleasePairFixture,
) -> None:
    files = dict(release_pair.windows_files)
    files["_nodriver_internal/www/settings.js"] = b"stale runtime frontend"
    _write_zip(release_pair.windows_path, files)

    with pytest.raises(ValueError, match="_nodriver_internal/www") as error:
        _verify(release_pair)
    assert "mismatch=['settings.js']" in str(error.value)


def test_release_pair_rejects_wrong_round1_windows_base(
    release_pair: ReleasePairFixture,
) -> None:
    files = dict(release_pair.windows_files)
    files[RC2_PROVENANCE_NAME] = _manifest(
        release_pair.commit,
        windows_base_name="hunterX_windows_0.5.1.zip",
    )
    _write_zip(release_pair.windows_path, files)

    with pytest.raises(ValueError, match="windows_base_name"):
        _verify(release_pair)


def test_release_pair_rejects_false_final_claim(release_pair: ReleasePairFixture) -> None:
    final_windows = release_pair.windows_path.with_name(
        f"hunterX_windows_{VERSION}_final.zip"
    )
    final_source = release_pair.source_path.with_name(
        f"hunterX_source_{VERSION}_final.zip"
    )
    release_pair.windows_path.rename(final_windows)
    release_pair.source_path.rename(final_source)
    release_pair.windows_path = final_windows
    release_pair.source_path = final_source

    with pytest.raises(ValueError, match="Windows archive missing required files"):
        _verify(release_pair, qualifier="final")


def test_release_pair_cli_runs_joint_gate(release_pair: ReleasePairFixture) -> None:
    script = Path(__file__).resolve().parents[1] / "scripts" / "verify_release_archive.py"
    result = subprocess.run(
        [
            sys.executable,
            str(script),
            "pair",
            "--windows-archive",
            str(release_pair.windows_path),
            "--source-archive",
            str(release_pair.source_path),
            "--version",
            VERSION,
            "--repo-root",
            str(release_pair.repo),
            "--commit",
            release_pair.commit,
            "--qualifier",
            "rc2",
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout)["parity"] == "ok"


def test_archive_smoke_extracts_to_fresh_temporary_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    archive_path = tmp_path / "package.zip"
    _write_zip(
        archive_path,
        {
            "settings.exe": b"MZsettings",
            "nodriver_tixcraft.exe": b"MZbot",
            "nested/data.txt": b"fresh bytes",
        },
    )
    observed: dict[str, Path] = {}

    def fake_verify_package(package_dir: Path) -> dict[str, str]:
        observed["package_dir"] = package_dir
        assert (package_dir / "nested" / "data.txt").read_bytes() == b"fresh bytes"
        return {"settings_smoke": "PASS"}

    monkeypatch.setattr(v052_packaged_smoke, "verify_package", fake_verify_package)

    assert v052_packaged_smoke.verify_archive_package(archive_path) == {
        "settings_smoke": "PASS"
    }
    assert not observed["package_dir"].exists()


def test_archive_smoke_rejects_path_traversal_before_execution(tmp_path: Path) -> None:
    archive_path = tmp_path / "unsafe.zip"
    _write_zip(archive_path, {"../settings.exe": b"MZsettings"})

    with pytest.raises(ValueError, match="Unsafe or non-portable ZIP path"):
        v052_packaged_smoke.verify_archive_package(archive_path)


def test_archive_smoke_rejects_duplicate_paths_before_execution(tmp_path: Path) -> None:
    archive_path = tmp_path / "duplicate.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("settings.exe", b"first")
        archive.writestr("SETTINGS.EXE", b"second")

    with pytest.raises(ValueError, match="Duplicate ZIP path"):
        v052_packaged_smoke.verify_archive_package(archive_path)


def test_archive_smoke_rejects_symlink_before_execution(tmp_path: Path) -> None:
    archive_path = tmp_path / "symlink.zip"
    symlink = zipfile.ZipInfo("settings.exe")
    symlink.create_system = 3
    symlink.external_attr = (stat.S_IFLNK | 0o777) << 16
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr(symlink, "outside.exe")

    with pytest.raises(ValueError, match="ZIP symlink is forbidden"):
        v052_packaged_smoke.verify_archive_package(archive_path)
