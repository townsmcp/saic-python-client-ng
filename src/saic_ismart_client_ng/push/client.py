"""Registration and the MCS connection behind one small interface."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from enum import StrEnum
import logging
import time
from typing import Any

import httpx

from .mcs import ConnectionFactory, McsClient, McsMessage
from .messages import PushMessage
from .register import PushCredentials, PushRegistrationError, register, unregister

logger = logging.getLogger(__name__)

PushCallback = Callable[[PushMessage], Awaitable[None] | None]
StateCallback = Callable[[bool], Any]
RejectedCallback = Callable[[], Any]  # plain function; schedule any async work

# Logins refused in a row before the identity is considered dead. One refusal can
# be a hiccup on Google's side; several in a row mean registering again.
CREDENTIALS_REJECTED_AFTER = 3


class PushState(StrEnum):
    STOPPED = "stopped"
    CONNECTING = "connecting"
    CONNECTED = "connected"


class SaicPushClient:
    """Receives SAIC's real-time pushes for one SAIC account.

    Typical use::

        push = SaicPushClient(on_message=handle, credentials=saved_or_none)
        credentials = await push.start()      # registers if there are none
        config.push_token = credentials.token # then (re-)login so SAIC binds it
        ...
        await push.stop()

    One client serves a whole account: pushes for every vehicle on it arrive on
    the same connection, each carrying its ``vin``.
    """

    def __init__(
        self,
        on_message: PushCallback,
        *,
        credentials: PushCredentials | None = None,
        persistent_ids: list[str] | None = None,
        on_connection_state: StateCallback | None = None,
        on_credentials_rejected: RejectedCallback | None = None,
        http_client: httpx.AsyncClient | None = None,
        connect: ConnectionFactory | None = None,
        mcs_options: Mapping[str, Any] | None = None,
    ) -> None:
        self._on_message = on_message
        self._on_state = on_connection_state
        self._on_credentials_rejected = on_credentials_rejected
        self._credentials = credentials
        self._persistent_ids = list(persistent_ids or [])
        self._http = http_client
        self._owns_http = http_client is None
        self._connect = connect
        # Advanced tuning passed to McsClient (heartbeat and reconnect timings).
        self._mcs_options = dict(mcs_options or {})
        self._mcs: McsClient | None = None
        self._registered_at: float | None = None
        self._last_push_at: float | None = None

    # --- state ------------------------------------------------------------------

    @property
    def credentials(self) -> PushCredentials | None:
        return self._credentials

    @property
    def token(self) -> str | None:
        return self._credentials.token if self._credentials else None

    @property
    def state(self) -> PushState:
        if self._mcs is None or not self._mcs.is_running():
            return PushState.STOPPED
        return PushState.CONNECTED if self._mcs.connected else PushState.CONNECTING

    @property
    def connected(self) -> bool:
        return self.state is PushState.CONNECTED

    @property
    def credentials_rejected(self) -> bool:
        """True once Google has refused the login several times in a row.

        The identity is then unlikely to recover; call :meth:`renew` and log in to
        SAIC again with the new token.
        """
        return (
            self._mcs is not None
            and self._mcs.login_rejections >= CREDENTIALS_REJECTED_AFTER
        )

    @property
    def registered_at(self) -> float | None:
        """Unix time this identity was registered, or None if it was loaded."""
        return self._registered_at

    @property
    def last_push_at(self) -> float | None:
        return self._last_push_at

    @property
    def persistent_ids(self) -> list[str]:
        """Message ids to persist so a restart does not see old pushes again."""
        return self._mcs.persistent_ids if self._mcs else list(self._persistent_ids)

    # --- lifecycle --------------------------------------------------------------

    def _client(self) -> httpx.AsyncClient:
        if self._http is None:
            self._http = httpx.AsyncClient()
        return self._http

    async def register(self) -> PushCredentials:
        """Register a fresh device identity (does not touch a running connection)."""
        self._credentials = await register(self._client())
        self._registered_at = time.time()
        self._persistent_ids = []
        return self._credentials

    async def start(self) -> PushCredentials:
        """Connect, registering first if there are no credentials yet.

        Raises :class:`PushRegistrationError` if registering fails.
        """
        if self._credentials is None:
            await self.register()
        assert self._credentials is not None  # noqa: S101
        if self._mcs is None or not self._mcs.is_running():
            self._mcs = McsClient(
                self._credentials.android_id,
                self._credentials.security_token,
                self._handle,
                on_connection_state=self._on_state,
                on_login_rejected=self._login_rejected,
                persistent_ids=self._persistent_ids,
                connect=self._connect,
                **self._mcs_options,
            )
            await self._mcs.start()
        return self._credentials

    async def stop(self) -> None:
        if self._mcs is not None:
            self._persistent_ids = self._mcs.persistent_ids
            await self._mcs.stop()
            self._mcs = None
        if self._owns_http and self._http is not None:
            await self._http.aclose()
            self._http = None

    async def renew(self) -> PushCredentials:
        """Replace the identity with a fresh one and reconnect under it.

        The caller must log in to SAIC again with the new ``token`` afterwards;
        until then SAIC still routes pushes to the old one. The old registration
        is removed on a best-effort basis.
        """
        old = self._credentials
        had_connection = self._mcs is not None
        if self._mcs is not None:
            await self._mcs.stop()
            self._mcs = None
        try:
            await self.register()
        except PushRegistrationError:
            # Keep the identity we had rather than ending up with nothing.
            self._credentials = old
            if had_connection:
                await self.start()
            raise
        if old is not None:
            await unregister(self._client(), old)
        await self.start()
        assert self._credentials is not None  # noqa: S101
        return self._credentials

    async def unregister(self) -> bool:
        """Stop and remove the registration, e.g. when the integration is deleted."""
        credentials = self._credentials
        await self.stop()
        if credentials is None:
            return False
        async with httpx.AsyncClient() as client:
            confirmed = await unregister(client, credentials)
        self._credentials = None
        return confirmed

    def _login_rejected(self, count: int) -> None:
        # Fire once, on the refusal that crosses the threshold.
        if count == CREDENTIALS_REJECTED_AFTER and self._on_credentials_rejected:
            try:
                self._on_credentials_rejected()
            except Exception:
                logger.exception("Credentials-rejected callback failed")

    # --- messages ---------------------------------------------------------------

    async def _handle(self, message: McsMessage) -> None:
        self._last_push_at = time.time()
        parsed = PushMessage.from_mcs(message)
        result = self._on_message(parsed)
        if asyncio.iscoroutine(result):
            await result
