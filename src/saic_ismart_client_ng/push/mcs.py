"""A persistent MCS connection to Google's mobile connection server.

FCM data messages reach an Android device over a long-lived TLS connection to
``mtalk.google.com:5228``. This client logs in with the device identity from
:mod:`.register`, answers the server's heartbeats, acknowledges every message
(so Google does not deliver it again), notices a connection that has silently
died, and reconnects with a capped back-off.

The framing and stanza handling follow ``pymazda/push/_mcs.py`` from
https://github.com/fano0001/home-assistant-mazda (Apache-2.0, see NOTICE).
Differences: separate send/receive framing state, dead-connection detection,
a back-off that never gives up, and persistent ids that survive a restart.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
import contextlib
from dataclasses import dataclass, field
import logging
import ssl
import struct
import time
from typing import Any

from ._constants import MCS_HOST, MCS_PORT
from ._proto.mcs_pb2 import (  # pylint: disable=no-name-in-module
    Close,
    DataMessageStanza,
    HeartbeatAck,
    HeartbeatPing,
    IqStanza,
    LoginRequest,
    LoginResponse,
    SelectiveAck,
    StreamAck,
    StreamErrorStanza,
)

logger = logging.getLogger(__name__)

MCS_VERSION = 41
MCS_SELECTIVE_ACK_ID = 12

_TAG_BY_TYPE: dict[type, int] = {
    HeartbeatPing: 0,
    HeartbeatAck: 1,
    LoginRequest: 2,
    LoginResponse: 3,
    Close: 4,
    IqStanza: 7,
    DataMessageStanza: 8,
    StreamErrorStanza: 10,
    StreamAck: 11,
}
_TYPE_BY_TAG: dict[int, type] = {tag: cls for cls, tag in _TAG_BY_TYPE.items()}

# How many received persistent ids to remember for the next login. Google only
# needs the ones it has not yet been told we received.
_MAX_PERSISTENT_IDS = 100


@dataclass(frozen=True)
class McsMessage:
    """One FCM data message."""

    data: dict[str, str]
    persistent_id: str
    sent_ms: int | None
    ttl: int | None
    received_at: float = field(default_factory=time.time)


MessageCallback = Callable[[McsMessage], Any]
StateCallback = Callable[[bool], Any]
RejectedCallback = Callable[[int], Any]
Connection = tuple[asyncio.StreamReader, asyncio.StreamWriter]
ConnectionFactory = Callable[[], Awaitable[Connection]]


class _ServerClose(Exception):
    """The server sent Close, which is its normal way of asking for a reconnect."""


async def _default_connect() -> Connection:
    loop = asyncio.get_running_loop()
    context = await loop.run_in_executor(None, ssl.create_default_context)
    return await asyncio.open_connection(MCS_HOST, MCS_PORT, ssl=context)


class McsClient:
    """Keeps an MCS connection open and delivers messages to a callback."""

    def __init__(
        self,
        android_id: str,
        security_token: str,
        on_message: MessageCallback,
        *,
        on_connection_state: StateCallback | None = None,
        on_login_rejected: RejectedCallback | None = None,
        persistent_ids: list[str] | None = None,
        server_heartbeat_interval: int = 10,
        client_heartbeat_interval: float = 20.0,
        dead_after: float = 90.0,
        reconnect_min: float = 3.0,
        reconnect_max: float = 300.0,
        connect: ConnectionFactory | None = None,
    ) -> None:
        self._android_id = android_id
        self._security_token = security_token
        self._on_message = on_message
        self._on_state = on_connection_state
        self._on_rejected = on_login_rejected
        self._persistent_ids: list[str] = list(persistent_ids or [])
        self._server_hb = server_heartbeat_interval
        self._client_hb = client_heartbeat_interval
        self._dead_after = dead_after
        self._reconnect_min = reconnect_min
        self._reconnect_max = reconnect_max
        self._connect = connect or _default_connect
        self._reconnect_delay = reconnect_min

        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._tasks: list[asyncio.Task[None]] = []
        self._running = False
        self._connected = False
        self._sent_first = False
        self._received_first = False
        self._input_stream_id = 0
        self._reported_stream_id = -1
        self._last_inbound: float | None = None
        self.last_connected_at: float | None = None
        self.last_message_at: float | None = None
        self.reconnects = 0
        # Logins in a row that Google answered with an error. A dead network does
        # not count; only an explicit refusal does, which suggests the identity
        # itself is no longer accepted.
        self.login_rejections = 0

    # --- public ---------------------------------------------------------------

    @property
    def connected(self) -> bool:
        return self._connected

    @property
    def persistent_ids(self) -> list[str]:
        """Ids received but not yet confirmed to Google; persist and pass back in."""
        return list(self._persistent_ids)

    def _stopping(self) -> bool:
        return not self._running

    def is_running(self) -> bool:
        return self._running and any(not task.done() for task in self._tasks)

    async def start(self) -> None:
        if self.is_running():
            return
        self._running = True
        self._tasks = [
            asyncio.create_task(self._listen(), name="saic_mcs_listen"),
            asyncio.create_task(self._monitor(), name="saic_mcs_monitor"),
        ]

    async def stop(self) -> None:
        self._running = False
        # Cancel before closing: tearing down TLS can take a while if the peer is
        # unresponsive, and nothing should wait on that.
        for task in self._tasks:
            task.cancel()
        for task in self._tasks:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        self._tasks = []
        await self._close_writer()
        self._set_connected(False)

    # --- state -----------------------------------------------------------------

    def _set_connected(self, value: bool) -> None:
        if value == self._connected:
            return
        self._connected = value
        if value:
            self.last_connected_at = time.time()
        if self._on_state is not None:
            try:
                self._on_state(value)
            except Exception:
                logger.exception("MCS connection-state callback failed")

    def _notify_rejected(self) -> None:
        if self._on_rejected is None:
            return
        try:
            self._on_rejected(self.login_rejections)
        except Exception:
            logger.exception("MCS login-rejected callback failed")

    # --- wire format -----------------------------------------------------------

    @staticmethod
    def _encode_varint(value: int) -> bytes:
        if value == 0:
            return b"\x00"
        out = bytearray()
        while value:
            byte = value & 0x7F
            value >>= 7
            if value:
                byte |= 0x80
            out.append(byte)
        return bytes(out)

    async def _read_varint(self) -> int:
        result = shift = 0
        while True:
            (byte,) = struct.unpack("B", await self._reader.readexactly(1))  # type: ignore[union-attr]
            result |= (byte & 0x7F) << shift
            if not byte & 0x80:
                return int(result)
            shift += 7

    async def _send(self, message: Any) -> None:
        writer = self._writer
        if writer is None:
            raise ConnectionResetError("MCS not connected")
        tag = _TAG_BY_TYPE[type(message)]
        header = bytes([tag]) if self._sent_first else bytes([MCS_VERSION, tag])
        self._sent_first = True
        payload = message.SerializeToString()
        writer.write(header + self._encode_varint(len(payload)) + payload)
        await writer.drain()

    async def _recv(self) -> Any | None:
        reader = self._reader
        if reader is None:
            raise ConnectionResetError("MCS not connected")
        if not self._received_first:
            version, tag = struct.unpack("BB", await reader.readexactly(2))
            if version < MCS_VERSION and version != 38:
                msg = f"Unsupported MCS version {version}"
                raise ConnectionResetError(msg)
            self._received_first = True
        else:
            (tag,) = struct.unpack("B", await reader.readexactly(1))
        size = await self._read_varint()
        body = await reader.readexactly(size)
        message_type = _TYPE_BY_TAG.get(tag)
        if message_type is None:
            logger.debug("MCS: skipping unknown stanza tag %d", tag)
            return None
        message = message_type()
        message.ParseFromString(body)
        return message

    def _next_stream_id(self) -> int | None:
        """Return the stream id to acknowledge, or None if nothing new arrived."""
        if self._reported_stream_id == self._input_stream_id:
            return None
        self._reported_stream_id = self._input_stream_id
        return self._input_stream_id

    # --- connection ------------------------------------------------------------

    async def _login(self) -> None:
        request = LoginRequest()
        request.adaptive_heartbeat = False
        request.auth_service = LoginRequest.ANDROID_ID
        request.auth_token = self._security_token
        request.id = "android-0"
        request.domain = "mcs.android.com"
        request.device_id = f"android-{int(self._android_id):x}"
        request.network_type = 1
        request.resource = self._android_id
        request.user = self._android_id
        request.use_rmq2 = True
        request.setting.add(name="new_vc", value="1")
        request.received_persistent_id.extend(self._persistent_ids)
        if self._server_hb > 0:
            request.heartbeat_stat.ip = ""
            request.heartbeat_stat.timeout = True
            request.heartbeat_stat.interval_ms = 1000 * self._server_hb
        await self._send(request)

    async def _close_writer(self) -> None:
        writer, self._writer = self._writer, None
        self._reader = None
        if writer is None:
            return
        writer.close()
        with contextlib.suppress(Exception):
            async with asyncio.timeout(3):
                await writer.wait_closed()

    async def _handle(self, message: Any) -> None:
        self._last_inbound = time.monotonic()
        self._input_stream_id += 1

        if isinstance(message, LoginResponse):
            if message.HasField("error") and message.error.code != 0:
                self.login_rejections += 1
                self._notify_rejected()
                msg = (
                    f"MCS login rejected: {message.error.code} {message.error.message}"
                )
                raise ConnectionResetError(msg)
            self.login_rejections = 0
            self._persistent_ids = []
            self._reconnect_delay = self._reconnect_min
            self._set_connected(True)
        elif isinstance(message, Close):
            raise _ServerClose
        elif isinstance(message, StreamErrorStanza):
            msg = f"MCS stream error {message.type}: {message.text}"
            raise ConnectionResetError(msg)
        elif isinstance(message, DataMessageStanza):
            await self._handle_data(message)
        elif isinstance(message, HeartbeatPing):
            ack = HeartbeatAck()
            if (stream_id := self._next_stream_id()) is not None:
                ack.last_stream_id_received = stream_id
            await self._send(ack)

    async def _handle_data(self, message: DataMessageStanza) -> None:
        # Acknowledge first, so a failing callback can never make Google resend it.
        if message.persistent_id:
            self._persistent_ids.append(message.persistent_id)
            del self._persistent_ids[:-_MAX_PERSISTENT_IDS]
            ack = IqStanza()
            ack.type = IqStanza.SET
            ack.id = ""
            ack.extension.id = MCS_SELECTIVE_ACK_ID
            selective = SelectiveAck()
            selective.id.extend([message.persistent_id])
            ack.extension.data = selective.SerializeToString()
            await self._send(ack)
        data = {item.key: item.value for item in message.app_data}
        self.last_message_at = time.time()
        parsed = McsMessage(
            data=data,
            persistent_id=message.persistent_id,
            sent_ms=int(message.sent) if message.HasField("sent") else None,
            ttl=int(message.ttl) if message.HasField("ttl") else None,
        )
        try:
            result = self._on_message(parsed)
            if asyncio.iscoroutine(result):
                await result
        except Exception:
            logger.exception("MCS message callback failed")

    async def _listen(self) -> None:
        self._reconnect_delay = self._reconnect_min
        while self._running:
            try:
                self._reader, self._writer = await self._connect()
                self._sent_first = self._received_first = False
                self._input_stream_id = 0
                self._reported_stream_id = -1
                self._last_inbound = time.monotonic()
                await self._login()
                while self._running:
                    message = await self._recv()
                    if message is not None:
                        await self._handle(message)
            except _ServerClose:
                logger.debug("MCS: server asked for a reconnect")
            except (OSError, EOFError, asyncio.IncompleteReadError, ssl.SSLError) as ex:
                logger.debug("MCS: connection lost: %s", ex)
            except Exception:
                logger.exception("MCS: unexpected error")
            finally:
                self._set_connected(False)
                await self._close_writer()
            if self._stopping():
                return
            self.reconnects += 1
            await asyncio.sleep(self._reconnect_delay)
            self._reconnect_delay = min(self._reconnect_delay * 2, self._reconnect_max)

    async def _monitor(self) -> None:
        """Ping when the line is quiet; drop a connection that has gone dead."""
        while self._running:
            await asyncio.sleep(self._client_hb)
            last = self._last_inbound
            if self._writer is None or last is None:
                continue
            quiet = time.monotonic() - last
            if quiet >= self._dead_after:
                logger.debug("MCS: nothing received for %.0fs, reconnecting", quiet)
                self._last_inbound = time.monotonic()
                await self._close_writer()  # makes _recv fail and _listen reconnect
                continue
            if quiet >= self._client_hb:
                try:
                    ping = HeartbeatPing()
                    if (stream_id := self._next_stream_id()) is not None:
                        ping.last_stream_id_received = stream_id
                    await self._send(ping)
                except Exception as ex:
                    logger.debug("MCS: heartbeat failed: %s", ex)
