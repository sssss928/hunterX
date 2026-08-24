from __future__ import annotations

import asyncio
import inspect
import os
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Awaitable, Callable

import chrome_downloader
import util
from hunter_metadata import APP_NAME
from navigation_context import canonicalize_target_url
from platform_registry import platform_key_for_url
from runtime_health import RecoveryLevel
from zendriver_hardening import install_zendriver_transaction_guard

try:
    from zendriver import cdp
    from zendriver.core.config import Config
except Exception:  # pragma: no cover - exercised in environments without zendriver.
    cdp = None
    Config = None


BROWSER_CHROME = "chrome"
BROWSER_EDGE = "edge"
PRIVATE_ARG = {
    BROWSER_CHROME: "--incognito",
    BROWSER_EDGE: "--inprivate",
}


def normalize_browser_type(value: str | None) -> str:
    value = (value or BROWSER_CHROME).strip().lower()
    if value not in {BROWSER_CHROME, BROWSER_EDGE}:
        return BROWSER_CHROME
    return value


def normalize_private_mode(value) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "y", "on"}


def find_edge_executable() -> str | None:
    candidates = [
        os.environ.get("HUNTERX_EDGE_PATH", ""),
        os.path.expandvars(r"%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe"),
        os.path.expandvars(r"%ProgramFiles%\Microsoft\Edge\Application\msedge.exe"),
        os.path.expandvars(r"%LocalAppData%\Microsoft\Edge\Application\msedge.exe"),
    ]
    for candidate in candidates:
        if candidate and os.path.exists(candidate):
            return candidate
    return None


@dataclass
class BrowserLaunchConfig:
    browser_type: str = BROWSER_CHROME
    private_mode: bool = False
    headless: bool = False
    proxy_server_port: str = ""
    instance: str = "default"


class BrowserExitState(str, Enum):
    ALIVE = "alive"
    CLEAN_EXIT = "clean_exit"
    CRASHED = "crashed"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class BrowserRecoveryResult:
    success: bool
    reason: str
    level: RecoveryLevel
    driver: Any | None = None
    tab: Any | None = None
    restarted: bool = False


@dataclass(frozen=True)
class BrowserBootstrapResult:
    """A fully initialized browser and its single automation-owned tab."""

    driver: Any
    tab: Any


@dataclass(frozen=True)
class OwnedTabDiagnostic:
    """Read-only evidence for the one-automated-tab-per-instance contract."""

    platform_key: str
    supports_same_browser_multi_tab: bool
    automated_tab: Any | None
    same_platform_tab_count: int
    extra_tab_count: int
    extra_tabs: tuple[Any, ...]
    extra_target_ids: tuple[str, ...]
    truncated: bool = False


