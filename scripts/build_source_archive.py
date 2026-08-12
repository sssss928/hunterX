#!/usr/bin/env python3
"""Build and verify the exact HunterX source ZIP for a release commit."""

from __future__ import annotations

import argparse
import subprocess
import zipfile
from pathlib import Path

import release_utils
from verify_release_archive import verify_source_archive
from verify_release_archive import working_tree_source_files


def build_source_archive(
    *,
    version: str,
    output: Path,
    repo_root: Path,
    commit: str = "HEAD",
    working_tree: bool = False,
) -> Path:
    """Create a commit or local-working-tree archive and verify it fail-closed."""
    normalized_version = release_utils.validate_semver(version)
    prefix = release_utils.source_archive_prefix(normalized_version)
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        output.unlink()

    if working_tree:
        files = working_tree_source_files(repo_root, prefix)
        with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for name, content in sorted(files.items()):
                archive.writestr(name, content)
    else:
        subprocess.run(
            [
                "git",
                "archive",
                "--format=zip",
                f"--prefix={prefix}",
                f"--output={output}",
                commit,
            ],
            cwd=repo_root,
            check=True,
        )
    verify_source_archive(
        output,
        normalized_version,
        repo_root,
        commit,
        working_tree=working_tree,
    )
    return output


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    parser.add_argument("--commit", default="HEAD")
    parser.add_argument("--working-tree", action="store_true")
    return parser


def main() -> int:
    args = _build_parser().parse_args()
    archive = build_source_archive(
        version=args.version,
        output=args.output,
        repo_root=args.repo_root,
        commit=args.commit,
        working_tree=args.working_tree,
    )
    print(archive.resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
