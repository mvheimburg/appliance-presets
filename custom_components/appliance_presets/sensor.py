"""Sensor platform: one preset status entity per appliance, plus entity services."""

from __future__ import annotations

import voluptuous as vol
from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import entity_platform
from homeassistant.helpers.device import async_device_info_to_link_from_device_id
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    ATTR_CONFIRM,
    ATTR_PRESET,
    ATTR_READY_AT,
    ATTR_START_AT,
    SERVICE_ABORT,
    SERVICE_RUN,
    STATE_FAILED,
    STATE_FINISHED,
    STATE_IDLE,
    STATE_RUNNING,
    STATE_SCHEDULED,
)
from .engine import PresetRunner

RUN_FIELDS = {
    vol.Required(ATTR_PRESET): cv.string,
    vol.Optional(ATTR_START_AT): cv.datetime,
    vol.Optional(ATTR_READY_AT): cv.datetime,
    vol.Optional(ATTR_CONFIRM, default=False): cv.boolean,
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    runner: PresetRunner = entry.runtime_data
    async_add_entities([PresetStatusSensor(runner, entry)])

    platform = entity_platform.async_get_current_platform()
    platform.async_register_entity_service(SERVICE_RUN, RUN_FIELDS, "async_run")
    platform.async_register_entity_service(SERVICE_ABORT, {}, "async_abort")


class PresetStatusSensor(SensorEntity):
    """idle | scheduled | running | finished | failed, with the run as attributes."""

    _attr_has_entity_name = True
    _attr_translation_key = "preset"
    _attr_should_poll = False
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = [STATE_IDLE, STATE_SCHEDULED, STATE_RUNNING, STATE_FINISHED, STATE_FAILED]

    def __init__(self, runner: PresetRunner, entry: ConfigEntry) -> None:
        self.runner = runner
        self._attr_unique_id = f"{entry.entry_id}_preset"
        # Attach to the appliance's own device so it shows up alongside it.
        self._attr_device_info = async_device_info_to_link_from_device_id(
            runner.hass, runner.device_id
        )

    async def async_added_to_hass(self) -> None:
        self.runner.entity_id = self.entity_id
        self.async_on_remove(self.runner.async_add_listener(self.async_write_ha_state))
        self.async_on_remove(self.runner.presets.async_add_listener(self.async_write_ha_state))

    @property
    def native_value(self) -> str:
        return self.runner.state

    @property
    def extra_state_attributes(self):
        return self.runner.attributes

    async def async_run(self, preset: str, confirm: bool = False, **kwargs) -> None:
        await self.runner.async_run(
            preset,
            start_at=kwargs.get(ATTR_START_AT),
            ready_at=kwargs.get(ATTR_READY_AT),
            confirm=confirm,
        )

    async def async_abort(self) -> None:
        await self.runner.async_abort()
