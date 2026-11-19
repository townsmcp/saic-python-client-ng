from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from saic_ismart_client_ng import SaicApi
from saic_ismart_client_ng.api.schema import LoginResp
from saic_ismart_client_ng.model import SaicApiConfiguration


async def _login_body(config: SaicApiConfiguration) -> dict[str, str]:
    api = SaicApi(config)
    reply = LoginResp(access_token="token", expires_in=3600)
    with patch.object(
        SaicApi, "execute_api_call", new=AsyncMock(return_value=reply)
    ) as call:
        await api.login()
    await api.close()
    assert call.await_args is not None
    body: dict[str, str] = call.await_args.kwargs["form_body"]
    return body


@pytest.mark.asyncio
async def test_login_without_push_token_is_unchanged() -> None:
    body = await _login_body(SaicApiConfiguration("user@example.com", "password"))
    assert body["deviceType"] == "0"
    assert body["deviceId"].startswith("simulator")
    assert body["deviceId"].endswith("###com.saicmotor.europecar")


@pytest.mark.asyncio
async def test_login_with_push_token_binds_it_like_the_android_app() -> None:
    config = SaicApiConfiguration(
        "user@example.com", "password", push_token="abc:APA91xyz"
    )
    body = await _login_body(config)
    assert body["deviceId"] == "abc:APA91xyz###europecar"
    assert body["deviceType"] == "1"


@pytest.mark.asyncio
async def test_push_token_can_be_changed_between_logins() -> None:
    config = SaicApiConfiguration("user@example.com", "password")
    config.push_token = "first"
    assert (await _login_body(config))["deviceId"] == "first###europecar"
    config.push_token = "second"
    assert (await _login_body(config))["deviceId"] == "second###europecar"
    config.push_token = None
    assert (await _login_body(config))["deviceType"] == "0"
