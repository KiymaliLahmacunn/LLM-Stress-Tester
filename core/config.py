"""
core/config.py
--------------
Configuration settings for the application.
"""


def apply_thinking_config(payload: dict, model_lower: str, question: str) -> None:
    """Mutates payload in-place to add model-specific thinking/reasoning config."""

    # --- Anthropic Claude ---
    if "claude" in model_lower:
        payload["max_tokens"] = 8000
        payload["thinking"] = {"type": "enabled", "budget_tokens": 5000}

    # --- OpenAI Reasoning Models (o1, o3, o4-mini) ---
    elif any(x in model_lower for x in ["o1", "o3", "o4-mini"]):
        payload["max_completion_tokens"] = 8000
        payload["reasoning_effort"] = "high"

    # --- Qwen3 (native /think tag) ---
    elif "qwen3" in model_lower or "qwen-3" in model_lower:
        payload["max_tokens"] = 8000
        payload["temperature"] = 0.6
        payload["messages"][-1]["content"] = "/think\n" + question

    # --- Qwen2 / Qwen2.5 / QwQ ---
    elif "qwen" in model_lower or "qwq" in model_lower:
        payload["max_tokens"] = 6000
        payload["temperature"] = 0.4
        payload["messages"].insert(
            0,
            {
                "role": "system",
                "content": (
                    "You are an expert reasoning assistant. Before giving your final answer, "
                    "work through the problem carefully step by step inside "
                    "<thinking>...</thinking> tags, then provide your final answer."
                ),
            },
        )

    # --- DeepSeek-R1 / DeepSeek-R1-Distill ---
    elif "deepseek" in model_lower and (
        "r1" in model_lower or "reasoner" in model_lower
    ):
        payload["max_tokens"] = 8000
        payload["temperature"] = 0.6
        payload["messages"].insert(
            0,
            {
                "role": "system",
                "content": "Please reason step by step and put your thinking in <think>...</think> tags.",
            },
        )

    # --- Gemma 3 / Gemma 2 ---
    elif "gemma" in model_lower:
        payload["max_tokens"] = 6000
        payload["temperature"] = 1.0
        payload["top_p"] = 0.95
        payload["messages"].insert(
            0,
            {
                "role": "system",
                "content": "Think step by step before answering. Structure your reasoning clearly.",
            },
        )

    # --- Phi-4 / Phi-3.5 / Phi-3 ---
    elif "phi" in model_lower:
        payload["max_tokens"] = 6000
        payload["temperature"] = 0.8
        payload["messages"].insert(
            0,
            {
                "role": "system",
                "content": "You are a careful reasoning assistant. Think through the problem step by step before providing your answer.",
            },
        )

    # --- Llama 3 / 3.1 / 3.2 / 3.3 ---
    elif "llama" in model_lower:
        payload["max_tokens"] = 6000
        payload["temperature"] = 0.6
        payload["messages"].insert(
            0,
            {
                "role": "system",
                "content": "Think through the problem step by step before answering. Show your reasoning clearly.",
            },
        )

    # --- Mistral / Mixtral ---
    elif "mistral" in model_lower or "mixtral" in model_lower:
        payload["max_tokens"] = 6000
        payload["temperature"] = 0.7
        payload["messages"].insert(
            0,
            {
                "role": "system",
                "content": "Reason step by step before giving your final answer.",
            },
        )

    # --- Generic fallback ---
    else:
        payload["max_tokens"] = 6000
        payload["temperature"] = 0.7
        payload["messages"].insert(
            0,
            {
                "role": "system",
                "content": "Think step by step and reason carefully before answering.",
            },
        )
