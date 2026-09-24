"""
core/payload_builder.py
-----------------------
Builds the API request payload based on model family and test settings.
Pure function - no UI or app dependencies.
"""

from core.config import apply_thinking_config


def build_payload(
    model_name: str,
    question: str,
    thinking_enabled: bool,
    streaming_enabled: bool,
    seed: int = None,
) -> dict:

    payload = {"model": model_name, "messages": [{"role": "user", "content": question}]}

    if seed is not None:
        payload["seed"] = seed

    if streaming_enabled:
        payload["stream"] = True
        payload["stream_options"] = {"include_usage": True}

    model_lower = model_name.lower()

    if thinking_enabled:
        apply_thinking_config(payload, model_lower, question)
    else:
        # Thinking OFF: fast, short, direct answers
        payload["max_tokens"] = 512
        payload["temperature"] = 0.7

    return payload
