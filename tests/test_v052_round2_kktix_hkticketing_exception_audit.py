from __future__ import annotations

import ast
from pathlib import Path

import pytest

import settings
from platforms import hkticketing, kktix
from scripts.audit_browser_exception_handlers import REPO_ROOT, audit_source


PLATFORM_PATHS = (
    Path(REPO_ROOT, "src", "platforms", "kktix.py"),
    Path(REPO_ROOT, "src", "platforms", "hkticketing.py"),
)
EXPECTED_FINDING_COUNTS = {
    "kktix.py": 44,
    "hkticketing.py": 67,
}


def _handler_index(path: Path) -> dict[int, ast.ExceptHandler]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    return {
        handler.lineno: handler
        for node in ast.walk(tree)
        if isinstance(node, ast.Try)
        for handler in node.handlers
    }


def _is_terminal_guard(statement: ast.stmt, exception_name: str) -> bool:
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
        and call.args[0].id == exception_name
    )


def _handler_returns_literal_success(handler: ast.ExceptHandler) -> bool:
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


@pytest.mark.parametrize("path", PLATFORM_PATHS, ids=lambda path: path.stem)
def test_browser_fallbacks_escalate_terminal_errors_as_first_action(
    path: Path,
) -> None:
    findings = audit_source(path, repo_root=REPO_ROOT)
    assert len(findings) == EXPECTED_FINDING_COUNTS[path.name]
    assert all(finding.disposition == "terminal_classifier" for finding in findings)

    handlers = _handler_index(path)
    for finding in findings:
        handler = handlers[finding.handler_line]
        assert handler.name, (finding.function, finding.handler_line)
        assert handler.body, (finding.function, finding.handler_line)
        assert _is_terminal_guard(handler.body[0], handler.name), (
            finding.function,
            finding.handler_line,
        )


@pytest.mark.parametrize("path", PLATFORM_PATHS, ids=lambda path: path.stem)
def test_browser_fallbacks_never_convert_errors_to_literal_success(path: Path) -> None:
    findings = audit_source(path, repo_root=REPO_ROOT)
    handlers = _handler_index(path)
    offenders = [
        (finding.function, finding.handler_line)
        for finding in findings
        if _handler_returns_literal_success(handlers[finding.handler_line])
    ]
    assert offenders == []


class _DeadBrowserTab:
    target = type(
        "Target",
        (),
        {"url": "https://kktix.com/events/terminal", "target_id": "terminal-tab"},
    )()

    async def evaluate(self, *_args, **_kwargs):
        raise ConnectionError("WebSocket connection closed")

    async def query_selector(self, *_args, **_kwargs):
        raise ConnectionError("WebSocket connection closed")


@pytest.mark.asyncio
async def test_kktix_confirm_terminal_error_is_not_converted_to_success() -> None:
    with pytest.raises(ConnectionError, match="WebSocket connection closed"):
        await kktix.nodriver_kktix_confirm_order_button(
            _DeadBrowserTab(),
            settings.get_default_config(),
        )


@pytest.mark.asyncio
async def test_hkticketing_confirm_terminal_error_is_not_converted_to_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def immediate_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr(hkticketing.asyncio, "sleep", immediate_sleep)
    with pytest.raises(ConnectionError, match="WebSocket connection closed"):
        await hkticketing.nodriver_hkticketing_type02_confirm_order(
            _DeadBrowserTab(),
            settings.get_default_config(),
        )


@pytest.mark.asyncio
async def test_recovery_terminal_errors_are_not_converted_to_false(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def terminal_browser_call(*_args, **_kwargs):
        raise ConnectionError("WebSocket connection closed")

    config = settings.get_default_config()
    config["advanced"]["auto_reload_page_interval"] = 1.0
    kktix._state.clear()
    monkeypatch.setattr(kktix, "guarded_reload", terminal_browser_call)
    with pytest.raises(ConnectionError, match="WebSocket connection closed"):
        await kktix._reload_page_when_due(
            _DeadBrowserTab(),
            config,
            "terminal_recovery",
            "[TEST]",
        )

    monkeypatch.setattr(hkticketing, "guarded_get", terminal_browser_call)
    with pytest.raises(ConnectionError, match="WebSocket connection closed"):
        await hkticketing.nodriver_hkticketing_url_redirect(
            _DeadBrowserTab(),
            "https://queue.hkticketing.com/hotshow.html",
            config,
        )
