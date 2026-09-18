"""The preset runner: walks a preset's steps on one appliance.

Home Connect cannot change a running programme, so every step transition is a
small fallible workflow: abort -> wait until idle -> check remote start ->
select programme -> set setpoint/duration -> start -> wait until running, with
retries. Anything that goes wrong leaves the appliance aborted and the runner
failed with a reason; it never retries forever.

Safety properties, in order of how much they matter:

* The appliance's own duration is set on every step (timed: the step length,
  preheat: its timeout, hold: what is left of max_total_minutes), so the oven
  stops by itself even if Home Assistant dies mid-run.
* max_total_minutes aborts and powers down regardless of step state.
* A door left open above door_min_setpoint for door_grace_seconds aborts.
* Starting always requires confirm; aborting never does.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Coroutine
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import STATE_HOME
from homeassistant.core import CALLBACK_TYPE, Event, HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers.event import (
    async_track_point_in_utc_time,
    async_track_state_change_event,
)
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .appliance import Appliance
from .const import (
    ACTIVE_STATES,
    BUSY_OPERATIONS,
    CONF_ABORT_ON_DOOR_OPEN,
    CONF_DEVICE_ID,
    CONF_DOOR_GRACE_SECONDS,
    CONF_DOOR_MIN_SETPOINT,
    CONF_MAX_TOTAL_MINUTES,
    CONF_PERSON_ENTITIES,
    CONF_PREHEAT_MINUTES,
    CONF_REQUIRE_HOME,
    DEFAULT_ABORT_ON_DOOR_OPEN,
    DEFAULT_DOOR_GRACE_SECONDS,
    DEFAULT_DOOR_MIN_SETPOINT,
    DEFAULT_MAX_TOTAL_MINUTES,
    DEFAULT_PREHEAT_MINUTES,
    DEFAULT_REQUIRE_HOME,
    EVENT_ABORTED,
    EVENT_FAILED,
    EVENT_FINISHED,
    EVENT_SCHEDULED,
    EVENT_STARTED,
    EVENT_STEP_FINISHED,
    EVENT_STEP_STARTED,
    EVENT_TYPE,
    MISSED_START_GRACE,
    OP_OFFLINE,
    OP_RUNNING,
    OP_UNKNOWN,
    PREHEAT_TOLERANCE,
    REASON_APPLIANCE_STOPPED,
    REASON_BUSY,
    REASON_DOOR_OPENED,
    REASON_INTERRUPTED,
    REASON_INVALID_PROGRAM,
    REASON_INVALID_SETPOINT,
    REASON_MAX_DURATION,
    REASON_MISSED_START,
    REASON_MISSING_ROLE,
    REASON_NOBODY_HOME,
    REASON_OFFLINE,
    REASON_PREHEAT_TIMEOUT,
    REASON_REMOTE_START,
    REASON_START_FAILED,
    REASON_STOP_FAILED,
    RECOVERY_TIMEOUT,
    RUN_STORAGE_KEY,
    START_ATTEMPTS,
    START_TIMEOUT,
    STATE_FAILED,
    STATE_FINISHED,
    STATE_IDLE,
    STATE_RUNNING,
    STATE_SCHEDULED,
    STEP_HOLD,
    STEP_PREHEAT,
    STEP_TIMED,
    STOP_TIMEOUT,
    STORAGE_VERSION,
)
from .models import Preset, Step
from .roles import (
    ROLE_CURRENT_TEMPERATURE,
    ROLE_DOOR,
    ROLE_DURATION,
    ROLE_OPERATION,
    ROLE_REMOTE_START,
    ROLE_TARGET_TEMPERATURE,
    resolve_roles,
)
from .store import PresetStore

_LOGGER = logging.getLogger(__name__)

# Seconds a scheduled start may be in the future before it is "scheduled"
# rather than started immediately.
_IMMEDIATE = 5
# A timed step whose appliance stops this close to the planned end has
# finished, not been interrupted.
_END_SLACK = timedelta(seconds=90)


class RunFailed(Exception):
    """A step could not be started; carries the failure reason."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(slots=True)
class RunRecord:
    """What survives a restart. Datetimes are ISO strings."""

    state: str = STATE_IDLE
    preset: dict[str, Any] | None = None  # snapshot: editing a preset never changes a run
    step_index: int = 0
    start_at: str | None = None
    ready_at: str | None = None
    started_at: str | None = None
    step_started_at: str | None = None
    step_ends_at: str | None = None
    last_error: str | None = None


