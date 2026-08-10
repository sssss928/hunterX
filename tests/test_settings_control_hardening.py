from __future__ import annotations

import asyncio
import base64
import json
import os
import socket
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
import tornado.web

import settings
import util


@pytest.fixture(autouse=True)
def _clear_run_instance_admission_state() -> object:
    settings.RUN_INSTANCE_PENDING.clear()
    yield
    settings.RUN_INSTANCE_PENDING.clear()


class _FakeHandler:
    def __init__(
        self,
        body: object = None,
        query: dict[str, str] | None = None,
        *,
        method: str = "POST",
        host: str = "127.0.0.1:16888",
        origin: str | None = None,
    ) -> None:
        encoded_body = b"" if body is None else json.dumps(body).encode("utf-8")
        headers = {} if origin is None else {"Origin": origin}
        self.request = SimpleNamespace(
            body=encoded_body,
            headers=headers,
            host=host,
            method=method,
            protocol="http",
        )
        self.query = query or {}
        self.status_code = 200
        self.headers: dict[str, str] = {}
        self.responses: list[object] = []
        self.finished = False

    def get_query_argument(self, name: str, default: str = "") -> str:
        return self.query.get(name, default)

    def set_header(self, name: str, value: str) -> None:
        self.headers[name] = value

    def set_status(self, status_code: int) -> None:
        self.status_code = status_code

    def write(self, value: object) -> None:
        self.responses.append(value)

    def finish(self, value: object = None) -> None:
        if value is not None:
            self.responses.append(value)
        self.finished = True


