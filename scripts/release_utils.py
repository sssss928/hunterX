#!/usr/bin/env python3
"""Release helper functions shared by GitHub Actions and tests."""

from __future__ import annotations

import argparse
import ast
import re
import subprocess
import sys
from pathlib import Path


PROJECT_NAME = "HunterX"
SEMVER_RE = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")
FULL_COMMIT_RE = re.compile(r"^[0-9a-fA-F]{40}$")
RELEASE_QUALIFIERS = frozenset({"rc", "rc2", "rc3", "final"})
RC2_QUALIFIER = "rc2"
RC3_QUALIFIER = "rc3"
FINAL_QUALIFIER = "final"


def is_valid_semver(version: str) -> bool:
    """Return True for strict x.y.z SemVer used by HunterX releases."""
    return bool(SEMVER_RE.fullmatch(version.strip()))


def validate_semver(version: str) -> str:
    """Return a normalized version or raise ValueError."""
    normalized = version.strip()
    if not is_valid_semver(normalized):
        raise ValueError(f"Invalid release version: {version!r}. Expected strict SemVer like 0.1.0.")
    return normalized


def normalize_qualifier(qualifier: str | None) -> str | None:
    """Normalize an optional release qualifier and reject unknown values."""

    if qualifier is None:
        return None
    normalized = qualifier.strip().casefold()
    if normalized not in RELEASE_QUALIFIERS:
        raise ValueError(
            f"Invalid release qualifier: {qualifier!r}. "
            f"Expected one of {sorted(RELEASE_QUALIFIERS)!r}."
        )
    return normalized


def require_rc2_qualifier(qualifier: str | None) -> str:
    """Require the fail-closed Round-2 RC release profile."""

    normalized = normalize_qualifier(qualifier)
    if normalized != RC2_QUALIFIER:
        raise ValueError(
            "Round-2 release pipeline requires qualifier 'rc2'; "
            f"refusing {qualifier!r}."
        )
    return normalized


def require_rc3_qualifier(qualifier: str | None) -> str:
    """Require the fail-closed Final-Layer RC3 release profile."""

    normalized = normalize_qualifier(qualifier)
    if normalized != RC3_QUALIFIER:
        raise ValueError(
            "Final-Layer release pipeline requires qualifier 'rc3'; "
            f"refusing {qualifier!r}."
        )
    return normalized


def require_candidate_qualifier(qualifier: str | None) -> str:
    """Require one of the explicitly supported committed RC profiles."""

    normalized = normalize_qualifier(qualifier)
    if normalized not in {RC2_QUALIFIER, RC3_QUALIFIER}:
        raise ValueError(
            "Release-candidate pipeline requires qualifier 'rc2' or 'rc3'; "
            f"refusing {qualifier!r}."
        )
    return normalized


def require_build_qualifier(qualifier: str | None) -> str:
    """Require an explicitly supported committed RC or FINAL profile."""

    normalized = normalize_qualifier(qualifier)
    if normalized not in {RC2_QUALIFIER, RC3_QUALIFIER, FINAL_QUALIFIER}:
        raise ValueError(
            "Release build pipeline requires qualifier 'rc2', 'rc3', or 'final'; "
            f"refusing {qualifier!r}."
        )
    return normalized


def resolve_version(
    event_name: str,
    ref_name: str,
    input_version: str | None = None,
    qualifier: str | None = None,
) -> str:
    """Resolve and validate a release version from a tag push or workflow_dispatch input."""
    normalized_qualifier = normalize_qualifier(qualifier)
    if event_name == "workflow_dispatch":
        if input_version is None or not input_version.strip():
            raise ValueError("workflow_dispatch requires a version input such as 0.1.0.")
        if input_version.strip().startswith("v"):
            raise ValueError("workflow_dispatch version must not include a leading v.")
        return validate_semver(input_version)

    if not ref_name.startswith("v"):
        raise ValueError(f"Tag release must use v-prefixed refs such as v0.1.0, got {ref_name!r}.")
    tag_version = ref_name[1:]
    if normalized_qualifier and normalized_qualifier != FINAL_QUALIFIER:
        expected_suffix = f"-{normalized_qualifier}"
        if not tag_version.casefold().endswith(expected_suffix):
            raise ValueError(
                f"{normalized_qualifier} release tag must end in {expected_suffix!r}, "
                f"got {ref_name!r}."
            )
        tag_version = tag_version[: -len(expected_suffix)]
    elif normalized_qualifier == FINAL_QUALIFIER and "-" in tag_version:
        raise ValueError(
            f"FINAL release tag must be the official vX.Y.Z tag without a suffix, got {ref_name!r}."
        )
    return validate_semver(tag_version)