def _parse(value: str | None) -> datetime | None:
    return dt_util.parse_datetime(value) if value else None


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


class PresetRunner:
    """One runner per config entry (= per appliance)."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, presets: PresetStore) -> None:
        self.hass = hass
        self.entry = entry
        self.presets = presets
        self.device_id: str = entry.data[CONF_DEVICE_ID]
        self.entity_id: str | None = None
        self.record = RunRecord()
        self._store: Store[dict[str, Any]] = Store(
            hass, STORAGE_VERSION, f"{RUN_STORAGE_KEY}.{entry.entry_id}"
        )
        self._listeners: list[Callable[[], None]] = []
        self._unsubs: list[CALLBACK_TYPE] = []  # whole-run handlers
        self._step_unsubs: list[CALLBACK_TYPE] = []  # current-step handlers
        self._tasks: set[asyncio.Task[None]] = set()
        self._door_timer: CALLBACK_TYPE | None = None
        self._transitioning = False
        self._appliance: Appliance | None = None

    # --- options ---------------------------------------------------------

    def _opt(self, key: str, default: Any) -> Any:
        return self.entry.options.get(key, default)

    @property
    def preheat_minutes(self) -> int:
        return int(self._opt(CONF_PREHEAT_MINUTES, DEFAULT_PREHEAT_MINUTES))

    @property
    def max_total(self) -> timedelta:
        return timedelta(minutes=int(self._opt(CONF_MAX_TOTAL_MINUTES, DEFAULT_MAX_TOTAL_MINUTES)))

    # --- public state ----------------------------------------------------

    @property
    def appliance(self) -> Appliance:
        # Re-resolved lazily: entities may be registered after we are.
        if self._appliance is None or self._appliance.roles.missing_required:
            self._appliance = Appliance(self.hass, resolve_roles(self.hass, self.device_id))
        return self._appliance

    @property
    def state(self) -> str:
        return self.record.state

    @property
    def preset(self) -> Preset | None:
        return Preset.from_dict(self.record.preset) if self.record.preset else None

    @property
    def step(self) -> Step | None:
        preset = self.preset
        if preset is None or self.record.state != STATE_RUNNING:
            return None
        return preset.steps[self.record.step_index]

    def estimated_finish(self) -> datetime | None:
        preset = self.preset
        if preset is None or self.record.state not in ACTIVE_STATES:
            return None
        if self.record.state == STATE_SCHEDULED:
            start = _parse(self.record.start_at)
            return start + preset.planned_duration(self.preheat_minutes) if start else None
        step = preset.steps[self.record.step_index]
        if step.kind == STEP_PREHEAT:
            # The step deadline is the preheat timeout; plan with the estimate.
            base = (_parse(self.record.step_started_at) or dt_util.utcnow()) + timedelta(
                minutes=self.preheat_minutes
            )
        else:
            base = _parse(self.record.step_ends_at) or dt_util.utcnow()
        rest = preset.steps[self.record.step_index + 1 :]
        return base + sum((s.planned_duration(self.preheat_minutes) for s in rest), timedelta(0))

    @property
    def attributes(self) -> dict[str, Any]:
        preset, step = self.preset, self.step
        return {
            "preset": preset.name if preset else None,
            "preset_id": preset.id if preset else None,
            "step_index": self.record.step_index if step else None,
            "step_count": len(preset.steps) if preset else None,
            "step_name": step.label if step else None,
            "step_kind": step.kind if step else None,
            "step_program": step.program if step else None,
            "step_setpoint": step.setpoint if step else None,
            "step_ends_at": self.record.step_ends_at if step else None,
            "started_at": self.record.started_at,
            "start_at": self.record.start_at,
            "ready_at": self.record.ready_at,
            "estimated_finish": _iso(self.estimated_finish()),
            "last_error": self.record.last_error,
            "device_id": self.device_id,
            "presets": [p.name for p in self.presets.for_device(self.device_id)],
            "remote_start": self.appliance.remote_start,
        }

    @callback
    def async_add_listener(self, listener: Callable[[], None]) -> Callable[[], None]:
        self._listeners.append(listener)
        return lambda: self._listeners.remove(listener)

    @callback
    def _spawn(self, coro: Coroutine[Any, Any, None], name: str) -> None:
        task = self.entry.async_create_background_task(self.hass, coro, f"appliance_presets_{name}")
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    @callback
    def _cancel_tasks(self) -> None:
        """Cancel in-flight work, e.g. a transition about to press start."""
        current = asyncio.current_task()
        for task in list(self._tasks):
            if task is not current:
                task.cancel()

    @callback
    def _notify(self) -> None:
        for listener in list(self._listeners):
            listener()

    # --- lifecycle -------------------------------------------------------

    async def async_setup(self) -> None:
        """Load the persisted run and pick up where it left off."""
        if data := await self._store.async_load():
            self.record = RunRecord(**data)
        if self.record.state == STATE_SCHEDULED:
            self._recover_scheduled()
        elif self.record.state == STATE_RUNNING:
            self._spawn(self._async_recover_running(), "recover")

    @callback
    def async_shutdown(self) -> None:
        """Stop listening. The record stays as-is so a restart can resume."""
        self._clear_handlers()

    # --- commands --------------------------------------------------------

    async def async_run(
        self,
        preset_key: str,
        *,
        start_at: datetime | None = None,
        ready_at: datetime | None = None,
        confirm: bool = False,
    ) -> None:
        if self.record.state in ACTIVE_STATES:
            raise ServiceValidationError(f"A preset is already {self.record.state}; abort it first")
        if not confirm:
            raise ServiceValidationError("Starting an appliance remotely needs confirm: true")
        preset = self.presets.find(preset_key, self.device_id)
        if preset is None:
            raise ServiceValidationError(f"No preset {preset_key!r} for this appliance")
        if start_at and ready_at:
            raise ServiceValidationError("Give start_at or ready_at, not both")

        # Refuse up front rather than start hopefully.
        self._appliance = None
        if missing := self.appliance.roles.missing_required:
            raise ServiceValidationError(f"Appliance is missing: {', '.join(missing)}")
        if self.appliance.remote_start == "no":
            raise ServiceValidationError(
                "Remote start is not enabled on the appliance; arm it there first"
            )
        self._validate_steps(preset)

        now = dt_util.utcnow()
        if ready_at is not None:
            start_at = preset.start_for_ready(dt_util.as_utc(ready_at), self.preheat_minutes)
        start = dt_util.as_utc(start_at) if start_at else now
        if start < now - timedelta(seconds=_IMMEDIATE):
            raise ServiceValidationError("That start time has already passed")

        self.record = RunRecord(
            state=STATE_SCHEDULED,
            preset=preset.as_dict(),
            start_at=_iso(start),
            ready_at=_iso(dt_util.as_utc(ready_at)) if ready_at else None,
        )
        if start > now + timedelta(seconds=_IMMEDIATE):
            self._arm_schedule(start)
            await self._async_commit()
            self._fire(EVENT_SCHEDULED, start_at=_iso(start))
            return
        await self._async_commit()
        self._begin()

    async def async_abort(self) -> None:
        """Stop whatever is scheduled or running. Never needs confirmation."""
        was = self.record.state
        self._cancel_tasks()
        self._clear_handlers()
        self._transitioning = False
        if was == STATE_RUNNING and self.appliance.operation in BUSY_OPERATIONS:
            await self._async_safe_abort()
        if was in ACTIVE_STATES:
            self.record.state = STATE_IDLE
            await self._async_commit()
            self._fire(EVENT_ABORTED)
        elif was in (STATE_FINISHED, STATE_FAILED):
            # Acknowledge a finished/failed run.
            self.record = RunRecord()
            await self._async_commit()

    # --- the run ---------------------------------------------------------

    @callback
    def _begin(self) -> None:
        self._spawn(self._async_begin(), "begin")

    async def _async_begin(self) -> None:
        if not self._someone_home():
            await self._async_fail(REASON_NOBODY_HOME, abort_appliance=False)
            return
        if self.appliance.operation == OP_OFFLINE:
            await self._async_fail(REASON_OFFLINE, abort_appliance=False)
            return
        if self.appliance.operation in BUSY_OPERATIONS:
            # Something else is running; it is not ours to abort.
            await self._async_fail(REASON_BUSY, abort_appliance=False)
            return
        now = dt_util.utcnow()
        self.record.state = STATE_RUNNING
        self.record.started_at = _iso(now)
        self.record.step_index = 0
        self._arm_run_guards(now)
        self._fire(EVENT_STARTED)
        await self._async_run_step(0)

    async def _async_run_step(self, index: int) -> None:
        preset = self.preset
        assert preset is not None
        step = preset.steps[index]
        self.record.step_index = index
        self._clear_step_handlers()
        self._transitioning = True
        try:
            await self._async_transition(step, first=index == 0)
        except RunFailed as err:
            await self._async_fail(err.reason)
            return
        finally:
            self._transitioning = False
        if self.record.state != STATE_RUNNING:
            return  # aborted while transitioning

        now = dt_util.utcnow()
        self.record.step_started_at = _iso(now)
        if step.kind == STEP_TIMED:
            ends: datetime | None = now + timedelta(minutes=step.duration_minutes or 0)
        elif step.kind == STEP_PREHEAT:
            ends = now + step.preheat_timeout  # a deadline, not a plan
        else:
            ends = None
        self.record.step_ends_at = _iso(ends)
        await self._async_commit()
        self._fire(EVENT_STEP_STARTED, step_index=index, step_name=step.label)
        self._arm_step(step, ends)

    async def _async_transition(self, step: Step, *, first: bool) -> None:
        appliance = self.appliance
        if appliance.operation in BUSY_OPERATIONS:
            if first:
                raise RunFailed(REASON_BUSY)
            await appliance.async_abort()
            if not await appliance.async_wait(
                lambda: appliance.operation not in BUSY_OPERATIONS, STOP_TIMEOUT
            ):
                raise RunFailed(REASON_STOP_FAILED)

        # Many models clear remote start after each programme. Give it a
        # moment to re-arm, then refuse rather than hope.
        if appliance.remote_start == "no" and not await appliance.async_wait(
            lambda: appliance.remote_start != "no", 10, roles=(ROLE_REMOTE_START,)
        ):
            raise RunFailed(REASON_REMOTE_START)

        seconds = self._appliance_duration(step)
        for attempt in range(1, START_ATTEMPTS + 1):
            try:
                await appliance.async_select_program(step.program)
                if step.setpoint is not None:
                    await appliance.async_set_setpoint(step.setpoint)
                if seconds is not None:
                    await appliance.async_set_duration(seconds)
                await appliance.async_start()
            except HomeAssistantError as err:
                _LOGGER.warning("Starting %s failed (attempt %s): %s", step.label, attempt, err)
            else:
                if await appliance.async_wait(
                    lambda: appliance.operation == OP_RUNNING, START_TIMEOUT
                ):
                    return
                _LOGGER.warning("%s did not start (attempt %s)", step.label, attempt)
            if appliance.remote_start == "no":
                raise RunFailed(REASON_REMOTE_START)
        raise RunFailed(REASON_START_FAILED)

    def _appliance_duration(self, step: Step) -> int | None:
        """Seconds to program into the appliance itself, as a dead-man's switch."""
        if not self.appliance.has(ROLE_DURATION):
            return None
        if step.kind == STEP_TIMED:
            seconds = (step.duration_minutes or 0) * 60
        elif step.kind == STEP_PREHEAT:
            seconds = int(step.preheat_timeout.total_seconds())
        else:
            started = _parse(self.record.started_at) or dt_util.utcnow()
            remaining = started + self.max_total - dt_util.utcnow()
            seconds = max(60, int(remaining.total_seconds()))
        state = self.hass.states.get(self.appliance.roles.get(ROLE_DURATION) or "")
        if state is not None and (upper := state.attributes.get("max")) is not None:
            seconds = min(seconds, int(float(upper)))
        return seconds

    async def _async_step_done(self) -> None:
        preset = self.preset
        if preset is None or self.record.state != STATE_RUNNING:
            return
        index = self.record.step_index
        self._clear_step_handlers()
        self._fire(EVENT_STEP_FINISHED, step_index=index, step_name=preset.steps[index].label)
        if index + 1 < len(preset.steps):
            await self._async_run_step(index + 1)
            return
        # Stop listening before stopping the appliance, or our own abort
        # reads as the appliance stopping underneath us.
        self._clear_handlers()
        if self.appliance.operation in BUSY_OPERATIONS:
            await self._async_safe_abort()
        self.record.state = STATE_FINISHED
        self.record.step_ends_at = None
        await self._async_commit()
        self._fire(EVENT_FINISHED)

    async def _async_fail(self, reason: str, *, abort_appliance: bool = True) -> None:
        _LOGGER.warning("Preset on %s failed: %s", self.entity_id, reason)
        # A guard can fire mid-transition; that transition must not go on to
        # press start after we have aborted.
        self._cancel_tasks()
        self._clear_handlers()
        if abort_appliance and self.appliance.operation in BUSY_OPERATIONS:
            await self._async_safe_abort()
        self.record.state = STATE_FAILED
        self.record.last_error = reason
        await self._async_commit()
        self._fire(EVENT_FAILED, reason=reason)

    async def _async_safe_abort(self) -> None:
        try:
            await self.appliance.async_abort()
        except HomeAssistantError:
            _LOGGER.exception("Could not abort the appliance on %s", self.entity_id)

    # --- handlers --------------------------------------------------------

    @callback
    def _arm_schedule(self, start: datetime) -> None:
        @callback
        def _due(_now: datetime) -> None:
            self._begin()

        self._unsubs.append(async_track_point_in_utc_time(self.hass, _due, start))

    @callback
    def _arm_run_guards(self, started: datetime) -> None:
        """Guards that hold for the whole run, independent of the step."""

        @callback
        def _max_reached(_now: datetime) -> None:
            self._spawn(self._async_max_reached(), "max")

        self._unsubs.append(
            async_track_point_in_utc_time(self.hass, _max_reached, started + self.max_total)
        )
        watched = self.appliance.watched(ROLE_DOOR, ROLE_OPERATION)
        if watched:
            self._unsubs.append(
                async_track_state_change_event(self.hass, watched, self._on_appliance_change)
            )

    async def _async_max_reached(self) -> None:
        await self._async_fail(REASON_MAX_DURATION)
        try:
            await self.appliance.async_power_off()
        except HomeAssistantError:
            _LOGGER.exception("Could not power off %s", self.entity_id)

    @callback
    def _on_appliance_change(self, event: Event) -> None:
        if self.record.state != STATE_RUNNING or self._transitioning:
            return
        entity_id = event.data["entity_id"]
        if entity_id == self.appliance.roles.get(ROLE_DOOR):
            self._check_door()
        elif entity_id == self.appliance.roles.get(ROLE_OPERATION):
            self._check_operation()

    @callback
    def _check_door(self) -> None:
        step = self.step
        guarded = (
            self._opt(CONF_ABORT_ON_DOOR_OPEN, DEFAULT_ABORT_ON_DOOR_OPEN)
            and step is not None
            and (step.setpoint or 0) >= self._opt(CONF_DOOR_MIN_SETPOINT, DEFAULT_DOOR_MIN_SETPOINT)
        )
        if self.appliance.door_open and guarded:
            if self._door_timer is None:

                @callback
                def _grace_over(_now: datetime) -> None:
                    self._door_timer = None
                    self._spawn(self._async_fail(REASON_DOOR_OPENED), "door")

                self._door_timer = async_track_point_in_utc_time(
                    self.hass,
                    _grace_over,
                    dt_util.utcnow()
                    + timedelta(
                        seconds=self._opt(CONF_DOOR_GRACE_SECONDS, DEFAULT_DOOR_GRACE_SECONDS)
                    ),
                )
        elif self._door_timer is not None:
            self._door_timer()
            self._door_timer = None

    @callback
    def _check_operation(self) -> None:
        """The appliance stopped without us asking."""
        operation = self.appliance.operation
        if operation in BUSY_OPERATIONS or operation in (OP_OFFLINE, OP_UNKNOWN):
            return  # offline flaps are ridden out; max_total still bounds the run
        step, ends = self.step, _parse(self.record.step_ends_at)
        if (
            step is not None
            and step.kind == STEP_TIMED
            and ends
            and dt_util.utcnow() >= ends - _END_SLACK
        ):
            self._spawn(self._async_step_done(), "step")
            return
        self._spawn(self._async_fail(REASON_APPLIANCE_STOPPED, abort_appliance=False), "stopped")

    @callback
    def _arm_step(self, step: Step, ends: datetime | None) -> None:
        @callback
        def _done(_now: datetime | None = None) -> None:
            self._spawn(self._async_step_done(), "step")

        if step.kind == STEP_TIMED and ends is not None:
            self._step_unsubs.append(async_track_point_in_utc_time(self.hass, _done, ends))
        elif step.kind == STEP_PREHEAT and ends is not None:
            target = (step.setpoint or 0) - PREHEAT_TOLERANCE

            def reached() -> bool:
                current = self.appliance.current_temperature
                return current is not None and current >= target

            @callback
            def _temperature(_event: Event) -> None:
                if reached() and self.record.state == STATE_RUNNING:
                    self._clear_step_handlers()
                    _done()

            @callback
            def _timeout(_now: datetime) -> None:
                self._spawn(self._async_fail(REASON_PREHEAT_TIMEOUT), "preheat")

            if reached():
                _done()
                return
            self._step_unsubs = [
                async_track_state_change_event(
                    self.hass, self.appliance.watched(ROLE_CURRENT_TEMPERATURE), _temperature
                ),
                async_track_point_in_utc_time(self.hass, _timeout, ends),
            ]
            return
        # STEP_HOLD: runs until aborted or max_total.

    @callback
    def _clear_step_handlers(self) -> None:
        for unsub in self._step_unsubs:
            unsub()
        self._step_unsubs = []
        if self._door_timer is not None:
            self._door_timer()
            self._door_timer = None

    @callback
    def _clear_handlers(self) -> None:
        self._clear_step_handlers()
        for unsub in self._unsubs:
            unsub()
        self._unsubs = []

    # --- recovery --------------------------------------------------------

    @callback
    def _recover_scheduled(self) -> None:
        start = _parse(self.record.start_at)
        now = dt_util.utcnow()
        if start is None or now > start + timedelta(seconds=MISSED_START_GRACE):
            self._spawn(self._async_fail(REASON_MISSED_START, abort_appliance=False), "missed")
        elif start <= now:
            self._begin()
        else:
            self._arm_schedule(start)

    async def _async_recover_running(self) -> None:
        """HA restarted mid-run. Re-attach if the appliance is still going."""
        appliance = self.appliance
        await appliance.async_wait(
            lambda: appliance.operation not in (OP_OFFLINE, OP_UNKNOWN), RECOVERY_TIMEOUT
        )
        preset = self.preset
        started = _parse(self.record.started_at)
        if preset is None or started is None:
            await self._async_fail(REASON_INTERRUPTED, abort_appliance=False)
            return
        now = dt_util.utcnow()
        if now >= started + self.max_total:
            await self._async_max_reached()
            return
        step = preset.steps[self.record.step_index]
        ends = _parse(self.record.step_ends_at)
        running = appliance.operation == OP_RUNNING
        self._arm_run_guards(started)
        if step.kind == STEP_TIMED and ends and now >= ends - _END_SLACK:
            # The step ran out while we were down (the appliance's own timer
            # stopped it, or is about to): move on.
            await self._async_step_done()
        elif not running:
            await self._async_fail(REASON_INTERRUPTED, abort_appliance=False)
        else:
            self._arm_step(step, ends)
            self._notify()

    # --- helpers ---------------------------------------------------------

    def _validate_steps(self, preset: Preset) -> None:
        options = self.appliance.program_options
        bounds = self.appliance.setpoint_bounds
        for step in preset.steps:
            if options and step.program not in options:
                raise ServiceValidationError(
                    f"Step {step.label!r}: {step.program!r} is not a programme this appliance offers"
                    f" ({REASON_INVALID_PROGRAM})"
                )
            if step.setpoint is not None:
                if not self.appliance.has(ROLE_TARGET_TEMPERATURE):
                    raise ServiceValidationError(
                        f"Step {step.label!r} sets a temperature but the appliance has no"
                        f" setpoint ({REASON_MISSING_ROLE})"
                    )
                if bounds and not bounds[0] <= step.setpoint <= bounds[1]:
                    raise ServiceValidationError(
                        f"Step {step.label!r}: {step.setpoint} is outside {bounds}"
                        f" ({REASON_INVALID_SETPOINT})"
                    )
            if step.kind == STEP_PREHEAT and not self.appliance.has(ROLE_CURRENT_TEMPERATURE):
                raise ServiceValidationError(
                    f"Step {step.label!r} waits for a temperature the appliance does not report"
                    f" ({REASON_MISSING_ROLE})"
                )
            if step.kind == STEP_HOLD and step.setpoint is None:
                _LOGGER.debug("Hold step %s keeps the programme default", step.label)

    def _someone_home(self) -> bool:
        if not self._opt(CONF_REQUIRE_HOME, DEFAULT_REQUIRE_HOME):
            return True
        people = self._opt(CONF_PERSON_ENTITIES, [])
        if not people:
            return True
        return any(
            (state := self.hass.states.get(p)) is not None and state.state == STATE_HOME
            for p in people
        )

    async def _async_commit(self) -> None:
        await self._store.async_save(asdict(self.record))
        self._notify()

    @callback
    def _fire(self, event_type: str, **data: Any) -> None:
        preset = self.preset
        self.hass.bus.async_fire(
            EVENT_TYPE,
            {
                "entity_id": self.entity_id,
                "type": event_type,
                "preset": preset.name if preset else None,
                **data,
            },
        )
        self._notify()
