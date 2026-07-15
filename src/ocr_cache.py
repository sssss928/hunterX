from __future__ import annotations

import os
from dataclasses import dataclass
from time import perf_counter_ns
from typing import Any

import util

try:
    import ddddocr
except Exception:
    ddddocr = None


CONST_TIXCRAFT_TM_MODEL_PATH = "assets/model/tixcraft_tm"
CONST_DEFAULT_UNIVERSAL_PATH = "assets/model/universal"

_TIXCRAFT_DOMAINS = ("tixcraft.com", "indievox.com", "ticketmaster.")
_OCR_CACHE: dict["OcrProfile", Any] = {}


@dataclass(frozen=True)
class OcrProfile:
    mode: str
    model_path: str
    beta: bool
    ranges: int | None


def _log(debug: Any, *parts: object) -> None:
    if debug is not None:
        debug.log(*parts)


def _elapsed_ms(started_ns: int) -> float:
    return max(0.0, (perf_counter_ns() - started_ns) / 1_000_000)


def _resolve_model_path(model_path: str) -> str:
    if not model_path:
        return ""
    if os.path.isabs(model_path):
        return os.path.normpath(model_path)
    return os.path.normpath(os.path.join(util.get_app_root(), model_path))


def _has_universal_model(model_path: str) -> bool:
    if not model_path:
        return False
    return os.path.exists(os.path.join(model_path, "custom.onnx")) and os.path.exists(
        os.path.join(model_path, "charsets.json")
    )


def _is_tixcraft_scope(config_dict: dict[str, Any], platform_hint: str | None) -> bool:
    hint = (platform_hint or "").lower()
    if hint in {"tixcraft", "ticketmaster", "indievox"}:
        return True
    homepage = str(config_dict.get("homepage", "")).lower()
    return any(domain in homepage for domain in _TIXCRAFT_DOMAINS)


def _universal_profile(config_dict: dict[str, Any], platform_hint: str | None) -> OcrProfile | None:
    ocr_cfg = config_dict.get("ocr_captcha", {})
    if not ocr_cfg.get("use_universal", True):
        return None

    user_path = str(ocr_cfg.get("path", "") or "")
    candidate_paths: list[str] = []
    if user_path and user_path != CONST_DEFAULT_UNIVERSAL_PATH:
        candidate_paths.append(_resolve_model_path(user_path))
    else:
        if _is_tixcraft_scope(config_dict, platform_hint):
            candidate_paths.append(_resolve_model_path(CONST_TIXCRAFT_TM_MODEL_PATH))
        if user_path:
            candidate_paths.append(_resolve_model_path(user_path))
        candidate_paths.append(_resolve_model_path(CONST_DEFAULT_UNIVERSAL_PATH))

    for model_path in dict.fromkeys(candidate_paths):
        if _has_universal_model(model_path):
            return OcrProfile("universal", model_path, False, None)
    return None


def resolve_ocr_profile(
    config_dict: dict[str, Any],
    platform_hint: str | None = None,
    fallback_ranges: int | None = 1,
    prefer_universal: bool = True,
) -> OcrProfile | None:
    ocr_cfg = config_dict.get("ocr_captcha", {})
    if not ocr_cfg.get("enable", False):
        return None

    if prefer_universal:
        universal = _universal_profile(config_dict, platform_hint)
        if universal is not None:
            return universal

    return OcrProfile(
        "standard",
        "",
        bool(ocr_cfg.get("beta", False)),
        fallback_ranges,
    )


def _create_from_profile(profile: OcrProfile) -> Any:
    if ddddocr is None:
        return None

    if profile.mode == "universal":
        return ddddocr.DdddOcr(
            det=False,
            ocr=False,
            show_ad=False,
            import_onnx_path=os.path.join(profile.model_path, "custom.onnx"),
            charsets_path=os.path.join(profile.model_path, "charsets.json"),
        )

    ocr = ddddocr.DdddOcr(show_ad=False, beta=profile.beta)
    if profile.ranges is not None:
        ocr.set_ranges(profile.ranges)
    return ocr


def get_ocr_instance(
    config_dict: dict[str, Any],
    platform_hint: str | None = None,
    fallback_ranges: int | None = 1,
    debug: Any = None,
    prefer_universal: bool = True,
) -> Any:
    profile = resolve_ocr_profile(config_dict, platform_hint, fallback_ranges, prefer_universal)
    if profile is None:
        return None

    cached = _OCR_CACHE.get(profile)
    if cached is not None:
        _log(debug, f"[OCR CACHE] Reusing OCR instance: mode={profile.mode} path={profile.model_path or 'default'}")
        return cached

    started_ns = perf_counter_ns()
    ocr = _create_from_profile(profile)
    if ocr is None:
        _log(debug, "[OCR CACHE] ddddocr component unavailable")
        return None

    _OCR_CACHE[profile] = ocr
    _log(
        debug,
        "[OCR CACHE] Initialized OCR instance:",
        f"mode={profile.mode}",
        f"path={profile.model_path or 'default'}",
        f"ranges={profile.ranges}",
        f"elapsed_ms={_elapsed_ms(started_ns):.3f}",
    )
    return ocr


def preload_ocr_instance(
    config_dict: dict[str, Any],
    platform_hint: str | None = None,
    fallback_ranges: int | None = 1,
    debug: Any = None,
    prefer_universal: bool = True,
) -> Any:
    return get_ocr_instance(config_dict, platform_hint, fallback_ranges, debug, prefer_universal)


def create_universal_ocr(config_dict: dict[str, Any], debug: Any = None) -> Any:
    profile = _universal_profile(config_dict, platform_hint=None)
    if profile is None:
        _log(debug, "[OCR CACHE] Universal model files not found or disabled")
        return None
    cached = _OCR_CACHE.get(profile)
    if cached is not None:
        return cached
    started_ns = perf_counter_ns()
    ocr = _create_from_profile(profile)
    if ocr is None:
        _log(debug, "[OCR CACHE] ddddocr component unavailable")
        return None
    _OCR_CACHE[profile] = ocr
    _log(debug, "[OCR CACHE] Initialized universal OCR:", f"path={profile.model_path}", f"elapsed_ms={_elapsed_ms(started_ns):.3f}")
    return ocr


def clear_ocr_cache() -> None:
    _OCR_CACHE.clear()


def get_ocr_cache_stats() -> dict[str, int]:
    return {"size": len(_OCR_CACHE)}
