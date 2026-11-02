from typing import ClassVar

from pydantic import Field, SecretStr
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
    assistant_model_timeout_seconds: float = Field(default=120.0, gt=0)
    assistant_model_connect_timeout_seconds: float = Field(default=5.0, gt=0, le=30)
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

    # Google Health connector (server-side OAuth). Disabled unless all three are set. The
    # client secret and the token-encryption key live only in the server environment.
    google_oauth_client_id: str | None = None
    google_oauth_client_secret: SecretStr | None = None
    google_oauth_redirect_uri: str | None = None
    connection_key: SecretStr | None = None  # Fernet key for stored refresh tokens
    google_oauth_authorize_url: str = "https://accounts.google.com/o/oauth2/v2/auth"
    google_oauth_token_url: str = "https://oauth2.googleapis.com/token"
    google_oauth_revoke_url: str = "https://oauth2.googleapis.com/revoke"
    google_health_base_url: str = "https://health.googleapis.com/v4"
    connection_sync_default_days: int = 14
    connection_sync_max_days: int = 90
    # Where the browser is sent after the callback; the app registers this URL scheme.
    app_callback_url: str = "healthops://google-health"

    # Knowledge base (retrieval). Embeddings come from the same Ollama endpoint as the chat
    # model unless an embedding URL is set. The model name is stored per chunk; changing it
    # means re-ingesting.
    knowledge_embedding_model: str = "nomic-embed-text"
    knowledge_embedding_base_url: str | None = None  # defaults to assistant_model_base_url
    knowledge_embedding_dimensions: int = 768
    knowledge_top_k: int = 3
    # Cosine similarity below this is "no reference". Probed locally with nomic-embed-text:
    # unrelated questions peaked at 0.49, genuine matches were 0.63 and above.
    knowledge_min_similarity: float = 0.55

    @property
    def knowledge_embedding_url(self) -> str | None:
        return self.knowledge_embedding_base_url or self.assistant_model_base_url

    @property
    def google_health_configured(self) -> bool:
        return bool(
            self.google_oauth_client_id
            and self.google_oauth_client_secret
            and self.google_oauth_redirect_uri
            and self.connection_key
        )

    SUPPORTED_ENVIRONMENTS: ClassVar[tuple[str, ...]] = ("local", "cloud-dev")

    def require_local(self) -> None:
        """Fail closed for any environment this milestone does not support."""
        if self.environment not in self.SUPPORTED_ENVIRONMENTS:
            raise ValueError(
                "This milestone supports the local and cloud-dev (single tester) environments only."
            )
