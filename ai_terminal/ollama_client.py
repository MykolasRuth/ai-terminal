import json
import re
import socket
import time
from urllib.parse import urlparse

import requests

from .config import (
    OLLAMA_BASE_URL,
    OLLAMA_CONNECT_TIMEOUT_SECONDS,
    OLLAMA_ORCHESTRATOR_READ_TIMEOUT_SECONDS,
    OLLAMA_READ_TIMEOUT_SECONDS,
    OLLAMA_STREAM_MAX_SECONDS,
    OLLAMA_STREAM_STALL_TIMEOUT_SECONDS,
    OLLAMA_TAGS_TIMEOUT_SECONDS,
    OLLAMA_URL,
    TAGS_URL,
)


def ollama_host_port():
    parsed = urlparse(OLLAMA_BASE_URL)
    host = parsed.hostname or "localhost"
    if parsed.port:
        port = parsed.port
    elif parsed.scheme == "https":
        port = 443
    else:
        port = 80
    return host, port


def ollama_is_reachable():
    host, port = ollama_host_port()
    try:
        with socket.create_connection((host, port), timeout=1):
            return True
    except OSError:
        return False


def build_messages(history, system_prompt=None):
    messages = []

    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})

    messages.extend(history)
    return messages


def chat(model, messages, stream=True, timeout=None):
    payload = {
        "model": model,
        "messages": messages,
        "stream": stream,
    }

    response = requests.post(
        OLLAMA_URL,
        json=payload,
        stream=stream,
        timeout=timeout or (OLLAMA_CONNECT_TIMEOUT_SECONDS, OLLAMA_READ_TIMEOUT_SECONDS),
    )
    response.raise_for_status()
    return response


def ask_stream(model, messages):
    start = time.time()
    response_text = ""

    try:
        with chat(model, messages, stream=True) as response:
            final_stats = None

            for line in response.iter_lines():
                if not line:
                    continue

                data = json.loads(line.decode("utf-8"))

                if "message" in data and "content" in data["message"]:
                    print(data["message"]["content"], end="", flush=True)
                    response_text += data["message"]["content"]

                if data.get("done"):
                    final_stats = data
    except requests.HTTPError as error:
        print(f"\nOllama error: {error}")
        return ""
    except requests.RequestException as error:
        print(f"\nCould not reach Ollama: {error}")
        return ""
    except KeyboardInterrupt:
        print("\nResponse interrupted.")
        return response_text

    print_stats(final_stats, time.time() - start)
    return response_text


def ask_no_stream(model, messages):
    text, _stats = ask_no_stream_with_stats(model, messages)
    return text


def ask_no_stream_with_stats(model, messages):
    start = time.time()

    try:
        response = chat(
            model,
            messages,
            stream=False,
            timeout=(OLLAMA_CONNECT_TIMEOUT_SECONDS, OLLAMA_ORCHESTRATOR_READ_TIMEOUT_SECONDS),
        )
        data = response.json()
        elapsed_seconds = time.time() - start
        return data.get("message", {}).get("content", ""), build_stats(data, elapsed_seconds)
    except requests.HTTPError as error:
        print(f"\nOllama error: {error}")
    except requests.RequestException as error:
        print(f"\nCould not reach Ollama: {error}")
    except json.JSONDecodeError:
        print("\nOllama returned invalid JSON.")

    return "", build_stats({}, time.time() - start)


def remove_thinking_blocks(text):
    cleaned = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL | re.IGNORECASE)
    cleaned = re.sub(r"<think>.*$", "", cleaned, flags=re.DOTALL | re.IGNORECASE)
    return cleaned.strip()


def latest_thinking_text(text):
    closed_blocks = re.findall(r"<think>(.*?)</think>", text, flags=re.DOTALL | re.IGNORECASE)
    if closed_blocks:
        return closed_blocks[-1].strip()

    open_match = re.search(r"<think>(.*)$", text, flags=re.DOTALL | re.IGNORECASE)
    if open_match:
        return open_match.group(1).strip()

    return ""


def stream_timed_out(start, last_progress):
    now = time.time()
    if OLLAMA_STREAM_MAX_SECONDS > 0 and now - start > OLLAMA_STREAM_MAX_SECONDS:
        return "max"
    if OLLAMA_STREAM_STALL_TIMEOUT_SECONDS > 0 and now - last_progress > OLLAMA_STREAM_STALL_TIMEOUT_SECONDS:
        return "stall"
    return ""


