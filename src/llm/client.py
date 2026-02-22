"""
Async LLM Client via LiteLLM.

Supports multi-model (OpenAI, Anthropic, etc.) with vision capabilities
for screenshot analysis.
"""

import logging
from typing import Any, Optional

import litellm

logger = logging.getLogger(__name__)

# Suppress litellm's verbose logging
litellm.suppress_debug_info = True


class LLMClient:
    """Async LLM wrapper with vision support."""

    def __init__(
        self,
        model: str = "gpt-4o-mini",
        temperature: float = 0.2,
        max_tokens: int = 4096,
    ):
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens

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

        try:
            response = await litellm.acompletion(**kwargs)
            content = response.choices[0].message.content
            logger.debug(f"LLM response ({len(content)} chars): {content[:100]}...")
            return content
        except Exception as e:
            logger.error(f"LLM call failed: {e}")
            raise

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

        try:
            response = await litellm.acompletion(**kwargs)
            content = response.choices[0].message.content
            logger.debug(f"Vision LLM response ({len(content)} chars)")
            return content
        except Exception as e:
            logger.error(f"Vision LLM call failed: {e}")
            raise
