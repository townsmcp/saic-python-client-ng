from __future__ import annotations

import json
import unittest

import dacite

from saic_ismart_client_ng.api.vehicle import VehicleStatusResp

# An MG S6 EV (series MIS3E) /vehicle/status response, 2026-09-29, with one
# rear seat heating. Trimmed; GPS kept so the nested types are exercised.
MGS6_STATUS = """{
  "basicVehicleStatus": {
    "frontLeftSeatHeatLevel": 0,
    "frontRightSeatHeatLevel": 2,
    "secondRowLeftSeatHeatLevel": 3,
    "secondRowRightSeatHeatLevel": 0,
    "steeringHeatLevel": 0,
    "lockStatus": 1,
    "remoteClimateStatus": 0,
    "mileage": -128,
    "fuelRangeElec": 3430,
    "rearRightOSTyrePressure": -128,
    "elecRangeDspMode": -128
  },
  "extendedVehicleStatus": {"alertDataSum": [0, 0, 0]},
  "gpsPosition": {
    "timeStamp": 1790714719,
    "gpsStatus": 3,
    "wayPoint": {
      "satellites": 16,
      "heading": 21,
      "position": {"altitude": 42, "latitude": 51117513, "longitude": 891696},
      "hdop": 5,
      "speed": 0
    }
  },
  "statusTime": 1790714718
}"""


class TestVehicleStatusResp(unittest.TestCase):
    def setUp(self) -> None:
        self.status = dacite.from_dict(VehicleStatusResp, json.loads(MGS6_STATUS))
        assert self.status.basicVehicleStatus is not None
        self.basic = self.status.basicVehicleStatus

    def test_rear_seat_heat_levels(self) -> None:
        assert self.basic.secondRowLeftSeatHeatLevel == 3
        assert self.basic.secondRowRightSeatHeatLevel == 0

    def test_front_seat_heat_levels(self) -> None:
        assert self.basic.frontLeftSeatHeatLevel == 0
        assert self.basic.frontRightSeatHeatLevel == 2

    def test_rear_seat_levels_absent_on_cars_without_them(self) -> None:
        payload = json.loads(MGS6_STATUS)
        del payload["basicVehicleStatus"]["secondRowLeftSeatHeatLevel"]
        del payload["basicVehicleStatus"]["secondRowRightSeatHeatLevel"]
        status = dacite.from_dict(VehicleStatusResp, payload)
        assert status.basicVehicleStatus is not None
        assert status.basicVehicleStatus.secondRowLeftSeatHeatLevel is None
        assert status.basicVehicleStatus.secondRowRightSeatHeatLevel is None

    def test_rest_of_the_status_is_unchanged(self) -> None:
        assert self.basic.lockStatus == 1
        assert self.basic.is_parked
        assert self.status.statusTime == 1790714718
        assert self.status.gpsPosition is not None
        assert self.status.gpsPosition.wayPoint is not None
        assert self.status.gpsPosition.wayPoint.position is not None
        assert self.status.gpsPosition.wayPoint.position.latitude == 51117513


if __name__ == "__main__":
    unittest.main()
