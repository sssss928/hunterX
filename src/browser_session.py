from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import chrome_downloader
import util
from hunter_metadata import APP_NAME

try:
    from zendriver.core.config import Config
except Exception:  # pragma: no cover - exercised in environments without zendriver.
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

    def attach(self, driver) -> None:
        self.driver = driver

    async def stop_browser(self) -> None:
        if self.driver is None:
            return
        await self.driver.stop()
        self.driver = None
