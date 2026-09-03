# Version: 2026.09.03.28
"""Text platform for FireBoard: per-device LAN IP for reachability checks."""

from __future__ import annotations

import logging

from homeassistant.components.text import TextEntity, TextMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DEV_CONF_IP, DOMAIN, IPV4_REGEX, UNIQUE_ID_VERSION, is_valid_ipv4
from .coordinator import FireBoardDataUpdateCoordinator
from .entity import FireBoardConfigEntity

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up a LAN-IP text entity per device."""
    coordinator: FireBoardDataUpdateCoordinator = hass.data[DOMAIN][
        config_entry.entry_id
    ]

    entities = [
        FireBoardDeviceIpText(coordinator, uuid)
        for uuid in coordinator.data
    ]
    async_add_entities(entities)


class FireBoardDeviceIpText(FireBoardConfigEntity, TextEntity):
    """Editable LAN IP address used for offline reachability checks."""

    _attr_entity_category = EntityCategory.CONFIG
    _attr_mode = TextMode.TEXT
    _attr_icon = "mdi:ip-network"
    # Allow blank (to clear) or a strict IPv4 dotted-decimal address. The
    # pattern gives immediate client-side feedback; async_set_value re-checks.
    _attr_native_min = 0
    _attr_native_max = 15
    _attr_pattern = rf"^$|{IPV4_REGEX.pattern}"

    def __init__(
        self,
        coordinator: FireBoardDataUpdateCoordinator,
        device_uuid: str,
    ) -> None:
        """Initialize the IP text entity."""
        super().__init__(coordinator, device_uuid)
        self._attr_unique_id = f"{device_uuid}_lan_ip_{UNIQUE_ID_VERSION}"
        self._attr_name = f"{self._device_title} LAN IP"

    @property
    def native_value(self) -> str | None:
        """Return the user-entered LAN IP (empty if unset).

        Shows the manual override only -- never the cloud-reported IP -- so the
        field always reflects what the user typed. The effective IP used for
        pinging (cloud-preferred) is exposed via the On Network sensor.
        """
        return self.coordinator.device_manual_ip(self._device_uuid) or ""

    async def async_set_value(self, value: str) -> None:
        """Persist a new LAN IP; reject anything but strict IPv4 (or blank)."""
        value = value.strip()
        if value and not is_valid_ipv4(value):
            raise ValueError(
                f"'{value}' is not a valid IPv4 address (expected N.N.N.N)"
            )
        await self.coordinator.async_set_device_option(
            self._device_uuid, DEV_CONF_IP, value
        )
