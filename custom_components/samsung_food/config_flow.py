"""Config flow for Samsung Food."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import Account, SamsungFoodAuthError, SamsungFoodClient, SamsungFoodError
from .const import CONF_ACCESS_TOKEN, CONF_EXPIRES_AT, DOMAIN

_LOGGER = logging.getLogger(__name__)

USER_SCHEMA = vol.Schema({vol.Required(CONF_EMAIL): str, vol.Required(CONF_PASSWORD): str})
REAUTH_SCHEMA = vol.Schema({vol.Required(CONF_PASSWORD): str})


class SamsungFoodConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def _try_login(self, email: str, password: str) -> tuple[Account | None, str | None]:
        client = SamsungFoodClient(async_get_clientsession(self.hass), email, password)
        try:
            account = await client.login()
            if not account.user_id:
                account.user_id = await client.get_user_id()
        except SamsungFoodAuthError as err:
            _LOGGER.debug("Samsung Food login rejected: %s", err)
            return None, "invalid_auth"
        except SamsungFoodError:
            _LOGGER.exception("Error connecting to Samsung Food")
            return None, "cannot_connect"
        return account, None

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            email = user_input[CONF_EMAIL].strip()
            account, error = await self._try_login(email, user_input[CONF_PASSWORD])
            if account:
                await self.async_set_unique_id(account.user_id or email.lower())
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=email,
                    data={
                        CONF_EMAIL: email,
                        CONF_PASSWORD: user_input[CONF_PASSWORD],
                        CONF_ACCESS_TOKEN: account.access_token,
                        CONF_EXPIRES_AT: account.expires_at,
                    },
                )
            errors["base"] = error
        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(USER_SCHEMA, user_input),
            errors=errors,
        )

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        entry = self._get_reauth_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            account, error = await self._try_login(entry.data[CONF_EMAIL], user_input[CONF_PASSWORD])
            if account:
                return self.async_update_reload_and_abort(
                    entry,
                    data_updates={
                        CONF_PASSWORD: user_input[CONF_PASSWORD],
                        CONF_ACCESS_TOKEN: account.access_token,
                        CONF_EXPIRES_AT: account.expires_at,
                    },
                )
            errors["base"] = error
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=REAUTH_SCHEMA,
            description_placeholders={"email": entry.data[CONF_EMAIL]},
            errors=errors,
        )
