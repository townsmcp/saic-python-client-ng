from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from saic_ismart_client_ng.push import (
    PushCredentials,
    PushMessage,
    PushRegistrationError,
    PushState,
    SaicPushClient,
)

from .fake_mcs import FakeMcsServer, eventually, make_data
from .test_push_mcs import _answer_logins

CREDS_A = PushCredentials("111", "222", "token-a", fid="fa", refresh_token="ra")
CREDS_B = PushCredentials("333", "444", "token-b", fid="fb", refresh_token="rb")


def _ignore(_message: Any) -> None:
    return None


@pytest.mark.asyncio
async def test_start_registers_when_there_are_no_credentials() -> None:
    async with FakeMcsServer() as server:
        push = SaicPushClient(_ignore, connect=server.connect)
        with patch(
            "saic_ismart_client_ng.push.client.register",
            new=AsyncMock(return_value=CREDS_A),
        ) as register:
            credentials = await push.start()
        register.assert_awaited_once()
        assert credentials == CREDS_A
        assert push.token == "token-a"
        assert push.registered_at is not None
        await eventually(lambda: push.connected)
        assert push.state is PushState.CONNECTED
        await push.stop()
        assert not push.connected


@pytest.mark.asyncio
async def test_saved_credentials_are_reused_without_registering() -> None:
    async with FakeMcsServer() as server:
        push = SaicPushClient(_ignore, credentials=CREDS_A, connect=server.connect)
        with patch(
            "saic_ismart_client_ng.push.client.register", new=AsyncMock()
        ) as register:
            await push.start()
        register.assert_not_awaited()
        assert push.registered_at is None  # loaded, not freshly registered
        await push.stop()


@pytest.mark.asyncio
async def test_messages_arrive_as_typed_push_messages() -> None:
    received: list[PushMessage] = []
    async with FakeMcsServer() as server:
        push = SaicPushClient(
            received.append, credentials=CREDS_A, connect=server.connect
        )
        await push.start()
        await eventually(lambda: push.connected)
        await server.connections[0].send(
            make_data(
                "p1", messageType="323", vin="LSJTESTVIN0000001", title="Vehicle Start"
            )
        )
        await eventually(lambda: received)
        assert received[0].is_vehicle_start
        assert received[0].vin == "LSJTESTVIN0000001"
        assert push.last_push_at is not None
        await push.stop()


@pytest.mark.asyncio
async def test_async_callbacks_are_awaited() -> None:
    seen: list[str | None] = []

    async def handler(message: PushMessage) -> None:
        await asyncio.sleep(0)
        seen.append(message.message_type)

    async with FakeMcsServer() as server:
        push = SaicPushClient(handler, credentials=CREDS_A, connect=server.connect)
        await push.start()
        await eventually(lambda: push.connected)
        await server.connections[0].send(make_data("p1", messageType="328"))
        await eventually(lambda: seen == ["328"])
        await push.stop()


@pytest.mark.asyncio
async def test_renew_switches_identity_and_removes_the_old_one() -> None:
    async with FakeMcsServer() as server:
        push = SaicPushClient(_ignore, credentials=CREDS_A, connect=server.connect)
        await push.start()
        await eventually(lambda: push.connected)
        with (
            patch(
                "saic_ismart_client_ng.push.client.register",
                new=AsyncMock(return_value=CREDS_B),
            ),
            patch(
                "saic_ismart_client_ng.push.client.unregister",
                new=AsyncMock(return_value=True),
            ) as unregister,
        ):
            renewed = await push.renew()
        assert renewed == CREDS_B
        assert push.token == "token-b"
        unregister.assert_awaited_once()
        assert unregister.await_args is not None
        assert unregister.await_args.args[1] == CREDS_A
        await eventually(lambda: push.connected)
        # The new connection logs in as the new identity.
        login = server.connections[-1].received[0]
        assert login.auth_token == "444"
        await push.stop()


@pytest.mark.asyncio
async def test_failed_renew_keeps_the_working_identity_connected() -> None:
    async with FakeMcsServer() as server:
        push = SaicPushClient(_ignore, credentials=CREDS_A, connect=server.connect)
        await push.start()
        await eventually(lambda: push.connected)
        with (
            patch(
                "saic_ismart_client_ng.push.client.register",
                new=AsyncMock(side_effect=PushRegistrationError("offline")),
            ),
            pytest.raises(PushRegistrationError),
        ):
            await push.renew()
        assert push.token == "token-a"
        await eventually(lambda: push.connected)
        await push.stop()


@pytest.mark.asyncio
async def test_start_propagates_a_registration_failure() -> None:
    push = SaicPushClient(_ignore)
    with (
        patch(
            "saic_ismart_client_ng.push.client.register",
            new=AsyncMock(side_effect=PushRegistrationError("no google")),
        ),
        pytest.raises(PushRegistrationError),
    ):
        await push.start()
    assert push.state is PushState.STOPPED
    assert push.credentials is None


@pytest.mark.asyncio
async def test_persistent_ids_survive_stop_for_the_next_start() -> None:
    async with FakeMcsServer() as server:
        push = SaicPushClient(_ignore, credentials=CREDS_A, connect=server.connect)
        await push.start()
        await eventually(lambda: push.connected)
        await server.connections[0].send(make_data("p9"))
        await eventually(lambda: push.persistent_ids == ["p9"])
        await push.stop()
        assert push.persistent_ids == ["p9"]
        await push.start()
        first_login: Any = await eventually(
            lambda: len(server.connections) == 2 and server.connections[1]
        )
        await eventually(lambda: first_login.received)
        assert list(first_login.received[0].received_persistent_id) == ["p9"]
        await push.stop()


@pytest.mark.asyncio
async def test_unregister_removes_the_registration_and_forgets_it() -> None:
    async with FakeMcsServer() as server:
        push = SaicPushClient(_ignore, credentials=CREDS_A, connect=server.connect)
        await push.start()
        with patch(
            "saic_ismart_client_ng.push.client.unregister",
            new=AsyncMock(return_value=True),
        ) as unregister:
            assert await push.unregister() is True
        unregister.assert_awaited_once()
        assert push.credentials is None
        assert push.state is PushState.STOPPED


@pytest.mark.asyncio
async def test_credentials_are_flagged_dead_after_repeated_refusals() -> None:
    fired: list[str] = []
    async with FakeMcsServer(answer_login=False) as server:
        # Never accepts, so the refusals keep coming.
        answering = asyncio.create_task(_answer_logins(server, rejections=1000))
        push = SaicPushClient(
            _ignore,
            credentials=CREDS_A,
            connect=server.connect,
            on_credentials_rejected=lambda: fired.append("rejected"),
            mcs_options={"reconnect_min": 0.01, "reconnect_max": 0.02},
        )
        assert not push.credentials_rejected
        await push.start()
        await eventually(lambda: push.credentials_rejected)
        await asyncio.sleep(0.2)
        assert fired == ["rejected"]  # once, not on every later refusal
        await push.stop()
        answering.cancel()
