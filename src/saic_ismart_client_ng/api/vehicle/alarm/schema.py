from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class AlarmType(Enum):
    ALARM_TYPE_VEHICLE_FAULT = 0
    ALARM_TYPE_GEOFENCE = 2
    ALARM_TYPE_VEHICLE_START = 3


@dataclass
class AlarmSwitch:
    alarmType: int | None = None
    functionSwitch: int | None = None
    alarmSwitch: int | None = None


@dataclass
class AlarmSwitchResp:
    alarmSwitchList: list[AlarmSwitch] = field(default_factory=list)


@dataclass
class AlarmSwitchReq:
    vin: str
    alarmSwitchList: list[AlarmSwitch] = field(default_factory=list)


@dataclass(frozen=True)
class AlarmRearmResult:
    """What happened to one alarm type while it was switched off and on again."""

    alarm_type: AlarmType
    off_confirmed: bool
    on_confirmed: bool
