"""Tab-scoped navigation intent used by login and safe recovery paths."""

from __future__ import annotations

import time
from dataclasses import dataclass
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


DEFAULT_TARGET_TTL_SECONDS = 30 * 60.0
IGNORED_QUERY_KEYS = frozenset({"_", "fbclid", "gclid", "timestamp", "ts"})


def canonicalize_target_url(url: str) -> str:
    """Canonicalize routing noise without collapsing distinct event queries."""

    try:
        parts = urlsplit(str(url or "").strip())
    except ValueError:
        return ""
    if parts.scheme.casefold() not in {"http", "https"} or not parts.hostname:
        return ""
    host = parts.hostname.casefold().rstrip(".")
    if parts.port is not None:
        host = f"{host}:{parts.port}"
    path = parts.path.rstrip("/") or "/"
    query = [
        (key, value)
        for key, value in parse_qsl(parts.query, keep_blank_values=True)
        if key.casefold() not in IGNORED_QUERY_KEYS
        and not key.casefold().startswith("utm_")
    ]
    return urlunsplit(
        (
            parts.scheme.casefold(),
            host,
            path,
            urlencode(sorted(query)),
            "",
        )
    )


@dataclass(frozen=True)
class NavigationIntent:
    target_url: str
    normalized_target_url: str
    platform: str
    event_id: str
    mode: str
    instance_id: str
    tab_identity: str
    created_at: float
    expires_at: float
    config_generation: int
    reason: str

    @classmethod
    def create(
        cls,
        target_url: str,
        *,
        platform: str,
        event_id: str,
        mode: str,
        instance_id: str,
        tab_identity: str,
        config_generation: int,
        reason: str,
        now: float | None = None,
        ttl_seconds: float = DEFAULT_TARGET_TTL_SECONDS,
    ) -> NavigationIntent:
        created_at = time.monotonic() if now is None else float(now)
        return cls(
            target_url=str(target_url or ""),
            normalized_target_url=canonicalize_target_url(target_url),
            platform=str(platform or "").casefold(),
            event_id=str(event_id or ""),
            mode=str(mode or "onsale"),
            instance_id=str(instance_id or "default"),
            tab_identity=str(tab_identity or ""),
            created_at=created_at,
            expires_at=created_at + max(0.0, float(ttl_seconds)),
            config_generation=max(0, int(config_generation)),
            reason=str(reason or "navigation"),
        )

    def is_expired(self, now: float | None = None) -> bool:
        current = time.monotonic() if now is None else float(now)
        return current >= self.expires_at


@dataclass
class TargetContext:
    """Mutable bounded restore bookkeeping attached to PlatformRuntimeState."""

    intent: NavigationIntent
    restore_attempts: int = 0
    next_restore_at: float = 0.0
    restored_at: float | None = None
    last_error: str = ""

    def can_restore(
        self,
        *,
        max_attempts: int,
        now: float | None = None,
    ) -> bool:
        current = time.monotonic() if now is None else float(now)
        return bool(
            self.intent.normalized_target_url
            and self.restored_at is None
            and not self.intent.is_expired(current)
            and self.restore_attempts < max(0, int(max_attempts))
            and current >= self.next_restore_at
        )

    def record_restore(
        self,
        success: bool,
        *,
        retry_seconds: float,
        error: str = "",
        now: float | None = None,
    ) -> None:
        current = time.monotonic() if now is None else float(now)
        self.restore_attempts += 1
        self.last_error = str(error or "")[:160]
        if success:
            self.restored_at = current
            self.next_restore_at = 0.0
        else:
            self.next_restore_at = current + max(0.0, float(retry_seconds))
