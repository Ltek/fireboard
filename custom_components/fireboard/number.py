"""Number platform for FireBoard: live-adjustable refresh intervals."""

from __future__ import annotations

import logging

from homeassistant.components.number import (
    NumberDeviceClass,
    NumberEntity,
    NumberMode,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    PERCENTAGE,
    EntityCategory,
    UnitOfTemperature,
    UnitOfTime,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    CONF_DEVICES_INTERVAL,
    CONF_DRIVE_INTERVAL,
    CONF_OFFLINE_INTERVAL,
    DOMAIN,
    MAX_POLLING_INTERVAL,
    MIN_DRIVE_INTERVAL,
    MIN_OFFLINE_INTERVAL,
    MIN_POLLING_INTERVAL,
    UNIQUE_ID_VERSION,
)
from .coordinator import FireBoardDataUpdateCoordinator
from .entity import FireBoardEntity, service_device_info

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the FireBoard interval number entities."""
    coordinator: FireBoardDataUpdateCoordinator = hass.data[DOMAIN][
        config_entry.entry_id
    ]

    entities: list[NumberEntity] = [
        FireBoardDevicesIntervalNumber(coordinator),
        FireBoardDriveIntervalNumber(coordinator),
        FireBoardOfflineIntervalNumber(coordinator),
    ]

    # Experimental write-back controls per device (disabled by default;
    # opt-in via the "enable setpoint/Drive control" option).
    for device_uuid in coordinator.data:
        entities.append(FireBoardDriveSetpointNumber(coordinator, device_uuid))
        entities.append(FireBoardDriveSpeedNumber(coordinator, device_uuid))
        entities.append(FireBoardDriveChannelNumber(coordinator, device_uuid))

    async_add_entities(entities)


class _FireBoardIntervalNumber(
    CoordinatorEntity[FireBoardDataUpdateCoordinator], NumberEntity
):
    """Base class for a config-entry-scoped interval control."""

    _attr_entity_category = EntityCategory.CONFIG
    _attr_native_unit_of_measurement = UnitOfTime.SECONDS
    _attr_native_step = 1
    _attr_mode = NumberMode.BOX

    def __init__(self, coordinator: FireBoardDataUpdateCoordinator) -> None:
        """Initialize the interval number."""
        super().__init__(coordinator)
        self._attr_device_info = service_device_info(
            coordinator.config_entry.entry_id
        )


class FireBoardDevicesIntervalNumber(_FireBoardIntervalNumber):
    """Refresh interval for devices.json (temps, battery, onboard, etc.)."""

    _attr_icon = "mdi:timer-sync"
    _attr_native_min_value = MIN_POLLING_INTERVAL
    _attr_native_max_value = MAX_POLLING_INTERVAL

    def __init__(self, coordinator: FireBoardDataUpdateCoordinator) -> None:
        """Initialize the devices interval number."""
        super().__init__(coordinator)
        entry = coordinator.config_entry
        self._attr_unique_id = f"{entry.entry_id}_devices_interval_{UNIQUE_ID_VERSION}"
        self._attr_name = "FireBoard Devices Refresh Interval"

    @property
    def native_value(self) -> float:
        """Return the current devices refresh interval."""
        return self.coordinator.devices_interval

    async def async_set_native_value(self, value: float) -> None:
        """Persist a new devices refresh interval (applied live)."""
        await self.coordinator.async_set_option(
            CONF_DEVICES_INTERVAL, int(value)
        )


class FireBoardDriveIntervalNumber(_FireBoardIntervalNumber):
    """Refresh interval for drivelog.json (one request per device)."""

    _attr_icon = "mdi:timer-sync-outline"
    _attr_native_min_value = MIN_DRIVE_INTERVAL
    _attr_native_max_value = MAX_POLLING_INTERVAL

    def __init__(self, coordinator: FireBoardDataUpdateCoordinator) -> None:
        """Initialize the drive interval number."""
        super().__init__(coordinator)
        entry = coordinator.config_entry
        self._attr_unique_id = f"{entry.entry_id}_drive_interval_{UNIQUE_ID_VERSION}"
        self._attr_name = "FireBoard Drive Refresh Interval"

    @property
    def native_value(self) -> float:
        """Return the current drive refresh interval."""
        return self.coordinator.drive_interval

    @property
    def available(self) -> bool:
        """Only relevant when Drive polling is enabled."""
        return self.coordinator.enable_drive

    async def async_set_native_value(self, value: float) -> None:
        """Persist a new drive refresh interval (applied live)."""
        await self.coordinator.async_set_option(
            CONF_DRIVE_INTERVAL, int(value)
        )


class FireBoardOfflineIntervalNumber(_FireBoardIntervalNumber):
    """Fast interval used to catch reconnection (on-network but stale)."""

    _attr_icon = "mdi:timer-alert-outline"
    _attr_native_min_value = MIN_OFFLINE_INTERVAL
    _attr_native_max_value = MAX_POLLING_INTERVAL

    def __init__(self, coordinator: FireBoardDataUpdateCoordinator) -> None:
        """Initialize the offline interval number."""
        super().__init__(coordinator)
        entry = coordinator.config_entry
        self._attr_unique_id = f"{entry.entry_id}_offline_interval_{UNIQUE_ID_VERSION}"
        self._attr_name = "FireBoard Offline Refresh Interval"

    @property
    def native_value(self) -> float:
        """Return the current offline refresh interval."""
        return self.coordinator.offline_interval

    async def async_set_native_value(self, value: float) -> None:
        """Persist a new offline refresh interval (applied live)."""
        await self.coordinator.async_set_option(
            CONF_OFFLINE_INTERVAL, int(value)
        )


class FireBoardDriveSetpointNumber(FireBoardEntity, NumberEntity):
    """EXPERIMENTAL writable FireBoard Drive setpoint.

    Disabled by default and opt-in. Setting a value POSTs to the FireBoard API
    to change the Drive's target temperature. The write endpoint is NOT
    documented by FireBoard and may be rejected or change without notice, so
    this is intentionally hidden unless the user enables it.
    """

    _attr_device_class = NumberDeviceClass.TEMPERATURE
    _attr_mode = NumberMode.BOX
    _attr_native_step = 1
    _attr_entity_category = EntityCategory.CONFIG
    _attr_entity_registry_enabled_default = False
    _attr_icon = "mdi:target"

    def __init__(
        self,
        coordinator: FireBoardDataUpdateCoordinator,
        device_uuid: str,
    ) -> None:
        """Initialize the setpoint number."""
        super().__init__(coordinator, device_uuid)
        self._attr_unique_id = f"{device_uuid}_setpoint_target_{UNIQUE_ID_VERSION}"
        self._attr_name = "Drive Setpoint Target"
        # Enabled by default per user request (still only available when Drive
        # polling is on and drive data is present).
        self._attr_entity_registry_enabled_default = True

    def _is_celsius(self) -> bool:
        """Return True if the device reports in Celsius (degreetype 1)."""
        return self._device_data.get("device_info", {}).get("degreetype") == 1

    @property
    def native_unit_of_measurement(self) -> str:
        """Return the device's temperature unit."""
        if self._is_celsius():
            return UnitOfTemperature.CELSIUS
        return UnitOfTemperature.FAHRENHEIT

    @property
    def native_min_value(self) -> float:
        """Return the minimum allowed setpoint for the device's unit."""
        return 0 if self._is_celsius() else 32

    @property
    def native_max_value(self) -> float:
        """Return the maximum allowed setpoint for the device's unit."""
        return 260 if self._is_celsius() else 500

    @property
    def available(self) -> bool:
        """Available only when Drive polling is on and drive data exists."""
        return (
            super().available
            and self.coordinator.enable_drive
            and bool(self._device_data.get("drivelog"))
        )

    @property
    def native_value(self) -> float | None:
        """Return the current Drive setpoint (read from drivelog)."""
        setpoint = self._device_data.get("drivelog", {}).get("setpoint")
        if setpoint is None:
            return None
        try:
            return float(setpoint)
        except (ValueError, TypeError):
            return None

    async def async_set_native_value(self, value: float) -> None:
        """Write the new setpoint to the Drive (experimental)."""
        await self.coordinator.async_set_drive_setpoint(
            self._device_uuid, value
        )


