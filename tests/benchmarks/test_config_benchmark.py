from __future__ import annotations

import settings


def test_default_config_loading_benchmark(benchmark) -> None:
    config = benchmark(settings.get_default_config)
    assert config["advanced"]["server_port"] == settings.CONST_SERVER_PORT
