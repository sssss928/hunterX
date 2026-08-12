"""Bounded registry for tabs created by HunterX actions.

Closing an arbitrary browser tab is destructive.  Callers must register the
specific tab observed as a result of a bot click before it can be closed.
"""

from __future__ import annotations

import weakref
from typing import Any


OWNED_TAB_FALLBACK_CAPACITY = 128
_owned_tabs: weakref.WeakKeyDictionary[Any, str] = weakref.WeakKeyDictionary()
_fallback_owned_tabs: dict[int, tuple[Any, str]] = {}


def register_owned_tab(tab: Any, reason: str) -> None:
    try:
        _owned_tabs[tab] = str(reason or "bot_created")
        return
    except TypeError:
        pass
    if len(_fallback_owned_tabs) >= OWNED_TAB_FALLBACK_CAPACITY:
        _fallback_owned_tabs.clear()
    _fallback_owned_tabs[id(tab)] = (tab, str(reason or "bot_created"))


def is_owned_tab(tab: Any) -> bool:
    try:
        if tab in _owned_tabs:
            return True
    except TypeError:
        pass
    current = _fallback_owned_tabs.get(id(tab))
    return current is not None and current[0] is tab


def forget_owned_tab(tab: Any) -> None:
    try:
        _owned_tabs.pop(tab, None)
    except TypeError:
        pass
    current = _fallback_owned_tabs.get(id(tab))
    if current is not None and current[0] is tab:
        _fallback_owned_tabs.pop(id(tab), None)


async def close_owned_tab(tab: Any) -> bool:
    if not is_owned_tab(tab):
        return False
    try:
        await tab.close()
        return True
    finally:
        forget_owned_tab(tab)


def owned_tab_count() -> int:
    return len(_owned_tabs) + len(_fallback_owned_tabs)