class FireBoardDriveSpeedNumber(FireBoardEntity, NumberEntity):
    """EXPERIMENTAL manual Drive fan speed (0-100%).

    Setting a value puts the Drive into manual mode at a fixed fan power.
    Disabled by default; opt-in only. Controls physical hardware.
    """

    _attr_mode = NumberMode.SLIDER
    _attr_native_min_value = 0
    _attr_native_max_value = 100
    _attr_native_step = 1
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_entity_category = EntityCategory.CONFIG
    _attr_icon = "mdi:fan"

    def __init__(
        self,
        coordinator: FireBoardDataUpdateCoordinator,
        device_uuid: str,
    ) -> None:
        """Initialize the fan-speed number."""
        super().__init__(coordinator, device_uuid)
        self._attr_unique_id = f"{device_uuid}_drive_speed_{UNIQUE_ID_VERSION}"
        self._attr_name = "Drive Fan Speed"
        self._attr_entity_registry_enabled_default = coordinator.enable_setpoint

    @property
    def available(self) -> bool:
        """Available only when Drive polling is on and drive data exists."""
        return (
            super().available
            and self.coordinator.enable_drive
            and bool(self._device_data.get("drivelog"))
        )

    @property
    def native_value(self) -> float | None:
        """Return the current fan output % (driveper is a 0..1 fraction)."""
        driveper = self._device_data.get("drivelog", {}).get("driveper")
        if driveper is None:
            return None
        try:
            return round(float(driveper) * 100)
        except (ValueError, TypeError):
            return None

    async def async_set_native_value(self, value: float) -> None:
        """Set a fixed fan speed on the Drive (experimental)."""
        await self.coordinator.async_set_drive_speed(self._device_uuid, value)


