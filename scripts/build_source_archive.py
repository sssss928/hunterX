#!/usr/bin/env python3
"""Build and verify the exact HunterX source ZIP for a release commit."""

from __future__ import annotations

import argparse
import subprocess
import tempfile
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
    commit: str | None = None,
    working_tree: bool = False,
    qualifier: str | None = None,
) -> Path:
    """Create a commit or local-working-tree archive and verify it fail-closed."""
    normalized_version = release_utils.validate_semver(version)
    normalized_qualifier = release_utils.normalize_qualifier(qualifier)
    if normalized_qualifier is not None:
        release_utils.require_build_qualifier(normalized_qualifier)
    if working_tree and normalized_qualifier is not None:
        raise ValueError("Release artifacts must not be built from a working tree")
    prefix = release_utils.source_archive_prefix(normalized_version)
    expected_output_name = release_utils.artifact_name(
        normalized_version,
        "source",
        normalized_qualifier,
    )
    if output.name != expected_output_name:
        raise ValueError(f"output must be named {expected_output_name!r}")

    repo_root = repo_root.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)

    if working_tree:
        declared_version = release_utils.project_version(
            repo_root / "src" / "hunter_metadata.py"
        )
        if declared_version != normalized_version:
            raise ValueError(
                "release version mismatch: "
                f"requested {normalized_version}, working tree declares {declared_version}"
            )
        files = working_tree_source_files(repo_root, prefix)
        verified_commit = commit or "working-tree"
    else:
        if commit is None:
            raise ValueError("release commit is required for committed source archives")
        verified_commit = release_utils.resolve_clean_commit(repo_root, commit)
        try:
            metadata_source = subprocess.run(
                [
                    "git",
                    "show",
                    f"{verified_commit}:src/hunter_metadata.py",
                ],
                cwd=repo_root,
                check=True,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="strict",
            ).stdout
        except (OSError, subprocess.CalledProcessError, UnicodeError) as exc:
            raise ValueError(
                "release commit is missing valid UTF-8 src/hunter_metadata.py"
            ) from exc
        declared_version = release_utils.project_version_from_text(
            metadata_source,
            f"{verified_commit}:src/hunter_metadata.py",
        )
        if declared_version != normalized_version:
            raise ValueError(
                "release version mismatch: "
                f"requested {normalized_version}, commit declares {declared_version}"
            )

    with tempfile.TemporaryDirectory(
        prefix=f".{output.name}.",
        dir=output.parent,
    ) as temporary_directory:
        staged_output = Path(temporary_directory) / output.name
        if working_tree:
            with zipfile.ZipFile(
                staged_output,
                "w",
                compression=zipfile.ZIP_DEFLATED,
            ) as archive:
                for name, content in sorted(files.items()):
                    archive.writestr(name, content)
        else:
            subprocess.run(
                [
                    "git",
                    "-c",
                    "core.autocrlf=false",
                    "-c",
                    "core.eol=lf",
                    "archive",
                    "--format=zip",
                    f"--prefix={prefix}",
                    f"--output={staged_output}",
                    verified_commit,
                ],
                cwd=repo_root,
                check=True,
            )
            release_utils.resolve_clean_commit(repo_root, verified_commit)
        verify_source_archive(
            staged_output,
            normalized_version,
            repo_root,
            verified_commit,
            working_tree=working_tree,
            qualifier=normalized_qualifier,
        )
        output.unlink(missing_ok=True)
        staged_output.replace(output)
    return output


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    parser.add_argument("--commit", required=True)
    parser.add_argument(
        "--qualifier",
        choices=(
            release_utils.RC2_QUALIFIER,
            release_utils.RC3_QUALIFIER,
            release_utils.FINAL_QUALIFIER,
        ),
        required=True,
    )
    return parser


def main() -> int:
    args = _build_parser().parse_args()
    archive = build_source_archive(
        version=args.version,
        output=args.output,
        repo_root=args.repo_root,
        commit=args.commit,
        qualifier=args.qualifier,
    )
    print(archive.resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
