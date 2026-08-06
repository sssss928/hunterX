from __future__ import annotations

import asyncio
import json
from contextlib import suppress
from types import SimpleNamespace

import pytest
from zendriver.core.connection import Listener, ProtocolException, Transaction

import browser_session
from zendriver_hardening import (
    _ORIGINAL_CALL_ATTRIBUTE,
    install_zendriver_transaction_guard,
)


def _return_value_command():
    result = yield {"method": "Runtime.evaluate", "params": {}}
    return result["value"]


def _non_terminal_command():
    yield {"method": "Runtime.evaluate", "params": {}}
    yield {"method": "Runtime.evaluate", "params": {"expression": "next"}}


def _restore_original_call(monkeypatch) -> None:
    current_call = Transaction.__call__
    original_call = getattr(current_call, _ORIGINAL_CALL_ATTRIBUTE, current_call)
    monkeypatch.setattr(Transaction, "__call__", original_call)


@pytest.mark.asyncio
async def test_unpatched_zendriver_reproduces_cancelled_future_race(monkeypatch):
    _restore_original_call(monkeypatch)
    transaction = Transaction(_return_value_command())
    transaction.cancel()

    with pytest.raises(asyncio.InvalidStateError, match="invalid state"):
        transaction(result={"value": "loading"})


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "response",
    [
        {"result": {"value": "loading"}},
        {"error": {"code": -32000, "message": "execution context replaced"}},
    ],
)
async def test_guard_discards_late_result_and_error_responses(monkeypatch, response):
    _restore_original_call(monkeypatch)
    assert install_zendriver_transaction_guard(Transaction) is True

    transaction = Transaction(_return_value_command())
    transaction.id = 17
    transaction.cancel()

    assert transaction(**response) is None
    assert transaction.cancelled() is True


@pytest.mark.asyncio
async def test_guard_keeps_normal_pending_transaction_semantics(monkeypatch):
    _restore_original_call(monkeypatch)
    assert install_zendriver_transaction_guard(Transaction) is True

    transaction = Transaction(_return_value_command())
    transaction(result={"value": "complete"})

    assert transaction.done() is True
    assert transaction.result() == "complete"


@pytest.mark.asyncio
async def test_guard_does_not_hide_other_protocol_errors(monkeypatch):
    _restore_original_call(monkeypatch)
    assert install_zendriver_transaction_guard(Transaction) is True
    transaction = Transaction(_non_terminal_command())

    with pytest.raises(ProtocolException, match="could not parse the cdp response"):
        transaction(result={"value": "loading"})


def test_guard_installation_is_idempotent(monkeypatch):
    _restore_original_call(monkeypatch)

    assert install_zendriver_transaction_guard(Transaction) is True
    guarded_call = Transaction.__call__
    assert install_zendriver_transaction_guard(Transaction) is False
    assert Transaction.__call__ is guarded_call


@pytest.mark.asyncio
async def test_default_installation_guards_real_zendriver_transaction(monkeypatch):
    _restore_original_call(monkeypatch)

    assert install_zendriver_transaction_guard() is True
    transaction = Transaction(_return_value_command())
    transaction.cancel()

    assert transaction(result={"value": "loading"}) is None


def test_guard_only_suppresses_invalid_state_after_transaction_finishes():
    class SimulatedRaceTransaction:
        def __init__(self):
            self.finished = False

        def done(self):
            return self.finished

        def cancelled(self):
            return False

        def __call__(self, **_response):
            self.finished = True
            raise asyncio.InvalidStateError("simulated callback race")

    assert install_zendriver_transaction_guard(SimulatedRaceTransaction) is True
    assert SimulatedRaceTransaction()(result={"value": "loading"}) is None


def test_guard_propagates_invalid_state_while_transaction_is_pending():
    class LiveTransaction:
        def done(self):
            return False

        def cancelled(self):
            return False

        def __call__(self, **_response):
            raise asyncio.InvalidStateError("unrelated live transaction error")

    assert install_zendriver_transaction_guard(LiveTransaction) is True

    with pytest.raises(asyncio.InvalidStateError, match="unrelated live transaction error"):
        LiveTransaction()(result={"value": "loading"})


def test_browser_config_installs_guard_before_returning_config(monkeypatch, tmp_path):
    install_calls = []

    class FakeConfig:
        def __init__(self, **kwargs):
            self.kwargs = kwargs
            self.user_data_dir = ""

    monkeypatch.setattr(browser_session, "Config", FakeConfig)
    monkeypatch.setattr(
        browser_session,
        "install_zendriver_transaction_guard",
        lambda: install_calls.append("installed"),
    )
    monkeypatch.setattr(browser_session.util, "get_app_root", lambda: str(tmp_path))
    manager = browser_session.BrowserSessionManager(
        {"advanced": {"browser_type": "chrome", "browser_private_mode": True}}
    )
    monkeypatch.setattr(manager, "browser_executable_path", lambda: "chrome.exe")

    config = manager.build_config(["--disable-extensions"])

    assert install_calls == ["installed"]
    assert config.kwargs["browser_executable_path"] == "chrome.exe"
    assert config.kwargs["browser_args"] == ["--disable-extensions", "--incognito"]


@pytest.mark.asyncio
async def test_real_listener_continues_after_cancelled_transaction_response(monkeypatch):
    _restore_original_call(monkeypatch)
    assert install_zendriver_transaction_guard(Transaction) is True

    class FakeWebSocket:
        def __init__(self):
            self.messages = asyncio.Queue()

        async def recv(self):
            return await self.messages.get()

    websocket = FakeWebSocket()
    cancelled_transaction = Transaction(_return_value_command())
    cancelled_transaction.id = 41
    cancelled_transaction.cancel()
    live_transaction = Transaction(_return_value_command())
    live_transaction.id = 42
    connection = SimpleNamespace(
        websocket=websocket,
        mapper={41: cancelled_transaction, 42: live_transaction},
        handlers={},
    )

    await websocket.messages.put(json.dumps({"id": 41, "result": {"value": "loading"}}))
    await websocket.messages.put(json.dumps({"id": 42, "result": {"value": "complete"}}))
    listener = Listener(connection)
    try:
        assert await asyncio.wait_for(live_transaction, timeout=1.0) == "complete"
        assert listener.running is True
    finally:
        listener.cancel()
        with suppress(asyncio.CancelledError):
            await listener.task
