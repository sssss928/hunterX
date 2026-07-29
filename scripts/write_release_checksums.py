#!/usr/bin/env python3
"""Write SHA-256 checksums for release assets."""

from __future__ import annotations

import argparse
import hashlib
from pathlib import Path

import release_utils


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_checksums(paths: list[Path], output: Path) -> Path:
    if not paths:
        raise ValueError("At least one release asset is required.")
    output.parent.mkdir(parents=True, exist_ok=True)

    lines: list[str] = []
    for path in paths:
        if not path.is_file():
            raise ValueError(f"Release asset is missing: {path}")
        lines.append(f"{sha256_file(path)}  {path.name}")

    output.write_text("\n".join(lines) + "\n", encoding="ascii")
    return output


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("assets", type=Path, nargs="+")
    parser.add_argument("--version", required=True)
    parser.add_argument("--output", type=Path)
    return parser


def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()
    try:
        output = args.output or Path("dist/release") / release_utils.checksum_name(args.version)
        manifest = write_checksums(args.assets, output)
    except ValueError as exc:
        parser.error(str(exc))
    else:
        print(manifest.resolve())
        return 0

    return 2


if __name__ == "__main__":
    raise SystemExit(main())
