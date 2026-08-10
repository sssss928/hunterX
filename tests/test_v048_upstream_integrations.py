from __future__ import annotations

import json

import pytest

from platforms import kktix
from platforms import cityline
from tab_ownership import close_owned_tab, is_owned_tab, register_owned_tab


class QualificationTab:
    def __init__(self) -> None:
        self.selected = False
        self.member_filled = False
        self.next_clicks = 0
        self.scripts: list[str] = []

    async def evaluate(self, script: str):
        self.scripts.append(script)
        if script == kktix.CONST_KKTIX_FORM_STATE_JS:
            return {
                "scope": "ticket-unit",
                "hasTicket": True,
                "ticketCount": 2,
                "agreed": True,
                "qualification": {
                    "status": (
                        "satisfied"
                        if self.selected and self.member_filled
                        else "pending"
                    ),
                    "label": "會員資格",
                    "selectedIndex": 0 if self.selected else -1,
                    "options": [
                        {
                            "index": 0,
                            "type": "member_code",
                            "disabled": False,
                            "checked": self.selected,
                            "codeFilled": self.member_filled,
                        }
                    ],
                    "invitationPending": False,
                },
                "ready": self.selected and self.member_filled,
            }
        if "radio.click()" in script:
            self.selected = True
            return {"clicked": True, "reason": ""}
        if "member-code:not([disabled])" in script:
            return self.selected
        if "const memberCode =" in script:
            assert "selectedScope.querySelectorAll" in script
            self.member_filled = True
            return {"success": True, "filledCount": 1}
        if "register-new-next-button-area" in script:
            self.next_clicks += 1
            return {
                "success": True,
                "clicked": True,
                "buttonText": "下一步",
                "buttonCount": 1,
            }
        if script == "window.location.href":
            return "https://demo.kktix.cc/events/demo/registrations/new"
        raise AssertionError(f"unexpected KKTIX script: {script[:100]}")

    async def sleep(self, _seconds: float) -> None:
        return None

    async def send(self, *_args, **_kwargs):
        raise RuntimeError("no dialog")


def _config() -> dict:
    return {
        "advanced": {"discount_code": "member-'code", "verbose": False},
        "kktix": {"auto_press_next_step_button": True},
    }


@pytest.mark.asyncio
async def test_kktix_qualification_sequence_is_scoped_fill_then_submit(
    monkeypatch,
) -> None:
    tab = QualificationTab()
    monkeypatch.setattr(
        kktix,
        "check_and_handle_pause",
        lambda *_args, **_kwargs: _async_result(False),
    )

    assert await kktix.nodriver_kktix_handle_qualification_and_next(
        tab,
        _config(),
    )
    assert tab.selected is True
    assert tab.member_filled is True
    assert tab.next_clicks == 1

    select_index = next(i for i, value in enumerate(tab.scripts) if "radio.click()" in value)
    fill_index = next(i for i, value in enumerate(tab.scripts) if "const memberCode =" in value)
    submit_index = next(
        i
        for i, value in enumerate(tab.scripts)
        if "register-new-next-button-area" in value
    )
    assert select_index < fill_index < submit_index
    assert "for (const unit of document.querySelectorAll('.ticket-unit'))" in (
        tab.scripts[select_index]
    )
    assert json.dumps("member-'code") in tab.scripts[fill_index]


async def _async_result(value):
    return value


@pytest.mark.asyncio
async def test_kktix_invitation_qualification_fails_closed_without_submit() -> None:
    class InvitationTab(QualificationTab):
        async def evaluate(self, script: str):
            if script == kktix.CONST_KKTIX_FORM_STATE_JS:
                return {
                    "scope": "ticket-unit",
                    "hasTicket": True,
                    "ticketCount": 1,
                    "agreed": True,
                    "qualification": {
                        "status": "pending",
                        "label": "邀請碼",
                        "selectedIndex": -1,
                        "options": [],
                        "invitationPending": True,
                    },
                    "ready": False,
                }
            return await super().evaluate(script)

    tab = InvitationTab()
    assert not await kktix.nodriver_kktix_handle_qualification_and_next(
        tab,
        _config(),
    )
    assert tab.next_clicks == 0


