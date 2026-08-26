#!/usr/bin/env python3
"""Build the official HunterX Windows FINAL archive from one clean Git commit."""

from __future__ import annotations

import argparse
import importlib.metadata
import json
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

import release_utils
from build_windows_archive import build_windows_archive
from build_windows_from_base import (
    FINAL_AUDIT_DIRECTORY,
    FINAL_PROVENANCE_NAME,
    RUNTIME_LAYOUTS,
    overlay_release_files,
    promote_staged_package,
    sha256_file,
    snapshot_release_source,
)
from final_qualification import (
    CONTEXT_NAME as FINAL_QUALIFICATION_CONTEXT_NAME,
    SINGLE_EVIDENCE_NAME as FINAL_SINGLE_EVIDENCE_NAME,
    THREE_EVIDENCE_NAME as FINAL_THREE_EVIDENCE_NAME,
    WAIVER_NAME as FINAL_WAIVER_NAME,
    sha256_bytes,
    validate_context_bytes,
    validate_waiver_bytes,
)
from v052_packaged_smoke import verify_archive_package
from verify_release_archive import verify_windows_archive


EXPECTED_PYTHON = (3, 11, 9)
EXPECTED_PYINSTALLER = "6.21.0"
REQUIREMENTS_LOCK_NAME = "requirements-lock-windows-py311.txt"


def _git_object_id(repo_root: Path, revision: str) -> str:
    result = subprocess.run(
        ["git", "rev-parse", revision],
        cwd=repo_root,
        check=True,
        capture_output=True,
        text=True,
        encoding="ascii",
    )
    object_id = result.stdout.strip().casefold()
    if len(object_id) != 40 or any(character not in "0123456789abcdef" for character in object_id):
        raise ValueError(f"Git revision did not resolve to a full object ID: {revision!r}")
    return object_id


def _installed_pyinstaller_version() -> str:
    try:
        version = importlib.metadata.version("pyinstaller")
    except importlib.metadata.PackageNotFoundError as exc:
        raise ValueError("PyInstaller is not installed in the build environment") from exc
    if version != EXPECTED_PYINSTALLER:
        raise ValueError(
            f"PyInstaller {EXPECTED_PYINSTALLER} is required; found {version}"
        )
    return version


def _qualification_fields(
    package_root: Path,
    *,
    repo_root: Path,
    source_commit: str,
) -> dict[str, object]:
    evidence_root = package_root / FINAL_AUDIT_DIRECTORY
    waiver_path = evidence_root / FINAL_WAIVER_NAME
    if waiver_path.is_file():
        waiver_content = waiver_path.read_bytes()
        validate_waiver_bytes(waiver_content)
        return {
            "qualification_mode": "USER_WAIVED_8H_GATES",
            "waiver_sha256": sha256_bytes(waiver_content),
            "eight_hour_single_instance_soak_verified": False,
            "eight_hour_three_named_instances_soak_verified": False,
            "eight_hour_soak_verified": False,
            "final_eligible": False,
            "user_approved_final_without_eight_hour_soak": True,
        }

    single_content = (evidence_root / FINAL_SINGLE_EVIDENCE_NAME).read_bytes()
    three_content = (evidence_root / FINAL_THREE_EVIDENCE_NAME).read_bytes()
    context_content = (evidence_root / FINAL_QUALIFICATION_CONTEXT_NAME).read_bytes()
    context = validate_context_bytes(
        context_content=context_content,
        single_content=single_content,
        three_content=three_content,
        repo_root=repo_root,
        release_commit=source_commit,
    )
    return {
        "qualification_mode": "COMPLETED_8H_GATES",
        "qualification_runtime_source_commit": context["runtime_source_commit"],
        "qualification_runtime_src_tree": context["runtime_src_tree"],
        "single_evidence_sha256": sha256_bytes(single_content),
        "three_evidence_sha256": sha256_bytes(three_content),
        "eight_hour_single_instance_soak_verified": True,
        "eight_hour_three_named_instances_soak_verified": True,
        "eight_hour_soak_verified": True,
        "final_eligible": True,
        "user_approved_final_without_eight_hour_soak": False,
    }


