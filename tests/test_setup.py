"""Config flow and the WebSocket API the panel uses."""

from __future__ import annotations

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import device_registry as dr
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.appliance_presets.const import DOMAIN

from .conftest import GRILL, HOT_AIR


async def test_config_flow_creates_entry_per_appliance(
    hass: HomeAssistant, device_id, oven
) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"device_id": device_id}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Dampovn"

    again = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    again = await hass.config_entries.flow.async_configure(
        again["flow_id"], {"device_id": device_id}
    )
    assert again["type"] is FlowResultType.ABORT


async def test_config_flow_rejects_non_programmable_device(hass: HomeAssistant) -> None:
    hc = MockConfigEntry(domain="homeconnect_ws")
    hc.add_to_hass(hass)
    fridge = dr.async_get(hass).async_get_or_create(
        config_entry_id=hc.entry_id, identifiers={("homeconnect_ws", "fridge")}, name="Kjøleskap"
    )
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"device_id": fridge.id}
    )
    assert result["errors"] == {"base": "not_programmable"}


async def test_websocket_editor_round_trip(
    hass: HomeAssistant, hass_ws_client, entry, oven, device_id
) -> None:
    # Not the `loaded` fixture: its frozen clock would expire the auth token.
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    client = await hass_ws_client(hass)

    await client.send_json_auto_id({"type": f"{DOMAIN}/appliances"})
    [appliance] = (await client.receive_json())["result"]
    assert appliance["programs"] == [HOT_AIR, GRILL]
    assert appliance["setpoint"] == [30.0, 275.0]
    assert appliance["can_preheat"] and appliance["missing"] == []
    assert appliance["entity_id"] == "sensor.dampovn_preset"

    preset = {
        "name": "Pizza",
        "device_id": device_id,
        "steps": [{"kind": "preheat", "program": HOT_AIR, "setpoint": 250}],
    }
    await client.send_json_auto_id({"type": f"{DOMAIN}/presets/save", "preset": preset})
    saved = (await client.receive_json())["result"]
    assert saved["id"] == "pizza"
    assert hass.states.get("sensor.dampovn_preset").attributes["presets"] == ["Pizza"]

    bad = {**preset, "steps": [{"kind": "timed", "program": HOT_AIR}]}
    await client.send_json_auto_id({"type": f"{DOMAIN}/presets/save", "preset": bad})
    assert (await client.receive_json())["error"]["code"] == "invalid_format"

    await client.send_json_auto_id({"type": f"{DOMAIN}/presets", "device_id": device_id})
    assert [p["name"] for p in (await client.receive_json())["result"]] == ["Pizza"]

    await client.send_json_auto_id({"type": f"{DOMAIN}/presets/delete", "preset_id": "pizza"})
    assert (await client.receive_json())["success"]
    await client.send_json_auto_id({"type": f"{DOMAIN}/presets"})
    assert (await client.receive_json())["result"] == []
