import requests
import json
import time
import subprocess
from pathlib import Path

OLLAMA_URL = "http://localhost:11434/api/chat"
TAGS_URL = "http://localhost:11434/api/tags"
DEFAULT_ORCHESTRATOR_MODEL = "phi4-mini:latest"
WORKSPACE_DIR = Path.cwd()
MAX_FILE_CHARS = 12000
MAX_SEARCH_RESULTS = 50
SKIP_DIRS = {".git", "venv", "__pycache__", ".pytest_cache"}

ORCHESTRATOR_SYSTEM_PROMPT = """
You are the orchestrator for a local Ollama terminal chat.
Your job is to decide whether to answer with the current orchestrator model
or hand off the conversation to a better suited local model.

Return only valid JSON in this exact shape:
{
  "action": "answer" or "handoff",
  "model": "model-name-or-empty-string",
  "reason": "short reason",
  "handoff_prompt": "short instruction for the selected model"
}

Choose "answer" for normal conversation, simple coding help, explanations,
and anything the orchestrator can handle well.
Choose "handoff" when another available model is clearly better suited.
Only hand off to a model from the available model list.
""".strip()


def build_messages(history, system_prompt=None):
    messages = []

    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})

    messages.extend(history)
    return messages


def chat(model, messages, stream=True):
    payload = {
        "model": model,
        "messages": messages,
        "stream": stream
    }

    response = requests.post(OLLAMA_URL, json=payload, stream=stream)
    response.raise_for_status()
    return response


def ask(model, messages):
    start = time.time()
    response_text = ""

    try:
        with chat(model, messages, stream=True) as r:
            final_stats = None

            for line in r.iter_lines():
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
    except KeyboardInterrupt:
        print("\nResponse interrupted.")
        return response_text

    end = time.time()

    print("\n\n--- Stats ---")

    if final_stats:
        prompt_tokens = final_stats.get("prompt_eval_count", 0)
        output_tokens = final_stats.get("eval_count", 0)
        eval_duration_ns = final_stats.get("eval_duration", 0)

        eval_seconds = eval_duration_ns / 1_000_000_000 if eval_duration_ns else 0
        tok_per_sec = output_tokens / eval_seconds if eval_seconds > 0 else 0

        print(f"| Input tokens: {prompt_tokens} | Output tokens: {output_tokens} | Output speed: {tok_per_sec:.2f} tokens/sec | Wall time: {end - start:.2f} sec |")

    return response_text


def ask_no_stream(model, messages):
    try:
        response = chat(model, messages, stream=False)
        data = response.json()
        return data.get("message", {}).get("content", "")
    except requests.HTTPError as error:
        print(f"\nOllama error: {error}")
    except requests.RequestException as error:
        print(f"\nCould not reach Ollama: {error}")
    except json.JSONDecodeError:
        print("\nOllama returned invalid JSON.")

    return ""


def parse_json_object(text):
    text = text.strip()

    if text.startswith("```"):
        lines = text.splitlines()
        text = "\n".join(lines[1:-1]).strip()

    start = text.find("{")
    end = text.rfind("}")

    if start == -1 or end == -1:
        raise json.JSONDecodeError("No JSON object found", text, 0)

    return json.loads(text[start:end + 1])


def choose_model(orchestrator_model, available_models, history):
    model_list = "\n".join(f"- {model}" for model in available_models)
    system_prompt = f"""
{ORCHESTRATOR_SYSTEM_PROMPT}

Available models:
{model_list}
""".strip()

    messages = build_messages(history, system_prompt)
    raw_decision = ask_no_stream(orchestrator_model, messages)

    try:
        decision = parse_json_object(raw_decision)
    except json.JSONDecodeError:
        return {
            "action": "answer",
            "model": orchestrator_model,
            "reason": "orchestrator did not return valid JSON",
            "handoff_prompt": ""
        }

    action = decision.get("action", "answer").lower()
    selected_model = decision.get("model", "")

    if action != "handoff" or selected_model not in available_models:
        decision["action"] = "answer"
        decision["model"] = orchestrator_model

    return decision


def get_models():
    try:
        response = requests.get(TAGS_URL)
        response.raise_for_status()
        data = response.json()
        return [model["name"] for model in data.get("models", [])]
    except requests.RequestException as error:
        print(f"Failed to fetch models: {error}")
    except json.JSONDecodeError:
        print("Failed to parse models from Ollama.")

    return []


