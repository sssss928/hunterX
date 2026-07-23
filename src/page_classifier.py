from __future__ import annotations

from enum import Enum


class PageClass(str, Enum):
    UNKNOWN = "unknown"
    HOME = "home"
    ACTIVITY = "activity"
    DATE = "date"
    AREA = "area"
    TICKET = "ticket"
    ORDER = "order"
    CHECKOUT = "checkout"
    PAYMENT = "payment"
    QUEUE = "queue"
    SOLD_OUT = "sold_out"
    REJECTED_ERROR = "rejected_error"
    CANCELED_ORDER = "canceled_order"
    CONTINUE_SHOPPING = "continue_shopping"


def classify_page(url: str = "", text: str = "") -> PageClass:
    url_lower = (url or "").lower()
    text_lower = (text or "").lower()

    if "queue-it.net" in url_lower or "queue" in url_lower:
        return PageClass.QUEUE
    if "/ticket/checkout" in url_lower or "/checkout" in url_lower:
        return PageClass.CHECKOUT
    if "/ticket/order" in url_lower or "/order" in url_lower:
        return PageClass.ORDER
    if "/payment" in url_lower or "ecpay" in url_lower:
        return PageClass.PAYMENT
    if "/ticket/ticket/" in url_lower or "/ticket/check-captcha/" in url_lower:
        return PageClass.TICKET
    if "/ticket/area/" in url_lower:
        return PageClass.AREA
    if "/activity/game/" in url_lower:
        return PageClass.DATE
    if "/activity/detail/" in url_lower:
        return PageClass.ACTIVITY
    if url_lower.rstrip("/") in {
        "https://tixcraft.com",
        "https://tixcraft.com/activity",
        "https://kktix.com",
        "https://ticketplus.com.tw",
    }:
        return PageClass.HOME

    retryable_text = (
        "e0024",
        "sold out",
        "unavailable",
        "not enough tickets",
        "insufficient",
        "no ticket",
        "已售完",
        "已無足夠",
        "不足票數",
        "票數不足",
        "剩餘票券不足",
        "無足夠",
        "沒有足夠",
        "目前無法取得",
        "暫無可售",
        "無可售",
        "請重新選擇",
        "請返回重新選擇",
        "請重新選取",
        "請重新訂購",
        "選購一空",
        "票券已被選購一空",
        "票券已售完",
        "座位已售出",
        "座位已被選走",
    )
    if any(item in text_lower for item in retryable_text):
        return PageClass.REJECTED_ERROR
    if "取消訂單" in text_lower or "order canceled" in text_lower or "cancelled" in text_lower:
        return PageClass.CANCELED_ORDER
    if "繼續選購" in text_lower or "continue shopping" in text_lower:
        return PageClass.CONTINUE_SHOPPING
    return PageClass.UNKNOWN


def is_protected_after_ticket(page_class: PageClass | str) -> bool:
    page = PageClass(page_class)
    return page in {PageClass.TICKET, PageClass.ORDER, PageClass.CHECKOUT, PageClass.PAYMENT}
