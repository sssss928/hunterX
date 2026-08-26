from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import settings


def test_default_config_contains_required_sections() -> None:
    config = settings.get_default_config()
    assert config["homepage"] == settings.CONST_HOMEPAGE_DEFAULT
    assert config["webdriver_type"] == settings.CONST_WEBDRIVER_TYPE_NODRIVER
    assert "advanced" in config
    assert "accounts" in config
    assert config["advanced"]["server_port"] == settings.CONST_SERVER_PORT
    assert config["refresh_calibration"]["timezone"] == "Asia/Taipei"
    assert config["time_calibration"]["mode"] == "auto"
    assert config["time_calibration"]["background_refresh_seconds"] >= 60


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


def test_settings_smoke_requires_frontend_runtime_assets(tmp_path: Path, monkeypatch, capsys) -> None:
    www_root = tmp_path / "www"
    (www_root / "css").mkdir(parents=True)
    (www_root / "dist" / "bootstrap").mkdir(parents=True)
    for rel in (
        "settings.html",
        "settings.js",
        "help-content.js",
        "css/settings.css",
        "dist/bootstrap/bootstrap.min.css",
        "dist/bootstrap/bootstrap.min.js",
    ):
        (www_root / rel).write_text("", encoding="utf-8")
    monkeypatch.setattr(settings, "SCRIPT_DIR", str(tmp_path))
    monkeypatch.setattr(settings, "load_json", lambda: (str(tmp_path / "settings.json"), settings.get_default_config()))

    assert settings.handle_cli_command(["HTTP", "/run", "smoke", "test"]) is True

    captured = capsys.readouterr()
    assert "smoke test failed: missing assets" in captured.out
    assert "jquery.min.js" in captured.out


def test_settings_smoke_passes_with_frontend_runtime_assets(tmp_path: Path, monkeypatch, capsys) -> None:
    www_root = tmp_path / "www"
    (www_root / "css").mkdir(parents=True)
    (www_root / "dist" / "bootstrap").mkdir(parents=True)
    for rel in (
        "settings.html",
        "settings.js",
        "help-content.js",
        "css/settings.css",
        "dist/jquery.min.js",
        "dist/bootstrap/bootstrap.min.css",
        "dist/bootstrap/bootstrap.min.js",
    ):
        (www_root / rel).write_text("", encoding="utf-8")
    monkeypatch.setattr(settings, "SCRIPT_DIR", str(tmp_path))
    monkeypatch.setattr(settings, "load_json", lambda: (str(tmp_path / "settings.json"), settings.get_default_config()))

    assert settings.handle_cli_command(["HTTP", "/run", "smoke", "test"]) is True

    captured = capsys.readouterr()
    assert "smoke test ok" in captured.out
    assert "notification formatter: ok (1-10)" in captured.out


def test_settings_frontend_static_version_matches_release() -> None:
    html = Path("src/www/settings.html").read_text(encoding="utf-8")
    js = Path("src/www/settings.js").read_text(encoding="utf-8")

    assert "HunterX (0.5.2)" in html
    assert "HunterX (0.5.2)" in js
    assert "HunterX (0.4.4)" not in html
    assert "HunterX (0.4.4)" not in js
    assert "HunterX (0.4.3)" not in html
    assert "HunterX (0.4.3)" not in js
    assert "HunterX (0.4.2)" not in html
    assert "HunterX (0.4.2)" not in js
    assert "HunterX (0.2.1)" not in html
    assert "HunterX (0.2.1)" not in js


def test_bot_cli_exposes_safe_version_smoke() -> None:
    result = subprocess.run(
        [sys.executable, "src/nodriver_tixcraft.py", "--version"],
        check=True,
        capture_output=True,
        text=True,
    )

    assert result.stdout.strip() == "HunterX (0.5.2)"


