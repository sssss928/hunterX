from __future__ import annotations

from pathlib import Path


def _settings_js() -> str:
    return Path("src/www/settings.js").read_text(encoding="utf-8")


def test_profile_load_uses_abortable_generation_and_three_way_commit_guard() -> None:
    js = _settings_js()

    assert "let loaded_profile = null;" in js
    assert "let profile_load_generation = 0;" in js
    assert "let active_profile_load = null;" in js
    assert "previousLoad.abort();" in js
    assert "const requestedProfile = current_profile;" in js
    assert "const requestGeneration = ++profile_load_generation;" in js
    assert "requestGeneration !== profile_load_generation" in js
    assert "requestedProfile !== current_profile" in js
    assert "current_profile !== loaded_profile" in js
    assert "active_profile_load === loadRequest" in js


def test_profile_mutations_require_loaded_snapshot_and_run_is_snapshot_bound() -> None:
    js = _settings_js()

    guarded_signatures = (
        "function create_profile(name, homepage, sourceProfile)",
        "function maxbot_reset_api()",
        "function maxbot_launch()",
        "function do_launch(instance_override, launchProfile)",
        "function maxbot_run_api(instance_override, launchProfile)",
        "function maxbot_save_api(callback, profileSnapshot)",
        "function maxbot_save()",
    )
    for signature in guarded_signatures:
        start = js.index(signature)
        body_prefix = js[start : start + 500]
        assert "require_profile_context(" in body_prefix, signature

    assert "const launchProfile = current_profile;" in js
    assert "it.id === launchProfile && it.alive" in js
    assert "next_instance_id(launchProfile, rows)" in js
    assert "do_launch('', launchProfile);" in js
    assert '"/run" + profile_query_for(launchProfile)' in js
    assert "profile_context_is_ready(launchProfile)" in js



def test_settings_html_ids_are_unique() -> None:
    import re

    html = Path("src/www/settings.html").read_text(encoding="utf-8")
    ids = re.findall(r'\\bid=["\\\']([^"\\\']+)["\\\']', html)
    duplicates = sorted({item for item in ids if ids.count(item) > 1})

    assert duplicates == []
