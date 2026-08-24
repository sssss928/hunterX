#!/usr/bin/env python3
"""Repack one PyInstaller entry script while preserving a proven Windows binary.

This release helper is intentionally narrow: it keeps the original PE
bootloader, resources, runtime hooks, dependency archive, compression flags,
and TOC order, and replaces only the selected application entry script.  The
replacement script loads HunterX application modules from ``app_src`` inside
the executable's existing PyInstaller contents directory.

The helper must run with the same Python major/minor version as the packaged
runtime. HunterX v0.5.2 Windows packages use CPython 3.11.
"""

from __future__ import annotations

import argparse
import marshal
import os
import shutil
import struct
import sys
import tempfile
import zlib
from dataclasses import dataclass
from pathlib import Path

from PyInstaller.archive.readers import CArchiveReader
from PyInstaller.archive.writers import CArchiveWriter


@dataclass(frozen=True)
class ArchiveCookie:
    magic: bytes
    archive_length: int
    toc_offset: int
    toc_length: int
    pyvers: int
    pylib_name: bytes


@dataclass(frozen=True)
class RawEntry:
    name: str
    data: bytes
    compressed: bool
    typecode: str


def _read_cookie(executable: Path) -> tuple[ArchiveCookie, int, int]:
    reader = CArchiveReader(str(executable))
    cookie_offset = reader._end_offset - reader._COOKIE_LENGTH
    with executable.open("rb") as stream:
        stream.seek(cookie_offset)
        values = struct.unpack(reader._COOKIE_FORMAT, stream.read(reader._COOKIE_LENGTH))
    cookie = ArchiveCookie(*values)
    return cookie, reader._start_offset, reader._end_offset


def _read_entries(executable: Path, cookie: ArchiveCookie, archive_start: int) -> list[RawEntry]:
    header_format = CArchiveReader._TOC_ENTRY_FORMAT
    header_length = CArchiveReader._TOC_ENTRY_LENGTH
    entries: list[RawEntry] = []

    with executable.open("rb") as stream:
        stream.seek(archive_start + cookie.toc_offset)
        toc_data = stream.read(cookie.toc_length)
        cursor = 0
        while cursor < len(toc_data):
            header = toc_data[cursor : cursor + header_length]
            if len(header) != header_length:
                raise ValueError("truncated PyInstaller TOC header")
            entry_length, offset, length, raw_length, compressed, typecode = struct.unpack(
                header_format,
                header,
            )
            name_bytes = toc_data[cursor + header_length : cursor + entry_length]
            name = name_bytes.rstrip(b"\0").decode("utf-8")
            cursor += entry_length

            stream.seek(archive_start + offset)
            data = stream.read(length)
            if len(data) != length:
                raise ValueError(f"truncated PyInstaller entry: {name}")
            if compressed:
                data = zlib.decompress(data)
            if len(data) != raw_length:
                raise ValueError(f"invalid uncompressed size for PyInstaller entry: {name}")
            entries.append(
                RawEntry(
                    name=name,
                    data=data,
                    compressed=bool(compressed),
                    typecode=typecode.decode("ascii"),
                )
            )

    return entries


def _bootstrap_source(entry_name: str) -> str:
    return f'''\
import importlib.abc
import importlib.util
import os
import runpy
import sys

_HUNTERX_APP_SRC = os.path.join(sys._MEIPASS, "app_src")
_HUNTERX_ENTRY = os.path.join(_HUNTERX_APP_SRC, {entry_name!r} + ".py")


class _HunterXExternalSourceFinder(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        relative = fullname.replace(".", os.sep)
        package_init = os.path.join(_HUNTERX_APP_SRC, relative, "__init__.py")
        if os.path.isfile(package_init):
            return importlib.util.spec_from_file_location(
                fullname,
                package_init,
                submodule_search_locations=[os.path.dirname(package_init)],
            )
        module_file = os.path.join(_HUNTERX_APP_SRC, relative + ".py")
        if os.path.isfile(module_file):
            return importlib.util.spec_from_file_location(fullname, module_file)
        return None


if not os.path.isfile(_HUNTERX_ENTRY):
    raise RuntimeError("HunterX v0.5.2 application source is missing: " + _HUNTERX_ENTRY)

sys.meta_path.insert(0, _HunterXExternalSourceFinder())
sys.path.insert(0, _HUNTERX_APP_SRC)
runpy.run_path(_HUNTERX_ENTRY, run_name="__main__")
'''


