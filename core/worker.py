"""
core/worker.py
--------------
Worker thread logic: picks questions, sends HTTP requests,
collects ALL possible metrics from the OpenAI-compatible API response,
and logs everything to both UI terminal and disk.

Uses requests.Session for TCP connection reuse (critical for high concurrency).

Based on the full OpenAI Chat Completions API specification:
https://platform.openai.com/docs/api-reference/chat/create
"""

import csv
import datetime
import json
import os
import threading
import time

import requests

from core.payload_builder import build_payload

# Max chars of response content to show in UI terminal (full text goes to disk log)
_UI_RESPONSE_LIMIT = 500

# ---------------------------------------------------------------------------
# CSV column headers — every field extractable from the OpenAI-compatible API
# ---------------------------------------------------------------------------
CSV_HEADERS = [
    # === Identity & Timing ===
    "Task ID",  # 1  - Sequential request number
    "Worker",  # 2  - Thread name (Worker-00...)
    "Sent At",  # 3  - ISO timestamp when request was sent
    "Sent At (epoch ms)",  # 4  - Epoch ms when request was sent
    "First Token At (epoch ms)",  # 5  - Epoch ms when first token arrived
    "Finished At (epoch ms)",  # 6  - Epoch ms when response completed
    "TTFT (ms)",  # 7  - Time to First Token in ms
    "Generation Time (ms)",  # 8  - First token → last token duration
    "Total Duration (ms)",  # 9  - Full request→response duration
    # === HTTP & API Response Metadata ===
    "HTTP Status",  # 10 - HTTP status code (200, 500, etc.)
    "Status",  # 11 - Success / Error / Exception
    "Response ID",  # 12 - API response id (chatcmpl-xxxx)
    "Object Type",  # 13 - chat.completion / chat.completion.chunk
    "Created (unix)",  # 14 - Unix timestamp from API (server-side)
    "Model (Returned)",  # 15 - Model string returned by API
    "System Fingerprint",  # 16 - Backend config fingerprint
    "Service Tier",  # 17 - default / priority / flex (if available)
    # === Choice Details ===
    "Finish Reason",  # 18 - stop / length / content_filter / tool_calls
    "Choice Index",  # 19 - Index of the choice (usually 0)
    # === Token Usage ===
    "Prompt Tokens",  # 20 - Input token count
    "Completion Tokens",  # 21 - Output token count
    "Total Tokens",  # 22 - Total tokens (prompt + completion)
    # === Token Details (if available) ===
    "Reasoning Tokens",  # 23 - CoT/thinking tokens
    "Cached Tokens",  # 24 - Cached prompt tokens (KV cache)
    "Audio Tokens (Prompt)",  # 25 - Audio input tokens
    "Audio Tokens (Completion)",  # 26 - Audio output tokens
    "Accepted Prediction Tokens",  # 27 - Speculative decoding accepted
    "Rejected Prediction Tokens",  # 28 - Speculative decoding rejected
    # === Computed Metrics ===
    "TPS (tok/s)",  # 29 - Completion tokens per second
    "Dataset Task ID",  # 30 - Original ID from the user dataset
    # === Content Stats ===
    "Prompt Chars",  # 31 - Character count of question
    "Response Chars",  # 32 - Character count of response
    "Response Words",  # 33 - Word count of response
    "SSE Chunks",  # 34 - Number of streaming chunks (0 for blocking)
    # === Full Content ===
    "Question Text",  # 35 - Full question text
    "Response Text",  # 36 - Full response text
    # === Additional Info ===
    "Seed",  # 37 - Random seed used
    "Dataset Name",  # 38 - Dataset used
]


