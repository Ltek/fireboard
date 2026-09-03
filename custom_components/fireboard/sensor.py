"""Sensor platform for FireBoard integration."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    PERCENTAGE,
    SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
    EntityCategory,
    UnitOfElectricPotential,
    UnitOfTemperature,
)
from homeassistant.components.sensor import ENTITY_ID_FORMAT
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import async_generate_entity_id
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util

from .const import DOMAIN, UNIQUE_ID_VERSION, normalize_fireboard_timestamp
from .coordinator import FireBoardDataUpdateCoordinator
from .entity import FireBoardEntity

_LOGGER = logging.getLogger(__name__)

# Generic diagnostic fields exposed as sensors. Each entry:
#   (key, name, source, field, icon)
# source is "device" (top-level device dict) or "log" (device_log dict).
# All are diagnostic + disabled by default to avoid clutter.
_DIAGNOSTIC_FIELDS: tuple[tuple[str, str, str, str, str | None], ...] = (
    ("internal_ip", "IP Address", "log", "internalIP", "mdi:ip-network"),
    ("public_ip", "Public IP", "log", "publicIP", "mdi:wan"),
    ("mac_address", "MAC Address", "log", "macNIC", "mdi:network"),
    ("ssid", "WiFi Network", "log", "ssid", "mdi:wifi-settings"),
    ("wifi_band", "WiFi Band", "log", "band", "mdi:wifi"),
    ("wifi_frequency", "WiFi Frequency", "log", "frequency", "mdi:sine-wave"),
    ("uptime", "Uptime", "log", "uptime", "mdi:timer-outline"),
    ("firmware_version", "Firmware Version", "log", "version", "mdi:chip"),
    ("channel_count", "Channel Count", "device", "channel_count", "mdi:format-list-numbered"),
    ("model_name", "Model Name", "device", "model_name", "mdi:tag-outline"),
    ("hardware_id", "Serial Number", "device", "hardware_id", "mdi:identifier"),
)


def _unit_for_degreetype(degreetype: Any) -> str:
    """Map the device degreetype (1=C, 2=F) to a HA unit."""
    if degreetype == 1:
        return UnitOfTemperature.CELSIUS
    return UnitOfTemperature.FAHRENHEIT


def _parse_fireboard_datetime(raw: Any) -> Any:
    """Parse a FireBoard timestamp (with its "UTC" suffix) into an aware dt."""
    text = normalize_fireboard_timestamp(raw)
    if not text:
        return None
    parsed = dt_util.parse_datetime(text)
    if parsed is None:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt_util.UTC)
    return parsed


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up FireBoard sensor entities."""
    coordinator: FireBoardDataUpdateCoordinator = hass.data[DOMAIN][
        config_entry.entry_id
    ]

    entities: list[SensorEntity] = []

    for device_uuid, device_data in coordinator.data.items():
        device_info = device_data.get("device_info", {})

        # One temperature sensor per probe/channel.
        channels = device_info.get("channels", [])
        for channel in channels:
            channel_number = channel.get("channel")
            if channel_number is not None:
                entities.append(
                    FireBoardTemperatureSensor(
                        coordinator,
                        device_uuid,
                        channel_number,
                    )
                )

        # Device-level telemetry from device_log.
        entities.append(FireBoardBatterySensor(coordinator, device_uuid))
        entities.append(FireBoardBatteryVoltageSensor(coordinator, device_uuid))
        entities.append(FireBoardOnboardTempSensor(coordinator, device_uuid))
        entities.append(FireBoardLastSeenSensor(coordinator, device_uuid))
        entities.append(FireBoardSignalStrengthSensor(coordinator, device_uuid))
        entities.append(FireBoardLinkQualitySensor(coordinator, device_uuid))

        # Generic diagnostic sensors for the remaining device / device_log
        # fields the API reports (IP, MAC, SSID, channel count, versions, etc.).
        for descr in _DIAGNOSTIC_FIELDS:
            entities.append(
                FireBoardDiagnosticSensor(coordinator, device_uuid, descr)
            )

        # Cook session sensors (from sessions.json).
        entities.append(FireBoardCookStartedSensor(coordinator, device_uuid))
        entities.append(FireBoardCookSessionSensor(coordinator, device_uuid))
        entities.append(FireBoardSessionCountSensor(coordinator, device_uuid))
        entities.append(FireBoardLastCookSensor(coordinator, device_uuid))

        # FireBoard Drive sensors are always created; they become available
        # once Drive polling is enabled (via the switch) and data arrives, so
        # toggling the switch works on the fly without reloading the entry.
        entities.append(FireBoardDrivePercentSensor(coordinator, device_uuid))
        entities.append(FireBoardDriveSetpointSensor(coordinator, device_uuid))
        entities.append(FireBoardDriveBatterySensor(coordinator, device_uuid))

    async_add_entities(entities)