def list_models():
    models = get_models()

    if models:
        for model in models:
            print(f"- {model}")
    else:
        print("No models found.")

    return models


def get_default_orchestrator(models):
    if DEFAULT_ORCHESTRATOR_MODEL in models:
        return DEFAULT_ORCHESTRATOR_MODEL

    return models[0]


def resolve_path(raw_path, base_dir):
    return (base_dir / raw_path).resolve()


def should_skip_path(path):
    return any(part in SKIP_DIRS for part in path.parts)


def list_directory(raw_dir=".", base_dir=WORKSPACE_DIR):
    root = resolve_path(raw_dir, base_dir)

    if not root.exists():
        raise FileNotFoundError(f"{raw_dir} does not exist.")

    if not root.is_dir():
        raise ValueError(f"{raw_dir} is not a directory.")

    entries = []

    for path in root.iterdir():
        if should_skip_path(path):
            continue

        suffix = "/" if path.is_dir() else ""
        entries.append(f"{path.name}{suffix}")

    return sorted(entries)


def list_workspace_files(raw_dir=".", base_dir=WORKSPACE_DIR):
    root = resolve_path(raw_dir, base_dir)

    if not root.exists():
        raise FileNotFoundError(f"{raw_dir} does not exist.")

    if not root.is_dir():
        raise ValueError(f"{raw_dir} is not a directory.")

    files = []

    for path in root.rglob("*"):
        if should_skip_path(path):
            continue

        if path.is_file():
            files.append(path.relative_to(root))

    return sorted(files)


def search_workspace(query, raw_dir=".", base_dir=WORKSPACE_DIR):
    root = resolve_path(raw_dir, base_dir)

    if not root.exists():
        raise FileNotFoundError(f"{raw_dir} does not exist.")

    if not root.is_dir():
        raise ValueError(f"{raw_dir} is not a directory.")

    query_lower = query.lower()
    results = []

    for path in root.rglob("*"):
        if should_skip_path(path):
            continue

        relative_path = path.relative_to(root)

        if query_lower in str(relative_path).lower():
            results.append(f"{relative_path}")

        if path.is_file():
            try:
                for line_number, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
                    if query_lower in line.lower():
                        results.append(f"{relative_path}:{line_number}: {line.strip()}")

                    if len(results) >= MAX_SEARCH_RESULTS:
                        return results
            except OSError:
                continue

        if len(results) >= MAX_SEARCH_RESULTS:
            return results

    return results


def read_workspace_file(raw_path, base_dir=WORKSPACE_DIR):
    path = resolve_path(raw_path, base_dir)

    if not path.exists():
        raise FileNotFoundError(f"{raw_path} does not exist.")

    if not path.is_file():
        raise ValueError(f"{raw_path} is not a file.")

    content = path.read_text(encoding="utf-8", errors="replace")

    if len(content) > MAX_FILE_CHARS:
        return content[:MAX_FILE_CHARS] + "\n\n[File truncated for chat context.]"

    return content


def run_terminal_command(command, state):
    command = command.strip()

    if not command:
        print("Usage: !<terminal command>")
        return

    if command.lower() in ["pwd", "cd"]:
        print(state["cwd"])
        return

    if command.lower().startswith("cd "):
        raw_path = command[3:].strip().strip('"')
        next_dir = (state["cwd"] / raw_path).resolve()

        if not next_dir.exists():
            print(f"{next_dir} does not exist.")
            return

        if not next_dir.is_dir():
            print(f"{next_dir} is not a directory.")
            return

        state["cwd"] = next_dir
        print(state["cwd"])
        return

    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command", command],
            cwd=state["cwd"],
            text=True,
            capture_output=True
        )
    except OSError as error:
        print(f"Command failed: {error}")
        return

    if result.stdout:
        print(result.stdout, end="")

    if result.stderr:
        print(result.stderr, end="")

    if result.returncode != 0:
        print(f"Command exited with code {result.returncode}.")