def test_save_json_uses_same_directory_atomic_replace_and_fsync(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "settings.json"
    target.write_text('{"old": true}', encoding="utf-8")
    replace_calls: list[tuple[str, str]] = []
    fsync_calls: list[int] = []
    real_replace = os.replace
    real_fsync = os.fsync

    def replace_spy(source: str, destination: str) -> None:
        replace_calls.append((source, destination))
        real_replace(source, destination)

    def fsync_spy(fd: int) -> None:
        fsync_calls.append(fd)
        real_fsync(fd)

    monkeypatch.setattr(util.os, "replace", replace_spy)
    monkeypatch.setattr(util.os, "fsync", fsync_spy)

    util.save_json({"new": "value"}, target)

    assert json.loads(target.read_text(encoding="utf-8")) == {"new": "value"}
    assert json.loads((tmp_path / "settings.json.bak").read_text(encoding="utf-8")) == {
        "old": True
    }
    assert fsync_calls
    assert len(replace_calls) == 2
    assert {Path(destination) for _, destination in replace_calls} == {
        target,
        tmp_path / "settings.json.bak",
    }
    assert all(Path(source).parent == target.parent for source, _ in replace_calls)
    assert list(tmp_path.glob("*.tmp")) == []


def test_save_json_replace_failure_preserves_previous_file_and_raises(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "settings.json"
    original = '{"old": true}'
    target.write_text(original, encoding="utf-8")

    def fail_replace(source: str, destination: str) -> None:
        raise OSError("replace denied")

    monkeypatch.setattr(util.os, "replace", fail_replace)

    with pytest.raises(OSError, match="replace denied"):
        util.save_json({"new": "value"}, target)

    assert target.read_text(encoding="utf-8") == original
    assert list(tmp_path.glob("*.tmp")) == []


def test_reset_json_keeps_previous_file_until_atomic_save(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / settings.CONST_MAXBOT_CONFIG_FILE
    original = '{"keep": true}'
    target.write_text(original, encoding="utf-8")
    monkeypatch.setattr(settings.util, "get_app_root", lambda: str(tmp_path))

    config_path, config = settings.reset_json()

    assert Path(config_path) == target
    assert isinstance(config, dict)
    assert target.read_text(encoding="utf-8") == original


def test_write_string_to_file_replaces_from_same_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "MAXBOT_LAST_URL.txt"
    replace_calls: list[tuple[str, str]] = []
    real_replace = os.replace

    def replace_spy(source: str, destination: str) -> None:
        replace_calls.append((source, destination))
        real_replace(source, destination)

    monkeypatch.setattr(util.os, "replace", replace_spy)

    util.write_string_to_file(target, "https://tixcraft.com/ticket/area/example")

    assert target.read_text(encoding="utf-8") == "https://tixcraft.com/ticket/area/example"
    assert len(replace_calls) == 1
    assert Path(replace_calls[0][0]).parent == target.parent
    assert Path(replace_calls[0][1]) == target


@pytest.mark.parametrize(
    "token",
    [
        "",
        "../escape",
        r"..\escape",
        "/absolute/path",
        r"C:\absolute\path",
        "contains space",
        "x" * 65,
        123,
    ],
)
def test_sendkey_rejects_unsafe_token_without_wildcard_cors(
    token: object,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings.util, "get_app_root", lambda: str(tmp_path))
    handler = _FakeHandler({"token": token, "value": "test"})

    settings.SendkeyHandler.post(handler)

    assert handler.status_code == 400
    assert handler.responses[-1] == {"return": False, "error": "invalid token"}
    assert handler.headers.get("Access-Control-Allow-Origin") != "*"
    assert list(tmp_path.rglob("*.tmp")) == []


def test_sendkey_writes_allowlisted_token_inside_app_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings.util, "get_app_root", lambda: str(tmp_path))
    payload = {"token": "answer_key-1", "value": "A"}
    handler = _FakeHandler(payload)

    settings.SendkeyHandler.post(handler)

    assert handler.status_code == 200
    assert handler.responses[-1] == {"return": True}
    assert json.loads((tmp_path / "answer_key-1.tmp").read_text(encoding="utf-8")) == payload
    assert handler.headers.get("Access-Control-Allow-Origin") != "*"


def test_sendkey_reports_atomic_storage_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings.util, "get_app_root", lambda: str(tmp_path))

    def fail_save_json(payload: object, target: object) -> None:
        raise OSError("disk denied")

    monkeypatch.setattr(settings.util, "save_json", fail_save_json)
    handler = _FakeHandler({"token": "answer_key-1", "value": "A"})

    settings.SendkeyHandler.post(handler)

    assert handler.status_code == 500
    assert handler.responses[-1] == {"return": False, "error": "failed to persist payload"}


def test_ocr_endpoint_is_bounded_and_does_not_expose_wildcard_cors() -> None:
    encoded = base64.b64encode(b"fixture").decode("ascii")
    handler = _FakeHandler({"image_data": encoded})
    handler.application = SimpleNamespace(
        ocr=SimpleNamespace(classification=lambda image: "AB" if image == b"fixture" else "")
    )
    settings.OCR_REQUEST_TIMES.clear()

    asyncio.run(settings.OcrHandler.post(handler))

    assert handler.status_code == 200
    assert handler.responses[-1] == {"answer": "AB"}
    assert handler.headers.get("Access-Control-Allow-Origin") != "*"


def test_ocr_endpoint_rejects_oversized_and_invalid_payloads(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "CONST_OCR_MAX_BASE64_BYTES", 8)
    oversized = _FakeHandler({"image_data": "A" * 12})
    asyncio.run(settings.OcrHandler.post(oversized))
    assert oversized.status_code == 413
    assert oversized.responses[-1]["error"] == "image payload too large"

    invalid = _FakeHandler({"image_data": "!!!"})
    asyncio.run(settings.OcrHandler.post(invalid))
    assert invalid.status_code == 400
    assert invalid.responses[-1]["error"] == "invalid image_data"


def test_ocr_rate_limit_is_bounded_and_recovers_after_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "CONST_OCR_RATE_LIMIT_REQUESTS", 2)
    settings.OCR_REQUEST_TIMES.clear()

    assert settings._allow_ocr_request(now=10.0)
    assert settings._allow_ocr_request(now=11.0)
    assert not settings._allow_ocr_request(now=12.0)
    assert settings._allow_ocr_request(now=71.0)
    assert len(settings.OCR_REQUEST_TIMES) <= 2


def test_ocr_endpoint_rejects_when_bounded_worker_queue_is_busy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    encoded = base64.b64encode(b"fixture").decode("ascii")
    handler = _FakeHandler({"image_data": encoded})
    handler.application = SimpleNamespace(ocr=object())
    settings.OCR_REQUEST_TIMES.clear()
    monkeypatch.setattr(
        settings,
        "OCR_REQUEST_SLOTS",
        SimpleNamespace(acquire=lambda **_kwargs: False),
    )

    asyncio.run(settings.OcrHandler.post(handler))

    assert handler.status_code == 429
    assert handler.responses[-1] == {"answer": "", "error": "OCR is busy"}


def test_ocr_endpoint_reports_initializing_engine() -> None:
    encoded = base64.b64encode(b"fixture").decode("ascii")
    handler = _FakeHandler({"image_data": encoded})
    handler.application = SimpleNamespace(ocr=None)

    asyncio.run(settings.OcrHandler.post(handler))

    assert handler.status_code == 503
    assert handler.responses[-1]["error"] == "OCR is initializing or unavailable"


