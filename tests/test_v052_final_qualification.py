from __future__ import annotations

import copy
import json
import subprocess
from pathlib import Path

import pytest

from final_qualification import (
    CONTEXT_NAME,
    MINIMUM_SOAK_SECONDS,
    SINGLE_EVIDENCE_NAME,
    THREE_EVIDENCE_NAME,
    build_context_payload,
    validate_context_bytes,
    validate_soak_pair_bytes,
    validate_waiver_bytes,
)


def _result(instance: str) -> dict[str, object]:
    return {
        "instance": instance,
        "duration_seconds": MINIMUM_SOAK_SECONDS + 0.25,
        "cycles": 50000,
        "target_replacements": 0,
        "reload_injections": 0,
        "success_continue_cycles": 100,
        "login_restore_cycles": 100,
        "fallback_resolutions": 100,
        "duplicate_submit_claims": 0,
        "errors": 0,
        "cdp_errors": 0,
        "recovery_count": 0,
        "state_transition_count": 500,
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
        "hunterx_rss_end": 110,
        "hunterx_rss_max": 120,
        "browser_rss_start": 200,
        "browser_rss_end": 210,
        "browser_rss_max": 220,
        "hunterx_rss_samples": [100, 110],
        "browser_rss_samples": [200, 210],
        "stalled_seconds_max": 11.0,
    }


def _payload(instances: int) -> dict[str, object]:
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
    return payload


def _encoded(payload: object) -> bytes:
    return (json.dumps(payload, sort_keys=True) + "\n").encode()


def _git(repo: Path, *arguments: str) -> str:
    return subprocess.run(
        ["git", *arguments],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _committed_repo(tmp_path: Path) -> tuple[Path, str]:
    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "final-gate@example.invalid")
    _git(repo, "config", "user.name", "Final Gate")
    (repo / "src" / "runtime.py").write_text("VALUE = 1\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "runtime qualified")
    return repo, _git(repo, "rev-parse", "HEAD")


def test_validates_two_complete_eight_hour_process_isolated_gates() -> None:
    result = validate_soak_pair_bytes(_encoded(_payload(1)), _encoded(_payload(3)))

    assert result["single"]["instances"] == 1
    assert result["three"]["instances"] == 3
    assert result["three"]["process_isolation"] == "three_os_processes"


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ((1, "requested_duration_seconds", 28799), "FINAL requires at least"),
        ((1, "errors", 1), "errors must be zero"),
        ((1, "duplicate_submit_claims", 1), "duplicate_submit_claims must be zero"),
        ((3, "process_isolation", "shared_asyncio_tasks"), "three_os_processes"),
        ((3, "exit_code", 1), "exit_code must be 0"),
    ],
)
def test_rejects_incomplete_failed_or_nonisolated_claims(
    mutation: tuple[int, str, object],
    message: str,
) -> None:
    single = _payload(1)
    three = _payload(3)
    instances, name, value = mutation
    target = single if instances == 1 else three
    if name in {"requested_duration_seconds", "process_isolation"}:
        target[name] = value
    elif name == "exit_code":
        target["workers"][0][name] = value  # type: ignore[index]
    else:
        target["results"][0][name] = value  # type: ignore[index]

    with pytest.raises(ValueError, match=message):
        validate_soak_pair_bytes(_encoded(single), _encoded(three))


def test_context_hashes_both_raw_evidence_files_and_detects_tampering(
    tmp_path: Path,
) -> None:
    repo, runtime_commit = _committed_repo(tmp_path)
    single = _encoded(_payload(1))
    three = _encoded(_payload(3))
    context = build_context_payload(
        single_content=single,
        three_content=three,
        runtime_commit=runtime_commit,
        repo_root=repo,
    )
    context_bytes = _encoded(context)

    validated = validate_context_bytes(
        context_content=context_bytes,
        single_content=single,
        three_content=three,
        repo_root=repo,
        release_commit=runtime_commit,
    )
    assert validated["single_evidence_name"] == SINGLE_EVIDENCE_NAME
    assert validated["three_evidence_name"] == THREE_EVIDENCE_NAME
    assert CONTEXT_NAME.endswith(".json")

    tampered = copy.deepcopy(_payload(1))
    tampered["results"][0]["cycles"] = 49999  # type: ignore[index]
    with pytest.raises(ValueError, match="single_evidence_sha256"):
        validate_context_bytes(
            context_content=context_bytes,
            single_content=_encoded(tampered),
            three_content=three,
        )


def test_final_release_must_reuse_exact_runtime_qualified_src_tree(tmp_path: Path) -> None:
    repo, runtime_commit = _committed_repo(tmp_path)
    single = _encoded(_payload(1))
    three = _encoded(_payload(3))
    context = build_context_payload(
        single_content=single,
        three_content=three,
        runtime_commit=runtime_commit,
        repo_root=repo,
    )
    (repo / "src" / "runtime.py").write_text("VALUE = 2\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "unqualified runtime mutation")
    release_commit = _git(repo, "rev-parse", "HEAD")

    with pytest.raises(ValueError, match="differs from the exact runtime-qualified"):
        validate_context_bytes(
            context_content=_encoded(context),
            single_content=single,
            three_content=three,
            repo_root=repo,
            release_commit=release_commit,
        )


def test_user_waiver_is_explicit_and_cannot_claim_soak_pass() -> None:
    waiver = {
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
        "reason": "User requested immediate delivery without the two eight-hour gates.",
        "partial_run_duration_seconds": 2052.0,
    }

    assert validate_waiver_bytes(_encoded(waiver))["status"] == "USER_WAIVED"
    waiver["eight_hour_soak_verified"] = True
    with pytest.raises(ValueError, match="must be False"):
        validate_waiver_bytes(_encoded(waiver))
