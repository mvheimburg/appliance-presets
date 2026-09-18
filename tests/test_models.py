"""Preset validation and planning."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
import voluptuous as vol

from custom_components.appliance_presets.models import Preset

HOT_AIR = "Cooking.Oven.Program.HeatingMode.HotAir"


def pizza() -> Preset:
    return Preset.from_dict(
        {
            "name": "Pizza",
            "device_id": "dev",
            "steps": [
                {"kind": "preheat", "program": HOT_AIR, "setpoint": 250},
                {"kind": "timed", "program": HOT_AIR, "setpoint": 250, "duration_minutes": 12},
                {"kind": "timed", "program": "Grill", "setpoint": 220, "duration_minutes": 3},
            ],
        }
    )


def test_id_defaults_to_slug() -> None:
    assert pizza().id == "pizza"


def test_ready_at_works_backwards_through_preheat_estimate() -> None:
    ready = datetime(2026, 9, 18, 17, 0, tzinfo=UTC)
    assert pizza().planned_duration(15) == timedelta(minutes=30)
    assert pizza().start_for_ready(ready, 15) == ready - timedelta(minutes=30)


def test_trailing_hold_counts_as_ready() -> None:
    roast = Preset.from_dict(
        {
            "name": "Langtidsstek",
            "device_id": "dev",
            "steps": [
                {"kind": "timed", "program": HOT_AIR, "setpoint": 220, "duration_minutes": 20},
                {"kind": "timed", "program": HOT_AIR, "setpoint": 75, "duration_minutes": 360},
                {"kind": "hold", "program": HOT_AIR, "setpoint": 50},
            ],
        }
    )
    assert roast.planned_duration(15) == timedelta(minutes=380)


@pytest.mark.parametrize(
    ("steps", "message"),
    [
        ([{"kind": "timed", "program": HOT_AIR}], "duration"),
        ([{"kind": "preheat", "program": HOT_AIR}], "setpoint"),
        ([{"kind": "hold", "program": HOT_AIR, "duration_minutes": 5}], "no duration"),
        (
            [
                {"kind": "hold", "program": HOT_AIR},
                {"kind": "timed", "program": HOT_AIR, "duration_minutes": 5},
            ],
            "last step",
        ),
        ([], "length"),
    ],
)
def test_invalid_presets(steps, message) -> None:
    with pytest.raises(vol.Invalid, match=message):
        Preset.from_dict({"name": "x", "device_id": "dev", "steps": steps})


def test_round_trip() -> None:
    assert Preset.from_dict(pizza().as_dict()) == pizza()
