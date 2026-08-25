#!/usr/bin/env python3
"""Fail-closed integrity checks for HunterX release ZIP archives."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import tarfile
import zipfile
from pathlib import Path, PurePosixPath

import release_utils
from final_qualification import (
    CONTEXT_NAME as FINAL_QUALIFICATION_CONTEXT_NAME,
    SINGLE_EVIDENCE_NAME as FINAL_SINGLE_EVIDENCE_NAME,
    THREE_EVIDENCE_NAME as FINAL_THREE_EVIDENCE_NAME,
    WAIVER_NAME as FINAL_WAIVER_NAME,
    sha256_bytes,
    validate_context_bytes,
    validate_waiver_bytes,
)


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
        ("_nodriver_internal", "app_src", "www", "dist"),
        ("_settings_internal", "www", "dist"),
        ("_settings_internal", "app_src", "www", "dist"),
    }
)
WINDOWS_ALLOWED_PUBLIC_CERTIFICATES = frozenset(
    {
        "_nodriver_internal/certifi/cacert.pem",
        "_settings_internal/certifi/cacert.pem",
    }
)
RC2_PROVENANCE_NAME = "RC2_BUILD_PROVENANCE.json"
RC2_WINDOWS_BASE_NAME = "hunterX_windows_0.5.2_rc.zip"
RC2_WINDOWS_BASE_SHA256 = (
    "b593dc3899316a4700d425461ac9413610bad04f462839d2d93b2fcc179f26ed"
)
RC2_REQUIRED_DOCUMENTS = frozenset(
    {
        "ROUND2_FINAL_CROSS_AUDIT_v0.5.2.md",
        "ROUND2_TEST_REPORT_v0.5.2.md",
        "ROUND2_PRODUCTION_INTEGRATION_REPORT_v0.5.2.md",
        "ROUND2_LONG_RUN_STABILITY_REPORT_v0.5.2.md",
        "ROUND2_PERFORMANCE_COMPARISON.md",
        "ROUTE_REARM_MATRIX_v0.5.2.md",
        "REQUIREMENT_TEST_TRACEABILITY_v0.5.2.md",
        "IMPLEMENTATION_DIFF_v0.5.2_RC2.md",
        "ROUND2_OBSERVED_FAILURES_FIX_LOOPS.md",
    }
)
RC3_PROVENANCE_NAME = "RC3_BUILD_PROVENANCE.json"
RC3_WINDOWS_BASE_NAME = "hunterX_windows_0.5.2_rc2.zip"
RC3_WINDOWS_BASE_SHA256 = (
    "47747a962cf5c4ae49654aec574ca64ac52c27032fc5b1ec1f70d83c3d09da48"
)
RC3_REQUIRED_DOCUMENTS = frozenset(
    {
        "FINAL_LAYER_ROOT_CAUSE_v0.5.2.md",
        "FINAL_LAYER_IMPLEMENTATION_DIFF_v0.5.2_RC2_to_RC3.md",
        "FINAL_LAYER_TEST_REPORT_v0.5.2_RC3.md",
        "FINAL_LAYER_REAL_WINDOWS_REPRO_REPORT.md",
        "FINAL_LAYER_BROWSER_RECOVERY_AUDIT.md",
        "FINAL_LAYER_USER_DICTIONARY_ACCEPTANCE.md",
        "FINAL_LAYER_PERFORMANCE_REPORT.md",
        "FINAL_LAYER_LONG_RUN_REPORT.md",
        "FINAL_LAYER_ARTIFACT_VERIFICATION.md",
        "FINAL_LAYER_FAILURE_FIX_LOG.md",
        "FINAL_LAYER_REQUIREMENT_TRACEABILITY.md",
        "FINAL_PRE_RELEASE_AUDIT_v0.5.2.md",
        "FINAL_ROOT_CAUSE_REPORT_v0.5.2.md",
        "FINAL_IMPLEMENTATION_DIFF_v0.5.2.md",
        "FINAL_TEST_REPORT_v0.5.2.md",
        "FINAL_PERFORMANCE_REPORT_v0.5.2.md",
        "FINAL_RC2_RC3_FINAL_PERFORMANCE_COMPARISON.md",
        "FINAL_USER_DICTIONARY_ACCEPTANCE_v0.5.2.md",
        "FINAL_BROWSER_RECOVERY_AUDIT_v0.5.2.md",
        "FINAL_MULTI_INSTANCE_AUDIT_v0.5.2.md",
        "FINAL_LONG_RUN_REPORT_v0.5.2.md",
        "FINAL_GITHUB_ACTIONS_AUDIT_v0.5.2.md",
        "FINAL_ARTIFACT_VERIFICATION_v0.5.2.md",
        "FINAL_REQUIREMENT_TRACEABILITY_v0.5.2.md",
        "FINAL_FAILURE_FIX_LOG_v0.5.2.md",
    }
)
FINAL_PROVENANCE_NAME = "FINAL_BUILD_PROVENANCE.json"
FINAL_WINDOWS_BASE_NAME = "hunterX_windows_0.5.2_rc3.zip"
FINAL_WINDOWS_BASE_SHA256 = (
    "f2ec4f918e50de5c78c2303a184ef54b0ce69d1ba2f87d34365d87be59d46cd9"
)
FINAL_CORE_REQUIRED_DOCUMENTS = RC3_REQUIRED_DOCUMENTS | frozenset(
    {
        "FINAL_RELEASE_AUDIT_v0.5.2.md",
        "RELEASE_NOTES_v0.5.2_FINAL.md",
    }
)
FINAL_STRICT_QUALIFICATION_DOCUMENTS = frozenset(
    {
        FINAL_SINGLE_EVIDENCE_NAME,
        FINAL_THREE_EVIDENCE_NAME,
        FINAL_QUALIFICATION_CONTEXT_NAME,
    }
)
FINAL_REQUIRED_DOCUMENTS = (
    FINAL_CORE_REQUIRED_DOCUMENTS | FINAL_STRICT_QUALIFICATION_DOCUMENTS
)
STAGED_RUNTIME_DIRECTORIES = frozenset({"assets", "platforms", "www"})
RUNTIME_APP_SRC_PREFIXES = (
    "_nodriver_internal/app_src/",
    "_settings_internal/app_src/",
)
RUNTIME_LAYOUT_PREFIXES = (
    "_nodriver_internal/",
    "_settings_internal/",
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


def _expected_archive_name(kind: str, version: str, qualifier: str | None) -> str:
    suffix = f"_{qualifier.casefold()}" if qualifier else ""
    return f"hunterX_{kind}_{version}{suffix}.zip"


def _read_json_member(
    archive: zipfile.ZipFile,
    entries: dict[str, zipfile.ZipInfo],
    name: str,
) -> dict[str, object]:
    try:
        raw_value = archive.read(entries[name])
    except KeyError as exc:
        raise ValueError(f"Windows archive missing required file: {name}") from exc
    try:
        value = json.loads(raw_value.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"Invalid JSON in {name}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{name} must contain a JSON object")
    return value


def _require_exact_manifest_value(
    manifest: dict[str, object],
    name: str,
    expected: object,
    *,
    profile: str = "RC2",
) -> None:
    actual = manifest.get(name)
    if type(actual) is not type(expected) or actual != expected:
        raise ValueError(
            f"{profile} provenance field {name!r} must be {expected!r}, got {actual!r}"
        )


def _verify_release_provenance(
    archive: zipfile.ZipFile,
    entries: dict[str, zipfile.ZipInfo],
    version: str,
    qualifier: str | None,
    *,
    resolved_commit: str | None = None,
) -> dict[str, object] | None:
    normalized_qualifier = qualifier.casefold() if qualifier else None
    if normalized_qualifier in {"rc2", "rc3"}:
        is_rc3 = normalized_qualifier == "rc3"
        profile = normalized_qualifier.upper()
        required_documents = RC3_REQUIRED_DOCUMENTS if is_rc3 else RC2_REQUIRED_DOCUMENTS
        provenance_name = RC3_PROVENANCE_NAME if is_rc3 else RC2_PROVENANCE_NAME
        base_name = RC3_WINDOWS_BASE_NAME if is_rc3 else RC2_WINDOWS_BASE_NAME
        base_sha256 = RC3_WINDOWS_BASE_SHA256 if is_rc3 else RC2_WINDOWS_BASE_SHA256
        missing_documents = sorted(required_documents - set(entries))
        if missing_documents:
            raise ValueError(
                f"{profile} Windows archive missing required documents: {missing_documents}"
            )
        manifest = _read_json_member(archive, entries, provenance_name)
        expected_values: dict[str, object] = {
            "schema": 1,
            "version": version,
            "qualifier": normalized_qualifier,
            "windows_base_name": base_name,
            "windows_base_sha256": base_sha256,
            "clean_committed_snapshot": True,
            "eight_hour_soak_verified": False,
            "final_eligible": False,
        }
        if is_rc3:
            expected_values.update(
                {
                    "eight_hour_single_instance_soak_verified": False,
                    "eight_hour_three_named_instances_soak_verified": False,
                }
            )
        for name, expected in expected_values.items():
            _require_exact_manifest_value(manifest, name, expected, profile=profile)
        source_commit = manifest.get("source_commit")
        if not isinstance(source_commit, str) or re.fullmatch(
            r"[0-9a-f]{40}", source_commit
        ) is None:
            raise ValueError(
                f"{profile} provenance field 'source_commit' must be a lowercase "
                "40-hex commit"
            )
        if resolved_commit is not None and source_commit != resolved_commit:
            raise ValueError(
                f"{profile} provenance source_commit does not match resolved source commit: "
                f"{source_commit!r} != {resolved_commit!r}"
            )
        return manifest

    if normalized_qualifier == "final":
        missing_documents = sorted(FINAL_CORE_REQUIRED_DOCUMENTS - set(entries))
        if missing_documents:
            raise ValueError(
                f"FINAL Windows archive missing required documents: {missing_documents}"
            )
        has_strict_evidence = FINAL_STRICT_QUALIFICATION_DOCUMENTS.issubset(entries)
        has_waiver = FINAL_WAIVER_NAME in entries
        if has_strict_evidence == has_waiver:
            raise ValueError(
                "FINAL archive must contain exactly one qualification mode: "
                "complete eight-hour evidence or explicit user waiver"
            )
        manifest = _read_json_member(archive, entries, FINAL_PROVENANCE_NAME)
        final_expected_values: dict[str, object] = {
            "schema": 1,
            "version": version,
            "qualifier": release_utils.FINAL_QUALIFIER,
            "windows_base_name": FINAL_WINDOWS_BASE_NAME,
            "windows_base_sha256": FINAL_WINDOWS_BASE_SHA256,
            "clean_committed_snapshot": True,
        }
        if has_strict_evidence:
            single_content = archive.read(entries[FINAL_SINGLE_EVIDENCE_NAME])
            three_content = archive.read(entries[FINAL_THREE_EVIDENCE_NAME])
            context_content = archive.read(entries[FINAL_QUALIFICATION_CONTEXT_NAME])
            context = validate_context_bytes(
                context_content=context_content,
                single_content=single_content,
                three_content=three_content,
            )
            final_expected_values.update(
                {
                    "qualification_mode": "COMPLETED_8H_GATES",
                    "runtime_source_commit": context["runtime_source_commit"],
                    "runtime_src_tree": context["runtime_src_tree"],
                    "single_evidence_sha256": sha256_bytes(single_content),
                    "three_evidence_sha256": sha256_bytes(three_content),
                    "eight_hour_single_instance_soak_verified": True,
                    "eight_hour_three_named_instances_soak_verified": True,
                    "eight_hour_soak_verified": True,
                    "final_eligible": True,
                    "user_approved_final_without_eight_hour_soak": False,
                }
            )
        else:
            waiver_content = archive.read(entries[FINAL_WAIVER_NAME])
            validate_waiver_bytes(waiver_content)
            final_expected_values.update(
                {
                    "qualification_mode": "USER_WAIVED_8H_GATES",
                    "waiver_sha256": sha256_bytes(waiver_content),
                    "eight_hour_single_instance_soak_verified": False,
                    "eight_hour_three_named_instances_soak_verified": False,
                    "eight_hour_soak_verified": False,
                    "final_eligible": False,
                    "user_approved_final_without_eight_hour_soak": True,
                }
            )
        for name, expected in final_expected_values.items():
            _require_exact_manifest_value(manifest, name, expected, profile="FINAL")
        source_commit = manifest.get("source_commit")
        if not isinstance(source_commit, str) or re.fullmatch(
            r"[0-9a-f]{40}", source_commit
        ) is None:
            raise ValueError(
                "FINAL provenance field 'source_commit' must be a lowercase 40-hex commit"
            )
        if resolved_commit is not None and source_commit != resolved_commit:
            raise ValueError(
                "FINAL provenance source_commit does not match resolved source commit: "
                f"{source_commit!r} != {resolved_commit!r}"
            )
        return manifest
    return None


def verify_windows_archive(
    path: Path,
    version: str,
    *,
    qualifier: str | None = None,
) -> dict[str, object]:
    archive, entries = _open_checked_zip(path)
    try:
        required = {
            "BUILD_INFO.txt",
            "CHANGELOG.md",
            "CODEX_MASTER_PROMPT_v0.5.2.md",
            "FINAL_AUDIT_v0.5.2.md",
            "IMPLEMENTATION_DIFF_v0.5.2_RC.md",
            "LICENSE",
            "README.md",
            "README_Release.txt",
            "LONG_RUN_STABILITY_REPORT_v0.5.2.md",
            "PERFORMANCE_COMPARISON_v0.5.1_vs_v0.5.2.md",
            "PLATFORM_COMPLETION_LATCH_AUDIT.md",
            "REFRESH_OWNERSHIP_MATRIX_v0.5.2.md",
            "RELEASE_NOTES_v0.5.2_RC.md",
            "REQUIREMENT_TEST_TRACEABILITY_v0.5.2.md",
            "TEST_REPORT_v0.5.2_RC.md",
            "WINDOWS_PACKAGE_zh-TW.txt",
            "nodriver_tixcraft.exe",
            "settings.exe",
            "www/css/settings.css",
            "www/favicon.ico",
            "www/settings.html",
            "www/settings.js",
            "_nodriver_internal/base_library.zip",
            "_nodriver_internal/app_src/hunter_metadata.py",
            "_nodriver_internal/app_src/nodriver_tixcraft.py",
            "_nodriver_internal/app_src/platforms/ticketplus.py",
            "_nodriver_internal/python311.dll",
            "_settings_internal/base_library.zip",
            "_settings_internal/app_src/hunter_metadata.py",
            "_settings_internal/app_src/settings.py",
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
        for executable_name in ("settings.exe", "nodriver_tixcraft.exe"):
            if archive.read(entries[executable_name])[:2] != b"MZ":
                raise ValueError(f"Windows executable is not a PE file: {executable_name}")
        version_pattern = re.compile(
            rb"(?m)^APP_VERSION\s*=\s*['\"]" + re.escape(version.encode("ascii")) + rb"['\"]\s*$"
        )
        for metadata_name in (
            "_nodriver_internal/app_src/hunter_metadata.py",
            "_settings_internal/app_src/hunter_metadata.py",
        ):
            if version_pattern.search(archive.read(entries[metadata_name])) is None:
                raise ValueError(
                    f"Windows archive runtime version mismatch: {metadata_name}"
                )
        display_version = f"HunterX ({version})".encode("utf-8")
        for frontend_name in ("www/settings.html", "www/settings.js"):
            if display_version not in archive.read(entries[frontend_name]):
                raise ValueError(
                    f"Windows archive frontend version mismatch: {frontend_name}"
                )
        provenance = _verify_release_provenance(
            archive,
            entries,
            version,
            qualifier,
        )
        expected_name = _expected_archive_name("windows", version, qualifier)
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
            "provenance": "verified" if provenance is not None else "not-required",
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

    if (repo_root / ".git").exists():
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
    else:
        # Uploaded source archives do not have Git metadata. Build the same
        # fail-closed release input directly from regular files while excluding
        # local environments, caches and generated artifacts.
        denied_top_level = {
            ".git",
            ".mypy_cache",
            ".pytest_cache",
            ".ruff_cache",
            "build",
            "dist",
            "venv",
        }
        denied_generated_files = {".coverage", "coverage.xml"}
        relative_names = []
        for candidate in sorted(repo_root.rglob("*")):
            relative = candidate.relative_to(repo_root)
            parts = relative.parts
            if not parts:
                continue
            if (
                parts[0].casefold() in denied_top_level
                or parts[0].casefold().startswith(".venv")
                or "__pycache__" in {part.casefold() for part in parts}
                or candidate.suffix.casefold() in {".pyc", ".pyo"}
                or candidate.name.casefold() in denied_generated_files
                or candidate.name.casefold().startswith(".coverage.")
            ):
                continue
            if candidate.is_symlink():
                raise ValueError(f"Source symlink is not allowed: {relative.as_posix()}")
            if candidate.is_file():
                relative_names.append(relative.as_posix())
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
    qualifier: str | None = None,
) -> dict[str, object]:
    expected_prefix = f"hunterX-{version}/"
    expected_name = _expected_archive_name("source", version, qualifier)
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
        metadata_name = f"{expected_prefix}src/hunter_metadata.py"
        metadata_bytes = expected.get(metadata_name)
        if metadata_bytes is None:
            raise ValueError(f"Source release is missing version metadata: {metadata_name}")
        try:
            metadata_source = metadata_bytes.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError(f"Source version metadata is not UTF-8: {metadata_name}") from exc
        declared_version = release_utils.project_version_from_text(
            metadata_source,
            metadata_name,
        )
        if declared_version != version:
            raise ValueError(
                "Source release version mismatch: "
                f"requested {version}, commit declares {declared_version}"
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
        if qualifier is not None and qualifier.casefold() == release_utils.FINAL_QUALIFIER:
            missing_final_documents = sorted(
                name
                for name in FINAL_CORE_REQUIRED_DOCUMENTS
                if f"{expected_prefix}{name}" not in entries
            )
            if missing_final_documents:
                raise ValueError(
                    "FINAL source archive missing required documents: "
                    f"{missing_final_documents}"
                )
            qualification_names = {
                "single": f"{expected_prefix}{FINAL_SINGLE_EVIDENCE_NAME}",
                "three": f"{expected_prefix}{FINAL_THREE_EVIDENCE_NAME}",
                "context": f"{expected_prefix}{FINAL_QUALIFICATION_CONTEXT_NAME}",
            }
            has_strict_evidence = all(name in entries for name in qualification_names.values())
            waiver_name = f"{expected_prefix}{FINAL_WAIVER_NAME}"
            has_waiver = waiver_name in entries
            if has_strict_evidence == has_waiver:
                raise ValueError(
                    "FINAL source archive must contain exactly one qualification mode"
                )
            if has_strict_evidence:
                validate_context_bytes(
                    context_content=archive.read(entries[qualification_names["context"]]),
                    single_content=archive.read(entries[qualification_names["single"]]),
                    three_content=archive.read(entries[qualification_names["three"]]),
                    repo_root=repo_root,
                    release_commit=commit,
                )
            else:
                validate_waiver_bytes(archive.read(entries[waiver_name]))
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


def _source_staged_runtime_files(
    archive: zipfile.ZipFile,
    entries: dict[str, zipfile.ZipInfo],
    version: str,
) -> dict[str, bytes]:
    source_prefix = f"hunterX-{version}/src/"
    expected: dict[str, bytes] = {}
    for name, info in entries.items():
        if not name.startswith(source_prefix):
            continue
        relative = name.removeprefix(source_prefix)
        parts = PurePosixPath(relative).parts
        if not parts:
            continue
        if "__pycache__" in {part.casefold() for part in parts}:
            continue
        if PurePosixPath(relative).suffix.casefold() in {".pyc", ".pyo"}:
            continue
        is_top_level_python = len(parts) == 1 and relative.casefold().endswith(".py")
        is_staged_directory = len(parts) > 1 and parts[0] in STAGED_RUNTIME_DIRECTORIES
        if is_top_level_python or is_staged_directory:
            expected[relative] = archive.read(info)
    if not expected:
        raise ValueError("Source archive has no staged runtime files")
    return expected


def _archive_subtree_files(
    archive: zipfile.ZipFile,
    entries: dict[str, zipfile.ZipInfo],
    prefix: str,
) -> dict[str, bytes]:
    return {
        name.removeprefix(prefix): archive.read(info)
        for name, info in entries.items()
        if name.startswith(prefix)
    }


def _assert_exact_file_parity(
    expected: dict[str, bytes],
    actual: dict[str, bytes],
    label: str,
) -> None:
    expected_names = set(expected)
    actual_names = set(actual)
    missing = sorted(expected_names - actual_names)
    extra = sorted(actual_names - expected_names)
    mismatch = sorted(
        name
        for name in expected_names & actual_names
        if expected[name] != actual[name]
    )
    if missing or extra or mismatch:
        raise ValueError(
            f"Release pair parity failure for {label}: "
            f"missing={missing} extra={extra} mismatch={mismatch}"
        )


def verify_release_pair(
    windows_path: Path,
    source_path: Path,
    version: str,
    repo_root: Path,
    commit: str,
    qualifier: str = "rc2",
) -> dict[str, object]:
    """Verify both archives and prove byte parity for every staged runtime file."""

    windows_result = verify_windows_archive(
        windows_path,
        version,
        qualifier=qualifier,
    )
    source_result = verify_source_archive(
        source_path,
        version,
        repo_root,
        commit,
        qualifier=qualifier,
    )
    resolved_commit = release_utils.resolve_clean_commit(repo_root, commit)

    windows_archive, windows_entries = _open_checked_zip(windows_path)
    source_archive, source_entries = _open_checked_zip(
        source_path,
        enforce_strict_denylist=False,
    )
    try:
        provenance = _verify_release_provenance(
            windows_archive,
            windows_entries,
            version,
            qualifier,
            resolved_commit=resolved_commit,
        )
        expected_runtime = _source_staged_runtime_files(
            source_archive,
            source_entries,
            version,
        )
        parity_targets: list[tuple[str, dict[str, bytes], dict[str, bytes]]] = []
        for prefix in RUNTIME_APP_SRC_PREFIXES:
            parity_targets.append(
                (
                    prefix.rstrip("/"),
                    expected_runtime,
                    _archive_subtree_files(windows_archive, windows_entries, prefix),
                )
            )

        for directory_name in ("assets", "www"):
            source_directory = {
                name.removeprefix(f"{directory_name}/"): content
                for name, content in expected_runtime.items()
                if name.startswith(f"{directory_name}/")
            }
            parity_targets.append(
                (
                    directory_name,
                    source_directory,
                    _archive_subtree_files(
                        windows_archive,
                        windows_entries,
                        f"{directory_name}/",
                    ),
                )
            )
            for runtime_prefix in RUNTIME_LAYOUT_PREFIXES:
                target_prefix = f"{runtime_prefix}{directory_name}/"
                parity_targets.append(
                    (
                        target_prefix.rstrip("/"),
                        source_directory,
                        _archive_subtree_files(
                            windows_archive,
                            windows_entries,
                            target_prefix,
                        ),
                    )
                )

        for label, expected, actual in parity_targets:
            _assert_exact_file_parity(expected, actual, label)

        return {
            "windows": windows_result,
            "source": source_result,
            "source_commit": resolved_commit,
            "qualifier": qualifier.casefold(),
            "provenance": "verified" if provenance is not None else "not-required",
            "runtime_files": len(expected_runtime),
            "parity_targets": len(parity_targets),
            "parity": "ok",
        }
    finally:
        source_archive.close()
        windows_archive.close()


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="kind", required=True)

    windows = subparsers.add_parser("windows")
    windows.add_argument("--archive", type=Path, required=True)
    windows.add_argument("--version", required=True)
    windows.add_argument("--qualifier", choices=("rc", "rc2", "rc3", "final"))

    source = subparsers.add_parser("source")
    source.add_argument("--archive", type=Path, required=True)
    source.add_argument("--version", required=True)
    source.add_argument("--repo-root", type=Path, default=Path.cwd())
    source.add_argument("--commit", default="HEAD")
    source.add_argument("--working-tree", action="store_true")
    source.add_argument("--qualifier", choices=("rc", "rc2", "rc3", "final"))

    pair = subparsers.add_parser("pair")
    pair.add_argument("--windows-archive", type=Path, required=True)
    pair.add_argument("--source-archive", type=Path, required=True)
    pair.add_argument("--version", required=True)
    pair.add_argument("--repo-root", type=Path, default=Path.cwd())
    pair.add_argument("--commit", required=True)
    pair.add_argument(
        "--qualifier",
        choices=("rc", "rc2", "rc3", "final"),
        default="rc3",
    )
    return parser


def main() -> int:
    args = _build_parser().parse_args()
    try:
        if args.kind == "windows":
            result = verify_windows_archive(
                args.archive,
                args.version,
                qualifier=args.qualifier,
            )
        elif args.kind == "source":
            result = verify_source_archive(
                args.archive,
                args.version,
                args.repo_root,
                args.commit,
                working_tree=args.working_tree,
                qualifier=args.qualifier,
            )
        else:
            result = verify_release_pair(
                args.windows_archive,
                args.source_archive,
                args.version,
                args.repo_root,
                args.commit,
                qualifier=args.qualifier,
            )
    except (OSError, ValueError, zipfile.BadZipFile) as exc:
        print(f"release archive verification failed: {exc}")
        return 1
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
