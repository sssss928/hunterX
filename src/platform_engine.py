"""Shared runtime lifecycle for all ticket-platform adapters."""

from __future__ import annotations

import time
import weakref
from dataclasses import dataclass
from typing import Any

import runtime_health
from page_classifier import PageClass
from platform_adapters import adapter_for_url
from platform_contract import PlatformAdapter, PlatformRuntimeState
from run_modes import is_leak_watch_mode


@dataclass(frozen=True)
class DispatchDecision:
    allowed: bool
    reason: str
    page_class: PageClass
    adapter: PlatformAdapter | None


class PlatformEngine:
    """Own bounded per-tab state and apply fail-closed dispatch policy."""

    def __init__(self) -> None:
        self._states: weakref.WeakKeyDictionary[Any, dict[str, PlatformRuntimeState]] = (
            weakref.WeakKeyDictionary()
        )
        self._fallback_states: dict[int, tuple[Any, dict[str, PlatformRuntimeState]]] = {}
        self._fallback_capacity = 64

    def _tab_states(self, tab: Any) -> dict[str, PlatformRuntimeState]:
        try:
            states = self._states.get(tab)
            if states is None:
                states = {}
                self._states[tab] = states
            return states
        except TypeError:
            tab_id = id(tab)
            current = self._fallback_states.get(tab_id)
            if current is not None and current[0] is tab:
                return current[1]
            if len(self._fallback_states) >= self._fallback_capacity:
                self._fallback_states.clear()
            states = {}
            self._fallback_states[tab_id] = (tab, states)
            return states

    def state_for(self, tab: Any, adapter: PlatformAdapter) -> PlatformRuntimeState:
        states = self._tab_states(tab)
        state = states.get(adapter.key)
        if state is None:
            state = PlatformRuntimeState()
            states[adapter.key] = state
        state.backfill()
        return state

    def before_dispatch(
        self,
        tab: Any,
        url: str,
        config_dict: dict[str, Any] | None,
        *,
        text: str = "",
        now: float | None = None,
    ) -> DispatchDecision:
        adapter = adapter_for_url(url)
        if adapter is None:
            return DispatchDecision(False, "unsupported_host", PageClass.UNKNOWN, None)

        page_class = adapter.classify_page(url, text)
        state = self.state_for(tab, adapter)
        state.current_page = page_class
        state.previous_url = str(url or "")
        state.cycle_count += 1
        current = time.monotonic() if now is None else float(now)
        expired = state.leak_scheduler.maintenance(config_dict, url, current)
        for event in expired:
            runtime_health.runtime_log(
                "[PLATFORM] watchdog_recovered",
                config_dict,
                platform=adapter.key,
                event=event,
                current_url=url,
            )

        if is_leak_watch_mode(config_dict):
            if not adapter.capabilities().supports_leak_watch:
                return DispatchDecision(False, "leak_watch_not_contract_complete", page_class, adapter)
            if adapter.is_protected_page(url, text):
                # Platform selection/checkout handlers may still inspect this
                # page. Automatic leak-watch refresh remains fail-closed in
                # ReloadGuard; protected status does not disable normal
                # purchase progression.
                return DispatchDecision(True, "protected_no_reload", page_class, adapter)
            if not adapter.is_safe_watch_page(url, text):
                return DispatchDecision(True, "unknown_no_reload", page_class, adapter)

        return DispatchDecision(True, "ready", page_class, adapter)

    def clear_tab(self, tab: Any) -> None:
        try:
            self._states.pop(tab, None)
        except TypeError:
            pass
        current = self._fallback_states.get(id(tab))
        if current is not None and current[0] is tab:
            self._fallback_states.pop(id(tab), None)

    @property
    def state_count(self) -> int:
        return sum(len(states) for states in self._states.values()) + sum(
            len(item[1]) for item in self._fallback_states.values()
        )


platform_engine = PlatformEngine()
