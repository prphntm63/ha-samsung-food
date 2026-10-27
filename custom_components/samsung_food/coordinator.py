"""Polling coordinator for Samsung Food."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import ListContents, Meal, SamsungFoodAuthError, SamsungFoodClient, SamsungFoodError
from .const import DOMAIN, SCAN_INTERVAL, WEEK_START_WEEKDAY

_LOGGER = logging.getLogger(__name__)

type SamsungFoodConfigEntry = ConfigEntry[SamsungFoodCoordinator]


@dataclass
class SamsungFoodData:
    lists: dict[str, ListContents] = field(default_factory=dict)
    week_start: date = field(default_factory=date.today)
    # Meals for this week and next week.
    meals: list[Meal] = field(default_factory=list)

    def meals_between(self, start: date, end: date) -> list[Meal]:
        return [m for m in self.meals if m.day and start <= m.day <= end]


def week_start_for(day: date) -> date:
    return day - timedelta(days=(day.weekday() - WEEK_START_WEEKDAY) % 7)


class SamsungFoodCoordinator(DataUpdateCoordinator[SamsungFoodData]):
    config_entry: SamsungFoodConfigEntry

    def __init__(
        self, hass: HomeAssistant, entry: SamsungFoodConfigEntry, client: SamsungFoodClient
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=SCAN_INTERVAL,
        )
        self.client = client

    async def _async_update_data(self) -> SamsungFoodData:
        week_start = week_start_for(dt_util.now().date())
        try:
            lists = await self.client.get_lists()
            contents = {lst.id: await self.client.get_list(lst.id) for lst in lists}
            for lst in lists:
                # GetList doesn't always echo the name/primary flag; GetLists does.
                contents[lst.id].list = lst
            meals = await self.client.get_meals(week_start, week_start + timedelta(days=13))
        except SamsungFoodAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except SamsungFoodError as err:
            raise UpdateFailed(str(err)) from err
        return SamsungFoodData(lists=contents, week_start=week_start, meals=meals)
