"""The preset engine against a faked oven.

The spec names four places this will actually break: the transition gap, a
refused restart, a door opened mid-programme and a Home Assistant restart
halfway through step 2. Each has a test here.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError

from .conftest import (
    CURRENT,
    DOOR,
    GRILL,
    HOT_AIR,
    OPERATION,
    REMOTE,
    START,
    abort,
    advance,
    run,
    save,
    settle,
    state,
    types,
)

PIZZA = [
    {"kind": "preheat", "program": HOT_AIR, "setpoint": 250},
    {"kind": "timed", "program": HOT_AIR, "setpoint": 250, "duration_minutes": 12},
    {"kind": "timed", "program": GRILL, "setpoint": 220, "duration_minutes": 3},
]
TWO_STEP = [
    {"kind": "timed", "program": HOT_AIR, "setpoint": 200, "duration_minutes": 10},
    {"kind": "timed", "program": HOT_AIR, "setpoint": 120, "duration_minutes": 10},
]


async def test_pizza_runs_every_step_and_leaves_oven_stopped(
    hass: HomeAssistant, freezer, loaded, device_id, oven, events
) -> None:
    await save(hass, device_id, "Pizza", PIZZA)
    await run(hass, "Pizza")
    assert state(hass).state == "running"
    assert state(hass).attributes["step_kind"] == "preheat"
    # The appliance gets its own duration as a dead-man's switch (45 min preheat timeout).
    assert ("duration", 45 * 60) in oven.calls

    hass.states.async_set(CURRENT, "248")
    await settle(hass)
    assert state(hass).attributes["step_index"] == 1

    await advance(hass, freezer, 12 * 60)
    assert state(hass).attributes["step_program"] == GRILL
    await advance(hass, freezer, 3 * 60)

    assert state(hass).state == "finished"
    assert hass.states.get(OPERATION).state == "ready"
    assert types(events) == [
        "started",
        "step_started",
        "step_finished",
        "step_started",
        "step_finished",
        "step_started",
        "step_finished",
        "finished",
    ]
    assert oven.names[-1] == "abort"


async def test_transition_is_abort_then_select_then_start(
    hass: HomeAssistant, freezer, loaded, device_id, oven
) -> None:
    await save(hass, device_id, "Two", TWO_STEP)
    await run(hass, "Two")
    oven.calls.clear()
    await advance(hass, freezer, 10 * 60)
    assert oven.names[:5] == ["abort", "select", "setpoint", "duration", "start"]
    assert ("setpoint", 120.0) in oven.calls
    assert state(hass).attributes["step_index"] == 1


async def test_refused_restart_fails_safely(
    hass: HomeAssistant, freezer, loaded, device_id, oven, events
) -> None:
    """Remote start clears after step 1 -- the single most likely real failure."""
    oven.clear_remote_after_start = True
    await save(hass, device_id, "Two", TWO_STEP)
    await run(hass, "Two")
    assert hass.states.get(REMOTE).state == "off"

    await advance(hass, freezer, 10 * 60 + 15)
    assert state(hass).state == "failed"
    assert state(hass).attributes["last_error"] == "remote_start_unavailable"
    assert events[-1] == {**events[-1], "type": "failed", "reason": "remote_start_unavailable"}
    assert hass.states.get(OPERATION).state == "ready"
    assert oven.names.count("start") == 1


async def test_appliance_that_never_starts_fails_after_retries(
    hass: HomeAssistant, freezer, loaded, device_id, oven
) -> None:
    oven.refuse_start = True
    await save(hass, device_id, "Two", TWO_STEP)
    await run(hass, "Two")
    await advance(hass, freezer, 70)
    assert state(hass).attributes["last_error"] == "start_failed"
    assert oven.names.count("start") == 2


async def test_door_left_open_on_hot_oven_aborts(
    hass: HomeAssistant, freezer, loaded, device_id, oven
) -> None:
    await save(hass, device_id, "Two", TWO_STEP)
    await run(hass, "Two")
    hass.states.async_set(DOOR, "on")
    await advance(hass, freezer, 60)
    assert state(hass).state == "running"  # still inside the grace period
    await advance(hass, freezer, 65)
    assert state(hass).attributes["last_error"] == "door_opened"
    assert hass.states.get(OPERATION).state == "ready"


async def test_door_closed_within_grace_is_fine(
    hass: HomeAssistant, freezer, loaded, device_id, oven
) -> None:
    await save(hass, device_id, "Two", TWO_STEP)
    await run(hass, "Two")
    hass.states.async_set(DOOR, "on")
    await advance(hass, freezer, 60)
    hass.states.async_set(DOOR, "off")
    await advance(hass, freezer, 120)
    assert state(hass).state == "running"


async def test_restart_halfway_through_step_two_resumes(
    hass: HomeAssistant, freezer, loaded, device_id, oven, events
) -> None:
    await save(hass, device_id, "Two", TWO_STEP)
    await run(hass, "Two")
    await advance(hass, freezer, 15 * 60)
    assert state(hass).attributes["step_index"] == 1

    # "Restart": unload and set up again; the oven keeps running meanwhile.
    await hass.config_entries.async_unload(loaded.entry_id)
    await hass.async_block_till_done()
    freezer.tick(timedelta(minutes=2))
    assert await hass.config_entries.async_setup(loaded.entry_id)
    await settle(hass)
    assert state(hass).state == "running"
    assert state(hass).attributes["step_index"] == 1

    await advance(hass, freezer, 4 * 60)
    assert state(hass).state == "finished"
    assert oven.names.count("start") == 2  # resumed, not restarted


async def test_restart_with_oven_stopped_mid_step_is_interrupted(
    hass: HomeAssistant, freezer, loaded, device_id, oven
) -> None:
    await save(hass, device_id, "Two", TWO_STEP)
    await run(hass, "Two")
    await hass.config_entries.async_unload(loaded.entry_id)
    await hass.async_block_till_done()
    oven.set(OPERATION, "ready")  # someone stopped it at the appliance
    assert await hass.config_entries.async_setup(loaded.entry_id)
    await settle(hass)
    assert state(hass).attributes["last_error"] == "interrupted"
    assert oven.names.count("start") == 1  # never restarts a hot oven blindly


async def test_ready_at_schedules_backwards(
    hass: HomeAssistant, freezer, loaded, device_id, oven, events
) -> None:
    await save(hass, device_id, "Pizza", PIZZA)
    await run(hass, "Pizza", ready_at=(START + timedelta(hours=2)).isoformat())
    assert state(hass).state == "scheduled"
    # 2 h - (15 min preheat estimate + 12 + 3)
    assert state(hass).attributes["start_at"] == (START + timedelta(minutes=90)).isoformat()
    assert oven.names == []
    await advance(hass, freezer, 90 * 60)
    assert state(hass).state == "running"
    assert types(events)[:2] == ["scheduled", "started"]


async def test_abort_needs_no_confirmation_and_stops_the_oven(
    hass: HomeAssistant, freezer, loaded, device_id, oven, events
) -> None:
    await save(hass, device_id, "Two", TWO_STEP)
    await run(hass, "Two")
    await abort(hass)
    assert state(hass).state == "idle"
    assert hass.states.get(OPERATION).state == "ready"
    assert types(events)[-1] == "aborted"
    await advance(hass, freezer, 11 * 60)
    assert oven.names.count("start") == 1  # no step 2 after abort


async def test_max_total_duration_aborts_and_powers_down(
    hass: HomeAssistant, freezer, loaded, device_id, oven
) -> None:
    hass.config_entries.async_update_entry(loaded, options={"max_total_minutes": 30})
    await save(hass, device_id, "Roast", [{"kind": "hold", "program": HOT_AIR, "setpoint": 80}])
    await run(hass, "Roast")
    assert ("duration", 30 * 60) in oven.calls  # the oven itself is bounded too
    await advance(hass, freezer, 30 * 60 + 5)
    assert state(hass).attributes["last_error"] == "max_duration"
    assert "power_off" in oven.names


@pytest.mark.parametrize(
    ("setup", "match"),
    [
        (lambda hass: None, "confirm"),
        (lambda hass: hass.states.async_set(REMOTE, "off"), "Remote start"),
        (lambda hass: hass.states.async_set(REMOTE, "unavailable"), "Remote start"),
    ],
)
async def test_run_is_refused_up_front(
    hass: HomeAssistant, loaded, device_id, oven, setup, match
) -> None:
    await save(hass, device_id, "Two", TWO_STEP)
    setup(hass)
    with pytest.raises(ServiceValidationError, match=match):
        await hass.services.async_call(
            "appliance_presets",
            "run",
            {"entity_id": "sensor.dampovn_preset", "preset": "Two", "confirm": match != "confirm"},
            blocking=True,
        )
    assert oven.names == []


async def test_busy_appliance_is_not_ours_to_abort(
    hass: HomeAssistant, loaded, device_id, oven
) -> None:
    await save(hass, device_id, "Two", TWO_STEP)
    oven.set(OPERATION, "run")
    await run(hass, "Two")
    assert state(hass).attributes["last_error"] == "appliance_busy"
    assert "abort" not in oven.names