def test_ocr_endpoint_times_out_without_blocking_event_loop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    encoded = base64.b64encode(b"fixture").decode("ascii")
    handler = _FakeHandler({"image_data": encoded})
    handler.application = SimpleNamespace(
        ocr=SimpleNamespace(
            classification=lambda _image: (time.sleep(0.05), "late")[1],
        )
    )
    slots = settings.threading.BoundedSemaphore(2)
    settings.OCR_REQUEST_TIMES.clear()
    monkeypatch.setattr(settings, "OCR_REQUEST_SLOTS", slots)
    monkeypatch.setattr(settings, "CONST_OCR_TIMEOUT_SECONDS", 0.001)

    asyncio.run(settings.OcrHandler.post(handler))
    time.sleep(0.08)

    assert handler.status_code == 504
    assert handler.responses[-1] == {"answer": "", "error": "OCR timeout"}
    assert slots.acquire(blocking=False)


def test_control_handler_rejects_oversized_request_body(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "CONST_CONTROL_MAX_BODY_BYTES", 4)
    handler = _FakeHandler({"value": "too large"})

    with pytest.raises(tornado.web.HTTPError) as exc_info:
        settings.LocalControlHandler.prepare(handler)

    assert exc_info.value.status_code == 413


def test_settings_server_listens_only_on_loopback(monkeypatch: pytest.MonkeyPatch) -> None:
    listen_calls: list[tuple[int, dict[str, object]]] = []

    class FakeApplication:
        def __init__(self, handlers: object) -> None:
            self.handlers = handlers

        def listen(self, port: int, **kwargs: object) -> None:
            listen_calls.append((port, kwargs))

    class ImmediateEvent:
        async def wait(self) -> None:
            return None

    monkeypatch.setattr(settings, "Application", FakeApplication)
    monkeypatch.setattr(settings, "ddddocr", SimpleNamespace(DdddOcr=lambda **kwargs: object()), raising=False)
    monkeypatch.setattr(
        settings,
        "load_json",
        lambda: ("settings.json", {"advanced": {"server_port": 16991}}),
    )
    monkeypatch.setattr(settings.asyncio, "Event", lambda: ImmediateEvent())
    monkeypatch.setattr(settings.webbrowser, "open_new", lambda url: True)

    asyncio.run(settings.main_server())

    assert listen_calls == [
        (
            16991,
            {
                "address": "127.0.0.1",
                "max_body_size": settings.CONST_CONTROL_MAX_BODY_BYTES,
                "max_buffer_size": settings.CONST_CONTROL_MAX_BUFFER_BYTES,
            },
        )
    ]


def test_server_transport_limit_rejects_before_handler_buffer_can_grow() -> None:
    assert settings.CONST_CONTROL_MAX_BUFFER_BYTES >= (
        settings.CONST_CONTROL_MAX_BODY_BYTES
    )
    assert settings.CONST_CONTROL_MAX_BUFFER_BYTES <= (
        settings.CONST_CONTROL_MAX_BODY_BYTES + (64 * 1024)
    )


def test_ntp_control_workload_has_bounded_servers_and_samples(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[list[str], int, int, int]] = []

    def capture(
        servers: list[str],
        timeout_ms: int,
        samples_per_server: int,
        min_valid_samples: int,
    ) -> dict[str, object]:
        calls.append(
            (servers, timeout_ms, samples_per_server, min_valid_samples)
        )
        return {"success": True, "source": "ntp"}

    monkeypatch.setattr(settings, "calibrate_ntp_servers", capture)

    result = settings.calibrate_time_by_mode(
        {
            "mode": "ntp",
            "ntp_servers": ["a.example", "b.example"],
            "ntp_timeout_ms": 999999,
            "ntp_samples_per_server": 999999,
            "ntp_min_valid_samples": 999999,
        }
    )

    assert result["success"] is True
    assert calls == [
        (
            ["a.example", "b.example"],
            1500,
            2,
            4,
        )
    ]

    with pytest.raises(ValueError, match="at most"):
        settings.calibrate_time_by_mode(
            {
                "mode": "ntp",
                "ntp_servers": [
                    "a.example",
                    "b.example",
                    "c.example",
                    "d.example",
                    "e.example",
                ],
            }
        )
    with pytest.raises(ValueError, match="invalid NTP server"):
        settings.calibrate_time_by_mode(
            {"mode": "ntp", "ntp_servers": ["bad host/path"]}
        )