def _write_archive(
    output_archive: Path,
    entries: list[RawEntry],
    cookie: ArchiveCookie,
    entry_name: str,
) -> None:
    source = _bootstrap_source(entry_name)
    code = compile(source, f"{entry_name}.py", "exec", optimize=0)
    replacement = marshal.dumps(code)
    replacement_count = 0

    writer = CArchiveWriter.__new__(CArchiveWriter)
    writer._collected_names = set()
    with output_archive.open("wb") as stream:
        toc = []
        for entry in entries:
            data = entry.data
            if entry.name == entry_name and entry.typecode in {"s", "s1", "s2"}:
                data = replacement
                replacement_count += 1
            toc.append(
                writer._write_blob(
                    stream,
                    data,
                    entry.name,
                    "s" if entry.name == entry_name else entry.typecode,
                    compress=entry.compressed,
                )
            )

        if replacement_count != 1:
            raise ValueError(
                f"expected exactly one {entry_name!r} script entry; found {replacement_count}"
            )

        toc_offset = stream.tell()
        toc_data = writer._serialize_toc(toc)
        stream.write(toc_data)
        archive_length = toc_offset + len(toc_data) + CArchiveWriter._COOKIE_LENGTH
        stream.write(
            struct.pack(
                CArchiveWriter._COOKIE_FORMAT,
                cookie.magic,
                archive_length,
                toc_offset,
                len(toc_data),
                cookie.pyvers,
                cookie.pylib_name,
            )
        )


def repack(executable: Path, output: Path, entry_name: str) -> Path:
    executable = executable.resolve()
    output = output.resolve()
    if not executable.is_file():
        raise FileNotFoundError(executable)
    if executable == output:
        raise ValueError("output must differ from the source executable")

    cookie, archive_start, archive_end = _read_cookie(executable)
    expected_pyvers = sys.version_info.major * 100 + sys.version_info.minor
    if cookie.pyvers != expected_pyvers:
        raise RuntimeError(
            f"packaged Python is {cookie.pyvers}, but repacker is {expected_pyvers}; "
            "use the matching CPython major/minor"
        )
    if archive_end != executable.stat().st_size:
        raise ValueError("unexpected data follows the PyInstaller CArchive")
    entries = _read_entries(executable, cookie, archive_start)

    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="hunterx-repack-", dir=output.parent) as temp_dir:
        temp_root = Path(temp_dir)
        archive_path = temp_root / "archive.pkg"
        candidate = temp_root / output.name
        _write_archive(archive_path, entries, cookie, entry_name)
        with candidate.open("wb") as destination, executable.open("rb") as source:
            remaining = archive_start
            while remaining:
                chunk = source.read(min(1024 * 1024, remaining))
                if not chunk:
                    raise ValueError("truncated PE bootloader")
                destination.write(chunk)
                remaining -= len(chunk)
            with archive_path.open("rb") as archive_stream:
                shutil.copyfileobj(archive_stream, destination, length=1024 * 1024)
        os.chmod(candidate, executable.stat().st_mode)
        candidate.replace(output)

    verify_reader = CArchiveReader(str(output))
    if verify_reader.options != CArchiveReader(str(executable)).options:
        raise ValueError("PyInstaller archive options changed during repack")
    replacement_code = marshal.loads(verify_reader.extract(entry_name))
    if "app_src" not in replacement_code.co_names and "app_src" not in replacement_code.co_consts:
        raise ValueError("replacement entrypoint verification failed")
    if output.read_bytes()[:2] != b"MZ":
        raise ValueError("output is not a Windows PE executable")
    return output


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--executable", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--entry-name", choices=("settings", "nodriver_tixcraft"), required=True)
    return parser


def main() -> int:
    args = _build_parser().parse_args()
    if sys.version_info[:2] != (3, 11):
        print("repack failed: CPython 3.11 is required", file=sys.stderr)
        return 2
    try:
        result = repack(args.executable, args.output, args.entry_name)
    except (OSError, RuntimeError, ValueError, EOFError, zlib.error) as exc:
        print(f"repack failed: {exc}", file=sys.stderr)
        return 1
    print(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
