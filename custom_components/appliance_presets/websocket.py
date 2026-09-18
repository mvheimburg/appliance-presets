"""WebSocket API for the preset editor panel."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.components import websocket_api
from homeassistant.core import HomeAssistant, callback

from .const import DOMAIN
from .engine import PresetRunner
from .roles import ROLE_CURRENT_TEMPERATURE, ROLE_DURATION, ROLE_TARGET_TEMPERATURE
from .store import PresetStore


@callback
def async_register(hass: HomeAssistant) -> None:
    websocket_api.async_register_command(hass, ws_appliances)
    websocket_api.async_register_command(hass, ws_list)
    websocket_api.async_register_command(hass, ws_save)
    websocket_api.async_register_command(hass, ws_delete)


def _store(hass: HomeAssistant) -> PresetStore:
    return hass.data[DOMAIN]


@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/appliances"})
@callback
def ws_appliances(hass: HomeAssistant, connection, msg: dict[str, Any]) -> None:
    """Configured appliances and what the editor may offer for each."""
    result = []
    for entry in hass.config_entries.async_loaded_entries(DOMAIN):
        runner: PresetRunner = entry.runtime_data
        appliance = runner.appliance
        result.append(
            {
                "entry_id": entry.entry_id,
                "title": entry.title,
                "device_id": runner.device_id,
                "entity_id": runner.entity_id,
                "state": runner.state,
                "programs": appliance.program_options,
                "setpoint": appliance.setpoint_bounds,
                "can_preheat": appliance.has(ROLE_CURRENT_TEMPERATURE),
                "has_setpoint": appliance.has(ROLE_TARGET_TEMPERATURE),
                "has_duration": appliance.has(ROLE_DURATION),
                "remote_start": appliance.remote_start,
                "missing": appliance.roles.missing_required,
            }
        )
    connection.send_result(msg["id"], result)


@websocket_api.websocket_command(
    {vol.Required("type"): f"{DOMAIN}/presets", vol.Optional("device_id"): str}
)
@callback
def ws_list(hass: HomeAssistant, connection, msg: dict[str, Any]) -> None:
    store = _store(hass)
    presets = store.for_device(msg["device_id"]) if "device_id" in msg else store.all()
    connection.send_result(msg["id"], [p.as_dict() for p in presets])


@websocket_api.websocket_command(
    {vol.Required("type"): f"{DOMAIN}/presets/save", vol.Required("preset"): dict}
)
@websocket_api.require_admin
@websocket_api.async_response
async def ws_save(hass: HomeAssistant, connection, msg: dict[str, Any]) -> None:
    try:
        preset = await _store(hass).async_save(msg["preset"])
    except vol.Invalid as err:
        connection.send_error(msg["id"], "invalid_format", str(err))
        return
    connection.send_result(msg["id"], preset.as_dict())


@websocket_api.websocket_command(
    {vol.Required("type"): f"{DOMAIN}/presets/delete", vol.Required("preset_id"): str}
)
@websocket_api.require_admin
@websocket_api.async_response
async def ws_delete(hass: HomeAssistant, connection, msg: dict[str, Any]) -> None:
    if not await _store(hass).async_delete(msg["preset_id"]):
        connection.send_error(msg["id"], "not_found", "No such preset")
        return
    connection.send_result(msg["id"])