class FireBoardTemperatureSensor(FireBoardEntity, SensorEntity):
    """Representation of a FireBoard temperature probe."""

    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(
        self,
        coordinator: FireBoardDataUpdateCoordinator,
        device_uuid: str,
        channel_number: int,
    ) -> None:
        """Initialize the temperature sensor."""
        super().__init__(coordinator, device_uuid, channel_number)

        # Get channel info for naming from device configuration
        device_info = self._device_data.get("device_info", {})
        channels = device_info.get("channels", [])
        channel_label = ""

        for channel in channels:
            if channel.get("channel") == channel_number:
                channel_label = channel.get("channel_label") or ""
                break

        self._attr_unique_id = f"{device_uuid}_temp_{channel_number}_{UNIQUE_ID_VERSION}"

        # Give the channel a stable entity_id that does NOT include the custom
        # label or the area, so renaming the probe in the FireBoard app never
        # makes the id misleading. Format: sensor.<serial>_channel_<n>, matching
        # the auto-generated ids of the other entities (device-name slug).
        serial = (device_info.get("hardware_id") or device_uuid).lower()
        self.entity_id = async_generate_entity_id(
            ENTITY_ID_FORMAT,
            f"{serial}_channel_{channel_number}",
            hass=coordinator.hass,
        )

        # Name format: "Channel <#> - <label>". Omit the label when it is blank
        # or just FireBoard's own default ("Channel N"), so an un-named channel
        # shows plain "Channel N" instead of "Channel N - Channel N".
        default_label = f"Channel {channel_number}"
        if channel_label and channel_label.strip().casefold() != (
            default_label.casefold()
        ):
            self._attr_name = f"{default_label} - {channel_label}"
        else:
            self._attr_name = default_label

    def _get_channel(self) -> dict[str, Any]:
        """Return the channel object for this sensor from coordinator data."""
        device_info = self._device_data.get("device_info", {})
        for channel in device_info.get("channels", []):
            if channel.get("channel") == self._channel_number:
                return channel
        return {}

    @property
    def native_value(self) -> float | None:
        """Return the temperature value.

        The current reading lives in ``channel.last_templog.temp``. The API
        only populates it while the reading is fresh, so a probe that isn't
        plugged in / reporting reads as unknown.
        """
        last_templog = self._get_channel().get("last_templog")
        if not last_templog:
            return None

        temp = last_templog.get("temp")
        if temp is None:
            return None

        try:
            return float(temp)
        except (ValueError, TypeError):
            _LOGGER.warning(
                "Invalid temperature value for %s: %s", self._attr_name, temp
            )
            return None

    @property
    def native_unit_of_measurement(self) -> str:
        """Return the unit reported by the device (degreetype: 1=C, 2=F)."""
        device_info = self._device_data.get("device_info", {})
        return _unit_for_degreetype(device_info.get("degreetype"))

    @property
    def available(self) -> bool:
        """Return True only when this channel has a live reading."""
        return super().available and bool(self._get_channel().get("last_templog"))

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return additional state attributes."""
        channel = self._get_channel()
        attributes: dict[str, Any] = {"channel": self._channel_number}

        label = channel.get("channel_label")
        if label:
            attributes["label"] = label

        return attributes


class FireBoardOnboardTempSensor(FireBoardEntity, SensorEntity):
    """The FireBoard unit's internal board temperature (diagnostic).

    This measures the temperature of the FireBoard device's own circuit board /
    onboard sensor -- not a food probe or the pit -- so it lives in the
    Diagnostic section.
    """

    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False

    def __init__(
        self,
        coordinator: FireBoardDataUpdateCoordinator,
        device_uuid: str,
    ) -> None:
        """Initialize the onboard temperature sensor."""
        super().__init__(coordinator, device_uuid)
        self._attr_unique_id = f"{device_uuid}_onboard_temp_{UNIQUE_ID_VERSION}"
        self._attr_name = "Onboard Temperature"
        self._attr_entity_registry_enabled_default = coordinator.enable_diagnostics

    @property
    def native_value(self) -> float | None:
        """Return the onboard (ambient) temperature."""
        temp = self._device_data.get("device_log", {}).get("onboardTemp")
        if temp is None:
            return None
        try:
            return float(temp)
        except (ValueError, TypeError):
            return None

    @property
    def native_unit_of_measurement(self) -> str:
        """Return the unit reported by the device (degreetype: 1=C, 2=F)."""
        device_info = self._device_data.get("device_info", {})
        return _unit_for_degreetype(device_info.get("degreetype"))


class FireBoardLastSeenSensor(FireBoardEntity, SensorEntity):
    """Timestamp of the FireBoard's last temperature report (diagnostic).

    Also surfaces the coordinator's poll health so a rate-limit/blip is visible
    without every entity dropping to unavailable.
    """

    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:clock-check-outline"

    def __init__(
        self,
        coordinator: FireBoardDataUpdateCoordinator,
        device_uuid: str,
    ) -> None:
        """Initialize the last-seen sensor."""
        super().__init__(coordinator, device_uuid)
        self._attr_unique_id = f"{device_uuid}_last_seen_{UNIQUE_ID_VERSION}"
        self._attr_name = "Last Seen"

    @property
    def available(self) -> bool:
        """Available whenever we have data (even if the device is offline)."""
        return self.coordinator.data.get(self._device_uuid) is not None

    @property
    def native_value(self) -> Any:
        """Return the last templog timestamp as a datetime."""
        device_info = self._device_data.get("device_info", {})
        device_log = self._device_data.get("device_log", {})
        raw = device_log.get("date") or device_info.get("last_templog")
        return _parse_fireboard_datetime(raw)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Expose coordinator poll health for troubleshooting."""
        attrs: dict[str, Any] = {"update_state": self.coordinator.update_state}
        if self.coordinator.update_error:
            attrs["update_error"] = self.coordinator.update_error
        return attrs


