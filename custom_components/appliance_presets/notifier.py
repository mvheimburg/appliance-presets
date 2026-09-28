"""Notify when home: an extra phone notification for appliance alarms.

The appliance already beeps. This sends the chosen people who are home a push
notification as well, through their Home Assistant Companion app. Nobody away
is disturbed, and nothing is sent when no chosen person is home.

Home Connect Local reports events as binary sensors that turn on when the
event is present: the kitchen timer ran out, the programme finished, a door or
temperature alarm, an empty tank. Only an off -> on change counts, so a
restart or a reconnect (unavailable -> on) does not resend old news.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EVENT_STATE_CHANGED, STATE_HOME, STATE_OFF, STATE_ON
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
from homeassistant.util import slugify

from .const import (
    CONF_NOTIFY_ON,
    CONF_NOTIFY_PERSONS,
    DOMAIN,
    EVENT_FAILED,
    EVENT_FINISHED,
    EVENT_TYPE,
    NOTIFY_ALARM_CLOCK,
    NOTIFY_DEDUPE_SECONDS,
    NOTIFY_FINISHED,
    NOTIFY_KINDS,
    NOTIFY_PROBLEM,
    STATE_RUNNING,
)
from .roles import semantic_key

_LOGGER = logging.getLogger(__name__)

# Home Connect Local unique-id keys. The problem pattern agrees with the
# card's "attention" role (lovelace-appliance-panel src/roles.ts), less the
# programme-aborted event: a preset aborts the programme at every step.
_ALARM_CLOCK = re.compile(r"^binary_sensor_(?:oven_)?alarm_clock_elapsed(?:_\d+)?$")
_FINISHED = re.compile(r"^binary_sensor_program_finished$")
_PROBLEM = re.compile(
    r"^binary_sensor_(?:(?:door|temperature)_alarm_(?:freezer|fridge|chiller_common)"
    r"|.*(?:alarm|error|lack|empty|low_water_pressure|aqua_stop|water_filter_(?:almost_)?full))$"
)
_ABORTED = "binary_sensor_program_aborted"

_MESSAGES: dict[str, dict[str, str]] = {
    "en": {
        NOTIFY_ALARM_CLOCK: "The timer is done",
        NOTIFY_FINISHED: "The programme has finished",
        "preset_finished": "{preset} is done",
        NOTIFY_PROBLEM: "Needs attention: {alarm}",
        "preset_failed": "{preset} stopped: {reason}",
    },
    "nb": {
        NOTIFY_ALARM_CLOCK: "Tidsuret er ferdig",
        NOTIFY_FINISHED: "Programmet er ferdig",
        "preset_finished": "{preset} er ferdig",
        NOTIFY_PROBLEM: "Trenger oppmerksomhet: {alarm}",
        "preset_failed": "{preset} stoppet: {reason}",
    },
}


def message_language(language: str | None) -> str:
    """nb, nb-NO, no and nn (no Nynorsk of its own) get Bokmål; others English."""
    base = (language or "").strip().lower().replace("_", "-").split("-", 1)[0]
    return "nb" if base in ("nb", "no", "nn") else "en"


def _classify_key(key: str) -> str | None:
    if key == _ABORTED:
        return None
    if _ALARM_CLOCK.match(key):
        return NOTIFY_ALARM_CLOCK
    if _FINISHED.match(key):
        return NOTIFY_FINISHED
    if _PROBLEM.match(key):
        return NOTIFY_PROBLEM
    return None


def classify(entry: er.RegistryEntry, device_class: str | None = None) -> str | None:
    """Which kind of news an appliance binary sensor carries, if any."""
    if key := semantic_key(entry.unique_id):
        kind = _classify_key(key)
        if kind is None and key != _ABORTED and device_class == "problem":
            return NOTIFY_PROBLEM
        return kind
    # A renamed or foreign entity: try the tails of its entity id, as the card does.
    parts = entry.entity_id.partition(".")[2].split("_")
    kinds = {
        kind
        for i in range(len(parts))
        if (kind := _classify_key("binary_sensor_" + "_".join(parts[i:])))
    }
    if len(kinds) == 1:
        return kinds.pop()
    return NOTIFY_PROBLEM if device_class == "problem" else None


def notify_services(hass: HomeAssistant, person_id: str) -> list[str]:
    """The Companion-app notify services of a person who is home."""
    state = hass.states.get(person_id)
    if state is None or state.state != STATE_HOME:
        return []
    entities, devices = er.async_get(hass), dr.async_get(hass)
    services: list[str] = []
    for tracker in state.attributes.get("device_trackers", []):
        entry = entities.async_get(tracker)
        if entry is None or entry.platform != "mobile_app" or entry.device_id is None:
            continue
        device = devices.async_get(entry.device_id)
        # mobile_app names its service after the registered device name.
        if device is None or not device.name:
            continue
        service = f"mobile_app_{slugify(device.name)}"
        if hass.services.has_service("notify", service) and service not in services:
            services.append(service)
    return services


class AlarmNotifier:
    """Watches one appliance and its preset runner; notifies chosen people at home."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, runner: Any) -> None:
        self.hass = hass
        self.entry = entry
        self.runner = runner
        self._sent: dict[str, datetime] = {}

    @callback
    def async_setup(self) -> None:
        self.entry.async_on_unload(
            self.hass.bus.async_listen(EVENT_STATE_CHANGED, self._on_state_changed)
        )
        self.entry.async_on_unload(self.hass.bus.async_listen(EVENT_TYPE, self._on_preset_event))

    @property
    def _kinds(self) -> list[str]:
        return list(self.entry.options.get(CONF_NOTIFY_ON, NOTIFY_KINDS))

    @property
    def _people(self) -> list[str]:
        return list(self.entry.options.get(CONF_NOTIFY_PERSONS, []))

    @property
    def _title(self) -> str:
        device = dr.async_get(self.hass).async_get(self.runner.device_id)
        return (device and (device.name_by_user or device.name)) or self.entry.title

    def _text(self, key: str, **placeholders: str) -> str:
        messages = _MESSAGES[message_language(self.hass.config.language)]
        return messages[key].format(**placeholders)

    @callback
    def _on_state_changed(self, event: Event) -> None:
        entity_id: str = event.data["entity_id"]
        if not self._people or not entity_id.startswith("binary_sensor."):
            return
        old, new = event.data.get("old_state"), event.data.get("new_state")
        if old is None or new is None or old.state != STATE_OFF or new.state != STATE_ON:
            return
        entry = er.async_get(self.hass).async_get(entity_id)
        if entry is None or entry.device_id != self.runner.device_id:
            return
        kind = classify(entry, new.attributes.get("device_class"))
        if kind is None:
            return
        if kind == NOTIFY_FINISHED and self.runner.state == STATE_RUNNING:
            return  # a preset step ended; the preset reports its own finish
        if kind == NOTIFY_PROBLEM:
            alarm = entry.name or entry.original_name or new.name
            self._send(kind, self._text(kind, alarm=alarm), tag=entity_id)
        else:
            self._send(kind, self._text(kind))

    @callback
    def _on_preset_event(self, event: Event) -> None:
        if event.data.get("entity_id") != self.runner.entity_id or not self._people:
            return
        preset = event.data.get("preset") or ""
        if event.data.get("type") == EVENT_FINISHED:
            self._send(NOTIFY_FINISHED, self._text("preset_finished", preset=preset))
        elif event.data.get("type") == EVENT_FAILED:
            reason = str(event.data.get("reason", ""))
            self._send(
                NOTIFY_PROBLEM,
                self._text("preset_failed", preset=preset, reason=reason),
                tag="preset",
            )

    @callback
    def _send(self, kind: str, message: str, *, tag: str = "") -> None:
        if kind not in self._kinds:
            return
        dedupe = f"{kind}:{tag}"
        now = dt_util.utcnow()
        if (last := self._sent.get(dedupe)) and now - last < timedelta(
            seconds=NOTIFY_DEDUPE_SECONDS
        ):
            return
        services: list[str] = []
        for person in self._people:
            services += [s for s in notify_services(self.hass, person) if s not in services]
        if not services:
            return
        self._sent[dedupe] = now
        data = {
            "title": self._title,
            "message": message,
            "data": {
                # A newer notification of the same kind replaces the older one.
                "tag": f"{DOMAIN}_{self.entry.entry_id}_{kind}_{slugify(tag)}".rstrip("_"),
                "group": DOMAIN,
                "push": {"interruption-level": "time-sensitive"},
                "ttl": 0,
                "priority": "high",
            },
        }
        for service in services:
            self.entry.async_create_background_task(
                self.hass,
                self._async_call(service, data),
                f"{DOMAIN}_notify_{service}",
            )

    async def _async_call(self, service: str, data: dict[str, Any]) -> None:
        try:
            await self.hass.services.async_call("notify", service, data, blocking=True)
        except Exception:  # noqa: BLE001 - one broken phone must not stop the rest
            _LOGGER.exception("Could not notify %s", service)
