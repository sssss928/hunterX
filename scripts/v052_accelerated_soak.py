#!/usr/bin/env python3
"""Deterministic 100,000-cycle lifecycle soak without external services."""

from __future__ import annotations

import argparse
import json
import sys
import time
import tracemalloc
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from platform_adapters import adapter_for_key
from platform_engine import PlatformEngine


ROUTES = (
    ("tixcraft", "https://tixcraft.com/ticket/area/event/1", "https://tixcraft.com/ticket/checkout/event"),
    ("kktix", "https://demo.kktix.cc/events/event/registrations/new", "https://demo.kktix.cc/orders/1"),
    ("ticketplus", "https://ticketplus.com.tw/order/event/session", "https://ticketplus.com.tw/confirm/event/session"),
    ("ibon", "https://ticket.ibon.com.tw/ActivityInfo/Details/1", "https://ticket.ibon.com.tw/Order/Checkout/1"),
    ("kham", "https://kham.com.tw/application/UTK02/UTK0201_.aspx?PRODUCT_ID=1", "https://kham.com.tw/application/UTK02/UTK0206_.aspx?ORDER_ID=1"),
    ("famiticket", "https://www.famiticket.com.tw/Activity/Info/1", "https://www.famiticket.com.tw/Order/Checkout/1"),
    ("funone", "https://tickets.funone.io/events/1", "https://tickets.funone.io/orders/1"),
    ("fansigo", "https://go.fansi.me/events/1", "https://go.fansi.me/orders/1"),
    ("cityline", "https://www.cityline.com/Events.html?id=1", "https://www.cityline.com/checkout/1"),
    ("hkticketing", "https://premier.hkticketing.com/events/1", "https://premier.hkticketing.com/payment/1"),
)


class _Tab:
    def __init__(self, identity: str) -> None:
        self.target = type("Target", (), {"target_id": identity})()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cycles", type=int, default=100_000)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    engines = [PlatformEngine() for _ in range(3)]
    tabs = [[_Tab(f"instance-{i}-platform-{j}") for j in range(len(ROUTES))] for i in range(3)]
    duplicate_claims = 0
    started = time.perf_counter()
    tracemalloc.start()
    for cycle in range(args.cycles):
        instance_index = cycle % 3
        route_index = cycle % len(ROUTES)
        engine = engines[instance_index]
        tab = tabs[instance_index][route_index]
        key, safe, protected = ROUTES[route_index]
        config = {"advanced": {"run_mode": "leak_watch" if cycle % 2 else "onsale"}}
        decision = engine.before_dispatch(tab, safe, config)
        if not decision.automation_allowed:
            raise RuntimeError(f"attempt failed to rearm at cycle {cycle}")
        token = engine.claim_submit(tab, key, owner="accelerated_soak")
        duplicate_claims += int(bool(token and engine.claim_submit(tab, key, owner="duplicate")))
        engine.before_dispatch(tab, protected, config)
        engine.mark_attempt_completed(tab, key, reason="accelerated_success")
    _current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    payload = {
        "status": "PASS" if duplicate_claims == 0 else "FAIL",
        "cycles": args.cycles,
        "instances": 3,
        "platforms": len(ROUTES),
        "duplicate_submit_claims": duplicate_claims,
        "state_count": sum(engine.state_count for engine in engines),
        "refresh_owner_count": sum(engine.refresh_coordinator_count for engine in engines),
        "peak_traced_bytes": peak,
        "elapsed_seconds": time.perf_counter() - started,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(json.dumps(payload, indent=2))
    return 0 if payload["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
