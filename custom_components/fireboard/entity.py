"""Base entity for FireBoard integration."""

from __future__ import annotations

from typing import Any

from homeassistant.helpers.device_registry import (
    CONNECTION_NETWORK_MAC,
    DeviceEntryType,
    DeviceInfo,
)
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, VERSION
from .coordinator import FireBoardDataUpdateCoordinator

# Name of the integration-level "service" device that groups the FireBoard
# cloud connection settings (refresh intervals, drive toggle, per-device LAN IP
# and offline-polling controls).
SERVICE_DEVICE_NAME = "FireBoard Server Connection"


def service_device_info(entry_id: str) -> DeviceInfo:
    """Return the DeviceInfo for the integration's server-connection device."""
    return DeviceInfo(
        identifiers={(DOMAIN, f"{entry_id}_config")},
        name=SERVICE_DEVICE_NAME,
        manufacturer="FireBoard",
        model="Cloud Integration",
        sw_version=VERSION,
        entry_type=DeviceEntryType.SERVICE,
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

    def _device_name(self) -> str:
        """Compose the device name as 'Model Serial' (falls back gracefully)."""
        device_info = self.coordinator.data.get(self._device_uuid, {}).get(
            "device_info", {}
        )
        model = device_info.get("model")
        serial = device_info.get("hardware_id")
        parts = [p for p in (model, serial) if p]
        return " ".join(parts) or device_info.get("title") or "FireBoard"

    @property
    def device_info(self) -> DeviceInfo:
        """Return device information."""
        device_data = self.coordinator.data.get(self._device_uuid, {})
        device_info = device_data.get("device_info", {})
        device_log = device_data.get("device_log", {})

        # Expose the network MAC (device_log.macNIC) as a device connection.
        connections = set()
        mac = device_log.get("macNIC")
        if mac:
            connections.add((CONNECTION_NETWORK_MAC, mac))

        return DeviceInfo(
            identifiers={(DOMAIN, self._device_uuid)},
            connections=connections,
            # Device name is Model + Serial (e.g. "FBX2D GCMC8H432").
            name=self._device_name(),
            manufacturer="FireBoard",
            model=device_info.get("model", "FireBoard"),
            serial_number=device_info.get("hardware_id"),
            # Firmware version is reported in the "version" field.
            sw_version=device_info.get("version"),
            configuration_url="https://fireboard.io",
        )

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
    """A per-device configuration entity grouped under the service device.

    Behaves like FireBoardEntity (knows its device UUID / title) but is placed
    on the integration's "FireBoard Server Connection" device so all connection
    and polling controls live together, rather than being mixed in with the
    physical FireBoard's temperature entities.
    """

    # These share one service device across all FireBoards, so keep the full
    # explicit name (which includes the device title) to tell them apart.
    _attr_has_entity_name = False

    @property
    def device_info(self) -> DeviceInfo:
        """Group this control under the server-connection service device."""
        return service_device_info(self.coordinator.config_entry.entry_id)

    @property
    def available(self) -> bool:
        """Config controls are available whenever the coordinator has data."""
        return self.coordinator.last_update_success
