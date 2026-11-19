from __future__ import annotations


class SaicApiConfiguration:
    # pylint: disable=too-many-positional-arguments
    def __init__(
        self,
        username: str,
        password: str,
        username_is_email: bool = True,
        phone_country_code: str | None = None,
        base_uri: str = "https://gateway-mg-eu.soimt.com/api.app/v1/",
        tenant_id: str = "459771",
        region: str = "eu",
        sms_delivery_delay: float = 3.0,
        read_timeout: float = 5.0,
        push_token: str | None = None,
    ) -> None:
        self.__username = username
        self.__password = password
        self.__username_is_email = username_is_email
        self.__phone_country_code = phone_country_code
        self.__base_uri = base_uri
        self.__tenant_id = tenant_id
        self.__region = region
        self.__sms_delivery_delay = sms_delivery_delay
        self.__read_timeout = read_timeout
        self.__push_token = push_token

    @property
    def username(self) -> str:
        return self.__username

    @property
    def password(self) -> str:
        return self.__password

    @property
    def username_is_email(self) -> bool:
        return self.__username_is_email

    @property
    def phone_country_code(self) -> str | None:
        return self.__phone_country_code

    @property
    def base_uri(self) -> str:
        return self.__base_uri

    @property
    def tenant_id(self) -> str:
        return self.__tenant_id

    @property
    def region(self) -> str:
        return self.__region

    @property
    def sms_delivery_delay(self) -> float:
        return self.__sms_delivery_delay

    @property
    def read_timeout(self) -> float:
        return self.__read_timeout

    @property
    def push_token(self) -> str | None:
        """An FCM registration token to bind to the session at login.

        When set, login presents it the way the Android app does, so SAIC
        delivers real-time pushes to that registration. It can be changed at
        any time; the next login (including an automatic re-login) uses it.
        """
        return self.__push_token

    @push_token.setter
    def push_token(self, value: str | None) -> None:
        self.__push_token = value or None
