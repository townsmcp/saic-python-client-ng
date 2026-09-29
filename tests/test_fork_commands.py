"""Commands captured from the iSmart app (decrypted MG S6 EV traffic).

Each test records the request body the library would send and checks it is
exactly what the app sends, parameter by parameter.
"""

from __future__ import annotations

import asyncio
import base64
from typing import Any

import pytest

from saic_ismart_client_ng import SaicApi
from saic_ismart_client_ng.api.vehicle import RvcReqType
from saic_ismart_client_ng.api.vehicle.climate import (
    REAR_HEATED_SEAT_ON_LEVEL,
    HeatedSeat,
)
from saic_ismart_client_ng.api.vehicle.windows import DoorWindowsAction
from saic_ismart_client_ng.crypto_utils import sha256_hex_digest
from saic_ismart_client_ng.model import SaicApiConfiguration

VIN = "OFFLINE0000000000"


def _sent(
    call: str, **kwargs: Any
) -> tuple[str | int | None, list[tuple[int, bytes]], str]:
    """Run an API command offline; return (request type, params, hashed vin)."""
    api = SaicApi(SaicApiConfiguration(username="offline@example.com", password="x"))  # noqa: S106
    captured: dict[str, Any] = {}

    async def capture(body: Any, vin: str) -> None:
        captured["body"] = body
        captured["vin"] = vin

    api.send_vehicle_control_command = capture  # type: ignore[method-assign,assignment]
    asyncio.run(getattr(api, call)(VIN, **kwargs))
    body = captured["body"]
    params = [(p.paramId, base64.b64decode(p.paramValue)) for p in body.rvcParams]
    return body.rvcReqType, params, body.vin


@pytest.mark.parametrize(("enable", "value"), [(True, 1), (False, 0)])
def test_heated_steering_wheel(enable: bool, value: int) -> None:
    req_type, params, vin = _sent("control_heated_steering_wheel", enable=enable)
    assert req_type == "8"
    assert req_type == RvcReqType.HEATED_STEERING_WHEEL.value
    assert params == [(24, bytes([value]))]
    assert vin == sha256_hex_digest(VIN)


@pytest.mark.parametrize(
    ("action", "value"),
    [
        (DoorWindowsAction.CLOSE, 0),
        (DoorWindowsAction.VENTILATE, 1),
        (DoorWindowsAction.OPEN, 2),
    ],
)
def test_door_windows(action: DoorWindowsAction, value: int) -> None:
    req_type, params, vin = _sent("control_door_windows", action=action)
    assert req_type == "3"
    assert params == [
        (8, b"\x00"),  # sunroof left alone
        (9, b"\x01"),
        (10, b"\x01"),
        (11, b"\x01"),
        (12, b"\x01"),
        (13, bytes([value])),
    ]
    assert vin == sha256_hex_digest(VIN)


def test_control_windows_open_value_unchanged() -> None:
    # The original helper keeps its open value (3) for other models.
    _, params, _ = _sent("control_windows", should_open=True, windows=[])
    assert params[-1] == (13, b"\x03")


@pytest.mark.parametrize(
    ("seat", "param_id"),
    [
        (HeatedSeat.FRONT_LEFT, 17),
        (HeatedSeat.FRONT_RIGHT, 18),
        (HeatedSeat.REAR_LEFT, 25),
        (HeatedSeat.REAR_RIGHT, 26),
    ],
)
@pytest.mark.parametrize("level", [0, 1, 2, 3])
def test_heated_seat_one_seat_only(seat: HeatedSeat, param_id: int, level: int) -> None:
    req_type, params, vin = _sent("control_heated_seat", seat=seat, level=level)
    assert req_type == "5"
    assert params == [(param_id, bytes([level]))]
    assert vin == sha256_hex_digest(VIN)


def test_rear_heated_seat_on_is_the_apps_level() -> None:
    assert REAR_HEATED_SEAT_ON_LEVEL == 3
    _, params, _ = _sent(
        "control_heated_seat",
        seat=HeatedSeat.REAR_LEFT,
        level=REAR_HEATED_SEAT_ON_LEVEL,
    )
    assert params == [(25, b"\x03")]


@pytest.mark.parametrize("level", [-1, 4])
def test_heated_seat_rejects_out_of_range_levels(level: int) -> None:
    with pytest.raises(ValueError, match="0-3"):
        _sent("control_heated_seat", seat=HeatedSeat.FRONT_LEFT, level=level)


def test_front_seats_are_physical_sides_with_old_names_kept() -> None:
    from saic_ismart_client_ng.api.vehicle import RvcParamsId  # noqa: PLC0415

    assert RvcParamsId.HEATED_SEAT_FRONT_LEFT.value == 17
    assert RvcParamsId.HEATED_SEAT_FRONT_RIGHT.value == 18
    # The original names still work and are the same parameters. (Looked up
    # by name: mypy doesn't know enum aliases share a member.)
    assert RvcParamsId["HEATED_SEAT_DRIVER"] is RvcParamsId.HEATED_SEAT_FRONT_LEFT
    assert RvcParamsId["HEATED_SEAT_PASSENGER"] is RvcParamsId.HEATED_SEAT_FRONT_RIGHT


def test_both_front_seats_command_unchanged() -> None:
    _, params, _ = _sent("control_heated_seats", left_side_level=1, right_side_level=3)
    assert params == [(17, b"\x01"), (18, b"\x03"), (255, b"\x00\x00\x00\x00")]
