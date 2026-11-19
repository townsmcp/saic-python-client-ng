"""Constants for the iSmart (EU) Firebase project and the SAIC login binding.

The Firebase values are the public identifiers embedded in the iSmart Android
app (``com.saicmotor.iov.europe``). They identify the app to Google so that a
registration made by this library is routed the same way as one made by the app.
"""

from __future__ import annotations

# --- Firebase / FCM identity of the iSmart EU Android app -------------------
FCM_PROJECT_ID = "mg-ismart-eu"
FCM_SENDER_ID = "50558369982"
FCM_APP_ID = "1:50558369982:android:999349912069950645deef"
FCM_API_KEY = "AIzaSyAzY31EaV1F_jhstzcqkgcA3GfiGm0bMVA"
FCM_ANDROID_PACKAGE = "com.saicmotor.iov.europe"
# SHA-1 of the app's signing certificate, upper-case hex, no colons.
FCM_ANDROID_CERT_SHA1 = "C833880727A139517CCC57F24B8EAB1710DC65AF"

# --- Values the Firebase Android SDK sends alongside the above --------------
FIS_SDK_VERSION = "a:19.0.1"
FCM_CLIENT_LIBRARY = "fcm-25.0.1"
FCM_APP_VERSION_CODE = "130"
FCM_APP_VERSION_NAME = "3.0.5"
FCM_GMSV = "243816016"
FCM_OSV = "35"
FCM_TARGET_VER = "35"
FCM_PLATFORM = "0"
FIREBASE_APP_NAME = "[DEFAULT]"

# --- Endpoints ---------------------------------------------------------------
GCM_CHECKIN_URL = "https://android.clients.google.com/checkin"
GCM_REGISTER_URL = "https://android.clients.google.com/c2dm/register3"
FIS_INSTALLATIONS_URL = (
    f"https://firebaseinstallations.googleapis.com/v1/projects/{FCM_PROJECT_ID}"
    "/installations"
)
FIS_AUTH_TOKEN_URL = FIS_INSTALLATIONS_URL + "/{fid}/authTokens:generate"
FIS_DELETE_URL = FIS_INSTALLATIONS_URL + "/{fid}"

MCS_HOST = "mtalk.google.com"
MCS_PORT = 5228

# The SAIC login binding (deviceId suffix and deviceType) is in
# saic_ismart_client_ng.push_binding so login works without protobuf installed.
