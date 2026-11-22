"""LLM fallback client (OpenAI-compatible API).

Works with OpenAI directly, or with Gemini / any OpenAI-compatible gateway
by overriding `base_url` (e.g. Gemini:
`https://generativelanguage.googleapis.com/v1beta/openai/`).
"""

import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)


class LLMClient:
    """Thin async wrapper that degrades gracefully when unconfigured."""

    def __init__(
        self,
        api_key: str,
        base_url: str = "https://api.openai.com/v1",
        model: str = "gpt-4o-mini",
        max_history: int = 10,
    ) -> None:
        self._api_key = api_key
        self._base_url = base_url
        self._model = model
        self._max_history = max(0, max_history)
        self._client: Optional[Any] = None
        if api_key:
            try:
                from openai import AsyncOpenAI

                self._client = AsyncOpenAI(api_key=api_key, base_url=base_url)
            except ImportError:
                logger.warning("openai package not installed; LLM fallback disabled.")

    @property
    def enabled(self) -> bool:
        """True when the client can actually call an LLM."""
        return self._client is not None

    async def generate(
        self,
        system_prompt: str,
        history: list[dict[str, str]],
        user_text: str,
        menu_hint: str = "",
    ) -> str:
        """Generate a natural reply grounded in the business profile.

        Returns a safe fallback message when the LLM is unavailable or fails.
        """
        if self._client is None:
            logger.info("LLM fallback skipped (no API key); using static fallback.")
            return self.static_fallback(menu_hint)

        messages: list[dict[str, str]] = [{"role": "system", "content": system_prompt}]
        messages.extend(history[-self._max_history :] if self._max_history else [])
        messages.append({"role": "user", "content": user_text})

        try:
            response = await self._client.chat.completions.create(
                model=self._model,
                messages=messages,  # type: ignore[arg-type]
                temperature=0.4,
                max_tokens=500,
            )
            content: Optional[str] = response.choices[0].message.content
            if content:
                return content.strip()
            logger.warning("LLM returned empty content; using static fallback.")
        except Exception as exc:  # Never let the LLM break the webhook.
            logger.error("LLM request failed: %s", exc)
        return self.static_fallback(menu_hint)

    @staticmethod
    def static_fallback(menu_hint: str = "") -> str:
        """Reply used when the LLM is unavailable."""
        fallback = (
            "I didn't quite catch that. Type *menu* to see what I can help with,"
        )
        if menu_hint:
            fallback += f" e.g. {menu_hint}."
        else:
            fallback += " and I'll take it from there."
        return fallback
