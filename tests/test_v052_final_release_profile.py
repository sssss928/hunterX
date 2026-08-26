from __future__ import annotations

import json
import subprocess
import zipfile
from pathlib import Path

import pytest

import release_utils
from build_source_archive import build_source_archive
from build_windows_from_base import (
    FINAL_PROVENANCE_NAME,
    RC3_WINDOWS_BASE_NAME,
    RC3_WINDOWS_BASE_SHA256,
    REQUIRED_BASE_FILES,
    extract_verified_baseline,
    write_final_provenance,
)
from final_qualification import (
    CONTEXT_NAME,
    MINIMUM_SOAK_SECONDS,
    SINGLE_EVIDENCE_NAME,
    THREE_EVIDENCE_NAME,
    WAIVER_NAME,
    build_context_payload,
    sha256_bytes,
)
from verify_release_archive import (
    FINAL_AUDIT_PREFIX,
    FINAL_REQUIRED_DOCUMENTS,
    FINAL_ROOT_DOCUMENTS,
    FINAL_SUPPLEMENTAL_AUDIT_DOCUMENTS,
    verify_source_archive,
    verify_windows_archive,
)
from write_release_checksums import verify_final_checksums, write_checksums


VERSION = "0.5.2"


def _windows_evidence_name(name: str) -> str:
    if name in FINAL_ROOT_DOCUMENTS:
        return name
    return f"{FINAL_AUDIT_PREFIX}{name}"


def _result(instance: str) -> dict[str, object]:
    return {
        "instance": instance,
        "duration_seconds": MINIMUM_SOAK_SECONDS + 1,
        "cycles": 1000,
        "target_replacements": 0,
        "reload_injections": 0,
        "success_continue_cycles": 2,
        "login_restore_cycles": 2,
        "fallback_resolutions": 2,
        "duplicate_submit_claims": 0,
        "errors": 0,
        "cdp_errors": 0,
        "recovery_count": 0,
        "state_transition_count": 10,
        "max_tab_count": 1,
        "task_count_start": 0,
        "task_count_end": 0,
        "task_count_max": 0,
        "asyncio_task_count_start": 8,
        "asyncio_task_count_end": 8,
        "asyncio_task_count_max": 8,
        "browser_action_count_max": 0,
        "cdp_mapper_count_start": 0,
        "cdp_mapper_count_end": 0,
        "cdp_mapper_count_max": 0,
        "hunterx_rss_start": 100,
        "hunterx_rss_end": 100,
        "hunterx_rss_max": 100,
        "browser_rss_start": 200,
        "browser_rss_end": 200,
        "browser_rss_max": 200,
        "hunterx_rss_samples": [100, 100],
        "browser_rss_samples": [200, 200],
        "stalled_seconds_max": 11.0,
    }


def _evidence(instances: int) -> bytes:
    payload: dict[str, object] = {
        "status": "PASS",
        "requested_duration_seconds": MINIMUM_SOAK_SECONDS,
        "instances": instances,
        "run_id": "final-single" if instances == 1 else "final-three",
        "process_isolation": (
            "single_os_process" if instances == 1 else "three_os_processes"
        ),
        "results": [_result(str(index)) for index in range(1, instances + 1)],
    }
    if instances == 3:
        payload["workers"] = [
            {"instance": str(index), "exit_code": 0, "status": "PASS", "error": ""}
            for index in range(1, 4)
        ]
    return (json.dumps(payload, sort_keys=True) + "\n").encode()


def _waiver() -> bytes:
    return (
        json.dumps(
            {
                "schema": 1,
                "status": "USER_WAIVED",
                "waived_gates": [
                    "eight_hour_single_instance",
                    "eight_hour_three_named_instances",
                ],
                "eight_hour_single_instance_soak_verified": False,
                "eight_hour_three_named_instances_soak_verified": False,
                "eight_hour_soak_verified": False,
                "final_release_requested": True,
                "requested_at": "2026-08-25T11:31:10+08:00",
                "reason": "User requested immediate delivery without the two 8-hour gates.",
                "partial_run_duration_seconds": 2052.0,
            },
            sort_keys=True,
        )
        + "\n"
    ).encode()


