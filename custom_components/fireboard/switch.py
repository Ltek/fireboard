"""Switch platform for FireBoard: enable/disable Drive polling on the fly."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CONF_ENABLE_DRIVE, DEV_CONF_OFFLINE_POLL, DOMAIN, UNIQUE_ID_VERSION
from .coordinator import FireBoardDataUpdateCoordinator
from .entity import FireBoardConfigEntity, FireBoardEntity, build_device_info

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the FireBoard switches: global Drive polling and per-device
    offline polling."""
    coordinator: FireBoardDataUpdateCoordinator = hass.data[DOMAIN][
        config_entry.entry_id
    ]

    entities: list[SwitchEntity] = [FireBoardDriveSwitch(coordinator)]
    for uuid in coordinator.data:
        entities.append(FireBoardOfflinePollSwitch(coordinator, uuid))
        entities.append(FireBoardDriveOffSwitch(coordinator, uuid))

    async_add_entities(entities)


class FireBoardDriveSwitch(
    CoordinatorEntity[FireBoardDataUpdateCoordinator], SwitchEntity
):
    """Enable or disable FireBoard Drive (drivelog.json) polling."""

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.CONFIG
    _attr_icon = "mdi:fan"

    def __init__(self, coordinator: FireBoardDataUpdateCoordinator) -> None:
        """Initialize the drive polling switch."""
        super().__init__(coordinator)
        entry = coordinator.config_entry
        self._attr_unique_id = f"{entry.entry_id}_enable_drive_{UNIQUE_ID_VERSION}"
        self._attr_name = "Drive Polling"
        uuid = coordinator.primary_device_uuid
        if uuid:
            self._attr_device_info = build_device_info(coordinator, uuid)

    @property
    def is_on(self) -> bool:
        """Return True if Drive polling is enabled."""
        return self.coordinator.enable_drive

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Enable Drive polling (applied live)."""
        await self.coordinator.async_set_option(CONF_ENABLE_DRIVE, True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Disable Drive polling (applied live)."""
        await self.coordinator.async_set_option(CONF_ENABLE_DRIVE, False)


class FireBoardOfflinePollSwitch(FireBoardConfigEntity, SwitchEntity):
    """Per-device toggle for IP-triggered offline polling.

    When on and a LAN IP is set, the integration pings that IP; if the device
    is on the network but its cloud data is stale it polls fast to catch the
    reconnection, and idles the poll when the device is off the network.
    """

    _attr_entity_category = EntityCategory.CONFIG
    _attr_icon = "mdi:lan-connect"

    def __init__(
        self,
        coordinator: FireBoardDataUpdateCoordinator,
        device_uuid: str,
    ) -> None:
        """Initialize the offline-poll switch."""
        super().__init__(coordinator, device_uuid)
        self._attr_unique_id = f"{device_uuid}_offline_poll_{UNIQUE_ID_VERSION}"
        self._attr_name = "Offline Polling"

    @property
    def is_on(self) -> bool:
        """Return True if offline polling is enabled for this device."""
        return self.coordinator.device_offline_poll(self._device_uuid)

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Enable offline polling for this device."""
        await self.coordinator.async_set_device_option(
            self._device_uuid, DEV_CONF_OFFLINE_POLL, True
        )

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Disable offline polling for this device."""
        await self.coordinator.async_set_device_option(
            self._device_uuid, DEV_CONF_OFFLINE_POLL, False
        )


class FireBoardDriveOffSwitch(FireBoardEntity, SwitchEntity):
    """EXPERIMENTAL: turn the FireBoard Drive fan on/off.

    On = fan running (last known drive output > 0); turning the switch OFF
    sends a Drive-off command (setpoint 0). Turning ON is not a real command
    (the Drive resumes when a setpoint/speed is set), so ON only reflects state.
    Disabled by default; opt-in only. Controls physical hardware.
    """

    _attr_entity_category = EntityCategory.CONFIG
    _attr_icon = "mdi:fan-off"
    _attr_assumed_state = True

    def __init__(
        self,
        coordinator: FireBoardDataUpdateCoordinator,
        device_uuid: str,
    ) -> None:
        """Initialize the drive-off switch."""
        super().__init__(coordinator, device_uuid)
        self._attr_unique_id = f"{device_uuid}_drive_off_{UNIQUE_ID_VERSION}"
        self._attr_name = "Drive Fan Running"
        self._attr_entity_registry_enabled_default = (
            coordinator.entity_enabled_default("drive_off", "drive")
        )

    @property
    def available(self) -> bool:
        """Available only when Drive polling is on and drive data exists."""
        return (
            super().available
            and self.coordinator.enable_drive
            and bool(self._device_data.get("drivelog"))
        )

    @property
    def is_on(self) -> bool | None:
        """On when the Drive is currently running (fan output > 0)."""
        driveper = self._device_data.get("drivelog", {}).get("driveper")
        if driveper is None:
            return None
        try:
            return float(driveper) > 0
        except (ValueError, TypeError):
            return None

    async def async_turn_on(self, **kwargs: Any) -> None:
        """No direct 'on' command; the Drive resumes when a setpoint/speed is
        set. Turning on here is a no-op placeholder (state is assumed)."""
        _LOGGER.warning(
            "Drive 'Fan Running' cannot be turned on directly; set a setpoint "
            "or fan speed instead (device %s)",
            self._device_uuid,
        )

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the Drive fan off (sends setpoint 0)."""
        await self.coordinator.async_set_drive_off(self._device_uuid)
