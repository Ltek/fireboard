"""Data update coordinator for the FireBoard integration.

All data is delivered by the FireBoard cloud REST API. A single call to
``devices.json`` returns every device with its nested channel readings
(``channels[].last_templog``), onboard/battery telemetry (``device_log``),
model and firmware info, so one poll refreshes every entity and counts as a
single request against the rate limit.

Refresh scheduling adapts to per-device state:
  * ``devices.json`` — one call; polled at the normal interval when a device
    is reporting to the cloud.
  * ``drivelog.json`` — one call per device, refreshes FireBoard Drive data.

Optional per-device "offline polling": the FireBoard cloud API exposes no IP
address, so the user supplies the device's LAN IP. The integration pings that
IP itself (no external automation needed). When a device is on the network but
its cloud data is still stale, the coordinator polls fast (the offline
interval) to catch the reconnection quickly. When a device is confirmed off the
network, the poll backs off to an idle rate to conserve API calls, and the ping
loop resumes fast polling the moment the IP reappears.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api_client import (
    FireBoardApiClient,
    FireBoardApiClientAuthenticationError,
    FireBoardApiClientCommunicationError,
    FireBoardApiClientError,
    FireBoardApiClientRateLimitError,
)
from .const import (
    CONF_DEVICE_CONFIG,
    CONF_DEVICES_INTERVAL,
    CONF_DRIVE_INTERVAL,
    CONF_ENABLE_DIAGNOSTICS,
    CONF_ENABLE_DRIVE,
    CONF_ENABLE_SETPOINT,
    CONF_ENABLED_ENTITIES,
    CONF_OFFLINE_INTERVAL,
    CONF_POLLING_INTERVAL,
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
    IDLE_INTERVAL,
    PING_INTERVAL_SECONDS,
    normalize_fireboard_timestamp,
)

_LOGGER = logging.getLogger(__name__)

# A device is considered online (reporting to the cloud) if it reported within
# this window.
ONLINE_THRESHOLD = timedelta(minutes=5)


class FireBoardDataUpdateCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Class to manage fetching data from the FireBoard REST API."""

    def __init__(
        self,
        hass: HomeAssistant,
        config_entry: ConfigEntry,
    ) -> None:
        """Initialize the coordinator."""
        self.config_entry = config_entry

        # Live-adjustable settings (kept in sync by async_apply_options()).
        self._enable_drive: bool = DEFAULT_ENABLE_DRIVE
        self._devices_interval: int = DEFAULT_DEVICES_INTERVAL
        self._drive_interval: int = DEFAULT_DRIVE_INTERVAL
        self._offline_interval: int = DEFAULT_OFFLINE_INTERVAL
        self._enable_diagnostics: bool = DEFAULT_ENABLE_DIAGNOSTICS
        self._enable_setpoint: bool = DEFAULT_ENABLE_SETPOINT
        self._enabled_entities: dict[str, bool] = {}
        # Per-device config: {uuid: {ip, offline_poll_enabled}}.
        self._device_config: dict[str, dict[str, Any]] = {}

        # Runtime state (not persisted).
        self._cloud_online: dict[str, bool] = {}  # from devices.json freshness
        self._on_network: dict[str, bool | None] = {}  # from ping (None=unknown)
        # Last LAN IP the cloud reported per device (device_log.internalIP).
        self._cloud_ip: dict[str, str] = {}
        self._last_devices_fetch: float | None = None
        self._last_drive_fetch: float | None = None
        self._ping_unsub: CALLBACK_TYPE | None = None
        # Health of the most recent poll: "ok" | "rate_limited" | "error".
        self.update_state: str = "ok"
        self.update_error: str | None = None
        # One-time device-field diagnostic dump guard.
        self._logged_device_shape: bool = False

        self._read_options()

        session = async_get_clientsession(hass)
        self.client = FireBoardApiClient(
            email=config_entry.data[CONF_EMAIL],
            password=config_entry.data[CONF_PASSWORD],
            session=session,
        )

        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=self._tick_seconds()),
        )

    @property
    def primary_device_uuid(self) -> str | None:
        """Return the first device UUID (host for global controls)."""
        return next(iter(self.data), None) if self.data else None

    # ---- Global live settings (exposed to number/switch entities) ----

    @property
    def devices_interval(self) -> int:
        """Current devices.json refresh interval in seconds."""
        return self._devices_interval

    @property
    def drive_interval(self) -> int:
        """Current drivelog.json refresh interval in seconds."""
        return self._drive_interval

    @property
    def offline_interval(self) -> int:
        """Fast interval used to catch reconnection (on-network but stale)."""
        return self._offline_interval

    @property
    def enable_drive(self) -> bool:
        """Whether FireBoard Drive polling is enabled."""
        return self._enable_drive

    @property
    def enable_diagnostics(self) -> bool:
        """Whether diagnostic sensors should be created enabled by default."""
        return self._enable_diagnostics

    @property
    def enable_setpoint(self) -> bool:
        """Whether the experimental setpoint control is enabled by default."""
        return self._enable_setpoint

    @property
    def enabled_entities(self) -> dict[str, bool]:
        """Return the per-entity enable override map."""
        return self._enabled_entities

    def entity_enabled_default(self, key: str, group: str) -> bool:
        """Resolve enabled-by-default for an optional entity.

        A per-entity override (from the options flow's entity page) wins;
        otherwise fall back to the group toggle ("diagnostics" or "drive").
        """
        overrides = self._enabled_entities
        if key in overrides:
            return bool(overrides[key])
        if group == "drive":
            return self._enable_setpoint
        return self._enable_diagnostics

    # ---- Per-device settings / state (exposed to per-device entities) ----

    def device_manual_ip(self, uuid: str) -> str | None:
        """Return the user-entered LAN IP for a device (may be blank/None).

        This is what the LAN IP text entity displays/edits; it is never
        overwritten by the cloud value.
        """
        return self._device_config.get(uuid, {}).get(DEV_CONF_IP) or None

    def device_cloud_ip(self, uuid: str) -> str | None:
        """Return the LAN IP the cloud last reported (device_log.internalIP)."""
        return self._cloud_ip.get(uuid)

    def device_ip(self, uuid: str) -> str | None:
        """Effective LAN IP used for reachability checks.

        Prefers the cloud-reported IP (authoritative while the device is
        reporting; corrects a stale manual value after a new DHCP lease) and
        falls back to the user-entered manual IP otherwise.
        """
        return self._cloud_ip.get(uuid) or self.device_manual_ip(uuid)

    def device_offline_poll(self, uuid: str) -> bool:
        """Return whether offline polling is enabled for a device."""
        return bool(
            self._device_config.get(uuid, {}).get(
                DEV_CONF_OFFLINE_POLL, DEFAULT_OFFLINE_POLL_ENABLED
            )
        )

    def device_on_network(self, uuid: str) -> bool | None:
        """Return the last ping result for a device (None if unknown/no IP)."""
        return self._on_network.get(uuid)

    def device_cloud_online(self, uuid: str) -> bool:
        """Return whether the device is currently reporting to the cloud."""
        return self._cloud_online.get(uuid, False)

    # ---- Scheduling ----

    def _tick_seconds(self) -> int:
        """Return the coordinator tick: the fastest currently-needed rate."""
        tick = self._effective_devices_interval()
        if self._enable_drive:
            tick = min(tick, self._drive_interval)
        return tick

    def _effective_devices_interval(self) -> int:
        """Compute the devices.json rate from per-device state.

        devices.json is a single shared call, so we poll at the fastest rate
        any device currently needs:
          * offline polling off (or no IP)  -> normal devices interval
          * feature on, cloud online         -> normal devices interval
          * feature on, offline, on-network  -> fast offline interval
          * feature on, offline, off-network -> idle backoff
        """
        known = set(self._cloud_online) | set(self._device_config)
        if not known:
            return self._devices_interval

        desired: list[int] = []
        for uuid in known:
            if not self.device_offline_poll(uuid) or not self.device_ip(uuid):
                desired.append(self._devices_interval)
                continue
            if self._cloud_online.get(uuid, False):
                desired.append(self._devices_interval)
                continue
            on_net = self._on_network.get(uuid)
            if on_net:
                desired.append(self._offline_interval)
            elif on_net is False:
                desired.append(IDLE_INTERVAL)
            else:  # unknown yet -> stay at normal rate
                desired.append(self._devices_interval)

        return min(desired)

    # ---- Options plumbing ----

    def _read_options(self) -> None:
        """Load live settings from the config entry options/data."""
        options = {**self.config_entry.data, **self.config_entry.options}

        self._enable_drive = bool(
            options.get(CONF_ENABLE_DRIVE, DEFAULT_ENABLE_DRIVE)
        )
        self._devices_interval = int(
            options.get(
                CONF_DEVICES_INTERVAL,
                options.get(CONF_POLLING_INTERVAL, DEFAULT_DEVICES_INTERVAL),
            )
        )
        self._drive_interval = int(
            options.get(CONF_DRIVE_INTERVAL, DEFAULT_DRIVE_INTERVAL)
        )
        self._offline_interval = int(
            options.get(CONF_OFFLINE_INTERVAL, DEFAULT_OFFLINE_INTERVAL)
        )
        self._enable_diagnostics = bool(
            options.get(CONF_ENABLE_DIAGNOSTICS, DEFAULT_ENABLE_DIAGNOSTICS)
        )
        self._enable_setpoint = bool(
            options.get(CONF_ENABLE_SETPOINT, DEFAULT_ENABLE_SETPOINT)
        )
        self._enabled_entities = dict(options.get(CONF_ENABLED_ENTITIES, {}))
        self._device_config = dict(options.get(CONF_DEVICE_CONFIG, {}))

    async def async_apply_options(self) -> None:
        """Re-read options and apply them live (no full reload)."""
        self._read_options()
        self.update_interval = timedelta(seconds=self._tick_seconds())
        if not self._enable_drive:
            self._last_drive_fetch = None
        # Reconfiguring IPs may add/remove ping targets.
        self._async_schedule_ping()
        await self.async_request_refresh()

    async def async_set_option(self, key: str, value: Any) -> None:
        """Persist a single global option to the config entry."""
        new_options = {**self.config_entry.options, key: value}
        self.hass.config_entries.async_update_entry(
            self.config_entry, options=new_options
        )

    async def async_set_device_option(
        self, uuid: str, key: str, value: Any
    ) -> None:
        """Persist a single per-device option to the config entry."""
        device_config = {
            k: dict(v) for k, v in self.config_entry.options.get(
                CONF_DEVICE_CONFIG, {}
            ).items()
        }
        device_config.setdefault(uuid, {})[key] = value
        new_options = {
            **self.config_entry.options,
            CONF_DEVICE_CONFIG: device_config,
        }
        self.hass.config_entries.async_update_entry(
            self.config_entry, options=new_options
        )

    # ---- Reachability (built-in ping) ----

    @callback
    def async_start_background_tasks(self) -> None:
        """Start the ping loop (called after first refresh)."""
        self._async_schedule_ping()

    def _ping_targets(self) -> dict[str, str]:
        """Return {uuid: ip} for devices that should be pinged.

        A device is pinged when it has an effective IP (cloud-reported or
        manual). The resolved IP prefers the cloud value; manual is fallback.
        """
        targets: dict[str, str] = {}
        known = set(self._cloud_ip) | set(self._device_config)
        for uuid in known:
            ip = self.device_ip(uuid)
            if ip:
                targets[uuid] = ip
        return targets

    @callback
    def _async_schedule_ping(self) -> None:
        """(Re)schedule the periodic ping loop when IPs are available."""
        has_targets = bool(self._ping_targets())
        if has_targets and self._ping_unsub is None:
            self._ping_unsub = async_track_time_interval(
                self.hass,
                self._async_ping_loop,
                timedelta(seconds=PING_INTERVAL_SECONDS),
            )
            # Kick off an immediate ping so state is fresh.
            self.hass.async_create_task(self._async_ping_loop())
        elif not has_targets and self._ping_unsub is not None:
            self._ping_unsub()
            self._ping_unsub = None

    async def _async_ping_host(self, ip: str) -> bool:
        """Return True if the host answers a single ICMP echo."""
        try:
            proc = await asyncio.create_subprocess_exec(
                "ping",
                "-c",
                "1",
                "-W",
                "1",
                ip,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            return await proc.wait() == 0
        except (OSError, ValueError) as err:
            _LOGGER.debug("Ping of %s failed to run: %s", ip, err)
            return False

    async def _async_ping_loop(self, _now: Any = None) -> None:
        """Ping every configured device IP and react to state changes."""
        changed = False
        for uuid, ip in self._ping_targets().items():
            reachable = await self._async_ping_host(ip)
            if self._on_network.get(uuid) != reachable:
                self._on_network[uuid] = reachable
                changed = True
                _LOGGER.debug(
                    "Device %s network reachability: %s (%s)",
                    uuid,
                    reachable,
                    ip,
                )

        if changed:
            # A reachability change may raise or lower the needed poll rate.
            self.update_interval = timedelta(seconds=self._tick_seconds())
            # Update the on-network binary sensors immediately...
            self.async_update_listeners()
            # ...and if a device just came back on-network, poll now ("boom").
            await self.async_request_refresh()

    async def async_shutdown(self) -> None:
        """Cancel background tasks."""
        if self._ping_unsub is not None:
            self._ping_unsub()
            self._ping_unsub = None
        await super().async_shutdown()

    # ---- Write-back (experimental) ----

    async def async_set_drive_setpoint(
        self, device_uuid: str, setpoint: float
    ) -> None:
        """Set the Drive target temperature (auto mode) and refresh."""
        await self.client.set_drive_setpoint(device_uuid, setpoint)
        await self.async_request_refresh()

    async def async_set_drive_speed(
        self, device_uuid: str, percent: float
    ) -> None:
        """Set a fixed Drive fan speed (manual mode) and refresh."""
        await self.client.set_drive_speed(device_uuid, percent)
        await self.async_request_refresh()

    async def async_set_drive_off(self, device_uuid: str) -> None:
        """Turn the Drive fan off and refresh."""
        await self.client.set_drive_off(device_uuid)
        await self.async_request_refresh()

    async def async_set_drive_channel(
        self, device_uuid: str, channel: int
    ) -> None:
        """Set which channel the Drive PID controls and refresh."""
        await self.client.set_drive_channel(device_uuid, channel)
        await self.async_request_refresh()

    # ---- Data fetch ----

    @staticmethod
    def _is_online(device: dict[str, Any]) -> bool:
        """Derive cloud-online status from the freshness of the device data."""
        if device.get("latest_temps"):
            return True

        device_log = device.get("device_log") or {}
        timestamp = normalize_fireboard_timestamp(
            device_log.get("date") or device.get("last_templog")
        )
        if timestamp:
            last_seen = dt_util.parse_datetime(timestamp)
            if last_seen is not None:
                if last_seen.tzinfo is None:
                    last_seen = last_seen.replace(tzinfo=dt_util.UTC)
                return dt_util.utcnow() - last_seen <= ONLINE_THRESHOLD

        return False

    async def _async_update_data(self) -> dict[str, Any]:
        """Refresh whichever endpoints are due, reusing cached data otherwise.

        Raises:
            UpdateFailed: If the update fails.

        """
        now = self.hass.loop.time()
        eff_devices = self._effective_devices_interval()
        devices_due = self._due(self._last_devices_fetch, eff_devices, now)
        drive_due = self._enable_drive and self._due(
            self._last_drive_fetch, self._drive_interval, now
        )

        device_data: dict[str, Any] = dict(self.data or {})

        try:
            if not self.client.auth_token:
                await self.client.authenticate()

            # --- devices.json (one call, refreshes everything but Drive) ---
            if devices_due or not device_data:
                devices = await self.client.get_devices()
                refreshed: dict[str, Any] = {}

                # One-time diagnostic: dump the full device + device_log so we
                # can see every field the API actually returns (e.g. whether an
                # RSSI / signal-strength field exists). Logged once per HA run.
                if not self._logged_device_shape and devices:
                    first = devices[0]
                    _LOGGER.warning(
                        "FireBoard device field dump (one-time): device keys=%s; "
                        "device_log=%r",
                        list(first.keys()) if isinstance(first, dict) else "n/a",
                        (first.get("device_log") if isinstance(first, dict) else None),
                    )
                    self._logged_device_shape = True

                for device in devices:
                    device_uuid = device.get("uuid")
                    if not device_uuid:
                        continue

                    prev = device_data.get(device_uuid, {})
                    online = self._is_online(device)
                    self._cloud_online[device_uuid] = online

                    # Cache the LAN IP the cloud reports (device_log.internalIP)
                    # while the device is online. When offline we keep the last
                    # known value; the manual entry remains the fallback.
                    device_log = device.get("device_log") or {}
                    internal_ip = device_log.get("internalIP")
                    if online and internal_ip:
                        self._cloud_ip[device_uuid] = internal_ip

                    refreshed[device_uuid] = {
                        "device_info": device,
                        "channels": device.get("channels", []),
                        "latest_temps": device.get("latest_temps", []),
                        "device_log": device.get("device_log") or {},
                        "online": online,
                        "drivelog": prev.get("drivelog", {}),
                    }

                device_data = refreshed
                self._last_devices_fetch = now

                # A newly cached cloud IP may enable pinging for the first time.
                self._async_schedule_ping()

                # --- sessions.json (same cadence; one shared call) ---
                # Map the newest active cook session onto each device.
                try:
                    sessions = await self.client.get_sessions()
                    if sessions:
                        _LOGGER.debug(
                            "sessions.json returned %d; first session keys=%s "
                            "sample=%r",
                            len(sessions),
                            list(sessions[0].keys())
                            if isinstance(sessions[0], dict)
                            else "n/a",
                            sessions[0],
                        )
                    active = self._active_sessions_by_device(sessions, devices)
                    by_device = self._sessions_by_device(sessions, devices)
                    for device_uuid, entry in device_data.items():
                        entry["session"] = active.get(device_uuid, {})
                        dev_sessions = by_device.get(device_uuid, [])
                        entry["session_count"] = len(dev_sessions)
                        entry["last_session"] = dev_sessions[0] if dev_sessions else {}
                except FireBoardApiClientError as err:
                    _LOGGER.debug("Could not fetch sessions: %s", err)
                    for entry in device_data.values():
                        entry.setdefault("session", {})
                        entry.setdefault("session_count", 0)
                        entry.setdefault("last_session", {})

                # Cloud-online changes can alter the needed rate.
                self.update_interval = timedelta(seconds=self._tick_seconds())
                _LOGGER.debug(
                    "Refreshed devices.json (%d devices, next tick %ss)",
                    len(refreshed),
                    self._tick_seconds(),
                )

            # --- drivelog.json (one call per online device) ---
            if drive_due:
                for device_uuid, entry in device_data.items():
                    if not entry.get("online"):
                        continue
                    try:
                        entry["drivelog"] = await self.client.get_drivelog(
                            device_uuid
                        )
                    except FireBoardApiClientError as err:
                        _LOGGER.debug(
                            "No drivelog for device %s: %s", device_uuid, err
                        )
                self._last_drive_fetch = now
                _LOGGER.debug("Refreshed drivelog.json")

            self.update_state = "ok"
            self.update_error = None
            return device_data

        except FireBoardApiClientAuthenticationError as err:
            # Bad/expired credentials -> trigger HA's reauth flow.
            _LOGGER.warning("Authentication failed: %s", err)
            self.update_state = "error"
            self.update_error = str(err)
            raise ConfigEntryAuthFailed(str(err)) from err
        except FireBoardApiClientRateLimitError as err:
            _LOGGER.error("Rate limit exceeded: %s", err)
            self.update_state = "rate_limited"
            self.update_error = str(err)
            # Keep cached data available; surface the blip without going
            # unavailable if we already have data.
            if self.data:
                return dict(self.data)
            raise UpdateFailed(f"Rate limit exceeded: {err}") from err
        except FireBoardApiClientCommunicationError as err:
            _LOGGER.error("Communication error: %s", err)
            self.update_state = "error"
            self.update_error = str(err)
            if self.data:
                return dict(self.data)
            raise UpdateFailed(f"Communication error: {err}") from err
        except Exception as err:
            _LOGGER.error("Unexpected error: %s", err)
            self.update_state = "error"
            self.update_error = str(err)
            raise UpdateFailed(f"Unexpected error: {err}") from err

    @staticmethod
    def _active_sessions_by_device(
        sessions: list[dict[str, Any]],
        devices: list[dict[str, Any]],
    ) -> dict[str, dict[str, Any]]:
        """Return {device_uuid: newest active session} for active cooks.

        A session is active when it has no end time (``end_time`` / ``end``
        null). Sessions link to devices either by numeric id list
        (``device_ids`` / ``devices``) or by device UUIDs; we accept both and
        translate ids -> uuid. When several active sessions touch one device the
        newest by ``start_time`` (falling back to ``created``) wins.
        """
        # Map both numeric id and uuid -> uuid so either linkage form works.
        id_to_uuid: dict[Any, str] = {}
        known_uuids: set[str] = set()
        for device in devices:
            if not isinstance(device, dict):
                continue
            uuid = device.get("uuid")
            if uuid is None:
                continue
            known_uuids.add(uuid)
            id_to_uuid[uuid] = uuid
            if device.get("id") is not None:
                id_to_uuid[device["id"]] = uuid

        def started_at(session: dict[str, Any]) -> Any:
            return session.get("start_time") or session.get("created") or ""

        def is_active(session: dict[str, Any]) -> bool:
            # Treat missing/null/empty end time as active.
            return not (session.get("end_time") or session.get("end"))

        def device_refs(session: dict[str, Any]) -> list[Any]:
            # Accept several possible linkage fields; each may hold ids,
            # uuids, or nested {"id"/"uuid": ...} dicts.
            refs: list[Any] = []
            for key in ("device_ids", "devices", "device", "device_uuids"):
                val = session.get(key)
                if val is None:
                    continue
                items = val if isinstance(val, list) else [val]
                for item in items:
                    if isinstance(item, dict):
                        refs.append(item.get("uuid") or item.get("id"))
                    else:
                        refs.append(item)
            return refs

        result: dict[str, dict[str, Any]] = {}
        for session in sessions:
            if not isinstance(session, dict) or not is_active(session):
                continue
            refs = device_refs(session)
            # If a session lists no devices but there is exactly one device on
            # the account, attribute it to that device.
            if not refs and len(known_uuids) == 1:
                refs = list(known_uuids)
            for ref in refs:
                uuid = id_to_uuid.get(ref)
                if uuid is None:
                    continue
                current = result.get(uuid)
                if current is None or started_at(session) > started_at(current):
                    result[uuid] = session

        _LOGGER.debug(
            "Session mapping: %d sessions, %d active-matched to devices %s",
            len(sessions),
            len(result),
            list(result),
        )
        return result

    @staticmethod
    def _sessions_by_device(
        sessions: list[dict[str, Any]],
        devices: list[dict[str, Any]],
    ) -> dict[str, list[dict[str, Any]]]:
        """Return {device_uuid: [sessions]} sorted newest-first per device.

        Includes both completed and active sessions, so entities can report a
        session count and the most-recent cook. Uses the same flexible
        device-linkage handling as _active_sessions_by_device.
        """
        id_to_uuid: dict[Any, str] = {}
        known_uuids: set[str] = set()
        for device in devices:
            if not isinstance(device, dict):
                continue
            uuid = device.get("uuid")
            if uuid is None:
                continue
            known_uuids.add(uuid)
            id_to_uuid[uuid] = uuid
            if device.get("id") is not None:
                id_to_uuid[device["id"]] = uuid

        def started_at(session: dict[str, Any]) -> Any:
            return session.get("start_time") or session.get("created") or ""

        def device_refs(session: dict[str, Any]) -> list[Any]:
            refs: list[Any] = []
            for key in ("device_ids", "devices", "device", "device_uuids"):
                val = session.get(key)
                if val is None:
                    continue
                items = val if isinstance(val, list) else [val]
                for item in items:
                    if isinstance(item, dict):
                        refs.append(item.get("uuid") or item.get("id"))
                    else:
                        refs.append(item)
            return refs

        result: dict[str, list[dict[str, Any]]] = {}
        for session in sessions:
            if not isinstance(session, dict):
                continue
            refs = device_refs(session)
            if not refs and len(known_uuids) == 1:
                refs = list(known_uuids)
            for ref in refs:
                uuid = id_to_uuid.get(ref)
                if uuid is None:
                    continue
                result.setdefault(uuid, []).append(session)

        for uuid in result:
            result[uuid].sort(key=started_at, reverse=True)
        return result

    def _due(self, last: float | None, interval: int, now: float) -> bool:
        """Return True if ``interval`` seconds have elapsed since ``last``."""
        return last is None or (now - last) >= interval
