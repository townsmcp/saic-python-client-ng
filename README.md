# SAIC Python client library

## About

A Python package for interacting with the MG iSmart API.

MG iSmart is the connectivity system in your MG car (MG5 MG4, ZS, MGS5, MGS6 ...).

Supported functionality (partial list, check the code for more)

* login
* set alarm switches
* get message list
* get vehicle status
* get charging status
* lock vehicle
* unlock vehicle
* start rear window heating
* stop rear window heating
* remote climate
* heated seats: each seat on its own, front and rear, plus rear seat status
* heated steering wheel
* door windows: close, ventilate or fully open


## Errors

Every error is a `SaicApiException` (or a subclass). Besides the message text, it carries SAIC's reply as separate values, so you don't need to parse the text:

* `return_code`: SAIC's numeric code (see `SaicReturnCode`), or `None`
* `saic_message`: the message on its own
* checks for the common cases: `is_vehicle_unreachable` (code 4), `is_request_rejected` (code 8), `is_vehicle_not_locked`, `is_logged_out` and `is_unexpected_failure`

## Prerequisites

You have an iSmart account (can be created in the iSmart app)

## Credit

This is a maintained fork of [SAIC-iSmart-API/saic-python-client-ng](https://github.com/SAIC-iSmart-API/saic-python-client-ng) by Giovanni Condello, used by the [mg-saic-ha](https://github.com/townsmcp/mg-saic-ha) Home Assistant integration. Licensed under MIT; see [LICENSE](LICENSE).
