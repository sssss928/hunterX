from __future__ import annotations

from pathlib import Path

import pytest

import browser_session
import util
import platforms.tixcraft as tixcraft_platform
from browser_session import BrowserSessionManager
from flow_state import FlowState, FlowStateMachine
from leak_watch import LEAK_WATCH_POLICIES, is_protected_url, is_safe_page
from nodriver_common import build_shared_notification_context
from notification_context import clean_event_name, clean_seat_area, format_seat_rows, make_notification_context
from page_classifier import PageClass, classify_page
from platforms.common_async import get_auto_reload_interval
from platforms.tixcraft import (
    _classify_recovery_page,
    _detect_tixcraft_soft_block,
    _get_tixcraft_soft_block_recovery_url,
    _handle_tixcraft_soft_block,
    _is_retryable_alert,
    _is_tixcraft_soft_block_text,
    _reset_tixcraft_area_retry_state,
    _resolve_soft_block_wait_seconds,
    _state as tixcraft_state,
)
from reload_guard import ReloadGuard
from runtime_control import RuntimeCommand, RuntimeController, RuntimeEvent
from run_modes import RunMode, get_run_mode, is_leak_watch_mode
from submit_guard import SubmitGuard


class _Target:
    url = "https://tixcraft.com/ticket/ticket/abc/1/1"


class _Tab:
    target = _Target()

    def __init__(self) -> None:
        self.reload_count = 0

    async def reload(self) -> None:
        self.reload_count += 1


class _TextTab:
    def __init__(self, text: str) -> None:
        self.text = text

    async def evaluate(self, _script: str) -> str:
        return self.text


class _SoftBlockTab:
    def __init__(self, text: str) -> None:
        self.text = text
        self.get_calls: list[str] = []
        self.reload_count = 0

    async def evaluate(self, _script: str) -> str:
        return self.text

    async def get(self, url: str) -> None:
        self.get_calls.append(url)

    async def reload(self) -> None:
        self.reload_count += 1


def test_runtime_controller_only_user_quit_closes_browser() -> None:
    controller = RuntimeController()
    assert controller.decide(RuntimeEvent.USER_QUIT) == RuntimeCommand.QUIT_BROWSER
    assert controller.decide(RuntimeEvent.USER_STOP) == RuntimeCommand.STOP_AUTOMATION_KEEP_BROWSER
    assert controller.decide(page_class=PageClass.ORDER) == RuntimeCommand.CONTINUE
    assert controller.decide(page_class=PageClass.CHECKOUT) == RuntimeCommand.CONTINUE
    assert controller.decide(RuntimeEvent.CANCELED_ORDER) == RuntimeCommand.RECOVER_TO_AREA


def test_flow_state_machine_has_no_lifecycle_call() -> None:
    machine = FlowStateMachine()
    assert machine.transition(FlowState.AREA_SCANNING) == FlowState.AREA_SCANNING
    source = Path("src/flow_state.py").read_text(encoding="utf-8")
    assert "driver.stop" not in source
    assert ".reload(" not in source


@pytest.mark.asyncio
async def test_reload_guard_blocks_ticket_page_reload() -> None:
    tab = _Tab()
    guard = ReloadGuard()
    assert await guard.reload(tab, reason="ticket_hot_path") is False
    assert tab.reload_count == 0
    assert await guard.reload(tab, reason="recovery", recovery=True) is True
    assert tab.reload_count == 1


def test_page_classifier_core_tixcraft_pages() -> None:
    assert classify_page("https://tixcraft.com/ticket/area/abc/1") == PageClass.AREA
    assert classify_page("https://tixcraft.com/ticket/ticket/abc/1/1") == PageClass.TICKET
    assert classify_page("https://tixcraft.com/ticket/order") == PageClass.ORDER
    assert classify_page("https://tixcraft.com/ticket/checkout") == PageClass.CHECKOUT
    assert classify_page("https://tixcraft.com/activity/game/abc") == PageClass.DATE


