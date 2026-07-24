from __future__ import annotations

from enum import Enum

from page_classifier import PageClass


class RuntimeCommand(str, Enum):
    CONTINUE = "CONTINUE"
    RECOVER_TO_AREA = "RECOVER_TO_AREA"
    STOP_AUTOMATION_KEEP_BROWSER = "STOP_AUTOMATION_KEEP_BROWSER"
    QUIT_BROWSER = "QUIT_BROWSER"
    MANUAL_INTERVENTION_KEEP_BROWSER = "MANUAL_INTERVENTION_KEEP_BROWSER"


class RuntimeEvent(str, Enum):
    NONE = "none"
    USER_STOP = "stop_clicked"
    USER_QUIT = "quit_clicked"
    RETRYABLE_REJECTED = "retryable_rejected"
    UNKNOWN_ALERT = "unknown_alert"
    CANCELED_ORDER = "canceled_order_detected"
    CONTINUE_SHOPPING = "continue_shopping_detected"
    HOMEPAGE_REDIRECTED = "homepage_redirected_while_running"
    ACTIVITY_OR_DATE_REDIRECTED = "activity_or_date_redirected_while_running"


class RuntimeController:
    """Maps runtime events to commands. Only explicit user quit closes browser."""

    def decide(
        self,
        event: RuntimeEvent | str = RuntimeEvent.NONE,
        page_class: PageClass | str = PageClass.UNKNOWN,
        running: bool = True,
    ) -> RuntimeCommand:
        event = RuntimeEvent(event)
        page_class = PageClass(page_class)

        if event == RuntimeEvent.USER_QUIT:
            return RuntimeCommand.QUIT_BROWSER
        if event == RuntimeEvent.USER_STOP:
            return RuntimeCommand.STOP_AUTOMATION_KEEP_BROWSER
        if event == RuntimeEvent.UNKNOWN_ALERT:
            return RuntimeCommand.MANUAL_INTERVENTION_KEEP_BROWSER
        if event in {
            RuntimeEvent.RETRYABLE_REJECTED,
            RuntimeEvent.CANCELED_ORDER,
            RuntimeEvent.CONTINUE_SHOPPING,
        }:
            return RuntimeCommand.RECOVER_TO_AREA
        if running and event in {
            RuntimeEvent.HOMEPAGE_REDIRECTED,
            RuntimeEvent.ACTIVITY_OR_DATE_REDIRECTED,
        }:
            return RuntimeCommand.RECOVER_TO_AREA
        if page_class in {PageClass.CANCELED_ORDER, PageClass.CONTINUE_SHOPPING, PageClass.REJECTED_ERROR}:
            return RuntimeCommand.RECOVER_TO_AREA
        return RuntimeCommand.CONTINUE
