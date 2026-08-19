from __future__ import annotations

import asyncio

from platform_registry import platform_key_for_url


def test_all_reported_platform_families_dispatch() -> None:
    cases = {
        "https://tixcraft.com/activity/detail/demo": "tixcraft",
        "https://example.kktix.cc/events/demo": "kktix",
        "https://www.ticketmaster.com/event/demo": "tixcraft",
        "https://www.ticketmaster.co.uk/event/demo": "tixcraft",
        "https://www.ticketmaster.de/event/demo": "tixcraft",
        "https://ticketplus.com.tw/activity/demo": "ticketplus",
    }
    assert {url: platform_key_for_url(url) for url in cases} == cases


def test_ticketmaster_lookalike_is_not_dispatched() -> None:
    assert platform_key_for_url("https://ticketmaster.com.attacker.invalid/event/demo") is None
    assert platform_key_for_url("https://not-ticketmaster.de/event/demo") is None


def test_navigation_race_prefers_advanced_cdp_target(monkeypatch) -> None:
    import nodriver_common

    old_url = "https://tixcraft.com/activity/game/demo"
    new_url = "https://tixcraft.com/ticket/area/demo/1"

    class Tab:
        target = type("Target", (), {"url": new_url})()

        async def js_dumps(self, _script):
            # Old execution contexts can still answer briefly after TargetInfo
            # has advanced to the next document.
            await asyncio.sleep(0)
            return old_url

    resolved, should_quit = asyncio.run(nodriver_common.nodriver_current_url(Tab(), {}))
    assert resolved == new_url
    assert should_quit is False