def _git(repo: Path, *arguments: str) -> str:
    return subprocess.run(
        ["git", *arguments],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _release_repo(tmp_path: Path) -> tuple[Path, str, str, bytes, bytes, bytes]:
    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "final-release@example.invalid")
    _git(repo, "config", "user.name", "Final Release")
    _git(repo, "config", "core.autocrlf", "false")
    (repo / "src" / "hunter_metadata.py").write_text(
        f'APP_VERSION = "{VERSION}"\n', encoding="utf-8"
    )
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "qualified runtime")
    runtime_commit = _git(repo, "rev-parse", "HEAD")

    single = _evidence(1)
    three = _evidence(3)
    context = build_context_payload(
        single_content=single,
        three_content=three,
        runtime_commit=runtime_commit,
        repo_root=repo,
    )
    context_bytes = (json.dumps(context, indent=2, sort_keys=True) + "\n").encode()
    for name in FINAL_REQUIRED_DOCUMENTS:
        content = b"final release evidence\n"
        if name == SINGLE_EVIDENCE_NAME:
            content = single
        elif name == THREE_EVIDENCE_NAME:
            content = three
        elif name == CONTEXT_NAME:
            content = context_bytes
        (repo / name).write_bytes(content)
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "final evidence")
    return repo, runtime_commit, _git(repo, "rev-parse", "HEAD"), single, three, context_bytes


def _windows_files(
    *,
    source_commit: str,
    runtime_commit: str,
    runtime_src_tree: str,
    single: bytes,
    three: bytes,
    context: bytes,
) -> dict[str, bytes]:
    files = {
        "BUILD_INFO.txt": b"build",
        "CHANGELOG.md": b"changes",
        "LEGAL_NOTICE.md": b"legal",
        "LICENSE": b"license",
        "README_Release.txt": b"release",
        "WINDOWS_PACKAGE_zh-TW.txt": b"windows",
        "nodriver_tixcraft.exe": b"MZbot",
        "settings.exe": b"MZsettings",
        "guide/README.md": b"guide",
        "www/css/settings.css": b"css",
        "www/favicon.ico": b"ico",
        "www/settings.html": b"HunterX (0.5.2)",
        "www/settings.js": b"HunterX (0.5.2)",
        "assets/icon.png": b"png",
        "_nodriver_internal/base_library.zip": b"base-a",
        "_nodriver_internal/app_src/hunter_metadata.py": b"APP_VERSION = '0.5.2'",
        "_nodriver_internal/app_src/nodriver_tixcraft.py": b"pass",
        "_nodriver_internal/app_src/platforms/ticketplus.py": b"pass",
        "_nodriver_internal/python311.dll": b"dll-a",
        "_settings_internal/base_library.zip": b"base-b",
        "_settings_internal/app_src/hunter_metadata.py": b"APP_VERSION = '0.5.2'",
        "_settings_internal/app_src/settings.py": b"pass",
        "_settings_internal/python311.dll": b"dll-b",
    }
    files.update(
        {
            _windows_evidence_name(name): b"final evidence"
            for name in FINAL_REQUIRED_DOCUMENTS
        }
    )
    files.update(
        {
            _windows_evidence_name(name): b"supplemental evidence"
            for name in FINAL_SUPPLEMENTAL_AUDIT_DOCUMENTS
        }
    )
    files[_windows_evidence_name(SINGLE_EVIDENCE_NAME)] = single
    files[_windows_evidence_name(THREE_EVIDENCE_NAME)] = three
    files[_windows_evidence_name(CONTEXT_NAME)] = context
    files[FINAL_PROVENANCE_NAME] = json.dumps(
        {
            "schema": 1,
            "version": VERSION,
            "qualifier": "final",
            "source_commit": source_commit,
            "runtime_source_commit": runtime_commit,
            "runtime_src_tree": runtime_src_tree,
            "windows_base_name": RC3_WINDOWS_BASE_NAME,
            "windows_base_sha256": RC3_WINDOWS_BASE_SHA256,
            "single_evidence_sha256": sha256_bytes(single),
            "three_evidence_sha256": sha256_bytes(three),
            "qualification_mode": "COMPLETED_8H_GATES",
            "clean_committed_snapshot": True,
            "eight_hour_single_instance_soak_verified": True,
            "eight_hour_three_named_instances_soak_verified": True,
            "eight_hour_soak_verified": True,
            "final_eligible": True,
            "user_approved_final_without_eight_hour_soak": False,
        },
        sort_keys=True,
    ).encode()
    return files


