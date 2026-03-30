"""
Async LLM Client via LiteLLM.

Supports multi-model (OpenAI, Anthropic, etc.) with vision capabilities
for screenshot analysis. Includes automatic retry with exponential backoff
for rate limit errors.
"""

import asyncio
import importlib
import logging
import time
from typing import Any, Optional

import litellm
import openai._compat as openai_compat

from ..observability import Span, get_metrics, log_event

logger = logging.getLogger(__name__)

# Suppress litellm's verbose logging
litellm.suppress_debug_info = True

MAX_LLM_RETRIES = 5
INITIAL_RETRY_DELAY = 1.0  # seconds


def _patch_openai_model_dump() -> None:
    """
    Work around OpenAI/Pydantic compatibility where by_alias=None crashes.

    Some installed combinations call pydantic v2's model_dump(by_alias=None),
    which raises `TypeError: argument 'by_alias': 'NoneType' object cannot be
    converted to 'PyBool'`. Coerce None to False before delegating.
    """
    original_model_dump = openai_compat.model_dump

    def patched_model_dump(
        model: Any,
        *,
        exclude: Any = None,
        exclude_unset: bool = False,
        exclude_defaults: bool = False,
        warnings: bool = True,
        mode: str = "python",
        by_alias: Optional[bool] = None,
    ) -> dict[str, Any]:
        return original_model_dump(
            model,
            exclude=exclude,
            exclude_unset=exclude_unset,
            exclude_defaults=exclude_defaults,
            warnings=warnings,
            mode=mode,
            by_alias=False if by_alias is None else by_alias,
        )

    if getattr(openai_compat.model_dump, "__name__", "") == "patched_model_dump":
        return

    openai_compat.model_dump = patched_model_dump

    # The OpenAI SDK imports `model_dump` directly into several modules at import
    # time, so patch those bound references too.
    for module_name in (
        "openai._base_client",
        "openai._utils._transform",
        "openai._utils._json",
        "openai.lib.streaming._assistants",
        "openai.lib.streaming.chat._completions",
    ):
        try:
            module = importlib.import_module(module_name)
        except Exception:
            continue
        if hasattr(module, "model_dump"):
            setattr(module, "model_dump", patched_model_dump)


_patch_openai_model_dump()


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

    async def _call_with_retry(self, kwargs: dict[str, Any], role: str = "unknown") -> str:
        """
        Call litellm.acompletion with exponential backoff on rate limit errors.

        Retries up to MAX_LLM_RETRIES times with exponential backoff
        (1s, 2s, 4s, 8s, 16s) on 429 RateLimitError.
        """
        last_error = None
        call_start = time.monotonic()

        for attempt in range(1, MAX_LLM_RETRIES + 1):
            try:
                response = await litellm.acompletion(**kwargs)
                content = response.choices[0].message.content
                latency_ms = (time.monotonic() - call_start) * 1000

                # Track usage metrics
                metrics = get_metrics()
                if metrics:
                    metrics.increment("llm.calls", role=role)
                    metrics.record("llm.latency_ms", latency_ms)

                    usage = getattr(response, "usage", None)
                    if usage:
                        prompt_tokens = getattr(usage, "prompt_tokens", 0) or 0
                        completion_tokens = getattr(usage, "completion_tokens", 0) or 0
                        metrics.increment("llm.prompt_tokens", prompt_tokens, role=role)
                        metrics.increment("llm.completion_tokens", completion_tokens, role=role)

                        try:
                            cost = litellm.completion_cost(completion_response=response)
                            metrics.increment("llm.cost_usd", cost, role=role)
                        except Exception:
                            pass  # Cost calculation not available for all models

                log_event(
                    "llm_call",
                    model=self.model,
                    role=role,
                    attempt=attempt,
                    latency_ms=round(latency_ms, 1),
                    prompt_tokens=getattr(getattr(response, "usage", None), "prompt_tokens", None),
                    completion_tokens=getattr(getattr(response, "usage", None), "completion_tokens", None),
                )

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
        role: str = "unknown",
    ) -> str:
        """
        Send a chat completion request.

        Args:
            messages: List of message dicts (role + content)
            json_mode: Request JSON output format
            role: Agent role for metrics tracking (planner/executor/critic/recovery)

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

        return await self._call_with_retry(kwargs, role=role)

    async def complete_with_vision_multi(
        self,
        prompt: str,
        images_b64: list[str],
        system: Optional[str] = None,
        json_mode: bool = False,
        role: str = "annotator",
    ) -> str:
        """
        Send a vision request with multiple base64 images.

        Args:
            prompt: Text prompt describing what to analyze
            images_b64: List of base64-encoded images (PNG/JPEG)
            system: Optional system prompt
            json_mode: Request JSON output format

        Returns:
            The assistant's response text
        """
        messages = []
        if system:
            messages.append({"role": "system", "content": system})

        content: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
        for img_b64 in images_b64:
            content.append({
                "type": "image_url",
                "image_url": {
                    "url": f"data:image/png;base64,{img_b64}",
                    "detail": "high",
                },
            })
        messages.append({"role": "user", "content": content})

        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
        }

        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}

        return await self._call_with_retry(kwargs, role=role)

    async def complete_with_vision(
        self,
        prompt: str,
        image_b64: str,
        system: Optional[str] = None,
        json_mode: bool = False,
        role: str = "annotator",
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

        return await self._call_with_retry(kwargs, role=role)