class FireBoardCookStartedSensor(FireBoardEntity, SensorEntity):
    """Start time of the active cook session (from sessions.json)."""

    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_icon = "mdi:clock-start"

    def __init__(
        self,
        coordinator: FireBoardDataUpdateCoordinator,
        device_uuid: str,
    ) -> None:
        """Initialize the cook-started sensor."""
        super().__init__(coordinator, device_uuid)
        self._attr_unique_id = f"{device_uuid}_cook_started_{UNIQUE_ID_VERSION}"
        self._attr_name = "Cook Started"

    @property
    def available(self) -> bool:
        """Available only while a cook session is active."""
        return super().available and bool(self._device_data.get("session"))

    @property
    def native_value(self) -> Any:
        """Return the session start time as a datetime."""
        session = self._device_data.get("session", {})
        return _parse_fireboard_datetime(
            session.get("start_time") or session.get("created")
        )

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Expose the session id and duration."""
        session = self._device_data.get("session", {})
        attrs: dict[str, Any] = {}
        if "id" in session:
            attrs["session_id"] = session.get("id")
        if session.get("duration"):
            attrs["duration"] = session.get("duration")
        return attrs


class FireBoardCookSessionSensor(FireBoardEntity, SensorEntity):
    """Title of the active cook session (from sessions.json)."""

    _attr_icon = "mdi:grill"

    def __init__(
        self,
        coordinator: FireBoardDataUpdateCoordinator,
        device_uuid: str,
    ) -> None:
        """Initialize the cook-session sensor."""
        super().__init__(coordinator, device_uuid)
        self._attr_unique_id = f"{device_uuid}_cook_session_{UNIQUE_ID_VERSION}"
        self._attr_name = "Cook Session"

    @property
    def available(self) -> bool:
        """Available only while a cook session is active."""
        return super().available and bool(self._device_data.get("session"))

    @property
    def native_value(self) -> str | None:
        """Return the session title."""
        session = self._device_data.get("session", {})
        return session.get("title") or session.get("name")


class FireBoardBatterySensor(FireBoardEntity, SensorEntity):
    """Representation of a FireBoard battery level sensor."""

    _attr_device_class = SensorDeviceClass.BATTERY
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(
        self,
        coordinator: FireBoardDataUpdateCoordinator,
        device_uuid: str,
    ) -> None:
        """Initialize the battery sensor."""
        super().__init__(coordinator, device_uuid)
        self._attr_unique_id = f"{device_uuid}_battery_{UNIQUE_ID_VERSION}"
        self._attr_name = "Battery"

    @property
    def native_value(self) -> int | None:
        """Return the battery level as a percentage.

        The API reports ``device_log.vBattPer`` as a 0..1 fraction.
        """
        batt = self._device_data.get("device_log", {}).get("vBattPer")
        if batt is None:
            return None
        try:
            return round(float(batt) * 100)
        except (ValueError, TypeError):
            _LOGGER.warning(
                "Invalid battery value for %s: %s", self._attr_name, batt
            )
            return None


class FireBoardDrivePercentSensor(FireBoardEntity, SensorEntity):
    """FireBoard Drive fan/blower output percentage."""

    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_icon = "mdi:fan"

    def __init__(
        self,
        coordinator: FireBoardDataUpdateCoordinator,
        device_uuid: str,
    ) -> None:
        """Initialize the drive percent sensor."""
        super().__init__(coordinator, device_uuid)
        self._attr_unique_id = f"{device_uuid}_drive_percent_{UNIQUE_ID_VERSION}"
        self._attr_name = "Drive Output"

    @property
    def available(self) -> bool:
        """Available only when Drive polling is on and data has arrived."""
        return (
            super().available
            and self.coordinator.enable_drive
            and bool(self._device_data.get("drivelog"))
        )

    @property
    def native_value(self) -> int | None:
        """Return the drive output percentage (driveper is a 0..1 fraction)."""
        driveper = self._device_data.get("drivelog", {}).get("driveper")
        if driveper is None:
            return None
        try:
            return round(float(driveper) * 100)
        except (ValueError, TypeError):
            return None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return drive mode details."""
        drivelog = self._device_data.get("drivelog", {})
        attributes: dict[str, Any] = {}
        if "lidpaused" in drivelog:
            attributes["lid_paused"] = drivelog.get("lidpaused")
        if "tiedchannel" in drivelog:
            attributes["tied_channel"] = drivelog.get("tiedchannel")
        if "modetype" in drivelog:
            attributes["mode"] = drivelog.get("modetype")
        return attributes


