from __future__ import annotations

import json
from pathlib import Path

import pytest

import nodriver_tixcraft
import settings
import util
from platform_registry import platform_key_for_url
from platforms import famiticket, hkticketing, ibon, kham, kktix, ticketplus, tixcraft


@pytest.fixture(autouse=True)
def _isolate_online_answer_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        util,
        "get_instance_state_path",
        lambda _filename: str(tmp_path / "missing-online-answer.txt"),
    )


@pytest.mark.parametrize(
    ("stored_value", "expected"),
    [
        ('"alpha","beta"', ["alpha", "beta"]),
        ("alpha; beta", ["alpha", "beta"]),
        ('"alpha\nbeta"', ["alpha", "beta"]),
        ('"alpha；beta"', ["alpha", "beta"]),
        ('["alpha", "beta"]', ["alpha", "beta"]),
        (["alpha", "beta"], ["alpha", "beta"]),
        ('"price 3,280"', ["price 3,280"]),
        (json.dumps('say "YES"', ensure_ascii=False), ['say "YES"']),
        ('"alpha","alpha"," beta "', ["alpha", "beta"]),
        (None, []),
    ],
)
def test_user_dictionary_parser_accepts_supported_storage_and_display_formats(
    stored_value: object,
    expected: list[str],
) -> None:
    config = {"advanced": {"user_guess_string": stored_value}}

    assert util.get_answer_list_from_user_guess_string(config, "answers.txt") == expected


def test_user_dictionary_serializer_round_trips_quotes_backslashes_and_newlines() -> None:
    displayed = 'first answer\nanswer "two"；path\\member;price 3,280'

    stored = util.serialize_user_dictionary_answers(displayed)

    assert util.parse_user_dictionary_answers(stored) == [
        "first answer",
        'answer "two"',
        "path\\member",
        "price 3,280",
    ]
    assert stored == ",".join(
        json.dumps(item, ensure_ascii=False)
        for item in ["first answer", 'answer "two"', "path\\member", "price 3,280"]
    )