def test_kktix_form_state_script_scopes_dynamic_fields_to_selected_unit() -> None:
    script = kktix.CONST_KKTIX_FORM_STATE_JS
    assert "readQuantity(unit) > 0" in script
    assert "scope.querySelector('div.code-input')" in script
    assert "qualification.status" in script
    assert "invitationPending" in script


class SigninDiagnosticTab:
    def __init__(self, snapshot=None, error=None) -> None:
        self.snapshot = snapshot
        self.error = error

    async def evaluate(self, _script: str):
        if self.error is not None:
            raise self.error
        return self.snapshot


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("snapshot", "expected"),
    [
        (
            {
                "url": "https://kktix.com/users/sign_in",
                "queue": True,
                "challenge": False,
            },
            "waiting_room",
        ),
        (
            {
                "url": "https://kktix.com/users/sign_in",
                "queue": False,
                "challenge": True,
            },
            "challenge_manual",
        ),
        (
            {
                "url": "https://kktix.com/users/sign_in",
                "queue": False,
                "challenge": False,
                "hasAccount": False,
                "hasPassword": False,
                "hasSubmit": True,
            },
            "fields_not_loaded",
        ),
        (
            {
                "url": "https://kktix.com/users/sign_in",
                "queue": False,
                "challenge": False,
                "hasAccount": True,
                "hasPassword": True,
                "hasSubmit": False,
            },
            "submit_selector_missing",
        ),
        (
            {
                "url": "https://kktix.com/users/sign_in",
                "queue": False,
                "challenge": False,
                "hasAccount": True,
                "hasPassword": True,
                "hasSubmit": True,
            },
            "ready",
        ),
    ],
)
async def test_kktix_signin_diagnostic_is_explicit_and_fail_closed(
    snapshot,
    expected,
) -> None:
    result = await kktix.nodriver_kktix_signin_diagnostic(
        SigninDiagnosticTab(snapshot),
    )
    assert result["state"] == expected


@pytest.mark.asyncio
async def test_kktix_signin_diagnostic_reports_connection_failure() -> None:
    result = await kktix.nodriver_kktix_signin_diagnostic(
        SigninDiagnosticTab(error=ConnectionError("websocket closed")),
    )
    assert result == {
        "state": "connection_error",
        "error_type": "ConnectionError",
    }


class OwnedTab:
    def __init__(self, url: str = "https://example.invalid") -> None:
        self.target = type("Target", (), {"url": url})()
        self.close_count = 0
        self.activate_count = 0
        self.browser = None

    async def close(self) -> None:
        self.close_count += 1

    async def activate(self) -> None:
        self.activate_count += 1


@pytest.mark.asyncio
async def test_unowned_user_tab_cannot_be_closed() -> None:
    tab = OwnedTab()
    assert is_owned_tab(tab) is False
    assert await close_owned_tab(tab) is False
    assert tab.close_count == 0

    register_owned_tab(tab, "fixture_bot_click")
    assert is_owned_tab(tab) is True
    assert await close_owned_tab(tab) is True
    assert tab.close_count == 1
    assert is_owned_tab(tab) is False


@pytest.mark.asyncio
async def test_cityline_ignores_preexisting_user_tab(monkeypatch) -> None:
    current = OwnedTab("https://shows.cityline.com/event")
    user_tab = OwnedTab("https://example.com/user-tab")
    browser = type("Browser", (), {"tabs": [current, user_tab]})()
    current.browser = browser
    user_tab.browser = browser
    cityline._state.clear()
    monkeypatch.setattr(cityline.asyncio, "sleep", _async_result)

    selected = await cityline.nodriver_cityline_close_second_tab(
        current,
        current.target.url,
    )
    assert selected is current
    assert current.close_count == 0
    assert user_tab.close_count == 0
    assert user_tab.activate_count == 0