class FireBoardDriveSetpointSensor(FireBoardEntity, SensorEntity):
    """FireBoard Drive target setpoint temperature."""

    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(
        self,
        coordinator: FireBoardDataUpdateCoordinator,
        device_uuid: str,
    ) -> None:
        """Initialize the drive setpoint sensor."""
        super().__init__(coordinator, device_uuid)
        self._attr_unique_id = f"{device_uuid}_drive_setpoint_{UNIQUE_ID_VERSION}"
        self._attr_name = "Drive Setpoint"

    @property
    def available(self) -> bool:
        """Available only when Drive polling is on and data has arrived."""
        return (
            super().available
            and self.coordinator.enable_drive
            and bool(self._device_data.get("drivelog"))
        )

    @property
    def native_value(self) -> float | None:
        """Return the drive setpoint temperature."""
        setpoint = self._device_data.get("drivelog", {}).get("setpoint")
        if setpoint is None:
            return None
        try:
            return float(setpoint)
        except (ValueError, TypeError):
            return None

    @property
    def native_unit_of_measurement(self) -> str:
        """Return the unit reported by the device (degreetype: 1=C, 2=F)."""
        device_info = self._device_data.get("device_info", {})
        return _unit_for_degreetype(device_info.get("degreetype"))


class FireBoardDriveBatterySensor(FireBoardEntity, SensorEntity):
    """FireBoard Drive battery voltage (diagnostic)."""

    _attr_device_class = SensorDeviceClass.VOLTAGE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfElectricPotential.VOLT
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False

    def __init__(
        self,
        coordinator: FireBoardDataUpdateCoordinator,
        device_uuid: str,
    ) -> None:
        """Initialize the drive battery sensor."""
        super().__init__(coordinator, device_uuid)
        self._attr_unique_id = f"{device_uuid}_drive_battery_{UNIQUE_ID_VERSION}"
        self._attr_name = "Drive Battery"

    @property
    def available(self) -> bool:
        """Available only when Drive polling is on and a voltage is present."""
        return (
            super().available
            and self.coordinator.enable_drive
            and self._device_data.get("drivelog", {}).get("vbatt") is not None
        )

    @property
    def native_value(self) -> float | None:
        """Return the drive battery voltage (drivelog.vbatt)."""
        vbatt = self._device_data.get("drivelog", {}).get("vbatt")
        if vbatt is None:
            return None
        try:
            return float(vbatt)
        except (ValueError, TypeError):
            return None


