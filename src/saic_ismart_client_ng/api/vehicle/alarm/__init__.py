from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from saic_ismart_client_ng.api.vehicle import SaicVehicleApi
from saic_ismart_client_ng.api.vehicle.alarm.schema import (
    AlarmRearmResult,
    AlarmSwitch,
    AlarmSwitchReq,
    AlarmSwitchResp,
    AlarmType,
)
from saic_ismart_client_ng.crypto_utils import sha256_hex_digest

if TYPE_CHECKING:
    from collections.abc import Sequence

# The two notifications the app lists as "Vehicle Fault" and "Vehicle Start".
DEFAULT_REARM_TYPES: tuple[AlarmType, ...] = (
    AlarmType.ALARM_TYPE_VEHICLE_FAULT,
    AlarmType.ALARM_TYPE_VEHICLE_START,
)

__all__ = [
    "AlarmRearmResult",
    "AlarmSwitch",
    "AlarmSwitchReq",
    "AlarmSwitchResp",
    "AlarmType",
]


class SaicVehicleAlarmApi(SaicVehicleApi):
    async def get_alarm_switch(self, vin: str) -> AlarmSwitchResp:
        return await self.execute_api_call(
            "GET",
            "/vehicle/alarmSwitch",
            out_type=AlarmSwitchResp,
            params={"vin": sha256_hex_digest(vin)},
        )

    async def set_alarm_switches(
        self,
        alarm_switches: list[AlarmType],
        vin: str,
        *,
        enabled: bool = True,
    ) -> None:
        """Switch the given alarm (notification) types on, or off if ``enabled`` is False.

        Off is what the iSmart app sends when you untick a notification:
        ``alarmSwitch`` and ``functionSwitch`` both 0.
        """
        value = 1 if enabled else 0
        actual_switches = [
            AlarmSwitch(
                alarmType=alarm_type.value, alarmSwitch=value, functionSwitch=value
            )
            for alarm_type in alarm_switches
        ]
        body = AlarmSwitchReq(
            alarmSwitchList=actual_switches, vin=sha256_hex_digest(vin)
        )
        await self.execute_api_call_no_result("PUT", "/vehicle/alarmSwitch", body=body)

    async def rearm_alarm_switches(
        self,
        vin: str,
        alarm_types: Sequence[AlarmType] = DEFAULT_REARM_TYPES,
        *,
        pause: float = 10.0,
        verify: bool = True,
    ) -> list[AlarmRearmResult]:
        """Switch notifications off and back on, as untick-and-tick in the app does.

        SAIC appears to start sending some messages (finished charging, for one)
        only when a notification is switched from off to on, not when it is
        already on. Each type is sent as its own request, like the app does, with
        ``pause`` seconds between off and on. The switch is always turned back on,
        even if something fails in between. With ``verify`` the state is read
        back after each change and reported in the result.
        """
        results: list[AlarmRearmResult] = []
        for alarm_type in alarm_types:
            off_confirmed = on_confirmed = False
            try:
                await self.set_alarm_switches([alarm_type], vin, enabled=False)
                if verify:
                    off_confirmed = not await self._alarm_is_on(vin, alarm_type)
                await asyncio.sleep(pause)
            finally:
                await self.set_alarm_switches([alarm_type], vin, enabled=True)
                if verify:
                    on_confirmed = await self._alarm_is_on(vin, alarm_type)
            results.append(AlarmRearmResult(alarm_type, off_confirmed, on_confirmed))
        return results

    async def _alarm_is_on(self, vin: str, alarm_type: AlarmType) -> bool:
        response = await self.get_alarm_switch(vin)
        return any(
            switch.alarmType == alarm_type.value and switch.alarmSwitch == 1
            for switch in response.alarmSwitchList
        )
