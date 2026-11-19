from __future__ import annotations

from urllib.parse import parse_qs

import httpx
import pytest

from saic_ismart_client_ng.push._constants import (
    FCM_ANDROID_CERT_SHA1,
    FCM_ANDROID_PACKAGE,
    FCM_SENDER_ID,
)
from saic_ismart_client_ng.push._proto.checkin_pb2 import (
    AndroidCheckinResponse,
)
from saic_ismart_client_ng.push.register import (
    PushCredentials,
    PushRegistrationError,
    register,
    unregister,
)


def _checkin_reply() -> bytes:
    reply = AndroidCheckinResponse()
    reply.stats_ok = True
    reply.android_id = 3801226316615669722
    reply.security_token = 8195473012345678901
    return bytes(reply.SerializeToString())


class FakeGoogle:
    """Just enough of check-in, Firebase Installations and register3."""

    def __init__(self, *, register_reply: str = "token=fuf5yRcj:APA91bTOKEN") -> None:
        self.requests: list[httpx.Request] = []
        self.register_reply = register_reply

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        url = str(request.url)
        if url.endswith("/checkin"):
            return httpx.Response(200, content=_checkin_reply())
        if url.endswith("/installations"):
            return httpx.Response(
                200, json={"fid": "fid123", "refreshToken": "refresh123"}
            )
        if url.endswith("authTokens:generate"):
            return httpx.Response(
                200, json={"token": "fisauth123", "expiresIn": "604800s"}
            )
        if url.endswith("/register3"):
            return httpx.Response(200, text=self.register_reply)
        if request.method == "DELETE":
            return httpx.Response(200, json={})
        return httpx.Response(404)

    def request_to(self, suffix: str) -> httpx.Request:
        return next(r for r in self.requests if str(r.url).endswith(suffix))


def _client(google: FakeGoogle) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(google))


@pytest.mark.asyncio
async def test_register_runs_the_whole_flow_and_returns_credentials() -> None:
    google = FakeGoogle()
    async with _client(google) as client:
        credentials = await register(client)
    assert credentials.android_id == "3801226316615669722"
    assert credentials.security_token == "8195473012345678901"
    assert credentials.token == "fuf5yRcj:APA91bTOKEN"
    assert credentials.fid == "fid123"
    assert credentials.refresh_token == "refresh123"
    assert [str(r.url).rsplit("/", 1)[-1] for r in google.requests] == [
        "checkin",
        "installations",
        "authTokens:generate",
        "register3",
    ]


@pytest.mark.asyncio
async def test_register3_carries_the_routing_fields() -> None:
    google = FakeGoogle()
    async with _client(google) as client:
        await register(client)
    request = google.request_to("/register3")
    body = {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
    # The sender id, not the package name, is what makes the token routable.
    assert body["X-subtype"] == FCM_SENDER_ID
    assert body["subtype"] == FCM_SENDER_ID
    assert body["sender"] == FCM_SENDER_ID
    assert body["app"] == FCM_ANDROID_PACKAGE
    assert body["cert"] == FCM_ANDROID_CERT_SHA1
    assert body["Goog-Firebase-Installations-Auth"] == "fisauth123"
    assert body["X-Goog-Firebase-Installations-Auth"] == "fisauth123"
    assert body["appid"] == "fid123"
    assert request.headers["Authorization"] == (
        "AidLogin 3801226316615669722:8195473012345678901"
    )
    assert request.headers["X-Android-Cert"] == FCM_ANDROID_CERT_SHA1


@pytest.mark.asyncio
async def test_register_fails_cleanly_when_register3_refuses() -> None:
    google = FakeGoogle(register_reply="Error=PHONE_REGISTRATION_ERROR")
    async with _client(google) as client:
        with pytest.raises(PushRegistrationError, match="register3"):
            await register(client)


@pytest.mark.asyncio
async def test_register_fails_cleanly_when_google_is_unreachable() -> None:
    def offline(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no network", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(offline)) as client:
        with pytest.raises(PushRegistrationError, match="check-in"):
            await register(client)


def test_credentials_round_trip_and_hide_secrets() -> None:
    credentials = PushCredentials(
        android_id="1",
        security_token="SECRET-TOKEN",
        token="abcdefghijklmnop",
        fid="f",
        refresh_token="R",
    )
    assert PushCredentials.from_dict(credentials.as_dict()) == credentials
    assert "SECRET-TOKEN" not in repr(credentials)
    with pytest.raises(PushRegistrationError):
        PushCredentials.from_dict({"android_id": "1"})


@pytest.mark.asyncio
async def test_unregister_deletes_the_installation() -> None:
    google = FakeGoogle()
    credentials = PushCredentials(
        "1", "2", "tok", fid="fid123", refresh_token="refresh123"
    )
    async with _client(google) as client:
        assert await unregister(client, credentials) is True
    request = google.requests[-1]
    assert request.method == "DELETE"
    assert request.headers["Authorization"] == "FIS_v2 refresh123"


@pytest.mark.asyncio
async def test_unregister_never_raises() -> None:
    def offline(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no network", request=request)

    credentials = PushCredentials("1", "2", "tok", fid="f", refresh_token="r")
    async with httpx.AsyncClient(transport=httpx.MockTransport(offline)) as client:
        assert await unregister(client, credentials) is False
