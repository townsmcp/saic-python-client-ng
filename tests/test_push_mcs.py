from __future__ import annotations

import asyncio
from functools import partial
from typing import TYPE_CHECKING, Any

import pytest

from saic_ismart_client_ng.push._proto.mcs_pb2 import (
    Close,
    DataMessageStanza,
    HeartbeatAck,
    HeartbeatPing,
    IqStanza,
    LoginRequest,
    LoginResponse,
    SelectiveAck,
)
from saic_ismart_client_ng.push.mcs import (
    _MAX_MESSAGE_SIZE,
    _TAG_BY_TYPE,
    MCS_SELECTIVE_ACK_ID,
    McsClient,
    McsMessage,
)

from .fake_mcs import FakeMcsServer, _varint, eventually, make_data

if TYPE_CHECKING:
    from collections.abc import Callable


def _client(
    server: FakeMcsServer, on_message: Callable[[McsMessage], Any], **kwargs: Any
) -> McsClient:
    options: dict[str, Any] = {
        "client_heartbeat_interval": 0.05,
        "dead_after": 0.4,
        "reconnect_min": 0.02,
        "reconnect_max": 0.05,
        "connect": server.connect,
    }
    options.update(kwargs)
    return McsClient(
        "3801226316615669722", "8195473012345678901", on_message, **options
    )


def _ignore(_message: Any) -> None:
    return None


@pytest.mark.asyncio
async def test_login_connects_and_delivers_a_message() -> None:
    received: list[McsMessage] = []
    states: list[bool] = []
    async with FakeMcsServer() as server:
        client = _client(server, received.append, on_connection_state=states.append)
        await client.start()
        await eventually(lambda: client.connected)
        login = server.connections[0].of_type(LoginRequest)[0]
        assert login.auth_token == "8195473012345678901"
        assert login.resource == "3801226316615669722"
        assert login.device_id == f"android-{3801226316615669722:x}"

        await server.connections[0].send(
            make_data("p1", messageType="323", vin="LSJTESTVIN0000001")
        )
        await eventually(lambda: received)
        assert received[0].data == {"messageType": "323", "vin": "LSJTESTVIN0000001"}
        assert received[0].persistent_id == "p1"
        assert received[0].sent_ms == 1795101876212
        assert received[0].ttl == 2419200
        assert client.last_message_at is not None
        await client.stop()
    assert states == [True, False]


@pytest.mark.asyncio
async def test_every_message_is_acknowledged_to_google() -> None:
    async with FakeMcsServer() as server:
        client = _client(server, _ignore)
        await client.start()
        await eventually(lambda: client.connected)
        await server.connections[0].send(make_data("p1", messageType="323"))

        def ack() -> IqStanza | None:
            acks = server.connections[0].of_type(IqStanza)
            return acks[0] if acks else None

        iq = await eventually(ack)
        assert iq.extension.id == MCS_SELECTIVE_ACK_ID
        selective = SelectiveAck()
        selective.ParseFromString(iq.extension.data)
        assert list(selective.id) == ["p1"]
        await client.stop()


@pytest.mark.asyncio
async def test_a_failing_callback_still_acknowledges_and_does_not_stop_delivery() -> (
    None
):
    seen: list[str] = []

    def handler(message: McsMessage) -> None:
        seen.append(message.persistent_id)
        if message.persistent_id == "p1":
            raise RuntimeError("boom")

    async with FakeMcsServer() as server:
        client = _client(server, handler)
        await client.start()
        await eventually(lambda: client.connected)
        await server.connections[0].send(make_data("p1"))
        await server.connections[0].send(make_data("p2"))
        await eventually(lambda: seen == ["p1", "p2"])
        await eventually(lambda: len(server.connections[0].of_type(IqStanza)) == 2)
        await client.stop()


@pytest.mark.asyncio
async def test_received_ids_are_reported_at_the_next_login() -> None:
    async with FakeMcsServer() as server:
        client = _client(server, _ignore)
        await client.start()
        await eventually(lambda: client.connected)
        await server.connections[0].send(make_data("p1"))
        await server.connections[0].send(make_data("p2"))
        await eventually(lambda: client.persistent_ids == ["p1", "p2"])

        await server.connections[0].send(
            Close()
        )  # the server's way to ask for a reconnect
        await eventually(lambda: len(server.connections) == 2)
        second = server.connections[1]
        await eventually(lambda: second.of_type(LoginRequest))
        assert list(second.of_type(LoginRequest)[0].received_persistent_id) == [
            "p1",
            "p2",
        ]
        # Once Google has accepted the login those ids no longer need reporting.
        await eventually(lambda: client.connected and client.persistent_ids == [])
        await client.stop()


