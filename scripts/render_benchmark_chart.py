#!/usr/bin/env python3
"""Render a small Markdown summary from pytest-benchmark JSON output."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def render_summary(input_path: Path) -> str:
    data = json.loads(input_path.read_text(encoding="utf-8"))
    benchmarks = data.get("benchmarks", [])
    lines = ["# Benchmark Summary", ""]
    if not benchmarks:
        lines.append("No benchmarks were recorded.")
        return "\n".join(lines) + "\n"

    lines.extend(["| Name | Mean seconds | Rounds |", "| --- | ---: | ---: |"])
    for item in benchmarks:
        stats = item.get("stats", {})
        lines.append(f"| `{item.get('name', 'unknown')}` | {stats.get('mean', 0):.8f} | {stats.get('rounds', 0)} |")
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", default="benchmark.json")
    parser.add_argument("--output", default="benchmark-summary.md")
    args = parser.parse_args()

    output = render_summary(Path(args.input))
    Path(args.output).write_text(output, encoding="utf-8")
    print(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
