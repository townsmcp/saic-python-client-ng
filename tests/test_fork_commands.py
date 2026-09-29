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
