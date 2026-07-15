from __future__ import annotations

import argparse
import json
import statistics
import time
from pathlib import Path
from typing import Any

import ddddocr


IMAGE_SUFFIXES = {".bmp", ".jpeg", ".jpg", ".png", ".webp"}


def _elapsed_ms(started_ns: int) -> float:
    return max(0.0, (time.perf_counter_ns() - started_ns) / 1_000_000)


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    if len(values) == 1:
        return values[0]
    ordered = sorted(values)
    index = (len(ordered) - 1) * percentile
    lower = int(index)
    upper = min(lower + 1, len(ordered) - 1)
    weight = index - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def _summarize(values: list[float]) -> dict[str, float]:
    if not values:
        return {"count": 0.0, "min_ms": 0.0, "p50_ms": 0.0, "p95_ms": 0.0, "max_ms": 0.0, "mean_ms": 0.0}
    return {
        "count": float(len(values)),
        "min_ms": min(values),
        "p50_ms": statistics.median(values),
        "p95_ms": _percentile(values, 0.95),
        "max_ms": max(values),
        "mean_ms": statistics.fmean(values),
    }


def _find_images(image_dir: Path) -> list[Path]:
    return sorted(path for path in image_dir.rglob("*") if path.is_file() and path.suffix.lower() in IMAGE_SUFFIXES)


def run_benchmark(image_dir: Path, repeat: int, beta: bool) -> dict[str, Any]:
    images = _find_images(image_dir)
    load_started_ns = time.perf_counter_ns()
    ocr = ddddocr.DdddOcr(show_ad=False, beta=beta)
    load_ms = _elapsed_ms(load_started_ns)

    samples: list[dict[str, Any]] = []
    ocr_times: list[float] = []
    for image_path in images:
        payload = image_path.read_bytes()
        for iteration in range(repeat):
            started_ns = time.perf_counter_ns()
            answer = ocr.classification(payload)
            elapsed_ms = _elapsed_ms(started_ns)
            ocr_times.append(elapsed_ms)
            samples.append(
                {
                    "file": str(image_path),
                    "iteration": iteration + 1,
                    "ocr_ms": elapsed_ms,
                    "answer_length": len(answer or ""),
                }
            )

    return {
        "image_dir": str(image_dir),
        "image_count": len(images),
        "repeat": repeat,
        "ocr_preload_ms": load_ms,
        "ocr": _summarize(ocr_times),
        "samples": samples,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Offline OCR latency benchmark for local image samples.")
    parser.add_argument("--image-dir", required=True, type=Path, help="Directory containing local captcha sample images.")
    parser.add_argument("--repeat", default=3, type=int, help="Number of OCR runs per image.")
    parser.add_argument("--beta", action="store_true", help="Use ddddocr beta mode.")
    parser.add_argument("--output", type=Path, help="Optional JSON output path.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.repeat < 1:
        raise SystemExit("--repeat must be >= 1")
    if not args.image_dir.is_dir():
        raise SystemExit(f"--image-dir does not exist or is not a directory: {args.image_dir}")

    result = run_benchmark(args.image_dir, args.repeat, args.beta)
    output = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(output + "\n", encoding="utf-8")
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
