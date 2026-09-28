"""Notify when home: who gets told, about what, and in which language."""

from __future__ import annotations

import pytest
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.appliance_presets.notifier import message_language

from .conftest import HOT_AIR, SERIAL, advance, run, save, settle

ALARM_CLOCK = "binary_sensor.kitchen_dampovn_alarm_clock_elapsed"
FINISHED = "binary_sensor.kitchen_dampovn_program_finished"
ABORTED = "binary_sensor.kitchen_dampovn_program_aborted"
DOOR_ALARM = "binary_sensor.kitchen_dampovn_door_alarm_fridge"


def _register(hass: HomeAssistant, device_id: str, key: str, entity_id: str, **kw) -> None:
    device = dr.async_get(hass).async_get(device_id)
    hc_entry = hass.config_entries.async_get_entry(next(iter(device.config_entries)))
    er.async_get(hass).async_get_or_create(
        "binary_sensor",
        "homeconnect_ws",
        f"{SERIAL}-{key}",
        suggested_object_id=entity_id.split(".")[1],
        config_entry=hc_entry,
        device_id=device_id,
        **kw,
    )
    hass.states.async_set(entity_id, "off")


def _phone(hass: HomeAssistant, person: str, phone: str, home: bool) -> None:
    """A person tracked by the Companion app on a phone called `phone`."""
    app = MockConfigEntry(domain="mobile_app", title=phone)
    app.add_to_hass(hass)
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=app.entry_id, identifiers={("mobile_app", phone)}, name=phone
    )
    tracker = er.async_get(hass).async_get_or_create(
        "device_tracker", "mobile_app", phone, config_entry=app, device_id=device.id
    )
    hass.states.async_set(
        f"person.{person}",
        "home" if home else "not_home",
        {"device_trackers": [tracker.entity_id]},
    )


@pytest.fixture
def sent(hass: HomeAssistant, device_id: str):
    for key, entity_id in (
        ("binary_sensor_alarm_clock_elapsed", ALARM_CLOCK),
        ("binary_sensor_program_finished", FINISHED),
        ("binary_sensor_program_aborted", ABORTED),
        ("binary_sensor_door_alarm_fridge", DOOR_ALARM),
    ):
        _register(hass, device_id, key, entity_id, original_name=key.split("_", 2)[2])
    _phone(hass, "anna", "Anna sin telefon", home=True)
    _phone(hass, "ola", "Ola Pixel", home=False)
    calls: list[tuple[str, dict]] = []

    async def _capture(call: ServiceCall) -> None:
        calls.append((call.service, dict(call.data)))

    for service in ("mobile_app_anna_sin_telefon", "mobile_app_ola_pixel"):
        hass.services.async_register("notify", service, _capture)
    return calls


@pytest.fixture
async def notifying(hass: HomeAssistant, freezer, oven, entry: MockConfigEntry, sent):
    hass.config_entries.async_update_entry(
        entry, options={"notify_persons": ["person.anna", "person.ola"]}
    )
    from .conftest import START

    freezer.move_to(START)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    yield entry
    await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()


async def _turn_on(hass: HomeAssistant, entity_id: str) -> None:
    hass.states.async_set(entity_id, "on")
    await settle(hass)


async def test_timer_notifies_only_people_who_are_home(hass: HomeAssistant, notifying, sent):
    await _turn_on(hass, ALARM_CLOCK)
    assert [service for service, _ in sent] == ["mobile_app_anna_sin_telefon"]
    data = sent[0][1]
    assert data["title"] == "Dampovn"
    assert data["message"] == "The timer is done"
    assert data["data"]["push"] == {"interruption-level": "time-sensitive"}


async def test_bokmal_message(hass: HomeAssistant, notifying, sent):
    hass.config.language = "nb-NO"
    await _turn_on(hass, ALARM_CLOCK)
    assert sent[0][1]["message"] == "Tidsuret er ferdig"


async def test_nothing_when_nobody_chosen_is_home(hass: HomeAssistant, notifying, sent):
    hass.states.async_set("person.anna", "not_home", {"device_trackers": []})
    await _turn_on(hass, ALARM_CLOCK)
    assert sent == []


async def test_reconnect_does_not_resend(hass: HomeAssistant, notifying, sent):
    hass.states.async_set(ALARM_CLOCK, "unavailable")
    await _turn_on(hass, ALARM_CLOCK)
    assert sent == []


async def test_alarm_names_the_problem_and_aborts_are_quiet(hass: HomeAssistant, notifying, sent):
    await _turn_on(hass, ABORTED)
    assert sent == []
    await _turn_on(hass, DOOR_ALARM)
    assert sent[0][1]["message"] == "Needs attention: door_alarm_fridge"


async def test_kinds_can_be_switched_off(hass: HomeAssistant, notifying, sent):
    hass.config_entries.async_update_entry(
        notifying, options={**notifying.options, "notify_on": ["problem"]}
    )
    await _turn_on(hass, ALARM_CLOCK)
    await _turn_on(hass, FINISHED)
    assert sent == []


async def test_preset_reports_its_own_finish_once(
    hass: HomeAssistant, freezer, notifying, sent, device_id
):
    await save(
        hass,
        device_id,
        "Pizza",
        [
            {"kind": "timed", "program": HOT_AIR, "setpoint": 250, "duration_minutes": 5},
            {"kind": "timed", "program": HOT_AIR, "setpoint": 220, "duration_minutes": 5},
        ],
    )
    await run(hass, "Pizza")
    # The appliance's programme ends at a step boundary: not news.
    await _turn_on(hass, FINISHED)
    hass.states.async_set(FINISHED, "off")
    await advance(hass, freezer, 10 * 60 + 10)
    assert hass.states.get("sensor.dampovn_preset").state == "finished"
    # The appliance also reports finishing; it is the same news.
    await _turn_on(hass, FINISHED)
    assert [data["message"] for _, data in sent] == ["Pizza is done"]


@pytest.mark.parametrize(
    ("language", "expected"),
    [
        ("nb", "nb"),
        ("nb-NO", "nb"),
        ("NB_no", "nb"),
        ("no", "nb"),
        ("nn", "nb"),
        ("en-GB", "en"),
        ("de", "en"),
        (None, "en"),
        ("", "en"),
    ],
)
def test_message_language(language, expected):
    assert message_language(language) == expected
