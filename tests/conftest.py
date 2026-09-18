"""Shared fixtures: a faked Home Connect Local oven and helpers to drive time."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import pytest
from homeassistant.core import Event, HomeAssistant, ServiceCall, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.appliance_presets.const import CONF_DEVICE_ID, DOMAIN

SERIAL = "SIEMENS-HB978GUB1-68A40E000000"
HOT_AIR = "Cooking.Oven.Program.HeatingMode.HotAir"
GRILL = "Cooking.Oven.Program.HeatingMode.GrillLargeArea"
STATUS = "sensor.dampovn_preset"

# A fixed "now": Friday 2026-09-18 14:00 UTC
START = datetime(2026, 9, 18, 14, 0, tzinfo=UTC)

# Home Connect Local unique-id keys -> entity ids as HA would name them.
ENTITIES = {
    "sensor_operation_state": "sensor.kitchen_dampovn_operation_state",
    "select_program": "select.kitchen_dampovn_selected_program",
    "button_start_program": "button.kitchen_dampovn_start",
    "button_abort_program": "button.kitchen_dampovn_abort",
    "binary_remote_start_allowed": "binary_sensor.kitchen_dampovn_remote_start",
    "number_oven_setpoint_temperature": "number.kitchen_dampovn_setpoint_temperature",
    "sensor_oven_current_temperature": "sensor.kitchen_dampovn_current_temperature",
    "number_duration": "number.kitchen_dampovn_duration",
    "binary_sensor_door_state": "binary_sensor.kitchen_dampovn_door",
    "switch_power_state": "switch.kitchen_dampovn_power",
}
OPERATION = ENTITIES["sensor_operation_state"]
SELECT = ENTITIES["select_program"]
REMOTE = ENTITIES["binary_remote_start_allowed"]
SETPOINT = ENTITIES["number_oven_setpoint_temperature"]
CURRENT = ENTITIES["sensor_oven_current_temperature"]
DURATION = ENTITIES["number_duration"]
DOOR = ENTITIES["binary_sensor_door_state"]
POWER = ENTITIES["switch_power_state"]


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    yield


@dataclass
class FakeOven:
    """Responds to the services the engine calls the way a real oven would.

    Knobs let a test make it misbehave: refuse to start, clear remote start
    after each programme (the single most likely real failure), and so on.
    """

    hass: HomeAssistant
    calls: list[tuple[str, object]] = field(default_factory=list)
    program: str = HOT_AIR
    refuse_start: bool = False
    clear_remote_after_start: bool = False

    def set(self, entity_id: str, state: str, **attrs) -> None:
        current = self.hass.states.get(entity_id)
        merged = {**(current.attributes if current else {}), **attrs}
        self.hass.states.async_set(entity_id, state, merged)

    def install(self) -> None:
        self.set(OPERATION, "ready")
        self.set(SELECT, HOT_AIR, options=[HOT_AIR, GRILL])
        self.set(REMOTE, "on")
        self.set(SETPOINT, "30", min=30, max=275)
        self.set(CURRENT, "21")
        self.set(DURATION, "0", min=1, max=86340)
        self.set(DOOR, "off", device_class="door")
        self.set(POWER, "on")
        self.hass.services.async_register("button", "press", self._press)
        self.hass.services.async_register("select", "select_option", self._select)
        self.hass.services.async_register("number", "set_value", self._number)
        self.hass.services.async_register("switch", "turn_off", self._switch_off)

    @property
    def names(self) -> list[str]:
        return [name for name, _ in self.calls]

    async def _press(self, call: ServiceCall) -> None:
        entity_id = call.data["entity_id"]
        entity_id = entity_id[0] if isinstance(entity_id, list) else entity_id
        if entity_id == ENTITIES["button_start_program"]:
            self.calls.append(("start", self.program))
            remote = self.hass.states.get(REMOTE).state == "on"
            if remote and not self.refuse_start:
                self.set(OPERATION, "run")
                if self.clear_remote_after_start:
                    self.set(REMOTE, "off")
        elif entity_id == ENTITIES["button_abort_program"]:
            self.calls.append(("abort", None))
            self.set(OPERATION, "ready")

    async def _select(self, call: ServiceCall) -> None:
        self.program = call.data["option"]
        self.calls.append(("select", self.program))
        self.set(SELECT, self.program)

    async def _number(self, call: ServiceCall) -> None:
        entity_id = call.data["entity_id"]
        entity_id = entity_id[0] if isinstance(entity_id, list) else entity_id
        name = "setpoint" if entity_id == SETPOINT else "duration"
        self.calls.append((name, call.data["value"]))
        self.set(entity_id, str(call.data["value"]))

    async def _switch_off(self, call: ServiceCall) -> None:
        self.calls.append(("power_off", None))
        self.set(POWER, "off")
        self.set(OPERATION, "inactive")


@pytest.fixture
def device_id(hass: HomeAssistant) -> str:
    """Register the oven's device and entities the way homeconnect_ws does."""
    hc_entry = MockConfigEntry(domain="homeconnect_ws", title="Home Connect Local")
    hc_entry.add_to_hass(hass)
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=hc_entry.entry_id,
        identifiers={("homeconnect_ws", SERIAL)},
        name="Dampovn",
        manufacturer="Siemens",
    )
    registry = er.async_get(hass)
    for key, entity_id in ENTITIES.items():
        domain, object_id = entity_id.split(".")
        registry.async_get_or_create(
            domain,
            "homeconnect_ws",
            f"{SERIAL}-{key}",
            suggested_object_id=object_id,
            config_entry=hc_entry,
            device_id=device.id,
        )
    return device.id


