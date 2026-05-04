"""LLM communication utilities for the judge system."""

import random
import threading
import time

from openai import OpenAI

from .logger import logger
from .misc_utils import load_env_var

# Retry constants
BASE_DELAY = 1
MAX_DELAY = 60
RATE_LIMIT_DELAY = 30
MAX_ATTEMPTS = 10

# ---- Provider routing ------------------------------------------------------
# `google/*` model slugs are routed to Gemini's OpenAI-compatible endpoint
# using KEYS_GEMINI_KEY; everything else goes to OpenRouter. Clients are
# cached per-provider so the underlying httpx connection pool is reused
# across calls (the OpenAI SDK is thread-safe).

_GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"
_OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
_GEMINI_PREFIX = "google/"

_clients: dict[str, OpenAI] = {}
_clients_lock = threading.Lock()


def is_gemini_model(model: str) -> bool:
    """True iff the model id should be routed to Gemini's API directly."""
    return model.startswith(_GEMINI_PREFIX)


def to_api_model_id(model: str) -> str:
    """Strip provider prefixes the wire endpoint doesn't expect.

    Gemini's compat endpoint takes bare names (`gemini-3-flash-preview`),
    not OpenRouter's `google/...` slug. OpenRouter takes the slug as-is.
    The full slug stays the canonical id everywhere else (cost lookup,
    metrics, DB rows) so only this function strips it.
    """
    if is_gemini_model(model):
        return model[len(_GEMINI_PREFIX) :]
    return model


def get_client(model: str) -> OpenAI:
    """Return a cached OpenAI-compatible client matching the model's provider.

    First call per provider builds the client; subsequent calls are dict
    lookups. Safe to call from worker threads — the lock only contends
    on cold start.
    """
    provider = "gemini" if is_gemini_model(model) else "openrouter"
    client = _clients.get(provider)
    if client is not None:
        return client
    with _clients_lock:
        client = _clients.get(provider)
        if client is not None:
            return client
        if provider == "gemini":
            client = OpenAI(
                base_url=_GEMINI_BASE_URL,
                api_key=load_env_var("KEYS_GEMINI_KEY", required=True),
            )
        else:
            client = OpenAI(
                base_url=_OPENROUTER_BASE_URL,
                api_key=load_env_var("KEYS_OPEN_ROUTER_API_KEY", required=True),
            )
        _clients[provider] = client
        return client

# Pricing per 1M tokens (input, output) for common OpenRouter models
_MODEL_PRICING = {
    "openai/gpt-4o": (5.0, 15.0),
    "openai/gpt-4o-mini": (0.15, 0.6),
    "openai/gpt-4-turbo": (10.0, 30.0),
    "openai/gpt-4": (30.0, 60.0),
    "anthropic/claude-sonnet-4-5-20250929": (3.0, 15.0),
    "anthropic/claude-3.5-sonnet": (3.0, 15.0),
    "anthropic/claude-3-opus": (15.0, 75.0),
    "google/gemini-pro-1.5": (3.5, 10.5),
    "google/gemini-2.5-pro": (1.25, 10.0),
    "google/gemini-2.5-flash": (0.15, 0.60),
    "google/gemini-2.5-flash-lite": (0.075, 0.30),
    "google/gemini-3-flash-preview": (0.5, 3.0),
    "google/gemini-3.1-pro-preview": (2.0, 12.0),
}
_DEFAULT_PRICING = (10.0, 30.0)  # Fallback pricing per 1M tokens


def calculate_message_size(messages):
    """Calculate the total string length of all messages.

    Returns two counts: one for text-only content and one that also includes
    image_url data (base64 data URIs).

    Args:
        messages: List of message dicts in OpenAI format

    Returns:
        dict: {"text": int, "total": int} where "text" counts only text content
              and "total" also includes image_url data.
    """
    text_size = 0
    image_size = 0
    for msg in messages:
        if isinstance(msg, dict):
            content = msg.get("content", "")
            if isinstance(content, str):
                text_size += len(content)
            elif isinstance(content, list):
                for item in content:
                    if isinstance(item, dict):
                        if "text" in item:
                            text_size += len(item["text"])
                        elif item.get("type") == "image_url":
                            url = item.get("image_url", {}).get("url", "")
                            image_size += len(url)
    return {"text": text_size, "total": text_size + image_size}