def test_telegram_control_workload_rejects_too_many_recipients(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    invoked = False

    async def must_not_run(*_args: object, **_kwargs: object) -> object:
        nonlocal invoked
        invoked = True
        raise AssertionError("external task must not be submitted")

    monkeypatch.setattr(settings, "run_bounded_control_task", must_not_run)
    monkeypatch.setattr(
        settings,
        "load_json",
        lambda profile_name="": ("settings.json", settings.get_default_config()),
    )
    handler = _FakeHandler(
        {
            "bot_token": "123456:ABC_def",
            "chat_id": ",".join(str(value) for value in range(1, 7)),
        }
    )

    asyncio.run(settings.TestTelegramHandler.post(handler))

    assert handler.status_code == 400
    assert handler.responses[-1] == {
        "success": False,
        "message": "At most 5 Chat IDs are allowed",
    }
    assert invoked is False


def test_bounded_control_task_rejects_when_all_slots_are_occupied(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        settings,
        "CONTROL_TASK_SLOTS",
        SimpleNamespace(acquire=lambda **_kwargs: False),
    )

    async def invoke() -> None:
        with pytest.raises(settings.ControlTaskBusyError):
            await settings.run_bounded_control_task(lambda: None)

    asyncio.run(invoke())


def test_bounded_control_task_timeout_eventually_releases_slot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    slots = settings.threading.BoundedSemaphore(1)
    monkeypatch.setattr(settings, "CONTROL_TASK_SLOTS", slots)
    monkeypatch.setattr(settings, "CONST_CONTROL_TASK_TIMEOUT_SECONDS", 0.001)

    async def invoke() -> None:
        with pytest.raises(asyncio.TimeoutError):
            await settings.run_bounded_control_task(time.sleep, 0.03)

    asyncio.run(invoke())
    time.sleep(0.06)

    assert slots.acquire(blocking=False)
    slots.release()


def test_launch_maxbot_returns_spawned_process(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    spawned = object()
    monkeypatch.delattr(util.sys, "frozen", raising=False)
    monkeypatch.setattr(util, "get_app_root", lambda: str(tmp_path))
    monkeypatch.setattr(
        util.subprocess,
        "Popen",
        lambda args, cwd=None, shell=None: spawned,
    )

    assert util.launch_maxbot("nodriver_tixcraft") is spawned


def test_launch_maxbot_propagates_spawn_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delattr(util.sys, "frozen", raising=False)
    monkeypatch.setattr(util, "get_app_root", lambda: str(tmp_path))

    def fail_spawn(
        args: object,
        cwd: str | None = None,
        shell: bool | None = None,
    ) -> None:
        raise OSError("spawn denied")

    monkeypatch.setattr(util.subprocess, "Popen", fail_spawn)

    with pytest.raises(OSError, match="spawn denied"):
        util.launch_maxbot("nodriver_tixcraft")


def test_run_handler_reports_launch_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail_launch(profile_name: str = "", instance_override: str = "") -> None:
        raise OSError("spawn denied")

    monkeypatch.setattr(settings, "launch_maxbot", fail_launch)
    handler = _FakeHandler(query={})

    settings.RunHandler.post(handler)

    assert handler.status_code == 500
    assert handler.responses[-1] == {"run": False, "error": "launch failed"}


def test_run_handler_returns_process_pid_and_command_ack(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    process = SimpleNamespace(pid=4321, poll=lambda: None)
    monkeypatch.setattr(settings, "launch_maxbot", lambda *_args: process)
    handler = _FakeHandler(query={"profile": "default"})

    settings.RunHandler.post(handler)

    response = handler.responses[-1]
    assert response["run"] is True
    assert response["profile"] == "default"
    assert response["pid"] == 4321
    assert response["ack"] == "spawned"
    assert len(response["command_id"]) == 32


def test_run_handler_rejects_process_that_exits_during_spawn_ack(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    process = SimpleNamespace(pid=4321, poll=lambda: 2)
    monkeypatch.setattr(settings, "launch_maxbot", lambda *_args: process)
    handler = _FakeHandler(query={})

    settings.RunHandler.post(handler)

    assert handler.status_code == 500
    assert handler.responses[-1] == {
        "run": False,
        "error": "process did not start",
    }


def test_run_handler_rejects_missing_profile_before_spawn(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    launched: list[object] = []
    monkeypatch.setattr(settings.util, "get_app_root", lambda: str(tmp_path))
    monkeypatch.setattr(
        settings,
        "launch_maxbot",
        lambda *_args: launched.append(object()),
    )
    handler = _FakeHandler(query={"profile": "ghost"})

    settings.RunHandler.post(handler)

    assert handler.status_code == 404
    assert handler.responses[-1] == {"run": False, "error": "profile not found"}
    assert launched == []


def test_run_handler_rejects_cross_profile_instance_override(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    profiles = tmp_path / settings.CONST_PROFILES_DIR
    profiles.mkdir()
    (profiles / "foo.json").write_text("{}", encoding="utf-8")
    launched: list[object] = []
    monkeypatch.setattr(settings.util, "get_app_root", lambda: str(tmp_path))
    monkeypatch.setattr(
        settings,
        "launch_maxbot",
        lambda *_args: launched.append(object()),
    )
    handler = _FakeHandler(query={"profile": "foo", "instance": "bar"})

    settings.RunHandler.post(handler)

    assert handler.status_code == 400
    assert "numbered child" in handler.responses[-1]["error"]
    assert launched == []


def test_run_handler_admission_guard_allows_only_one_pending_spawn(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    profiles = tmp_path / settings.CONST_PROFILES_DIR
    profiles.mkdir()
    (profiles / "foo.json").write_text("{}", encoding="utf-8")
    process = SimpleNamespace(pid=4321, poll=lambda: None)
    launch_calls: list[tuple[object, ...]] = []
    monkeypatch.setattr(settings.util, "get_app_root", lambda: str(tmp_path))

    def launch(*args: object) -> object:
        launch_calls.append(args)
        return process

    monkeypatch.setattr(settings, "launch_maxbot", launch)
    first = _FakeHandler(query={"profile": "foo", "instance": "foo-2"})
    second = _FakeHandler(query={"profile": "foo", "instance": "foo-2"})

    settings.RunHandler.post(first)
    settings.RunHandler.post(second)

    assert first.status_code == 200
    assert second.status_code == 409
    assert len(launch_calls) == 1


def test_run_instance_claim_rechecks_profile_inside_admission_lock(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings.util, "get_app_root", lambda: str(tmp_path))

    assert (
        settings._claim_run_instance(
            "deleted-profile-2",
            profile_name="deleted-profile",
            now=10.0,
        )
        is False
    )
    assert settings.RUN_INSTANCE_PENDING == {}


def test_profile_delete_rejects_pending_child_without_deleting_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    profiles_dir = tmp_path / settings.CONST_PROFILES_DIR
    profiles_dir.mkdir()
    profile_path = profiles_dir / "foo.json"
    profile_path.write_text("{}", encoding="utf-8")
    instance_state = tmp_path / "instances" / "foo-2" / "state.json"
    instance_state.parent.mkdir(parents=True)
    instance_state.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(settings.util, "get_app_root", lambda: str(tmp_path))
    monkeypatch.setattr(
        settings,
        "wait_for_instances_to_stop",
        lambda *_args, **_kwargs: pytest.fail("delete must not block for shutdown"),
    )
    monkeypatch.setattr(
        settings,
        "maxbot_quit",
        lambda *_args, **_kwargs: pytest.fail("pending process must not be stopped"),
    )
    settings.RUN_INSTANCE_PENDING["foo-2"] = {
        "expires_at": time.monotonic() + 30.0,
        "process": SimpleNamespace(pid=4321),
        "profile": "foo",
    }
    handler = _FakeHandler(query={"profile": "foo"}, method="DELETE")

    settings.ProfilesHandler.delete(handler)

    assert handler.status_code == 409
    assert handler.responses[-1] == {
        "success": False,
        "error": "profile has starting or running instances",
        "starting_instances": ["foo-2"],
        "running_instances": [],
    }
    assert profile_path.exists()
    assert instance_state.exists()


def test_profile_delete_rejects_running_instance_without_synchronous_wait(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    profiles_dir = tmp_path / settings.CONST_PROFILES_DIR
    profiles_dir.mkdir()
    profile_path = profiles_dir / "foo.json"
    profile_path.write_text("{}", encoding="utf-8")
    heartbeat = (
        tmp_path
        / "instances"
        / "foo"
        / settings.CONST_HEARTBEAT_FILE
    )
    heartbeat.parent.mkdir(parents=True)
    heartbeat.write_text("alive", encoding="utf-8")
    monkeypatch.setattr(settings.util, "get_app_root", lambda: str(tmp_path))
    monkeypatch.setattr(
        settings,
        "wait_for_instances_to_stop",
        lambda *_args, **_kwargs: pytest.fail("delete must not block for shutdown"),
    )
    monkeypatch.setattr(
        settings,
        "maxbot_quit",
        lambda *_args, **_kwargs: pytest.fail("delete must reject active ownership"),
    )
    handler = _FakeHandler(query={"profile": "foo"}, method="DELETE")

    settings.ProfilesHandler.delete(handler)

    assert handler.status_code == 409
    assert handler.responses[-1] == {
        "success": False,
        "error": "profile has starting or running instances",
        "starting_instances": [],
        "running_instances": ["foo"],
    }
    assert profile_path.exists()
    assert heartbeat.exists()


def test_profile_name_validation_rejects_terminal_newline() -> None:
    assert settings.is_valid_profile_name("valid-profile")
    assert not settings.is_valid_profile_name("valid-profile\n")


def test_mutating_control_endpoints_do_not_implement_get() -> None:
    mutation_handlers = (
        settings.ShutdownHandler,
        settings.CleanupInstancesHandler,
        settings.PauseHandler,
        settings.ResumeHandler,
        settings.StopHandler,
        settings.QuitHandler,
        settings.RunHandler,
        settings.ResetJsonHandler,
    )

    for handler_class in mutation_handlers:
        assert "get" not in handler_class.__dict__
        assert "post" in handler_class.__dict__


def test_cross_origin_control_request_is_rejected() -> None:
    handler = _FakeHandler(origin="https://evil.example")

    with pytest.raises(tornado.web.HTTPError) as exc_info:
        settings.LocalControlHandler.prepare(handler)

    assert exc_info.value.status_code == 403


def test_non_loopback_host_is_rejected() -> None:
    handler = _FakeHandler(host="attacker.example:16888")

    with pytest.raises(tornado.web.HTTPError) as exc_info:
        settings.LocalControlHandler.prepare(handler)

    assert exc_info.value.status_code == 403


def test_same_origin_loopback_control_request_is_allowed() -> None:
    handler = _FakeHandler(origin="http://127.0.0.1:16888")

    settings.LocalControlHandler.prepare(handler)


def test_config_secret_mask_round_trip_preserves_old_values_and_allows_clear() -> None:
    existing = {
        "accounts": {
            "kktix_account": "user@example.com",
            "kktix_password": "private-password",
            "tixcraft_sid": "private-sid",
            "ibonqware": "private-ibon-cookie",
        },
        "advanced": {
            "discord_webhook_url": "https://discord.com/api/webhooks/id/token",
            "telegram_bot_token": "private-token",
        },
    }

    masked = settings.mask_config_secrets(existing)

    assert masked["accounts"]["kktix_account"] == "user@example.com"
    assert masked["accounts"]["kktix_password"] == settings.CONST_SECRET_MASK
    assert masked["accounts"]["tixcraft_sid"] == settings.CONST_SECRET_MASK
    assert masked["accounts"]["ibonqware"] == settings.CONST_SECRET_MASK
    assert masked["advanced"]["discord_webhook_url"] == settings.CONST_SECRET_MASK
    assert masked["advanced"]["telegram_bot_token"] == settings.CONST_SECRET_MASK

    masked["accounts"]["kktix_password"] = ""
    restored = settings.restore_masked_config_secrets(masked, existing)

    assert restored["accounts"]["kktix_password"] == ""
    assert restored["accounts"]["tixcraft_sid"] == "private-sid"
    assert restored["accounts"]["ibonqware"] == "private-ibon-cookie"
    assert restored["advanced"]["discord_webhook_url"] == "https://discord.com/api/webhooks/id/token"
    assert restored["advanced"]["telegram_bot_token"] == "private-token"


def test_load_endpoint_never_returns_plaintext_secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    config = settings.get_default_config()
    config["accounts"]["kktix_password"] = "private-password"
    config["accounts"]["tixcraft_sid"] = "private-sid"
    config["accounts"]["ibonqware"] = "private-ibon-cookie"
    config["advanced"]["discord_webhook_url"] = "https://discord.com/api/webhooks/id/token"
    config["advanced"]["telegram_bot_token"] = "private-token"
    monkeypatch.setattr(settings, "load_json", lambda profile_name="": ("settings.json", config))
    handler = _FakeHandler(method="GET")

    settings.LoadJsonHandler.get(handler)

    payload = handler.responses[-1]
    serialized = json.dumps(payload)
    assert "private-password" not in serialized
    assert "private-sid" not in serialized
    assert "private-ibon-cookie" not in serialized
    assert "discord.com/api/webhooks" not in serialized
    assert "private-token" not in serialized
    assert payload["accounts"]["kktix_password"] == settings.CONST_SECRET_MASK
    assert payload["accounts"]["ibonqware"] == settings.CONST_SECRET_MASK
    assert payload[settings.CONST_SECRET_SOURCE_PROFILE_KEY] == settings.CONST_DEFAULT_PROFILE


def test_save_endpoint_restores_masked_secret_before_atomic_write(monkeypatch: pytest.MonkeyPatch) -> None:
    existing = settings.get_default_config()
    existing["accounts"]["kktix_password"] = "private-password"
    existing["accounts"]["tixcraft_sid"] = "private-sid"
    existing["accounts"]["ibonqware"] = "private-ibon-cookie"
    incoming = settings.mask_config_secrets(existing)
    incoming[settings.CONST_SECRET_SOURCE_PROFILE_KEY] = settings.CONST_DEFAULT_PROFILE
    incoming["accounts"]["kktix_password"] = ""
    captured: dict[str, object] = {}

    monkeypatch.setattr(settings, "load_json", lambda profile_name="": ("settings.json", existing))

    def capture_save(config: object, path: object) -> None:
        captured["config"] = config
        captured["path"] = path

    monkeypatch.setattr(settings.util, "save_json", capture_save)
    handler = _FakeHandler(incoming)

    settings.SaveJsonHandler.post(handler)

    assert handler.status_code == 200
    saved = captured["config"]
    assert saved["accounts"]["kktix_password"] == ""
    assert saved["accounts"]["tixcraft_sid"] == "private-sid"
    assert saved["accounts"]["ibonqware"] == "private-ibon-cookie"
    assert settings.CONST_SECRET_SOURCE_PROFILE_KEY not in saved


def test_shutdown_cli_uses_post(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, float]] = []

    def fake_post(url: str, timeout: float) -> SimpleNamespace:
        calls.append((url, timeout))
        return SimpleNamespace(status_code=200)

    monkeypatch.setattr(settings, "get_server_port", lambda: 16991)
    monkeypatch.setattr(settings.requests, "post", fake_post)

    assert settings.handle_cli_command(["--shutdown"]) is True
    assert calls == [("http://127.0.0.1:16991/shutdown", 1.5)]


def test_profile_clone_restores_masked_secrets_from_source_profile(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    existing = settings.get_default_config()
    existing["accounts"]["kktix_password"] = "private-password"
    incoming = settings.mask_config_secrets(existing)
    incoming[settings.CONST_SECRET_SOURCE_PROFILE_KEY] = settings.CONST_DEFAULT_PROFILE
    captured: dict[str, object] = {}

    monkeypatch.setattr(settings.util, "get_app_root", lambda: str(tmp_path))
    monkeypatch.setattr(settings, "load_json", lambda profile_name="": ("settings.json", existing))

    def capture_save(config: object, path: object) -> None:
        captured["config"] = config
        captured["path"] = path

    monkeypatch.setattr(settings.util, "save_json", capture_save)
    handler = _FakeHandler({"name": "copy", "config": incoming})

    settings.ProfilesHandler.post(handler)

    assert handler.status_code == 200
    assert handler.responses[-1] == {"success": True, "profile": "copy"}
    assert captured["config"]["accounts"]["kktix_password"] == "private-password"
    assert settings.CONST_SECRET_SOURCE_PROFILE_KEY not in captured["config"]


def test_failed_server_start_does_not_clean_shared_state(monkeypatch: pytest.MonkeyPatch) -> None:
    clean_calls: list[bool] = []
    monkeypatch.setattr(settings, "start_web_server_background", lambda startup_timeout=5.0: None, raising=False)
    monkeypatch.setattr(settings, "clean_tmp_file", lambda: clean_calls.append(True))

    assert settings.initialize_settings_server() is None
    assert clean_calls == []


def test_port_already_in_use_does_not_clean_shared_state(monkeypatch: pytest.MonkeyPatch) -> None:
    clean_calls: list[bool] = []
    monkeypatch.setattr(settings, "get_server_port", lambda: 16991)
    monkeypatch.setattr(settings.util, "is_connectable", lambda port, host=None: True)
    monkeypatch.setattr(settings.webbrowser, "open_new", lambda url: True)
    monkeypatch.setattr(settings, "clean_tmp_file", lambda: clean_calls.append(True))

    assert settings.initialize_settings_server(startup_timeout=1.0) is None
    assert clean_calls == []


def test_successful_server_start_cleans_shared_state_once(monkeypatch: pytest.MonkeyPatch) -> None:
    server_thread = object()
    clean_calls: list[bool] = []
    monkeypatch.setattr(
        settings,
        "start_web_server_background",
        lambda startup_timeout=5.0: server_thread,
    )
    monkeypatch.setattr(settings, "clean_tmp_file", lambda: clean_calls.append(True))

    assert settings.initialize_settings_server() is server_thread
    assert clean_calls == [True]


def test_clean_tmp_file_preserves_live_default_runtime_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings.util, "get_app_root", lambda: str(tmp_path))
    protected_names = (
        settings.CONST_HEARTBEAT_FILE,
        settings.CONST_MAXBOT_INT28_FILE,
        settings.CONST_MAXBOT_INT28_QUIT_FILE,
        settings.CONST_MAXBOT_AUTOMATION_STOP_FILE,
        settings.CONST_MAXBOT_QUESTION_FILE,
        "answer-token.tmp",
    )
    for name in protected_names:
        (tmp_path / name).write_text("live", encoding="utf-8")

    settings.clean_tmp_file()

    assert all((tmp_path / name).exists() for name in protected_names)


def test_clean_tmp_file_removes_dead_default_runtime_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings.util, "get_app_root", lambda: str(tmp_path))
    heartbeat = tmp_path / settings.CONST_HEARTBEAT_FILE
    heartbeat.write_text("stale", encoding="utf-8")
    old_timestamp = time.time() - settings.CONST_HEARTBEAT_ALIVE_SEC - 5
    os.utime(heartbeat, (old_timestamp, old_timestamp))
    pause_file = tmp_path / settings.CONST_MAXBOT_INT28_FILE
    pause_file.write_text("stale", encoding="utf-8")
    token_file = tmp_path / "answer-token.tmp"
    token_file.write_text("stale", encoding="utf-8")

    settings.clean_tmp_file()

    assert not heartbeat.exists()
    assert not pause_file.exists()
    assert not token_file.exists()


def test_orphan_cleanup_preserves_pending_instance_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    orphan_state = tmp_path / "instances" / "orphan-2" / "state.json"
    orphan_state.parent.mkdir(parents=True)
    orphan_state.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(settings.util, "get_app_root", lambda: str(tmp_path))
    settings.RUN_INSTANCE_PENDING["orphan-2"] = {
        "expires_at": time.monotonic() + 30.0,
        "process": SimpleNamespace(pid=4321),
        "profile": "orphan",
    }

    removed, preserved = settings.cleanup_orphan_instance_dirs()

    assert removed == []
    assert preserved == ["orphan-2"]
    assert orphan_state.exists()


@pytest.mark.parametrize(
    ("handler_class", "is_async"),
    [
        (settings.ProfilesHandler, False),
        (settings.TestDiscordWebhookHandler, True),
        (settings.TestTelegramHandler, True),
        (settings.TimeCalibrationHandler, True),
        (settings.TicketLatencyHandler, True),
        (settings.PlatformTimingCapabilityHandler, False),
    ],
)
def test_json_control_handlers_reject_non_object_payloads(
    handler_class: type,
    is_async: bool,
) -> None:
    handler = _FakeHandler([])

    result = handler_class.post(handler)
    if is_async:
        asyncio.run(result)

    assert handler.status_code == 400


@pytest.mark.parametrize(
    ("handler_class", "payload"),
    [
        (
            settings.TestDiscordWebhookHandler,
            {"webhook_url": 123, "custom_message": ""},
        ),
        (
            settings.TestTelegramHandler,
            {"bot_token": 123, "chat_id": "1", "custom_message": ""},
        ),
        (
            settings.TestTelegramHandler,
            {"bot_token": "1:abc", "chat_id": 123, "custom_message": ""},
        ),
    ],
)
def test_notification_test_handlers_reject_non_string_fields(
    handler_class: type,
    payload: dict[str, object],
) -> None:
    handler = _FakeHandler(payload)

    asyncio.run(handler_class.post(handler))

    assert handler.status_code == 400
    assert handler.responses[-1]["message"] == "invalid field type"


def test_public_response_body_reader_rejects_declared_oversize() -> None:
    response = SimpleNamespace(
        headers={
            "Content-Length": str(settings.CONST_PUBLIC_RESPONSE_MAX_BYTES + 1)
        }
    )

    with pytest.raises(ValueError, match="response body too large"):
        settings.read_limited_response_body(response)


def test_settings_server_shutdown_closes_listener(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]

    monkeypatch.setattr(
        settings,
        "load_json",
        lambda: ("settings.json", {"advanced": {"server_port": port}}),
    )
    monkeypatch.setattr(settings, "_create_ocr_engine", lambda: None)
    monkeypatch.setattr(settings.webbrowser, "open_new", lambda _url: False)

    thread = settings.start_web_server_background(startup_timeout=3.0)
    assert isinstance(thread, threading.Thread)
    assert settings.util.is_connectable(port, host="127.0.0.1")

    response = settings.requests.post(
        f"http://127.0.0.1:{port}/shutdown",
        timeout=2.0,
    )
    assert response.status_code == 200
    assert response.json() == {"shutdown": True}

    thread.join(timeout=3.0)
    assert not thread.is_alive()
    assert not settings.util.is_connectable(port, host="127.0.0.1")


def test_port_reuse_requires_matching_hunterx_version(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    opened: list[str] = []
    startup_event = threading.Event()
    startup_result: dict[str, object] = {}
    monkeypatch.setattr(settings, "get_server_port", lambda: 16991)
    monkeypatch.setattr(
        settings.util,
        "is_connectable",
        lambda _port, host=None: True,
    )
    monkeypatch.setattr(
        settings.requests,
        "get",
        lambda *_args, **_kwargs: SimpleNamespace(
            status_code=200,
            json=lambda: {"version": "0.4.3"},
        ),
    )
    monkeypatch.setattr(
        settings.webbrowser,
        "open_new",
        lambda url: opened.append(url),
    )

    assert settings.web_server(startup_event, startup_result) is False
    assert startup_event.is_set()
    assert startup_result["started"] is False
    assert "incompatible" in str(startup_result["error"])
    assert opened == []
