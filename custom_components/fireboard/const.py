"""Constants for the FireBoard integration."""

import re
from typing import Final

# Integration domain
DOMAIN: Final = "fireboard"

# Release version: YYYY.MM.DD.N where N is an increment that never resets.
# Keep in sync with manifest.json "version".
VERSION: Final = "2026.09.03.28"

# Suffix appended to every entity unique_id. Bump this to force Home Assistant
# to create brand-new entities (with current naming) instead of re-adopting
# stale entity-registry rows from an earlier install. Old rows become inert
# orphans.
UNIQUE_ID_VERSION: Final = "v4"

# Strict IPv4 dotted-decimal: four 0-255 octets, no leading zeros, no
# hostnames/IPv6/partials. Used to validate user-entered device LAN IPs.
_IPV4_OCTET = r"(25[0-5]|2[0-4][0-9]|1[0-9][0-9]|[1-9]?[0-9])"
IPV4_REGEX: Final = re.compile(rf"^{_IPV4_OCTET}\.{_IPV4_OCTET}\.{_IPV4_OCTET}\.{_IPV4_OCTET}$")


def is_valid_ipv4(value: str) -> bool:
    """Return True if value is a strict IPv4 dotted-decimal address."""
    return bool(IPV4_REGEX.match(value.strip()))


def normalize_fireboard_timestamp(raw: object) -> str | None:
    """Normalize a FireBoard timestamp string for datetime parsing.

    FireBoard's device_log.date looks like "2026-09-03 00:51:00 UTC" -- a "UTC"
    word suffix rather than an ISO offset, which stdlib/HA parsers reject.
    Convert that suffix to "+00:00"; return None for empty input.
    """
    if not raw:
        return None
    text = str(raw).strip()
    if text.endswith(" UTC"):
        text = text[: -len(" UTC")] + "+00:00"
    return text

# API Configuration
API_BASE_URL: Final = "https://fireboard.io/api/v1"
API_TIMEOUT: Final = 30
API_RATE_LIMIT: Final = 200  # calls per hour

# Configuration keys
CONF_EMAIL: Final = "email"
CONF_PASSWORD: Final = "password"
CONF_POLLING_INTERVAL: Final = "polling_interval"  # legacy (devices.json)
CONF_ENABLE_DRIVE: Final = "enable_drive"
# Per-endpoint refresh intervals (seconds)
CONF_DEVICES_INTERVAL: Final = "devices_interval"  # devices.json
CONF_DRIVE_INTERVAL: Final = "drive_interval"  # drivelog.json (per device)
CONF_OFFLINE_INTERVAL: Final = "offline_interval"  # devices.json when offline
# Toggles that control whether normally-disabled entities are created enabled.
CONF_ENABLE_DIAGNOSTICS: Final = "enable_diagnostics"
CONF_ENABLE_SETPOINT: Final = "enable_setpoint_control"
DEFAULT_ENABLE_DIAGNOSTICS: Final = False
DEFAULT_ENABLE_SETPOINT: Final = False

# Per-device configuration, stored in options under CONF_DEVICE_CONFIG as
# {uuid: {DEV_CONF_IP: str, DEV_CONF_OFFLINE_POLL: bool}}.
CONF_DEVICE_CONFIG: Final = "device_config"
DEV_CONF_IP: Final = "ip"
DEV_CONF_OFFLINE_POLL: Final = "offline_poll_enabled"

# Default values for optional config
DEFAULT_ENABLE_DRIVE: Final = True  # poll drivelog.json (one call/device/poll)
DEFAULT_OFFLINE_POLL_ENABLED: Final = False

# Reachability (built-in ping) settings
PING_INTERVAL_SECONDS: Final = 20  # how often to ping configured device IPs
# When a feature-enabled device is confirmed off-network, back the API poll off
# to this idle rate instead of stopping entirely (safety net if ping is wrong).
IDLE_INTERVAL: Final = 900  # 15 minutes

# Refresh interval defaults / bounds (seconds).
# The FireBoard API drops readings older than 60s and allows ~200 calls/hour,
# so 40s keeps data fresh (90 calls/hour) while staying well under the limit.
DEFAULT_POLLING_INTERVAL: Final = 40  # seconds (90 calls/hour, well under 200 limit)
# 10s minimum is intended for short-term troubleshooting of temperature
# stability. At 10s a single endpoint makes 360 calls/hour, which EXCEEDS the
# ~200/hour API limit if sustained -- use briefly, not as a steady state.
MIN_POLLING_INTERVAL: Final = 10  # minimum (troubleshooting; may hit rate limit)
MAX_POLLING_INTERVAL: Final = 300  # 5 minutes maximum

# devices.json refresh (temperatures, battery, onboard temp, model, firmware)
DEFAULT_DEVICES_INTERVAL: Final = 40
# drivelog.json refresh (one call per device, so a higher floor is used)
DEFAULT_DRIVE_INTERVAL: Final = 40
MIN_DRIVE_INTERVAL: Final = 10
# Fast poll used to catch reconnection when a device is on-network but the
# cloud data is still stale. Only active once ping detects the device on the
# network, so a fast default doesn't waste calls while the device is offline.
DEFAULT_OFFLINE_INTERVAL: Final = 20
MIN_OFFLINE_INTERVAL: Final = 10

# Device information
ATTR_DEVICE_ID: Final = "device_id"
ATTR_DEVICE_UUID: Final = "uuid"
ATTR_DEVICE_TITLE: Final = "title"
ATTR_DEVICE_MODEL: Final = "model"
ATTR_DEVICE_HARDWARE: Final = "hardware_id"

# Channel information
ATTR_CHANNEL_NUMBER: Final = "channel"
ATTR_CHANNEL_LABEL: Final = "label"
ATTR_TEMPERATURE: Final = "temperature"
ATTR_TARGET_TEMP: Final = "target_temp"

# Session information
ATTR_SESSION_ID: Final = "session_id"
ATTR_SESSION_START: Final = "start_time"

# Battery information
ATTR_BATTERY_LEVEL: Final = "battery_level"
ATTR_BATTERY_LOW: Final = "battery_low"

# Connection status
ATTR_ONLINE: Final = "online"
ATTR_LAST_SEEN: Final = "last_seen"
