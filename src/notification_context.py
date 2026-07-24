from __future__ import annotations

import re
from dataclasses import dataclass


STAGE_TEXT = {
    "ticket": "已找到可購買票券",
    "ticket_found": "已找到可購買票券",
    "order": "訂單建立中﹍",
    "order_pending": "訂單建立中﹍",
    "checkout_reached": "已進入結帳畫面，請立即付款！",
    "payment_reached": "已進入結帳畫面，請立即付款！",
    "rejected_recovering": "票券狀態異常，重新選區中",
    "sold_out_retrying": "目前區域已售完，重新嘗試中",
    "soft_block_waiting": "偵測到暫時鎖定，等待後重試",
}

GENERIC_EVENT_NAMES = {
    "選擇區域:",
    "選擇票券:",
    "地區",
    "區域",
    "票區",
    "日期",
    "場次",
    "訂單",
    "結帳",
    "付款",
}

PLATFORM_SUFFIXES = (
    "| tixcraft拓元售票",
    "| tixcraft",
    "| ticketmaster",
    "| KKTIX",
    "| TicketPlus",
    "| iBon",
    "| KHAM",
    "| Cityline",
    "| FunOne",
    "| HKTicketing",
    "| FamiTicket",
)


def redact_sensitive_text(text: str | None) -> str:
    if text is None:
        return ""
    value = str(text)
    value = re.sub(
        r"(https://(?:discord(?:app)?\.com)/api/webhooks/)[^\s\"']+",
        r"\1***",
        value,
        flags=re.IGNORECASE,
    )
    value = re.sub(
        r"\b(TIXUISID|IVUISID|TIXPUISID|cookie|token|password|authorization)(\s*[:=]\s*)[^\s,;\"']+",
        r"\1\2***",
        value,
        flags=re.IGNORECASE,
    )
    return value


def clean_event_name(value: str | None, fallback: str = "Unknown Event") -> str:
    text = redact_sensitive_text(value).strip()
    if not text:
        return fallback
    for prefix in ("選擇區域:", "選擇票券:"):
        if text.startswith(prefix):
            text = text[len(prefix):].strip()
    for suffix in PLATFORM_SUFFIXES:
        if text.lower().endswith(suffix.lower()):
            text = text[: -len(suffix)].strip()
    text = re.sub(r"\s+", " ", text).strip()
    if text in GENERIC_EVENT_NAMES or text.rstrip(":：") in GENERIC_EVENT_NAMES:
        return fallback
    return text or fallback


def clean_seat_area(value: str | None, fallback: str = "Unknown Area") -> str:
    text = redact_sensitive_text(value).strip()
    if not text:
        return fallback
    lines = []
    for raw_line in text.splitlines() or [text]:
        line = raw_line.strip()
        line = re.sub(r"\s*(剩餘|剩余)\s*\d+\s*$", "", line)
        line = re.sub(r"\s*(已售完|熱賣中|热卖中|sold out|unavailable)\s*$", "", line, flags=re.IGNORECASE)
        line = re.sub(r"\s*(?:票價\s*)?(?:NT\$|TWD|\$)\s*[\d,]+\s*$", "", line, flags=re.IGNORECASE)
        line = re.sub(r"\s+", " ", line).strip()
        if line:
            lines.append(line)
    return "\n".join(lines) if lines else fallback


_NUMBER_EMOJI = {
    1: "1️⃣",
    2: "2️⃣",
    3: "3️⃣",
    4: "4️⃣",
    5: "5️⃣",
    6: "6️⃣",
    7: "7️⃣",
    8: "8️⃣",
    9: "9️⃣",
    10: "🔟",
}

_NUMBERED_ROW_PREFIX = re.compile(r"^(?:(?:[1-9]\ufe0f?\u20e3)|🔟|\d+[.)、])\s*")


def format_seat_rows(value: str | list[str] | tuple[str, ...] | None, fallback: str = "-") -> str:
    if value is None:
        return fallback
    if isinstance(value, (list, tuple)):
        raw_lines = [str(item) for item in value]
    else:
        raw_lines = str(value).splitlines()
    lines = []
    seat_index = 0
    for raw_line in raw_lines:
        line = redact_sensitive_text(raw_line).strip()
        if not line:
            continue
        if line in {"-", "訂單建立中﹍", "訂單建立中"}:
            lines.append(line)
            continue
        seat_index += 1
        numbered_prefix = _NUMBERED_ROW_PREFIX.match(line)
        if numbered_prefix:
            prefix = numbered_prefix.group(0).strip()
            content = line[numbered_prefix.end():].strip()
            lines.append(f"{prefix} {content}".strip())
            continue
        prefix = _NUMBER_EMOJI.get(seat_index, f"{seat_index}.")
        lines.append(f"{prefix} {line}")
    return "\n".join(lines) if lines else fallback


@dataclass
class NotificationContext:
    platform: str = "Unknown Platform"
    event_name: str = "Unknown Event"
    ticket_count: str = "-"
    seat_area: str = "Unknown Area"
    seat_rows: str = "-"
    stage: str = "-"
    current_url: str = ""
    page_class: str = ""
    last_valid_area_url: str = ""

    def normalized(self) -> "NotificationContext":
        return NotificationContext(
            platform=redact_sensitive_text(self.platform or "Unknown Platform") or "Unknown Platform",
            event_name=clean_event_name(self.event_name, "Unknown Event"),
            ticket_count=redact_sensitive_text(str(self.ticket_count or "-")) or "-",
            seat_area=clean_seat_area(self.seat_area, "Unknown Area"),
            seat_rows=format_seat_rows(self.seat_rows, "-"),
            stage=STAGE_TEXT.get(self.stage, redact_sensitive_text(self.stage or "-") or "-"),
            current_url=redact_sensitive_text(self.current_url or ""),
            page_class=redact_sensitive_text(self.page_class or ""),
            last_valid_area_url=redact_sensitive_text(self.last_valid_area_url or ""),
        )

    def as_placeholders(self) -> dict[str, str]:
        ctx = self.normalized()
        return {
            "platform": ctx.platform,
            "event_name": ctx.event_name,
            "ticket_count": ctx.ticket_count,
            "seat_area": ctx.seat_area,
            "seat_rows": ctx.seat_rows,
            "stage": ctx.stage,
        }

    def format_message(self, custom_message: str | None = None) -> str:
        values = self.as_placeholders()
        if custom_message:
            try:
                return redact_sensitive_text(custom_message.format(**values))
            except Exception:
                return redact_sensitive_text(custom_message)
        return (
            f"🎫 [{values['platform']}]\n\n"
            f"活動：{values['event_name']}\n"
            f"票數：{values['ticket_count']}\n"
            f"區域： {values['seat_area']}\n"
            f"排數：\n{values['seat_rows']}\n"
            f"狀態：{values['stage']}"
        )


def make_notification_context(
    platform: str = "Unknown Platform",
    stage: str = "-",
    event_name: str = "Unknown Event",
    ticket_count: str | int = "-",
    seat_area: str = "Unknown Area",
    seat_rows: str | list[str] | tuple[str, ...] = "-",
    current_url: str = "",
    page_class: str = "",
    last_valid_area_url: str = "",
) -> NotificationContext:
    return NotificationContext(
        platform=platform or "Unknown Platform",
        event_name=event_name or "Unknown Event",
        ticket_count=str(ticket_count) if ticket_count not in (None, "") else "-",
        seat_area=seat_area or "Unknown Area",
        seat_rows=seat_rows or "-",
        stage=stage or "-",
        current_url=current_url or "",
        page_class=page_class or "",
        last_valid_area_url=last_valid_area_url or "",
    )
