from __future__ import annotations

import subprocess

import pytest

import util
from hunter_metadata import APP_VERSION
from platforms.tixcraft import (
    _parse_tixcraft_area_text_cache,
    _parse_tixcraft_row_htmls,
    _tixcraft_text_contains_keyword,
    nodriver_tixcraft_date_auto_select,
)


class _Debug:
    def __init__(self) -> None:
        self.enabled = False
        self.messages: list[str] = []

    def log(self, message: str, *args: object) -> None:
        if args:
            message = " ".join([message, *[str(arg) for arg in args]])
        self.messages.append(message)


class _FakeDateRow:
    def __init__(self, html: str) -> None:
        self.html = html
        self.clicked = False

    async def get_html(self) -> str:
        return self.html

    async def query_selector(self, selector: str):  # noqa: ANN001
        if selector != "button":
            return None
        return self

    async def click(self) -> None:
        self.clicked = True


class _FakeDateTab:
    def __init__(self, rows: list[_FakeDateRow], row_html_mode: str = "list") -> None:
        self.rows = rows
        self.row_html_mode = row_html_mode

    async def wait_for(self, selector: str, timeout: int) -> None:
        return None

    async def evaluate(self, script: str):  # noqa: ANN001
        if "document.documentElement.lang" in script:
            return "zh-TW"
        if "querySelectorAll('#gameList > table > tbody > tr')" in script:
            if self.row_html_mode == "json":
                import json

                return json.dumps([row.html for row in self.rows])
            return [row.html for row in self.rows]
        return None

    async def query_selector_all(self, selector: str):  # noqa: ANN001
        return self.rows


def test_app_version_bumped_to_v041() -> None:
    assert APP_VERSION == "0.4.1"


def test_remove_html_tags_preserves_word_boundaries() -> None:
    html = "<td>2026/09/26 (六)<br>19:00</td><td>後台 Backstage Live</td>"

    assert util.remove_html_tags(html) == "2026/09/26 (六) 19:00 後台 Backstage Live"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ('"2026/09/26"', ["2026/09/26"]),
        ("2026/09/26", ["2026/09/26"]),
        ("S區;T區;W區;全票＋福利券", ["S區", "T區", "W區", "全票＋福利券"]),
        ('["A區","B區"]', ["A區", "B區"]),
        ('"A區","B區"', ["A區", "B區"]),
    ],
)
def test_parse_keyword_string_to_array_accepts_legacy_and_raw_formats(raw: str, expected: list[str]) -> None:
    assert util.parse_keyword_string_to_array(raw) == expected


@pytest.mark.asyncio
@pytest.mark.parametrize("row_html_mode", ["list", "json"])
async def test_tixcraft_date_keyword_matches_row_with_br_spacing(
    monkeypatch: pytest.MonkeyPatch,
    row_html_mode: str,
) -> None:
    debug = _Debug()
    monkeypatch.setattr(util, "create_debug_logger", lambda config: debug)
    rows = [
        _FakeDateRow("<td>2026/08/12 (三)<br>19:30</td><td>Legacy TERA</td><td><button>立即訂購</button></td>"),
        _FakeDateRow("<td>2026/09/26 (六)<br>19:00</td><td>後台 Backstage Live</td><td><button>立即訂購</button></td>"),
        _FakeDateRow("<td>2026/09/27 (日)<br>18:00</td><td>後台 Backstage Live</td><td><button>立即訂購</button></td>"),
    ]
    tab = _FakeDateTab(rows, row_html_mode=row_html_mode)

    matched = await nodriver_tixcraft_date_auto_select(
        tab,
        "https://tixcraft.com/activity/game/26_edyhsiao",
        {
            "advanced": {"verbose": False},
            "date_auto_select": {
                "enable": True,
                "mode": util.CONST_RANDOM,
                "date_keyword": "2026/09/26",
            },
            "date_auto_fallback": False,
            "keyword_exclude": "",
            "tixcraft": {
                "pass_date_is_sold_out": True,
                "auto_reload_coming_soon_page": True,
            },
        },
        "tixcraft",
    )

    assert matched is True
    assert rows[0].clicked is False
    assert rows[1].clicked is True
    assert rows[2].clicked is False
    assert any("Keyword #1 matched" in message for message in debug.messages)