def handle_command(prompt, state):
    command = prompt.lower().strip()

    if command in ["exit", "quit", "/bye"]:
        return "exit"

    if prompt.startswith("!"):
        run_terminal_command(prompt[1:], state)
        return "handled"

    if command == "/models":
        state["models"] = list_models()
        return "handled"

    if command == "/model":
        state["models"] = list_models()
        new_model = input("New active model name: ").strip()

        if new_model:
            state["active_model"] = new_model
            print(f"Switched active model to {new_model}")

        return "handled"

    if command == "/orchestrator":
        state["models"] = list_models()
        new_model = input("New orchestrator model name: ").strip()

        if new_model:
            state["orchestrator_model"] = new_model
            print(f"Switched orchestrator to {new_model}")

        return "handled"

    if command == "/clear":
        state["history"].clear()
        print("Cleared chat history.")
        return "handled"

    if command == "/history":
        for item in state["history"]:
            print(f"{item['role']}: {item['content']}")

        return "handled"

    if command.startswith("/files"):
        raw_dir = prompt[6:].strip() or "."

        try:
            entries = list_directory(raw_dir, state["cwd"])
        except (FileNotFoundError, ValueError) as error:
            print(error)
            return "handled"

        for entry in entries:
            print(f"- {entry}")

        print(f"{len(entries)} item(s).")
        return "handled"

    if command.startswith("/tree"):
        raw_dir = prompt[5:].strip() or "."

        try:
            files = list_workspace_files(raw_dir, state["cwd"])
        except (FileNotFoundError, ValueError) as error:
            print(error)
            return "handled"

        for path in files:
            print(f"- {path}")

        print(f"{len(files)} file(s).")
        return "handled"

    if command.startswith("/search "):
        parts = prompt.split(maxsplit=2)

        if len(parts) < 2:
            print("Usage: /search <text> [directory]")
            return "handled"

        query = parts[1]
        raw_dir = parts[2] if len(parts) == 3 else "."

        try:
            results = search_workspace(query, raw_dir, state["cwd"])
        except (FileNotFoundError, ValueError) as error:
            print(error)
            return "handled"

        for result in results:
            print(result)

        if len(results) == MAX_SEARCH_RESULTS:
            print(f"Showing first {MAX_SEARCH_RESULTS} result(s).")
        else:
            print(f"{len(results)} result(s).")

        return "handled"

    if command.startswith("/read "):
        raw_path = prompt[6:].strip()

        try:
            content = read_workspace_file(raw_path, state["cwd"])
        except (FileNotFoundError, ValueError) as error:
            print(error)
            return "handled"

        resolved_path = resolve_path(raw_path, state["cwd"])

        state["history"].append({
            "role": "system",
            "content": f"File loaded: {resolved_path}\n\n```text\n{content}\n```"
        })
        print(f"Loaded {resolved_path} into chat context.")
        return "handled"

    return None


def main():
    models = list_models()

    if not models:
        return

    try:
        orchestrator_model = get_default_orchestrator(models)

        state = {
            "orchestrator_model": orchestrator_model,
            "active_model": orchestrator_model,
            "models": models,
            "history": [],
            "cwd": WORKSPACE_DIR
        }

        print(f"Using {orchestrator_model} as orchestrator.")
        print("Commands: !<command>, /files [dir], /tree [dir], /search <text> [dir], /read <path>, /model, /orchestrator, /models, /clear, /history, /bye")

        while True:
            prompt = input(f"\n[{state['active_model']}] You: ").strip()

            if not prompt:
                continue

            command_result = handle_command(prompt, state)

            if command_result == "exit":
                break

            if command_result == "handled":
                continue

            state["history"].append({"role": "user", "content": prompt})
            decision = choose_model(
                state["orchestrator_model"],
                state["models"],
                state["history"]
            )

            selected_model = decision["model"]
            state["active_model"] = selected_model

            if decision["action"] == "handoff":
                reason = decision.get("reason", "better suited for this turn")
                print(f"\n[orchestrator -> {selected_model}: {reason}]")
                system_prompt = decision.get("handoff_prompt", "")
            else:
                system_prompt = None

            print(f"\n{selected_model}: ", end="")
            messages = build_messages(state["history"], system_prompt)
            answer = ask(selected_model, messages)

            if answer:
                state["history"].append({"role": "assistant", "content": answer})
    except KeyboardInterrupt:
        print("\nExiting...")


if __name__ == "__main__":
    main()
