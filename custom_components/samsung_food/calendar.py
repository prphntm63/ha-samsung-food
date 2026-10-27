"""Samsung Food meal plan as a Home Assistant calendar."""

from __future__ import annotations

from datetime import datetime, timedelta

from homeassistant.components.calendar import CalendarEntity, CalendarEvent
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from .api import Meal, SamsungFoodError
from .coordinator import SamsungFoodConfigEntry, SamsungFoodCoordinator
from .entity import SamsungFoodEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SamsungFoodConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    async_add_entities([SamsungFoodMealPlanCalendar(entry.runtime_data)])


def _to_event(meal: Meal) -> CalendarEvent:
    description = "\n".join(p for p in (meal.source_url, meal.url) if p)
    return CalendarEvent(
        start=meal.day,
        end=meal.day + timedelta(days=1),
        summary=f"{meal.meal_time}: {meal.name}",
        description=description or None,
        uid=meal.id,
    )


class SamsungFoodMealPlanCalendar(SamsungFoodEntity, CalendarEntity):
    _attr_name = "Meal plan"
    _attr_icon = "mdi:silverware-fork-knife"

    def __init__(self, coordinator: SamsungFoodCoordinator) -> None:
        super().__init__(coordinator, "meal_plan_calendar")

    @property
    def event(self) -> CalendarEvent | None:
        """Today's first meal, or the next upcoming one."""
        today = dt_util.now().date()
        upcoming = [m for m in self.coordinator.data.meals if m.day and m.day >= today]
        return _to_event(upcoming[0]) if upcoming else None

    async def async_get_events(
        self, hass: HomeAssistant, start_date: datetime, end_date: datetime
    ) -> list[CalendarEvent]:
        try:
            meals = await self.coordinator.client.get_meals(start_date.date(), end_date.date())
        except SamsungFoodError as err:
            raise HomeAssistantError(f"Samsung Food: {err}") from err
        return [_to_event(m) for m in meals if m.day]