def test_release_helpers_and_packaged_readme_match_current_version() -> None:
    release_readme = Path("build_scripts/README_Release.txt").read_text(
        encoding="utf-8"
    )
    quick_start = Path("build_scripts/QUICK_START.md").read_text(encoding="utf-8")
    build_batch = Path("build_scripts/build_and_test.bat").read_text(encoding="utf-8")

    assert "hunterX_windows_0.5.2_final.zip" in release_readme
    assert "hunterX_windows_0.5.2_final.zip" in quick_start
    assert "hunterX_windows_%VERSION%_final.zip" in build_batch
    assert "hunterX_source_%VERSION%_final.zip" in build_batch
    assert "SHA256SUMS_v%VERSION%_FINAL.txt" in build_batch
    assert "rc2" not in build_batch.casefold()
    assert "rc3" not in build_batch.casefold()
    assert "BASE_ARCHIVE" not in build_batch
    assert "project-version --metadata src\\hunter_metadata.py" in build_batch
    assert "validate-project-version --version" in build_batch
    assert "scripts\\build_windows_final.ps1" in build_batch
    assert "verify_release_archive.py windows" in build_batch
    assert "verify_release_archive.py source" in build_batch
    assert "verify_release_archive.py pair" in build_batch
    assert "git describe --tags" not in build_batch


def test_windows_build_isolates_runtimes_and_packages_license() -> None:
    build_script = Path("scripts/build_windows_final.ps1").read_text(encoding="utf-8")
    source_builder = Path("scripts/build_windows_final.py").read_text(
        encoding="utf-8"
    )
    build_batch = Path("build_scripts/build_and_test.bat").read_text(encoding="utf-8")
    bot_spec = Path("build_scripts/nodriver_tixcraft.spec").read_text(encoding="utf-8")
    settings_spec = Path("build_scripts/settings.spec").read_text(encoding="utf-8")

    assert build_script.count("$LASTEXITCODE -ne 0") >= 5
    assert "python scripts/build_windows_final.py" in build_script
    assert "--commit $Commit" in build_script
    assert "--base-archive" not in build_script
    assert "EXPECTED_PYTHON = (3, 11, 9)" in source_builder
    assert 'EXPECTED_PYINSTALLER = "6.21.0"' in source_builder
    assert '"build_mode": "source_native"' in source_builder
    assert '"windows_base_name": None' in source_builder
    assert "snapshot_release_source" in source_builder
    assert "overlay_release_files" in source_builder
    assert "verify_windows_archive" in source_builder
    assert "verify_archive_package" in source_builder
    assert '"_nodriver_internal"' not in source_builder
    assert '"_settings_internal"' not in source_builder
    assert "RUNTIME_LAYOUTS" in source_builder
    assert 'contents_directory=\'_nodriver_internal\'' in bot_spec
    assert 'contents_directory=\'_settings_internal\'' in settings_spec
    assert "python -m pytest" in build_batch
    assert "python -m pip_audit" in build_batch
    assert '-Commit "%RELEASE_COMMIT%"' in build_batch
    assert "BASE_ARCHIVE" not in build_batch
    assert 'dist\\nodriver_tixcraft\\_internal' not in build_script
    assert 'dist\\settings\\_internal' not in build_script
    assert "taskkill" not in build_batch.lower()
    assert "tasklist" not in build_batch.lower()
    assert "Merging _internal directories" not in build_batch
    assert "playsound_pathex" not in bot_spec
    assert "playsound_pathex" not in settings_spec
    assert "pathex=[os.path.join(project_root, 'src')]" in bot_spec
    assert "pathex=[]" in settings_spec


def test_migrate_config_fills_missing_sections() -> None:
    config = {"advanced": {"server_port": 16889}, "accounts": {"discount_code": "ABC"}}

    migrated = settings.migrate_config(json.loads(json.dumps(config)))

    assert migrated["advanced"]["server_port"] == 16889
    assert migrated["advanced"]["discount_code"] == "ABC"
    assert "ocr_captcha" in migrated
    assert "kktix" in migrated
    assert migrated["refresh_calibration"]["timezone"] == "Asia/Taipei"
    assert migrated["time_calibration"]["mode"] == "auto"


