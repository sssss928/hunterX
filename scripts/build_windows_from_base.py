#!/usr/bin/env python3
"""Build HunterX v0.5.0 on top of the verified v0.4.9 Windows runtime."""

from __future__ import annotations

import argparse
import hashlib
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path, PurePosixPath

import release_utils
from build_windows_archive import build_windows_archive
from verify_release_archive import verify_windows_archive


BASELINE_VERSION = "0.4.9"
BASELINE_ARCHIVE_NAME = f"hunterX_windows_{BASELINE_VERSION}.zip"
BASELINE_SHA256 = "9482be6a6e8e5de39ddc19e7d95c088b7412f1f6fa6fb2d79ce2fc1e128eb666"
RUNTIME_LAYOUTS = {
    "nodriver_tixcraft": "_nodriver_internal",
    "settings": "_settings_internal",
}
REQUIRED_BASE_FILES = {
    "nodriver_tixcraft.exe",
    "settings.exe",
    "_nodriver_internal/base_library.zip",
    "_nodriver_internal/python311.dll",
    "_settings_internal/base_library.zip",
    "_settings_internal/python311.dll",
    "www/settings.html",
    "www/settings.js",
}
SOURCE_DIRECTORIES = ("platforms", "assets", "www")
TOP_LEVEL_DOCUMENTS = (
    "CHANGELOG.md",
    "LEGAL_NOTICE.md",
    "LICENSE",
    "README.md",
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _checked_members(archive: zipfile.ZipFile) -> dict[str, zipfile.ZipInfo]:
    corrupt = archive.testzip()
    if corrupt is not None:
        raise ValueError(f"baseline ZIP CRC failure: {corrupt}")

    members: dict[str, zipfile.ZipInfo] = {}
    casefolded: set[str] = set()
    for info in archive.infolist():
        raw_name = info.filename.replace("\\", "/")
        path = PurePosixPath(raw_name)
        if (
            not raw_name
            or info.filename != raw_name
            or raw_name.startswith("./")
            or "//" in raw_name
            or raw_name.startswith("/")
            or path.is_absolute()
            or ".." in path.parts
            or path.as_posix() != raw_name.rstrip("/")
        ):
            raise ValueError(f"unsafe baseline ZIP path: {info.filename!r}")
        normalized = path.as_posix().rstrip("/")
        if not normalized or info.is_dir():
            continue
        folded = normalized.casefold()
        if folded in casefolded:
            raise ValueError(f"duplicate baseline ZIP path ignoring case: {normalized!r}")
        casefolded.add(folded)
        members[normalized] = info
    return members


def extract_verified_baseline(base_archive: Path, destination: Path) -> None:
    if not base_archive.is_file():
        raise FileNotFoundError(base_archive)
    if base_archive.name != BASELINE_ARCHIVE_NAME:
        raise ValueError(
            f"baseline archive must be named {BASELINE_ARCHIVE_NAME!r}, "
            f"not {base_archive.name!r}"
        )
    actual_sha256 = sha256_file(base_archive)
    if actual_sha256 != BASELINE_SHA256:
        raise ValueError(
            "v0.4.9 baseline SHA-256 mismatch: "
            f"expected {BASELINE_SHA256}, got {actual_sha256}"
        )

    with zipfile.ZipFile(base_archive) as archive:
        members = _checked_members(archive)
        missing = sorted(REQUIRED_BASE_FILES - set(members))
        if missing:
            raise ValueError(f"v0.4.9 baseline is incomplete: {missing}")
        for executable in ("nodriver_tixcraft.exe", "settings.exe"):
            if archive.read(members[executable])[:2] != b"MZ":
                raise ValueError(f"baseline executable is not a Windows PE file: {executable}")

        destination.mkdir(parents=True, exist_ok=False)
        root = destination.resolve()
        for name, info in members.items():
            target = (root / Path(name)).resolve()
            try:
                target.relative_to(root)
            except ValueError as exc:
                raise ValueError(f"baseline ZIP entry escapes destination: {name}") from exc
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(info) as source, target.open("wb") as output:
                shutil.copyfileobj(source, output, length=1024 * 1024)


def _replace_tree(source: Path, destination: Path) -> None:
    if not source.is_dir():
        raise FileNotFoundError(source)
    if destination.exists():
        shutil.rmtree(destination)
    shutil.copytree(
        source,
        destination,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo"),
    )


def stage_application_source(project_root: Path, destination: Path) -> None:
    source_root = project_root / "src"
    if destination.exists():
        shutil.rmtree(destination)
    destination.mkdir(parents=True)

    python_files = sorted(source_root.glob("*.py"))
    required_python = {"hunter_metadata.py", "nodriver_tixcraft.py", "settings.py"}
    if not required_python.issubset({path.name for path in python_files}):
        raise ValueError("application source is missing a required entry module")
    for source in python_files:
        shutil.copy2(source, destination / source.name)
    for directory_name in SOURCE_DIRECTORIES:
        _replace_tree(source_root / directory_name, destination / directory_name)

    forbidden_names = {
        "config.json",
        "heartbeat.txt",
        "maxbot_int28_idle.txt",
        "maxbot_last_url.txt",
        "maxbot_question.txt",
        "settings.json",
    }
    for candidate in destination.rglob("*"):
        if candidate.name.casefold() in forbidden_names:
            raise ValueError(f"runtime source contains local state: {candidate}")


def overlay_release_files(project_root: Path, package_root: Path) -> None:
    source_root = project_root / "src"
    for name in TOP_LEVEL_DOCUMENTS:
        source = project_root / name
        if source.is_file():
            shutil.copy2(source, package_root / name)

    release_readme = project_root / "build_scripts" / "README_Release.txt"
    if not release_readme.is_file():
        raise FileNotFoundError(release_readme)
    shutil.copy2(release_readme, package_root / "README_Release.txt")

    _replace_tree(project_root / "guide", package_root / "guide")
    _replace_tree(source_root / "assets", package_root / "assets")
    _replace_tree(source_root / "www", package_root / "www")

    for runtime_root_name in RUNTIME_LAYOUTS.values():
        runtime_root = package_root / runtime_root_name
        if not runtime_root.is_dir():
            raise ValueError(f"baseline runtime directory is missing: {runtime_root_name}")
        _replace_tree(source_root / "assets", runtime_root / "assets")
        _replace_tree(source_root / "www", runtime_root / "www")
        stage_application_source(project_root, runtime_root / "app_src")


def repack_entrypoints(package_root: Path) -> None:
    # Import lazily so source-only checks do not need PyInstaller installed.
    from repack_pyinstaller_entrypoint import repack

    for entry_name in RUNTIME_LAYOUTS:
        executable = package_root / f"{entry_name}.exe"
        output = package_root / f".{entry_name}.repacked.exe"
        try:
            repack(executable, output, entry_name)
            output.replace(executable)
        finally:
            output.unlink(missing_ok=True)


def build_from_verified_baseline(
    *,
    version: str,
    base_archive: Path,
    output: Path,
    package_dir: Path,
    project_root: Path,
) -> Path:
    normalized_version = release_utils.validate_semver(version)
    declared_version = release_utils.project_version(project_root / "src" / "hunter_metadata.py")
    if normalized_version != declared_version:
        raise ValueError(
            f"release version mismatch: requested {normalized_version}, project declares {declared_version}"
        )
    expected_output_name = release_utils.artifact_name(normalized_version)
    if output.name != expected_output_name:
        raise ValueError(f"output must be named {expected_output_name!r}")

    package_dir = package_dir.resolve()
    package_dir.parent.mkdir(parents=True, exist_ok=True)
    temporary_parent = Path(
        tempfile.mkdtemp(prefix=f".{package_dir.name}.", dir=package_dir.parent)
    )
    staged_package = temporary_parent / package_dir.name
    try:
        extract_verified_baseline(base_archive.resolve(), staged_package)
        overlay_release_files(project_root.resolve(), staged_package)
        repack_entrypoints(staged_package)
        if package_dir.exists():
            shutil.rmtree(package_dir)
        staged_package.replace(package_dir)
    finally:
        shutil.rmtree(temporary_parent, ignore_errors=True)

    build_windows_archive(package_dir, output)
    verify_windows_archive(output, normalized_version)
    return output


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", required=True)
    parser.add_argument("--base-archive", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--package-dir", type=Path, required=True)
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    return parser


def main() -> int:
    args = _build_parser().parse_args()
    if sys.version_info[:2] != (3, 11):
        print("Windows baseline repack requires CPython 3.11")
        return 2
    try:
        result = build_from_verified_baseline(
            version=args.version,
            base_archive=args.base_archive,
            output=args.output,
            package_dir=args.package_dir,
            project_root=args.project_root,
        )
    except (OSError, RuntimeError, ValueError, EOFError, zipfile.BadZipFile) as exc:
        print(f"Windows baseline build failed: {exc}")
        return 1
    print(result.resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
