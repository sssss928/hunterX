from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone

import nodriver_tixcraft
import settings
import util
from onsale_preflight import build_onsale_preflight


def test_new_config_excludes_accessibility_ticket_keyword() -> None:
    assert "愛心" in settings.get_default_config()["keyword_exclude"]


def test_migration_preserves_user_exclusions_and_removes_dead_overheat() -> None:
    original = {
        "keyword_exclude": '"我自己的排除"',
        "advanced": {
            "auto_reload_page_interval": "4.5",
            "auto_reload_overheat_count": 2,
            "auto_reload_overheat_cd": 1,
        },
    }

    migrated = settings.migrate_config(deepcopy(original))

    assert migrated["keyword_exclude"] == '"我自己的排除"'
    assert migrated["advanced"]["auto_reload_page_interval"] == 4.5
    assert "auto_reload_overheat_count" not in migrated["advanced"]
    assert "auto_reload_overheat_cd" not in migrated["advanced"]
    assert migrated["refresh_calibration"]["timezone"] == "Asia/Taipei"


def test_ibon_tracker_blocks_are_exact_and_platform_scoped() -> None:
    expected = (
        "*tour-uat.ibon.com.tw/dmp/api/v1.0/events*",
        "*order-uat.ibon.com.tw/dmp/api/v1.0/events*",
    )
    assert nodriver_tixcraft.IBON_TRACKER_BLOCK_PATTERNS == expected
    assert nodriver_tixcraft.platform_specific_network_block_patterns(
        "https://ticket.ibon.com.tw/ActivityInfo/Details/demo"
    ) == expected
    assert nodriver_tixcraft.platform_specific_network_block_patterns(
        "https://tixcraft.com/activity/detail/demo"
    ) == ()
    assert all("/dmp/api/v1.0/events" in item for item in expected)
    assert all("payment" not in item and "checkout" not in item for item in expected)


def test_persistent_empty_url_safe_stop_uses_monotonic_threshold() -> None:
    predicate = nodriver_tixcraft.persistent_empty_url_should_stop

    assert predicate(100.0, 129.999) is False
    assert predicate(100.0, 130.0) is True
    assert predicate(100.0, 99.0) is False
    assert predicate(0.0, 10_000.0) is False


def test_onsale_preflight_is_read_only_explicit_and_timezone_aware() -> None:
    target = datetime(2026, 8, 10, 11, 0, tzinfo=timezone(timedelta(hours=8)))
    report = build_onsale_preflight(
        {
            "ticket_number": 2,
            "date_auto_select": {},
            "area_auto_select": {},
            "advanced": {"run_mode": "onsale"},
        },
        "https://ticketplus.com.tw/activity/bigbang",
        target,
        now=target - timedelta(minutes=10),
    )

    assert report["ready"] is True
    assert report["platform"] == "ticketplus"
    assert all(item["status"] != "error" for item in report["checks"])
    assert any(item["name"] == "session" for item in report["checks"])


def test_onsale_preflight_fails_closed_for_bad_target_route_and_count() -> None:
    report = build_onsale_preflight(
        {
            "ticket_number": 0,
            "advanced": {"run_mode": "onsale"},
        },
        "",
        None,
    )

    errors = {item["name"] for item in report["checks"] if item["status"] == "error"}
    assert report["ready"] is False
    assert {"sale_target", "browser_route", "ticket_number"}.issubset(errors)


def test_launcher_uses_argv_shell_false_with_spaces_and_metacharacters(
    monkeypatch,
) -> None:
    calls = []

    def fake_popen(argv, **kwargs):
        calls.append((argv, kwargs))
        return object()

    monkeypatch.delattr(util.sys, "frozen", raising=False)
    monkeypatch.setattr(util.sys, "executable", r"C:\Program Files\Python 3\python.exe")
    monkeypatch.setattr(util, "get_app_root", lambda: r"C:\Hunter X & QA")
    monkeypatch.setattr(util.subprocess, "Popen", fake_popen)

    util.launch_maxbot(
        filename=r"C:\Profiles\BIG BANG & VIP.json",
        homepage="https://example.test/event?a=1&b=2",
        instance="VIP A",
    )

    argv, kwargs = calls[0]
    assert argv[0] == r"C:\Program Files\Python 3\python.exe"
    assert argv[1] == "nodriver_tixcraft.py"
    assert r"--input=C:\Profiles\BIG BANG & VIP.json" in argv
    assert "--homepage=https://example.test/event?a=1&b=2" in argv
    assert "--instance=VIP A" in argv
    assert kwargs == {"cwd": r"C:\Hunter X & QA", "shell": False}
