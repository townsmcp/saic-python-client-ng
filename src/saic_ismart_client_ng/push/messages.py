"""The push messages SAIC sends, as typed objects."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .mcs import McsMessage

# ``messageType`` values seen in real pushes.
MESSAGE_TYPE_VEHICLE_START = "323"
MESSAGE_TYPE_FINISH_CHARGING = "328"


@dataclass(frozen=True)
class PushMessage:
    """A vehicle or account notification delivered by push."""

    message_type: str | None
    vin: str | None
    title: str | None
    body: str | None
    sent_at: datetime | None
    persistent_id: str
    data: dict[str, str]

    @property
    def is_vehicle_start(self) -> bool:
        return self.message_type == MESSAGE_TYPE_VEHICLE_START

    @property
    def is_charge_finished(self) -> bool:
        return self.message_type == MESSAGE_TYPE_FINISH_CHARGING

    def age_seconds(self, now: datetime | None = None) -> float | None:
        """How long ago Google says SAIC sent it, or None if unknown."""
        if self.sent_at is None:
            return None
        return ((now or datetime.now(UTC)) - self.sent_at).total_seconds()

    @classmethod
    def from_mcs(cls, message: McsMessage) -> PushMessage:
        data = message.data

        def text(key: str) -> str | None:
            value = data.get(key)
            return value if value not in (None, "", "null") else None

        sent_at = None
        if message.sent_ms:
            sent_at = datetime.fromtimestamp(message.sent_ms / 1000, tz=UTC)
        return cls(
            message_type=text("messageType"),
            vin=text("vin"),
            title=text("title"),
            body=text("body"),
            sent_at=sent_at,
            persistent_id=message.persistent_id,
            data=dict(data),
        )

    def as_event_data(self) -> dict[str, Any]:
        """Summarise as JSON-friendly data, without Google's internal bookkeeping keys."""
        return {
            "message_type": self.message_type,
            "vin": self.vin,
            "title": self.title,
            "body": self.body,
            "sent_at": self.sent_at.isoformat() if self.sent_at else None,
            "data": {
                key: value
                for key, value in self.data.items()
                if not key.startswith("google.") and value != "null"
            },
        }
