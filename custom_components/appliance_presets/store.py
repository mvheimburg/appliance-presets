"""Persistent preset library, shared by every appliance entry."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.storage import Store

from .const import PRESETS_STORAGE_KEY, STORAGE_VERSION
from .models import Preset


class PresetStore:
    """Presets live in .storage, not YAML, so the panel can edit them."""

    def __init__(self, hass: HomeAssistant) -> None:
        self._store: Store[dict[str, Any]] = Store(hass, STORAGE_VERSION, PRESETS_STORAGE_KEY)
        self._presets: dict[str, Preset] = {}
        self._listeners: list[Callable[[], None]] = []

    async def async_load(self) -> None:
        data = await self._store.async_load() or {}
        self._presets = {}
        for raw in data.get("presets", []):
            preset = Preset.from_dict(raw)
            self._presets[preset.id] = preset

    def get(self, preset_id: str) -> Preset | None:
        return self._presets.get(preset_id)

    def find(self, key: str, device_id: str | None = None) -> Preset | None:
        """Look a preset up by id or, failing that, by (case-insensitive) name."""
        if (preset := self._presets.get(key)) and device_id in (None, preset.device_id):
            return preset
        for preset in self._presets.values():
            if preset.name.casefold() == key.casefold() and device_id in (None, preset.device_id):
                return preset
        return None

    def for_device(self, device_id: str) -> list[Preset]:
        return sorted(
            (p for p in self._presets.values() if p.device_id == device_id),
            key=lambda p: p.name.casefold(),
        )

    def all(self) -> list[Preset]:
        return sorted(self._presets.values(), key=lambda p: p.name.casefold())

    async def async_save(self, data: dict[str, Any]) -> Preset:
        preset = Preset.from_dict(data)
        self._presets[preset.id] = preset
        await self._async_write()
        return preset

    async def async_delete(self, preset_id: str) -> bool:
        if self._presets.pop(preset_id, None) is None:
            return False
        await self._async_write()
        return True

    @callback
    def async_add_listener(self, listener: Callable[[], None]) -> Callable[[], None]:
        self._listeners.append(listener)
        return lambda: self._listeners.remove(listener)

    async def _async_write(self) -> None:
        await self._store.async_save({"presets": [p.as_dict() for p in self._presets.values()]})
        for listener in list(self._listeners):
            listener()
