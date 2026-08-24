from __future__ import annotations

import ast
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import nodriver_tixcraft
import settings
import util
from platforms import ticketplus


def test_dictionary_parser_has_only_explicit_text_question_consumers() -> None:
    source_root = Path(__file__).resolve().parents[1] / "src" / "platforms"
    expected_consumers = {
        "tixcraft.py": {"nodriver_tixcraft_input_check_code"},
        "kktix.py": {"nodriver_kktix_reg_captcha"},
        "famiticket.py": {"nodriver_fami_verify"},
        "ibon.py": {
            "nodriver_ibon_card_vaildate",
            "nodriver_ibon_verification_question",
        },
        "kham.py": {"_kham_resolve_user_dictionary_answers"},
        "ticketplus.py": {"_ticketplus_resolve_user_dictionary_answers"},
        "hkticketing.py": {"nodriver_hkticketing_date_password_input"},
    }

    for filename, expected in expected_consumers.items():
        tree = ast.parse((source_root / filename).read_text(encoding="utf-8"))
        actual: set[str] = set()
        for node in tree.body:
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if any(
                isinstance(child, ast.Call)
                and isinstance(child.func, ast.Attribute)
                and child.func.attr == "get_answer_list_from_user_guess_string"
                for child in ast.walk(node)
            ):
                actual.add(node.name)
        assert actual == expected, filename


class _SettingsPostHandler:
    def __init__(self, payload: dict[str, object]) -> None:
        self.request = SimpleNamespace(
            body=json.dumps(payload, ensure_ascii=False).encode("utf-8")
        )
        self.status_code = 200
        self.responses: list[object] = []
        self.finished = False

    def get_query_argument(self, _name: str, default: str = "") -> str:
        return default

    def set_status(self, status_code: int) -> None:
        self.status_code = status_code

    def write(self, value: object) -> None:
        self.responses.append(value)

    def finish(self) -> None:
        self.finished = True


@pytest.mark.asyncio
async def test_settings_post_to_runtime_hot_reload_preserves_dictionary_losslessly(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    settings_path = tmp_path / "settings.json"
    existing = settings.get_default_config()
    incoming = settings.get_default_config()
    incoming["advanced"]["user_guess_string"] = (
        'first answer\nanswer "two"；path\\member;price 3,280'
    )
    handler = _SettingsPostHandler(incoming)

    monkeypatch.setattr(settings, "get_profile_filepath", lambda _profile="": str(settings_path))
    monkeypatch.setattr(
        settings,
        "load_json",
        lambda _profile="": (str(settings_path), existing),
    )

    settings.SaveJsonHandler.post(handler)

    assert handler.status_code == 200
    assert handler.finished is True
    stored = json.loads(settings_path.read_text(encoding="utf-8"))
    assert util.parse_user_dictionary_answers(
        stored["advanced"]["user_guess_string"]
    ) == ["first answer", 'answer "two"', "path\\member", "price 3,280"]

    async def no_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr(nodriver_tixcraft.asyncio, "sleep", no_sleep)
    runtime_config, loaded_mtime = await nodriver_tixcraft.reload_config(
        existing,
        -1.0,
        str(settings_path),
    )

    assert loaded_mtime == settings_path.stat().st_mtime
    assert util.get_answer_list_from_user_guess_string(
        runtime_config,
        "missing-online-answer.txt",
    ) == ["first answer", 'answer "two"', "path\\member", "price 3,280"]


@pytest.mark.asyncio
async def test_ticketplus_checkout_never_consumes_user_dictionary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dictionary_calls: list[bool] = []

    async def forbidden_dictionary_fill(_tab, _config) -> int:
        dictionary_calls.append(True)
        return 1

    async def no_agreement(_tab, _config) -> bool:
        return False

    monkeypatch.setattr(
        ticketplus,
        "nodriver_ticketplus_fill_user_dictionary_fields",
        forbidden_dictionary_fill,
    )
    monkeypatch.setattr(ticketplus, "nodriver_ticketplus_ticket_agree", no_agreement)

    assert await ticketplus.nodriver_ticketplus_confirm(object(), {}) is False
    assert dictionary_calls == []
