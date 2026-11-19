# SAIC Python client library

## About

A Python package for interacting with the MG iSmart API.

MG iSmart is the connectivity system in your MG car (MG5 MG4, ZS, MGS5, MGS6 ...).

Supported functionality (partial list, check the code for more)

* login
* set alarm switches
* get message list
* get vehicle status
* get cached vehicle status (SAIC's stored copy; does not wake the car)
* get charging status
* lock vehicle
* unlock vehicle
* start rear window heating
* stop rear window heating
* remote climate
* heated seats: each seat on its own, front and rear, plus rear seat status
* heated steering wheel
* door windows: close, ventilate or fully open


## Push notifications

The optional `saic_ismart_client_ng.push` package receives SAIC's real-time
notifications (vehicle started, charging finished, ...) instead of polling the
message queue. Install it with `pip install "mg-saic-client[push]"`.

It registers as an Android device with Google, keeps a connection to Google's
push servers open, and binds that registration to your SAIC session at login:

```python
from saic_ismart_client_ng import SaicApi
from saic_ismart_client_ng.model import SaicApiConfiguration
from saic_ismart_client_ng.push import PushMessage, SaicPushClient


async def on_push(message: PushMessage) -> None:
    if message.is_vehicle_start:
        print("Car started:", message.vin, message.sent_at)


push = SaicPushClient(on_message=on_push)
credentials = await push.start()  # registers on first use; keep `credentials` to reuse

config = SaicApiConfiguration("user@example.com", "password", push_token=credentials.token)
api = SaicApi(config)
await api.login()  # SAIC now sends pushes for this account to `push`
```

Things worth knowing:

* SAIC allows one session per account, and push follows the most recent login.
  Logging in anywhere else (including the phone app) moves pushes there. Use a
  separate account for the integration.
* A registration that has been in use for several days can stop receiving
  vehicle pushes while still looking healthy. `SaicPushClient.renew()` swaps in
  a fresh identity; log in again with the new `push_token` afterwards.
* Every message is acknowledged to Google, and `persistent_ids` can be saved
  between runs so a restart does not see old messages again. Google keeps
  undelivered messages for up to 28 days, so check `PushMessage.age_seconds()`
  before acting on one.
* A connected client does not prove pushes are arriving. Keep polling as a
  slow fallback.

## Errors

Every error is a `SaicApiException` (or a subclass). Besides the message text, it carries SAIC's reply as separate values, so you don't need to parse the text:

* `return_code`: SAIC's numeric code (see `SaicReturnCode`), or `None`
* `saic_message`: the message on its own
* checks for the common cases: `is_vehicle_unreachable` (code 4), `is_request_rejected` (code 8), `is_vehicle_not_locked`, `is_logged_out` and `is_unexpected_failure`

## Prerequisites

You have an iSmart account (can be created in the iSmart app)

## Credit

This is a maintained fork of [SAIC-iSmart-API/saic-python-client-ng](https://github.com/SAIC-iSmart-API/saic-python-client-ng) by Giovanni Condello, used by the [mg-saic-ha](https://github.com/townsmcp/mg-saic-ha) Home Assistant integration. Licensed under MIT; see [LICENSE](LICENSE).
