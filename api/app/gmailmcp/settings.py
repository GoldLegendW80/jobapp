"""Provider settings deliberately have no database, session, encryption, or OAuth secret."""

from typing import Literal

from pydantic import SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class ProviderSettings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")
    app_env: Literal["production", "test", "development"] = "production"
    gmail_mcp_url: str = "http://gmailmcp:9400/mcp"
    google_api_base_url: str | None = None
    google_auth_url: str | None = None
    google_token_base_url: str | None = None
    # GoogleClient's OAuth half is never called in this process.
    google_oauth_client_id: None = None
    google_oauth_client_secret: None = None
    mail_send_key: SecretStr | None = None
    gmail_mcp_redis_password_file: str | None = None

    @model_validator(mode="after")
    def production_endpoints(self) -> "ProviderSettings":
        if self.app_env == "production" and any(
            (
                self.google_api_base_url,
                self.google_auth_url,
                self.google_token_base_url,
            )
        ):
            raise ValueError("Google endpoint overrides are refused in production")
        return self
