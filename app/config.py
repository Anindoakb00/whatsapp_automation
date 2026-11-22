"""Application configuration loaded from environment / `.env` file.

Zero hardcoded secrets: every credential comes from the environment.
Copy `.env.example` to `.env` and fill in your own values.
"""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime settings for the WhatsApp chatbot."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Meta WhatsApp Cloud API (Graph API v26.0)
    WHATSAPP_TOKEN: str = ""
    PHONE_NUMBER_ID: str = ""
    VERIFY_TOKEN: str = ""
    APP_SECRET: str = ""  # Optional: enables X-Hub-Signature-256 verification when set.
    GRAPH_VERSION: str = "v26.0"

    # LLM fallback (OpenAI-compatible; works with OpenAI or Gemini's
    # OpenAI-compatible endpoint via LLM_BASE_URL override)
    LLM_API_KEY: str = ""
    LLM_BASE_URL: str = "https://api.openai.com/v1"
    LLM_MODEL: str = "gpt-4o-mini"
    LLM_MAX_HISTORY: int = 10

    # Active business profile: name of a JSON file in profiles/ (without extension)
    ACTIVE_PROFILE: str = "ecommerce"

    # Resets the conversation to the main menu (lowercase, stripped match)
    RESET_KEYWORDS: tuple[str, ...] = ("menu", "hi", "hello", "hey", "start", "restart")

    @property
    def graph_base_url(self) -> str:
        """Messages endpoint for the configured phone number ID."""
        return f"https://graph.facebook.com/{self.GRAPH_VERSION}/{self.PHONE_NUMBER_ID}/messages"

    @property
    def whatsapp_configured(self) -> bool:
        """True when the minimum Meta credentials are present."""
        return bool(self.WHATSAPP_TOKEN and self.PHONE_NUMBER_ID and self.VERIFY_TOKEN)

    @property
    def llm_configured(self) -> bool:
        """True when an LLM key is present for the AI fallback."""
        return bool(self.LLM_API_KEY)


settings = Settings()
