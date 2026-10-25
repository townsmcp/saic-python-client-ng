"""SAIC's stored copy of the car's status (/vehicle/status/cache).

The reply below is from a capture of the iSmart app opening on an MG S6 EV
(3 Oct 2026), with the position replaced.
"""

from __future__ import annotations

import unittest
from unittest.mock import AsyncMock

import dacite

from saic_ismart_client_ng.api.vehicle import SaicVehicleApi, VehicleStatusCacheResp
from saic_ismart_client_ng.crypto_utils import sha256_hex_digest

APP_REPLY = {
    "basicVehicleStatus": {
        "fuelLevelPrc": 0,
        "clstrDspdFuelLvlSgmt": 7,
        "frontLeftTyrePressure": 66,
        "remoteClimateStatus": 0,
        "rearRightTyrePressure": 66,
        "rmtHtdRrWndSt": 0,
        "frontRightTyrePressure": 66,
        "bonnetStatus": 0,
        "lockStatus": 1,
        "bootStatus": 0,
        "fuelRangeElec": 3500,
        "engineStatus": 0,
        "rearRightWindow": 0,
        "rearLeftTyrePressure": 66,
        "extendedData2": 0,
        "passengerWindow": 0,
        "extendedData1": 72,
        "rearLeftWindow": 0,
        "mileage": 51760,
        "elecRangeStdA": 768,
    },
    "gpsPosition": {
        "wayPoint": {"position": {"altitude": 47, "latitude": 1, "longitude": 2}}
    },
    "statusTime": 1791016937,
    "onlineStatus": 1,
}


class TestVehicleStatusCached(unittest.IsolatedAsyncioTestCase):
    def test_decodes_the_apps_reply(self) -> None:
        reply = dacite.from_dict(VehicleStatusCacheResp, APP_REPLY)
        assert reply.statusTime == 1791016937
        assert reply.onlineStatus == 1
        assert reply.basicVehicleStatus is not None
        assert reply.basicVehicleStatus["extendedData1"] == 72
        assert reply.basicVehicleStatus["mileage"] == 51760

    def test_every_field_is_kept_as_sent(self) -> None:
        # Including ones this library has no name for.
        payload = {
            **APP_REPLY,
            "basicVehicleStatus": {
                **APP_REPLY["basicVehicleStatus"],  # type: ignore[dict-item]
                "somethingNew": 5,
            },
        }
        reply = dacite.from_dict(VehicleStatusCacheResp, payload)
        assert reply.basicVehicleStatus is not None
        assert reply.basicVehicleStatus["somethingNew"] == 5
        assert reply.basicVehicleStatus["elecRangeStdA"] == 768
        assert len(reply.basicVehicleStatus) == 21

    def test_unknown_top_level_fields_and_an_empty_reply(self) -> None:
        reply = dacite.from_dict(VehicleStatusCacheResp, {**APP_REPLY, "extra": 1})
        assert reply.statusTime == 1791016937
        empty = dacite.from_dict(VehicleStatusCacheResp, {})
        assert empty.basicVehicleStatus is None
        assert empty.statusTime is None

    async def test_a_plain_request_not_an_ask_the_car_one(self) -> None:
        expected = VehicleStatusCacheResp(statusTime=1791016937)
        api = SaicVehicleApi.__new__(SaicVehicleApi)
        api.execute_api_call = AsyncMock(return_value=expected)  # type: ignore[method-assign]
        api.execute_api_call_with_event_id = AsyncMock()  # type: ignore[method-assign]

        result = await api.get_vehicle_status_cached("LSJWH4098PN000001")

        assert result is expected
        api.execute_api_call.assert_awaited_once_with(
            "GET",
            "/vehicle/status/cache",
            params={
                "vin": sha256_hex_digest("LSJWH4098PN000001"),
                "vehStatusReqType": "2",
            },
            out_type=VehicleStatusCacheResp,
        )
        api.execute_api_call_with_event_id.assert_not_awaited()
