"""Config flow: one entry per appliance; presets and notifications live in the options."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry, ConfigFlow, OptionsFlow
from homeassistant.core import callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.selector import selector

from .const import (
    APPLIANCE_INTEGRATION,
    CONF_ABORT_ON_DOOR_OPEN,
    CONF_DEVICE_ID,
    CONF_DOOR_GRACE_SECONDS,
    CONF_DOOR_MIN_SETPOINT,
    CONF_MAX_TOTAL_MINUTES,
    CONF_NOTIFY_ON,
    CONF_NOTIFY_PERSONS,
    CONF_PERSON_ENTITIES,
    CONF_PREHEAT_MINUTES,
    CONF_REQUIRE_HOME,
    DEFAULT_ABORT_ON_DOOR_OPEN,
    DEFAULT_DOOR_GRACE_SECONDS,
    DEFAULT_DOOR_MIN_SETPOINT,
    DEFAULT_MAX_TOTAL_MINUTES,
    DEFAULT_PREHEAT_MINUTES,
    DEFAULT_REQUIRE_HOME,
    DOMAIN,
    NOTIFY_KINDS,
)


def _number(minimum: int, maximum: int, unit: str) -> Any:
    return selector(
        {"number": {"min": minimum, "max": maximum, "mode": "box", "unit_of_measurement": unit}}
    )


def _options_schema(current: dict[str, Any], programmable: bool) -> vol.Schema:
    def default(key: str, fallback: Any) -> dict[str, Any]:
        return {"default": current.get(key, fallback)}

    def suggested(key: str, fallback: Any) -> dict[str, Any]:
        # Not a default: voluptuous would put a cleared field back.
        return {"description": {"suggested_value": current.get(key, fallback)}}

    notify = {
        vol.Optional(CONF_NOTIFY_PERSONS, **suggested(CONF_NOTIFY_PERSONS, [])): selector(
            {"entity": {"domain": "person", "multiple": True}}
        ),
        vol.Optional(CONF_NOTIFY_ON, **suggested(CONF_NOTIFY_ON, NOTIFY_KINDS)): selector(
            {
                "select": {
                    "options": NOTIFY_KINDS,
                    "multiple": True,
                    "mode": "list",
                    "translation_key": CONF_NOTIFY_ON,
                }
            }
        ),
    }
    if not programmable:
        return vol.Schema(notify)
    return vol.Schema(
        {
            vol.Required(
                CONF_PREHEAT_MINUTES, **default(CONF_PREHEAT_MINUTES, DEFAULT_PREHEAT_MINUTES)
            ): _number(0, 90, "min"),
            vol.Required(
                CONF_MAX_TOTAL_MINUTES,
                **default(CONF_MAX_TOTAL_MINUTES, DEFAULT_MAX_TOTAL_MINUTES),
            ): _number(10, 24 * 60, "min"),
            vol.Required(
                CONF_ABORT_ON_DOOR_OPEN,
                **default(CONF_ABORT_ON_DOOR_OPEN, DEFAULT_ABORT_ON_DOOR_OPEN),
            ): selector({"boolean": {}}),
            vol.Required(
                CONF_DOOR_MIN_SETPOINT,
                **default(CONF_DOOR_MIN_SETPOINT, DEFAULT_DOOR_MIN_SETPOINT),
            ): _number(0, 300, "°C"),
            vol.Required(
                CONF_DOOR_GRACE_SECONDS,
                **default(CONF_DOOR_GRACE_SECONDS, DEFAULT_DOOR_GRACE_SECONDS),
            ): _number(0, 900, "s"),
            vol.Required(
                CONF_REQUIRE_HOME, **default(CONF_REQUIRE_HOME, DEFAULT_REQUIRE_HOME)
            ): selector({"boolean": {}}),
            vol.Optional(CONF_PERSON_ENTITIES, **suggested(CONF_PERSON_ENTITIES, [])): selector(
                {"entity": {"domain": "person", "multiple": True}}
            ),
            **notify,
        }
    )


class AppliancePresetsConfigFlow(ConfigFlow, domain=DOMAIN):
    """Pick a Home Connect appliance. Any appliance can notify; ovens also run presets."""

    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None):
        if user_input is not None:
            device_id = user_input[CONF_DEVICE_ID]
            await self.async_set_unique_id(device_id)
            self._abort_if_unique_id_configured()
            device = dr.async_get(self.hass).async_get(device_id)
            title = (device.name_by_user or device.name) if device else device_id
            return self.async_create_entry(
                title=title or device_id,
                data={CONF_DEVICE_ID: device_id},
                options={},
            )
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_DEVICE_ID): selector(
                        {"device": {"integration": APPLIANCE_INTEGRATION}}
                    )
                }
            ),
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> AppliancePresetsOptionsFlow:
        return AppliancePresetsOptionsFlow()


class AppliancePresetsOptionsFlow(OptionsFlow):
    """Planning, safety and notification settings."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None):
        runner = getattr(self.config_entry, "runtime_data", None)
        programmable = getattr(runner, "programmable", True)
        current = dict(self.config_entry.options)
        schema = _options_schema(current, programmable)
        if user_input is not None:
            # A cleared optional field is left out of user_input, so only keep
            # options the form did not show (preset settings on a fridge).
            shown = {str(key) for key in schema.schema}
            kept = {k: v for k, v in current.items() if k not in shown}
            return self.async_create_entry(title="", data={**kept, **user_input})
        return self.async_show_form(
            step_id="init" if programmable else "notify", data_schema=schema
        )

    async def async_step_notify(self, user_input: dict[str, Any] | None = None):
        """The same form for an appliance without presets."""
        return await self.async_step_init(user_input)