def worker_loop(app_ref, delay: float, thinking_enabled: bool, streaming_enabled: bool):
    """
    Main worker loop  runs in its own thread until app_ref.is_testing goes False.
    """

    http_headers = {"Content-Type": "application/json"}

    # Optional API key for external LLM endpoints
    api_key = (
        os.getenv("OPENAI_API_KEY")
        or os.getenv("API_KEY")
        or os.getenv("ANTHROPIC_API_KEY")
    )
    if api_key:
        http_headers["Authorization"] = f"Bearer {api_key}"

    log_path = getattr(
        app_ref,
        "debug_log_path",
        os.path.normpath(
            os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "debug.log")
        ),
    )

    session = requests.Session()
    session.headers.update(http_headers)

    thread_name = threading.current_thread().name
    thread_tag = thread_name[-7:]

    dataset_name = os.path.basename(getattr(app_ref, "active_dataset_path", "Unknown"))
    test_seed = getattr(app_ref, "seed", None)
    req_timeout = getattr(app_ref, "timeout", 30)

    try:
        while app_ref.is_testing:
            loop_start = time.time()

            # --- Pick a question ---
            if not app_ref.active_dataset:
                time.sleep(0.5)
                continue

            with app_ref.rng_lock:
                question_obj = app_ref.rng.choice(app_ref.active_dataset)
                app_ref.task_assigned_counter += 1
                assigned_task_id = app_ref.task_assigned_counter

            q_id = question_obj.get("id", "Unknown")
            q_text = question_obj.get("q", "")

            payload = build_payload(
                model_name=app_ref.selected_model_name,
                question=q_text,
                thinking_enabled=thinking_enabled,
                streaming_enabled=streaming_enabled,
                seed=test_seed,
            )

            # --- Log outgoing prompt ---
            ts_str = datetime.datetime.now().strftime("%H:%M:%S")
            try:
                app_ref.add_log(
                    f"[{ts_str}] [{thread_tag}] >>> PROMPT (ID: {q_id}):\n{q_text}"
                )
            except Exception:
                pass

            _write_log(
                log_path,
                (
                    f"\n[{ts_str}] --- REQUEST [{thread_tag}] ---\n"
                    f"URL: {app_ref.selected_model_url}\n"
                    f"Model: {app_ref.selected_model_name} | Dataset Task ID: {q_id} | Seed: {test_seed}\n"
                    f"PROMPT:\n{q_text}\n"
                ),
            )

            # --- Execute request ---
            sent_epoch_ms = time.time() * 1000
            sent_at_iso = datetime.datetime.now().isoformat(timespec="milliseconds")

            try:
                if streaming_enabled:
                    result = _streaming_request(
                        app_ref=app_ref,
                        session=session,
                        url=app_ref.selected_model_url,
                        payload=payload,
                        timeout=req_timeout,
                        sent_epoch_ms=sent_epoch_ms,
                        log_path=log_path,
                    )
                else:
                    result = _blocking_request(
                        session=session,
                        url=app_ref.selected_model_url,
                        payload=payload,
                        timeout=req_timeout,
                        sent_epoch_ms=sent_epoch_ms,
                        log_path=log_path,
                    )
            except Exception as e:
                finished_epoch_ms = time.time() * 1000
                total_ms = finished_epoch_ms - sent_epoch_ms
                result = _empty_result(
                    status=f"Exception: {e}",
                    finished_epoch_ms=finished_epoch_ms,
                    total_ms=total_ms,
                    response_content=f"Error: {e}",
                )
                _write_log(log_path, f"  CRITICAL ERR: {e}\n")

            # --- Extract key metrics for logging ---
            response_content = result.get("response_content", "")
            status = result.get("status", "Unknown")
            prompt_tokens = result.get("prompt_tokens", 0)
            completion_tokens = result.get("completion_tokens", 0)
            ttft_ms = result.get("ttft_ms", 0.0)
            total_ms = result.get("total_ms", 0.0)
            tps = result.get("tps", 0.0)

            response_id = result.get("response_id", "")
            object_type = result.get("object_type", "")
            created_unix = result.get("created_unix", "")
            model_returned = result.get("model_returned", "")
            choice_index = result.get("choice_index", 0)
            finish_reason = result.get("finish_reason", "")
            generation_ms = result.get("generation_ms", 0.0)

            # --- Log response ---
            ts_str = datetime.datetime.now().strftime("%H:%M:%S")
            try:
                truncated = response_content[:_UI_RESPONSE_LIMIT]
                if len(response_content) > _UI_RESPONSE_LIMIT:
                    truncated += f"\n... ({len(response_content) - _UI_RESPONSE_LIMIT} chars truncated)"
                # Reconstruct fake json
                fake_json = {
                    "id": response_id,
                    "object": object_type,
                    "created": created_unix,
                    "model": model_returned,
                    "choices": [
                        {
                            "index": choice_index,
                            "message": {
                                "role": "assistant",
                                "content": response_content[:_UI_RESPONSE_LIMIT]
                                + (
                                    "..."
                                    if len(response_content) > _UI_RESPONSE_LIMIT
                                    else ""
                                ),
                            },
                            "finish_reason": finish_reason,
                        }
                    ],
                    "usage": {
                        "prompt_tokens": prompt_tokens,
                        "completion_tokens": completion_tokens,
                        "total_tokens": result.get("total_tokens", 0),
                    },
                }

                ts_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                p_ms_pt = ttft_ms / prompt_tokens if prompt_tokens else 0
                p_tps = (prompt_tokens / (ttft_ms / 1000.0)) if ttft_ms > 0 else 0
                g_ms_pt = generation_ms / completion_tokens if completion_tokens else 0

                lm_studio_log = f"--- RESPONSE [{thread_tag}] (Status: {status}) ---\n"
                lm_studio_log += f"{ts_str} [DEBUG] prompt eval time = {ttft_ms:.2f} ms / {prompt_tokens} tokens ({p_ms_pt:.2f} ms per token, {p_tps:.2f} tokens per second)\n"
                lm_studio_log += f"{ts_str} [DEBUG] eval time = {generation_ms:.2f} ms / {completion_tokens} tokens ({g_ms_pt:.2f} ms per token, {tps:.2f} tokens per second)\n"
                lm_studio_log += f"{ts_str} [DEBUG] total time = {total_ms:.2f} ms / {result.get('total_tokens', 0)} tokens\n"

                import json as _json

                jstr = _json.dumps(fake_json, indent=2)
                lm_studio_log += (
                    f"{ts_str} [INFO] [{model_returned}] Generated prediction: \n{jstr}"
                )

                app_ref.add_log(lm_studio_log)
            except Exception:
                pass

            _write_log(
                log_path,
                (
                    f"--- RESPONSE [{thread_tag}] (Status: {status}) ---\n"
                    f"ID: {result.get('response_id', '')} | Model: {result.get('model_returned', '')}\n"
                    f"Finish: {result.get('finish_reason', '')} | Fingerprint: {result.get('system_fingerprint', '')}\n"
                    f"Tokens: prompt={prompt_tokens} completion={completion_tokens} "
                    f"reasoning={result.get('reasoning_tokens', '')} cached={result.get('cached_tokens', '')}\n"
                    f"TTFT={ttft_ms:.1f}ms | GenTime={result.get('generation_ms', 0):.1f}ms | "
                    f"Total={total_ms:.1f}ms | TPS={tps:.2f}\n"
                    f"{response_content}\n\n"
                ),
            )

            # --- Write 38-column CSV row (thread-safe) ---
            with app_ref.csv_lock:
                app_ref.task_counter += 1

                row = [
                    # Identity & Timing
                    assigned_task_id,  # 1
                    thread_name,  # 2
                    sent_at_iso,  # 3
                    f"{sent_epoch_ms:.0f}",  # 4
                    f"{result.get('first_token_epoch_ms', 0):.0f}",  # 5
                    f"{result.get('finished_epoch_ms', 0):.0f}",  # 6
                    _fmt(ttft_ms),  # 7
                    _fmt(result.get("generation_ms", 0)),  # 8
                    _fmt(total_ms),  # 9
                    # HTTP & Metadata
                    result.get("http_status", 0),  # 10
                    status,  # 11
                    result.get("response_id", ""),  # 12
                    result.get("object_type", ""),  # 13
                    result.get("created_unix", ""),  # 14
                    result.get("model_returned", ""),  # 15
                    result.get("system_fingerprint", ""),  # 16
                    result.get("service_tier", ""),  # 17
                    # Choice
                    result.get("finish_reason", ""),  # 18
                    result.get("choice_index", 0),  # 19
                    # Token Usage
                    prompt_tokens,  # 20
                    completion_tokens,  # 21
                    result.get("total_tokens", 0),  # 22
                    # Token Details
                    result.get("reasoning_tokens", ""),  # 23
                    result.get("cached_tokens", ""),  # 24
                    result.get("audio_tokens_prompt", ""),  # 25
                    result.get("audio_tokens_completion", ""),  # 26
                    result.get("accepted_prediction_tokens", ""),  # 27
                    result.get("rejected_prediction_tokens", ""),  # 28
                    # Computed
                    _fmt(tps),  # 29
                    q_id,  # 30
                    # Content Stats
                    len(q_text),  # 31
                    len(response_content),  # 32
                    len(response_content.split()) if response_content else 0,  # 33
                    result.get("sse_chunks", 0),  # 34
                    # Full Content
                    q_text,  # 35
                    response_content,  # 36
                    # Additional Info
                    str(test_seed) if test_seed is not None else "",  # 37
                    dataset_name,  # 38
                ]

                # --- Update Live Stats ---
                with app_ref.log_lock:
                    app_ref.stat_total.append(total_ms)
                    if status == "Success":
                        app_ref.stat_success += 1
                        app_ref.stat_ttft.append(ttft_ms)
                        app_ref.stat_gen.append(result.get("generation_ms", 0))
                        if not streaming_enabled:
                            app_ref.stat_tokens += completion_tokens
                    else:
                        app_ref.stat_failed += 1

                try:
                    with open(
                        app_ref.csv_filename, mode="a", newline="", encoding="utf-8-sig"
                    ) as f:
                        csv.writer(f, delimiter=";").writerow(row)
                except Exception as file_err:
                    _write_log(log_path, f"  CSV WRITE ERROR: {file_err}\n")

            # --- Sleep to maintain target RPS ---
            elapsed = time.time() - loop_start
            sleep_time = max(0.0, delay - elapsed)
            if sleep_time > 0:
                time.sleep(sleep_time)
    finally:
        session.close()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _fmt(value: float, decimals: int = 2) -> str:
    """Format a float for CSV with comma decimal separator (TR/EU locale)."""
    return f"{value:.{decimals}f}".replace(".", ",")


