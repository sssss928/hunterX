from __future__ import annotations

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def _read_tixcraft_source() -> str:
    return (REPO_ROOT / "src" / "platforms" / "tixcraft.py").read_text(
        encoding="utf-8",
        errors="replace",
    )


def test_tixcraft_ticket_quantity_selectors_are_still_supported() -> None:
    source = _read_tixcraft_source()

    assert ".mobile-select" in source
    assert 'select[id*="TicketForm_ticketPrice_"]' in source
    assert "allow_less_tickets" in source


def test_tixcraft_captcha_agreement_and_submit_flow_is_still_present() -> None:
    source = _read_tixcraft_source()

    assert "#TicketForm_verifyCode" in source
    assert "#TicketForm_agree" in source
    assert "nodriver_check_checkbox_enhanced(tab, '#TicketForm_agree')" in source
    assert 'dispatch_key_event("keyDown", code="Enter", key="Enter"' in source
    assert 'dispatch_key_event("keyUp", code="Enter", key="Enter"' in source


def test_tixcraft_submit_still_requires_ticket_captcha_and_agreement_ready() -> None:
    source = _read_tixcraft_source()

    assert "ticketOk" in source
    assert "verify && verify.value.length === 4" in source
    assert "agree && agree.checked" in source
    assert "ready: ticketOk &&" in source
