"""Resolve an appliance device to the entities that fill each role.

This is deliberately the subset of lovelace-appliance-panel's src/roles.ts that
the preset engine drives, and it must agree with it. The card's table is the
source of truth; when it learns a new spelling, mirror it here (and in
tests/test_roles.py). A shared table is the stated long-term goal.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

ROLE_OPERATION = "operation"
ROLE_SELECTED_PROGRAM = "selected_program"
ROLE_START = "start"
ROLE_ABORT = "abort"
ROLE_REMOTE_START = "remote_start"
ROLE_TARGET_TEMPERATURE = "target_temperature"
ROLE_CURRENT_TEMPERATURE = "current_temperature"
ROLE_DURATION = "duration"
ROLE_DOOR = "door"
ROLE_POWER = "power"

# Roles without which a preset cannot run at all.
REQUIRED_ROLES = (ROLE_OPERATION, ROLE_SELECTED_PROGRAM, ROLE_START, ROLE_ABORT)

# Home Connect Local unique-id keys (the part after "<serial>-").
_EXACT: dict[str, str] = {
    "sensor_operation_state": ROLE_OPERATION,
    "select_program": ROLE_SELECTED_PROGRAM,
    "button_start_program": ROLE_START,
    "button_abort_program": ROLE_ABORT,
    "binary_remote_start_allowed": ROLE_REMOTE_START,
    "number_oven_setpoint_temperature": ROLE_TARGET_TEMPERATURE,
    "number_duration": ROLE_DURATION,
    "switch_power_state": ROLE_POWER,
}
_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"^sensor_oven_current_temperature(?:_\d+)?$"), ROLE_CURRENT_TEMPERATURE),
    (re.compile(r"^(?:binary_sensor|sensor)_door_state$"), ROLE_DOOR),
)
_KEY = re.compile(
    r"(?:^|-)((?:binary_sensor_|binary_remote_|sensor_|select_|number_|switch_|button_).+)$"
)

# Entity-id suffix fallback: every spelling seen across Local and cloud
# versions, per (domain, role). Order is preference.
_SUFFIXES: dict[str, tuple[tuple[str, tuple[str, ...]], ...]] = {
    ROLE_OPERATION: (("sensor", ("operation_state",)),),
    ROLE_SELECTED_PROGRAM: (("select", ("selected_program", "selected_programme")),),
    ROLE_START: (("button", ("start_program", "start_programme", "start")),),
    ROLE_ABORT: (("button", ("abort_program", "stop_programme", "abort", "stop_program")),),
    ROLE_REMOTE_START: (("binary_sensor", ("remote_start_allowed", "remote_start")),),
    ROLE_TARGET_TEMPERATURE: (("number", ("setpoint_temperature",)),),
    ROLE_CURRENT_TEMPERATURE: (
        ("sensor", ("current_temperature", "current_oven_cavity_temperature")),
    ),
    ROLE_DURATION: (("number", ("duration",)),),
    ROLE_DOOR: (("binary_sensor", ("door",)), ("sensor", ("door",))),
    ROLE_POWER: (("switch", ("power",)),),
}


def role_for_key(key: str) -> str | None:
    if role := _EXACT.get(key):
        return role
    for pattern, role in _PATTERNS:
        if pattern.match(key):
            return role
    return None


def semantic_key(unique_id: str) -> str:
    match = _KEY.search(unique_id.lower())
    return match.group(1) if match else ""


def role_for_entity_id(entity_id: str) -> str | None:
    domain, _, object_id = entity_id.partition(".")
    for role, candidates in _SUFFIXES.items():
        for cand_domain, suffixes in candidates:
            if domain == cand_domain and any(
                object_id == s or object_id.endswith(f"_{s}") for s in suffixes
            ):
                return role
    return None


@dataclass(slots=True)
class ApplianceRoles:
    device_id: str
    entities: dict[str, str] = field(default_factory=dict)

    def get(self, role: str) -> str | None:
        return self.entities.get(role)

    @property
    def missing_required(self) -> list[str]:
        return [role for role in REQUIRED_ROLES if role not in self.entities]


def resolve_roles(hass: HomeAssistant, device_id: str) -> ApplianceRoles:
    """Map roles to entity ids for one device. Disabled entities do not exist."""
    registry = er.async_get(hass)
    by_key: dict[str, str] = {}
    by_suffix: dict[str, str] = {}
    for entry in er.async_entries_for_device(registry, device_id):
        if entry.disabled_by is not None:
            continue
        if role := role_for_key(semantic_key(entry.unique_id)):
            by_key.setdefault(role, entry.entity_id)
        elif role := role_for_entity_id(entry.entity_id):
            by_suffix.setdefault(role, entry.entity_id)
    # A unique-id match survives renames, so it wins over a suffix guess.
    return ApplianceRoles(device_id, {**by_suffix, **by_key})
