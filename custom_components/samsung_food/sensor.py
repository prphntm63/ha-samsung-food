"""Meal plan sensor exposing this week's meals (with recipe links) as attributes."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from homeassistant.components.sensor import SensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from .api import Meal
from .coordinator import SamsungFoodConfigEntry, SamsungFoodCoordinator
from .entity import SamsungFoodEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SamsungFoodConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    async_add_entities([SamsungFoodWeekSensor(entry.runtime_data)])


def _meal_dict(meal: Meal) -> dict[str, Any]:
    return {
        "date": meal.day.isoformat(),
        "weekday": meal.day.strftime("%A"),
        "meal_time": meal.meal_time,
        "name": meal.name,
        "url": meal.url,
        "link": meal.link,
        "image": meal.image_url,
        "source": meal.source_name,
        "source_url": meal.source_url,
    }


class SamsungFoodWeekSensor(SamsungFoodEntity, SensorEntity):
    """State: today's meals. Attributes: this week's and next week's meals."""

    _attr_name = "Meal plan"
    _attr_icon = "mdi:food"

    def __init__(self, coordinator: SamsungFoodCoordinator) -> None:
        super().__init__(coordinator, "meal_plan_week")

    @property
    def native_value(self) -> str:
        today = dt_util.now().date()
        meals = self.coordinator.data.meals_between(today, today)
        text = ", ".join(f"{m.meal_time}: {m.name}" for m in meals) or "Nothing planned"
        return text[:255]

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        data = self.coordinator.data
        start = data.week_start
        return {
            "week_start": start.isoformat(),
            "meals": [_meal_dict(m) for m in data.meals_between(start, start + timedelta(days=6))],
            "next_week_meals": [
                _meal_dict(m)
                for m in data.meals_between(start + timedelta(days=7), start + timedelta(days=13))
            ],
        }
