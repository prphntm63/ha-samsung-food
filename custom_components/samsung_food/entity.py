"""Shared entity base for Samsung Food."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import SamsungFoodCoordinator


class SamsungFoodEntity(CoordinatorEntity[SamsungFoodCoordinator]):
    _attr_has_entity_name = True

    def __init__(self, coordinator: SamsungFoodCoordinator, key: str) -> None:
        super().__init__(coordinator)
        entry = coordinator.config_entry
        self._attr_unique_id = f"{entry.unique_id}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.unique_id or entry.entry_id)},
            name="Samsung Food",
            manufacturer="Samsung",
            entry_type=DeviceEntryType.SERVICE,
            configuration_url="https://app.samsungfood.com/meal-plan",
        )
