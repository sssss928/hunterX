from __future__ import annotations

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def _source(relative_path: str) -> str:
    return (REPO_ROOT / relative_path).read_text(encoding="utf-8")


def test_kham_force_submit_is_guarded_instead_of_hard_disabled() -> None:
    source = _source("src/platforms/kham.py")

    assert "need 'auto assign seat' feature to enable away_from_keyboard" not in source
    assert "away_from_keyboard_enable = await _kham_can_auto_submit_current_page(tab, config_dict)" in source
    assert '_state.get("kham_auto_submit_clicked", False)' in source


def test_kham_captcha_fill_and_submit_are_event_hardened() -> None:
    source = _source("src/platforms/kham.py")

    assert 'input[name="CHK"]' in source
    assert 'input[id$="CHK"]' in source
    assert "KeyboardEvent('keyup'" in source
    assert "Event('blur'" in source
    assert 'input[id*="AddShopingCart"]' in source
    assert 'button[onclick*="addShoppingCart"]' in source
    assert "nodriver_kham_fill_user_dictionary_fields(tab, config_dict)" in source


def test_ticketplus_next_button_uses_browser_legal_selectors() -> None:
    source = _source("src/platforms/ticketplus.py")

    assert ":contains(" not in source
    assert "function findNextButton()" in source
    assert "const nextTexts = ['下一步', 'Next', '繼續', '確認', '送出'];" in source
    assert "document.querySelectorAll('button, a.v-btn, .v-btn')" in source


def test_ticketplus_and_kham_use_existing_user_dictionary_pipeline() -> None:
    for relative_path in ("src/platforms/ticketplus.py", "src/platforms/kham.py"):
        source = _source(relative_path)

        assert "get_answer_list_from_user_guess_string(config_dict, CONST_MAXBOT_ANSWER_ONLINE_FILE)" in source
        assert "extract_answer_by_question_pattern" in source
        assert "write_question_to_file" in source
        assert "user_guess_string" not in source or "advanced" in source
