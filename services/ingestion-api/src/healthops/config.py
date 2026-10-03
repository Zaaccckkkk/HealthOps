from typing import ClassVar

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="HEALTHOPS_", env_file=".env", extra="ignore")

    database_url: SecretStr = SecretStr(
        "postgresql+psycopg://healthops:local-only-password@localhost:55432/healthops"
    )
    # "local": loopback development. "cloud-dev": one single-tester GPU VM behind HTTPS with
    # development bearer tokens, synthetic data only; not a multi-tenant or production setting.
    environment: str = "local"
    # Per-user cap on assistant questions (sliding minute). GPU inference is never anonymous
    # and never unlimited: authentication happens before the limiter and the model.
    assistant_rate_limit_per_minute: int = 10

    # Assistant (single-turn question answering). Disabled unless a model endpoint is set.
    # Any OpenAI-compatible chat endpoint; local Ollama now, vLLM later. Switching servers
    # requires rerunning the protocol tests and the evaluation, not only changing these values.
    assistant_model_base_url: str | None = None
    # "ollama": native /api/chat with verified reasoning control (assistant_thinking honoured).
    # "openai_compatible": any OpenAI-style endpoint; assistant_thinking must stay unset.
    assistant_model_backend: str = "ollama"
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

    SUPPORTED_ENVIRONMENTS: ClassVar[tuple[str, ...]] = ("local", "cloud-dev")

    def require_local(self) -> None:
        """Fail closed for any environment this milestone does not support."""
        if self.environment not in self.SUPPORTED_ENVIRONMENTS:
            raise ValueError(
                "This milestone supports the local and cloud-dev (single tester) environments only."
            )
