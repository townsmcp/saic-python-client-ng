"""Register as an Android device with Google and obtain a routable FCM token.

The flow is the one the iSmart app performs through the Firebase SDK:

1. GCM check-in -> ``android_id`` + ``security_token`` (the device identity).
2. Firebase Installations: create an installation, then generate a fresh auth
   token from its refresh token.
3. ``c2dm/register3`` with the full Firebase field set -> the FCM token.

Skipping step 2, or sending the package name instead of the sender id as
``X-subtype``, yields a token that SAIC accepts but that never receives pushes.

The structure follows ``pymazda/push/_register.py`` from
https://github.com/fano0001/home-assistant-mazda (Apache-2.0, see NOTICE),
rewritten for httpx and for SAIC's Firebase project.
"""

from __future__ import annotations

import base64
from dataclasses import asdict, dataclass
import hashlib
import logging
from typing import Any

import httpx

from ._constants import (
    FCM_ANDROID_CERT_SHA1,
    FCM_ANDROID_PACKAGE,
    FCM_API_KEY,
    FCM_APP_ID,
    FCM_APP_VERSION_CODE,
    FCM_APP_VERSION_NAME,
    FCM_CLIENT_LIBRARY,
    FCM_GMSV,
    FCM_OSV,
    FCM_PLATFORM,
    FCM_SENDER_ID,
    FCM_TARGET_VER,
    FIREBASE_APP_NAME,
    FIS_AUTH_TOKEN_URL,
    FIS_DELETE_URL,
    FIS_INSTALLATIONS_URL,
    FIS_SDK_VERSION,
    GCM_CHECKIN_URL,
    GCM_REGISTER_URL,
)
from ._proto.android_checkin_pb2 import (  # pylint: disable=no-name-in-module
    DEVICE_ANDROID_OS,
    AndroidCheckinProto,
)
from ._proto.checkin_pb2 import (  # pylint: disable=no-name-in-module
    AndroidCheckinRequest,
    AndroidCheckinResponse,
)

logger = logging.getLogger(__name__)

_TIMEOUT = httpx.Timeout(10.0)
_ATTEMPTS = 3


class PushRegistrationError(Exception):
    """Registering with Google (check-in, Firebase or register3) failed."""


@dataclass(frozen=True)
class PushCredentials:
    """Everything needed to receive pushes again later, without registering."""

    android_id: str
    security_token: str
    token: str
    fid: str | None = None
    refresh_token: str | None = None

    def as_dict(self) -> dict[str, str | None]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PushCredentials:
        try:
            return cls(
                android_id=str(data["android_id"]),
                security_token=str(data["security_token"]),
                token=str(data["token"]),
                fid=data.get("fid"),
                refresh_token=data.get("refresh_token"),
            )
        except (KeyError, TypeError) as ex:
            msg = f"Invalid saved credentials: {ex}"
            raise PushRegistrationError(msg) from ex

    def __repr__(self) -> str:
        # Never put the secrets in a log line by accident.
        return (
            f"PushCredentials(android_id={self.android_id!r}, "
            f"token={self.token[:12]}..., security_token=<hidden>)"
        )


def _firebase_app_name_hash() -> str:
    digest = hashlib.sha1(FIREBASE_APP_NAME.encode()).digest()  # noqa: S324
    return base64.urlsafe_b64encode(digest).decode().rstrip("=")


def _android_headers() -> dict[str, str]:
    return {
        "X-Android-Package": FCM_ANDROID_PACKAGE,
        "X-Android-Cert": FCM_ANDROID_CERT_SHA1,
    }


def _checkin_payload(
    android_id: int | None = None, security_token: int | None = None
) -> AndroidCheckinRequest:
    checkin = AndroidCheckinProto()
    checkin.type = DEVICE_ANDROID_OS  # Android, not Chrome: this matters
    request = AndroidCheckinRequest()
    request.user_serial_number = 0
    request.checkin.CopyFrom(checkin)
    request.version = 3
    if android_id and security_token:
        request.id = android_id
        request.security_token = security_token
    return request