def _write_log(path: str, text: str):
    """Append text to the debug log file. Silently ignores errors."""
    try:
        with open(path, "a", encoding="utf-8") as f:
            f.write(text)
    except Exception:
        pass


def _empty_result(**overrides) -> dict:
    """Return a result dict with all fields set to empty/zero, then apply overrides."""
    base = {
        "first_token_epoch_ms": 0,
        "finished_epoch_ms": 0,
        "ttft_ms": 0.0,
        "generation_ms": 0.0,
        "total_ms": 0.0,
        "http_status": 0,
        "status": "Unknown",
        "response_id": "",
        "object_type": "",
        "created_unix": "",
        "model_returned": "",
        "system_fingerprint": "",
        "service_tier": "",
        "finish_reason": "",
        "choice_index": 0,
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
        "reasoning_tokens": "",
        "cached_tokens": "",
        "audio_tokens_prompt": "",
        "audio_tokens_completion": "",
        "accepted_prediction_tokens": "",
        "rejected_prediction_tokens": "",
        "tps": 0.0,
        "response_content": "",
        "sse_chunks": 0,
    }
    base.update(overrides)
    return base


def _extract_usage(data: dict) -> dict:
    """
    Extract ALL token usage fields from an OpenAI-compatible response.
    Handles the full specification:
      - usage.prompt_tokens / completion_tokens / total_tokens
      - usage.prompt_tokens_details.cached_tokens, audio_tokens
      - usage.completion_tokens_details.reasoning_tokens, audio_tokens,
        accepted_prediction_tokens, rejected_prediction_tokens
    """
    usage = data.get("usage") or {}
    result = {
        "prompt_tokens": usage.get("prompt_tokens", 0),
        "completion_tokens": usage.get("completion_tokens", 0),
        "total_tokens": usage.get("total_tokens", 0),
        "reasoning_tokens": "",
        "cached_tokens": "",
        "audio_tokens_prompt": "",
        "audio_tokens_completion": "",
        "accepted_prediction_tokens": "",
        "rejected_prediction_tokens": "",
    }

    # Prompt token details
    pd = usage.get("prompt_tokens_details") or {}
    if pd:
        _set_if_present(result, "cached_tokens", pd, "cached_tokens")
        _set_if_present(result, "audio_tokens_prompt", pd, "audio_tokens")

    # Completion token details
    cd = usage.get("completion_tokens_details") or {}
    if cd:
        _set_if_present(result, "reasoning_tokens", cd, "reasoning_tokens")
        _set_if_present(result, "audio_tokens_completion", cd, "audio_tokens")
        _set_if_present(
            result, "accepted_prediction_tokens", cd, "accepted_prediction_tokens"
        )
        _set_if_present(
            result, "rejected_prediction_tokens", cd, "rejected_prediction_tokens"
        )

    return result


