#!/usr/bin/env python3
"""Build a Windows Explorer-compatible HunterX release ZIP."""

from __future__ import annotations

import argparse
import os
import tempfile
import zipfile
from pathlib import Path, PurePosixPath


def build_windows_archive(source: Path, output: Path) -> Path:
    """Archive *source* contents without synthetic ``./`` members."""

    source = source.resolve()
    output = output.resolve()
    if not source.is_dir():
        raise ValueError(f"Windows package directory is missing: {source}")
    try:
        output.relative_to(source)
    except ValueError:
        pass
    else:
        raise ValueError("Windows archive output must be outside the package directory")

    files: list[tuple[Path, str]] = []
    for candidate in sorted(source.rglob("*")):
        if candidate.is_symlink():
            raise ValueError(f"Windows package symlink is not allowed: {candidate}")
        if candidate.is_dir():
            continue
        if not candidate.is_file():
            raise ValueError(f"Windows package entry is not a regular file: {candidate}")
        relative_name = candidate.relative_to(source).as_posix()
        if PurePosixPath(relative_name).as_posix() != relative_name:
            raise ValueError(f"Non-portable Windows ZIP path: {relative_name!r}")
        files.append((candidate, relative_name))
    if not files:
        raise ValueError(f"Windows package directory is empty: {source}")

    output.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=output.parent,
        prefix=f".{output.name}.",
        suffix=".tmp",
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        with zipfile.ZipFile(
            temporary,
            "w",
            compression=zipfile.ZIP_DEFLATED,
            compresslevel=9,
            allowZip64=True,
        ) as archive:
            for source_file, archive_name in files:
                archive.write(source_file, archive_name)
        temporary.replace(output)
    finally:
        temporary.unlink(missing_ok=True)
    return output


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser


def main() -> int:
    args = _build_parser().parse_args()
    try:
        archive = build_windows_archive(args.source, args.output)
    except (OSError, ValueError, zipfile.BadZipFile) as exc:
        print(f"Windows archive build failed: {exc}")
        return 1
    print(archive)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
