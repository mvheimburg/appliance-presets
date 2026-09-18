"""The Appliance Presets integration: staged, multi-step appliance programmes."""

from __future__ import annotations

import logging
from pathlib import Path

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.typing import ConfigType

from . import websocket
from .const import (
    DOMAIN,
    PANEL_COMPONENT,
    PANEL_STATIC_URL,
    PANEL_URL_PATH,
    PLATFORMS,
    SERVICE_DELETE,
    SERVICE_SAVE,
)
from .engine import PresetRunner
from .models import PRESET_SCHEMA
from .store import PresetStore

_LOGGER = logging.getLogger(__name__)

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

type AppliancePresetsConfigEntry = ConfigEntry[PresetRunner]


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up what is shared by every appliance: the preset library, API and panel."""
    store = PresetStore(hass)
    await store.async_load()
    hass.data[DOMAIN] = store
    websocket.async_register(hass)

    async def _save(call: ServiceCall) -> dict:
        try:
            preset = await store.async_save(dict(call.data))
        except vol.Invalid as err:
            raise ServiceValidationError(str(err)) from err
        return preset.as_dict()

    async def _delete(call: ServiceCall) -> None:
        if not await store.async_delete(call.data["preset_id"]):
            raise ServiceValidationError(f"No preset {call.data['preset_id']!r}")

    hass.services.async_register(
        DOMAIN,
        SERVICE_SAVE,
        _save,
        schema=PRESET_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(
        DOMAIN, SERVICE_DELETE, _delete, schema=vol.Schema({vol.Required("preset_id"): cv.string})
    )

    await _async_register_panel(hass)
    return True


async def _async_register_panel(hass: HomeAssistant) -> None:
    """A sidebar panel for editing presets, served from this integration."""
    if "frontend" not in hass.config.components:
        return  # e.g. tests, or a headless install
    from homeassistant.components import panel_custom
    from homeassistant.components.http import StaticPathConfig

    await hass.http.async_register_static_paths(
        [StaticPathConfig(PANEL_STATIC_URL, str(Path(__file__).parent / "frontend"), True)]
    )
    await panel_custom.async_register_panel(
        hass,
        frontend_url_path=PANEL_URL_PATH,
        webcomponent_name=PANEL_COMPONENT,
        sidebar_title="Presets",
        sidebar_icon="mdi:chef-hat",
        module_url=f"{PANEL_STATIC_URL}/{PANEL_COMPONENT}.js",
        require_admin=False,
    )


async def async_setup_entry(hass: HomeAssistant, entry: AppliancePresetsConfigEntry) -> bool:
    """Set up a preset runner for one appliance."""
    runner = PresetRunner(hass, entry, hass.data[DOMAIN])
    entry.runtime_data = runner
    await runner.async_setup()
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(runner.async_shutdown)
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    return True


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Options only change safety settings, which the runner reads live."""
    entry.runtime_data._notify()


async def async_unload_entry(hass: HomeAssistant, entry: AppliancePresetsConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
