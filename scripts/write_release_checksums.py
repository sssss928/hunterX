#!/usr/bin/env python3
"""Write SHA-256 checksums for release assets."""

from __future__ import annotations

import argparse
import hashlib
import re
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


def verify_checksums(manifest: Path, asset_dir: Path) -> list[Path]:
    """Verify a strict SHA-256 manifest after cross-job artifact transfer."""

    if not manifest.is_file():
        raise ValueError(f"Checksum manifest is missing: {manifest}")
    verified: list[Path] = []
    seen: set[str] = set()
    for line_number, line in enumerate(manifest.read_text(encoding="ascii").splitlines(), 1):
        match = re.fullmatch(r"([0-9a-f]{64})  ([^/\\]+)", line)
        if match is None:
            raise ValueError(f"Invalid checksum manifest line {line_number}.")
        expected, filename = match.groups()
        if filename in seen:
            raise ValueError(f"Duplicate checksum entry: {filename}")
        seen.add(filename)
        asset = asset_dir / filename
        if not asset.is_file() or sha256_file(asset) != expected:
            raise ValueError(f"Checksum verification failed: {filename}")
        verified.append(asset)
    if not verified:
        raise ValueError("Checksum manifest is empty.")
    return verified


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("assets", type=Path, nargs="*")
    parser.add_argument("--version", required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--verify-manifest", type=Path)
    parser.add_argument("--asset-dir", type=Path, default=Path("dist/release"))
    return parser


def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()
    try:
        if args.verify_manifest is not None:
            verified = verify_checksums(args.verify_manifest, args.asset_dir)
            print(f"Verified {len(verified)} release assets.")
            return 0
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
