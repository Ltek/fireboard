# Version: 2026.09.03.28
"""Config flow for FireBoard integration."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api_client import (
    FireBoardApiClient,
    FireBoardApiClientAuthenticationError,
    FireBoardApiClientCommunicationError,
    FireBoardApiClientRateLimitError,
)
from .const import (
    CONF_DEVICE_CONFIG,
    CONF_DEVICES_INTERVAL,
    CONF_DRIVE_INTERVAL,
    CONF_ENABLE_DIAGNOSTICS,
    CONF_ENABLE_DRIVE,
    CONF_ENABLE_SETPOINT,
    CONF_OFFLINE_INTERVAL,
    DEFAULT_DEVICES_INTERVAL,
    DEFAULT_DRIVE_INTERVAL,
    DEFAULT_ENABLE_DIAGNOSTICS,
    DEFAULT_ENABLE_DRIVE,
    DEFAULT_ENABLE_SETPOINT,
    DEFAULT_OFFLINE_INTERVAL,
    DEFAULT_OFFLINE_POLL_ENABLED,
    DEV_CONF_IP,
    DEV_CONF_OFFLINE_POLL,
    DOMAIN,
    MAX_POLLING_INTERVAL,
    MIN_DRIVE_INTERVAL,
    MIN_OFFLINE_INTERVAL,
    MIN_POLLING_INTERVAL,
    is_valid_ipv4,
)

_LOGGER = logging.getLogger(__name__)

STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_EMAIL): str,
        vol.Required(CONF_PASSWORD): str,
    }
)


def _options_schema(options: dict[str, Any]) -> vol.Schema:
    """Build the options schema pre-filled with current values."""
    return vol.Schema(
        {
            vol.Optional(
                CONF_DEVICES_INTERVAL,
                default=options.get(
                    CONF_DEVICES_INTERVAL, DEFAULT_DEVICES_INTERVAL
                ),
            ): vol.All(
                vol.Coerce(int),
                vol.Range(min=MIN_POLLING_INTERVAL, max=MAX_POLLING_INTERVAL),
            ),
            vol.Optional(
                CONF_ENABLE_DRIVE,
                default=options.get(CONF_ENABLE_DRIVE, DEFAULT_ENABLE_DRIVE),
            ): bool,
            vol.Optional(
                CONF_DRIVE_INTERVAL,
                default=options.get(CONF_DRIVE_INTERVAL, DEFAULT_DRIVE_INTERVAL),
            ): vol.All(
                vol.Coerce(int),
                vol.Range(min=MIN_DRIVE_INTERVAL, max=MAX_POLLING_INTERVAL),
            ),
            vol.Optional(
                CONF_OFFLINE_INTERVAL,
                default=options.get(
                    CONF_OFFLINE_INTERVAL, DEFAULT_OFFLINE_INTERVAL
                ),
            ): vol.All(
                vol.Coerce(int),
                vol.Range(min=MIN_OFFLINE_INTERVAL, max=MAX_POLLING_INTERVAL),
            ),
            vol.Optional(
                CONF_ENABLE_DIAGNOSTICS,
                default=options.get(
                    CONF_ENABLE_DIAGNOSTICS, DEFAULT_ENABLE_DIAGNOSTICS
                ),
            ): bool,
            vol.Optional(
                CONF_ENABLE_SETPOINT,
                default=options.get(
                    CONF_ENABLE_SETPOINT, DEFAULT_ENABLE_SETPOINT
                ),
            ): bool,
        }
    )


class ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for FireBoard."""

    VERSION = 1

    def __init__(self):
        """Initialize the config flow."""
        super().__init__()
        self._devices: list[dict[str, Any]] = []
        self._credentials: dict[str, Any] = {}

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: ConfigEntry,
    ) -> "OptionsFlowHandler":
        """Get the options flow for this handler."""
        return OptionsFlowHandler()

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle the credentials step."""
        if user_input is None:
            return self.async_show_form(
                step_id="user", data_schema=STEP_USER_DATA_SCHEMA
            )

        errors = {}

        try:
            devices = await self._test_connection(user_input)
            self._devices = devices
        except CannotConnect:
            errors["base"] = "cannot_connect"
        except InvalidAuth:
            errors["base"] = "invalid_auth"
        except RateLimitExceeded:
            errors["base"] = "rate_limit"
        except Exception:  # pylint: disable=broad-except
            _LOGGER.exception("Unexpected exception")
            errors["base"] = "unknown"

        if not errors:
            # Use email as the unique ID to prevent duplicate accounts
            await self.async_set_unique_id(user_input[CONF_EMAIL].lower())
            self._abort_if_unique_id_configured()

            self._credentials = user_input
            # Proceed to the optional per-device LAN IP step.
            return await self.async_step_device_ips()

        return self.async_show_form(
            step_id="user", data_schema=STEP_USER_DATA_SCHEMA, errors=errors
        )

    async def async_step_reauth(
        self, entry_data: dict[str, Any]
    ) -> FlowResult:
        """Start reauth when credentials stop working."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Prompt for the password (and email) again and update the entry."""
        entry = self.hass.config_entries.async_get_entry(
            self.context["entry_id"]
        )
        errors: dict[str, str] = {}

        if user_input is not None and entry is not None:
            candidate = {
                CONF_EMAIL: user_input.get(CONF_EMAIL, entry.data[CONF_EMAIL]),
                CONF_PASSWORD: user_input[CONF_PASSWORD],
            }
            try:
                await self._test_connection(candidate)
            except InvalidAuth:
                errors["base"] = "invalid_auth"
            except RateLimitExceeded:
                errors["base"] = "rate_limit"
            except CannotConnect:
                errors["base"] = "cannot_connect"
            except Exception:  # pylint: disable=broad-except
                _LOGGER.exception("Unexpected exception during reauth")
                errors["base"] = "unknown"

            if not errors:
                self.hass.config_entries.async_update_entry(
                    entry, data={**entry.data, **candidate}
                )
                await self.hass.config_entries.async_reload(entry.entry_id)
                return self.async_abort(reason="reauth_successful")

        default_email = entry.data[CONF_EMAIL] if entry else ""
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_EMAIL, default=default_email): str,
                    vol.Required(CONF_PASSWORD): str,
                }
            ),
            errors=errors,
        )

    def _ip_field_labels(self) -> dict[str, str]:
        """Return {form_field_label: uuid} for discovered devices.

        The field label is the device title (so the form reads nicely), made
        unique if two devices share a title.
        """
        labels: dict[str, str] = {}
        seen: dict[str, int] = {}
        for device in self._devices:
            uuid = device.get("uuid")
            if not uuid:
                continue
            title = device.get("title") or uuid
            label = f"{title} IP address"
            if label in labels:
                seen[label] = seen.get(label, 1) + 1
                label = f"{title} IP address ({seen[label]})"
            labels[label] = uuid
        return labels

    async def async_step_device_ips(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Optionally collect a LAN IP per device (all fields optional).

        The FireBoard cloud API does not report device IPs, so this lets the
        user supply them for the built-in reachability/offline-polling feature.
        Leaving them blank is fine -- they can be set later on the "FireBoard
        Server Connection" device entities.
        """
        field_map = self._ip_field_labels()

        # No devices with UUIDs -> nothing to configure, finish now.
        if not field_map:
            return self._create_entry({})

        errors: dict[str, str] = {}

        if user_input is not None:
            device_config: dict[str, dict[str, Any]] = {}
            for label, uuid in field_map.items():
                raw = (user_input.get(label) or "").strip()
                if not raw:
                    continue
                if not is_valid_ipv4(raw):
                    errors[label] = "invalid_ip"
                    continue
                device_config[uuid] = {
                    DEV_CONF_IP: raw,
                    # Providing an IP does not force offline polling on; that
                    # stays opt-in via the per-device switch.
                    DEV_CONF_OFFLINE_POLL: DEFAULT_OFFLINE_POLL_ENABLED,
                }

            if not errors:
                options = (
                    {CONF_DEVICE_CONFIG: device_config} if device_config else {}
                )
                return self._create_entry(options)

        # Build the form: one optional IP text field per device, labelled by
        # the device title, pre-filled with any value the user just typed.
        schema_dict: dict[Any, Any] = {}
        for label in field_map:
            existing = user_input.get(label, "") if user_input else ""
            schema_dict[
                vol.Optional(
                    label, description={"suggested_value": existing}
                )
            ] = str

        return self.async_show_form(
            step_id="device_ips",
            data_schema=vol.Schema(schema_dict),
            errors=errors,
            description_placeholders={
                "device_names": ", ".join(
                    self.coordinator_device_titles()
                )
            },
        )

    def coordinator_device_titles(self) -> list[str]:
        """Titles of discovered devices, for the IP step description."""
        return [
            d.get("title") or d.get("uuid", "")
            for d in self._devices
            if d.get("uuid")
        ]

    def _create_entry(self, options: dict[str, Any]) -> FlowResult:
        """Create the config entry from stored credentials and options."""
        title = f"FireBoard ({self._credentials[CONF_EMAIL]})"
        return self.async_create_entry(
            title=title,
            data=self._credentials,
            options=options,
        )

    async def _test_connection(
        self, user_input: dict[str, Any]
    ) -> list[dict[str, Any]]:
        """Test connection to the FireBoard API.

        Args:
            user_input: User input from config flow

        Returns:
            List of devices from the API

        Raises:
            CannotConnect: If connection fails
            InvalidAuth: If authentication fails
            RateLimitExceeded: If rate limited

        """
        session = async_get_clientsession(self.hass)

        client = FireBoardApiClient(
            email=user_input[CONF_EMAIL],
            password=user_input[CONF_PASSWORD],
            session=session,
        )

        try:
            # Try to authenticate
            await client.authenticate()

            # Try to fetch devices to verify API access
            devices = await client.get_devices()

            _LOGGER.debug("Successfully connected to FireBoard API")
            _LOGGER.debug("Found %d devices", len(devices))

            return devices

        except FireBoardApiClientAuthenticationError as err:
            _LOGGER.error("Authentication failed: %s", err)
            raise InvalidAuth from err
        except FireBoardApiClientRateLimitError as err:
            _LOGGER.error("Rate limit exceeded: %s", err)
            raise RateLimitExceeded from err
        except FireBoardApiClientCommunicationError as err:
            _LOGGER.error("Communication error: %s", err)
            raise CannotConnect from err
        except Exception as err:
            _LOGGER.error("Unexpected error during connection test: %s", err)
            raise CannotConnect from err


class OptionsFlowHandler(config_entries.OptionsFlow):
    """Handle FireBoard options: per-endpoint refresh intervals.

    ``config_entry`` is provided by the base OptionsFlow class in current
    Home Assistant versions; assigning it manually raises, so we do not.
    """

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Manage the refresh interval options."""
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        # Pre-fill from current options, then fall back to initial data.
        current = {**self.config_entry.data, **self.config_entry.options}

        return self.async_show_form(
            step_id="init",
            data_schema=_options_schema(current),
        )


class CannotConnect(HomeAssistantError):
    """Error to indicate we cannot connect."""


class InvalidAuth(HomeAssistantError):
    """Error to indicate there is invalid auth."""


class RateLimitExceeded(HomeAssistantError):
    """Error to indicate rate limit was exceeded."""
