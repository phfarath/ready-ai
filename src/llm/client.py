"""
Async LLM Client via LiteLLM.

Supports multi-model (OpenAI, Anthropic, etc.) with vision capabilities
for screenshot analysis. Includes automatic retry with exponential backoff
for rate limit errors.
"""

import asyncio
import logging
from typing import Any, Optional

import litellm

logger = logging.getLogger(__name__)

# Suppress litellm's verbose logging
litellm.suppress_debug_info = True

MAX_LLM_RETRIES = 5
INITIAL_RETRY_DELAY = 1.0  # seconds


class LLMClient:
    """Async LLM wrapper with vision support and rate limit retry."""

    def __init__(
        self,
        model: str = "gpt-4o-mini",
        temperature: float = 0.2,
        max_tokens: int = 4096,
    ):
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens

    async def _call_with_retry(self, kwargs: dict[str, Any]) -> str:
        """
        Call litellm.acompletion with exponential backoff on rate limit errors.

        Retries up to MAX_LLM_RETRIES times with exponential backoff
        (1s, 2s, 4s, 8s, 16s) on 429 RateLimitError.
        """
        last_error = None
        for attempt in range(1, MAX_LLM_RETRIES + 1):
            try:
                response = await litellm.acompletion(**kwargs)
                content = response.choices[0].message.content
                logger.debug(f"LLM response ({len(content)} chars): {content[:100]}...")
                return content
            except litellm.exceptions.RateLimitError as e:
                last_error = e
                delay = INITIAL_RETRY_DELAY * (2 ** (attempt - 1))
                logger.warning(
                    f"Rate limit hit (attempt {attempt}/{MAX_LLM_RETRIES}), "
                    f"retrying in {delay:.1f}s..."
                )
                await asyncio.sleep(delay)
            except Exception as e:
                logger.error(f"LLM call failed: {e}")
                raise

        logger.error(f"Rate limit exceeded after {MAX_LLM_RETRIES} retries")
        raise last_error

    async def complete(
        self,
        messages: list[dict],
        json_mode: bool = False,
    ) -> str:
        """
        Send a chat completion request.

        Args:
            messages: List of message dicts (role + content)
            json_mode: Request JSON output format

        Returns:
            The assistant's response text
        """
        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
        }

        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}

        return await self._call_with_retry(kwargs)

    async def complete_with_vision(
        self,
        prompt: str,
        image_b64: str,
        system: Optional[str] = None,
        json_mode: bool = False,
    ) -> str:
        """
        Send a vision request with a base64 image.

        Args:
            prompt: Text prompt describing what to analyze
            image_b64: Base64-encoded image (PNG/JPEG)
            system: Optional system prompt
            json_mode: Request JSON output format

        Returns:
            The assistant's response text
        """
        messages = []
        if system:
            messages.append({"role": "system", "content": system})

        messages.append({
            "role": "user",
            "content": [
                {"type": "text", "text": prompt},
                {
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:image/png;base64,{image_b64}",
                        "detail": "high",
                    },
                },
            ],
        })

        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
        }

        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}

        return await self._call_with_retry(kwargs)
