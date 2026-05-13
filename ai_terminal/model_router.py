import json
import re

from .config import DEFAULT_ORCHESTRATOR_MODEL
from .ollama_client import ask_no_stream, build_messages
from .prompts import ORCHESTRATOR_SYSTEM_PROMPT
from .tool_protocol import parse_json_value


FILE_TASK_WORDS = {
    "analyze",
    "check",
    "code",
    "codebase",
    "compare",
    "config",
    "containerize",
    "convert",
    "create",
    "directory",
    "docker",
    "dockerfile",
    "edit",
    "file",
    "files",
    "folder",
    "inspect",
    "project",
    "read",
    "repo",
    "review",
    "rewrite",
    "script",
    "scripts",
    "tree",
    "update",
    "write",
}

FILE_TASK_EXTENSIONS = {
    "bat",
    "cmd",
    "css",
    "dockerfile",
    "env",
    "html",
    "ini",
    "js",
    "json",
    "jsx",
    "md",
    "ps1",
    "py",
    "sh",
    "toml",
    "ts",
    "tsx",
    "txt",
    "yaml",
    "yml",
}


def list_models(get_models):
    models = get_models()

    if models:
        for model in models:
            print(f"- {model}")
    else:
        print("No local Ollama models available.")

    return models


def get_default_orchestrator(models):
    if DEFAULT_ORCHESTRATOR_MODEL in models:
        return DEFAULT_ORCHESTRATOR_MODEL

    return models[0]


def preferred_qwen_model(models):
    qwen_models = [model for model in models if "qwen" in model.lower()]
    if not qwen_models:
        return ""

    coder_models = [model for model in qwen_models if "coder" in model.lower()]
    candidates = coder_models or qwen_models
    return sorted(candidates, key=lambda item: (":latest" not in item, len(item), item.lower()))[0]


def prompt_is_file_task(prompt):
    normalized = prompt.lower()

    if re.search(r"\b[a-z0-9_.-]+\.(?:" + "|".join(FILE_TASK_EXTENSIONS) + r")\b", normalized):
        return True

    return any(re.search(rf"\b{re.escape(word)}\b", normalized) for word in FILE_TASK_WORDS)


def deterministic_model_for_prompt(prompt, available_models):
    if prompt_is_file_task(prompt):
        qwen = preferred_qwen_model(available_models)
        if qwen:
            return {
                "action": "handoff",
                "model": qwen,
                "reason": "file, code, or project work is routed to Qwen by default",
                "handoff_prompt": (
                    "This request involves local files, code, scripts, Docker, or project analysis. "
                    "Use tools to inspect the current directory before answering, and use file-writing "
                    "tools when the user asks you to create or modify files."
                ),
            }

    return None


def choose_model(orchestrator_model, available_models, history):
    last_user = next(
        (item.get("content", "") for item in reversed(history) if item.get("role") == "user"),
        "",
    )
    deterministic = deterministic_model_for_prompt(last_user, available_models)
    if deterministic:
        return deterministic

    model_list = "\n".join(f"- {model}" for model in available_models)
    system_prompt = f"""
{ORCHESTRATOR_SYSTEM_PROMPT}

Available models:
{model_list}
""".strip()

    messages = build_messages(history, system_prompt)
    raw_decision = ask_no_stream(orchestrator_model, messages)

    try:
        decision = parse_json_value(raw_decision)
    except (json.JSONDecodeError, ValueError):
        return {
            "action": "answer",
            "model": orchestrator_model,
            "reason": "orchestrator did not return valid JSON",
            "handoff_prompt": "",
        }

    if not isinstance(decision, dict):
        return {
            "action": "answer",
            "model": orchestrator_model,
            "reason": "orchestrator did not return a JSON object",
            "handoff_prompt": "",
        }

    action = decision.get("action", "answer").lower()
    selected_model = decision.get("model", "")

    if action != "handoff" or selected_model not in available_models:
        decision["action"] = "answer"
        decision["model"] = orchestrator_model

    return decision
