from __future__ import annotations

import shutil
import subprocess
import zipfile
from pathlib import Path

import pytest

from build_source_archive import build_source_archive
from build_windows_archive import build_windows_archive
from verify_release_archive import verify_windows_archive
from write_release_checksums import verify_checksums, verify_rc2_checksums, write_checksums


def test_write_release_checksums_records_each_asset(tmp_path: Path) -> None:
    windows_asset = tmp_path / "hunterX_windows_0.4.7.zip"
    source_asset = tmp_path / "hunterX_source_0.4.7.zip"
    manifest = tmp_path / "SHA256SUMS_v0.4.7.txt"
    windows_asset.write_bytes(b"windows")
    source_asset.write_bytes(b"source")

    write_checksums([windows_asset, source_asset], manifest)

    contents = manifest.read_text(encoding="ascii")
    assert "hunterX_windows_0.4.7.zip" in contents
    assert "hunterX_source_0.4.7.zip" in contents
    assert len(contents.splitlines()) == 2


def test_write_release_checksums_fails_closed_for_missing_asset(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="Release asset is missing"):
        write_checksums([tmp_path / "missing.zip"], tmp_path / "SHA256SUMS_v0.4.7.txt")


def test_verify_release_checksums_detects_tampering(tmp_path: Path) -> None:
    asset = tmp_path / "hunterX.zip"
    asset.write_bytes(b"original")
    manifest = write_checksums([asset], tmp_path / "checksums.txt")

    assert verify_checksums(manifest, tmp_path) == [asset]
    asset.write_bytes(b"tampered")

    with pytest.raises(ValueError, match="verification failed"):
        verify_checksums(manifest, tmp_path)


def test_verify_rc2_checksums_requires_exact_rc2_asset_set(tmp_path: Path) -> None:
    windows_asset = tmp_path / "hunterX_windows_0.5.2_rc2.zip"
    source_asset = tmp_path / "hunterX_source_0.5.2_rc2.zip"
    manifest = tmp_path / "SHA256SUMS_v0.5.2_RC2.txt"
    windows_asset.write_bytes(b"windows")
    source_asset.write_bytes(b"source")
    write_checksums([windows_asset, source_asset], manifest)

    assert verify_rc2_checksums(manifest, tmp_path, "0.5.2") == [
        windows_asset,
        source_asset,
    ]

    final_asset = tmp_path / "hunterX_windows_0.5.2_final.zip"
    final_asset.write_bytes(b"final")
    write_checksums([windows_asset, source_asset, final_asset], manifest)
    with pytest.raises(ValueError, match="RC2 checksum assets mismatch"):
        verify_rc2_checksums(manifest, tmp_path, "0.5.2")


def test_build_windows_archive_uses_explorer_compatible_member_names(
    tmp_path: Path,
) -> None:
    package = tmp_path / "package"
    (package / "www").mkdir(parents=True)
    (package / "README.md").write_text("HunterX\n", encoding="utf-8")
    (package / "www" / "settings.html").write_text("settings\n", encoding="utf-8")
    output = tmp_path / "hunterX_windows_0.4.9.zip"

    build_windows_archive(package, output)

    with zipfile.ZipFile(output) as archive:
        assert archive.namelist() == ["README.md", "www/settings.html"]
        assert all(not name.startswith("./") for name in archive.namelist())


def test_windows_verifier_rejects_dot_prefixed_members(tmp_path: Path) -> None:
    output = tmp_path / "hunterX_windows_0.4.9.zip"
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr("./README.md", "HunterX\n")

    with pytest.raises(ValueError, match="non-portable ZIP path"):
        verify_windows_archive(output, "0.4.9")


@pytest.mark.skipif(
    shutil.which("cscript.exe") is None,
    reason="Windows Shell namespace is unavailable",
)
def test_windows_shell_namespace_can_see_built_archive(tmp_path: Path) -> None:
    package = tmp_path / "package"
    (package / "www").mkdir(parents=True)
    (package / "settings.exe").write_bytes(b"exe")
    (package / "www" / "settings.html").write_text("settings\n", encoding="utf-8")
    output = tmp_path / "hunterX_windows_0.4.9.zip"
    build_windows_archive(package, output)

    verifier = Path(__file__).parents[1] / "scripts" / "verify_windows_shell_zip.js"
    result = subprocess.run(
        ["cscript.exe", "//nologo", str(verifier), str(output)],
        check=True,
        capture_output=True,
        text=True,
    )

    assert '"root_items":2' in result.stdout
    assert '"names":"settings|www"' in result.stdout


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
    (repo / "src").mkdir()
    (repo / "src" / "hunter_metadata.py").write_text(
        'APP_VERSION = "0.4.7"\n',
        encoding="utf-8",
    )
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "test source archive"], cwd=repo, check=True, stdout=subprocess.PIPE)
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()

    archive = build_source_archive(
        version="0.4.7",
        output=tmp_path / "hunterX_source_0.4.7.zip",
        repo_root=repo,
        commit=commit,
    )

    assert archive.is_file()


