from __future__ import annotations

import json
from pathlib import Path

import settings


def test_default_config_contains_required_sections() -> None:
    config = settings.get_default_config()
    assert config["homepage"] == settings.CONST_HOMEPAGE_DEFAULT
    assert config["webdriver_type"] == settings.CONST_WEBDRIVER_TYPE_NODRIVER
    assert "advanced" in config
    assert "accounts" in config
    assert config["advanced"]["server_port"] == settings.CONST_SERVER_PORT


def test_load_json_missing_config_returns_default(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(settings.util, "get_app_root", lambda: str(tmp_path))

    config_path, config = settings.load_json()

    assert config_path == str(tmp_path / settings.CONST_MAXBOT_CONFIG_FILE)
    assert config["homepage"] == settings.CONST_HOMEPAGE_DEFAULT


def test_load_json_invalid_config_falls_back_to_default(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.setattr(settings.util, "get_app_root", lambda: str(tmp_path))
    (tmp_path / settings.CONST_MAXBOT_CONFIG_FILE).write_text("{not valid json", encoding="utf-8")

    _, config = settings.load_json()

    captured = capsys.readouterr()
    assert "Failed to load" in captured.out
    assert "Using default settings" in captured.out
    assert config["homepage"] == settings.CONST_HOMEPAGE_DEFAULT


def test_migrate_config_fills_missing_sections() -> None:
    config = {"advanced": {"server_port": 16889}, "accounts": {"discount_code": "ABC"}}

    migrated = settings.migrate_config(json.loads(json.dumps(config)))

    assert migrated["advanced"]["server_port"] == 16889
    assert migrated["advanced"]["discount_code"] == "ABC"
    assert "ocr_captcha" in migrated
    assert "kktix" in migrated