async def gcm_check_in(client: httpx.AsyncClient) -> tuple[str, str]:
    """Check in as a new Android device; returns (android_id, security_token)."""
    payload = _checkin_payload().SerializeToString()
    last_error = "no response"
    for attempt in range(1, _ATTEMPTS + 1):
        try:
            resp = await client.post(
                GCM_CHECKIN_URL,
                headers={"Content-Type": "application/x-protobuf"},
                content=payload,
                timeout=_TIMEOUT,
            )
            if resp.status_code == 200:
                parsed = AndroidCheckinResponse()
                parsed.ParseFromString(resp.content)
                if parsed.android_id and parsed.security_token:
                    return str(parsed.android_id), str(parsed.security_token)
                last_error = "response without an android id"
            else:
                last_error = f"HTTP {resp.status_code}"
        except httpx.HTTPError as ex:
            last_error = str(ex) or type(ex).__name__
        logger.debug(
            "GCM check-in attempt %d/%d failed: %s", attempt, _ATTEMPTS, last_error
        )
    msg = f"GCM check-in failed: {last_error}"
    raise PushRegistrationError(msg)


async def _fis_create_installation(client: httpx.AsyncClient) -> dict[str, Any]:
    resp = await client.post(
        FIS_INSTALLATIONS_URL,
        headers={
            "x-goog-api-key": FCM_API_KEY,
            "Content-Type": "application/json",
            **_android_headers(),
        },
        json={
            "appId": FCM_APP_ID,
            "authVersion": "FIS_v2",
            "sdkVersion": FIS_SDK_VERSION,
        },
        timeout=_TIMEOUT,
    )
    if resp.status_code != 200:
        msg = f"Firebase installation failed: HTTP {resp.status_code}"
        raise PushRegistrationError(msg)
    data = resp.json()
    if "fid" not in data or "refreshToken" not in data:
        raise PushRegistrationError("Firebase installation response incomplete")
    result: dict[str, Any] = data
    return result


async def _fis_generate_auth_token(
    client: httpx.AsyncClient, fid: str, refresh_token: str
) -> str:
    resp = await client.post(
        FIS_AUTH_TOKEN_URL.format(fid=fid),
        headers={
            "x-goog-api-key": FCM_API_KEY,
            "Content-Type": "application/json",
            "Authorization": f"FIS_v2 {refresh_token}",
            **_android_headers(),
        },
        json={"installation": {"sdkVersion": FIS_SDK_VERSION}},
        timeout=_TIMEOUT,
    )
    if resp.status_code != 200 or "token" not in (data := resp.json()):
        msg = f"Firebase auth token failed: HTTP {resp.status_code}"
        raise PushRegistrationError(msg)
    return str(data["token"])


def _register_fields(fid: str, auth_token: str) -> dict[str, str]:
    """Build the Firebase field set register3 needs to issue a routable token."""
    common = {
        "appid": fid,
        "gmp_app_id": FCM_APP_ID,
        "Goog-Firebase-Installations-Auth": auth_token,
        "cliv": FCM_CLIENT_LIBRARY,
        "firebase-app-name-hash": _firebase_app_name_hash(),
        "app_ver": FCM_APP_VERSION_CODE,
        "app_ver_name": FCM_APP_VERSION_NAME,
        "osv": FCM_OSV,
        "gmsv": FCM_GMSV,
        "scope": "*",
        # The sender id, NOT the package name: the "differently routed token" trap.
        "subtype": FCM_SENDER_ID,
    }
    return {**common, **{f"X-{key}": value for key, value in common.items()}}