def test_official_final_tag_and_asset_names_are_unambiguous() -> None:
    assert release_utils.resolve_version("push", "v0.5.2", qualifier="final") == "0.5.2"
    with pytest.raises(ValueError, match="without a suffix"):
        release_utils.resolve_version("push", "v0.5.2-final", qualifier="final")
    assert release_utils.artifact_name(VERSION, "windows", "final") == (
        "hunterX_windows_0.5.2_final.zip"
    )
    assert release_utils.checksum_name(VERSION, "final") == "SHA256SUMS_v0.5.2_FINAL.txt"


def test_final_baseline_accepts_only_exact_rc3_name_and_hash(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    archive_path = tmp_path / RC3_WINDOWS_BASE_NAME
    with zipfile.ZipFile(archive_path, "w") as archive:
        for name in sorted(REQUIRED_BASE_FILES):
            archive.writestr(name, b"MZfixture" if name.endswith(".exe") else b"fixture")
    monkeypatch.setattr(
        "build_windows_from_base.sha256_file", lambda _path: RC3_WINDOWS_BASE_SHA256
    )

    destination = tmp_path / "output"
    extract_verified_baseline(archive_path, destination, qualifier="final")
    assert (destination / "nodriver_tixcraft.exe").read_bytes().startswith(b"MZ")


def test_final_source_archive_requires_and_validates_both_soaks(tmp_path: Path) -> None:
    repo, _runtime, release_commit, _single, _three, _context = _release_repo(tmp_path)
    output = tmp_path / "hunterX_source_0.5.2_final.zip"

    built = build_source_archive(
        version=VERSION,
        output=output,
        repo_root=repo,
        commit=release_commit,
        qualifier="final",
    )

    assert built == output
    assert verify_source_archive(
        output,
        VERSION,
        repo,
        release_commit,
        qualifier="final",
    )["mismatch"] == 0


def test_final_windows_archive_provenance_is_machine_verified(tmp_path: Path) -> None:
    repo, runtime, release_commit, single, three, context = _release_repo(tmp_path)
    runtime_tree = _git(repo, "rev-parse", f"{runtime}:src")
    archive = tmp_path / "hunterX_windows_0.5.2_final.zip"
    with zipfile.ZipFile(archive, "w") as output:
        for name, content in _windows_files(
            source_commit=release_commit,
            runtime_commit=runtime,
            runtime_src_tree=runtime_tree,
            single=single,
            three=three,
            context=context,
        ).items():
            output.writestr(name, content)

    assert verify_windows_archive(archive, VERSION, qualifier="final")["provenance"] == (
        "verified"
    )

    tampered_archive = tmp_path / "tampered" / "hunterX_windows_0.5.2_final.zip"
    tampered_archive.parent.mkdir()
    tampered_files = _windows_files(
        source_commit=release_commit,
        runtime_commit=runtime,
        runtime_src_tree=runtime_tree,
        single=single,
        three=three,
        context=context,
    )
    tampered_files[_windows_evidence_name(SINGLE_EVIDENCE_NAME)] = single.replace(
        b'"errors": 0', b'"errors": 1', 1
    )
    with zipfile.ZipFile(tampered_archive, "w") as output:
        for name, content in tampered_files.items():
            output.writestr(name, content)
    with pytest.raises(ValueError, match="errors must be zero"):
        verify_windows_archive(tampered_archive, VERSION, qualifier="final")


def test_final_windows_archive_rejects_root_level_release_clutter(
    tmp_path: Path,
) -> None:
    repo, runtime, release_commit, single, three, context = _release_repo(tmp_path)
    files = _windows_files(
        source_commit=release_commit,
        runtime_commit=runtime,
        runtime_src_tree=_git(repo, "rev-parse", f"{runtime}:src"),
        single=single,
        three=three,
        context=context,
    )
    files["FINAL_AUDIT_v0.5.2.md"] = b"misplaced root report"
    archive = tmp_path / "hunterX_windows_0.5.2_final.zip"
    with zipfile.ZipFile(archive, "w") as output:
        for name, content in files.items():
            output.writestr(name, content)

    with pytest.raises(ValueError, match="unexpected top-level items"):
        verify_windows_archive(archive, VERSION, qualifier="final")


def test_final_provenance_writer_and_checksum_set_are_exact(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo, runtime, release_commit, single, three, context = _release_repo(tmp_path)
    package = tmp_path / "package"
    package.mkdir()
    audit = package / Path(FINAL_AUDIT_PREFIX)
    audit.mkdir(parents=True)
    (audit / SINGLE_EVIDENCE_NAME).write_bytes(single)
    (audit / THREE_EVIDENCE_NAME).write_bytes(three)
    (audit / CONTEXT_NAME).write_bytes(context)
    base = tmp_path / RC3_WINDOWS_BASE_NAME
    base.write_bytes(b"verified rc3")
    monkeypatch.setattr(
        "build_windows_from_base.sha256_file", lambda _path: RC3_WINDOWS_BASE_SHA256
    )

    provenance = write_final_provenance(
        package,
        version=VERSION,
        source_commit=release_commit,
        base_archive=base,
        repo_root=repo,
    )
    payload = json.loads(provenance.read_text(encoding="ascii"))
    assert payload["runtime_source_commit"] == runtime
    assert payload["eight_hour_soak_verified"] is True
    assert payload["final_eligible"] is True

    windows = tmp_path / "hunterX_windows_0.5.2_final.zip"
    source = tmp_path / "hunterX_source_0.5.2_final.zip"
    windows.write_bytes(b"windows")
    source.write_bytes(b"source")
    manifest = write_checksums(
        [windows, source], tmp_path / "SHA256SUMS_v0.5.2_FINAL.txt"
    )
    assert verify_final_checksums(manifest, tmp_path, VERSION) == [windows, source]


def test_explicit_user_waiver_builds_without_claiming_eight_hour_pass(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo, _runtime, _release_commit, _single, _three, _context = _release_repo(tmp_path)
    for name in (SINGLE_EVIDENCE_NAME, THREE_EVIDENCE_NAME, CONTEXT_NAME):
        (repo / name).unlink()
    waiver = _waiver()
    (repo / WAIVER_NAME).write_bytes(waiver)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "explicit soak waiver")
    release_commit = _git(repo, "rev-parse", "HEAD")

    source = tmp_path / "hunterX_source_0.5.2_final.zip"
    build_source_archive(
        version=VERSION,
        output=source,
        repo_root=repo,
        commit=release_commit,
        qualifier="final",
    )
    assert verify_source_archive(
        source, VERSION, repo, release_commit, qualifier="final"
    )["mismatch"] == 0

    package = tmp_path / "package-waived"
    package.mkdir()
    audit = package / Path(FINAL_AUDIT_PREFIX)
    audit.mkdir(parents=True)
    (audit / WAIVER_NAME).write_bytes(waiver)
    base = tmp_path / RC3_WINDOWS_BASE_NAME
    base.write_bytes(b"verified rc3")
    monkeypatch.setattr(
        "build_windows_from_base.sha256_file", lambda _path: RC3_WINDOWS_BASE_SHA256
    )
    provenance_path = write_final_provenance(
        package,
        version=VERSION,
        source_commit=release_commit,
        base_archive=base,
        repo_root=repo,
    )
    provenance = json.loads(provenance_path.read_text(encoding="ascii"))
    assert provenance["qualification_mode"] == "USER_WAIVED_8H_GATES"
    assert provenance["eight_hour_soak_verified"] is False
    assert provenance["final_eligible"] is False
    assert provenance["user_approved_final_without_eight_hour_soak"] is True
