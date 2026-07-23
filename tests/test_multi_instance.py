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
    stop_file = tmp_path / "instances" / "kktix-a" / settings.CONST_MAXBOT_AUTOMATION_STOP_FILE
    quit_file = tmp_path / "instances" / "kktix-a" / settings.CONST_MAXBOT_INT28_QUIT_FILE
    assert stop_file.exists()
    assert not quit_file.exists()

    settings.maxbot_resume("kktix-a")
    assert not stop_file.exists()

    settings.maxbot_quit("kktix-a")
    assert quit_file.exists()


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


def test_cleanup_orphan_instance_dirs_preserves_live_and_removes_stale(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(settings.util, "get_app_root", lambda: str(tmp_path))
    live_dir = tmp_path / "instances" / "default-2"
    stale_dir = tmp_path / "instances" / "default-3"
    live_dir.mkdir(parents=True)
    stale_dir.mkdir(parents=True)
    (live_dir / settings.CONST_HEARTBEAT_FILE).write_text(str(int(time.time())), encoding="utf-8")
    stale_heartbeat = stale_dir / settings.CONST_HEARTBEAT_FILE
    stale_heartbeat.write_text("1", encoding="utf-8")
    old_time = time.time() - settings.CONST_HEARTBEAT_ALIVE_SEC - 5
    os.utime(stale_heartbeat, (old_time, old_time))

    removed, preserved = settings.cleanup_orphan_instance_dirs()

    assert removed == ["default-3"]
    assert preserved == ["default-2"]
    assert live_dir.exists()
    assert not stale_dir.exists()


def test_related_instance_ids_include_only_numbered_duplicates(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(settings.util, "get_app_root", lambda: str(tmp_path))
    profiles_dir = tmp_path / settings.CONST_PROFILES_DIR
    profiles_dir.mkdir()
    (profiles_dir / "kktix.json").write_text("{}", encoding="utf-8")
    for name in ["kktix-2", "kktix-10", "kktix-backup"]:
        (tmp_path / "instances" / name).mkdir(parents=True)

    assert settings.get_related_instance_ids("kktix") == ["kktix", "kktix-10", "kktix-2"]


def test_remove_instance_state_dir_never_removes_default_root(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(settings.util, "get_app_root", lambda: str(tmp_path))
    (tmp_path / settings.CONST_MAXBOT_LAST_URL_FILE).write_text("https://example.com", encoding="utf-8")

    assert settings.remove_instance_state_dir(settings.CONST_DEFAULT_PROFILE) is False
    assert tmp_path.exists()
    assert (tmp_path / settings.CONST_MAXBOT_LAST_URL_FILE).exists()