async def _gcm_register(
    client: httpx.AsyncClient,
    android_id: str,
    security_token: str,
    fields: dict[str, str],
) -> str:
    body = {
        "app": FCM_ANDROID_PACKAGE,
        "X-subtype": FCM_SENDER_ID,
        "device": android_id,
        "sender": FCM_SENDER_ID,
        "cert": FCM_ANDROID_CERT_SHA1,
        "gcm_ver": FCM_GMSV,
        "plat": FCM_PLATFORM,
        "app_ver": FCM_APP_VERSION_CODE,
        "app_ver_name": FCM_APP_VERSION_NAME,
        "osv": FCM_OSV,
        "target_ver": FCM_TARGET_VER,
        **fields,
    }
    last_error = "no response"
    for attempt in range(1, _ATTEMPTS + 1):
        try:
            resp = await client.post(
                GCM_REGISTER_URL,
                headers={
                    "Authorization": f"AidLogin {android_id}:{security_token}",
                    "Content-Type": "application/x-www-form-urlencoded",
                    **_android_headers(),
                },
                data=body,
                timeout=_TIMEOUT,
            )
            text = resp.text
            if resp.status_code == 200 and "Error" not in text and "=" in text:
                return text.split("=", 1)[1].strip()
            last_error = f"HTTP {resp.status_code}: {text[:80]}"
        except httpx.HTTPError as ex:
            last_error = str(ex) or type(ex).__name__
        logger.debug(
            "register3 attempt %d/%d failed: %s", attempt, _ATTEMPTS, last_error
        )
    msg = f"register3 failed: {last_error}"
    raise PushRegistrationError(msg)


async def register(client: httpx.AsyncClient) -> PushCredentials:
    """Register a brand-new device identity and return its credentials.

    Always creates a new identity. A token that has been in use for days can
    stop receiving vehicle pushes, so callers re-register on a schedule rather
    than reusing credentials indefinitely.
    """
    android_id, security_token = await gcm_check_in(client)
    try:
        installation = await _fis_create_installation(client)
        fid = str(installation["fid"])
        refresh_token = str(installation["refreshToken"])
        auth_token = await _fis_generate_auth_token(client, fid, refresh_token)
        token = await _gcm_register(
            client, android_id, security_token, _register_fields(fid, auth_token)
        )
    except httpx.HTTPError as ex:
        msg = f"Registration failed: {ex}"
        raise PushRegistrationError(msg) from ex
    credentials = PushCredentials(
        android_id=android_id,
        security_token=security_token,
        token=token,
        fid=fid,
        refresh_token=refresh_token,
    )
    logger.debug("Registered push identity %r", credentials)
    return credentials


async def unregister(client: httpx.AsyncClient, credentials: PushCredentials) -> bool:
    """Best-effort removal of a registration. Never raises; True if confirmed."""
    confirmed = False
    try:
        if credentials.fid and credentials.refresh_token:
            resp = await client.delete(
                FIS_DELETE_URL.format(fid=credentials.fid),
                headers={
                    "Authorization": f"FIS_v2 {credentials.refresh_token}",
                    "x-goog-api-key": FCM_API_KEY,
                    **_android_headers(),
                },
                timeout=_TIMEOUT,
            )
            confirmed = resp.status_code == 200
        else:
            resp = await client.post(
                GCM_REGISTER_URL,
                headers={
                    "Authorization": (
                        f"AidLogin {credentials.android_id}:{credentials.security_token}"
                    ),
                    "Content-Type": "application/x-www-form-urlencoded",
                    **_android_headers(),
                },
                data={
                    "app": FCM_ANDROID_PACKAGE,
                    "X-subtype": FCM_SENDER_ID,
                    "device": credentials.android_id,
                    "sender": FCM_SENDER_ID,
                    "cert": FCM_ANDROID_CERT_SHA1,
                    "delete": "true",
                    "token": credentials.token,
                },
                timeout=_TIMEOUT,
            )
            confirmed = resp.status_code == 200 and "deleted" in resp.text.lower()
    except httpx.HTTPError as ex:
        logger.debug("Push unregistration failed (non-fatal): %s", ex)
    return confirmed
