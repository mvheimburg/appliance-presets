"""A thin, role-based view of one appliance: read its state, drive its controls."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import Literal

from homeassistant.const import STATE_ON, STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers.event import async_call_later, async_track_state_change_event

from .const import (
    OP_ABORTING,
    OP_ACTION_REQUIRED,
    OP_DELAYED,
    OP_ERROR,
    OP_FINISHED,
    OP_OFF,
    OP_OFFLINE,
    OP_PAUSED,
    OP_READY,
    OP_RUNNING,
    OP_UNKNOWN,
)
from .roles import (
    ROLE_ABORT,
    ROLE_CURRENT_TEMPERATURE,
    ROLE_DOOR,
    ROLE_DURATION,
    ROLE_OPERATION,
    ROLE_POWER,
    ROLE_REMOTE_START,
    ROLE_SELECTED_PROGRAM,
    ROLE_START,
    ROLE_TARGET_TEMPERATURE,
    ApplianceRoles,
)

# Same normalisation as the card's model.ts: last dotted segment, lowercase,
# separators removed. Covers Local ("run") and cloud
# ("BSH.Common.EnumType.OperationState.Run") values.
_OPERATIONS = {
    "inactive": OP_OFF,
    "off": OP_OFF,
    "ready": OP_READY,
    "delayedstart": OP_DELAYED,
    "delayed": OP_DELAYED,
    "run": OP_RUNNING,
    "running": OP_RUNNING,
    "pause": OP_PAUSED,
    "paused": OP_PAUSED,
    "finished": OP_FINISHED,
    "error": OP_ERROR,
    "actionrequired": OP_ACTION_REQUIRED,
    "aborting": OP_ABORTING,
}


def normalize_operation(value: str | None) -> str:
    if value is None:
        return OP_UNKNOWN
    if value == STATE_UNAVAILABLE:
        return OP_OFFLINE
    key = value.rsplit(".", 1)[-1].lower().replace(" ", "").replace("_", "").replace("-", "")
    return _OPERATIONS.get(key, OP_UNKNOWN)


class Appliance:
    """Everything the engine knows about an appliance goes through here."""

    def __init__(self, hass: HomeAssistant, roles: ApplianceRoles) -> None:
        self.hass = hass
        self.roles = roles

    def _state(self, role: str):
        entity_id = self.roles.get(role)
        return self.hass.states.get(entity_id) if entity_id else None

    # --- reads -----------------------------------------------------------

    @property
    def operation(self) -> str:
        state = self._state(ROLE_OPERATION)
        return normalize_operation(state.state if state else None)

    @property
    def remote_start(self) -> Literal["yes", "no", "unverified"]:
        """Remote-start permission. Unknown and unavailable are a no."""
        if not self.roles.get(ROLE_REMOTE_START):
            return "unverified"
        state = self._state(ROLE_REMOTE_START)
        return "yes" if state is not None and state.state == STATE_ON else "no"

    @property
    def door_open(self) -> bool | None:
        state = self._state(ROLE_DOOR)
        if state is None or state.state in (STATE_UNKNOWN, STATE_UNAVAILABLE):
            return None
        return state.state.rsplit(".", 1)[-1].lower() in (STATE_ON, "open")

    @property
    def current_temperature(self) -> float | None:
        state = self._state(ROLE_CURRENT_TEMPERATURE)
        try:
            return float(state.state) if state else None
        except ValueError:
            return None

    @property
    def program_options(self) -> list[str]:
        state = self._state(ROLE_SELECTED_PROGRAM)
        return list(state.attributes.get("options", [])) if state else []

    @property
    def setpoint_bounds(self) -> tuple[float, float] | None:
        state = self._state(ROLE_TARGET_TEMPERATURE)
        if state is None:
            return None
        return (float(state.attributes.get("min", 0)), float(state.attributes.get("max", 300)))

    def has(self, role: str) -> bool:
        return self.roles.get(role) is not None

    # --- controls --------------------------------------------------------

    async def async_press(self, role: str) -> None:
        await self.hass.services.async_call(
            "button", "press", {"entity_id": self.roles.get(role)}, blocking=True
        )

    async def async_start(self) -> None:
        await self.async_press(ROLE_START)

    async def async_abort(self) -> None:
        await self.async_press(ROLE_ABORT)

    async def async_select_program(self, option: str) -> None:
        await self.hass.services.async_call(
            "select",
            "select_option",
            {"entity_id": self.roles.get(ROLE_SELECTED_PROGRAM), "option": option},
            blocking=True,
        )

    async def async_set_setpoint(self, value: float) -> None:
        await self._async_set_number(ROLE_TARGET_TEMPERATURE, value)

    async def async_set_duration(self, seconds: int) -> None:
        await self._async_set_number(ROLE_DURATION, seconds)

    async def async_power_off(self) -> None:
        if self.has(ROLE_POWER):
            await self.hass.services.async_call(
                "switch", "turn_off", {"entity_id": self.roles.get(ROLE_POWER)}, blocking=True
            )

    async def _async_set_number(self, role: str, value: float) -> None:
        await self.hass.services.async_call(
            "number",
            "set_value",
            {"entity_id": self.roles.get(role), "value": value},
            blocking=True,
        )

    # --- waiting ---------------------------------------------------------

    def watched(self, *roles: str) -> list[str]:
        return [e for role in roles if (e := self.roles.get(role))]

    async def async_wait(
        self, check: Callable[[], bool], timeout: float, roles: Iterable[str] = (ROLE_OPERATION,)
    ) -> bool:
        """Wait until check() holds, re-evaluated on each change of the roles' entities.

        The timeout runs on HA's scheduler (not asyncio.wait_for) so that it is
        driven by async_fire_time_changed in tests like every other timer.
        """
        if check():
            return True
        future = self.hass.loop.create_future()

        @callback
        def _changed(_event: Event) -> None:
            if not future.done() and check():
                future.set_result(True)

        @callback
        def _expired(_now) -> None:
            if not future.done():
                future.set_result(False)

        unsub_state = async_track_state_change_event(self.hass, self.watched(*roles), _changed)
        unsub_timer = async_call_later(self.hass, timeout, _expired)
        try:
            return await future
        finally:
            unsub_state()
            unsub_timer()
