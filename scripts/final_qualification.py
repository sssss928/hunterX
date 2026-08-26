#!/usr/bin/env python3
"""Validate the two real-browser gates required for a HunterX FINAL release."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import subprocess
from pathlib import Path
from typing import Any


MINIMUM_SOAK_SECONDS = 8 * 60 * 60
SINGLE_EVIDENCE_NAME = "FINAL_8H_SINGLE_INSTANCE_SOAK.json"
THREE_EVIDENCE_NAME = "FINAL_8H_THREE_NAMED_INSTANCES_SOAK.json"
CONTEXT_NAME = "FINAL_QUALIFICATION_CONTEXT.json"
WAIVER_NAME = "FINAL_8H_SOAK_WAIVER.json"
FULL_COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
WAIVED_GATES = ["eight_hour_single_instance", "eight_hour_three_named_instances"]


def sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _json_object(content: bytes, label: str) -> dict[str, Any]:
    try:
        decoded = content.decode("utf-8")
        payload = json.loads(decoded)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"{label} is not valid UTF-8 JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"{label} must contain one JSON object")
    return payload


def _number(value: object, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} must be a finite number")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"{label} must be a finite number")
    return result


def _integer(value: object, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{label} must be an integer")
    return value


def _require_zero(result: dict[str, Any], name: str, label: str) -> None:
    if _integer(result.get(name), f"{label}.{name}") != 0:
        raise ValueError(f"{label}.{name} must be zero for FINAL qualification")


def _validate_result(
    result: object,
    *,
    expected_instance: str,
    requested_duration: float,
    label: str,
) -> dict[str, object]:
    if not isinstance(result, dict):
        raise ValueError(f"{label} must be a JSON object")
    if result.get("instance") != expected_instance:
        raise ValueError(
            f"{label}.instance must be {expected_instance!r}, got {result.get('instance')!r}"
        )
    duration = _number(result.get("duration_seconds"), f"{label}.duration_seconds")
    if duration < requested_duration:
        raise ValueError(
            f"{label}.duration_seconds {duration} is shorter than requested "
            f"{requested_duration}"
        )
    cycles = _integer(result.get("cycles"), f"{label}.cycles")
    if cycles <= 0:
        raise ValueError(f"{label}.cycles must be positive")

    for name in (
        "target_replacements",
        "reload_injections",
        "duplicate_submit_claims",
        "errors",
        "cdp_errors",
        "recovery_count",
    ):
        _require_zero(result, name, label)

    for name in (
        "success_continue_cycles",
        "login_restore_cycles",
        "fallback_resolutions",
        "state_transition_count",
    ):
        if _integer(result.get(name), f"{label}.{name}") <= 0:
            raise ValueError(f"{label}.{name} must be positive")

    if _integer(result.get("max_tab_count"), f"{label}.max_tab_count") != 1:
        raise ValueError(f"{label}.max_tab_count must be exactly one")
    if _integer(
        result.get("browser_action_count_max"),
        f"{label}.browser_action_count_max",
    ) != 0:
        raise ValueError(f"{label}.browser_action_count_max must be zero")

    for prefix in ("task_count", "asyncio_task_count", "cdp_mapper_count"):
        start = _integer(result.get(f"{prefix}_start"), f"{label}.{prefix}_start")
        end = _integer(result.get(f"{prefix}_end"), f"{label}.{prefix}_end")
        maximum = _integer(result.get(f"{prefix}_max"), f"{label}.{prefix}_max")
        if start < 0 or end < 0 or maximum < 0:
            raise ValueError(f"{label}.{prefix} counters must not be negative")
        if end > start:
            raise ValueError(f"{label}.{prefix} leaked: start={start}, end={end}")
        if maximum < max(start, end):
            raise ValueError(f"{label}.{prefix}_max is inconsistent")

    for prefix in ("hunterx_rss", "browser_rss"):
        for suffix in ("start", "end", "max"):
            if _integer(result.get(f"{prefix}_{suffix}"), f"{label}.{prefix}_{suffix}") <= 0:
                raise ValueError(f"{label}.{prefix}_{suffix} must be positive")
        samples = result.get(f"{prefix}_samples")
        if not isinstance(samples, list) or len(samples) < 2:
            raise ValueError(f"{label}.{prefix}_samples must contain at least two samples")
        if any(_integer(item, f"{label}.{prefix}_samples") <= 0 for item in samples):
            raise ValueError(f"{label}.{prefix}_samples must contain positive integers")

    stalled = _number(result.get("stalled_seconds_max"), f"{label}.stalled_seconds_max")
    if stalled < 0:
        raise ValueError(f"{label}.stalled_seconds_max must not be negative")
    return {
        "instance": expected_instance,
        "duration_seconds": duration,
        "cycles": cycles,
        "hunterx_rss_start": result["hunterx_rss_start"],
        "hunterx_rss_end": result["hunterx_rss_end"],
        "hunterx_rss_max": result["hunterx_rss_max"],
        "browser_rss_start": result["browser_rss_start"],
        "browser_rss_end": result["browser_rss_end"],
        "browser_rss_max": result["browser_rss_max"],
    }


def validate_soak_payload(
    payload: dict[str, Any],
    *,
    expected_instances: int,
    expected_run_id: str,
    expected_isolation: str,
    label: str,
) -> dict[str, object]:
    if payload.get("status") != "PASS":
        raise ValueError(f"{label}.status must be 'PASS'")
    requested = _number(
        payload.get("requested_duration_seconds"),
        f"{label}.requested_duration_seconds",
    )
    if requested < MINIMUM_SOAK_SECONDS:
        raise ValueError(
            f"{label} requested only {requested} seconds; FINAL requires "
            f"at least {MINIMUM_SOAK_SECONDS}"
        )
    if _integer(payload.get("instances"), f"{label}.instances") != expected_instances:
        raise ValueError(f"{label}.instances must be {expected_instances}")
    if payload.get("run_id") != expected_run_id:
        raise ValueError(f"{label}.run_id must be {expected_run_id!r}")
    if payload.get("process_isolation") != expected_isolation:
        raise ValueError(f"{label}.process_isolation must be {expected_isolation!r}")

    results = payload.get("results")
    if not isinstance(results, list) or len(results) != expected_instances:
        raise ValueError(f"{label}.results must contain exactly {expected_instances} results")
    summaries = [
        _validate_result(
            result,
            expected_instance=str(index),
            requested_duration=requested,
            label=f"{label}.results[{index - 1}]",
        )
        for index, result in enumerate(results, start=1)
    ]

    if expected_instances == 3:
        workers = payload.get("workers")
        if not isinstance(workers, list) or len(workers) != 3:
            raise ValueError(f"{label}.workers must contain exactly three workers")
        for index, worker in enumerate(workers, start=1):
            if not isinstance(worker, dict):
                raise ValueError(f"{label}.workers[{index - 1}] must be an object")
            expected = {
                "instance": str(index),
                "exit_code": 0,
                "status": "PASS",
                "error": "",
            }
            for name, value in expected.items():
                if type(worker.get(name)) is not type(value) or worker.get(name) != value:
                    raise ValueError(
                        f"{label}.workers[{index - 1}].{name} must be {value!r}"
                    )

    return {
        "status": "PASS",
        "requested_duration_seconds": requested,
        "instances": expected_instances,
        "process_isolation": expected_isolation,
        "results": summaries,
    }


def validate_soak_pair_bytes(
    single_content: bytes,
    three_content: bytes,
) -> dict[str, object]:
    single = validate_soak_payload(
        _json_object(single_content, SINGLE_EVIDENCE_NAME),
        expected_instances=1,
        expected_run_id="final-single",
        expected_isolation="single_os_process",
        label="single-instance soak",
    )
    three = validate_soak_payload(
        _json_object(three_content, THREE_EVIDENCE_NAME),
        expected_instances=3,
        expected_run_id="final-three",
        expected_isolation="three_os_processes",
        label="three-named-instances soak",
    )
    return {"single": single, "three": three}


def validate_waiver_bytes(content: bytes) -> dict[str, object]:
    """Validate an explicit user waiver without representing the gates as passed."""

    payload = _json_object(content, WAIVER_NAME)
    expected_values: dict[str, object] = {
        "schema": 1,
        "status": "USER_WAIVED",
        "waived_gates": WAIVED_GATES,
        "eight_hour_single_instance_soak_verified": False,
        "eight_hour_three_named_instances_soak_verified": False,
        "eight_hour_soak_verified": False,
        "final_release_requested": True,
    }
    for name, expected in expected_values.items():
        actual = payload.get(name)
        if type(actual) is not type(expected) or actual != expected:
            raise ValueError(
                f"FINAL waiver field {name!r} must be {expected!r}, got {actual!r}"
            )
    requested_at = payload.get("requested_at")
    if not isinstance(requested_at, str) or not requested_at.strip():
        raise ValueError("FINAL waiver requested_at must be a non-empty timestamp")
    reason = payload.get("reason")
    if not isinstance(reason, str) or not reason.strip():
        raise ValueError("FINAL waiver reason must be non-empty")
    partial_duration = _number(
        payload.get("partial_run_duration_seconds"),
        "FINAL waiver partial_run_duration_seconds",
    )
    if partial_duration < 0 or partial_duration >= MINIMUM_SOAK_SECONDS:
        raise ValueError(
            "FINAL waiver partial_run_duration_seconds must describe an incomplete run"
        )
    return payload


def _git_output(repo_root: Path, *arguments: str) -> str:
    try:
        return subprocess.run(
            ["git", *arguments],
            cwd=repo_root,
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="strict",
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError, UnicodeError) as exc:
        raise ValueError(f"Unable to verify FINAL Git provenance: {exc}") from exc


def _resolved_commit(repo_root: Path, commit: str) -> str:
    if FULL_COMMIT_RE.fullmatch(commit) is None:
        raise ValueError("FINAL runtime commit must be a lowercase full 40-hex commit")
    resolved = _git_output(repo_root, "rev-parse", "--verify", f"{commit}^{{commit}}")
    if resolved != commit:
        raise ValueError(f"FINAL runtime commit did not resolve exactly: {commit!r}")
    return resolved


def build_context_payload(
    *,
    single_content: bytes,
    three_content: bytes,
    runtime_commit: str,
    repo_root: Path,
) -> dict[str, object]:
    summary = validate_soak_pair_bytes(single_content, three_content)
    resolved_runtime_commit = _resolved_commit(repo_root.resolve(), runtime_commit)
    runtime_src_tree = _git_output(repo_root.resolve(), "rev-parse", f"{runtime_commit}:src")
    return {
        "schema": 1,
        "runtime_source_commit": resolved_runtime_commit,
        "runtime_src_tree": runtime_src_tree,
        "clean_runtime_snapshot": True,
        "minimum_duration_seconds": MINIMUM_SOAK_SECONDS,
        "single_evidence_name": SINGLE_EVIDENCE_NAME,
        "single_evidence_sha256": sha256_bytes(single_content),
        "three_evidence_name": THREE_EVIDENCE_NAME,
        "three_evidence_sha256": sha256_bytes(three_content),
        "single_summary": summary["single"],
        "three_summary": summary["three"],
    }


def validate_context_bytes(
    *,
    context_content: bytes,
    single_content: bytes,
    three_content: bytes,
    repo_root: Path | None = None,
    release_commit: str | None = None,
) -> dict[str, object]:
    context = _json_object(context_content, CONTEXT_NAME)
    summary = validate_soak_pair_bytes(single_content, three_content)
    expected_values: dict[str, object] = {
        "schema": 1,
        "clean_runtime_snapshot": True,
        "minimum_duration_seconds": MINIMUM_SOAK_SECONDS,
        "single_evidence_name": SINGLE_EVIDENCE_NAME,
        "single_evidence_sha256": sha256_bytes(single_content),
        "three_evidence_name": THREE_EVIDENCE_NAME,
        "three_evidence_sha256": sha256_bytes(three_content),
        "single_summary": summary["single"],
        "three_summary": summary["three"],
    }
    for name, expected in expected_values.items():
        actual = context.get(name)
        if type(actual) is not type(expected) or actual != expected:
            raise ValueError(
                f"FINAL qualification context field {name!r} must be {expected!r}, "
                f"got {actual!r}"
            )

    runtime_commit = context.get("runtime_source_commit")
    runtime_src_tree = context.get("runtime_src_tree")
    if not isinstance(runtime_commit, str) or FULL_COMMIT_RE.fullmatch(runtime_commit) is None:
        raise ValueError("FINAL qualification runtime_source_commit must be lowercase 40-hex")
    if not isinstance(runtime_src_tree, str) or FULL_COMMIT_RE.fullmatch(runtime_src_tree) is None:
        raise ValueError("FINAL qualification runtime_src_tree must be lowercase 40-hex")

    if (repo_root is None) != (release_commit is None):
        raise ValueError("repo_root and release_commit must be provided together")
    if repo_root is not None and release_commit is not None:
        repo_root = repo_root.resolve()
        _resolved_commit(repo_root, runtime_commit)
        resolved_release = _resolved_commit(repo_root, release_commit)
        actual_runtime_tree = _git_output(repo_root, "rev-parse", f"{runtime_commit}:src")
        release_tree = _git_output(repo_root, "rev-parse", f"{resolved_release}:src")
        if actual_runtime_tree != runtime_src_tree:
            raise ValueError("FINAL qualification runtime src tree does not match its commit")
        if release_tree != runtime_src_tree:
            raise ValueError(
                "FINAL release src tree differs from the exact runtime-qualified src tree"
            )

    return context


def verify_final_qualification_files(
    *,
    single_path: Path,
    three_path: Path,
    context_path: Path,
    repo_root: Path | None = None,
    release_commit: str | None = None,
) -> dict[str, object]:
    for path in (single_path, three_path, context_path):
        if not path.is_file():
            raise FileNotFoundError(path)
    return validate_context_bytes(
        context_content=context_path.read_bytes(),
        single_content=single_path.read_bytes(),
        three_content=three_path.read_bytes(),
        repo_root=repo_root,
        release_commit=release_commit,
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    context = subparsers.add_parser("write-context")
    context.add_argument("--single", type=Path, required=True)
    context.add_argument("--three", type=Path, required=True)
    context.add_argument("--runtime-commit", required=True)
    context.add_argument("--repo-root", type=Path, default=Path.cwd())
    context.add_argument("--output", type=Path, required=True)

    verify = subparsers.add_parser("verify")
    verify.add_argument("--single", type=Path, required=True)
    verify.add_argument("--three", type=Path, required=True)
    verify.add_argument("--context", type=Path, required=True)
    verify.add_argument("--repo-root", type=Path)
    verify.add_argument("--release-commit")
    waiver = subparsers.add_parser("verify-waiver")
    waiver.add_argument("--waiver", type=Path, required=True)
    return parser


def main() -> int:
    args = _build_parser().parse_args()
    try:
        if args.command == "write-context":
            single_content = args.single.read_bytes()
            three_content = args.three.read_bytes()
            payload = build_context_payload(
                single_content=single_content,
                three_content=three_content,
                runtime_commit=args.runtime_commit,
                repo_root=args.repo_root,
            )
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(
                json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=True) + "\n",
                encoding="ascii",
            )
            print(args.output.resolve())
        elif args.command == "verify":
            result = verify_final_qualification_files(
                single_path=args.single,
                three_path=args.three,
                context_path=args.context,
                repo_root=args.repo_root,
                release_commit=args.release_commit,
            )
            print(json.dumps(result, ensure_ascii=True, sort_keys=True))
        else:
            result = validate_waiver_bytes(args.waiver.read_bytes())
            print(json.dumps(result, ensure_ascii=True, sort_keys=True))
    except (OSError, ValueError) as exc:
        print(f"FINAL qualification failed: {exc}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