class FireBoardSignalStrengthSensor(FireBoardEntity, SensorEntity):
    """WiFi signal strength (RSSI) from device_log.signallevel (dBm)."""

    _attr_device_class = SensorDeviceClass.SIGNAL_STRENGTH
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = SIGNAL_STRENGTH_DECIBELS_MILLIWATT
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False

    def __init__(
        self,
        coordinator: FireBoardDataUpdateCoordinator,
        device_uuid: str,
    ) -> None:
        """Initialize the signal strength sensor."""
        super().__init__(coordinator, device_uuid)
        self._attr_unique_id = f"{device_uuid}_signal_strength_{UNIQUE_ID_VERSION}"
        self._attr_name = "WiFi Signal"
        self._attr_entity_registry_enabled_default = coordinator.enable_diagnostics

    @property
    def native_value(self) -> int | None:
        """Return the RSSI in dBm."""
        rssi = self._device_data.get("device_log", {}).get("signallevel")
        if rssi is None:
            return None
        try:
            return int(rssi)
        except (ValueError, TypeError):
            return None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Expose related WiFi details from device_log."""
        log = self._device_data.get("device_log", {})
        attrs: dict[str, Any] = {}
        for src, key in (
            ("ssid", "ssid"),
            ("frequency", "frequency"),
            ("band", "band"),
            ("internalIP", "internal_ip"),
            ("bleSignalLevel", "ble_signal_level"),
        ):
            if log.get(src) is not None:
                attrs[key] = log.get(src)
        return attrs


class FireBoardLinkQualitySensor(FireBoardEntity, SensorEntity):
    """WiFi link quality from device_log.linkquality (percentage 0-100)."""

    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False
    _attr_icon = "mdi:wifi"

    def __init__(
        self,
        coordinator: FireBoardDataUpdateCoordinator,
        device_uuid: str,
    ) -> None:
        """Initialize the link quality sensor."""
        super().__init__(coordinator, device_uuid)
        self._attr_unique_id = f"{device_uuid}_link_quality_{UNIQUE_ID_VERSION}"
        self._attr_name = "WiFi Link Quality"
        self._attr_entity_registry_enabled_default = coordinator.enable_diagnostics

    @property
    def native_value(self) -> int | None:
        """Return link quality as a percentage.

        device_log.linkquality is a string like "72/100"; report the numerator.
        """
        raw = self._device_data.get("device_log", {}).get("linkquality")
        if not raw:
            return None
        try:
            return int(str(raw).split("/")[0])
        except (ValueError, TypeError, IndexError):
            return None


class FireBoardBatteryVoltageSensor(FireBoardEntity, SensorEntity):
    """Actual battery voltage from device_log.vBatt (diagnostic)."""

    _attr_device_class = SensorDeviceClass.VOLTAGE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = UnitOfElectricPotential.VOLT
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False

    def __init__(
        self,
        coordinator: FireBoardDataUpdateCoordinator,
        device_uuid: str,
    ) -> None:
        """Initialize the battery voltage sensor."""
        super().__init__(coordinator, device_uuid)
        self._attr_unique_id = f"{device_uuid}_battery_voltage_{UNIQUE_ID_VERSION}"
        self._attr_name = "Battery Voltage"
        self._attr_entity_registry_enabled_default = coordinator.enable_diagnostics

    @property
    def native_value(self) -> float | None:
        """Return the device battery voltage (device_log.vBatt)."""
        vbatt = self._device_data.get("device_log", {}).get("vBatt")
        if vbatt is None:
            return None
        try:
            return round(float(vbatt), 3)
        except (ValueError, TypeError):
            return None


class FireBoardDiagnosticSensor(FireBoardEntity, SensorEntity):
    """Generic read-only diagnostic sensor for a single device/log field."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False

    def __init__(
        self,
        coordinator: FireBoardDataUpdateCoordinator,
        device_uuid: str,
        descr: tuple[str, str, str, str, str | None],
    ) -> None:
        """Initialize from a (key, name, source, field, icon) descriptor."""
        super().__init__(coordinator, device_uuid)
        key, name, source, field, icon = descr
        self._source = source
        self._field = field
        self._attr_unique_id = f"{device_uuid}_{key}_{UNIQUE_ID_VERSION}"
        self._attr_name = name
        self._attr_entity_registry_enabled_default = coordinator.enable_diagnostics
        if icon:
            self._attr_icon = icon

    @property
    def native_value(self) -> Any:
        """Return the raw field value from the device or device_log."""
        if self._source == "log":
            container = self._device_data.get("device_log", {})
        else:
            container = self._device_data.get("device_info", {})
        value = container.get(self._field)
        return value if value not in ("", None) else None


