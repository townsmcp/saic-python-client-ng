"""How a push registration is presented to SAIC at login.

Kept free of any push dependency (protobuf) so ``login`` works without it.
"""

from __future__ import annotations

# The real Android app logs in with ``deviceId=<fcm token>###europecar`` and
# ``deviceType=1`` (the iOS app uses 0). SAIC binds push delivery to whichever
# session logged in last with such a deviceId.
PUSH_DEVICE_ID_SUFFIX = "europecar"
PUSH_DEVICE_TYPE = "1"
