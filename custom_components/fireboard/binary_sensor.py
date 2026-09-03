"""Binary sensor platform for FireBoard integration."""

from __future__ import annotations

import logging

from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, UNIQUE_ID_VERSION
from .coordinator import FireBoardDataUpdateCoordinator
from .entity import FireBoardConfigEntity, FireBoardEntity

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up FireBoard binary sensor entities."""
    coordinator: FireBoardDataUpdateCoordinator = hass.data[DOMAIN][
        config_entry.entry_id
    ]

    entities: list[BinarySensorEntity] = []

    # Create binary sensors for each device
    for device_uuid, device_data in coordinator.data.items():
        # Device connectivity sensor
        entities.append(
            FireBoardConnectivitySensor(
                coordinator,
                device_uuid,
            )
        )

        # Battery low sensor
        entities.append(
            FireBoardBatteryLowSensor(
                coordinator,
                device_uuid,
            )
        )

        # Drive lid-paused sensor is always created; it becomes available once
        # Drive polling is enabled and data arrives.
        entities.append(
            FireBoardDriveLidPausedSensor(
                coordinator,
                device_uuid,
            )
        )

        # On-network sensor (fed by the built-in ping loop).
        entities.append(
            FireBoardOnNetworkSensor(
                coordinator,
                device_uuid,
            )
        )

        # Per-channel alert sensor for channels that have alerts configured.
        device_info = device_data.get("device_info", {})
        for channel in device_info.get("channels", []):
            channel_number = channel.get("channel")
            if channel_number is not None and channel.get("alerts"):
                entities.append(
                    FireBoardAlertBinarySensor(
                        coordinator,
                        device_uuid,
                        channel_number,
                    )
                )

    async_add_entities(entities)


class FireBoardConnectivitySensor(FireBoardEntity, BinarySensorEntity):
    """Representation of a FireBoard connectivity sensor."""

    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(
        self,
        coordinator: FireBoardDataUpdateCoordinator,
        device_uuid: str,
    ) -> None:
        """Initialize the connectivity sensor."""
        super().__init__(coordinator, device_uuid)

        # Set unique ID
        self._attr_unique_id = f"{device_uuid}_connectivity_{UNIQUE_ID_VERSION}"

        # Set name
        self._attr_name = "Connectivity"

    @property
    def is_on(self) -> bool:
        """Return true if device is connected."""
        return self._device_data.get("online", False)

    @property
    def available(self) -> bool:
        """Available whenever we have data for this device.

        The connectivity sensor must stay available to report the offline
        state, so it does not gate on the device being online.
        """
        return self.coordinator.data.get(self._device_uuid) is not None


class FireBoardBatteryLowSensor(FireBoardEntity, BinarySensorEntity):
    """Representation of a FireBoard battery low sensor."""

    _attr_device_class = BinarySensorDeviceClass.BATTERY
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(
        self,
        coordinator: FireBoardDataUpdateCoordinator,
        device_uuid: str,
    ) -> None:
        """Initialize the battery low sensor."""
        super().__init__(coordinator, device_uuid)

        # Set unique ID
        self._attr_unique_id = f"{device_uuid}_battery_low_{UNIQUE_ID_VERSION}"

        # Set name
        self._attr_name = "Battery Low"

    @property
    def is_on(self) -> bool | None:
        """Return true if battery is low.

        The API reports ``device_log.vBattPer`` as a 0..1 fraction.
        """
        batt = self._device_data.get("device_log", {}).get("vBattPer")

        if batt is not None:
            try:
                # Consider battery low if below 20%
                return float(batt) < 0.20
            except (ValueError, TypeError):
                _LOGGER.warning(
                    "Invalid battery value for %s: %s",
                    self._attr_name,
                    batt,
                )
                return None

        return None


class FireBoardDriveLidPausedSensor(FireBoardEntity, BinarySensorEntity):
    """Representation of a FireBoard Drive lid-paused state."""

    _attr_icon = "mdi:pause-circle"

    def __init__(
        self,
        coordinator: FireBoardDataUpdateCoordinator,
        device_uuid: str,
    ) -> None:
        """Initialize the lid-paused sensor."""
        super().__init__(coordinator, device_uuid)
        self._attr_unique_id = f"{device_uuid}_drive_lid_paused_{UNIQUE_ID_VERSION}"
        self._attr_name = "Drive Lid Paused"

    @property
    def available(self) -> bool:
        """Available only when Drive polling is on and data has arrived."""
        return (
            super().available
            and self.coordinator.enable_drive
            and bool(self._device_data.get("drivelog"))
        )

    @property
    def is_on(self) -> bool | None:
        """Return true if the Drive is paused because the lid is open."""
        drivelog = self._device_data.get("drivelog", {})
        return drivelog.get("lidpaused")


class FireBoardOnNetworkSensor(FireBoardConfigEntity, BinarySensorEntity):
    """Whether the device's configured LAN IP responds to ping.

    Populated by the coordinator's built-in ping loop. Unavailable until a LAN
    IP is set for the device.
    """

    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:lan"

    def __init__(
        self,
        coordinator: FireBoardDataUpdateCoordinator,
        device_uuid: str,
    ) -> None:
        """Initialize the on-network sensor."""
        super().__init__(coordinator, device_uuid)
        self._attr_unique_id = f"{device_uuid}_on_network_{UNIQUE_ID_VERSION}"
        self._attr_name = "On Network"

    @property
    def available(self) -> bool:
        """Available only once a LAN IP has been configured and pinged."""
        return (
            self.coordinator.last_update_success
            and bool(self.coordinator.device_ip(self._device_uuid))
            and self.coordinator.device_on_network(self._device_uuid) is not None
        )

    @property
    def is_on(self) -> bool | None:
        """Return true if the device IP currently responds to ping."""
        return self.coordinator.device_on_network(self._device_uuid)

    @property
    def extra_state_attributes(self) -> dict[str, str]:
        """Expose the effective/cloud/manual IPs for reference."""
        attrs: dict[str, str] = {}
        effective = self.coordinator.device_ip(self._device_uuid)
        if effective:
            attrs["ip_address"] = effective
        cloud = self.coordinator.device_cloud_ip(self._device_uuid)
        if cloud:
            attrs["cloud_ip"] = cloud
        manual = self.coordinator.device_manual_ip(self._device_uuid)
        if manual:
            attrs["manual_ip"] = manual
        return attrs


class FireBoardAlertBinarySensor(FireBoardEntity, BinarySensorEntity):
    """Fires when a probe's temperature leaves its configured alert window.

    FireBoard channels carry an ``alerts`` array; each alert has ``temp_min``
    and/or ``temp_max`` and an ``enabled`` flag. This sensor is ON when the
    current temperature is outside any enabled alert's bounds.
    """

    _attr_device_class = BinarySensorDeviceClass.PROBLEM
    _attr_icon = "mdi:thermometer-alert"

    def __init__(
        self,
        coordinator: FireBoardDataUpdateCoordinator,
        device_uuid: str,
        channel_number: int,
    ) -> None:
        """Initialize the alert sensor."""
        super().__init__(coordinator, device_uuid, channel_number)

        label = f"Channel {channel_number}"
        for channel in self._device_data.get("device_info", {}).get(
            "channels", []
        ):
            if channel.get("channel") == channel_number:
                label = channel.get("channel_label", label)
                break

        self._attr_unique_id = f"{device_uuid}_channel_{channel_number}_alert_{UNIQUE_ID_VERSION}"
        self._attr_name = f"{label} Alert"

    def _channel(self) -> dict[str, Any]:
        """Return this sensor's channel object."""
        for channel in self._device_data.get("device_info", {}).get(
            "channels", []
        ):
            if channel.get("channel") == self._channel_number:
                return channel
        return {}

    def _current_temp(self) -> float | None:
        """Return the channel's current temperature, if any."""
        last = self._channel().get("last_templog") or {}
        temp = last.get("temp")
        if temp is None:
            return None
        try:
            return float(temp)
        except (ValueError, TypeError):
            return None

    @property
    def is_on(self) -> bool | None:
        """Return True if temperature is outside any enabled alert window."""
        temp = self._current_temp()
        if temp is None:
            return None

        for alert in self._channel().get("alerts", []):
            if not alert.get("enabled", True):
                continue
            temp_min = alert.get("temp_min")
            temp_max = alert.get("temp_max")
            if temp_min is not None and temp < float(temp_min):
                return True
            if temp_max is not None and temp > float(temp_max):
                return True
        return False

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Expose the configured alert thresholds."""
        thresholds = []
        for alert in self._channel().get("alerts", []):
            thresholds.append(
                {
                    "enabled": alert.get("enabled", True),
                    "temp_min": alert.get("temp_min"),
                    "temp_max": alert.get("temp_max"),
                }
            )
        return {"channel": self._channel_number, "alerts": thresholds}
