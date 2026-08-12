#!/usr/bin/env python3
"""Fail-closed integrity checks for HunterX release ZIP archives."""

from __future__ import annotations

import argparse
import json
import subprocess
import tarfile
import zipfile
from pathlib import Path, PurePosixPath


DENIED_PARTS = frozenset(
    {
        ".git",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        "__pycache__",
        "build",
        "dist",
        "instances",
        "logs",
        "profiles",
        "tmp",
        "venv",
    }
)
DENIED_FILENAMES = frozenset(
    {
        ".coverage",
        ".env",
        "config.json",
        "heartbeat.txt",
        "maxbot_int28_idle.txt",
        "maxbot_last_url.txt",
        "maxbot_question.txt",
        "settings.json",
    }
)
DENIED_SUFFIXES = (".key", ".p12", ".pem", ".pfx")
WINDOWS_VENDOR_DIST_PREFIXES = frozenset(
    {
        ("www", "dist"),
        ("_nodriver_internal", "www", "dist"),
        ("_settings_internal", "www", "dist"),
    }
)
WINDOWS_ALLOWED_PUBLIC_CERTIFICATES = frozenset(
    {
        "_nodriver_internal/certifi/cacert.pem",
        "_settings_internal/certifi/cacert.pem",
    }
)


def _normalized_file_entries(archive: zipfile.ZipFile) -> dict[str, zipfile.ZipInfo]:
    entries: dict[str, zipfile.ZipInfo] = {}
    casefolded: set[str] = set()
    for info in archive.infolist():
        raw_name = info.filename.replace("\\", "/")
        path = PurePosixPath(raw_name)
        if (
            not raw_name
            or info.filename != raw_name
            or raw_name.startswith("./")
            or "//" in raw_name
            or path.as_posix() != raw_name.rstrip("/")
            or raw_name.startswith("/")
            or path.is_absolute()
            or ".." in path.parts
        ):
            raise ValueError(f"Unsafe or non-portable ZIP path: {info.filename!r}")
        normalized = path.as_posix().rstrip("/")
        if not normalized or info.is_dir():
            continue
        folded = normalized.casefold()
        if folded in casefolded:
            raise ValueError(f"Duplicate ZIP path ignoring case: {normalized!r}")
        casefolded.add(folded)
        entries[normalized] = info
    return entries


def _assert_no_denied_paths(paths: set[str]) -> None:
    """Apply the strict Windows-package denylist to every path component."""
    for name in paths:
        parts = tuple(part.casefold() for part in PurePosixPath(name).parts)
        filename = parts[-1]
        for index, part in enumerate(parts):
            if part not in DENIED_PARTS:
                continue
            path_prefix = parts[: index + 1]
            if part == "dist" and path_prefix in WINDOWS_VENDOR_DIST_PREFIXES:
                continue
            raise ValueError(f"Denied release path: {name}")
        if (
            filename in DENIED_FILENAMES
            or filename.startswith(".env.")
            or filename.startswith(".coverage.")
        ):
            raise ValueError(f"Denied release file: {name}")
        if (
            filename.endswith(DENIED_SUFFIXES)
            and PurePosixPath(name).as_posix().casefold()
            not in WINDOWS_ALLOWED_PUBLIC_CERTIFICATES
        ):
            raise ValueError(f"Denied private-key file: {name}")


def _assert_no_denied_source_paths(paths: set[str], prefix: str) -> None:
    """Reject generated source roots without blocking tracked nested vendor assets."""
    for name in paths:
        relative_name = name.removeprefix(prefix)
        parts = tuple(
            part.casefold() for part in PurePosixPath(relative_name).parts
        )
        filename = parts[-1]
        if len(parts) > 1 and parts[0] in DENIED_PARTS:
            raise ValueError(f"Denied source top-level directory: {name}")
        if (
            filename in DENIED_FILENAMES
            or filename.startswith(".env.")
            or filename.startswith(".coverage.")
        ):
            raise ValueError(f"Denied release file: {name}")
        if filename.endswith(DENIED_SUFFIXES):
            raise ValueError(f"Denied private-key file: {name}")


def _open_checked_zip(
    path: Path,
    *,
    enforce_strict_denylist: bool = True,
) -> tuple[zipfile.ZipFile, dict[str, zipfile.ZipInfo]]:
    archive = zipfile.ZipFile(path)
    corrupt_name = archive.testzip()
    if corrupt_name is not None:
        archive.close()
        raise ValueError(f"ZIP CRC failure: {corrupt_name}")
    entries = _normalized_file_entries(archive)
    if enforce_strict_denylist:
        _assert_no_denied_paths(set(entries))
    return archive, entries


def verify_windows_archive(path: Path, version: str) -> dict[str, object]:
    archive, entries = _open_checked_zip(path)
    try:
        required = {
            "CHANGELOG.md",
            "LICENSE",
            "README.md",
            "README_Release.txt",
            "nodriver_tixcraft.exe",
            "settings.exe",
            "www/css/settings.css",
            "www/favicon.ico",
            "www/settings.html",
            "www/settings.js",
            "_nodriver_internal/base_library.zip",
            "_nodriver_internal/python311.dll",
            "_settings_internal/base_library.zip",
            "_settings_internal/python311.dll",
        }
        missing = sorted(required - set(entries))
        if missing:
            raise ValueError(f"Windows archive missing required files: {missing}")
        if any(
            PurePosixPath(name).parts[0].casefold() == "_internal"
            for name in entries
        ):
            raise ValueError("Legacy shared _internal runtime is forbidden")
        if not any(name.startswith("assets/") for name in entries):
            raise ValueError("Windows archive has no assets")
        expected_name = f"hunterX_windows_{version}.zip"
        if path.name != expected_name:
            raise ValueError(
                f"Windows archive name {path.name!r} does not match {expected_name!r}"
            )
        return {
            "archive": str(path.resolve()),
            "entries": len(entries),
            "missing": 0,
            "crc": "ok",
            "runtime_layout": "isolated",
        }
    finally:
        archive.close()