def calculate_cost(model, prompt_tokens, completion_tokens):
    """Estimate cost for an LLM call based on model and token counts.

    Args:
        model: Model identifier string
        prompt_tokens: Number of input tokens
        completion_tokens: Number of output tokens

    Returns:
        dict: {"total_cost": float, "prompt_cost": float, "completion_cost": float}
    """
    input_price, output_price = _MODEL_PRICING.get(model, _DEFAULT_PRICING)
    prompt_cost = (prompt_tokens / 1_000_000) * input_price
    completion_cost = (completion_tokens / 1_000_000) * output_price
    return {
        "total_cost": prompt_cost + completion_cost,
        "prompt_cost": prompt_cost,
        "completion_cost": completion_cost,
    }


def backoff(attempt):
    """Calculate exponential backoff with full jitter for retry delays."""
    return min(MAX_DELAY, (BASE_DELAY * (2**attempt)) * random.uniform(0.5, 1.5))


def robust_send_message(
    client,
    messages,
    model,
    system_instruction=None,
    response_format=None,
    reasoning_effort=None,
):
    """Send a message to OpenRouter with exponential backoff retry logic.

    Args:
        client: OpenAI-compatible client
        messages: List of message dicts
        model: Model identifier string
        system_instruction: Optional system message to prepend
        response_format: Optional response format dict (e.g., {"type": "json_object"})
        reasoning_effort: Optional reasoning effort level passed verbatim to the
            API (`"low"`, `"medium"`, `"high"`, `"minimal"`, or `"none"`).
            Models without thinking support may reject the kwarg.

    Returns:
        tuple: (response, metrics_dict) where metrics_dict contains:
            - message_size: Character count of text-only content
            - message_size_with_images: Character count including image_url data
            - prompt_tokens: Actual tokens used for prompt
            - completion_tokens: Actual tokens used for completion
            - total_tokens: Total tokens used
    """
    attempt = 0
    while True:
        try:
            api_messages = list(messages)

            if system_instruction:
                if not messages or (
                    isinstance(messages[0], dict)
                    and messages[0].get("role") != "system"
                ):
                    system_msg = {"role": "system", "content": system_instruction}
                    messages.insert(0, system_msg)
                    api_messages.insert(0, system_msg)

            size_info = calculate_message_size(api_messages)

            kwargs = {"model": to_api_model_id(model), "messages": api_messages}
            if response_format:
                kwargs["response_format"] = response_format
            if reasoning_effort is not None:
                kwargs["reasoning_effort"] = reasoning_effort

            response = client.chat.completions.create(**kwargs)

            metrics = {
                "message_size": size_info["text"],
                "message_size_with_images": size_info["total"],
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "total_tokens": 0,
            }

            if hasattr(response, "usage") and response.usage:
                metrics["prompt_tokens"] = response.usage.prompt_tokens
                metrics["completion_tokens"] = response.usage.completion_tokens
                metrics["total_tokens"] = response.usage.total_tokens

            return response, metrics
        except Exception as e:
            error_msg = str(e)

            if "400" in error_msg or "invalid" in error_msg.lower():
                raise

            if attempt >= MAX_ATTEMPTS - 1:
                raise

            delay = backoff(attempt)

            if (
                "429" in error_msg
                or "rate limit" in error_msg.lower()
                or "quota" in error_msg.lower()
            ):
                delay = RATE_LIMIT_DELAY

            import traceback

            traceback.print_exc()
            logger.info(
                f"   Retry {attempt + 1}/{MAX_ATTEMPTS} after {delay:.2f}s due to: {type(e).__name__}"
            )
            time.sleep(delay)
            attempt += 1