def test_parse_tixcraft_row_htmls_accepts_json_string_and_lists() -> None:
    rows = ["<tr><td>A</td></tr>", "<tr><td>B</td></tr>"]

    assert _parse_tixcraft_row_htmls(rows) == rows
    assert _parse_tixcraft_row_htmls('["<tr><td>A</td></tr>", "<tr><td>B</td></tr>"]') == rows
    assert _parse_tixcraft_row_htmls("not-json") is None


def test_parse_tixcraft_area_text_cache_accepts_json_string_and_lists() -> None:
    rows = [{"text": "B區 剩餘 1", "fontText": "剩餘 1"}]

    assert _parse_tixcraft_area_text_cache(rows) == rows
    assert _parse_tixcraft_area_text_cache('[{"text": "B區 剩餘 1", "fontText": "剩餘 1"}]') == rows
    assert _parse_tixcraft_area_text_cache("not-json") is None
    assert _parse_tixcraft_area_text_cache(["bad"]) is None


def test_tixcraft_date_keyword_matching_tolerates_whitespace_variations() -> None:
    row_text = "2026/09/26(六)19:00 後台 Backstage Live 立即訂購"

    assert _tixcraft_text_contains_keyword(row_text, "2026/09/26 (六) 19:00")
    assert _tixcraft_text_contains_keyword(row_text, "2026/09/26")
    assert not _tixcraft_text_contains_keyword(row_text, "2026/09/27")


def test_keyword_normalization_matches_full_width_ticket_text() -> None:
    row_text = util.format_keyword_string("全票＋福利券 ６,６８０")
    keyword = util.format_keyword_string("全票+福利券 6,680")

    assert keyword in row_text


def test_tixcraft_date_keyword_matching_tolerates_full_width_digits() -> None:
    row_text = (
        "\uff12\uff10\uff12\uff16\uff0f\uff10\uff19\uff0f\uff12\uff16 "
        "(\u516d) \uff11\uff19:\uff10\uff10 Backstage Live"
    )

    assert _tixcraft_text_contains_keyword(row_text, "2026/09/26")
    assert _tixcraft_text_contains_keyword(row_text, "2026/09/26 (\u516d) 19:00")


def test_launch_maxbot_does_not_put_credentials_in_command_line(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def fake_popen(args, cwd=None):  # noqa: ANN001
        captured["args"] = args
        captured["cwd"] = cwd
        return object()

    monkeypatch.setattr(subprocess, "Popen", fake_popen)

    util.launch_maxbot(
        script_name="nodriver_tixcraft",
        filename="settings.json",
        homepage="https://kktix.com/events/demo",
        kktix_account="user@example.com",
        kktix_password="secret-password",
        window_size="1024,768",
        headless="false",
    )

    command_line = " ".join(captured["args"])
    assert "--kktix_account" not in command_line
    assert "--kktix_password" not in command_line
    assert "user@example.com" not in command_line
    assert "secret-password" not in command_line
    assert "--input=settings.json" in command_line
    assert "--homepage=https://kktix.com/events/demo" in command_line


def test_redact_sensitive_text_masks_tokens_and_webhooks() -> None:
    text = (
        "failed https://api.telegram.org/bot123456:ABC_def/sendMessage "
        "webhook=https://discord.com/api/webhooks/123/token password=my-pass "
        "cookie=sessionid"
    )

    redacted = util.redact_sensitive_text(text)

    assert "123456:ABC_def" not in redacted
    assert "/api/webhooks/123/token" not in redacted
    assert "my-pass" not in redacted
    assert "sessionid" not in redacted
    assert "https://api.telegram.org/bot***" in redacted
    assert "webhook=***" in redacted