def artifact_name(
    version: str,
    platform: str = "windows",
    qualifier: str | None = None,
) -> str:
    """Return the release ZIP name for a platform."""
    normalized = validate_semver(version)
    platform_slug = platform.strip().lower()
    if not re.fullmatch(r"[a-z0-9_]+", platform_slug):
        raise ValueError(f"Invalid platform slug: {platform!r}")
    normalized_qualifier = normalize_qualifier(qualifier)
    suffix = f"_{normalized_qualifier}" if normalized_qualifier else ""
    return f"hunterX_{platform_slug}_{normalized}{suffix}.zip"


def checksum_name(version: str, qualifier: str | None = None) -> str:
    """Return the checksum manifest name for a release."""
    normalized_qualifier = normalize_qualifier(qualifier)
    suffix = f"_{normalized_qualifier.upper()}" if normalized_qualifier else ""
    return f"SHA256SUMS_v{validate_semver(version)}{suffix}.txt"


def source_archive_prefix(version: str) -> str:
    """Return the single top-level directory used inside source archives."""
    return f"hunterX-{validate_semver(version)}/"


def project_version_from_text(source: str, source_label: str) -> str:
    """Read a literal APP_VERSION from source text without executing it."""
    try:
        module = ast.parse(source, filename=source_label)
    except SyntaxError as exc:
        raise ValueError(f"Invalid Python syntax in project metadata {source_label}: {exc}") from exc

    assignments: list[ast.AST | None] = []
    for node in module.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "APP_VERSION" for target in node.targets
        ):
            assignments.append(node.value)
        elif (
            isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and node.target.id == "APP_VERSION"
        ):
            assignments.append(node.value)

    if len(assignments) != 1:
        raise ValueError(
            f"Expected exactly one top-level APP_VERSION assignment in {source_label}, "
            f"found {len(assignments)}."
        )

    value_node = assignments[0]
    if not isinstance(value_node, ast.Constant) or not isinstance(value_node.value, str):
        raise ValueError(f"APP_VERSION in {source_label} must be a literal string.")
    normalized = validate_semver(value_node.value)
    if normalized != value_node.value:
        raise ValueError(f"APP_VERSION in {source_label} must not contain surrounding whitespace.")
    return normalized


def project_version(metadata_path: Path) -> str:
    """Read the literal APP_VERSION assignment without importing project code."""

    try:
        source = metadata_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ValueError(f"Unable to read project metadata {metadata_path}: {exc}") from exc
    return project_version_from_text(source, str(metadata_path))


