import ast
import asyncio
import importlib
from pathlib import Path
import sys

import pytest

import settings
from platform_contract import PlatformStateProxy
from platform_engine import PlatformEngine


_REAL_PLATFORM = sys.platform
try:
    sys.platform = "test"
    import nodriver_tixcraft as runtime
finally:
    sys.platform = _REAL_PLATFORM


PLATFORM_CASES = (
    ("cityline", "https://www.cityline.com/Events.html"),
    ("famiticket", "https://www.famiticket.com.tw/Home/Activity/Info"),
    ("fansigo", "https://go.fansi.me/events/123"),
    ("funone", "https://tickets.funone.io/activity/123"),
    ("hkticketing", "https://premier.hkticketing.com/shows/show.aspx"),
    ("ibon", "https://ticket.ibon.com.tw/ActivityInfo/Details/123"),
    ("kham", "https://kham.com.tw/application/UTK02/UTK0201_.aspx"),
    ("kktix", "https://example.kktix.cc/events/demo"),
    ("ticketplus", "https://ticketplus.com.tw/activity/demo"),
)


class _Tab:
    pass


@pytest.mark.parametrize(("platform_key", "url"), PLATFORM_CASES)
def test_platform_module_state_is_owned_by_platform_engine(platform_key, url):
    engine = PlatformEngine()
    module = importlib.import_module(f"platforms.{platform_key}")
    tab = _Tab()

    decision = engine.before_dispatch(tab, url, {})

    assert decision.adapter is not None
    assert decision.adapter.key == platform_key
    assert isinstance(module._state, PlatformStateProxy)
    module._state["owner"] = id(tab)
    assert engine.state_for(tab, decision.adapter).platform_data["owner"] == id(tab)


@pytest.mark.asyncio
@pytest.mark.parametrize(("platform_key", "url"), PLATFORM_CASES)
async def test_platform_state_is_isolated_between_concurrent_tabs(platform_key, url):
    engine = PlatformEngine()
    module = importlib.import_module(f"platforms.{platform_key}")
    tabs = (_Tab(), _Tab())
    ready = (asyncio.Event(), asyncio.Event())

    async def run(index):
        decision = engine.before_dispatch(tabs[index], url, {})
        assert decision.adapter is not None
        module._state.clear()
        module._state["tab_marker"] = index
        ready[index].set()
        await ready[1 - index].wait()
        await asyncio.sleep(0)
        return module._state["tab_marker"]

    assert await asyncio.gather(run(0), run(1)) == [0, 1]


def test_platform_modules_do_not_bypass_guarded_navigation():
    source_root = Path(__file__).resolve().parents[1] / "src"
    platform_dir = source_root / "platforms"
    violations = []

    source_paths = [*sorted(platform_dir.glob("*.py")), source_root / "nodriver_tixcraft.py"]
    for source_path in source_paths:
        tree = ast.parse(source_path.read_text(encoding="utf-8"), filename=str(source_path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Await) or not isinstance(node.value, ast.Call):
                continue
            function = node.value.func
            if isinstance(function, ast.Attribute) and function.attr in {"get", "reload"}:
                violations.append(f"{source_path.name}:{node.lineno}:{function.attr}")

    assert violations == []


def test_platform_wait_retry_and_throttle_clocks_are_monotonic():
    source_root = Path(__file__).resolve().parents[1] / "src"
    source_paths = [
        *sorted((source_root / "platforms").glob("*.py")),
        source_root / "nodriver_tixcraft.py",
    ]
    violations = []

    for source_path in source_paths:
        tree = ast.parse(source_path.read_text(encoding="utf-8"), filename=str(source_path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            owner = node.func.value
            if (
                node.func.attr == "time"
                and isinstance(owner, ast.Name)
                and owner.id in {"time", "_time"}
            ):
                violations.append(f"{source_path.name}:{node.lineno}:time.time")

    assert violations == []


def test_instance_stop_timeout_uses_monotonic_clock():
    source = (Path(__file__).resolve().parents[1] / "src" / "settings.py").read_text(
        encoding="utf-8"
    )

    function_source = source.split("def wait_for_instances_to_stop", 1)[1].split(
        "\ndef ", 1
    )[0]
    assert "time.monotonic()" in function_source
    assert "time.time()" not in function_source


def test_leaving_family_clears_stale_platform_state():
    engine = PlatformEngine()
    tab = _Tab()
    kktix = importlib.import_module("platforms.kktix")

    first = engine.before_dispatch(tab, "https://example.kktix.cc/events/demo", {})
    assert first.adapter is not None
    kktix._state["event_marker"] = "old"

    second = engine.before_dispatch(tab, "https://ticketplus.com.tw/activity/new", {})

    assert second.adapter is not None
    assert second.adapter.key == "ticketplus"
    assert engine.state_for(tab, first.adapter).platform_data == {}


def test_protected_to_safe_transition_starts_clean_attempt_state():
    engine = PlatformEngine()
    tab = _Tab()
    kktix = importlib.import_module("platforms.kktix")

    protected = engine.before_dispatch(
        tab,
        "https://example.kktix.cc/orders/demo",
        {},
    )
    assert protected.adapter is not None
    kktix._state["got_ticket_detected"] = True

    safe = engine.before_dispatch(
        tab,
        "https://example.kktix.cc/events/new/registrations/new",
        {},
    )

    assert safe.adapter is protected.adapter
    assert kktix._state.get("got_ticket_detected") is None
    assert engine.state_for(tab, safe.adapter).recovery_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("homepage", "expected_calls"),
    [
        (
            "https://kktix.com.attacker.invalid/events/demo",
            ["https://kktix.com.attacker.invalid/events/demo"],
        ),
        (
            "https://example.kktix.cc/events/demo",
            [
                "https://example.kktix.cc/events/demo",
                "https://kktix.com/users/sign_in?back_to=https://example.kktix.cc/events/demo",
            ],
        ),
    ],
)
async def test_startup_navigation_uses_registry_hostname_ownership(
    monkeypatch: pytest.MonkeyPatch,
    homepage: str,
    expected_calls: list[str],
) -> None:
    class _Driver:
        def __init__(self):
            self.calls: list[str] = []

        async def get(self, url):
            self.calls.append(url)
            return _Tab()

    async def no_sleep(_seconds):
        return None

    config = settings.get_default_config()
    config["homepage"] = homepage
    config["accounts"]["kktix_account"] = "member@example.invalid"
    driver = _Driver()
    monkeypatch.setattr(runtime.asyncio, "sleep", no_sleep)

    await runtime.nodriver_goto_homepage(driver, config)

    assert driver.calls == expected_calls


@pytest.mark.asyncio
async def test_registered_alert_callback_cannot_mutate_another_family_tab() -> None:
    ibon = importlib.import_module("platforms.ibon")

    class _AlertTab:
        def __init__(self):
            self.target = type("Target", (), {"url": "https://ticket.ibon.com.tw/"})()
            self.callback = None
            self.dismissals = 0

        def add_handler(self, _event, callback):
            self.callback = callback

        async def send(self, _command):
            self.dismissals += 1

    tab = _AlertTab()
    state = ibon.platform_state_for_tab(tab, "ibon")
    state.clear()
    await ibon.register_ibon_alert_handler(tab, {})
    assert tab.callback is not None

    tab.target.url = "https://example.kktix.cc/events/demo"
    await tab.callback(type("Alert", (), {"message": "已售完"})())

    assert tab.dismissals == 0
    assert state["alert_handler_registered"] is True
