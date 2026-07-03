"""Config flow for the Mock Climate integration."""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult

from .const import DOMAIN

_TITLE = "Mock Climate"


class MockClimateConfigFlow(ConfigFlow, domain=DOMAIN):
    """Single-instance config flow for Mock Climate."""

    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Handle a user-initiated setup."""
        await self.async_set_unique_id(DOMAIN)
        self._abort_if_unique_id_configured()
        if user_input is None:
            return self.async_show_form(step_id="user")
        return self.async_create_entry(title=_TITLE, data={})

    async def async_step_import(self, import_data: dict[str, Any]) -> ConfigFlowResult:
        """Handle YAML import."""
        await self.async_set_unique_id(DOMAIN)
        self._abort_if_unique_id_configured()
        return self.async_create_entry(title=_TITLE, data={})
