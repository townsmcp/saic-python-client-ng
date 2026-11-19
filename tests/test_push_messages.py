from __future__ import annotations

from datetime import UTC, datetime, timedelta

from saic_ismart_client_ng.push import (
    MESSAGE_TYPE_FINISH_CHARGING,
    MESSAGE_TYPE_VEHICLE_START,
    McsMessage,
    PushMessage,
)

# A real car-start push (19 Nov 2026), with the VIN altered.
VEHICLE_START = {
    "preDriveId": "null",
    "sound": "default",
    "hiddenAttr": "",
    "contentId": "null",
    "google.c.sender.id": "50558369982",
    "lon": "null",
    "title": "Vehicle Start",
    "body": "The vehicle(***647) starts on 19-11-2026 15:24:36 GMT, please confirm that you know it.",
    "badge": "15",
    "sentryModeActiveSts": "null",
    "messageType": "323",
    "vin": "LSJTESTVIN0000001",
    "lat": "null",
}


def _mcs(data: dict[str, str], sent_ms: int | None = 1795101876212) -> McsMessage:
    return McsMessage(
        data=data,
        persistent_id="0:1795101876226486%7c07428ef9fd7ecd",
        sent_ms=sent_ms,
        ttl=2419200,
    )


def test_vehicle_start_is_parsed() -> None:
    message = PushMessage.from_mcs(_mcs(VEHICLE_START))
    assert message.message_type == MESSAGE_TYPE_VEHICLE_START == "323"
    assert message.is_vehicle_start
    assert not message.is_charge_finished
    assert message.vin == "LSJTESTVIN0000001"
    assert message.title == "Vehicle Start"
    assert message.sent_at == datetime(2026, 11, 19, 15, 24, 36, 212000, tzinfo=UTC)


def test_charge_finished_is_recognised() -> None:
    data = {**VEHICLE_START, "messageType": "328", "title": "Finish Charging"}
    message = PushMessage.from_mcs(_mcs(data))
    assert message.message_type == MESSAGE_TYPE_FINISH_CHARGING == "328"
    assert message.is_charge_finished
    assert not message.is_vehicle_start


def test_null_and_empty_values_become_none() -> None:
    message = PushMessage.from_mcs(
        _mcs({"messageType": "null", "vin": "", "title": "x"})
    )
    assert message.message_type is None
    assert message.vin is None
    assert message.title == "x"


def test_age_uses_when_the_message_was_sent() -> None:
    message = PushMessage.from_mcs(_mcs(VEHICLE_START))
    assert message.sent_at is not None
    later = message.sent_at + timedelta(seconds=90)
    assert message.age_seconds(later) == 90
    assert PushMessage.from_mcs(_mcs(VEHICLE_START, sent_ms=None)).age_seconds() is None


def test_event_data_drops_google_keys_and_null_placeholders() -> None:
    event = PushMessage.from_mcs(_mcs(VEHICLE_START)).as_event_data()
    assert event["message_type"] == "323"
    assert event["sent_at"] == "2026-11-19T15:24:36.212000+00:00"
    assert "google.c.sender.id" not in event["data"]
    assert "lat" not in event["data"]
    assert event["data"]["title"] == "Vehicle Start"
