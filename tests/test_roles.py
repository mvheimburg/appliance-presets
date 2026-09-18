"""Role resolution: unique-id keys first, every known entity-id spelling second."""

from __future__ import annotations

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from custom_components.appliance_presets.roles import (
    REQUIRED_ROLES,
    ROLE_ABORT,
    ROLE_OPERATION,
    ROLE_REMOTE_START,
    ROLE_SELECTED_PROGRAM,
    resolve_roles,
    role_for_entity_id,
)

from .conftest import ENTITIES


async def test_local_unique_ids_resolve_every_role(hass: HomeAssistant, device_id: str) -> None:
    roles = resolve_roles(hass, device_id)
    assert roles.missing_required == []
    assert roles.get(ROLE_OPERATION) == ENTITIES["sensor_operation_state"]
    assert roles.get(ROLE_REMOTE_START) == ENTITIES["binary_remote_start_allowed"]
    assert len(roles.entities) == len(ENTITIES)


async def test_unique_id_survives_rename(hass: HomeAssistant, device_id: str) -> None:
    registry = er.async_get(hass)
    old = ENTITIES["button_abort_program"]
    registry.async_update_entity(old, new_entity_id="button.something_else")
    assert resolve_roles(hass, device_id).get(ROLE_ABORT) == "button.something_else"


async def test_disabled_entities_do_not_exist(hass: HomeAssistant, device_id: str) -> None:
    er.async_get(hass).async_update_entity(
        ENTITIES["select_program"], disabled_by=er.RegistryEntryDisabler.USER
    )
    assert ROLE_SELECTED_PROGRAM in resolve_roles(hass, device_id).missing_required


@pytest.mark.parametrize(
    ("entity_id", "role"),
    [
        # Cloud integration, older and newer devices, side by side.
        ("select.kitchen_dampovn_selected_program", "selected_program"),
        ("select.siemens_dishwasher_selected_programme", "selected_program"),
        ("button.siemens_dishwasher_stop_programme", "abort"),
        ("button.kitchen_dampovn_abort", "abort"),
        ("sensor.kitchen_dampovn_operation_state", "operation"),
        ("binary_sensor.kitchen_dampovn_door", "door"),
        ("sensor.kitchen_dampovn_door", "door"),
        ("select.kitchen_dampovn_active_program", None),
        ("sensor.kitchen_dampovn_program_progress", None),
    ],
)
def test_entity_id_spellings(entity_id: str, role: str | None) -> None:
    assert role_for_entity_id(entity_id) == role


def test_required_roles_are_the_transport() -> None:
    assert set(REQUIRED_ROLES) == {"operation", "selected_program", "start", "abort"}
