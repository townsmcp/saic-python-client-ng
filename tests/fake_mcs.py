"""A fake MCS server and helpers shared by the push tests."""

from __future__ import annotations

import asyncio
import struct
from typing import TYPE_CHECKING, Any, Self

from saic_ismart_client_ng.push._proto.mcs_pb2 import (
    DataMessageStanza,
    LoginRequest,
    LoginResponse,
)
from saic_ismart_client_ng.push.mcs import _TAG_BY_TYPE, _TYPE_BY_TAG, MCS_VERSION

if TYPE_CHECKING:
    from collections.abc import Callable


def _varint(value: int) -> bytes:
    out = bytearray()
    while True:
        byte = value & 0x7F
        value >>= 7
        if value:
            out.append(byte | 0x80)
        else:
            out.append(byte)
            return bytes(out)


class Connection:
    """The server's side of one client connection."""

    def __init__(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        self.reader = reader
        self.writer = writer
        self.received: list[Any] = []
        self._sent_first = False
        self._got_first = False
        self.task: asyncio.Task[None] | None = None

    async def send(self, message: Any) -> None:
        tag = _TAG_BY_TYPE[type(message)]
        header = bytes([tag]) if self._sent_first else bytes([MCS_VERSION, tag])
        self._sent_first = True
        payload = message.SerializeToString()
        self.writer.write(header + _varint(len(payload)) + payload)
        await self.writer.drain()

    async def read_loop(self) -> None:
        try:
            while True:
                if not self._got_first:
                    _version, tag = struct.unpack(
                        "BB", await self.reader.readexactly(2)
                    )
                    self._got_first = True
                else:
                    (tag,) = struct.unpack("B", await self.reader.readexactly(1))
                size = shift = 0
                while True:
                    (byte,) = struct.unpack("B", await self.reader.readexactly(1))
                    size |= (byte & 0x7F) << shift
                    if not byte & 0x80:
                        break
                    shift += 7
                message = _TYPE_BY_TAG[tag]()
                message.ParseFromString(await self.reader.readexactly(size))
                self.received.append(message)
        except (asyncio.IncompleteReadError, ConnectionError):
            pass

    def of_type(self, cls: type) -> list[Any]:
        return [m for m in self.received if isinstance(m, cls)]


class FakeMcsServer:
    def __init__(self, *, answer_login: bool = True) -> None:
        self.connections: list[Connection] = []
        self.answer_login = answer_login
        self._server: asyncio.Server | None = None

    async def __aenter__(self) -> Self:
        self._server = await asyncio.start_server(self._accept, "127.0.0.1", 0)
        return self

    async def __aexit__(self, *exc: object) -> None:
        assert self._server is not None
        self._server.close()
        for conn in self.connections:
            conn.writer.close()
        await self._server.wait_closed()

    @property
    def port(self) -> int:
        assert self._server is not None
        return int(self._server.sockets[0].getsockname()[1])

    async def connect(self) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
        return await asyncio.open_connection("127.0.0.1", self.port)

    async def _accept(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        conn = Connection(reader, writer)
        self.connections.append(conn)
        conn.task = asyncio.create_task(conn.read_loop())
        if self.answer_login:
            await eventually(lambda: conn.of_type(LoginRequest))
            await conn.send(LoginResponse(id="android-0", jid="x"))
        await conn.task


async def eventually(check: Callable[[], Any], wait: float = 3.0) -> Any:
    deadline = asyncio.get_running_loop().time() + wait
    while True:
        if result := check():
            return result
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError("condition not met in time")
        await asyncio.sleep(0.01)


def make_data(persistent_id: str, **fields: str) -> DataMessageStanza:
    message = DataMessageStanza()
    message.persistent_id = persistent_id
    setattr(message, "from", "50558369982")
    message.category = "com.saicmotor.iov.europe"
    message.sent = 1795101876212
    message.ttl = 2419200
    for key, value in fields.items():
        message.app_data.add(key=key, value=value)
    return message