def test_user_dictionary_reads_the_entire_online_file_and_deduplicates_stably(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    answer_file = tmp_path / "online-answer.txt"
    answer_file.write_text(
        '"remote-a","remote-b"\nremote-c；remote-d\nremote-a',
        encoding="utf-8",
    )
    monkeypatch.setattr(util, "get_instance_state_path", lambda _filename: str(answer_file))
    config = {"advanced": {"user_guess_string": '"local","remote-b"'}}

    assert util.get_answer_list_from_user_guess_string(config, "answers.txt") == [
        "local",
        "remote-b",
        "remote-a",
        "remote-c",
        "remote-d",
    ]


def test_settings_migration_canonicalizes_user_dictionary_without_losing_content() -> None:
    config = settings.get_default_config()
    config["advanced"]["user_guess_string"] = [
        "member A",
        'member "B"',
        "price 3,280",
    ]

    migrated = settings.migrate_config(config)

    assert migrated is config
    assert util.parse_user_dictionary_answers(
        migrated["advanced"]["user_guess_string"]
    ) == ["member A", 'member "B"', "price 3,280"]
    assert isinstance(migrated["advanced"]["user_guess_string"], str)


@pytest.mark.asyncio
async def test_runtime_hot_reload_canonicalizes_and_applies_updated_user_dictionary(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    current = settings.get_default_config()
    updated = settings.get_default_config()
    updated["advanced"]["user_guess_string"] = 'first answer\nanswer "two"；path\\member'
    config_path = tmp_path / "settings.json"
    config_path.write_text(json.dumps(updated, ensure_ascii=False), encoding="utf-8")

    async def no_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr(nodriver_tixcraft.asyncio, "sleep", no_sleep)

    reloaded, loaded_mtime = await nodriver_tixcraft.reload_config(
        current,
        -1.0,
        str(config_path),
    )

    assert loaded_mtime == config_path.stat().st_mtime
    assert util.parse_user_dictionary_answers(
        reloaded["advanced"]["user_guess_string"]
    ) == ["first answer", 'answer "two"', "path\\member"]


@pytest.mark.parametrize(
    "resolver",
    [
        ticketplus._ticketplus_resolve_user_dictionary_answers,
        kham._kham_resolve_user_dictionary_answers,
    ],
)
def test_multi_field_platform_resolvers_receive_the_same_canonical_answers(resolver) -> None:
    config = settings.get_default_config()
    config["advanced"]["user_guess_string"] = "first；second"

    assert resolver(config, ["第一題", "第二題"]) == ["first", "second"]


class _FamiTab:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def evaluate(self, script: str):
        self.calls.append(script)
        if len(self.calls) == 1:
            return True
        if len(self.calls) == 2:
            return None
        if len(self.calls) == 3:
            return False
        raise AssertionError("unexpected FamiTicket browser interaction")


@pytest.mark.asyncio
async def test_famiticket_uses_user_dictionary_and_json_safe_field_assignment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    answer = '會員 "VIP" \\ path'
    config = settings.get_default_config()
    config["advanced"]["user_guess_string"] = json.dumps(answer, ensure_ascii=False)
    tab = _FamiTab()

    async def no_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr(famiticket.asyncio, "sleep", no_sleep)

    succeeded, fail_list = await famiticket.nodriver_fami_verify(tab, config)

    assert succeeded is True
    assert fail_list == []
    assert len(tab.calls) == 3
    assert json.dumps(answer, ensure_ascii=False) in tab.calls[1]
    assert "input.value = answer" in tab.calls[1]


@pytest.mark.asyncio
async def test_tixcraft_question_flow_receives_decoded_dictionary_answer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    answer = '會員 "VIP" \\ path'
    config = settings.get_default_config()
    config["advanced"]["user_guess_string"] = json.dumps(answer, ensure_ascii=False)
    captured: dict[str, object] = {}

    async def question_text(_tab, _selector: str, _attribute: str) -> str:
        return "請輸入會員代碼"

    async def fill_form(
        _tab,
        _config,
        inferred_answer: str,
        fail_list: list[str],
        *_args,
    ) -> tuple[bool, list[str]]:
        captured["answer"] = inferred_answer
        return True, [*fail_list, inferred_answer]

    monkeypatch.setattr(tixcraft, "nodriver_get_text_by_selector", question_text)
    monkeypatch.setattr(tixcraft, "write_question_to_file", lambda _text: None)
    monkeypatch.setattr(tixcraft, "nodriver_fill_verify_form", fill_form)

    fail_list = await tixcraft.nodriver_tixcraft_input_check_code(
        object(),
        config,
        [],
        ".zone-verify",
    )

    assert captured["answer"] == answer
    assert fail_list == [answer]


class _KktixTab:
    def __init__(self) -> None:
        self.scripts: list[str] = []

    async def evaluate(self, script: str):
        self.scripts.append(script)
        if len(self.scripts) == 1:
            return {
                "hasQuestion": True,
                "hasInput": True,
                "hasButtons": 1,
                "questionText": "請輸入會員代碼",
            }
        if len(self.scripts) == 2:
            return {"success": True, "value": "ignored", "focused": False}
        raise AssertionError("unexpected KKTIX browser interaction")

    async def sleep(self, _seconds: float) -> None:
        return None


@pytest.mark.asyncio
async def test_kktix_uses_dictionary_and_json_safe_field_assignment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    answer = '會員 "VIP" \\ path'
    config = settings.get_default_config()
    config["advanced"]["user_guess_string"] = json.dumps(answer, ensure_ascii=False)
    tab = _KktixTab()

    async def press_next(_tab, _config) -> bool:
        return True

    monkeypatch.setattr(kktix, "write_question_to_file", lambda _text: None)
    monkeypatch.setattr(kktix, "nodriver_kktix_press_next_button", press_next)

    fail_list, appeared, submitted = await kktix.nodriver_kktix_reg_captcha(
        tab,
        config,
        [],
        object(),
    )

    assert appeared is True
    assert submitted is True
    assert fail_list == [answer]
    assert len(tab.scripts) == 2
    assert f"const answer = {json.dumps(answer)};" in tab.scripts[1]


class _MultiFieldTab:
    def __init__(self) -> None:
        self.scripts: list[str] = []

    async def evaluate(self, script: str):
        self.scripts.append(script)
        if len(self.scripts) == 1:
            return [
                {"index": 0, "context": "會員代碼", "name": "member", "id": "member"},
                {"index": 1, "context": "邀請碼", "name": "invite", "id": "invite"},
            ]
        if len(self.scripts) == 2:
            return {"filledCount": 2, "fieldCount": 2}
        raise AssertionError("unexpected multi-field browser interaction")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "fill_fields",
    [
        ticketplus.nodriver_ticketplus_fill_user_dictionary_fields,
        kham.nodriver_kham_fill_user_dictionary_fields,
    ],
)
async def test_multi_field_platforms_fill_lossless_dictionary_answers(fill_fields) -> None:
    answers = ['會員 "VIP"', "path\\member"]
    config = settings.get_default_config()
    config["advanced"]["user_guess_string"] = util.serialize_user_dictionary_answers(answers)
    tab = _MultiFieldTab()

    assert await fill_fields(tab, config) == 2
    assert len(tab.scripts) == 2
    assert json.dumps(answers, ensure_ascii=False) in tab.scripts[1]


class _PasswordElement:
    def __init__(self) -> None:
        self.keys: list[str] = []

    async def click(self) -> None:
        return None

    async def send_keys(self, value: str) -> None:
        self.keys.append(value)


class _HkTicketingTab:
    def __init__(self) -> None:
        self.element = _PasswordElement()

    async def query_selector(self, _selector: str) -> _PasswordElement:
        return self.element


@pytest.mark.asyncio
async def test_hkticketing_sends_decoded_answer_without_storage_quotes() -> None:
    config = settings.get_default_config()
    config["advanced"]["user_guess_string"] = '"ALPHA","BETA"'
    tab = _HkTicketingTab()

    appeared, fail_list = await hkticketing.nodriver_hkticketing_date_password_input(
        tab,
        config,
        [],
    )

    assert appeared is True
    assert tab.element.keys == ["ALPHA", hkticketing.ENTER_KEY]
    assert fail_list == ["ALPHA"]


class _IbonTarget:
    url = "https://ticket.ibon.com.tw/Vaildate/demo"


class _IbonTab:
    target = _IbonTarget()

    def __init__(self) -> None:
        self.calls: list[str] = []

    async def evaluate(self, script: str):
        self.calls.append(script)
        return (True, "", True, True)[len(self.calls) - 1]


@pytest.mark.asyncio
async def test_ibon_priority_validation_uses_first_decoded_dictionary_answer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = settings.get_default_config()
    config["contact"]["credit_card_prefix"] = ""
    config["advanced"]["user_guess_string"] = '"ALPHA；BETA"'
    tab = _IbonTab()
    ibon._state.clear()

    async def no_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr(ibon.asyncio, "sleep", no_sleep)

    assert await ibon.nodriver_ibon_card_vaildate(tab, config) is True
    assert len(tab.calls) == 4
    assert json.dumps("ALPHA") in tab.calls[2]
    assert "ALPHA；BETA" not in tab.calls[2]


def test_every_text_question_capable_registered_platform_uses_shared_parser() -> None:
    source_root = Path(__file__).resolve().parents[1] / "src" / "platforms"
    eligible = {
        "tixcraft.py",
        "kktix.py",
        "famiticket.py",
        "ibon.py",
        "kham.py",
        "ticketplus.py",
        "hkticketing.py",
    }
    non_question_platforms = {"cityline.py", "funone.py", "fansigo.py"}

    for filename in eligible:
        source = (source_root / filename).read_text(encoding="utf-8")
        assert "get_answer_list_from_user_guess_string(" in source, filename

    for filename in non_question_platforms:
        source = (source_root / filename).read_text(encoding="utf-8")
        assert "user_guess_string" not in source, filename


def test_known_family_homepages_dispatch_to_dictionary_capable_handlers() -> None:
    expected = {
        "https://tixcraft.com": "tixcraft",
        "https://www.teamear.com": "tixcraft",
        "https://www.indievox.com": "tixcraft",
        "https://www.ticketmaster.sg": "tixcraft",
        "https://kktix.com": "kktix",
        "https://www.famiticket.com.tw": "famiticket",
        "https://ticket.ibon.com.tw": "ibon",
        "https://kham.com.tw": "kham",
        "https://ticket.com.tw": "kham",
        "https://tickets.udnfunlife.com": "kham",
        "https://ticketplus.com.tw": "ticketplus",
        "https://hotshow.hkticketing.com": "hkticketing",
        "https://www.galaxymacau.com": "hkticketing",
        "https://www.ticketek.com.au": "hkticketing",
    }

    assert {url: platform_key_for_url(url) for url in expected} == expected


def test_settings_frontend_uses_lossless_user_dictionary_conversion() -> None:
    source = (Path(__file__).resolve().parents[1] / "src" / "www" / "settings.js").read_text(
        encoding="utf-8"
    )

    assert "function format_user_dictionary_for_json" in source
    assert "function format_user_dictionary_for_display" in source
    assert "JSON.stringify(item)" in source
    assert "format_user_dictionary_for_json(user_guess_string.value)" in source
    assert "format_user_dictionary_for_display(settings.advanced.user_guess_string)" in source