def _set_if_present(target: dict, target_key: str, source: dict, source_key: str):
    """Set target[key] = source[key] only if source has a non-None value."""
    val = source.get(source_key)
    if val is not None:
        target[target_key] = val


def _extract_metadata(data: dict) -> dict:
    """
    Extract ALL metadata from an OpenAI-compatible response object.
    Fields: id, object, created, model, system_fingerprint, service_tier,
            finish_reason, choice_index.
    """
    meta = {
        "response_id": data.get("id", ""),
        "object_type": data.get("object", ""),
        "created_unix": data.get("created", ""),
        "model_returned": data.get("model", ""),
        "system_fingerprint": data.get("system_fingerprint", "") or "",
        "service_tier": data.get("service_tier", "") or "",
        "finish_reason": "",
        "choice_index": 0,
    }

    choices = data.get("choices")
    if choices and len(choices) > 0:
        choice = choices[0]
        meta["finish_reason"] = choice.get("finish_reason", "") or ""
        meta["choice_index"] = choice.get("index", 0)

    return meta


# ---------------------------------------------------------------------------
# Request implementations
# ---------------------------------------------------------------------------


def _streaming_request(
    app_ref,
    session: requests.Session,
    url: str,
    payload: dict,
    timeout: int,
    sent_epoch_ms: float,
    log_path: str,
) -> dict:
    """
    Execute a streaming (SSE) request. Extracts EVERY available field from
    the OpenAI-compatible streaming response chunks.
    """
    # Accumulators
    first_token_epoch_ms = 0.0
    first_token_received = False
    chunk_count = 0
    full_content: list[str] = []

    # Metadata (captured from chunks)
    response_id = ""
    object_type = ""
    created_unix = ""
    model_returned = ""
    system_fingerprint = ""
    service_tier = ""
    finish_reason = ""
    choice_index = 0

    # Usage (from final chunk when stream_options.include_usage=true)
    usage_data = {}

    response = session.post(url, json=payload, timeout=timeout, stream=True)
    http_status = response.status_code

    try:
        if response.status_code == 200:
            for line in response.iter_lines():
                if not app_ref.is_testing:
                    break
                if not line:
                    continue

                decoded = line.decode("utf-8")

                if not decoded.startswith("data: "):
                    if (
                        len(decoded.strip()) > 0
                        and chunk_count == 0
                        and not decoded.startswith(":")
                    ):
                        # Capture potential error JSON sent instead of SSE
                        full_content.append(decoded + "\n")
                    continue

                json_str = decoded[6:].strip()
                if json_str == "[DONE]":
                    continue

                # Record first token time
                if not first_token_received:
                    first_token_epoch_ms = time.time() * 1000
                    first_token_received = True

                chunk_count += 1

                try:
                    data = json.loads(json_str)

                    # --- Metadata (update from each chunk, but most are same) ---
                    if not response_id:
                        response_id = data.get("id", "")
                    if not object_type:
                        object_type = data.get("object", "")
                    if not created_unix:
                        created_unix = data.get("created", "")
                    if not model_returned:
                        model_returned = data.get("model", "")
                    if not system_fingerprint:
                        system_fingerprint = data.get("system_fingerprint", "") or ""
                    if not service_tier:
                        service_tier = data.get("service_tier", "") or ""

                    # --- Choice data ---
                    choices = data.get("choices")
                    if choices and len(choices) > 0:
                        choice = choices[0]
                        choice_index = choice.get("index", 0)

                        # finish_reason appears in the last content chunk
                        fr = choice.get("finish_reason")
                        if fr:
                            finish_reason = fr

                        # Delta content
                        delta = choice.get("delta", {})
                        content_piece = delta.get("content", "")
                        if content_piece:
                            full_content.append(content_piece)
                            # LIVE TELEMETRY UPDATE
                            with app_ref.log_lock:
                                app_ref.stat_tokens += 1

                    # --- Usage (final chunk with stream_options) ---
                    u = data.get("usage")
                    if u:
                        usage_data = u

                except json.JSONDecodeError, KeyError, IndexError:
                    pass

            finished_epoch_ms = time.time() * 1000

            if not first_token_received:
                first_token_epoch_ms = finished_epoch_ms

            # Parse usage
            token_info = _extract_usage({"usage": usage_data})

            # Fallback estimation
            if token_info["prompt_tokens"] == 0:
                total_words = sum(
                    len(str(m.get("content", "")).split())
                    for m in payload.get("messages", [])
                )
                token_info["prompt_tokens"] = max(1, int(total_words * 1.3))
            if token_info["completion_tokens"] == 0:
                token_info["completion_tokens"] = max(1, chunk_count - 2)
            if token_info["total_tokens"] == 0:
                token_info["total_tokens"] = (
                    token_info["prompt_tokens"] + token_info["completion_tokens"]
                )

            content_str = "".join(full_content)
            if chunk_count == 0:
                status = "Error: Invalid API Response (No stream chunks)"
                if len(content_str) == 0:
                    content_str = "(API returned HTTP 200 but did not send any Server-Sent Events)"
            else:
                status = "Success"
        else:
            finished_epoch_ms = time.time() * 1000
            first_token_epoch_ms = finished_epoch_ms
            status = f"Error {response.status_code}"
            content_str = response.text[:1000]
            token_info = _extract_usage({})
            _write_log(
                log_path, f"  HTTP {response.status_code}: {response.text[:300]}\n"
            )
    finally:
        response.close()

    # --- Computed metrics ---
    ttft_ms = first_token_epoch_ms - sent_epoch_ms
    total_ms = finished_epoch_ms - sent_epoch_ms
    generation_ms = max(0.0, finished_epoch_ms - first_token_epoch_ms)
    gen_seconds = generation_ms / 1000.0
    tps = token_info["completion_tokens"] / gen_seconds if gen_seconds > 0.001 else 0.0

    return {
        "first_token_epoch_ms": first_token_epoch_ms,
        "finished_epoch_ms": finished_epoch_ms,
        "ttft_ms": ttft_ms,
        "generation_ms": generation_ms,
        "total_ms": total_ms,
        "http_status": http_status,
        "status": status,
        "response_id": response_id,
        "object_type": object_type,
        "created_unix": created_unix,
        "model_returned": model_returned,
        "system_fingerprint": system_fingerprint,
        "service_tier": service_tier,
        "finish_reason": finish_reason,
        "choice_index": choice_index,
        **token_info,
        "tps": tps,
        "response_content": content_str,
        "sse_chunks": chunk_count,
    }


