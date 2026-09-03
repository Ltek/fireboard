"""Base entity for FireBoard integration."""

from __future__ import annotations

from typing import Any

from homeassistant.helpers.device_registry import (
    CONNECTION_NETWORK_MAC,
    DeviceInfo,
)
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, VERSION
from .coordinator import FireBoardDataUpdateCoordinator


def fireboard_device_name(device_info: dict[str, Any]) -> str:
    """Compose the device name as 'FireBoard <serial>'.

    Falls back to the API title, then a plain 'FireBoard', if no serial.
    """
    serial = device_info.get("hardware_id")
    if serial:
        return f"FireBoard {serial}"
    return device_info.get("title") or "FireBoard"


def build_device_info(
    coordinator: FireBoardDataUpdateCoordinator, device_uuid: str
) -> DeviceInfo:
    """Return the DeviceInfo for a physical FireBoard device.

    Every entity (temps, diagnostics, polling/interval controls, LAN IP, etc.)
    attaches to this single per-device DeviceInfo so they all group under one
    'FireBoard <serial>' device.
    """
    device_data = coordinator.data.get(device_uuid, {})
    device_info = device_data.get("device_info", {})
    device_log = device_data.get("device_log", {})

    connections = set()
    mac = device_log.get("macNIC")
    if mac:
        connections.add((CONNECTION_NETWORK_MAC, mac))

    return DeviceInfo(
        identifiers={(DOMAIN, device_uuid)},
        connections=connections,
        name=fireboard_device_name(device_info),
        manufacturer="FireBoard",
        model=device_info.get("model", "FireBoard"),
        serial_number=device_info.get("hardware_id"),
        sw_version=device_info.get("version") or VERSION,
        configuration_url="https://fireboard.io",
    )


class FireBoardEntity(CoordinatorEntity[FireBoardDataUpdateCoordinator]):
    """Base entity for FireBoard devices."""

    # Use HA's entity-naming: the entity's own name is just its sub-part
    # (e.g. "Battery Low"); HA composes the device name in front where needed.
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: FireBoardDataUpdateCoordinator,
        device_uuid: str,
        channel_number: int | None = None,
    ) -> None:
        """Initialize the entity.

        Args:
            coordinator: Data update coordinator
            device_uuid: Device UUID
            channel_number: Optional channel number for temperature entities

        """
        super().__init__(coordinator)
        self._device_uuid = device_uuid
        self._channel_number = channel_number

        # Get device info from coordinator data
        device_data = self.coordinator.data.get(device_uuid, {})
        device_info = device_data.get("device_info", {})

        self._device_title = device_info.get("title", "FireBoard")
        # The API exposes the product model in "model" and the serial number
        # in "hardware_id".
        self._device_model = device_info.get("model", "FireBoard")

    @property
    def device_info(self) -> DeviceInfo:
        """Return device information (one 'FireBoard <serial>' device)."""
        return build_device_info(self.coordinator, self._device_uuid)

    @property
    def available(self) -> bool:
        """Return if entity is available.

        Availability is decoupled from the last poll's success: a transient
        rate-limit or network blip should not flip every entity to
        ``unavailable`` (which loses recorder history and flickers the UI).
        As long as we have cached data and the device was last seen online, the
        entity stays available; staleness is surfaced via the coordinator's
        ``update_state`` attribute on diagnostic sensors instead.
        """
        device_data = self.coordinator.data.get(self._device_uuid)
        if device_data is None:
            # No data has ever been fetched for this device.
            return False
        return device_data.get("online", False)

    @property
    def _device_data(self) -> dict[str, Any]:
        """Return device data from coordinator."""
        return self.coordinator.data.get(self._device_uuid, {})


class FireBoardConfigEntity(FireBoardEntity):
    """A per-device configuration entity (LAN IP, offline polling, etc.).

    Lives on the same 'FireBoard <serial>' device as everything else, but stays
    available even when the device is offline (so you can always edit config).
    """

    @property
    def available(self) -> bool:
        """Config controls are available whenever the coordinator has data."""
        return self.coordinator.last_update_success
