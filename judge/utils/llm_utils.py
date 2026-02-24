"""LLM communication utilities for the judge system."""

import random
import time

from .logger import logger

# Retry constants
BASE_DELAY = 1
MAX_DELAY = 60
RATE_LIMIT_DELAY = 30
MAX_ATTEMPTS = 10

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
    client, messages, model, system_instruction=None, response_format=None
):
    """Send a message to OpenRouter with exponential backoff retry logic.

    Args:
        client: OpenAI-compatible client
        messages: List of message dicts
        model: Model identifier string
        system_instruction: Optional system message to prepend
        response_format: Optional response format dict (e.g., {"type": "json_object"})

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

            kwargs = {"model": model, "messages": api_messages}
            if response_format:
                kwargs["response_format"] = response_format

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

            logger.info(
                f"   Retry {attempt + 1}/{MAX_ATTEMPTS} after {delay:.2f}s due to: {type(e).__name__}"
            )
            time.sleep(delay)
            attempt += 1
