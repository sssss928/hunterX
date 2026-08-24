from __future__ import annotations

import ast
from pathlib import Path

import pytest

import nodriver_tixcraft
import settings
from scripts.audit_browser_exception_handlers import REPO_ROOT, audit_source


SOURCE_PATH = Path(REPO_ROOT, "src", "nodriver_tixcraft.py")


def _handler_index() -> dict[int, ast.ExceptHandler]:
    tree = ast.parse(SOURCE_PATH.read_text(encoding="utf-8"), filename=str(SOURCE_PATH))
    return {
        handler.lineno: handler
        for node in ast.walk(tree)
        if isinstance(node, ast.Try)
        for handler in node.handlers
    }


def _is_first_action_terminal_guard(handler: ast.ExceptHandler) -> bool:
    if not handler.name or not handler.body:
        return False
    statement = handler.body[0]
    if not isinstance(statement, ast.Expr) or not isinstance(statement.value, ast.Call):
        return False
    call = statement.value
    function = call.func
    return bool(
        isinstance(function, ast.Attribute)
        and isinstance(function.value, ast.Name)
        and function.value.id == "runtime_health"
        and function.attr == "raise_if_terminal_browser_error"
        and len(call.args) == 1
        and isinstance(call.args[0], ast.Name)
        and call.args[0].id == handler.name
    )


def _returns_literal_success(handler: ast.ExceptHandler) -> bool:
    module = ast.Module(body=handler.body, type_ignores=[])
    for node in ast.walk(module):
        if not isinstance(node, ast.Return) or node.value is None:
            continue
        if isinstance(node.value, ast.Constant) and node.value.value is True:
            return True
        if not isinstance(node.value, ast.Dict):
            continue
        for key, value in zip(node.value.keys, node.value.values):
            if (
                isinstance(key, ast.Constant)
                and str(key.value).casefold()
                in {"success", "submitted", "completed"}
                and isinstance(value, ast.Constant)
                and value.value is True
            ):
                return True
    return False


def test_nodriver_tixcraft_browser_handlers_are_fully_classified_first() -> None:
    findings = audit_source(SOURCE_PATH, repo_root=REPO_ROOT)
    assert len(findings) == 6
    assert all(finding.disposition == "terminal_classifier" for finding in findings)

    handlers = _handler_index()
    assert all(
        _is_first_action_terminal_guard(handlers[finding.handler_line])
        for finding in findings
    )


def test_nodriver_tixcraft_browser_fallbacks_never_return_literal_success() -> None:
    findings = audit_source(SOURCE_PATH, repo_root=REPO_ROOT)
    handlers = _handler_index()
    assert [
        (finding.function, finding.handler_line)
        for finding in findings
        if _returns_literal_success(handlers[finding.handler_line])
    ] == []


class _Cookies:
    def __init__(self) -> None:
        self.set_all_calls = []

    async def get_all(self):
        return []

    async def set_all(self, cookies) -> None:
        self.set_all_calls.append(cookies)


class _Driver:
    def __init__(self) -> None:
        self.cookies = _Cookies()


class _SendTab:
    target = type(
        "Target",
        (),
        {"url": "https://tixcraft.com/activity/detail/audit", "target_id": "audit-tab"},
    )()

    def __init__(self, error: Exception, *, fail_at: int = 1) -> None:
        self.error = error
        self.fail_at = fail_at
        self.send_count = 0

    async def send(self, _command):
        self.send_count += 1
        if self.send_count == self.fail_at:
            raise self.error
        return True


async def _immediate_sleep(_seconds: float) -> None:
    return None


def _patch_homepage_navigation(monkeypatch: pytest.MonkeyPatch, tab: _SendTab) -> None:
    async def _get(*_args, **_kwargs):
        return tab

    monkeypatch.setattr(nodriver_tixcraft.runtime_health, "guarded_driver_get", _get)
    monkeypatch.setattr(nodriver_tixcraft.asyncio, "sleep", _immediate_sleep)


@pytest.mark.asyncio
async def test_tixcraft_cookie_delete_terminal_error_reaches_browser_owner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tab = _SendTab(ConnectionError("WebSocket connection closed"), fail_at=1)
    driver = _Driver()
    config = settings.get_default_config()
    config["homepage"] = "https://tixcraft.com/activity/detail/audit"
    config["accounts"]["tixcraft_sid"] = "owned-session-cookie"
    _patch_homepage_navigation(monkeypatch, tab)

    with pytest.raises(ConnectionError, match="WebSocket connection closed"):
        await nodriver_tixcraft.nodriver_goto_homepage(driver, config)


