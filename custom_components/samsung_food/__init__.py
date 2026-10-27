"""The Samsung Food integration."""

from __future__ import annotations

import logging
from pathlib import Path

from homeassistant.components.http import StaticPathConfig
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.typing import ConfigType

from .api import Account, SamsungFoodClient
from .const import CARD_URL, CONF_ACCESS_TOKEN, CONF_EXPIRES_AT, DOMAIN
from .coordinator import SamsungFoodConfigEntry, SamsungFoodCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [Platform.CALENDAR, Platform.SENSOR, Platform.TODO]
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)
CARD_PATH = Path(__file__).parent / "frontend" / "samsung-food-meal-plan.js"


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Serve the meal plan card and load it on every dashboard."""
    await hass.http.async_register_static_paths(
        [StaticPathConfig(CARD_URL, str(CARD_PATH), cache_headers=True)]
    )
    # Version the URL by file mtime so browsers pick up a new card after an update.
    mtime = await hass.async_add_executor_job(lambda: int(CARD_PATH.stat().st_mtime))
    if "frontend" in hass.config.components:
        from homeassistant.components.frontend import add_extra_js_url

        add_extra_js_url(hass, f"{CARD_URL}?v={mtime}")
    else:
        _LOGGER.warning(
            "Frontend not loaded; add %s as a dashboard resource to use the meal plan card", CARD_URL
        )
    return True


async def async_setup_entry(hass: HomeAssistant, entry: SamsungFoodConfigEntry) -> bool:
    def save_token(account: Account) -> None:
        # Persist the token so restarts don't need a fresh login.
        hass.config_entries.async_update_entry(
            entry,
            data={
                **entry.data,
                CONF_ACCESS_TOKEN: account.access_token,
                CONF_EXPIRES_AT: account.expires_at,
            },
        )

    client = SamsungFoodClient(
        async_get_clientsession(hass),
        entry.data[CONF_EMAIL],
        entry.data[CONF_PASSWORD],
        access_token=entry.data.get(CONF_ACCESS_TOKEN),
        expires_at=entry.data.get(CONF_EXPIRES_AT, 0),
        on_token=save_token,
    )
    coordinator = SamsungFoodCoordinator(hass, entry, client)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: SamsungFoodConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
