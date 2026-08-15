from __future__ import annotations

import pytest

from saic_ismart_client_ng import SaicApi
from saic_ismart_client_ng.model import SaicApiConfiguration


@pytest.mark.asyncio
async def test_close_releases_http_resources_and_is_idempotent() -> None:
    api = SaicApi(SaicApiConfiguration("user@example.com", "password"))
    api_client = api.__dict__["_AbstractSaicApi__api_client"]
    http_client = api_client.__dict__["_SaicApiClient__client"]

    assert not http_client.is_closed

    await api.close()
    assert http_client.is_closed

    await api.close()
    assert http_client.is_closed