@pytest.mark.asyncio
async def test_tixcraft_recovery_text_classifier_detects_manual_cancel_and_continue() -> None:
    assert await _classify_recovery_page(
        _TextTab("訂單已取消，請繼續選購"),
        "https://tixcraft.com/ticket/order",
        PageClass.ORDER,
    ) == PageClass.CANCELED_ORDER
    assert await _classify_recovery_page(
        _TextTab("continue shopping"),
        "https://tixcraft.com/activity/game/abc",
        PageClass.DATE,
    ) == PageClass.CONTINUE_SHOPPING


@pytest.mark.asyncio
async def test_tixcraft_ticket_page_rejected_text_recovers_to_area() -> None:
    assert await _classify_recovery_page(
        _TextTab("很抱歉，剩餘票券不足，請返回重新選擇"),
        "https://tixcraft.com/ticket/ticket/abc/1/1",
        PageClass.TICKET,
    ) == PageClass.REJECTED_ERROR


def test_tixcraft_retryable_alert_dictionary_covers_common_dialogs() -> None:
    assert _is_retryable_alert("E0024：請重新選擇")
    assert _is_retryable_alert("很抱歉，剩餘票券不足，請返回重新選擇")
    assert _is_retryable_alert("票券已被選購一空")
    assert _is_retryable_alert("not enough tickets")
    assert not _is_retryable_alert("付款資料尚未填寫完成")


@pytest.mark.parametrize(
    "text",
    [
        "Your Browsing Activity Has Been Paused",
        "We've detected unusual behavior on either your network or your browser.",
        "detected unusual behavior",
        "偵測到異常行為，請稍後再試",
        "瀏覽活動已暫停",
    ],
)
def test_tixcraft_soft_block_text_markers(text: str) -> None:
    assert _is_tixcraft_soft_block_text(text)


@pytest.mark.asyncio
async def test_tixcraft_soft_block_text_page_is_detected_on_area_url() -> None:
    tab = _SoftBlockTab(
        "Your Browsing Activity Has Been Paused\n"
        "We've detected unusual behavior on either your network or your browser."
    )

    detection = await _detect_tixcraft_soft_block(tab, "https://tixcraft.com/ticket/area/abc/1", {})

    assert detection["blocked"] is True
    assert detection["kind"] == "text_marker"


def test_tixcraft_soft_block_delay_uses_custom_or_default(monkeypatch) -> None:
    config = {"advanced": {"tixcraft_soft_block_delay": "240"}}
    assert _resolve_soft_block_wait_seconds(config, "https://tixcraft.com/ticket/area/abc/1") == (240, True)

    monkeypatch.setattr(tixcraft_platform.random, "randint", lambda start, end: 333)
    invalid = {"advanced": {"tixcraft_soft_block_delay": "bad"}}
    assert _resolve_soft_block_wait_seconds(invalid, "https://tixcraft.com/ticket/area/abc/1") == (333, False)


def test_tixcraft_soft_block_recovery_url_priority() -> None:
    tixcraft_state.clear()
    tixcraft_state["last_valid_area_url"] = "https://tixcraft.com/ticket/area/last/1"

    assert _get_tixcraft_soft_block_recovery_url(
        {"homepage": "https://tixcraft.com/activity/game/home"},
        "https://tixcraft.com/ticket/area/current/1",
    ) == "https://tixcraft.com/ticket/area/last/1"

    tixcraft_state.clear()
    assert _get_tixcraft_soft_block_recovery_url(
        {"homepage": "https://tixcraft.com/activity/game/home"},
        "https://tixcraft.com/ticket/area/current/1",
    ) == "https://tixcraft.com/ticket/area/current/1"

    assert _get_tixcraft_soft_block_recovery_url({"homepage": "https://tixcraft.com/activity/game/home"}) == (
        "https://tixcraft.com/activity/game/home"
    )


