from __future__ import annotations

import importlib


SAFE_MODULES = [
    "hunter_metadata",
    "ocr_cache",
    "performance",
    "util",
    "settings",
    "platforms",
]


def test_safe_modules_import_without_launching_runtime() -> None:
    for module_name in SAFE_MODULES:
        module = importlib.import_module(module_name)
        assert module is not None
