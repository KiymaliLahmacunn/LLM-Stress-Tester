import asyncio
import json
import time

import httpx
import tiktoken
from httpx_sse import aconnect_sse

from .payload_builder import build_payload

# Initialize a global tiktoken encoding (cl100k_base is standard for OpenAI)
try:
    TOKENIZER = tiktoken.get_encoding("cl100k_base")
except Exception:
    TOKENIZER = None


def _estimate_tokens(text: str) -> int:
    if not text:
        return 0
    if TOKENIZER:
        return len(TOKENIZER.encode(text))
    return max(1, int(len(text.split()) * 1.3))


def _extract_usage(data: dict) -> dict:
    u = data.get("usage", {})
    return {
        "prompt_tokens": u.get("prompt_tokens", 0),
        "completion_tokens": u.get("completion_tokens", 0),
        "total_tokens": u.get("total_tokens", 0),
        "reasoning_tokens": u.get("completion_tokens_details", {}).get(
            "reasoning_tokens", 0
        ),
        "cached_tokens": u.get("prompt_tokens_details", {}).get("cached_tokens", 0),
    }


def _extract_metadata(data: dict) -> dict:
    return {
        "response_id": data.get("id", ""),
        "object_type": data.get("object", ""),
        "created_unix": data.get("created", ""),
        "model_returned": data.get("model", ""),
        "system_fingerprint": data.get("system_fingerprint", ""),
        "service_tier": data.get("service_tier", ""),
    }


def _write_log(path: str, text: str):
    if not path:
        return
    try:
        with open(path, "a", encoding="utf-8") as f:
            f.write(text)
    except Exception:
        pass


async def _streaming_request(
    app_ref,
    client: httpx.AsyncClient,
    url: str,
    payload: dict,
    timeout: int,
    sent_epoch_ms: float,
    log_path: str,
    is_warmup: bool,
) -> dict:
    first_token_epoch_ms = 0.0
    first_token_received = False
    chunk_count = 0
    full_content = []

    # Metadata
    meta = {
        "response_id": "",
        "object_type": "",
        "created_unix": "",
        "model_returned": "",
        "system_fingerprint": "",
        "service_tier": "",
        "finish_reason": "",
        "choice_index": 0,
    }
    usage_data = {}
    http_status = 0
    status = ""
    content_str = ""

    try:
        async with aconnect_sse(
            client, "POST", url, json=payload, timeout=float(timeout)
        ) as event_source:
            http_status = event_source.response.status_code
            if http_status != 200:
                finished_epoch_ms = time.time() * 1000
                first_token_epoch_ms = finished_epoch_ms
                status = f"Error {http_status}"
                content_str = str(await event_source.response.aread())[:1000]
                token_info = _extract_usage({})
                _write_log(log_path, f"  HTTP {http_status}: {content_str[:300]}\n")
            else:
                async for sse in event_source.aiter_sse():
                    if sse.data == "[DONE]":
                        continue

                    if not first_token_received:
                        first_token_epoch_ms = time.time() * 1000
                        first_token_received = True

                    chunk_count += 1
                    try:
                        data = json.loads(sse.data)

                        if not meta["response_id"]:
                            meta["response_id"] = data.get("id", "")
                        if not meta["object_type"]:
                            meta["object_type"] = data.get("object", "")
                        if not meta["created_unix"]:
                            meta["created_unix"] = data.get("created", "")
                        if not meta["model_returned"]:
                            meta["model_returned"] = data.get("model", "")
                        if not meta["system_fingerprint"]:
                            meta["system_fingerprint"] = data.get(
                                "system_fingerprint", ""
                            )
                        if not meta["service_tier"]:
                            meta["service_tier"] = data.get("service_tier", "")

                        choices = data.get("choices")
                        if choices and len(choices) > 0:
                            choice = choices[0]
                            meta["choice_index"] = choice.get("index", 0)
                            fr = choice.get("finish_reason")
                            if fr:
                                meta["finish_reason"] = fr

                            delta = choice.get("delta", {})
                            content_piece = delta.get("content", "")
                            if content_piece:
                                full_content.append(content_piece)
                                if not is_warmup:
                                    with app_ref.log_lock:
                                        app_ref.stat_tokens += 1
                        elif data.get("type") == "content_block_delta" and "delta" in data:
                            content_piece = data["delta"].get("text", "")
                            if content_piece:
                                full_content.append(content_piece)
                                if not is_warmup:
                                    with app_ref.log_lock:
                                        app_ref.stat_tokens += 1
                        elif "candidates" in data and len(data["candidates"]) > 0:
                            content_piece = data["candidates"][0].get("content", {}).get("parts", [{}])[0].get("text", "")
                            if content_piece:
                                full_content.append(content_piece)
                                if not is_warmup:
                                    with app_ref.log_lock:
                                        app_ref.stat_tokens += 1

                        u = data.get("usage")
                        if u:
                            usage_data = u
                    except Exception:
                        pass

                finished_epoch_ms = time.time() * 1000
                if not first_token_received:
                    first_token_epoch_ms = finished_epoch_ms

                token_info = _extract_usage({"usage": usage_data})
                content_str = "".join(full_content)

                # Fallbacks with tiktoken
                if token_info["prompt_tokens"] == 0:
                    token_info["prompt_tokens"] = _estimate_tokens(str(payload))
                if token_info["completion_tokens"] == 0:
                    token_info["completion_tokens"] = _estimate_tokens(content_str)
                if token_info["total_tokens"] == 0:
                    token_info["total_tokens"] = (
                        token_info["prompt_tokens"] + token_info["completion_tokens"]
                    )

                if chunk_count == 0:
                    status = "Error: Invalid API Response (No stream chunks)"
                    if len(content_str) == 0:
                        content_str = "(API returned HTTP 200 but did not send any Server-Sent Events)"
                else:
                    status = "Success"
    except Exception as e:
        finished_epoch_ms = time.time() * 1000
        if not first_token_received:
            first_token_epoch_ms = finished_epoch_ms
        http_status = 500
        status = f"Request Failed: {str(e)}"
        token_info = _extract_usage({})
        _write_log(log_path, f"  Exception: {str(e)}\n")

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
        **meta,
        **token_info,
        "tps": tps,
        "response_content": content_str,
        "sse_chunks": chunk_count,
    }


