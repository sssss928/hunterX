from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import ocr_cache


class FakeDdddOcr:
    instances: list["FakeDdddOcr"] = []

    def __init__(self, **kwargs: object) -> None:
        self.kwargs = kwargs
        self.ranges: int | None = None
        FakeDdddOcr.instances.append(self)

    def set_ranges(self, ranges: int) -> None:
        self.ranges = ranges


def _install_fake_ocr(monkeypatch) -> None:
    FakeDdddOcr.instances.clear()
    ocr_cache.clear_ocr_cache()
    monkeypatch.setattr(ocr_cache, "ddddocr", SimpleNamespace(DdddOcr=FakeDdddOcr))


def test_standard_ocr_cache_reuses_matching_profile(monkeypatch) -> None:
    _install_fake_ocr(monkeypatch)
    config = {"ocr_captcha": {"enable": True, "use_universal": False, "beta": False}}

    first = ocr_cache.get_ocr_instance(config, platform_hint="funone", fallback_ranges=5)
    second = ocr_cache.get_ocr_instance(config, platform_hint="funone", fallback_ranges=5)

    assert first is second
    assert first.ranges == 5
    assert len(FakeDdddOcr.instances) == 1


def test_standard_ocr_cache_rebuilds_when_ranges_change(monkeypatch) -> None:
    _install_fake_ocr(monkeypatch)
    config = {"ocr_captcha": {"enable": True, "use_universal": False, "beta": False}}

    first = ocr_cache.get_ocr_instance(config, fallback_ranges=1)
    second = ocr_cache.get_ocr_instance(config, fallback_ranges=5)

    assert first is not second
    assert first.ranges == 1
    assert second.ranges == 5
    assert len(FakeDdddOcr.instances) == 2


def test_tixcraft_profile_prefers_platform_model(monkeypatch, tmp_path) -> None:
    _install_fake_ocr(monkeypatch)
    model_dir = tmp_path / "assets" / "model" / "tixcraft_tm"
    model_dir.mkdir(parents=True)
    (model_dir / "custom.onnx").write_bytes(b"onnx")
    (model_dir / "charsets.json").write_text("[]", encoding="utf-8")
    monkeypatch.setattr(ocr_cache.util, "get_app_root", lambda: str(tmp_path))
    config = {
        "homepage": "https://tixcraft.com/",
        "ocr_captcha": {
            "enable": True,
            "use_universal": True,
            "path": ocr_cache.CONST_DEFAULT_UNIVERSAL_PATH,
            "beta": False,
        },
    }

    instance = ocr_cache.get_ocr_instance(config, platform_hint="tixcraft", fallback_ranges=1)

    assert instance.ranges is None
    assert Path(instance.kwargs["import_onnx_path"]).as_posix().endswith("assets/model/tixcraft_tm/custom.onnx")


def test_prefer_universal_false_keeps_platform_specific_charset(monkeypatch, tmp_path) -> None:
    _install_fake_ocr(monkeypatch)
    model_dir = tmp_path / "assets" / "model" / "universal"
    model_dir.mkdir(parents=True)
    (model_dir / "custom.onnx").write_bytes(b"onnx")
    (model_dir / "charsets.json").write_text("[]", encoding="utf-8")
    monkeypatch.setattr(ocr_cache.util, "get_app_root", lambda: str(tmp_path))
    config = {
        "ocr_captcha": {
            "enable": True,
            "use_universal": True,
            "path": ocr_cache.CONST_DEFAULT_UNIVERSAL_PATH,
            "beta": False,
        },
    }

    instance = ocr_cache.get_ocr_instance(
        config,
        platform_hint="funone",
        fallback_ranges=5,
        prefer_universal=False,
    )

    assert instance.ranges == 5
    assert instance.kwargs == {"show_ad": False, "beta": False}
