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

    if transaction_class is None:
        from zendriver.core.connection import Transaction

        transaction_class = Transaction

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