def write_source_native_provenance(
    package_root: Path,
    *,
    release_root: Path,
    repo_root: Path,
    version: str,
    source_commit: str,
    pyinstaller_version: str,
) -> Path:
    """Write fail-closed provenance for a runtime built directly from source."""

    lock_path = release_root / REQUIREMENTS_LOCK_NAME
    if not lock_path.is_file():
        raise FileNotFoundError(f"Windows dependency lock is missing: {lock_path}")
    runtime_src_tree = _git_object_id(repo_root, f"{source_commit}:src")
    payload: dict[str, object] = {
        "schema": 2,
        "version": release_utils.validate_semver(version),
        "qualifier": release_utils.FINAL_QUALIFIER,
        "build_mode": "source_native",
        "source_commit": source_commit,
        "runtime_source_commit": source_commit,
        "runtime_src_tree": runtime_src_tree,
        "python_version": ".".join(str(part) for part in EXPECTED_PYTHON),
        "pyinstaller_version": pyinstaller_version,
        "requirements_lock_sha256": sha256_file(lock_path),
        "windows_base_name": None,
        "windows_base_sha256": None,
        "clean_committed_snapshot": True,
    }
    payload.update(
        _qualification_fields(
            package_root,
            repo_root=repo_root,
            source_commit=source_commit,
        )
    )
    destination = package_root / FINAL_PROVENANCE_NAME
    destination.write_text(
        json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=True) + "\n",
        encoding="ascii",
    )
    return destination


def _run_pyinstaller(
    release_root: Path,
    *,
    entry_name: str,
    dist_root: Path,
    work_root: Path,
) -> Path:
    spec_path = release_root / "build_scripts" / f"{entry_name}.spec"
    if not spec_path.is_file():
        raise FileNotFoundError(f"PyInstaller spec is missing: {spec_path}")
    subprocess.run(
        [
            sys.executable,
            "-m",
            "PyInstaller",
            "--noconfirm",
            "--clean",
            "--distpath",
            str(dist_root),
            "--workpath",
            str(work_root / entry_name),
            str(spec_path),
        ],
        cwd=release_root,
        check=True,
    )
    return dist_root / entry_name


def _stage_built_runtimes(pyinstaller_dist: Path, package_root: Path) -> None:
    package_root.mkdir(parents=True, exist_ok=False)
    for entry_name, internal_name in RUNTIME_LAYOUTS.items():
        build_root = pyinstaller_dist / entry_name
        executable = build_root / f"{entry_name}.exe"
        internal = build_root / internal_name
        if not executable.is_file() or executable.read_bytes()[:2] != b"MZ":
            raise ValueError(f"PyInstaller output is missing a valid PE executable: {executable}")
        if not internal.is_dir():
            raise ValueError(f"PyInstaller output is missing its isolated runtime: {internal}")
        shutil.copy2(executable, package_root / executable.name)
        shutil.copytree(internal, package_root / internal.name)


def _directory_sha256_manifest(root: Path) -> dict[str, str]:
    """Return a stable content manifest for a regular-file directory tree."""

    manifest: dict[str, str] = {}
    for path in sorted(root.rglob("*"), key=lambda item: item.as_posix()):
        if path.is_symlink():
            raise ValueError(f"staged Windows package must not contain symlinks: {path}")
        if path.is_file():
            relative = path.relative_to(root).as_posix()
            manifest[relative] = sha256_file(path)
    return manifest


def _stage_verified_outputs_on_destination_volumes(
    staged_package: Path,
    staged_archive: Path,
    *,
    package_dir: Path,
    output: Path,
) -> tuple[Path, Path, Path]:
    """Copy verified outputs beside their destinations and verify every byte.

    The short build workspace intentionally lives under the operating-system
    temporary directory, which can be on a different Windows volume than the
    GitHub Actions workspace.  Windows cannot rename across volumes, so the
    final authoritative rename must start from destination-local staging.
    """

    promotion_root = Path(
        tempfile.mkdtemp(prefix=f".{package_dir.name}.promote-", dir=package_dir.parent)
    )
    local_package = promotion_root / package_dir.name
    local_archive: Path | None = None
    try:
        shutil.copytree(staged_package, local_package)
        if _directory_sha256_manifest(local_package) != _directory_sha256_manifest(
            staged_package
        ):
            raise ValueError("destination-local Windows package copy failed verification")

        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{output.name}.",
            suffix=".tmp",
            dir=output.parent,
        )
        os.close(descriptor)
        local_archive = Path(temporary_name)
        shutil.copy2(staged_archive, local_archive)
        if sha256_file(local_archive) != sha256_file(staged_archive):
            raise ValueError("destination-local Windows archive copy failed verification")
        return promotion_root, local_package, local_archive
    except BaseException:
        if local_archive is not None:
            local_archive.unlink(missing_ok=True)
        shutil.rmtree(promotion_root, ignore_errors=True)
        raise


