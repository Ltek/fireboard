"""The FireBoard integration for Home Assistant."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant

from .const import (
    CONF_ENABLE_DIAGNOSTICS,
    CONF_ENABLE_SETPOINT,
    CONF_ENABLED_ENTITIES,
    DEFAULT_ENABLE_DIAGNOSTICS,
    DEFAULT_ENABLE_SETPOINT,
    DOMAIN,
    VERSION,
)
from .coordinator import FireBoardDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [
    Platform.SENSOR,
    Platform.BINARY_SENSOR,
    Platform.NUMBER,
    Platform.SWITCH,
    Platform.TEXT,
]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up FireBoard from a config entry."""
    _LOGGER.info("Setting up FireBoard integration (version %s)", VERSION)
    hass.data.setdefault(DOMAIN, {})

    # Create coordinator
    coordinator = FireBoardDataUpdateCoordinator(hass, entry)

    # Fetch initial data
    await coordinator.async_config_entry_first_refresh()

    # Store coordinator
    hass.data[DOMAIN][entry.entry_id] = coordinator

    # Set up platforms
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Start the built-in reachability (ping) loop for any configured IPs.
    coordinator.async_start_background_tasks()

    # Reload the entry when options (refresh intervals) change.
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))

    return True


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Apply updated options.

    Most settings (intervals, drive/offline polling) apply live without a
    reload. The "enable diagnostics" / "enable setpoint control" toggles change
    which entities are created, which only takes effect at platform setup, so
    those require a reload. We reload when either toggle changed.
    """
    coordinator: FireBoardDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]

    options = {**entry.data, **entry.options}
    creation_toggles_changed = (
        options.get(CONF_ENABLE_DIAGNOSTICS, DEFAULT_ENABLE_DIAGNOSTICS)
        != coordinator.enable_diagnostics
        or options.get(CONF_ENABLE_SETPOINT, DEFAULT_ENABLE_SETPOINT)
        != coordinator.enable_setpoint
        or options.get(CONF_ENABLED_ENTITIES, {})
        != coordinator.enabled_entities
    )

    if creation_toggles_changed:
        await hass.config_entries.async_reload(entry.entry_id)
        return

    await coordinator.async_apply_options()


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    # Unload platforms
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

    # Remove data and stop background tasks
    if unload_ok and entry.entry_id in hass.data[DOMAIN]:
        coordinator: FireBoardDataUpdateCoordinator = hass.data[DOMAIN].pop(
            entry.entry_id
        )
        await coordinator.async_shutdown()

    return unload_ok