@pytest.mark.asyncio
async def test_tixcraft_soft_block_handler_waits_without_reload_and_resets_state(monkeypatch) -> None:
    tixcraft_state.clear()
    tixcraft_state.update(
        {
            "last_valid_area_url": "https://tixcraft.com/ticket/area/last/1",
            "captcha_submit_until": 99,
            "ocr_completed_url": "https://tixcraft.com/ticket/ticket/abc",
            "captcha_alert_detected": True,
            "manual_intervention_required": True,
            "ticket_assigned_abc": True,
            "tixcraft_area_reload_next_at": 99,
            "tixcraft_area_reload_url": "https://tixcraft.com/ticket/area/old/1",
        }
    )
    tab = _SoftBlockTab("Your Browsing Activity Has Been Paused")
    sleeps: list[int] = []

    async def false_check(_config=None):  # noqa: ANN001
        return False

    async def fake_sleep(seconds: int) -> None:
        sleeps.append(seconds)

    async def forbidden_reload(*_args, **_kwargs):  # noqa: ANN002, ANN003
        raise AssertionError("soft-block handler must not reload")

    monkeypatch.setattr(tixcraft_platform, "check_and_handle_pause", false_check)
    monkeypatch.setattr(tixcraft_platform, "check_and_handle_quit", false_check)
    monkeypatch.setattr(tixcraft_platform.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(tixcraft_platform, "guarded_reload", forbidden_reload)
    monkeypatch.setattr(tixcraft_platform, "_resolve_soft_block_wait_seconds", lambda *_args, **_kwargs: (1, True))

    assert await _handle_tixcraft_soft_block(
        tab,
        {"advanced": {"tixcraft_soft_block_delay": "240"}},
        "https://tixcraft.com/ticket/area/current/1",
        {"kind": "text_marker", "original_url": "", "client_ip": "unknown"},
    )

    assert sleeps == [1]
    assert tab.reload_count == 0
    assert tab.get_calls == ["https://tixcraft.com/ticket/area/last/1"]
    assert tixcraft_state["captcha_submit_until"] == 0
    assert tixcraft_state["ocr_completed_url"] == ""
    assert tixcraft_state["captcha_alert_detected"] is False
    assert tixcraft_state["manual_intervention_required"] is False
    assert tixcraft_state["ticket_assigned_abc"] is False
    assert tixcraft_state["tixcraft_area_reload_next_at"] == 0
    assert tixcraft_state["tixcraft_area_reload_url"] == ""


def test_tixcraft_area_retry_reset_clears_stale_reload_state() -> None:
    tixcraft_state.clear()
    tixcraft_state.update(
        {
            "area_retry_count": 9,
            "ticketmaster_phase": "done",
            "tixcraft_area_reload_next_at": 999,
            "tixcraft_area_reload_last_wait_log": 999,
            "tixcraft_date_reload_next_at": 999,
            "tixcraft_date_reload_last_wait_log": 999,
            "tixcraft_area_reload_url": "https://tixcraft.com/ticket/area/old",
            "tixcraft_date_reload_url": "https://tixcraft.com/activity/game/old",
        }
    )

    _reset_tixcraft_area_retry_state()

    assert tixcraft_state["area_retry_count"] == 0
    assert tixcraft_state["ticketmaster_phase"] == "area_select"
    assert tixcraft_state["tixcraft_area_reload_next_at"] == 0
    assert tixcraft_state["tixcraft_area_reload_url"] == ""


def test_submit_guard_prevents_duplicate_pending_submit() -> None:
    guard = SubmitGuard()
    assert guard.can_submit("https://tixcraft.com/ticket/ticket/a")
    guard.mark_submitted("https://tixcraft.com/ticket/ticket/a", pending_seconds=10)
    assert not guard.can_submit("https://tixcraft.com/ticket/ticket/a")
    assert guard.can_submit("https://tixcraft.com/ticket/order")
    guard.reset()
    assert guard.can_submit("https://tixcraft.com/ticket/ticket/a")


def test_notification_default_template_and_redaction() -> None:
    ctx = make_notification_context(
        platform="TixCraft",
        stage="checkout_reached",
        event_name="選擇區域: My Event | tixcraft拓元售票",
        ticket_count=2,
        seat_area="特A區 (VIP PACKAGE) 剩餘 46",
        seat_rows=["7排17號", "7排18號"],
        current_url="https://tixcraft.com/?TIXUISID=abc",
    )
    message = ctx.format_message()
    assert message == (
        "🎫 [TixCraft]\n\n"
        "活動：My Event\n"
        "票數：2\n"
        "區域： 特A區 (VIP PACKAGE)\n"
        "排數：1️⃣7排17號\n"
        "2️⃣7排18號\n"
        "狀態：已進入結帳畫面，請立即付款！"
    )
    assert "搶票成功" not in message
    assert "購票流程通知" not in message
    assert "剩餘 46" not in message
    assert "TIXUISID=abc" not in ctx.normalized().current_url


def test_notification_custom_placeholders_and_fallbacks() -> None:
    ctx = make_notification_context(platform="", stage="", event_name="", ticket_count="", seat_area="")
    assert ctx.format_message("{platform}|{event_name}|{ticket_count}|{seat_area}|{seat_rows}|{stage}") == (
        "Unknown Platform|Unknown Event|-|Unknown Area|-|-"
    )
    assert clean_event_name("地區") == "Unknown Event"
    assert clean_seat_area("B區 熱賣中") == "B區"
    assert format_seat_rows("訂單建立中﹍") == "訂單建立中﹍"


def test_notification_order_pending_area_and_rows_are_separate() -> None:
    ctx = make_notification_context(
        platform="TixCraft",
        stage="order_pending",
        event_name="WESTLIFE 25：THE ANNIVERSARY WORLD TOUR",
        ticket_count=4,
        seat_area="特A區 (VIP PACKAGE)",
        seat_rows="訂單建立中﹍",
    )

    assert ctx.format_message() == (
        "🎫 [TixCraft]\n\n"
        "活動：WESTLIFE 25：THE ANNIVERSARY WORLD TOUR\n"
        "票數：4\n"
        "區域： 特A區 (VIP PACKAGE)\n"
        "排數：訂單建立中﹍\n"
        "狀態：訂單建立中﹍"
    )


def test_run_mode_intervals_keep_onsale_hot_path_independent() -> None:
    config = {
        "advanced": {
            "run_mode": RunMode.ONSALE.value,
            "auto_reload_page_interval": 7,
            "leak_refresh_interval_seconds": 1,
        }
    }

    assert get_run_mode(config) == RunMode.ONSALE.value
    assert not is_leak_watch_mode(config)
    assert get_auto_reload_interval(config) == 7

    config["advanced"]["run_mode"] = RunMode.LEAK_WATCH.value

    assert is_leak_watch_mode(config)
    assert get_auto_reload_interval(config) == 1


def test_leak_watch_registry_covers_all_supported_platform_families() -> None:
    platforms = {policy.platform for policy in LEAK_WATCH_POLICIES}
    assert {
        "TixCraft",
        "KKTIX",
        "TicketPlus",
        "iBon",
        "KHAM",
        "ticket.com.tw",
        "udnfunlife",
        "Cityline",
        "FunOne",
        "HKTicketing",
        "FamiTicket",
        "FANSI GO",
    }.issubset(platforms)
    assert is_safe_page("https://tixcraft.com/ticket/area/abc/1")
    assert not is_safe_page("https://tixcraft.com/ticket/ticket/abc/1/1")
    assert is_protected_url("https://tixcraft.com/ticket/checkout")


def test_shared_notification_context_uses_config_metadata_for_legacy_platform_calls() -> None:
    config = {
        "homepage": "https://ticketplus.com.tw/activity/example_event",
        "ticket_number": 4,
        "area_auto_select": {"area_keyword": "特A區 (VIP PACKAGE)"},
        "advanced": {},
    }

    ctx = build_shared_notification_context(config, "order", "TicketPlus")

    assert "Unknown Event" not in ctx.format_message()
    assert "特A區 (VIP PACKAGE)" in ctx.format_message()
    assert "票數：4" in ctx.format_message()


@pytest.mark.parametrize(
    "platform",
    [
        "TixCraft",
        "KKTIX",
        "TicketPlus",
        "iBon",
        "KHAM",
        "Cityline",
        "FunOne",
        "HKTicketing",
        "FamiTicket",
        "FANSI GO",
    ],
)
def test_shared_notification_metadata_for_platforms(platform: str) -> None:
    ctx = make_notification_context(platform=platform, stage="order_pending", event_name="Event", ticket_count=1)
    assert f"[{platform}]" in ctx.format_message()
    assert "訂單建立中" in ctx.format_message()


def test_browser_args_for_chrome_edge_and_private_mode(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(browser_session.util, "get_app_root", lambda: str(tmp_path))
    config = {"advanced": {"browser_type": "chrome", "browser_private_mode": False, "headless": False}}
    manager = BrowserSessionManager(config)
    args = manager.build_args(["--no-first-run"])
    assert "--no-first-run" in args
    assert any(item.startswith("--user-data-dir=") for item in args)
    assert manager.profile_parent_dir().exists()

    private = BrowserSessionManager({"advanced": {"browser_type": "edge", "browser_private_mode": True}})
    private_args = private.build_args([])
    assert "--inprivate" in private_args
    assert not any(item.startswith("--user-data-dir=") for item in private_args)


def test_frozen_launcher_resolves_flat_and_split_layouts(tmp_path) -> None:
    flat_root = tmp_path / "hunterX"
    flat_root.mkdir()
    flat_exe = flat_root / "nodriver_tixcraft.exe"
    flat_exe.write_text("", encoding="utf-8")

    executable, cwd = util.resolve_frozen_executable("nodriver_tixcraft", str(flat_root))

    assert executable == str(flat_exe)
    assert cwd == str(flat_root)

    settings_root = tmp_path / "settings"
    bot_root = tmp_path / "nodriver_tixcraft"
    settings_root.mkdir()
    bot_root.mkdir()
    split_exe = bot_root / "nodriver_tixcraft.exe"
    split_exe.write_text("", encoding="utf-8")

    executable, cwd = util.resolve_frozen_executable("nodriver_tixcraft", str(settings_root))

    assert executable == str(split_exe)
    assert cwd == str(bot_root)


def test_frozen_launch_maxbot_uses_resolved_executable(tmp_path, monkeypatch) -> None:
    settings_root = tmp_path / "settings"
    bot_root = tmp_path / "nodriver_tixcraft"
    settings_root.mkdir()
    bot_root.mkdir()
    split_exe = bot_root / "nodriver_tixcraft.exe"
    split_exe.write_text("", encoding="utf-8")
    captured = {}

    def fake_popen(args, cwd=None):  # noqa: ANN001
        captured["args"] = args
        captured["cwd"] = cwd
        return object()

    monkeypatch.setattr(util.sys, "frozen", True, raising=False)
    monkeypatch.setattr(util, "get_app_root", lambda: str(settings_root))
    monkeypatch.setattr(util.platform, "system", lambda: "Windows")
    monkeypatch.setattr(util.subprocess, "Popen", fake_popen)

    util.launch_maxbot("nodriver_tixcraft")

    assert captured["args"][0] == str(split_exe)
    assert captured["cwd"] == str(bot_root)


def test_direct_lifecycle_calls_are_centralized() -> None:
    src_root = Path("src")
    reload_violations = []
    stop_violations = []
    for path in src_root.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        if ".reload(" in text and path.name != "reload_guard.py":
            reload_violations.append(str(path))
        if "driver.stop" in text and path.name != "browser_session.py":
            stop_violations.append(str(path))
    assert reload_violations == []
    assert stop_violations == []


def test_no_operation_modes_reintroduced() -> None:
    forbidden = ("operation_mode", "release_watch")
    for path in [*Path("src").rglob("*"), Path("README.md")]:
        if path.is_file() and path.suffix in {".py", ".js", ".html", ".md"}:
            text = path.read_text(encoding="utf-8", errors="ignore")
            assert not any(item in text for item in forbidden), str(path)


@pytest.mark.parametrize(
    "platform_file",
    [
        "cityline.py",
        "famiticket.py",
        "fansigo.py",
        "funone.py",
        "hkticketing.py",
        "ibon.py",
        "kham.py",
        "kktix.py",
        "ticketplus.py",
        "tixcraft.py",
    ],
)
def test_platforms_use_shared_discord_notification(platform_file: str) -> None:
    source = Path("src/platforms", platform_file).read_text(encoding="utf-8")
    assert "send_discord_notification(" in source