def build_windows_final(
    *,
    version: str,
    output: Path,
    package_dir: Path,
    project_root: Path,
    commit: str,
) -> Path:
    """Build, verify, and promote a FINAL Windows archive from exact commit bytes."""

    normalized_version = release_utils.validate_semver(version)
    expected_output_name = release_utils.artifact_name(
        normalized_version,
        qualifier=release_utils.FINAL_QUALIFIER,
    )
    if output.name != expected_output_name:
        raise ValueError(f"output must be named {expected_output_name!r}")
    if sys.version_info[:3] != EXPECTED_PYTHON:
        actual = ".".join(str(part) for part in sys.version_info[:3])
        raise ValueError(
            f"source-native Windows build requires CPython 3.11.9; found {actual}"
        )

    project_root = project_root.resolve()
    verified_commit = release_utils.resolve_clean_commit(project_root, commit)
    pyinstaller_version = _installed_pyinstaller_version()
    package_dir = package_dir.resolve()
    output = output.resolve()
    package_dir.parent.mkdir(parents=True, exist_ok=True)
    output.parent.mkdir(parents=True, exist_ok=True)

    # PyInstaller and some dependency metadata still encounter legacy Windows
    # path limits.  A repository-local staging path can exceed MAX_PATH before
    # COLLECT reaches nested SBOM files (for example cryptography's CycloneDX
    # metadata), so use the OS temporary root and promote only verified bytes.
    temporary_parent = Path(tempfile.mkdtemp(prefix="hunterx-v052-final-"))
    promotion_root: Path | None = None
    local_archive: Path | None = None
    try:
        release_root = snapshot_release_source(
            project_root,
            temporary_parent / "source_snapshot",
            verified_commit,
        )
        declared_version = release_utils.project_version(
            release_root / "src" / "hunter_metadata.py"
        )
        if declared_version != normalized_version:
            raise ValueError(
                "release version mismatch: "
                f"requested {normalized_version}, project declares {declared_version}"
            )

        pyinstaller_dist = temporary_parent / "pyinstaller-dist"
        pyinstaller_work = temporary_parent / "pyinstaller-work"
        for entry_name in RUNTIME_LAYOUTS:
            _run_pyinstaller(
                release_root,
                entry_name=entry_name,
                dist_root=pyinstaller_dist,
                work_root=pyinstaller_work,
            )

        staged_package = temporary_parent / package_dir.name
        _stage_built_runtimes(pyinstaller_dist, staged_package)
        overlay_release_files(
            release_root,
            staged_package,
            qualifier=release_utils.FINAL_QUALIFIER,
        )
        write_source_native_provenance(
            staged_package,
            release_root=release_root,
            repo_root=project_root,
            version=normalized_version,
            source_commit=verified_commit,
            pyinstaller_version=pyinstaller_version,
        )

        staged_archive = temporary_parent / expected_output_name
        build_windows_archive(staged_package, staged_archive)
        verify_windows_archive(
            staged_archive,
            normalized_version,
            qualifier=release_utils.FINAL_QUALIFIER,
        )
        verify_archive_package(staged_archive)
        release_utils.resolve_clean_commit(project_root, verified_commit)

        promotion_root, local_package, local_archive = (
            _stage_verified_outputs_on_destination_volumes(
                staged_package,
                staged_archive,
                package_dir=package_dir,
                output=output,
            )
        )
        if package_dir.exists():
            shutil.rmtree(package_dir)
        promote_staged_package(local_package, package_dir)
        output.unlink(missing_ok=True)
        local_archive.replace(output)
        local_archive = None
    finally:
        if local_archive is not None:
            local_archive.unlink(missing_ok=True)
        if promotion_root is not None:
            shutil.rmtree(promotion_root, ignore_errors=True)
        shutil.rmtree(temporary_parent, ignore_errors=True)
    return output


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--package-dir", type=Path, required=True)
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    parser.add_argument("--commit", required=True)
    return parser


def main() -> int:
    args = _build_parser().parse_args()
    try:
        result = build_windows_final(
            version=args.version,
            output=args.output,
            package_dir=args.package_dir,
            project_root=args.project_root,
            commit=args.commit,
        )
    except (
        OSError,
        RuntimeError,
        ValueError,
        EOFError,
        zipfile.BadZipFile,
        subprocess.CalledProcessError,
    ) as exc:
        print(f"Source-native Windows FINAL build failed: {exc}")
        return 1
    print(result.resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
