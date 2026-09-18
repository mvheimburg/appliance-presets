"""Preset and step models: validation, (de)serialisation and planning."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from typing import Any

import voluptuous as vol
from homeassistant.helpers import config_validation as cv
from homeassistant.util import slugify

from .const import (
    DEFAULT_PREHEAT_TIMEOUT_MINUTES,
    STEP_HOLD,
    STEP_KINDS,
    STEP_PREHEAT,
    STEP_TIMED,
)


def _validate_step(step: dict[str, Any]) -> dict[str, Any]:
    kind = step["kind"]
    if kind == STEP_TIMED and not step.get("duration_minutes"):
        raise vol.Invalid("a timed step needs duration_minutes")
    if kind == STEP_PREHEAT and step.get("setpoint") is None:
        raise vol.Invalid("a preheat step needs a setpoint to reach")
    if kind != STEP_TIMED and step.get("duration_minutes"):
        raise vol.Invalid(f"a {kind} step has no duration")
    return step


STEP_SCHEMA = vol.All(
    vol.Schema(
        {
            vol.Optional("name", default=""): cv.string,
            vol.Required("kind"): vol.In(STEP_KINDS),
            # The appliance's programme option, exactly as the selected-programme
            # select entity lists it.
            vol.Required("program"): cv.string,
            vol.Optional("setpoint"): vol.Any(None, vol.Coerce(float)),
            vol.Optional("duration_minutes"): vol.Any(
                None, vol.All(vol.Coerce(int), vol.Range(min=1, max=24 * 60))
            ),
            vol.Optional("timeout_minutes"): vol.Any(
                None, vol.All(vol.Coerce(int), vol.Range(min=1, max=180))
            ),
        }
    ),
    _validate_step,
)


def _validate_preset(preset: dict[str, Any]) -> dict[str, Any]:
    steps = preset["steps"]
    for step in steps[:-1]:
        if step["kind"] == STEP_HOLD:
            raise vol.Invalid("only the last step can be a hold step")
    return preset


PRESET_SCHEMA = vol.All(
    vol.Schema(
        {
            vol.Optional("id"): cv.string,
            vol.Required("name"): vol.All(cv.string, vol.Length(min=1, max=64)),
            vol.Required("device_id"): cv.string,
            vol.Required("steps"): vol.All([STEP_SCHEMA], vol.Length(min=1, max=20)),
        }
    ),
    _validate_preset,
)


@dataclass(slots=True)
class Step:
    kind: str
    program: str
    name: str = ""
    setpoint: float | None = None
    duration_minutes: int | None = None
    timeout_minutes: int | None = None

    @property
    def label(self) -> str:
        return self.name or self.program.rsplit(".", 1)[-1]

    @property
    def preheat_timeout(self) -> timedelta:
        return timedelta(minutes=self.timeout_minutes or DEFAULT_PREHEAT_TIMEOUT_MINUTES)

    def planned_duration(self, preheat_minutes: int) -> timedelta:
        """Duration used for planning; a hold step contributes nothing."""
        if self.kind == STEP_TIMED:
            return timedelta(minutes=self.duration_minutes or 0)
        if self.kind == STEP_PREHEAT:
            return timedelta(minutes=preheat_minutes)
        return timedelta(0)


@dataclass(slots=True)
class Preset:
    id: str
    name: str
    device_id: str
    steps: list[Step] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Preset:
        data = PRESET_SCHEMA(dict(data))
        return cls(
            id=data.get("id") or slugify(data["name"]),
            name=data["name"],
            device_id=data["device_id"],
            steps=[Step(**step) for step in data["steps"]],
        )

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)

    def planned_duration(self, preheat_minutes: int) -> timedelta:
        """Time from start until the food is ready (a trailing hold is 'ready')."""
        return sum((step.planned_duration(preheat_minutes) for step in self.steps), timedelta(0))

    def start_for_ready(self, ready_at: datetime, preheat_minutes: int) -> datetime:
        return ready_at - self.planned_duration(preheat_minutes)
