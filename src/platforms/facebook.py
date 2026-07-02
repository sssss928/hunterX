#!/usr/bin/env python3
# encoding=utf-8
"""Facebook login helper.

This module only fills the configured account/password fields and submits the
login form. It does not handle captcha, platform risk controls, or any bypass
behavior.
"""

from __future__ import annotations

import logging
from typing import Any, Mapping

from zendriver import cdp

from platforms.common_async import bounded_poll


LOGGER = logging.getLogger(__name__)

__all__ = [
    "nodriver_facebook_login",
    "nodriver_facebook_main",
]


async def _query_selector(tab: Any, selector: str) -> Any | None:
    return await tab.query_selector(selector)


async def _send_enter(tab: Any) -> None:
    await tab.send(
        cdp.input_.dispatch_key_event(
            "keyDown",
            code="Enter",
            key="Enter",
            text="\r",
            windows_virtual_key_code=13,
        )
    )
    await tab.send(
        cdp.input_.dispatch_key_event(
            "keyUp",
            code="Enter",
            key="Enter",
            text="\r",
            windows_virtual_key_code=13,
        )
    )


async def nodriver_facebook_login(tab: Any, facebook_account: str, facebook_password: str) -> bool:
    """Fill and submit the Facebook login form.

    Returns:
        True when both fields were found and submit events were sent; False for
        missing inputs, missing credentials, or transient browser errors.
    """
    if tab is None:
        LOGGER.warning("Facebook login skipped: browser tab is not available")
        return False

    if not facebook_account or not facebook_password:
        LOGGER.info("Facebook login skipped: account or password is empty")
        return False

    try:
        account = await bounded_poll(
            lambda: _query_selector(tab, "#email"),
            timeout=5.0,
            interval=0.5,
            description="Facebook account input",
        )
        if account is None:
            LOGGER.warning("Facebook account input not found")
            return False

        password = await bounded_poll(
            lambda: _query_selector(tab, "#pass"),
            timeout=5.0,
            interval=0.5,
            description="Facebook password input",
        )
        if password is None:
            LOGGER.warning("Facebook password input not found")
            return False

        await account.send_keys(facebook_account)
        await password.send_keys(facebook_password)
        await _send_enter(tab)
        return True
    except Exception:
        LOGGER.exception("Facebook login failed")
        return False


async def nodriver_facebook_main(tab: Any, config_dict: Mapping[str, Any]) -> bool:
    accounts = config_dict.get("accounts", {})
    if not isinstance(accounts, Mapping):
        LOGGER.warning("Facebook login skipped: accounts config is invalid")
        return False

    facebook_account = str(accounts.get("facebook_account", "")).strip()
    facebook_password = str(accounts.get("facebook_password", "")).strip()
    if len(facebook_account) <= 4:
        return False

    return await nodriver_facebook_login(tab, facebook_account, facebook_password)