@pytest.mark.asyncio
async def test_tixcraft_cookie_set_terminal_error_reaches_browser_owner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tab = _SendTab(ConnectionError("target closed"), fail_at=3)
    driver = _Driver()
    config = settings.get_default_config()
    config["homepage"] = "https://tixcraft.com/activity/detail/audit"
    config["accounts"]["tixcraft_sid"] = "owned-session-cookie"
    _patch_homepage_navigation(monkeypatch, tab)

    with pytest.raises(ConnectionError, match="target closed"):
        await nodriver_tixcraft.nodriver_goto_homepage(driver, config)


@pytest.mark.asyncio
async def test_tixcraft_cookie_set_programming_error_keeps_existing_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tab = _SendTab(ValueError("ordinary cookie encoding error"), fail_at=3)
    driver = _Driver()
    config = settings.get_default_config()
    config["homepage"] = "https://tixcraft.com/activity/detail/audit"
    config["accounts"]["tixcraft_sid"] = "owned-session-cookie"
    _patch_homepage_navigation(monkeypatch, tab)

    result = await nodriver_tixcraft.nodriver_goto_homepage(driver, config)

    assert result is tab
    assert len(driver.cookies.set_all_calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("error", "raises_terminal"),
    [
        (ConnectionError("WebSocket connection closed"), True),
        (ValueError("ordinary reload rejection"), False),
    ],
)
async def test_ibon_reload_handler_distinguishes_terminal_from_local_fallback(
    monkeypatch: pytest.MonkeyPatch,
    error: Exception,
    raises_terminal: bool,
) -> None:
    tab = _SendTab(ValueError("unused"))
    driver = _Driver()
    config = settings.get_default_config()
    config["homepage"] = "https://ticket.ibon.com.tw/ActivityInfo/Details/audit"
    _patch_homepage_navigation(monkeypatch, tab)

    async def _nothing(*_args, **_kwargs):
        return None

    async def _login(*_args, **_kwargs):
        return {"success": True}

    async def _reload(*_args, **_kwargs):
        raise error

    monkeypatch.setattr(nodriver_tixcraft, "register_ibon_alert_handler", _nothing)
    monkeypatch.setattr(nodriver_tixcraft, "dismiss_pending_ibon_dialog", _nothing)
    monkeypatch.setattr(nodriver_tixcraft, "nodriver_ibon_login", _login)
    monkeypatch.setattr(nodriver_tixcraft, "guarded_reload", _reload)

    if raises_terminal:
        with pytest.raises(ConnectionError, match="WebSocket connection closed"):
            await nodriver_tixcraft.nodriver_goto_homepage(driver, config)
    else:
        assert await nodriver_tixcraft.nodriver_goto_homepage(driver, config) is tab


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("error", "raises_terminal"),
    [
        (ConnectionError("target closed"), True),
        (ValueError("ordinary cookie rejection"), False),
    ],
)
async def test_funone_cookie_handler_distinguishes_terminal_from_local_fallback(
    monkeypatch: pytest.MonkeyPatch,
    error: Exception,
    raises_terminal: bool,
) -> None:
    tab = _SendTab(error)
    driver = _Driver()
    config = settings.get_default_config()
    config["homepage"] = "https://tickets.funone.io/events/audit"
    config["accounts"]["funone_session_cookie"] = "owned-session-cookie"
    _patch_homepage_navigation(monkeypatch, tab)

    if raises_terminal:
        with pytest.raises(ConnectionError, match="target closed"):
            await nodriver_tixcraft.nodriver_goto_homepage(driver, config)
    else:
        assert await nodriver_tixcraft.nodriver_goto_homepage(driver, config) is tab


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "helper_name",
    ["nodrver_block_urls", "_inject_clarity_stub_for_ticketplus"],
)
async def test_direct_bootstrap_helpers_propagate_terminal_errors(
    helper_name: str,
) -> None:
    tab = _SendTab(ConnectionError("WebSocket connection closed"))
    helper = getattr(nodriver_tixcraft, helper_name)
    args = (
        (tab, settings.get_default_config())
        if helper_name == "nodrver_block_urls"
        else (tab,)
    )
    with pytest.raises(ConnectionError, match="WebSocket connection closed"):
        await helper(*args)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "helper_name",
    ["nodrver_block_urls", "_inject_clarity_stub_for_ticketplus"],
)
async def test_direct_bootstrap_helpers_keep_ordinary_fallbacks(
    helper_name: str,
) -> None:
    tab = _SendTab(ValueError("ordinary unsupported CDP command"))
    helper = getattr(nodriver_tixcraft, helper_name)
    args = (
        (tab, settings.get_default_config())
        if helper_name == "nodrver_block_urls"
        else (tab,)
    )

    result = await helper(*args)

    if helper_name == "nodrver_block_urls":
        assert result is tab
    else:
        assert result is None
