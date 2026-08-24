"""Runtime compatibility guards for known Zendriver transport races.

This module intentionally stays below HunterX's platform and purchasing layers.
It only protects Zendriver's CDP response Future from a response that arrives
after its awaiting task has already completed or been cancelled.
"""

from __future__ import annotations

import asyncio
import functools
import logging
from typing import Any


logger = logging.getLogger(__name__)

_PATCH_MARKER = "__hunterx_late_cdp_response_guard__"
_ORIGINAL_CALL_ATTRIBUTE = "__hunterx_original_transaction_call__"
_CONNECTION_PATCH_MARKER = "__hunterx_event_mapper_guard__"
_ORIGINAL_INIT_ATTRIBUTE = "__hunterx_original_connection_init__"


class _PendingTransactionMap(dict[int, Any]):
    """Store request transactions while discarding write-only CDP events.

    Zendriver 0.14 adds every parsed browser event to ``Connection.mapper``.
    Request/response transactions are removed when their response arrives, but
    event transactions have no response and are never read from the mapping.
    A long-running browser therefore retains the complete event stream.  The
    listener still dispatches the local ``event`` variable to handlers, so not
    retaining the redundant EventTransaction does not change callback behavior.
    """

    def __init__(self, event_transaction_class: type, *args: Any, **kwargs: Any):
        self._event_transaction_class = event_transaction_class
        super().__init__(*args, **kwargs)

    def __setitem__(self, key: int, value: Any) -> None:
        if isinstance(value, self._event_transaction_class):
            return
        super().__setitem__(key, value)

    def update(self, *args: Any, **kwargs: Any) -> None:
        incoming = dict(*args, **kwargs)
        for key, value in incoming.items():
            self[key] = value


def install_zendriver_event_mapper_guard(
    connection_class: type | None = None,
    event_transaction_class: type | None = None,
) -> bool:
    """Install a process-wide bounded mapper for future Zendriver connections."""

    if connection_class is None or event_transaction_class is None:
        from zendriver.core.connection import Connection, EventTransaction

        connection_class = connection_class or Connection
        event_transaction_class = event_transaction_class or EventTransaction

    current_init = vars(connection_class).get("__init__")
    if not callable(current_init):
        raise TypeError("Zendriver Connection must define a callable __init__")
    if getattr(current_init, _CONNECTION_PATCH_MARKER, False):
        return False

    @functools.wraps(current_init)
    def guarded_init(connection: Any, *args: Any, **kwargs: Any) -> None:
        current_init(connection, *args, **kwargs)
        existing = getattr(connection, "mapper", {})
        if not isinstance(existing, _PendingTransactionMap):
            connection.mapper = _PendingTransactionMap(
                event_transaction_class,
                existing,
            )

    setattr(guarded_init, _CONNECTION_PATCH_MARKER, True)
    setattr(guarded_init, _ORIGINAL_INIT_ATTRIBUTE, current_init)
    # Zendriver's metaclass deliberately rejects ordinary class assignment.
    # Calling ``type.__setattr__`` installs this one compatibility wrapper
    # without weakening that metaclass policy for any later caller.
    type.__setattr__(connection_class, "__init__", guarded_init)
    return True


def _describe_transaction_state(transaction: Any) -> str:
    if transaction.cancelled():
        return "cancelled"
    if transaction.done():
        return "finished"
    return "pending"


def _log_late_response(transaction: Any, response: dict[str, Any]) -> None:
    logger.debug(
        "Ignored late Zendriver CDP response: id=%r method=%r state=%s response_has_error=%s",
        getattr(transaction, "id", None),
        getattr(transaction, "method", None),
        _describe_transaction_state(transaction),
        "error" in response,
    )


def install_zendriver_transaction_guard(transaction_class: type | None = None) -> bool:
    """Keep a late CDP response from terminating Zendriver's listener task.

    Zendriver stores each CDP command in an ``asyncio.Future`` subclass. If the
    caller is cancelled while Chrome is navigating, Chrome may still send the
    response. Zendriver then calls ``set_result`` or ``set_exception`` on an
    already-done Future, raising ``asyncio.InvalidStateError`` from its listener
    loop. Discarding that stale response is safe because no caller can consume
    it after the Future is done.

    Returns ``True`` when the guard is installed and ``False`` when the class
    was already guarded. ``transaction_class`` exists for isolated tests; the
    production path always uses Zendriver's real Transaction class.
    """

    install_mapper_guard = transaction_class is None
    if transaction_class is None:
        from zendriver.core.connection import Transaction

        transaction_class = Transaction

    if install_mapper_guard:
        install_zendriver_event_mapper_guard()

    current_call = transaction_class.__call__
    if getattr(current_call, _PATCH_MARKER, False):
        return False

    @functools.wraps(current_call)
    def guarded_call(transaction: Any, **response: Any) -> None:
        if transaction.done():
            _log_late_response(transaction, response)
            return None

        try:
            current_call(transaction, **response)
            return None
        except asyncio.InvalidStateError:
            # This catch closes the tiny callback race between done() above
            # and Zendriver's set_result()/set_exception() call. Never hide an
            # InvalidStateError while the transaction is still live.
            if not transaction.done():
                raise
            _log_late_response(transaction, response)
            return None

    setattr(guarded_call, _PATCH_MARKER, True)
    setattr(guarded_call, _ORIGINAL_CALL_ATTRIBUTE, current_call)
    setattr(transaction_class, "__call__", guarded_call)
    return True
