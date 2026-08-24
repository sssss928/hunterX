#!/usr/bin/env python3
"""Fail-closed native smoke for an extracted HunterX Windows package."""

from __future__ import annotations

import argparse
import json
import stat
import subprocess
import tempfile
import zipfile
from pathlib import Path, PurePosixPath


EXPECTED_DISPLAY_VERSION = "HunterX (0.5.2)"


def _run(executable: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(executable), *arguments],
        cwd=executable.parent,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
    )


def verify_package(package_dir: Path) -> dict[str, str]:
    package_dir = package_dir.resolve()
    settings_exe = package_dir / "settings.exe"
    bot_exe = package_dir / "nodriver_tixcraft.exe"
    for executable in (settings_exe, bot_exe):
        if not executable.is_file() or executable.read_bytes()[:2] != b"MZ":
            raise ValueError(f"missing or invalid PE executable: {executable}")

    settings_version = _run(settings_exe, "--version").stdout.strip()
    bot_version = _run(bot_exe, "--version").stdout.strip()
    smoke_output = _run(settings_exe, "HTTP", "/run", "smoke", "test").stdout
    if settings_version != EXPECTED_DISPLAY_VERSION:
        raise ValueError(f"settings version mismatch: {settings_version!r}")
    if bot_version != EXPECTED_DISPLAY_VERSION:
        raise ValueError(f"bot version mismatch: {bot_version!r}")
    if "smoke test ok: HunterX (0.5.2)" not in smoke_output:
        raise ValueError(f"packaged settings smoke did not pass: {smoke_output!r}")
    if "smoke test failed" in smoke_output.casefold():
        raise ValueError(f"packaged settings smoke reported a failure: {smoke_output!r}")
    return {
        "settings_version": settings_version,
        "bot_version": bot_version,
        "settings_smoke": "PASS",
    }


def _checked_archive_members(
    archive: zipfile.ZipFile,
) -> dict[str, zipfile.ZipInfo]:
    corrupt_name = archive.testzip()
    if corrupt_name is not None:
        raise ValueError(f"ZIP CRC failure: {corrupt_name}")

    members: dict[str, zipfile.ZipInfo] = {}
    casefolded: set[str] = set()
    for info in archive.infolist():
        raw_name = info.filename.replace("\\", "/")
        path = PurePosixPath(raw_name)
        if (
            not raw_name
            or raw_name != info.filename
            or raw_name.startswith("./")
            or raw_name.startswith("/")
            or "//" in raw_name
            or path.is_absolute()
            or ".." in path.parts
            or path.as_posix() != raw_name.rstrip("/")
        ):
            raise ValueError(f"Unsafe or non-portable ZIP path: {info.filename!r}")
        normalized = path.as_posix().rstrip("/")
        if not normalized:
            continue
        folded = normalized.casefold()
        if folded in casefolded:
            raise ValueError(f"Duplicate ZIP path ignoring case: {normalized!r}")
        casefolded.add(folded)
        unix_mode = info.external_attr >> 16
        if stat.S_ISLNK(unix_mode):
            raise ValueError(f"ZIP symlink is forbidden: {normalized!r}")
        if not info.is_dir():
            members[normalized] = info
    return members


def verify_archive_package(archive_path: Path) -> dict[str, str]:
    """Safely extract an archive into a new directory, then run native smoke."""

    archive_path = archive_path.resolve()
    with zipfile.ZipFile(archive_path) as archive:
        members = _checked_archive_members(archive)
        with tempfile.TemporaryDirectory(prefix="hunterx-v052-packaged-smoke-") as temp:
            package_dir = Path(temp).resolve()
            for name, info in members.items():
                destination = package_dir.joinpath(*PurePosixPath(name).parts)
                destination.parent.mkdir(parents=True, exist_ok=True)
                resolved_destination = destination.resolve()
                try:
                    resolved_destination.relative_to(package_dir)
                except ValueError as exc:
                    raise ValueError(f"ZIP member escapes extraction root: {name!r}") from exc
                with archive.open(info) as source, resolved_destination.open("xb") as target:
                    while chunk := source.read(1024 * 1024):
                        target.write(chunk)
            return verify_package(package_dir)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--archive", type=Path)
    source.add_argument("--package-dir", type=Path)
    args = parser.parse_args()
    try:
        result = (
            verify_archive_package(args.archive)
            if args.archive is not None
            else verify_package(args.package_dir)
        )
    except (OSError, ValueError, zipfile.BadZipFile, subprocess.SubprocessError) as exc:
        print(f"packaged smoke failed: {exc}")
        return 1
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
