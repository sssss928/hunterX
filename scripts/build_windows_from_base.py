#!/usr/bin/env python3
"""Build HunterX v0.5.2 on a byte-verified HunterX Windows runtime."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path, PurePosixPath

import release_utils
from build_windows_archive import build_windows_archive
from verify_release_archive import verify_windows_archive
from v052_packaged_smoke import verify_archive_package


BASELINE_VERSION = "0.5.1"
BASELINE_ARCHIVE_NAME = f"hunterX_windows_{BASELINE_VERSION}.zip"
BASELINE_SHA256 = "6c9083d9f743aea71c7ccad399f363afa5d8e4e1c671883d2a8092f9942cbd8b"
ROUND1_RC_ARCHIVE_NAME = "hunterX_windows_0.5.2_rc.zip"
ROUND1_RC_SHA256 = "b593dc3899316a4700d425461ac9413610bad04f462839d2d93b2fcc179f26ed"
RC2_WINDOWS_BASE_NAME = "hunterX_windows_0.5.2_rc2.zip"
RC2_WINDOWS_BASE_SHA256 = (
    "47747a962cf5c4ae49654aec574ca64ac52c27032fc5b1ec1f70d83c3d09da48"
)
SUPPORTED_BASE_ARCHIVES = {
    BASELINE_ARCHIVE_NAME: (BASELINE_SHA256, "verified v0.5.1 Windows runtime"),
    ROUND1_RC_ARCHIVE_NAME: (
        ROUND1_RC_SHA256,
        "verified Round-1 v0.5.2 RC Windows runtime",
    ),
    RC2_WINDOWS_BASE_NAME: (
        RC2_WINDOWS_BASE_SHA256,
        "verified Final-Layer v0.5.2 RC2 Windows runtime",
    ),
}
RUNTIME_LAYOUTS = {
    "nodriver_tixcraft": "_nodriver_internal",
    "settings": "_settings_internal",
}
REQUIRED_BASE_FILES = {
    "nodriver_tixcraft.exe",
    "settings.exe",
    "_nodriver_internal/base_library.zip",
    "_nodriver_internal/app_src/nodriver_tixcraft.py",
    "_nodriver_internal/python311.dll",
    "_settings_internal/base_library.zip",
    "_settings_internal/app_src/settings.py",
    "_settings_internal/python311.dll",
    "www/settings.html",
    "www/settings.js",
}
SOURCE_DIRECTORIES = ("platforms", "assets", "www")
TOP_LEVEL_DOCUMENTS = (
    "BUILD_INFO.txt",
    "CHANGELOG.md",
    "CODEX_MASTER_PROMPT_v0.5.2.md",
    "FINAL_AUDIT_v0.5.2.md",
    "IMPLEMENTATION_DIFF_v0.5.2_RC.md",
    "LEGAL_NOTICE.md",
    "LICENSE",
    "README.md",
    "LONG_RUN_STABILITY_REPORT_v0.5.2.md",
    "PERFORMANCE_COMPARISON_v0.5.1_vs_v0.5.2.md",
    "PLATFORM_COMPLETION_LATCH_AUDIT.md",
    "REFRESH_OWNERSHIP_MATRIX_v0.5.2.md",
    "RELEASE_NOTES_v0.5.2_RC.md",
    "REQUIREMENT_TEST_TRACEABILITY_v0.5.2.md",
    "TEST_REPORT_v0.5.2_RC.md",
    "WINDOWS_PACKAGE_zh-TW.txt",
)
RC2_REQUIRED_DOCUMENTS = (
    "ROUND2_FINAL_CROSS_AUDIT_v0.5.2.md",
    "ROUND2_TEST_REPORT_v0.5.2.md",
    "ROUND2_PRODUCTION_INTEGRATION_REPORT_v0.5.2.md",
    "ROUND2_LONG_RUN_STABILITY_REPORT_v0.5.2.md",
    "ROUND2_PERFORMANCE_COMPARISON.md",
    "ROUTE_REARM_MATRIX_v0.5.2.md",
    "REQUIREMENT_TEST_TRACEABILITY_v0.5.2.md",
    "IMPLEMENTATION_DIFF_v0.5.2_RC2.md",
    "ROUND2_OBSERVED_FAILURES_FIX_LOOPS.md",
)
RC2_PROVENANCE_NAME = "RC2_BUILD_PROVENANCE.json"
RC3_REQUIRED_DOCUMENTS = (
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
)
RC3_PROVENANCE_NAME = "RC3_BUILD_PROVENANCE.json"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _checked_members(
    archive: zipfile.ZipFile,
    *,
    archive_label: str = "baseline",
) -> dict[str, zipfile.ZipInfo]:
    corrupt = archive.testzip()
    if corrupt is not None:
        raise ValueError(f"{archive_label} ZIP CRC failure: {corrupt}")

    members: dict[str, zipfile.ZipInfo] = {}
    casefolded: set[str] = set()
    for info in archive.infolist():
        raw_name = info.filename.replace("\\", "/")
        path = PurePosixPath(raw_name)
        if (
            not raw_name
            or info.filename != raw_name
            or raw_name.startswith("./")
            or "//" in raw_name
            or raw_name.startswith("/")
            or path.is_absolute()
            or ".." in path.parts
            or path.as_posix() != raw_name.rstrip("/")
        ):
            raise ValueError(f"unsafe {archive_label} ZIP path: {info.filename!r}")
        normalized = path.as_posix().rstrip("/")
        if not normalized or info.is_dir():
            continue
        folded = normalized.casefold()
        if folded in casefolded:
            raise ValueError(
                f"duplicate {archive_label} ZIP path ignoring case: {normalized!r}"
            )
        casefolded.add(folded)
        members[normalized] = info
    return members


def _extract_checked_members(
    archive: zipfile.ZipFile,
    members: dict[str, zipfile.ZipInfo],
    destination: Path,
    *,
    archive_label: str,
) -> None:
    destination.mkdir(parents=True, exist_ok=False)
    root = destination.resolve()
    for name, info in members.items():
        target = (root / Path(name)).resolve()
        try:
            target.relative_to(root)
        except ValueError as exc:
            raise ValueError(
                f"{archive_label} ZIP entry escapes destination: {name}"
            ) from exc
        target.parent.mkdir(parents=True, exist_ok=True)
        with archive.open(info) as source, target.open("wb") as output:
            shutil.copyfileobj(source, output, length=1024 * 1024)


def extract_verified_baseline(
    base_archive: Path,
    destination: Path,
    *,
    qualifier: str | None = None,
) -> None:
    if not base_archive.is_file():
        raise FileNotFoundError(base_archive)
    base_contract = SUPPORTED_BASE_ARCHIVES.get(base_archive.name)
    if base_contract is None:
        raise ValueError(
            "baseline archive must be one of "
            f"{sorted(SUPPORTED_BASE_ARCHIVES)!r}, not {base_archive.name!r}"
        )
    if qualifier is not None:
        normalized_qualifier = release_utils.require_candidate_qualifier(qualifier)
        required_base = (
            ROUND1_RC_ARCHIVE_NAME
            if normalized_qualifier == release_utils.RC2_QUALIFIER
            else RC2_WINDOWS_BASE_NAME
        )
        profile_label = normalized_qualifier.upper()
        if base_archive.name != required_base:
            if normalized_qualifier == release_utils.RC2_QUALIFIER:
                raise ValueError(
                    "RC2 builds require the Round-1 Windows base "
                    f"{required_base!r}, not {base_archive.name!r}"
                )
            raise ValueError(
                f"{profile_label} builds require the verified Windows base "
                f"{required_base!r}, not {base_archive.name!r}"
            )
    expected_sha256, base_label = base_contract
    actual_sha256 = sha256_file(base_archive)
    if actual_sha256 != expected_sha256:
        raise ValueError(
            f"{base_label} SHA-256 mismatch: "
            f"expected {expected_sha256}, got {actual_sha256}"
        )

    with zipfile.ZipFile(base_archive) as archive:
        members = _checked_members(archive)
        missing = sorted(REQUIRED_BASE_FILES - set(members))
        if missing:
            raise ValueError(f"{base_label} is incomplete: {missing}")
        for executable in ("nodriver_tixcraft.exe", "settings.exe"):
            if archive.read(members[executable])[:2] != b"MZ":
                raise ValueError(f"baseline executable is not a Windows PE file: {executable}")

        _extract_checked_members(
            archive,
            members,
            destination,
            archive_label="baseline",
        )


def snapshot_release_source(
    project_root: Path,
    destination: Path,
    commit: str,
) -> Path:
    """Materialize clean, exact Git commit bytes used by both release ZIPs.

    A Windows checkout can retain mixed working-tree line endings even though
    ``git archive`` emits different committed bytes.  Building both artifacts
    from the same commit snapshot makes the two embedded ``app_src`` trees and
    packaged release documents byte-identical to the source ZIP.  Uploaded
    A dirty tree, non-Git directory, abbreviated commit, or non-HEAD commit is
    rejected before any release bytes are materialized.
    """

    project_root = project_root.resolve()
    verified_commit = release_utils.resolve_clean_commit(project_root, commit)

    snapshot_archive = destination.parent / f"{destination.name}.zip"
    subprocess.run(
        [
            "git",
            "archive",
            "--format=zip",
            f"--output={snapshot_archive}",
            verified_commit,
        ],
        cwd=project_root,
        check=True,
    )
    try:
        with zipfile.ZipFile(snapshot_archive) as archive:
            members = _checked_members(archive, archive_label="source snapshot")
            _extract_checked_members(
                archive,
                members,
                destination,
                archive_label="source snapshot",
            )
    finally:
        snapshot_archive.unlink(missing_ok=True)
    release_utils.resolve_clean_commit(project_root, verified_commit)
    return destination


def _replace_tree(source: Path, destination: Path) -> None:
    if not source.is_dir():
        raise FileNotFoundError(source)
    if destination.exists():
        shutil.rmtree(destination)
    shutil.copytree(
        source,
        destination,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo"),
    )


def stage_application_source(project_root: Path, destination: Path) -> None:
    source_root = project_root / "src"
    if destination.exists():
        shutil.rmtree(destination)
    destination.mkdir(parents=True)

    python_files = sorted(source_root.glob("*.py"))
    required_python = {"hunter_metadata.py", "nodriver_tixcraft.py", "settings.py"}
    if not required_python.issubset({path.name for path in python_files}):
        raise ValueError("application source is missing a required entry module")
    for source in python_files:
        shutil.copy2(source, destination / source.name)
    for directory_name in SOURCE_DIRECTORIES:
        _replace_tree(source_root / directory_name, destination / directory_name)

    forbidden_names = {
        "config.json",
        "heartbeat.txt",
        "maxbot_int28_idle.txt",
        "maxbot_last_url.txt",
        "maxbot_question.txt",
        "settings.json",
    }
    for candidate in destination.rglob("*"):
        if candidate.name.casefold() in forbidden_names:
            raise ValueError(f"runtime source contains local state: {candidate}")


def overlay_release_files(
    project_root: Path,
    package_root: Path,
    *,
    qualifier: str | None = None,
) -> None:
    source_root = project_root / "src"
    for name in TOP_LEVEL_DOCUMENTS:
        source = project_root / name
        if source.is_file():
            shutil.copy2(source, package_root / name)

    if qualifier is not None:
        normalized_qualifier = release_utils.require_candidate_qualifier(qualifier)
        required_documents = (
            RC2_REQUIRED_DOCUMENTS
            if normalized_qualifier == release_utils.RC2_QUALIFIER
            else RC3_REQUIRED_DOCUMENTS
        )
        for name in required_documents:
            source = project_root / name
            if not source.is_file():
                raise FileNotFoundError(
                    f"{normalized_qualifier.upper()} required release report is missing: "
                    f"{source}"
                )
            shutil.copy2(source, package_root / name)

    release_readme = project_root / "build_scripts" / "README_Release.txt"
    if not release_readme.is_file():
        raise FileNotFoundError(release_readme)
    shutil.copy2(release_readme, package_root / "README_Release.txt")

    _replace_tree(project_root / "guide", package_root / "guide")
    _replace_tree(source_root / "assets", package_root / "assets")
    _replace_tree(source_root / "www", package_root / "www")

    for runtime_root_name in RUNTIME_LAYOUTS.values():
        runtime_root = package_root / runtime_root_name
        if not runtime_root.is_dir():
            raise ValueError(f"baseline runtime directory is missing: {runtime_root_name}")
        _replace_tree(source_root / "assets", runtime_root / "assets")
        _replace_tree(source_root / "www", runtime_root / "www")
        stage_application_source(project_root, runtime_root / "app_src")


def write_rc2_provenance(
    package_root: Path,
    *,
    version: str,
    source_commit: str,
    base_archive: Path,
) -> Path:
    """Write machine-verifiable RC2 provenance without claiming FINAL status."""

    if base_archive.name != ROUND1_RC_ARCHIVE_NAME:
        raise ValueError(f"RC2 provenance refuses non-Round-1 base: {base_archive.name}")
    actual_base_sha256 = sha256_file(base_archive)
    if actual_base_sha256 != ROUND1_RC_SHA256:
        raise ValueError("RC2 provenance refuses an unverified Round-1 base")
    payload = {
        "schema": 1,
        "version": release_utils.validate_semver(version),
        "qualifier": release_utils.RC2_QUALIFIER,
        "source_commit": source_commit,
        "windows_base_name": ROUND1_RC_ARCHIVE_NAME,
        "windows_base_sha256": ROUND1_RC_SHA256,
        "clean_committed_snapshot": True,
        "eight_hour_soak_verified": False,
        "final_eligible": False,
    }
    destination = package_root / RC2_PROVENANCE_NAME
    destination.write_text(
        json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=True) + "\n",
        encoding="ascii",
    )
    return destination


def write_rc3_provenance(
    package_root: Path,
    *,
    version: str,
    source_commit: str,
    base_archive: Path,
) -> Path:
    """Write machine-verifiable RC3 provenance without claiming FINAL status."""

    if base_archive.name != RC2_WINDOWS_BASE_NAME:
        raise ValueError(f"RC3 provenance refuses non-RC2 base: {base_archive.name}")
    actual_base_sha256 = sha256_file(base_archive)
    if actual_base_sha256 != RC2_WINDOWS_BASE_SHA256:
        raise ValueError("RC3 provenance refuses an unverified RC2 base")
    payload = {
        "schema": 1,
        "version": release_utils.validate_semver(version),
        "qualifier": release_utils.RC3_QUALIFIER,
        "source_commit": source_commit,
        "windows_base_name": RC2_WINDOWS_BASE_NAME,
        "windows_base_sha256": RC2_WINDOWS_BASE_SHA256,
        "clean_committed_snapshot": True,
        "eight_hour_single_instance_soak_verified": False,
        "eight_hour_three_named_instances_soak_verified": False,
        "eight_hour_soak_verified": False,
        "final_eligible": False,
    }
    destination = package_root / RC3_PROVENANCE_NAME
    destination.write_text(
        json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=True) + "\n",
        encoding="ascii",
    )
    return destination


def repack_entrypoints(package_root: Path) -> None:
    # Import lazily so source-only checks do not need PyInstaller installed.
    from repack_pyinstaller_entrypoint import repack

    for entry_name in RUNTIME_LAYOUTS:
        executable = package_root / f"{entry_name}.exe"
        output = package_root / f".{entry_name}.repacked.exe"
        try:
            repack(executable, output, entry_name)
            output.replace(executable)
        finally:
            output.unlink(missing_ok=True)


def promote_staged_package(source: Path, destination: Path, attempts: int = 8) -> None:
    """Atomically promote a staged Windows package with bounded lock retries.

    Windows security scanners may briefly hold a newly repacked PE executable
    after it is closed. The directory rename remains the authoritative handoff;
    retrying only ``PermissionError`` avoids falling back to a partial copy.
    """

    last_error: PermissionError | None = None
    for attempt in range(attempts):
        try:
            source.replace(destination)
            return
        except PermissionError as exc:
            last_error = exc
            if attempt + 1 < attempts:
                time.sleep(min(0.25 * (2**attempt), 2.0))
    assert last_error is not None
    raise last_error


def build_from_verified_baseline(
    *,
    version: str,
    base_archive: Path,
    output: Path,
    package_dir: Path,
    project_root: Path,
    commit: str,
    qualifier: str,
) -> Path:
    normalized_version = release_utils.validate_semver(version)
    normalized_qualifier = release_utils.require_candidate_qualifier(qualifier)
    expected_output_name = release_utils.artifact_name(
        normalized_version,
        qualifier=normalized_qualifier,
    )
    if output.name != expected_output_name:
        raise ValueError(f"output must be named {expected_output_name!r}")

    package_dir = package_dir.resolve()
    package_dir.parent.mkdir(parents=True, exist_ok=True)
    output = output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary_parent = Path(
        tempfile.mkdtemp(prefix=f".{package_dir.name}.", dir=package_dir.parent)
    )
    staged_package = temporary_parent / package_dir.name
    try:
        release_root = snapshot_release_source(
            project_root.resolve(),
            temporary_parent / "source_snapshot",
            commit,
        )
        verified_commit = release_utils.resolve_clean_commit(project_root.resolve(), commit)
        declared_version = release_utils.project_version(
            release_root / "src" / "hunter_metadata.py"
        )
        if normalized_version != declared_version:
            raise ValueError(
                "release version mismatch: "
                f"requested {normalized_version}, project declares {declared_version}"
            )
        resolved_base_archive = base_archive.resolve()
        extract_verified_baseline(
            resolved_base_archive,
            staged_package,
            qualifier=normalized_qualifier,
        )
        overlay_release_files(
            release_root,
            staged_package,
            qualifier=normalized_qualifier,
        )
        repack_entrypoints(staged_package)
        provenance_writer = (
            write_rc2_provenance
            if normalized_qualifier == release_utils.RC2_QUALIFIER
            else write_rc3_provenance
        )
        provenance_writer(
            staged_package,
            version=normalized_version,
            source_commit=verified_commit,
            base_archive=resolved_base_archive,
        )

        staged_archive = temporary_parent / expected_output_name
        build_windows_archive(staged_package, staged_archive)
        verify_windows_archive(
            staged_archive,
            normalized_version,
            qualifier=normalized_qualifier,
        )
        verify_archive_package(staged_archive)
        release_utils.resolve_clean_commit(project_root.resolve(), verified_commit)

        if package_dir.exists():
            shutil.rmtree(package_dir)
        promote_staged_package(staged_package, package_dir)
        output.unlink(missing_ok=True)
        staged_archive.replace(output)
    finally:
        shutil.rmtree(temporary_parent, ignore_errors=True)
    return output


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", required=True)
    parser.add_argument("--base-archive", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--package-dir", type=Path, required=True)
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    parser.add_argument("--commit", required=True)
    parser.add_argument(
        "--qualifier",
        choices=(release_utils.RC2_QUALIFIER, release_utils.RC3_QUALIFIER),
        required=True,
    )
    return parser


def main() -> int:
    args = _build_parser().parse_args()
    if sys.version_info[:2] != (3, 11):
        print("Windows baseline repack requires CPython 3.11")
        return 2
    try:
        result = build_from_verified_baseline(
            version=args.version,
            base_archive=args.base_archive,
            output=args.output,
        package_dir=args.package_dir,
        project_root=args.project_root,
        commit=args.commit,
        qualifier=args.qualifier,
        )
    except (
        OSError,
        RuntimeError,
        ValueError,
        EOFError,
        zipfile.BadZipFile,
        subprocess.CalledProcessError,
    ) as exc:
        print(f"Windows baseline build failed: {exc}")
        return 1
    print(result.resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