class BrowserSessionManager:
    """Owns browser lifecycle. Other modules must not call driver.stop()."""

    def __init__(self, config_dict: dict, args=None) -> None:
        advanced = (config_dict or {}).get("advanced", {})
        cli_browser = getattr(args, "browser", "") if args else ""
        cli_private = getattr(args, "browser_private_mode", None) if args else None
        self.launch = BrowserLaunchConfig(
            browser_type=normalize_browser_type(cli_browser or advanced.get("browser_type", BROWSER_CHROME)),
            private_mode=normalize_private_mode(
                advanced.get("browser_private_mode", False) if cli_private is None else cli_private
            ),
            headless=bool(advanced.get("headless", False)),
            proxy_server_port=str(advanced.get("proxy_server_port", "") or ""),
            instance=getattr(args, "instance", "") if args and getattr(args, "instance", "") else util.get_instance_id(),
        )
        self.config_dict = config_dict
        self.args = args
        self.driver = None
        self.active_tab = None
        self._restart_factory: Callable[..., Awaitable[Any] | Any] | None = None
        self.stop_timeout_seconds = 10.0
        self.target_probe_timeout_seconds = 3.0

    def profile_parent_dir(self) -> Path:
        return Path(util.get_app_root()) / "browser_profiles" / self.launch.browser_type.capitalize()

    def profile_dir(self) -> Path:
        safe_instance = "".join(ch if ch.isalnum() or ch in ("_", "-") else "_" for ch in self.launch.instance)
        return self.profile_parent_dir() / (safe_instance or "default")

    def browser_executable_path(self) -> str | None:
        if self.launch.browser_type == BROWSER_EDGE:
            return find_edge_executable()
        webdriver_dir = os.path.join(util.get_app_root(), "webdriver")
        return chrome_downloader.ensure_chrome_available(download_dir=webdriver_dir)

    def build_args(self, base_args: list[str]) -> list[str]:
        browser_args = list(base_args)
        if self.launch.proxy_server_port:
            browser_args.append(f"--proxy-server={self.launch.proxy_server_port}")
        if self.launch.private_mode:
            browser_args.append(PRIVATE_ARG[self.launch.browser_type])
        else:
            profile_dir = self.profile_dir()
            profile_dir.mkdir(parents=True, exist_ok=True)
            browser_args.append(f"--user-data-dir={profile_dir}")
            browser_args.append(f"--profile-directory={APP_NAME}-{self.launch.instance or 'default'}")
        return browser_args

    def build_config(self, base_args: list[str], sandbox: bool = True) -> Config:
        if Config is None:
            raise RuntimeError("zendriver is required to build a browser config")
        # Install before ``uc.start`` can create a Connection/Listener. A
        # cancelled CDP transaction may receive a late Chrome response during
        # navigation; unguarded Zendriver raises InvalidStateError and kills
        # its only listener task. The patch is process-wide and idempotent.
        install_zendriver_transaction_guard()
        browser_path = self.browser_executable_path()
        if self.launch.browser_type == BROWSER_EDGE and not browser_path:
            raise FileNotFoundError("Microsoft Edge executable was not found")
        if self.launch.browser_type == BROWSER_CHROME and not browser_path:
            raise FileNotFoundError("Chrome executable was not found or downloaded")
        conf = Config(
            browser_args=self.build_args(base_args),
            sandbox=sandbox,
            headless=self.launch.headless,
            browser_executable_path=browser_path,
        )
        if not self.launch.private_mode:
            try:
                conf.user_data_dir = str(self.profile_dir())
            except Exception:
                pass
        return conf

    def attach(self, driver, tab=None) -> None:
        self.driver = driver
        if tab is not None:
            self.active_tab = tab

    def set_restart_factory(
        self,
        factory: Callable[..., Awaitable[Any] | Any] | None,
    ) -> None:
        self._restart_factory = factory

    def _call_restart_factory(self, *, target_url: str, platform_key: str) -> Any:
        """Invoke the context-aware bootstrap factory with legacy arity support."""

        if self._restart_factory is None:
            return None
        try:
            inspect.signature(self._restart_factory).bind(
                target_url=target_url,
                platform_key=platform_key,
            )
        except (TypeError, ValueError):
            # Keep the public setter callable by older embedders, but recovery
            # will still reject a legacy driver-only result as incomplete.
            return self._restart_factory()
        return self._restart_factory(
            target_url=target_url,
            platform_key=platform_key,
        )

    def browser_exit_state(self) -> BrowserExitState:
        if self.driver is None:
            return BrowserExitState.UNKNOWN
        process = None
        for name in ("browser_process", "process", "_process"):
            candidate = getattr(self.driver, name, None)
            if candidate is not None:
                process = candidate
                break
        if process is None:
            return BrowserExitState.UNKNOWN
        try:
            return_code = process.poll() if callable(getattr(process, "poll", None)) else process.returncode
        except Exception:
            return BrowserExitState.UNKNOWN
        if return_code is None:
            return BrowserExitState.ALIVE
        if int(return_code) == 0:
            return BrowserExitState.CLEAN_EXIT
        return BrowserExitState.CRASHED

    def browser_pid(self) -> int | None:
        if self.driver is None:
            return None
        for name in ("browser_process", "process", "_process"):
            process = getattr(self.driver, name, None)
            if process is None:
                continue
            try:
                pid = int(getattr(process, "pid", 0) or 0)
            except (TypeError, ValueError):
                continue
            if pid > 0:
                return pid
        return None

    @staticmethod
    def _tab_url(tab: Any) -> str:
        try:
            value = getattr(getattr(tab, "target", None), "url", "")
        except Exception:
            return ""
        return value.strip() if isinstance(value, str) else ""

    @staticmethod
    def _tab_target_id(tab: Any) -> str:
        try:
            target = getattr(tab, "target", None)
            value = getattr(target, "target_id", None) or getattr(target, "id", None)
        except Exception:
            return ""
        return str(value or "").strip()

    def _candidate_tabs(self) -> list[Any]:
        if self.driver is None:
            return []
        candidates: list[Any] = []
        tabs = getattr(self.driver, "tabs", None)
        if isinstance(tabs, (list, tuple)):
            candidates.extend(tabs)
        main_tab = getattr(self.driver, "main_tab", None)
        if main_tab is not None and all(main_tab is not item for item in candidates):
            candidates.append(main_tab)
        return candidates

    def owned_tab_diagnostic(self, platform_key: str) -> OwnedTabDiagnostic:
        """Describe extra same-platform tabs without operating on any of them.

        HunterX deliberately automates only ``active_tab`` in each named
        instance.  The returned tab list is capped so low-frequency diagnostics
        cannot grow without bound; ``extra_tab_count`` still reports the full
        observed count.
        """

        normalized_platform = str(platform_key or "").strip().casefold()
        automated_tab = self.active_tab
        if (
            automated_tab is None
            or platform_key_for_url(self._tab_url(automated_tab)) != normalized_platform
        ):
            automated_tab = None

        same_platform_tabs: list[Any] = []
        seen: set[int] = set()
        if normalized_platform:
            for candidate in self._candidate_tabs():
                marker = id(candidate)
                if marker in seen:
                    continue
                seen.add(marker)
                if platform_key_for_url(self._tab_url(candidate)) == normalized_platform:
                    same_platform_tabs.append(candidate)

        extras = [candidate for candidate in same_platform_tabs if candidate is not automated_tab]
        bounded_extras = tuple(extras[:16])
        return OwnedTabDiagnostic(
            platform_key=normalized_platform,
            supports_same_browser_multi_tab=False,
            automated_tab=automated_tab,
            same_platform_tab_count=len(same_platform_tabs),
            extra_tab_count=len(extras),
            extra_tabs=bounded_extras,
            extra_target_ids=tuple(self._tab_target_id(tab) for tab in bounded_extras),
            truncated=len(extras) > len(bounded_extras),
        )

    def _select_reacquire_tab(self, target_url: str, platform_key: str) -> Any | None:
        """Select a compatible target without changing the active-tab owner."""

        normalized_target = canonicalize_target_url(target_url)
        normalized_platform = str(platform_key or "").strip().casefold()
        if (
            not normalized_target
            or not normalized_platform
            or platform_key_for_url(normalized_target) != normalized_platform
        ):
            return None

        owned_target_id = self._tab_target_id(self.active_tab)
        exact_matches: list[Any] = []
        owned_matches: list[Any] = []
        for candidate in self._candidate_tabs():
            candidate_url = self._tab_url(candidate)
            if not candidate_url:
                continue
            if platform_key_for_url(candidate_url) != normalized_platform:
                continue
            if canonicalize_target_url(candidate_url) == normalized_target:
                exact_matches.append(candidate)
            if owned_target_id and self._tab_target_id(candidate) == owned_target_id:
                owned_matches.append(candidate)

        if len(exact_matches) == 1:
            return exact_matches[0]
        if len(exact_matches) > 1:
            exact_owned = [
                candidate
                for candidate in exact_matches
                if owned_target_id and self._tab_target_id(candidate) == owned_target_id
            ]
            return exact_owned[0] if len(exact_owned) == 1 else None
        if len(owned_matches) == 1:
            # A stable DevTools target id proves this is the same owned browser
            # target even when its route advanced while recovery was starting.
            return owned_matches[0]
        return None

    def reacquire_tab(self, target_url: str, platform_key: str) -> Any | None:
        """Compatibility API: select and commit a safely identifiable target."""

        candidate = self._select_reacquire_tab(target_url, platform_key)
        if candidate is not None:
            self.active_tab = candidate
        return candidate

    async def _prove_target_transport(
        self,
        tab: Any,
        *,
        target_url: str,
        platform_key: str,
        owned_target_id: str,
    ) -> bool:
        """Actively prove the selected target transport without page mutation."""

        normalized_target = canonicalize_target_url(target_url)
        normalized_platform = str(platform_key or "").strip().casefold()
        if tab is None or not normalized_target or not normalized_platform:
            return False

        live_url = ""
        live_target_id = ""
        send = getattr(tab, "send", None)
        if cdp is not None and callable(send):
            try:
                pending = send(cdp.target.get_target_info())
                if not inspect.isawaitable(pending):
                    return False
                target_info = await asyncio.wait_for(
                    pending,
                    timeout=self.target_probe_timeout_seconds,
                )
                live_url = str(getattr(target_info, "url", "") or "").strip()
                live_target_id = str(
                    getattr(target_info, "target_id", None)
                    or getattr(target_info, "id", None)
                    or ""
                ).strip()
            except Exception:
                return False
        else:
            # Compatibility path for lightweight embedders. Unlike
            # nodriver_current_url(), this deliberately has no cached-URL
            # fallback: a failed active call is a failed transport proof.
            evaluate = getattr(tab, "evaluate", None)
            if not callable(evaluate):
                return False
            try:
                pending = evaluate("window.location.href")
                if not inspect.isawaitable(pending):
                    return False
                live_url = str(
                    await asyncio.wait_for(
                        pending,
                        timeout=self.target_probe_timeout_seconds,
                    )
                    or ""
                ).strip()
            except Exception:
                return False

        if not live_url or platform_key_for_url(live_url) != normalized_platform:
            return False
        candidate_target_id = self._tab_target_id(tab)
        if (
            live_target_id
            and candidate_target_id
            and live_target_id != candidate_target_id
        ):
            return False
        if canonicalize_target_url(live_url) == normalized_target:
            return True
        proven_target_id = live_target_id or candidate_target_id
        return bool(owned_target_id and proven_target_id == owned_target_id)

    async def _refresh_targets(self) -> None:
        if self.driver is None:
            return
        callback = getattr(self.driver, "update_targets", None)
        if not callable(callback):
            return
        result = callback()
        if inspect.isawaitable(result):
            await asyncio.wait_for(result, timeout=3.0)

    async def recover(
        self,
        level: RecoveryLevel,
        *,
        target_url: str,
        platform_key: str,
        allow_restart: bool,
    ) -> BrowserRecoveryResult:
        if level in {RecoveryLevel.STOP, RecoveryLevel.FAIL_CLOSED}:
            return BrowserRecoveryResult(False, level.name.casefold(), level, self.driver, self.active_tab)

        if level is RecoveryLevel.REACQUIRE:
            previous_active_tab = self.active_tab
            owned_target_id = self._tab_target_id(previous_active_tab)
            tab = self._select_reacquire_tab(target_url, platform_key)
            if tab is None:
                return BrowserRecoveryResult(
                    False,
                    "target_not_found",
                    level,
                    self.driver,
                    None,
                )
            proven = await self._prove_target_transport(
                tab,
                target_url=target_url,
                platform_key=platform_key,
                owned_target_id=owned_target_id,
            )
            if not proven:
                return BrowserRecoveryResult(
                    False,
                    "transport_probe_failed",
                    level,
                    self.driver,
                    previous_active_tab,
                )
            self.active_tab = tab
            return BrowserRecoveryResult(
                True,
                "target_reacquired",
                level,
                self.driver,
                tab,
            )

        if level is RecoveryLevel.TRANSPORT_REBIND:
            previous_active_tab = self.active_tab
            owned_target_id = self._tab_target_id(previous_active_tab)
            try:
                await self._refresh_targets()
            except Exception:
                pass
            tab = self._select_reacquire_tab(target_url, platform_key)
            if tab is None:
                return BrowserRecoveryResult(
                    False,
                    "transport_rebind_failed",
                    level,
                    self.driver,
                    None,
                )
            proven = await self._prove_target_transport(
                tab,
                target_url=target_url,
                platform_key=platform_key,
                owned_target_id=owned_target_id,
            )
            if not proven:
                return BrowserRecoveryResult(
                    False,
                    "transport_probe_failed",
                    level,
                    self.driver,
                    previous_active_tab,
                )
            self.active_tab = tab
            return BrowserRecoveryResult(
                True,
                "transport_rebound",
                level,
                self.driver,
                tab,
            )

        if level is not RecoveryLevel.SAFE_RESTART:
            return BrowserRecoveryResult(False, "normal_retry", level, self.driver, self.active_tab)

        exit_state = self.browser_exit_state()
        if exit_state is BrowserExitState.CLEAN_EXIT:
            return BrowserRecoveryResult(False, "manual_browser_close", level, self.driver, self.active_tab)
        if not allow_restart:
            return BrowserRecoveryResult(False, "safe_restart_not_authorized", level, self.driver, self.active_tab)
        if self._restart_factory is None:
            return BrowserRecoveryResult(False, "restart_factory_unavailable", level, self.driver, self.active_tab)
        if exit_state is BrowserExitState.UNKNOWN:
            return BrowserRecoveryResult(False, "browser_exit_state_unknown", level, self.driver, self.active_tab)

        if exit_state is BrowserExitState.ALIVE:
            await self.stop_browser()
        else:
            self.driver = None
            self.active_tab = None
        try:
            restarted = self._call_restart_factory(
                target_url=target_url,
                platform_key=platform_key,
            )
            if inspect.isawaitable(restarted):
                restarted = await restarted
        except Exception:
            return BrowserRecoveryResult(
                False,
                "browser_bootstrap_failed",
                level,
                self.driver,
                self.active_tab,
                self.driver is not None,
            )
        if restarted is None:
            return BrowserRecoveryResult(False, "browser_restart_failed", level)
        if not isinstance(restarted, BrowserBootstrapResult):
            # Retain lifecycle ownership so final cleanup can close a browser
            # returned by a legacy/incomplete factory.  No tab is activated.
            self.driver = restarted
            self.active_tab = None
            return BrowserRecoveryResult(
                False,
                "browser_bootstrap_incomplete",
                level,
                restarted,
                None,
                True,
            )
        if restarted.driver is None or restarted.tab is None:
            if restarted.driver is not None:
                self.driver = restarted.driver
                self.active_tab = None
            return BrowserRecoveryResult(
                False,
                "browser_bootstrap_incomplete",
                level,
                restarted.driver,
                restarted.tab,
                restarted.driver is not None,
            )
        if self.driver is not restarted.driver or self.active_tab is not restarted.tab:
            self.attach(restarted.driver, restarted.tab)
        return BrowserRecoveryResult(
            True,
            "browser_restarted",
            level,
            restarted.driver,
            restarted.tab,
            True,
        )

    async def stop_browser(self) -> None:
        if self.driver is None:
            return
        stop = getattr(self.driver, "stop", None)
        if callable(stop):
            result = stop()
            if inspect.isawaitable(result):
                await asyncio.wait_for(result, timeout=self.stop_timeout_seconds)
        self.driver = None
        self.active_tab = None
