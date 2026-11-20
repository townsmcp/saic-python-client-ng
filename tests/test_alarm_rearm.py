from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any
from unittest.mock import patch

import pytest

from saic_ismart_client_ng import SaicApi
from saic_ismart_client_ng.api.vehicle.alarm import (
    AlarmRearmResult,
    AlarmSwitch,
    AlarmSwitchReq,
    AlarmSwitchResp,
    AlarmType,
)
from saic_ismart_client_ng.crypto_utils import sha256_hex_digest
from saic_ismart_client_ng.model import SaicApiConfiguration

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

VIN = "LSJTESTVIN0000001"
FAULT = AlarmType.ALARM_TYPE_VEHICLE_FAULT
START = AlarmType.ALARM_TYPE_VEHICLE_START
GEOFENCE = AlarmType.ALARM_TYPE_GEOFENCE


class FakeAlarmServer:
    """Remembers switch state like SAIC does, and records every request."""

    def __init__(self, *, ignore_off: bool = False, fail_on_off: bool = False) -> None:
        self.state = {t.value: (1, 1) for t in AlarmType}
        self.log: list[tuple[str, int, int, int]] = []
        self.ignore_off = ignore_off
        self.fail_on_off = fail_on_off

    async def put(self, method: str, path: str, **kwargs: Any) -> None:
        assert (method, path) == ("PUT", "/vehicle/alarmSwitch")
        body: AlarmSwitchReq = kwargs["body"]
        assert body.vin == sha256_hex_digest(VIN)
        for switch in body.alarmSwitchList:
            assert switch.alarmType is not None
            assert switch.alarmSwitch is not None
            assert switch.functionSwitch is not None
            self.log.append(
                (
                    "PUT",
                    switch.alarmType,
                    switch.alarmSwitch,
                    switch.functionSwitch,
                )
            )
            if switch.alarmSwitch == 0 and self.fail_on_off:
                msg = "SAIC unavailable"
                raise RuntimeError(msg)
            if switch.alarmSwitch == 0 and self.ignore_off:
                continue
            self.state[switch.alarmType] = (switch.alarmSwitch, switch.functionSwitch)

    async def get(self, vin: str) -> AlarmSwitchResp:
        assert vin == VIN
        self.log.append(("GET", -1, -1, -1))
        return AlarmSwitchResp(
            [
                AlarmSwitch(alarmType=t, alarmSwitch=a, functionSwitch=f)
                for t, (a, f) in self.state.items()
            ]
        )

    def puts(self) -> list[tuple[int, int, int]]:
        return [(t, a, f) for kind, t, a, f in self.log if kind == "PUT"]


@asynccontextmanager
async def _api(server: FakeAlarmServer) -> AsyncIterator[SaicApi]:
    api = SaicApi(SaicApiConfiguration("user@example.com", "password"))
    with (
        patch.object(SaicApi, "execute_api_call_no_result", new=server.put),
        patch.object(SaicApi, "get_alarm_switch", new=server.get),
    ):
        try:
            yield api
        finally:
            await api.close()


@pytest.mark.asyncio
async def test_set_alarm_switches_defaults_to_on_and_can_switch_off() -> None:
    server = FakeAlarmServer()
    async with _api(server) as api:
        await api.set_alarm_switches([FAULT, START], VIN)
        await api.set_alarm_switches([START], VIN, enabled=False)
    assert server.puts() == [(0, 1, 1), (3, 1, 1), (3, 0, 0)]


@pytest.mark.asyncio
async def test_rearm_turns_each_type_off_then_on_like_the_app() -> None:
    server = FakeAlarmServer()
    async with _api(server) as api:
        results = await api.rearm_alarm_switches(VIN, pause=0)
    assert server.puts() == [(0, 0, 0), (0, 1, 1), (3, 0, 0), (3, 1, 1)]
    # One request per type, each followed by a read-back.
    assert [kind for kind, *_ in server.log] == ["PUT", "GET"] * 4
    assert results == [
        AlarmRearmResult(FAULT, off_confirmed=True, on_confirmed=True),
        AlarmRearmResult(START, off_confirmed=True, on_confirmed=True),
    ]
    assert server.state[GEOFENCE.value] == (1, 1)  # never touched


@pytest.mark.asyncio
async def test_rearm_honours_the_chosen_types_and_can_skip_the_read_back() -> None:
    server = FakeAlarmServer()
    async with _api(server) as api:
        results = await api.rearm_alarm_switches(VIN, [GEOFENCE], pause=0, verify=False)
    assert server.puts() == [(2, 0, 0), (2, 1, 1)]
    assert all(kind == "PUT" for kind, *_ in server.log)
    assert results == [
        AlarmRearmResult(GEOFENCE, off_confirmed=False, on_confirmed=False)
    ]


@pytest.mark.asyncio
async def test_rearm_reports_when_the_off_did_not_take() -> None:
    server = FakeAlarmServer(ignore_off=True)
    async with _api(server) as api:
        results = await api.rearm_alarm_switches(VIN, [FAULT], pause=0)
    assert results == [AlarmRearmResult(FAULT, off_confirmed=False, on_confirmed=True)]


@pytest.mark.asyncio
async def test_rearm_always_switches_back_on_when_the_off_request_fails() -> None:
    server = FakeAlarmServer(fail_on_off=True)
    async with _api(server) as api:
        with pytest.raises(RuntimeError, match="unavailable"):
            await api.rearm_alarm_switches(VIN, [FAULT], pause=0)
    assert server.puts() == [(0, 0, 0), (0, 1, 1)]
    assert server.state[FAULT.value] == (1, 1)


@pytest.mark.asyncio
async def test_rearm_cancelled_while_off_still_switches_back_on() -> None:
    server = FakeAlarmServer()
    async with _api(server) as api:
        task = asyncio.create_task(api.rearm_alarm_switches(VIN, [START], pause=60))
        for _ in range(100):
            await asyncio.sleep(0.01)
            if server.state[START.value] == (0, 0):
                break
        assert server.state[START.value] == (0, 0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert server.state[START.value] == (1, 1)
