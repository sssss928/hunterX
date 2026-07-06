from __future__ import annotations

import os
import time
from pathlib import Path

import settings
import util


def test_named_instance_state_paths_are_isolated(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(util, "get_app_root", lambda: str(tmp_path))

    assert util.set_instance_id("ibon-a")
    assert util.get_instance_state_path("MAXBOT_LAST_URL.txt") == str(
        tmp_path / "instances" / "ibon-a" / "MAXBOT_LAST_URL.txt"
    )


def test_profile_file_resolution_and_listing(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(settings.util, "get_app_root", lambda: str(tmp_path))
    profiles_dir = tmp_path / settings.CONST_PROFILES_DIR
    profiles_dir.mkdir()
    (profiles_dir / "ibon-a.json").write_text("{}", encoding="utf-8")
    (profiles_dir / "bad name.json").write_text("{}", encoding="utf-8")

    assert settings.get_profile_filepath("") == str(tmp_path / settings.CONST_MAXBOT_CONFIG_FILE)
    assert settings.get_profile_filepath("default") == str(tmp_path / settings.CONST_MAXBOT_CONFIG_FILE)
    assert settings.get_profile_filepath("ibon-a") == str(profiles_dir / "ibon-a.json")
    assert settings.list_profile_names() == ["default", "ibon-a"]


def test_pause_resume_stop_are_per_instance(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(settings.util, "get_app_root", lambda: str(tmp_path))

    settings.maxbot_idle("kktix-a")
    pause_file = tmp_path / "instances" / "kktix-a" / settings.CONST_MAXBOT_INT28_FILE
    assert pause_file.exists()
    assert not (tmp_path / settings.CONST_MAXBOT_INT28_FILE).exists()

    settings.maxbot_resume("kktix-a")
    assert not pause_file.exists()

    settings.maxbot_stop("kktix-a")
    assert (tmp_path / "instances" / "kktix-a" / settings.CONST_MAXBOT_INT28_QUIT_FILE).exists()


def test_instance_status_reads_heartbeat_pause_and_last_url(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(settings.util, "get_app_root", lambda: str(tmp_path))
    instance_dir = tmp_path / "instances" / "ticketplus-a"
    instance_dir.mkdir(parents=True)
    (instance_dir / settings.CONST_HEARTBEAT_FILE).write_text(str(int(time.time())), encoding="utf-8")
    (instance_dir / settings.CONST_MAXBOT_INT28_FILE).write_text("", encoding="utf-8")
    (instance_dir / settings.CONST_MAXBOT_LAST_URL_FILE).write_text("https://ticketplus.com.tw/activity/abc", encoding="utf-8")

    status = settings.get_instance_status("ticketplus-a")

    assert status["alive"] is True
    assert status["paused"] is True
    assert status["last_url"] == "https://ticketplus.com.tw/activity/abc"
