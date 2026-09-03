"""FireBoard API client with session cookie support."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import aiohttp
import async_timeout

from .const import API_BASE_URL, API_TIMEOUT

_LOGGER = logging.getLogger(__name__)


class FireBoardApiClientError(Exception):
    """Base exception for FireBoard API errors."""


class FireBoardApiClientAuthenticationError(FireBoardApiClientError):
    """Exception for authentication errors."""


class FireBoardApiClientCommunicationError(FireBoardApiClientError):
    """Exception for communication errors."""


class FireBoardApiClientRateLimitError(FireBoardApiClientError):
    """Exception for rate limit errors."""


class FireBoardApiClient:
    """FireBoard API client with session cookie support."""

    def __init__(
        self,
        email: str,
        password: str,
        session: aiohttp.ClientSession,
    ) -> None:
        """Initialize the API client.

        Args:
            email: FireBoard account email
            password: FireBoard account password
            session: aiohttp client session

        """
        self._email = email
        self._password = password
        self._session = session
        self._token: str | None = None
        self._base_url = API_BASE_URL
        self._cookie_jar: aiohttp.CookieJar | None = None
        self._csrf_token: str | None = None

    async def authenticate(self) -> bool:
        """Authenticate with the FireBoard API and capture session cookies.

        Returns:
            True if authentication was successful

        Raises:
            FireBoardApiClientAuthenticationError: If authentication fails
            FireBoardApiClientCommunicationError: If communication fails

        """
        try:
            async with async_timeout.timeout(API_TIMEOUT):
                # Auth endpoint is at /api/rest-auth/login/ (not /api/v1/)
                auth_url = self._base_url.replace("/v1", "") + "/rest-auth/login/"
                response = await self._session.post(
                    auth_url,
                    json={
                        "username": self._email,
                        "password": self._password,
                    },
                    headers={
                        "Content-Type": "application/json",
                        "User-Agent": "HomeAssistant-FireBoard-Integration",
                    },
                )

                if response.status == 401:
                    raise FireBoardApiClientAuthenticationError(
                        "Invalid email or password"
                    )

                if response.status == 429:
                    raise FireBoardApiClientRateLimitError(
                        "Rate limit exceeded. Please wait before trying again."
                    )

                response.raise_for_status()
                data = await response.json()

                # Store the authentication token for REST requests
                self._token = (
                    data.get("key") or data.get("auth_token") or data.get("token")
                )

                if not self._token:
                    raise FireBoardApiClientAuthenticationError(
                        "No authentication token returned"
                    )

                # Store the cookie jar for subsequent requests
                self._cookie_jar = self._session.cookie_jar

                # Extract CSRF token from cookies
                for cookie in self._cookie_jar:
                    if cookie.key == "csrftoken":
                        self._csrf_token = cookie.value
                        break

                # Debug: Check what cookies we have
                cookies = [
                    f"{cookie.key}={cookie.value}"
                    for cookie in self._cookie_jar
                ]
                _LOGGER.debug(
                    "Successfully authenticated with FireBoard API. "
                    "Cookies: %s, CSRF: %s",
                    ", ".join(cookies) if cookies else "None",
                    self._csrf_token
                )
                return True

        except aiohttp.ClientError as err:
            raise FireBoardApiClientCommunicationError(
                f"Error communicating with API: {err}"
            ) from err
        except asyncio.TimeoutError as err:
            raise FireBoardApiClientCommunicationError(
                "Timeout communicating with API"
            ) from err

    async def _request(
        self,
        method: str,
        endpoint: str,
        **kwargs: Any,
    ) -> dict[str, Any] | list[dict[str, Any]]:
        """Make an authenticated API request with session cookies.

        Args:
            method: HTTP method (GET, POST, etc.)
            endpoint: API endpoint (without base URL)
            **kwargs: Additional arguments to pass to the request

        Returns:
            API response as dictionary or list

        Raises:
            FireBoardApiClientAuthenticationError: If not authenticated
            FireBoardApiClientCommunicationError: If communication fails

        """
        if not self._token or not self._cookie_jar:
            raise FireBoardApiClientAuthenticationError("Not authenticated")

        headers = kwargs.pop("headers", {})
        headers["Authorization"] = f"Token {self._token}"
        headers["Content-Type"] = "application/json"
        headers["User-Agent"] = "HomeAssistant-FireBoard-Integration"
        headers["Referer"] = "https://fireboard.io/"
        headers["Origin"] = "https://fireboard.io"

        # Include CSRF token if we have it
        if self._csrf_token:
            headers["X-CSRFToken"] = self._csrf_token

        try:
            async with async_timeout.timeout(API_TIMEOUT):
                url = f"{self._base_url}/{endpoint}"
                _LOGGER.debug(
                    "Making %s request to %s with %d cookies",
                    method,
                    url,
                    len(list(self._cookie_jar))
                )
                response = await self._session.request(
                    method,
                    url,
                    headers=headers,
                    **kwargs,
                )

                if response.status == 401:
                    # Token expired, try to re-authenticate once
                    _LOGGER.debug("Token expired, re-authenticating...")
                    await self.authenticate()
                    # Retry the request with new token and CSRF
                    headers["Authorization"] = f"Token {self._token}"
                    if self._csrf_token:
                        headers["X-CSRFToken"] = self._csrf_token
                    response = await self._session.request(
                        method,
                        url,
                        headers=headers,
                        **kwargs,
                    )

                    # Still unauthorized after re-auth -> credentials are bad;
                    # surface as an auth error so HA can start a reauth flow.
                    if response.status == 401:
                        raise FireBoardApiClientAuthenticationError(
                            "Authentication failed after re-authentication"
                        )

                if response.status == 429:
                    raise FireBoardApiClientRateLimitError("Rate limit exceeded")

                response.raise_for_status()

                # The API sometimes returns a non-JSON body (e.g. an empty or
                # text response on writes). Don't fail a successful request just
                # because the reply isn't JSON.
                try:
                    return await response.json(content_type=None)
                except (aiohttp.ContentTypeError, ValueError):
                    text = await response.text()
                    _LOGGER.warning(
                        "Non-JSON response from %s (status %s): %r",
                        url,
                        response.status,
                        text[:300],
                    )
                    return {}

        except aiohttp.ClientError as err:
            raise FireBoardApiClientCommunicationError(
                f"Error communicating with API: {err}"
            ) from err
        except asyncio.TimeoutError as err:
            raise FireBoardApiClientCommunicationError(
                "Timeout communicating with API"
            ) from err

    async def get_devices(self) -> list[dict[str, Any]]:
        """Get all devices for the authenticated account.

        Returns:
            List of device dictionaries with channels and configuration

        Raises:
            FireBoardApiClientError: If request fails

        """
        data = await self._request("GET", "devices.json")
        return data if isinstance(data, list) else []

    async def get_sessions(self) -> list[dict[str, Any]]:
        """Get cook sessions for the authenticated account.

        Returns:
            List of session dicts. Each has ``id``, ``title``, ``start_time``,
            ``end_time`` (null while a cook is active), ``created`` and
            ``device_ids`` (list linking the session to device DB ids).

        Raises:
            FireBoardApiClientError: If request fails

        """
        data = await self._request("GET", "sessions.json")
        return data if isinstance(data, list) else []

    async def get_drivelog(self, device_uuid: str) -> dict[str, Any]:
        """Get the real-time FireBoard Drive log for a device.

        The API returns an empty object ``{}`` when no Drive data is available
        (e.g. no Drive attached, or the last reading is older than a minute).

        Args:
            device_uuid: Device UUID

        Returns:
            Drivelog dictionary, or an empty dict if no data is available.

        Raises:
            FireBoardApiClientError: If request fails

        """
        result = await self._request("GET", f"devices/{device_uuid}/drivelog.json")
        return result if isinstance(result, dict) else {}

    async def get_device(self, device_uuid: str) -> dict[str, Any]:
        """Get a specific device by UUID.

        Args:
            device_uuid: Device UUID

        Returns:
            Device dictionary with current data

        Raises:
            FireBoardApiClientError: If request fails

        """
        result = await self._request("GET", f"devices/{device_uuid}.json")
        return result if isinstance(result, dict) else {}

    async def _drive_control(
        self, device_uuid: str, control: dict[str, Any]
    ) -> dict[str, Any]:
        """Send a Drive control request (EXPERIMENTAL, undocumented endpoint).

        Wraps ``control`` in the MQTT-style envelope FireBoard's control path
        expects and POSTs to ``/v1/devices/{uuid}/mq.json``. This controls
        physical Drive hardware, so callers are all opt-in/disabled-by-default.

        Args:
            device_uuid: Device UUID.
            control: The control fields (e.g. {"t": "fan", "setpoint": "240"}).

        Returns:
            The API response dict (usually empty even on success).

        Raises:
            FireBoardApiClientError: If the request fails.

        """
        payload = {
            "topic": "device",
            "payload": {"request_type": "control", **control},
        }
        result = await self._request(
            "POST",
            f"devices/{device_uuid}/mq.json",
            json=payload,
        )
        _LOGGER.warning(
            "drive_control(%s, %s) via mq.json raw response: %r",
            device_uuid,
            control,
            result,
        )
        return result if isinstance(result, dict) else {}

    @staticmethod
    def _num_str(value: float) -> str:
        """Format a number as a string without a trailing '.0'."""
        return str(int(value)) if float(value).is_integer() else str(value)

    async def set_drive_setpoint(
        self, device_uuid: str, setpoint: float
    ) -> dict[str, Any]:
        """Set the Drive target temperature (auto mode). EXPERIMENTAL."""
        return await self._drive_control(
            device_uuid, {"t": "fan", "setpoint": self._num_str(setpoint)}
        )

    async def set_drive_speed(
        self, device_uuid: str, percent: float
    ) -> dict[str, Any]:
        """Set a fixed Drive fan speed (manual mode). EXPERIMENTAL.

        The API expects the fan power as a 0..1 fraction under key ``p``
        (e.g. 45% -> "0.45").
        """
        fraction = max(0.0, min(100.0, float(percent))) / 100.0
        return await self._drive_control(
            device_uuid, {"t": "fan", "p": self._num_str(round(fraction, 4))}
        )

    async def set_drive_off(self, device_uuid: str) -> dict[str, Any]:
        """Turn the Drive fan off (setpoint 0). EXPERIMENTAL."""
        return await self._drive_control(
            device_uuid, {"t": "fan", "setpoint": "0"}
        )

    async def set_drive_channel(
        self, device_uuid: str, channel: int
    ) -> dict[str, Any]:
        """Set which channel the Drive PID controls (``cc``). EXPERIMENTAL."""
        return await self._drive_control(
            device_uuid, {"cc": str(int(channel))}
        )

    @property
    def auth_token(self) -> str | None:
        """Return the authentication token."""
        return self._token