def test_migrate_config_normalizes_auto_reload_interval() -> None:
    config = {"advanced": {"auto_reload_page_interval": "0"}, "accounts": {}}
    migrated = settings.migrate_config(json.loads(json.dumps(config)))
    assert migrated["advanced"]["auto_reload_page_interval"] == 0.0

    config["advanced"]["auto_reload_page_interval"] = -5
    migrated = settings.migrate_config(json.loads(json.dumps(config)))
    assert migrated["advanced"]["auto_reload_page_interval"] == 0.0

    config["advanced"]["auto_reload_page_interval"] = "bad"
    migrated = settings.migrate_config(json.loads(json.dumps(config)))
    assert migrated["advanced"]["auto_reload_page_interval"] == settings.get_default_config()["advanced"]["auto_reload_page_interval"]

    config["advanced"]["auto_reload_page_interval"] = "nan"
    migrated = settings.migrate_config(json.loads(json.dumps(config)))
    assert migrated["advanced"]["auto_reload_page_interval"] == settings.get_default_config()["advanced"]["auto_reload_page_interval"]

    config["advanced"]["auto_reload_page_interval"] = "inf"
    migrated = settings.migrate_config(json.loads(json.dumps(config)))
    assert migrated["advanced"]["auto_reload_page_interval"] == settings.get_default_config()["advanced"]["auto_reload_page_interval"]


def test_normalize_non_negative_float_rejects_invalid_defaults() -> None:
    assert settings.normalize_non_negative_float(None, float("inf")) == 0.0
    assert settings.normalize_non_negative_float("", float("nan")) == 0.0
    assert settings.normalize_non_negative_float("bad", float("inf")) == 0.0
    assert settings.normalize_non_negative_float("bad", "bad") == 0.0


def test_migrate_config_disables_deprecated_delay_calibration() -> None:
    config = {
        "refresh_calibration": {
            "enable": True,
            "auto_calibrate": True,
            "advanced_delay_mode": "enabled",
            "clock_offset_ms": 50,
            "frontend_delay_ms": 75,
            "network_uplink_ms": 90,
            "safety_margin_ms": 25,
            "freeze_before_seconds": 10,
        }
    }
    migrated = settings.migrate_config(json.loads(json.dumps(config)))
    assert migrated["refresh_calibration"]["advanced_delay_mode"] == "enabled"
    assert migrated["refresh_calibration"]["enable"] is False
    assert migrated["refresh_calibration"]["auto_calibrate"] is False
    assert migrated["refresh_calibration"]["clock_offset_ms"] == 50

    for value in (None, "", "invalid", 123):
        config = {"refresh_calibration": {"advanced_delay_mode": value}}
        migrated = settings.migrate_config(json.loads(json.dumps(config)))
        assert migrated["refresh_calibration"]["advanced_delay_mode"] == "auto"
        assert migrated["refresh_calibration"]["enable"] is False

    config = {"refresh_calibration": {"advanced_delay_mode": "DISABLED"}}
    migrated = settings.migrate_config(json.loads(json.dumps(config)))
    assert migrated["refresh_calibration"]["advanced_delay_mode"] == "disabled"


def test_time_calibration_config_normalization() -> None:
    default = settings.get_default_config()["time_calibration"]
    normalized = settings.normalize_time_calibration_config(
        {
            "mode": "invalid",
            "ntp_servers": "time.google.com, pool.ntp.org",
            "ntp_timeout_ms": "1",
            "ntp_samples_per_server": "999",
            "ntp_min_valid_samples": "bad",
            "background_refresh_seconds": "1",
        },
        default,
    )

    assert normalized["mode"] == "auto"
    assert normalized["ntp_servers"] == ["time.google.com", "pool.ntp.org"]
    assert normalized["ntp_timeout_ms"] == 50
    assert normalized["ntp_samples_per_server"] == 10
    assert normalized["ntp_min_valid_samples"] == default["ntp_min_valid_samples"]
    assert normalized["background_refresh_seconds"] == 60
