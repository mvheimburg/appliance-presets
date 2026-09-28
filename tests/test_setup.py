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


async def test_config_flow_accepts_a_fridge_for_notifications_only(
    hass: HomeAssistant, hass_ws_client
) -> None:
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
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    # No preset sensor and no panel entry: it only notifies.
    assert hass.states.async_entity_ids("sensor") == []
    client = await hass_ws_client(hass)
    await client.send_json_auto_id({"type": f"{DOMAIN}/appliances"})
    assert (await client.receive_json())["result"] == []

    # Its Configure form only has the notification settings.
    entry = result["result"]
    form = await hass.config_entries.options.async_init(entry.entry_id)
    assert form["step_id"] == "notify"
    assert {str(k) for k in form["data_schema"].schema} == {"notify_persons", "notify_on"}
    done = await hass.config_entries.options.async_configure(
        form["flow_id"], {"notify_persons": ["person.anna"], "notify_on": ["problem"]}
    )
    assert done["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options == {"notify_persons": ["person.anna"], "notify_on": ["problem"]}


async def test_options_keep_hidden_settings_and_drop_cleared_people(
    hass: HomeAssistant, freezer, oven, entry: MockConfigEntry
) -> None:
    hass.config_entries.async_update_entry(
        entry, options={"notify_persons": ["person.anna"], "preheat_minutes": 20}
    )
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    form = await hass.config_entries.options.async_init(entry.entry_id)
    assert form["step_id"] == "init"
    # The oven's form shows everything; the people field comes back cleared.
    user_input = {
        "preheat_minutes": 20,
        "max_total_minutes": 720,
        "abort_on_door_open": True,
        "door_min_setpoint": 150,
        "door_grace_seconds": 120,
        "require_home": False,
        "notify_on": ["finished"],
    }
    await hass.config_entries.options.async_configure(form["flow_id"], user_input)
    assert "notify_persons" not in entry.options
    assert entry.options["notify_on"] == ["finished"]
    await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()


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


async def test_status_sensor_id_uses_an_english_key_in_any_language(
    hass: HomeAssistant, freezer, oven, entry: MockConfigEntry
) -> None:
    hass.config.language = "nb"
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get("sensor.dampovn_preset") is not None
    await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