def _git_archive_files(
    repo_root: Path,
    commit: str,
    prefix: str,
) -> dict[str, bytes]:
    process = subprocess.Popen(
        [
            "git",
            "archive",
            "--format=tar",
            f"--prefix={prefix}",
            commit,
        ],
        cwd=repo_root,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    assert process.stdout is not None
    expected: dict[str, bytes] = {}
    try:
        with tarfile.open(fileobj=process.stdout, mode="r|") as archive:
            for member in archive:
                if not member.isfile():
                    continue
                extracted = archive.extractfile(member)
                if extracted is None:
                    raise ValueError(f"Unable to read git archive member: {member.name}")
                expected[PurePosixPath(member.name).as_posix()] = extracted.read()
    finally:
        process.stdout.close()
    stderr = process.stderr.read().decode("utf-8", errors="replace") if process.stderr else ""
    return_code = process.wait()
    if return_code != 0:
        raise ValueError(f"git archive failed ({return_code}): {stderr.strip()}")
    return expected


def working_tree_source_files(
    repo_root: Path,
    prefix: str,
) -> dict[str, bytes]:
    """Read tracked plus non-ignored untracked files from the local tree."""

    result = subprocess.run(
        [
            "git",
            "ls-files",
            "-z",
            "--cached",
            "--others",
            "--exclude-standard",
        ],
        cwd=repo_root,
        capture_output=True,
        check=True,
    )
    relative_names = sorted(
        {
            item.decode("utf-8", errors="strict").replace("\\", "/")
            for item in result.stdout.split(b"\0")
            if item
        }
    )
    deleted_result = subprocess.run(
        ["git", "ls-files", "-z", "--deleted"],
        cwd=repo_root,
        capture_output=True,
        check=True,
    )
    deleted_names = {
        item.decode("utf-8", errors="strict").replace("\\", "/")
        for item in deleted_result.stdout.split(b"\0")
        if item
    }
    relative_names = [name for name in relative_names if name not in deleted_names]
    archive_names = {f"{prefix}{name}" for name in relative_names}
    _assert_no_denied_source_paths(archive_names, prefix)

    files: dict[str, bytes] = {}
    root = repo_root.resolve()
    for relative_name in relative_names:
        candidate = root / Path(relative_name)
        if candidate.is_symlink():
            raise ValueError(f"Source symlink is not allowed: {relative_name}")
        source = candidate.resolve()
        if not source.is_file():
            raise ValueError(f"Source entry must be a regular file: {relative_name}")
        try:
            source.relative_to(root)
        except ValueError as exc:
            raise ValueError(f"Source entry escapes repository: {relative_name}") from exc
        files[f"{prefix}{relative_name}"] = source.read_bytes()
    return files


def verify_source_archive(
    path: Path,
    version: str,
    repo_root: Path,
    commit: str,
    *,
    working_tree: bool = False,
) -> dict[str, object]:
    expected_prefix = f"hunterX-{version}/"
    expected_name = f"hunterX_source_{version}.zip"
    if path.name != expected_name:
        raise ValueError(
            f"Source archive name {path.name!r} does not match {expected_name!r}"
        )
    archive, entries = _open_checked_zip(path, enforce_strict_denylist=False)
    try:
        if any(not name.startswith(expected_prefix) for name in entries):
            raise ValueError(f"Source ZIP root must be {expected_prefix!r}")
        _assert_no_denied_source_paths(set(entries), expected_prefix)
        expected = (
            working_tree_source_files(repo_root, expected_prefix)
            if working_tree
            else _git_archive_files(repo_root, commit, expected_prefix)
        )
        actual_names = set(entries)
        expected_names = set(expected)
        missing = sorted(expected_names - actual_names)
        extra = sorted(actual_names - expected_names)
        mismatch = sorted(
            name
            for name in expected_names & actual_names
            if archive.read(entries[name]) != expected[name]
        )
        if missing or extra or mismatch:
            raise ValueError(
                "Source archive differs from "
                f"{'working tree' if working_tree else 'commit ' + commit}: "
                f"missing={len(missing)} extra={len(extra)} "
                f"mismatch={len(mismatch)}"
            )
        return {
            "archive": str(path.resolve()),
            "source": "working-tree" if working_tree else f"commit:{commit}",
            "entries": len(entries),
            "missing": 0,
            "extra": 0,
            "mismatch": 0,
            "crc": "ok",
            "prefix": expected_prefix,
        }
    finally:
        archive.close()


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="kind", required=True)

    windows = subparsers.add_parser("windows")
    windows.add_argument("--archive", type=Path, required=True)
    windows.add_argument("--version", required=True)

    source = subparsers.add_parser("source")
    source.add_argument("--archive", type=Path, required=True)
    source.add_argument("--version", required=True)
    source.add_argument("--repo-root", type=Path, default=Path.cwd())
    source.add_argument("--commit", default="HEAD")
    source.add_argument("--working-tree", action="store_true")
    return parser


def main() -> int:
    args = _build_parser().parse_args()
    try:
        if args.kind == "windows":
            result = verify_windows_archive(args.archive, args.version)
        else:
            result = verify_source_archive(
                args.archive,
                args.version,
                args.repo_root,
                args.commit,
                working_tree=args.working_tree,
            )
    except (OSError, ValueError, zipfile.BadZipFile) as exc:
        print(f"release archive verification failed: {exc}")
        return 1
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
