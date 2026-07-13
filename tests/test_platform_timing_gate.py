from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path


def test_refresh_datetime_gate_platform_aware_deadline_behavior() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    env = dict(os.environ)
    env["PYTHONPATH"] = str(repo_root / "src")
    script = textwrap.dedent(
        r"""
        import asyncio
        import json
        from datetime import datetime, timedelta

        from nodriver_tixcraft import check_refresh_datetime_gate


        class FakeTab:
            def __init__(self):
                self.reload_count = 0

            async def reload(self):
                self.reload_count += 1


        def state():
            return {
                "state_key": "",
                "target_str": "",
                "reached": False,
                "last_countdown_print": 0,
                "reported_plan": False,
            }


        def config(target, advanced_delay_mode="auto", interval=5):
            return {
                "refresh_datetime": target.strftime("%Y/%m/%d %H:%M:%S.%f")[:-3],
                "refresh_calibration": {
                    "enable": True,
                    "advanced_delay_mode": advanced_delay_mode,
                    "clock_offset_ms": 0,
                    "frontend_delay_ms": 120,
                    "network_uplink_ms": 120,
                    "scheduler_jitter_ms": 0,
                    "safety_margin_ms": 0,
                    "freeze_before_seconds": 10,
                },
                "advanced": {"auto_reload_page_interval": interval},
            }


        async def run():
            results = {}

            target = datetime.now() + timedelta(milliseconds=90)
            tab = FakeTab()
            st = state()
            first = await check_refresh_datetime_gate(tab, config(target), st, "https://tixcraft.com/activity/detail/abc")
            await asyncio.sleep(0.12)
            second = await check_refresh_datetime_gate(tab, config(target), st, "https://tixcraft.com/activity/detail/abc")
            results["tixcraft_boundary"] = {
                "first": first,
                "second": second,
                "reload_count": tab.reload_count,
                "reached": st["reached"],
                "target_boundary_done": st["target_boundary_done"],
            }

            target = datetime.now() + timedelta(milliseconds=90)
            tab = FakeTab()
            st = state()
            await check_refresh_datetime_gate(tab, config(target), st, "https://tixcraft.com/activity/detail/abc")
            await asyncio.sleep(0.12)
            await check_refresh_datetime_gate(tab, config(target), st, "https://tixcraft.com/ticket/checkout/abc")
            results["tixcraft_boundary_suppressed"] = {
                "reload_count": tab.reload_count,
                "reached": st["reached"],
                "target_boundary_done": st["target_boundary_done"],
            }

            target = datetime.now() + timedelta(milliseconds=80)
            tab = FakeTab()
            st = state()
            await check_refresh_datetime_gate(tab, config(target), st, "https://ticketplus.com.tw/activity/abc")
            await check_refresh_datetime_gate(tab, config(target), st, "https://ticketplus.com.tw/activity/abc")
            results["unsupported_standard"] = {
                "reload_count": tab.reload_count,
                "reached": st["reached"],
            }

            target = datetime.now() + timedelta(milliseconds=80)
            tab = FakeTab()
            st = state()
            await check_refresh_datetime_gate(tab, config(target, advanced_delay_mode="disabled"), st, "https://tixcraft.com/activity/detail/abc")
            await check_refresh_datetime_gate(tab, config(target, advanced_delay_mode="disabled"), st, "https://tixcraft.com/activity/detail/abc")
            results["tixcraft_disabled"] = {
                "reload_count": tab.reload_count,
                "reached": st["reached"],
            }

            print("RESULT_JSON=" + json.dumps(results, sort_keys=True))


        asyncio.run(run())
        """
    )

    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=repo_root,
        env=env,
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    payload_line = next(line for line in result.stdout.splitlines() if line.startswith("RESULT_JSON="))
    payload = json.loads(payload_line.removeprefix("RESULT_JSON="))

    assert payload["tixcraft_boundary"] == {
        "first": False,
        "second": False,
        "reload_count": 1,
        "reached": True,
        "target_boundary_done": False,
    }
    assert payload["tixcraft_boundary_suppressed"] == {
        "reload_count": 1,
        "reached": True,
        "target_boundary_done": False,
    }
    assert payload["unsupported_standard"] == {"reload_count": 1, "reached": True}
    assert payload["tixcraft_disabled"] == {"reload_count": 1, "reached": True}