class FireBoardSessionCountSensor(FireBoardEntity, SensorEntity):
    """Total number of cook sessions recorded for this device."""

    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False
    _attr_icon = "mdi:history"

    def __init__(
        self,
        coordinator: FireBoardDataUpdateCoordinator,
        device_uuid: str,
    ) -> None:
        """Initialize the session count sensor."""
        super().__init__(coordinator, device_uuid)
        self._attr_unique_id = f"{device_uuid}_session_count_{UNIQUE_ID_VERSION}"
        self._attr_name = "Session Count"

    @property
    def native_value(self) -> int:
        """Return the number of sessions for this device."""
        return int(self._device_data.get("session_count", 0))


class FireBoardLastCookSensor(FireBoardEntity, SensorEntity):
    """Title of the most recent cook session (with times/duration attrs)."""

    _attr_icon = "mdi:grill-outline"
    _attr_entity_registry_enabled_default = False

    def __init__(
        self,
        coordinator: FireBoardDataUpdateCoordinator,
        device_uuid: str,
    ) -> None:
        """Initialize the last-cook sensor."""
        super().__init__(coordinator, device_uuid)
        self._attr_unique_id = f"{device_uuid}_last_cook_{UNIQUE_ID_VERSION}"
        self._attr_name = "Last Cook"

    @property
    def native_value(self) -> str | None:
        """Return the most-recent session title (or name)."""
        last = self._device_data.get("last_session", {})
        return last.get("title") or last.get("name")

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Expose start/end/duration and active flag of the last session."""
        last = self._device_data.get("last_session", {})
        if not last:
            return {}
        attrs: dict[str, Any] = {}
        for src, key in (
            ("start_time", "start_time"),
            ("end_time", "end_time"),
            ("duration", "duration"),
            ("id", "session_id"),
        ):
            if last.get(src) is not None:
                attrs[key] = last.get(src)
        attrs["active"] = not last.get("end_time")
        return attrs
