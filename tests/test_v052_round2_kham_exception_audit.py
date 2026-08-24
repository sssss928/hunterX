from __future__ import annotations

import ast
from pathlib import Path

import pytest

import settings
from platforms import kham
from scripts.audit_browser_exception_handlers import REPO_ROOT, audit_source


KHAM_PATH = Path(REPO_ROOT, "src", "platforms", "kham.py")


def _handler_index() -> dict[int, ast.ExceptHandler]:
    tree = ast.parse(KHAM_PATH.read_text(encoding="utf-8"), filename=str(KHAM_PATH))
    return {
        handler.lineno: handler
        for node in ast.walk(tree)
        if isinstance(node, ast.Try)
        for handler in node.handlers
    }


def _is_first_action_terminal_guard(
    handler: ast.ExceptHandler,
) -> bool:
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


def test_kham_browser_fallbacks_escalate_terminal_errors_first() -> None:
    findings = audit_source(KHAM_PATH, repo_root=REPO_ROOT)
    assert len(findings) == 103
    assert all(finding.disposition == "terminal_classifier" for finding in findings)

    handlers = _handler_index()
    assert all(
        _is_first_action_terminal_guard(handlers[finding.handler_line])
        for finding in findings
    )


def test_kham_browser_fallbacks_never_return_literal_success() -> None:
    findings = audit_source(KHAM_PATH, repo_root=REPO_ROOT)
    handlers = _handler_index()
    offenders = [
        (finding.function, finding.handler_line)
        for finding in findings
        if _returns_literal_success(handlers[finding.handler_line])
    ]
    assert offenders == []


class _FailingTab:
    target = type(
        "Target",
        (),
        {"url": "https://kham.com.tw/application/terminal", "target_id": "kham-terminal"},
    )()

    def __init__(self, error: Exception) -> None:
        self.error = error

    async def evaluate(self, *_args, **_kwargs):
        raise self.error


@pytest.mark.asyncio
async def test_kham_submit_terminal_error_is_not_converted_to_success() -> None:
    tab = _FailingTab(ConnectionError("WebSocket connection closed"))
    with pytest.raises(ConnectionError, match="WebSocket connection closed"):
        await kham._kham_click_submit_button(tab, settings.get_default_config())


@pytest.mark.asyncio
async def test_kham_submit_programming_error_fails_closed() -> None:
    tab = _FailingTab(ValueError("invalid selector result"))
    assert not await kham._kham_click_submit_button(
        tab,
        settings.get_default_config(),
    )


@pytest.mark.asyncio
async def test_kham_recovery_terminal_error_is_not_converted_to_false(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def terminal_reload(*_args, **_kwargs):
        raise ConnectionError("WebSocket connection closed")

    config = settings.get_default_config()
    config["advanced"]["auto_reload_page_interval"] = 1.0
    kham._state.clear()
    monkeypatch.setattr(kham, "guarded_reload", terminal_reload)
    with pytest.raises(ConnectionError, match="WebSocket connection closed"):
        await kham._reload_page_when_due(
            _FailingTab(ConnectionError("unused")),
            config,
            "terminal_recovery",
            "[TEST]",
        )