def ask_stream_with_stats(model, messages, on_thought=None, cancel_event=None):
    start = time.time()
    last_progress = start
    response_text = ""
    last_thought = ""
    thinking_text = ""

    try:
        with chat(model, messages, stream=True) as response:
            final_stats = None

            for line in response.iter_lines(chunk_size=1):
                if cancel_event is not None and cancel_event.is_set():
                    response.close()
                    return remove_thinking_blocks(response_text), build_stats(final_stats or {}, time.time() - start)

                timeout_reason = stream_timed_out(start, last_progress)
                if timeout_reason:
                    response.close()
                    if timeout_reason == "stall":
                        print(f"\nOllama stream stalled for {OLLAMA_STREAM_STALL_TIMEOUT_SECONDS} seconds.")
                    else:
                        print(f"\nOllama stream exceeded {OLLAMA_STREAM_MAX_SECONDS} seconds.")
                    stats = build_stats(final_stats or {}, time.time() - start)
                    stats["stop_reason"] = f"timeout_{timeout_reason}"
                    return "", stats

                if not line:
                    continue

                data = json.loads(line.decode("utf-8"))
                message = data.get("message", {})
                content = message.get("content", "")
                thinking = message.get("thinking") or data.get("thinking") or ""

                if content:
                    response_text += content
                    last_progress = time.time()

                if thinking:
                    thinking_text += thinking
                    last_progress = time.time()

                thought = thinking_text or latest_thinking_text(response_text)
                if thought and thought != last_thought and on_thought:
                    on_thought(thought)
                    last_thought = thought

                if data.get("done"):
                    final_stats = data
                    last_progress = time.time()

                if cancel_event is not None and cancel_event.is_set():
                    response.close()
                    return remove_thinking_blocks(response_text), build_stats(final_stats or {}, time.time() - start)

    except requests.HTTPError as error:
        print(f"\nOllama error: {error}")
        stats = build_stats({}, time.time() - start)
        stats["stop_reason"] = "http_error"
        return "", stats
    except requests.RequestException as error:
        print(f"\nCould not reach Ollama: {error}")
        stats = build_stats({}, time.time() - start)
        stats["stop_reason"] = "request_error"
        return "", stats
    except json.JSONDecodeError:
        print("\nOllama returned invalid JSON.")
        stats = build_stats({}, time.time() - start)
        stats["stop_reason"] = "invalid_json"
        return "", stats
    except KeyboardInterrupt:
        stats = build_stats({}, time.time() - start)
        stats["stop_reason"] = "keyboard_interrupt"
        return "", stats

    elapsed_seconds = time.time() - start
    stats = build_stats(final_stats or {}, elapsed_seconds)
    stats["stop_reason"] = "done"
    return remove_thinking_blocks(response_text), stats


def build_stats(data, elapsed_seconds):
    input_tokens = data.get("prompt_eval_count", 0)
    output_tokens = data.get("eval_count", 0)
    eval_duration_ns = data.get("eval_duration", 0)
    prompt_eval_duration_ns = data.get("prompt_eval_duration", 0)
    total_duration_ns = data.get("total_duration", 0)

    eval_seconds = eval_duration_ns / 1_000_000_000 if eval_duration_ns else 0
    tokens_per_second = output_tokens / eval_seconds if eval_seconds > 0 else 0

    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "eval_seconds": eval_seconds,
        "prompt_eval_seconds": prompt_eval_duration_ns / 1_000_000_000 if prompt_eval_duration_ns else 0,
        "total_duration_seconds": total_duration_ns / 1_000_000_000 if total_duration_ns else 0,
        "elapsed_seconds": elapsed_seconds,
        "tokens_per_second": tokens_per_second,
    }


def print_stats(final_stats, elapsed_seconds):
    if not final_stats:
        return

    prompt_tokens = final_stats.get("prompt_eval_count", 0)
    output_tokens = final_stats.get("eval_count", 0)
    eval_duration_ns = final_stats.get("eval_duration", 0)
    eval_seconds = eval_duration_ns / 1_000_000_000 if eval_duration_ns else 0
    tok_per_sec = output_tokens / eval_seconds if eval_seconds > 0 else 0

    stats_line = (
        f"Input Tokens: {prompt_tokens} | Output Tokens: {output_tokens} | "
        f"Speed: {tok_per_sec:.2f} t/s | Time: {elapsed_seconds:.2f}s"
    )

    print(f"\n\n[{stats_line}]\n")


def get_models():
    if not ollama_is_reachable():
        print(f"Could not reach Ollama at {OLLAMA_BASE_URL}.")
        print("Start Ollama, then run devserver again.")
        return []

    try:
        response = requests.get(
            TAGS_URL,
            timeout=(OLLAMA_CONNECT_TIMEOUT_SECONDS, OLLAMA_TAGS_TIMEOUT_SECONDS),
        )
        response.raise_for_status()
        data = response.json()
        return [model["name"] for model in data.get("models", [])]
    except requests.ConnectionError:
        print(f"Could not reach Ollama at {OLLAMA_BASE_URL}.")
        print("Start Ollama, then run devserver again.")
    except requests.Timeout:
        print(f"Ollama did not respond within {OLLAMA_TAGS_TIMEOUT_SECONDS} seconds.")
        print("Check that Ollama is running, then run devserver again.")
    except requests.RequestException as error:
        print(f"Failed to fetch models: {error}")
    except OSError as error:
        print(f"Could not reach Ollama: {error}")
    except json.JSONDecodeError:
        print("Failed to parse models from Ollama.")

    return []
