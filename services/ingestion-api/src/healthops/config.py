from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="HEALTHOPS_", env_file=".env", extra="ignore")

    database_url: SecretStr = SecretStr(
        "postgresql+psycopg://healthops:local-only-password@localhost:55432/healthops"
    )
    # The current bootstrap auth is not approved for an internet-facing deployment.
    environment: str = "local"

    def require_local(self) -> None:
        if self.environment != "local":
            raise ValueError("This milestone supports local development only.")