@pytest.fixture
def oven(hass: HomeAssistant, device_id: str) -> FakeOven:
    fake = FakeOven(hass)
    fake.install()
    return fake


@pytest.fixture
def events(hass: HomeAssistant):
    captured = []

    @callback
    def _capture(event: Event) -> None:
        captured.append(event.data)

    hass.bus.async_listen(f"{DOMAIN}_event", _capture)
    return captured


def types(events) -> list[str]:
    return [e["type"] for e in events]


@pytest.fixture
def entry(hass: HomeAssistant, device_id: str) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Dampovn",
        unique_id=device_id,
        data={CONF_DEVICE_ID: device_id},
        options={},
    )
    entry.add_to_hass(hass)
    return entry


@pytest.fixture
async def loaded(hass: HomeAssistant, freezer, oven: FakeOven, entry: MockConfigEntry):
    """The integration set up with the oven, time frozen at START."""
    freezer.move_to(START)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    yield entry
    await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()


async def settle(hass: HomeAssistant) -> None:
    """Let background tasks (the engine's transitions) run until they block."""
    for _ in range(30):
        await asyncio.sleep(0)
        await hass.async_block_till_done()


async def advance(hass: HomeAssistant, freezer, seconds: float) -> None:
    """Move frozen time forward in small ticks, firing each timer as it falls due."""
    remaining = seconds
    while remaining > 0:
        tick = min(remaining, 5)
        freezer.tick(timedelta(seconds=tick))
        async_fire_time_changed(hass, dt_util.utcnow())
        await settle(hass)
        remaining -= tick


async def save(hass: HomeAssistant, device_id: str, name: str, steps: list[dict]) -> None:
    await hass.services.async_call(
        DOMAIN, "save", {"name": name, "device_id": device_id, "steps": steps}, blocking=True
    )


async def run(hass: HomeAssistant, preset: str, **data) -> None:
    await hass.services.async_call(
        DOMAIN,
        "run",
        {"entity_id": STATUS, "preset": preset, "confirm": True, **data},
        blocking=True,
    )
    await settle(hass)


async def abort(hass: HomeAssistant) -> None:
    await hass.services.async_call(DOMAIN, "abort", {"entity_id": STATUS}, blocking=True)
    await settle(hass)


def state(hass: HomeAssistant):
    return hass.states.get(STATUS)
