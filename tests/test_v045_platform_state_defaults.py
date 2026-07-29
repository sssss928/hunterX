from __future__ import annotations

import re
from pathlib import Path

import platforms.cityline as cityline
import platforms.famiticket as famiticket
import platforms.fansigo as fansigo
import platforms.funone as funone
import platforms.hkticketing as hkticketing
import platforms.kham as kham
import platforms.kktix as kktix
import platforms.ticketplus as ticketplus
import platforms.tixcraft as tixcraft


DIRECT_STATE_KEY_RE = re.compile(r"(?<![A-Za-z0-9_])_state\[([\"'])(.*?)\1\]")


def _direct_state_keys(platform_name: str) -> set[str]:
    source = Path(f"src/platforms/{platform_name}.py").read_text(encoding="utf-8")
    return {match.group(2) for match in DIRECT_STATE_KEY_RE.finditer(source)}


def test_platform_state_default_helpers_backfill_partial_state() -> None:
    cases = [
        (cityline, cityline._ensure_cityline_state_defaults, cityline._cityline_state_defaults, "account_assigned", True),
        (
            famiticket,
            famiticket._ensure_famiticket_state_defaults,
            famiticket._famiticket_state_defaults,
            "last_activity",
            "keep-me",
        ),
        (fansigo, fansigo._ensure_fansigo_state_defaults, fansigo._fansigo_state_defaults, "is_cookie_injected", True),
        (funone, funone._ensure_funone_state_defaults, funone._funone_state_defaults, "refresh_retry_count", 7),
        (
            hkticketing,
            hkticketing._ensure_hkticketing_state_defaults,
            hkticketing._hkticketing_state_defaults,
            "shown_checkout_message",
            True,
        ),
        (kham, kham._ensure_kham_state_defaults, kham._kham_state_defaults, "is_popup_checkout", True),
        (kktix, kktix._ensure_kktix_state_defaults, kktix._kktix_state_defaults, "elapsed_time", 1.25),
        (
            ticketplus,
            ticketplus._ensure_ticketplus_state_defaults,
            ticketplus._ticketplus_state_defaults,
            "is_ticket_assigned",
            True,
        ),
        (tixcraft, tixcraft._ensure_tixcraft_state_defaults, tixcraft._tixcraft_state_defaults, "elapsed_time", 1.25),
    ]

    for module, ensure_defaults, defaults_factory, existing_key, existing_value in cases:
        module._state.clear()
        module._state[existing_key] = existing_value

        ensure_defaults()

        assert module._state[existing_key] == existing_value
        assert set(defaults_factory()).issubset(module._state)


def test_literal_direct_state_keys_have_defaults() -> None:
    cases = {
        "cityline": cityline._cityline_state_defaults,
        "famiticket": famiticket._famiticket_state_defaults,
        "fansigo": fansigo._fansigo_state_defaults,
        "funone": funone._funone_state_defaults,
        "hkticketing": hkticketing._hkticketing_state_defaults,
        "kham": kham._kham_state_defaults,
        "kktix": kktix._kktix_state_defaults,
        "ticketplus": ticketplus._ticketplus_state_defaults,
        "tixcraft": tixcraft._tixcraft_state_defaults,
    }
    runtime_tixcraft_keys = {"action_ledger", "submit_guard", "leak_scheduler"}

    for platform_name, defaults_factory in cases.items():
        expected_keys = set(defaults_factory())
        if platform_name == "tixcraft":
            expected_keys.update(runtime_tixcraft_keys)

        missing = _direct_state_keys(platform_name) - expected_keys

        assert missing == set(), f"{platform_name} direct _state keys missing defaults: {sorted(missing)}"


def test_state_defaults_do_not_share_mutable_values() -> None:
    mutable_defaults = [
        (famiticket._famiticket_state_defaults, "fail_list"),
        (funone._funone_state_defaults, "fail_list"),
        (hkticketing._hkticketing_state_defaults, "fail_list"),
        (kktix._kktix_state_defaults, "fail_list"),
        (ticketplus._ticketplus_state_defaults, "fail_list"),
        (tixcraft._tixcraft_state_defaults, "fail_list"),
        (tixcraft._tixcraft_state_defaults, "fail_promo_list"),
        (tixcraft._tixcraft_state_defaults, "selected_area_metadata"),
        (tixcraft._tixcraft_state_defaults, "event_metadata_cache"),
        (tixcraft._tixcraft_state_defaults, "notification_retry_at"),
    ]

    for defaults_factory, key in mutable_defaults:
        assert defaults_factory()[key] is not defaults_factory()[key]


def test_kham_udn_login_values_are_json_escaped_in_javascript() -> None:
    source = Path("src/platforms/kham.py").read_text(encoding="utf-8")

    assert 'emailInput.value = "{udn_account}";' not in source
    assert 'passInput.value = "{udn_password}";' not in source
    assert "emailInput.value = {udn_account_js};" in source
    assert "passInput.value = {udn_password_js};" in source