def _blocking_request(
    session: requests.Session,
    url: str,
    payload: dict,
    timeout: int,
    sent_epoch_ms: float,
    log_path: str,
) -> dict:
    """
    Execute a blocking (non-streaming) request. Extracts EVERY available field.
    """
    response = session.post(url, json=payload, timeout=timeout, stream=False)
    finished_epoch_ms = time.time() * 1000
    http_status = response.status_code

    content_str = ""

    try:
        if response.status_code == 200:
            data = response.json()

            # --- Full metadata extraction ---
            meta = _extract_metadata(data)
            token_info = _extract_usage(data)

            # --- Content ---
            choices = data.get("choices")
            if choices and len(choices) > 0:
                try:
                    content_str = choices[0].get("message", {}).get("content", "") or ""
                except Exception:
                    pass

            # Fallback estimation
            if token_info["prompt_tokens"] == 0:
                total_words = sum(
                    len(str(m.get("content", "")).split())
                    for m in payload.get("messages", [])
                )
                token_info["prompt_tokens"] = max(1, int(total_words * 1.3))
            if token_info["completion_tokens"] == 0:
                token_info["completion_tokens"] = (
                    max(1, int(len(content_str.split()) * 1.3)) if content_str else 1
                )
            if token_info["total_tokens"] == 0:
                token_info["total_tokens"] = (
                    token_info["prompt_tokens"] + token_info["completion_tokens"]
                )

            status = "Success"
        else:
            status = f"Error {response.status_code}"
            content_str = response.text[:1000]
            meta = _extract_metadata({})
            token_info = _extract_usage({})
            _write_log(
                log_path, f"  HTTP {response.status_code}: {response.text[:300]}\n"
            )
    finally:
        response.close()

    # --- Computed metrics ---
    total_ms = finished_epoch_ms - sent_epoch_ms
    ttft_ms = total_ms  # blocking: TTFT == total
    generation_ms = 0.0
    tps = token_info["completion_tokens"] / (total_ms / 1000.0) if total_ms > 1 else 0.0

    return {
        "first_token_epoch_ms": finished_epoch_ms,
        "finished_epoch_ms": finished_epoch_ms,
        "ttft_ms": ttft_ms,
        "generation_ms": generation_ms,
        "total_ms": total_ms,
        "http_status": http_status,
        "status": status,
        **meta,
        **token_info,
        "tps": tps,
        "response_content": content_str,
        "sse_chunks": 0,
    }