@pytest.mark.asyncio
async def test_ids_saved_from_an_earlier_run_are_sent_at_login() -> None:
    async with FakeMcsServer() as server:
        client = _client(server, _ignore, persistent_ids=["old1", "old2"])
        await client.start()
        login = await eventually(
            lambda: server.connections and server.connections[0].of_type(LoginRequest)
        )
        assert list(login[0].received_persistent_id) == ["old1", "old2"]
        await client.stop()


@pytest.mark.asyncio
async def test_server_heartbeat_is_answered() -> None:
    async with FakeMcsServer() as server:
        client = _client(server, _ignore, client_heartbeat_interval=30)
        await client.start()
        await eventually(lambda: client.connected)
        await server.connections[0].send(HeartbeatPing())
        await eventually(lambda: server.connections[0].of_type(HeartbeatAck))
        await client.stop()


@pytest.mark.asyncio
async def test_client_pings_when_the_line_is_quiet() -> None:
    async with FakeMcsServer() as server:
        client = _client(server, _ignore, dead_after=30)
        await client.start()
        await eventually(lambda: client.connected)
        await eventually(lambda: server.connections[0].of_type(HeartbeatPing))
        await client.stop()


@pytest.mark.asyncio
async def test_a_silently_dead_connection_is_dropped_and_reopened() -> None:
    states: list[bool] = []
    async with FakeMcsServer() as server:
        # The server logs the client in and then says nothing, ever again:
        # no heartbeat acks, no data. That is what a half-open socket looks like.
        client = _client(server, _ignore, on_connection_state=states.append)
        await client.start()
        await eventually(lambda: client.connected)
        await eventually(lambda: len(server.connections) >= 2, wait=5)
        assert client.reconnects >= 1
        assert False in states
        await client.stop()


@pytest.mark.asyncio
async def test_a_rejected_login_is_retried_not_treated_as_connected() -> None:
    async with FakeMcsServer(answer_login=False) as server:
        states: list[bool] = []

        async def reject(conn_task_server: FakeMcsServer = server) -> None:
            seen = 0
            while True:
                await asyncio.sleep(0.01)
                for conn in conn_task_server.connections[seen:]:
                    await eventually(partial(conn.of_type, LoginRequest))
                    response = LoginResponse(id="android-0")
                    response.error.code = 5
                    response.error.message = "bad token"
                    await conn.send(response)
                    seen += 1

        rejecter = asyncio.create_task(reject())
        client = _client(server, _ignore, on_connection_state=states.append)
        await client.start()
        await eventually(lambda: len(server.connections) >= 2)
        assert not client.connected
        assert True not in states
        await client.stop()
        rejecter.cancel()


@pytest.mark.asyncio
async def test_stop_closes_everything() -> None:
    async with FakeMcsServer() as server:
        client = _client(server, _ignore)
        await client.start()
        await eventually(lambda: client.connected)
        assert client.is_running()
        await client.stop()
        assert not client.is_running()
        assert not client.connected
        before = len(server.connections)
        await asyncio.sleep(0.2)
        assert len(server.connections) == before  # no reconnect after stop


@pytest.mark.asyncio
async def test_stream_ids_received_are_reported_back_to_the_server() -> None:
    async with FakeMcsServer() as server:
        client = _client(server, _ignore, client_heartbeat_interval=30.0)
        await client.start()
        await eventually(lambda: client.connected)
        conn = server.connections[0]
        # LoginResponse (1), two data messages (2, 3), then a ping (4).
        await conn.send(make_data("p1", messageType="323"))
        await conn.send(make_data("p2", messageType="323"))
        await conn.send(HeartbeatPing())
        await eventually(lambda: conn.of_type(HeartbeatAck))
        assert conn.of_type(HeartbeatAck)[0].last_stream_id_received == 4
        # Nothing new arrived, so a second ping does not repeat the number.
        await conn.send(HeartbeatPing())
        await eventually(lambda: len(conn.of_type(HeartbeatAck)) == 2)
        assert conn.of_type(HeartbeatAck)[1].last_stream_id_received == 5
        await client.stop()


