from __future__ import annotations

import re
from pathlib import Path

from hunter_metadata import APP_DISPLAY_VERSION, RELEASE_URL


REPO_ROOT = Path(__file__).resolve().parents[1]


def read_text(path: str) -> str:
    return (REPO_ROOT / path).read_text(encoding="utf-8", errors="replace")


def test_release_url_points_to_hunter() -> None:
    assert RELEASE_URL == "https://github.com/sssss928/hunterX/releases"
    assert "github.com/bouob/tickets_hunter/releases" not in read_text("src/settings.py")


def test_app_version_uses_hunter_semver_display() -> None:
    assert re.fullmatch(r"HunterX \(\d+\.\d+\.\d+\)", APP_DISPLAY_VERSION)


def test_codeowners_no_longer_points_to_upstream_maintainer() -> None:
    codeowners = read_text(".github/CODEOWNERS")
    assert "@bouob" not in codeowners
    assert "@sssss928" in codeowners


def test_readme_has_fork_notice() -> None:
    readme = read_text("README.md")
    assert "Fork Notice" in readme
    assert "sssss928/hunterX" in readme
    assert "bouob/tickets_hunter" in readme


def test_contributing_targets_hunter_not_upstream_clone() -> None:
    contributing = read_text("CONTRIBUTING.md")
    assert "git clone https://github.com/YOUR_USERNAME/hunterX.git" in contributing
    assert "YOUR_USERNAME/tickets_hunter" not in contributing
    assert "git remote add upstream https://github.com/bouob/tickets_hunter.git" not in contributing


def test_settings_ui_referenced_vendor_assets_exist() -> None:
    settings_html = read_text("src/www/settings.html")
    for asset in [
        "dist/bootstrap/bootstrap.min.css",
        "dist/bootstrap/bootstrap.min.js",
        "dist/jquery.min.js",
    ]:
        assert asset in settings_html
        assert (REPO_ROOT / "src/www" / asset).is_file()