async def _blocking_request(
    client: httpx.AsyncClient,
    url: str,
    payload: dict,
    timeout: int,
    sent_epoch_ms: float,
    log_path: str,
) -> dict:
    try:
        response = await client.post(url, json=payload, timeout=float(timeout))
        finished_epoch_ms = time.time() * 1000
        http_status = response.status_code
        content_str = ""

        if http_status == 200:
            data = response.json()
            meta = _extract_metadata(data)
            token_info = _extract_usage(data)

            choices = data.get("choices")
            if choices and len(choices) > 0:
                try:
                    content_str = choices[0].get("message", {}).get("content", "") or ""
                except Exception:
                    pass
            elif "content" in data and isinstance(data["content"], list):
                try:
                    content_str = data["content"][0].get("text", "")
                except Exception:
                    pass
            elif "candidates" in data and len(data["candidates"]) > 0:
                try:
                    content_str = data["candidates"][0].get("content", {}).get("parts", [{}])[0].get("text", "")
                except Exception:
                    pass

            if token_info["prompt_tokens"] == 0:
                token_info["prompt_tokens"] = _estimate_tokens(str(payload))
            if token_info["completion_tokens"] == 0:
                token_info["completion_tokens"] = _estimate_tokens(content_str)
            if token_info["total_tokens"] == 0:
                token_info["total_tokens"] = (
                    token_info["prompt_tokens"] + token_info["completion_tokens"]
                )

            status = "Success"
        else:
            status = f"Error {http_status}"
            content_str = response.text[:1000]
            meta = _extract_metadata({})
            token_info = _extract_usage({})
            _write_log(log_path, f"  HTTP {http_status}: {response.text[:300]}\n")
    except Exception as e:
        finished_epoch_ms = time.time() * 1000
        http_status = 500
        status = f"Request Failed: {str(e)}"
        content_str = ""
        meta = _extract_metadata({})
        token_info = _extract_usage({})
        _write_log(log_path, f"  Exception: {str(e)}\n")

    total_ms = finished_epoch_ms - sent_epoch_ms
    ttft_ms = total_ms
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