class FireBoardDriveChannelNumber(FireBoardEntity, NumberEntity):
    """EXPERIMENTAL: which channel the Drive PID controls (tied channel).

    Disabled by default; opt-in only. Controls physical hardware.
    """

    _attr_mode = NumberMode.BOX
    _attr_native_min_value = 1
    _attr_native_step = 1
    _attr_entity_category = EntityCategory.CONFIG
    _attr_icon = "mdi:thermometer-lines"

    def __init__(
        self,
        coordinator: FireBoardDataUpdateCoordinator,
        device_uuid: str,
    ) -> None:
        """Initialize the tied-channel number."""
        super().__init__(coordinator, device_uuid)
        self._attr_unique_id = f"{device_uuid}_drive_channel_{UNIQUE_ID_VERSION}"
        self._attr_name = "Drive Control Channel"
        self._attr_entity_registry_enabled_default = coordinator.enable_setpoint

    @property
    def native_max_value(self) -> float:
        """Max = the device's channel count (fallback to 8)."""
        count = self._device_data.get("device_info", {}).get("channel_count")
        try:
            return int(count) if count else 8
        except (ValueError, TypeError):
            return 8

    @property
    def available(self) -> bool:
        """Available only when Drive polling is on and drive data exists."""
        return (
            super().available
            and self.coordinator.enable_drive
            and bool(self._device_data.get("drivelog"))
        )

    @property
    def native_value(self) -> float | None:
        """Return the currently tied channel (drivelog.tiedchannel)."""
        tied = self._device_data.get("drivelog", {}).get("tiedchannel")
        if tied is None:
            return None
        try:
            return int(tied)
        except (ValueError, TypeError):
            return None

    async def async_set_native_value(self, value: float) -> None:
        """Set which channel the Drive PID controls (experimental)."""
        await self.coordinator.async_set_drive_channel(
            self._device_uuid, int(value)
        )
