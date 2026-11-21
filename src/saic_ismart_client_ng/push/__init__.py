"""Real-time push notifications from SAIC's iSmart backend.

Requires the optional ``protobuf`` dependency: ``pip install mg-saic-client[push]``.
"""

from __future__ import annotations

from .client import PushState, SaicPushClient
from .mcs import McsClient, McsMessage
from .messages import (
    MESSAGE_TYPE_FINISH_CHARGING,
    MESSAGE_TYPE_VEHICLE_START,
    PushMessage,
)
from .register import PushCredentials, PushRegistrationError

__all__ = [
    "MESSAGE_TYPE_FINISH_CHARGING",
    "MESSAGE_TYPE_VEHICLE_START",
    "McsClient",
    "McsMessage",
    "PushCredentials",
    "PushMessage",
    "PushRegistrationError",
    "PushState",
    "SaicPushClient",
]