def test_build_source_archive_ignores_host_line_ending_settings(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "autocrlf-repo"
    repo.mkdir()
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
    subprocess.run(
        ["git", "config", "core.autocrlf", "false"],
        cwd=repo,
        check=True,
    )
    (repo / ".gitattributes").write_bytes(b"* text\n")
    (repo / "README.md").write_bytes(b"release source\n")
    (repo / "src").mkdir()
    (repo / "src" / "hunter_metadata.py").write_bytes(
        b'APP_VERSION = "0.4.7"\n'
    )
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(
        ["git", "commit", "-q", "-m", "canonical LF source"],
        cwd=repo,
        check=True,
    )
    commit = subprocess.check_output(
        ["git", "rev-parse", "HEAD"],
        cwd=repo,
        text=True,
    ).strip()
    subprocess.run(
        ["git", "config", "core.autocrlf", "true"],
        cwd=repo,
        check=True,
    )
    subprocess.run(
        ["git", "config", "core.eol", "crlf"],
        cwd=repo,
        check=True,
    )

    archive = build_source_archive(
        version="0.4.7",
        output=tmp_path / "hunterX_source_0.4.7.zip",
        repo_root=repo,
        commit=commit,
    )

    with zipfile.ZipFile(archive) as source_zip:
        assert source_zip.read("hunterX-0.4.7/README.md") == b"release source\n"


def test_build_source_archive_can_verify_local_working_tree(tmp_path: Path) -> None:
    repo = tmp_path / "working tree with spaces"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, stdout=subprocess.PIPE)
    (repo / ".gitignore").write_text("settings.json\n", encoding="utf-8")
    (repo / "README.md").write_text("tracked source\n", encoding="utf-8")
    (repo / "removed.py").write_text("REMOVE_ME = True\n", encoding="utf-8")
    (repo / "src").mkdir()
    (repo / "src" / "hunter_metadata.py").write_text(
        'APP_VERSION = "0.4.8"\n',
        encoding="utf-8",
    )
    subprocess.run(
        ["git", "add", ".gitignore", "README.md", "removed.py", "src/hunter_metadata.py"],
        cwd=repo,
        check=True,
    )
    (repo / "removed.py").unlink()
    (repo / "new-feature.py").write_text("VERSION = 'working-tree'\n", encoding="utf-8")
    (repo / "settings.json").write_text('{"token": "secret"}', encoding="utf-8")

    archive = build_source_archive(
        version="0.4.8",
        output=tmp_path / "hunterX_source_0.4.8.zip",
        repo_root=repo,
        working_tree=True,
    )

    with zipfile.ZipFile(archive) as source_zip:
        names = set(source_zip.namelist())
        assert "hunterX-0.4.8/README.md" in names
        assert "hunterX-0.4.8/new-feature.py" in names
        assert "hunterX-0.4.8/removed.py" not in names
        assert all("settings.json" not in name for name in names)
        assert all("/.git/" not in name for name in names)


def test_build_source_archive_rejects_commit_metadata_version_mismatch(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
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
    (repo / "src" / "hunter_metadata.py").write_text(
        'APP_VERSION = "0.4.6"\n',
        encoding="utf-8",
    )
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "fixture"], cwd=repo, check=True)
    commit = subprocess.check_output(
        ["git", "rev-parse", "HEAD"],
        cwd=repo,
        text=True,
    ).strip()

    with pytest.raises(ValueError, match="commit declares 0.4.6"):
        build_source_archive(
            version="0.4.7",
            output=tmp_path / "hunterX_source_0.4.7.zip",
            repo_root=repo,
            commit=commit,
        )


def test_build_source_archive_final_fails_closed_without_qualification(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
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
    (repo / "src" / "hunter_metadata.py").write_text(
        'APP_VERSION = "0.5.2"\n',
        encoding="utf-8",
    )
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "fixture"], cwd=repo, check=True)
    commit = subprocess.check_output(
        ["git", "rev-parse", "HEAD"],
        cwd=repo,
        text=True,
    ).strip()

    with pytest.raises(ValueError, match="FINAL source archive missing required documents"):
        build_source_archive(
            version="0.5.2",
            output=tmp_path / "hunterX_source_0.5.2_final.zip",
            repo_root=repo,
            commit=commit,
            qualifier="final",
        )

    wrong_output = tmp_path / "hunterX_source_0.5.2.zip"
    wrong_output.write_bytes(b"preserve-existing-output")
    with pytest.raises(ValueError, match="output must be named"):
        build_source_archive(
            version="0.5.2",
            output=wrong_output,
            repo_root=repo,
            commit=commit,
            qualifier="rc2",
        )
    assert wrong_output.read_bytes() == b"preserve-existing-output"

    (repo / "dirty.txt").write_text("dirty\n", encoding="utf-8")
    with pytest.raises(ValueError, match="release repository must be clean"):
        build_source_archive(
            version="0.5.2",
            output=tmp_path / "hunterX_source_0.5.2_rc2.zip",
            repo_root=repo,
            commit=commit,
            qualifier="rc2",
        )
