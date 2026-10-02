from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="HEALTHOPS_", env_file=".env", extra="ignore")

    database_url: SecretStr = SecretStr(
        "postgresql+psycopg://healthops:local-only-password@localhost:55432/healthops"
    )
    # The current bootstrap auth is not approved for an internet-facing deployment.
    environment: str = "local"

    # Assistant (single-turn question answering). Disabled unless a model endpoint is set.
    # Any OpenAI-compatible chat endpoint; local Ollama now, vLLM later. Switching servers
    # requires rerunning the protocol tests and the evaluation, not only changing these values.
    assistant_model_base_url: str | None = None
    assistant_model_name: str = "qwen3:8b"
    assistant_model_timeout_seconds: float = 120.0
    assistant_max_output_tokens: int = 800
    assistant_temperature: float = 0.0
    assistant_thinking: bool | None = None
    assistant_max_tool_calls: int = 4
    assistant_max_model_turns: int = 6
    assistant_deadline_seconds: float = 120.0
    assistant_model_context_budget_chars: int = 24_000
    assistant_evidence_budget_bytes: int = 262_144
    assistant_max_concurrency: int = 2
    # Evaluation only: lets a request pin "today" so relative dates are reproducible.
    assistant_allow_today_override: bool = False

    def require_local(self) -> None:
        if self.environment != "local":
            raise ValueError("This milestone supports local development only.")