async def _answer_logins(server: FakeMcsServer, rejections: int) -> None:
    """Reject the first ``rejections`` logins, then accept."""
    answered = 0
    while True:
        await asyncio.sleep(0.01)
        for conn in server.connections[answered:]:
            await eventually(partial(conn.of_type, LoginRequest))
            response = LoginResponse(id="android-0")
            if answered < rejections:
                response.error.code = 5
                response.error.message = "bad token"
            await conn.send(response)
            answered += 1


@pytest.mark.asyncio
async def test_login_refusals_are_counted_reported_and_reset_on_success() -> None:
    seen: list[int] = []
    async with FakeMcsServer(answer_login=False) as server:
        answering = asyncio.create_task(_answer_logins(server, rejections=3))
        client = _client(server, _ignore, on_login_rejected=seen.append)
        await client.start()
        await eventually(lambda: client.connected)
        assert seen == [1, 2, 3]
        assert client.login_rejections == 0
        await client.stop()
        answering.cancel()


@pytest.mark.asyncio
async def test_a_message_is_delivered_before_its_id_is_recorded() -> None:
    """The id only counts as received once the acknowledgement has been sent."""
    seen_ids_at_delivery: list[list[str]] = []
    holder: dict[str, McsClient] = {}

    def handler(_message: McsMessage) -> None:
        seen_ids_at_delivery.append(holder["client"].persistent_ids)

    async with FakeMcsServer() as server:
        client = _client(server, handler)
        holder["client"] = client
        await client.start()
        await eventually(lambda: client.connected)
        await server.connections[0].send(make_data("p1", messageType="323"))
        await eventually(lambda: client.persistent_ids == ["p1"])
        assert seen_ids_at_delivery == [[]]
        await client.stop()


@pytest.mark.asyncio
async def test_a_message_is_still_delivered_when_its_acknowledgement_cannot_be_sent() -> (
    None
):
    """A connection that drops before the acknowledgement must not lose the push."""
    delivered: list[McsMessage] = []
    client = McsClient("1", "2", delivered.append)

    async def refuse(_message: Any) -> None:
        raise ConnectionResetError("MCS not connected")

    client._send = refuse  # type: ignore[method-assign]
    with pytest.raises(ConnectionResetError):
        await client._handle_data(make_data("p1", messageType="323"))
    assert [m.persistent_id for m in delivered] == ["p1"]
    # Not recorded, so the next login does not tell Google it was received and
    # Google sends it again.
    assert client.persistent_ids == []


async def _send_raw_and_expect_a_new_connection(raw: bytes) -> None:
    async with FakeMcsServer() as server:
        # Heartbeat and dead-connection timers are far too long to explain a
        # reconnect, so only the malformed-stream handling can.
        client = _client(
            server, _ignore, client_heartbeat_interval=30.0, dead_after=300.0
        )
        await client.start()
        await eventually(lambda: client.connected)
        tag = _TAG_BY_TYPE[DataMessageStanza]
        server.connections[0].writer.write(bytes([tag]) + raw)
        await server.connections[0].writer.drain()
        await eventually(lambda: len(server.connections) == 2)
        await eventually(lambda: client.connected)
        await client.stop()


@pytest.mark.asyncio
async def test_an_absurd_message_size_makes_the_client_reconnect() -> None:
    await _send_raw_and_expect_a_new_connection(_varint(_MAX_MESSAGE_SIZE + 1))


@pytest.mark.asyncio
async def test_an_endless_length_prefix_makes_the_client_reconnect() -> None:
    await _send_raw_and_expect_a_new_connection(b"\xff" * 6)


@pytest.mark.asyncio
async def test_a_message_at_the_size_limit_is_still_read() -> None:
    received: list[McsMessage] = []
    async with FakeMcsServer() as server:
        client = _client(server, received.append)
        await client.start()
        await eventually(lambda: client.connected)
        big = make_data("p1", messageType="323", filler="x" * 100_000)
        await server.connections[0].send(big)
        await eventually(lambda: received)
        assert len(received[0].data["filler"]) == 100_000
        await client.stop()