def _git_output(repo_root: Path, *arguments: str) -> str:
    try:
        result = subprocess.run(
            ["git", *arguments],
            cwd=repo_root,
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        detail = getattr(exc, "stderr", "") or str(exc)
        raise ValueError(f"Git release snapshot check failed: {detail.strip()}") from exc
    return result.stdout.strip()


def resolve_clean_commit(repo_root: Path, commit: str) -> str:
    """Resolve one full HEAD commit and require a completely clean Git tree."""

    repo_root = repo_root.resolve()
    requested = commit.strip()
    if FULL_COMMIT_RE.fullmatch(requested) is None:
        raise ValueError("release commit must be an explicit full 40-hex Git commit")
    top_level = Path(_git_output(repo_root, "rev-parse", "--show-toplevel")).resolve()
    if top_level != repo_root:
        raise ValueError(
            f"release repository root mismatch: expected {repo_root}, Git reports {top_level}"
        )
    resolved = _git_output(repo_root, "rev-parse", "--verify", f"{requested}^{{commit}}")
    if FULL_COMMIT_RE.fullmatch(resolved) is None or resolved.casefold() != requested.casefold():
        raise ValueError(f"release commit did not resolve exactly: {requested!r} -> {resolved!r}")
    head = _git_output(repo_root, "rev-parse", "HEAD")
    if head.casefold() != resolved.casefold():
        raise ValueError(f"release commit {resolved} is not the checked-out HEAD {head}")
    status = _git_output(
        repo_root,
        "status",
        "--porcelain=v1",
        "--untracked-files=all",
        "--ignore-submodules=none",
    )
    if status:
        entries = len(status.splitlines())
        raise ValueError(
            f"release repository must be clean; found {entries} modified or untracked entries"
        )
    return resolved.casefold()


def validate_project_version(version: str, metadata_path: Path) -> str:
    """Require a requested release version to match project metadata exactly."""
    requested = validate_semver(version)
    declared = project_version(metadata_path)
    if requested != declared:
        raise ValueError(
            f"Release version mismatch: requested {requested}, "
            f"but {metadata_path} declares APP_VERSION {declared}."
        )
    return declared


def extract_changelog(version: str, changelog_path: Path) -> str:
    """Extract a matching CHANGELOG.md section, with a non-failing fallback."""
    normalized = validate_semver(version)
    fallback = (
        f"## {PROJECT_NAME} v{normalized}\n\n"
        "- Windows build artifact generated by the HunterX release workflow.\n"
        "- See CHANGELOG.md for project history."
    )
    if not changelog_path.exists():
        return fallback

    content = changelog_path.read_text(encoding="utf-8", errors="replace")
    headings = list(re.finditer(r"(?m)^##\s+(.+?)\s*$", content))
    for index, match in enumerate(headings):
        title = match.group(1).strip()
        title_version = title[1:] if title.startswith("v") else title
        if title_version == normalized:
            start = match.end()
            end = headings[index + 1].start() if index + 1 < len(headings) else len(content)
            section = content[start:end].strip()
            if section:
                return section
    return fallback


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="HunterX release utility")
    subparsers = parser.add_subparsers(dest="command", required=True)

    validate_parser = subparsers.add_parser("validate")
    validate_parser.add_argument("--version", required=True)

    resolve_parser = subparsers.add_parser("resolve-version")
    resolve_parser.add_argument("--event-name", required=True)
    resolve_parser.add_argument("--ref-name", required=True)
    resolve_parser.add_argument("--input-version", default="")
    resolve_parser.add_argument("--qualifier", choices=sorted(RELEASE_QUALIFIERS))

    artifact_parser = subparsers.add_parser("artifact-name")
    artifact_parser.add_argument("--version", required=True)
    artifact_parser.add_argument("--platform", default="windows")
    artifact_parser.add_argument("--qualifier", choices=sorted(RELEASE_QUALIFIERS))

    checksum_parser = subparsers.add_parser("checksum-name")
    checksum_parser.add_argument("--version", required=True)
    checksum_parser.add_argument("--qualifier", choices=sorted(RELEASE_QUALIFIERS))

    prefix_parser = subparsers.add_parser("source-prefix")
    prefix_parser.add_argument("--version", required=True)

    project_version_parser = subparsers.add_parser("project-version")
    project_version_parser.add_argument("--metadata", default="src/hunter_metadata.py")

    project_check_parser = subparsers.add_parser("validate-project-version")
    project_check_parser.add_argument("--version", required=True)
    project_check_parser.add_argument("--metadata", default="src/hunter_metadata.py")

    notes_parser = subparsers.add_parser("notes")
    notes_parser.add_argument("--version", required=True)
    notes_parser.add_argument("--changelog", default="CHANGELOG.md")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    try:
        if args.command == "validate":
            print(validate_semver(args.version))
        elif args.command == "resolve-version":
            print(
                resolve_version(
                    args.event_name,
                    args.ref_name,
                    args.input_version,
                    args.qualifier,
                )
            )
        elif args.command == "artifact-name":
            print(artifact_name(args.version, args.platform, args.qualifier))
        elif args.command == "checksum-name":
            print(checksum_name(args.version, args.qualifier))
        elif args.command == "source-prefix":
            print(source_archive_prefix(args.version))
        elif args.command == "project-version":
            print(project_version(Path(args.metadata)))
        elif args.command == "validate-project-version":
            print(validate_project_version(args.version, Path(args.metadata)))
        elif args.command == "notes":
            print(extract_changelog(args.version, Path(args.changelog)))
        else:
            parser.error(f"Unhandled command: {args.command}")
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