async def async_worker_task(
    app_ref,
    task_id: int,
    client: httpx.AsyncClient,
    delay: float,
    thinking_enabled: bool,
    streaming_enabled: bool,
):
    log_path = getattr(app_ref, "debug_log_path", "")
    test_seed = getattr(app_ref, "seed", None)
    req_timeout = getattr(app_ref, "timeout", 30)

    try:
        while app_ref.is_testing:
            loop_start = time.time()

            if not app_ref.active_dataset:
                await asyncio.sleep(0.5)
                continue

            with app_ref.rng_lock:
                question_obj = app_ref.rng.choice(app_ref.active_dataset)
                app_ref.task_assigned_counter += 1
                assigned_task_id = app_ref.task_assigned_counter

            q_id = question_obj.get("id", "Unknown")
            q_text = question_obj.get("q", "")

            payload = build_payload(
                provider=getattr(app_ref, "provider", "openai"),
                model_name=app_ref.selected_model_name,
                question=q_text,
                thinking_enabled=thinking_enabled,
                streaming_enabled=streaming_enabled,
            )
            if test_seed is not None:
                payload["seed"] = test_seed

            ts_str = time.strftime("%H:%M:%S")
            thread_tag = f"Tsk-{task_id:03d}"

            lm_studio_log = (
                f"\n[{ts_str}] --- REQUEST [{thread_tag}] ---\n"
                f"URL: {app_ref.selected_model_url}\n"
                f"Model: {app_ref.selected_model_name} | Dataset Task ID: {q_id} | Seed: {test_seed}\n"
                f"PROMPT:\n{q_text}\n"
            )

            is_warmup = getattr(app_ref, "in_warmup_phase", False)

            sent_epoch_ms = time.time() * 1000
            if streaming_enabled:
                result = await _streaming_request(
                    app_ref,
                    client,
                    app_ref.selected_model_url,
                    payload,
                    req_timeout,
                    sent_epoch_ms,
                    log_path,
                    is_warmup,
                )
            else:
                result = await _blocking_request(
                    client,
                    app_ref.selected_model_url,
                    payload,
                    req_timeout,
                    sent_epoch_ms,
                    log_path,
                )

            status = result["status"]
            response_content = result["response_content"]
            ttft_ms = result["ttft_ms"]
            generation_ms = result["generation_ms"]
            total_ms = result["total_ms"]
            tps = result["tps"]
            prompt_tokens = result["prompt_tokens"]
            completion_tokens = result["completion_tokens"]

            if not is_warmup:
                with app_ref.csv_lock:
                    import csv

                    with open(
                        app_ref.csv_filename, mode="a", newline="", encoding="utf-8-sig"
                    ) as f:
                        writer = csv.writer(f, delimiter=";")
                        writer.writerow(
                            [
                                assigned_task_id,
                                q_id,
                                app_ref.selected_model_name,
                                sent_epoch_ms,
                                result["first_token_epoch_ms"],
                                result["finished_epoch_ms"],
                                status,
                                round(ttft_ms, 2),
                                round(generation_ms, 2),
                                round(total_ms, 2),
                                round(tps, 2),
                                prompt_tokens,
                                completion_tokens,
                                result["total_tokens"],
                                result.get("reasoning_tokens", ""),
                                result.get("cached_tokens", ""),
                            ]
                        )

                with app_ref.log_lock:
                    if status == "Success":
                        app_ref.stat_success += 1
                        app_ref.stat_ttft.append(ttft_ms)
                        app_ref.stat_gen.append(generation_ms)
                        app_ref.stat_total.append(total_ms)
                    else:
                        app_ref.stat_failed += 1

            _UI_RESPONSE_LIMIT = 300
            truncated = response_content[:_UI_RESPONSE_LIMIT]
            if len(response_content) > _UI_RESPONSE_LIMIT:
                truncated += f"\n... ({len(response_content) - _UI_RESPONSE_LIMIT} chars truncated)"

            fake_json = {"role": "assistant", "content": truncated}
            if result.get("reasoning_tokens", 0) > 0:
                fake_json["reasoning_tokens"] = result["reasoning_tokens"]

            p_ms_pt = (ttft_ms / prompt_tokens) if prompt_tokens > 0 else 0
            p_tps = (prompt_tokens / (ttft_ms / 1000.0)) if ttft_ms > 0 else 0
            g_ms_pt = (
                (generation_ms / completion_tokens) if completion_tokens > 0 else 0
            )

            lm_studio_log += (
                f"--- RESPONSE [{thread_tag}] (Status: {status}) ---\n"
                f"ID: {result.get('response_id', '')} | Model: {result.get('model_returned', '')}\n"
                f"Finish: {result.get('finish_reason', '')} | Fingerprint: {result.get('system_fingerprint', '')}\n"
                f"Tokens: prompt={prompt_tokens} completion={completion_tokens} "
                f"reasoning={result.get('reasoning_tokens', '')} cached={result.get('cached_tokens', '')}\n"
                f"TTFT={ttft_ms:.1f}ms | GenTime={generation_ms:.1f}ms | Total={total_ms:.1f}ms | TPS={tps:.2f}\n"
                f"{response_content}\n\n"
            )

            import json as _json

            lm_studio_log += f"Message Content:\n{_json.dumps(fake_json, indent=2, ensure_ascii=False)}\n"
            lm_studio_log += f"{ts_str} [DEBUG] prompt eval time = {ttft_ms:.2f} ms / {prompt_tokens} tokens ({p_ms_pt:.2f} ms per token, {p_tps:.2f} tokens per second)\n"
            lm_studio_log += f"{ts_str} [DEBUG] eval time = {generation_ms:.2f} ms / {completion_tokens} tokens ({g_ms_pt:.2f} ms per token, {tps:.2f} tokens per second)\n"
            lm_studio_log += f"{ts_str} [DEBUG] total time = {total_ms:.2f} ms / {result.get('total_tokens', 0)} tokens\n"
            lm_studio_log += "-" * 50 + "\n"

            _write_log(log_path, lm_studio_log)

            if not is_warmup:
                with app_ref.log_lock:
                    prefix = (
                        "[SUCCESS]"
                        if status == "Success"
                        else f"[FAILED {result['http_status']}]"
                    )
                    app_ref.logs.append(
                        f"{prefix} Task {assigned_task_id} (Dataset ID: {q_id}) | TTFT: {ttft_ms:.0f}ms | TPS: {tps:.1f}"
                    )
                    if len(app_ref.logs) > app_ref.max_logs:
                        app_ref.logs = app_ref.logs[-app_ref.max_logs :]

            elapsed = time.time() - loop_start
            if delay > elapsed:
                await asyncio.sleep(delay - elapsed)
            else:
                await asyncio.sleep(0.01)  # Yield control
    except asyncio.CancelledError:
        pass
    except Exception as e:
        _write_log(log_path, f"Fatal Worker Error: {str(e)}\n")
