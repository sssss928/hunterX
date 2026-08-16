from platform_registry import (
    PLATFORM_FAMILIES,
    ValidationLevel,
    platform_for_url,
    platform_key_for_url,
)
from platform_adapters import ADAPTERS


def test_every_host_has_one_owner() -> None:
    owners: dict[str, str] = {}
    for platform in PLATFORM_FAMILIES:
        assert platform.hosts
        for host in platform.hosts:
            assert host not in owners, f"{host} is owned by both {owners[host]} and {platform.key}"
            owners[host] = platform.key


def test_reference_family_includes_indievox_subdomains() -> None:
    platform = platform_for_url("https://www.indievox.com/activity/detail/26_iv0404354")
    assert platform is not None
    assert platform.key == "tixcraft"
    assert platform.matches_url("https://indievox.com/activity/game/26_iv0404354")


def test_hostname_matching_does_not_accept_substring_spoofing() -> None:
    assert platform_for_url("https://indievox.com.evil.example/activity/game/1") is None
    assert platform_for_url("https://notkktix.com/events/test") is None
    assert platform_for_url("not a url containing ticketplus.com") is None


def test_current_dispatch_families_are_resolved() -> None:
    expected = {
        "https://tixcraft.com/activity/detail/demo": "tixcraft",
        "https://www.ticketmaster.com/event/demo": "tixcraft",
        "https://www.ticketmaster.co.uk/event/demo": "tixcraft",
        "https://kktix.com/events/demo": "kktix",
        "https://ticket.ibon.com.tw/ActivityInfo/Details/demo": "ibon",
        "https://kham.com.tw/application/UTK02/demo": "kham",
        "https://tickets.udnfunlife.com/application/UTK02/demo": "kham",
        "https://ticketplus.com.tw/activity/demo": "ticketplus",
        "https://www.cityline.com/Events.html": "cityline",
        "https://premier.ticketek.com.sg/shows/demo": "hkticketing",
        "https://tickets.funone.io/activity/demo": "funone",
        "https://go.fansi.me/events/1": "fansigo",
    }
    assert {url: platform_key_for_url(url) for url in expected} == expected


def test_capability_levels_are_explicit_and_not_boolean_claims() -> None:
    for platform in PLATFORM_FAMILIES:
        assert isinstance(platform.validation.leak_watch, ValidationLevel)
        assert platform.validation.leak_watch >= ValidationLevel.SOURCE_REVIEWED


def test_every_platform_spec_has_one_matching_runtime_adapter() -> None:
    adapters_by_key = {adapter.key: adapter for adapter in ADAPTERS}

    assert set(adapters_by_key) == {spec.key for spec in PLATFORM_FAMILIES}
    for spec in PLATFORM_FAMILIES:
        adapter = adapters_by_key[spec.key]
        assert adapter.display_name == spec.display_name
        assert adapter.hosts == spec.hosts
