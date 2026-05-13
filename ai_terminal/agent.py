import json
import re
import time
from datetime import datetime
from pathlib import Path
from threading import Event

from .config import MAX_TOOL_CALLS_PER_ROUND, MAX_TOOL_ROUNDS, WORKSPACE_DIR
from .display import EscapeCancelWatcher, WorkingStatus, dim, directory_name, error, print_assistant_message, print_message_stats, success
from .ollama_client import ask_stream_with_stats, build_messages
from .policy import approve_tool_call, classify_tool_call
from .prompts import TOOL_SYSTEM_PROMPT
from .tool_protocol import looks_like_tool_attempt, mentions_tool_attempt, parse_tool_calls, tool_result_message
from .tools import tool_manifest


FILE_QUESTION_WORDS = {
    "file",
    "files",
    "folder",
    "folders",
    "directory",
    "directories",
    "code",
    "what they do",
    "what it does",
    "list out",
    "script",
    "scripts",
    "convert",
}

HEDGE_WORDS = {
    "likely",
    "probably",
    "might",
    "could",
    "possibly",
}

LOCAL_ACCESS_REFUSAL_PHRASES = {
    "can't actually list",
    "cannot actually list",
    "can't access",
    "cannot access",
    "limitations in this format",
    "you can execute",
    "using tools i've mentioned",
    "i don't have direct access",
    "please provide",
    "provide me",
    "provide the contents",
    "provide me either",
    "i will need access",
    "i need access",
    "once i've reviewed",
    "after inspecting your workspace files manually",
}

PROJECT_OPEN_WORDS = {
    "open",
    "launch",
    "access",
    "edit",
}

PROJECT_NOUN_WORDS = {
    "project",
    "projec",
    "repo",
    "repository",
    "codebase",
    "workspace",
    "vscode",
    "vs code",
    "visual studio code",
}

FAKE_COMPLETION_PHRASES = {
    "done",
    "successfully opened",
    "has been opened",
    "i am opening",
    "opened",
    "ready to be worked on",
}

FILE_MUTATION_REQUEST_WORDS = {
    "convert",
    "create",
    "edit",
    "fix",
    "generate",
    "make",
    "modify",
    "patch",
    "replace",
    "rewrite",
    "update",
    "write",
}

FILE_MUTATING_TOOL_NAMES = {
    "create_file",
    "edit_file",
    "dockerize_project",
    "replace_in_file",
    "write_file",
    "append_file",
    "apply_patch",
    "copy_path",
    "move_path",
    "delete_path",
}

def needs_file_inspection_nudge(history, answer):
    user_messages = [item["content"].lower() for item in history if item.get("role") == "user"]
    last_user_message = user_messages[-1] if user_messages else ""
    answer_lower = answer.lower()

    asks_about_files = asks_about_local_files(last_user_message)
    hedged_answer = any(word in answer_lower for word in HEDGE_WORDS)
    tool_results_seen = any("Tool result:" in item.get("content", "") for item in history[-6:])

    return asks_about_files and hedged_answer and not tool_results_seen


def named_files_in_prompt(prompt):
    matches = re.findall(
        r"(?i)\b[\w.-]+\.(?:sh|ps1|bat|cmd|py|js|ts|tsx|jsx|json|md|txt|yaml|yml|toml|ini|env)\b",
        prompt,
    )
    seen = set()
    files = []
    for match in matches:
        lowered = match.lower()
        if lowered not in seen:
            seen.add(lowered)
            files.append(match)
    return files


def recent_tool_results_text(history):
    return "\n".join(
        item.get("content", "")
        for item in history[-10:]
        if "Tool result:" in item.get("content", "")
    ).lower()


def has_recent_named_file_coverage(history, filenames):
    tool_text = recent_tool_results_text(history)
    if not tool_text:
        return False

    return all(filename.lower() in tool_text for filename in filenames)


def has_recent_named_file_read_coverage(history, filenames):
    tool_text = recent_tool_results_text(history)
    if not tool_text or '"content"' not in tool_text:
        return False

    return all(filename.lower() in tool_text for filename in filenames)


def claims_named_files_missing(answer):
    lowered = answer.lower()
    missing_phrases = [
        "no .sh files",
        "no sh files",
        "no shell scripts",
        "there are no",
        "does not exist",
        "could not find",
        "can't find",
        "cannot find",
        "not found",
    ]
    return any(phrase in lowered for phrase in missing_phrases)


def requests_named_file_contents(answer):
    lowered = answer.lower()
    content_request_phrases = [
        "please provide the contents",
        "provide the contents",
        "please provide me",
        "paste the contents",
        "share the contents",
        "send the contents",
    ]
    return any(phrase in lowered for phrase in content_request_phrases)


def needs_named_file_verification_retry(history, answer):
    filenames = named_files_in_prompt(last_user_prompt(history))
    if not filenames:
        return False

    if has_recent_named_file_read_coverage(history, filenames):
        return False

    return claims_named_files_missing(answer) or requests_named_file_contents(answer)


def needs_local_access_retry(history, answer):
    last_user_message = last_user_prompt(history).lower()
    answer_lower = answer.lower()

    asks_about_files = asks_about_local_files(last_user_message)
    refuses_local_access = any(phrase in answer_lower for phrase in LOCAL_ACCESS_REFUSAL_PHRASES)
    tool_results_seen = any("Tool result:" in item.get("content", "") for item in history[-6:])

    return asks_about_files and refuses_local_access and not tool_results_seen


def prompt_requires_file_mutation(prompt):
    lowered = prompt.lower()
    has_mutation_word = any(
        re.search(rf"\b{re.escape(word)}\b", lowered)
        for word in FILE_MUTATION_REQUEST_WORDS
    )
    asks_for_file_output = any(
        phrase in lowered
        for phrase in [
            "new file",
            "new files",
            "another file",
            "another script",
            "script that will work",
            "scripts that will work",
            "file that will work",
            "files that will work",
            ".ps1",
            ".bat",
            ".cmd",
        ]
    )
    asks_for_artifact = bool(
        re.search(
            r"(?i)\b(?:files?|scripts?|configs?|documents?|docs?|launchers?|wrappers?|templates?|modules?|components?)\b",
            prompt,
        )
    )
    asks_for_extension_family = bool(
        re.search(
            r"(?i)\b(?:ps1|bat|cmd|toml|ya?ml|json|py|js|ts|tsx|jsx|md|txt|ini|env)\s+(?:files?|scripts?)\b",
            prompt,
        )
    )
    references_previous_artifact = bool(re.search(r"(?i)\b(?:it|them|those|these)\b", prompt))

    return has_mutation_word and (
        asks_about_local_files(prompt)
        or asks_for_file_output
        or asks_for_artifact
        or asks_for_extension_family
        or references_previous_artifact
    )


def is_complex_execution_task(prompt):
    lowered = prompt.lower()
    complex_words = FILE_MUTATION_REQUEST_WORDS | {
        "analyze",
        "check",
        "compare",
        "review",
        "inspect",
        "verify",
        "setup",
        "install",
        "run",
    }
    has_complex_word = any(
        re.search(rf"\b{re.escape(word)}\b", lowered)
        for word in complex_words
    )
    has_multiple_files = len(named_files_in_prompt(prompt)) >= 2
    has_chain = any(marker in lowered for marker in [" and ", " then ", " if not", " after that", " so they "])
    return has_complex_word and (has_multiple_files or has_chain or prompt_requires_file_mutation(prompt))


def task_execution_instruction(prompt):
    if not is_complex_execution_task(prompt):
        return ""

    filenames = named_files_in_prompt(prompt)
    file_guidance = ""
    if filenames:
        quoted = ", ".join(filenames)
        file_guidance = (
            f" The user named these files: {quoted}. Read them with tools before "
            "making claims about their contents."
        )

    return (
        "This is a multi-step execution task. Do not answer after only one partial action. "
        "Work in this order: identify required information, gather it with the highest-level "
        "tools available, perform the requested changes with tools when changes are requested, "
        "verify the result, then give a concise final answer." + file_guidance
    )


def has_recent_file_mutation_result(history):
    return any(
        "Tool result:" in item.get("content", "")
        and any(
            re.search(rf'"tool"\s*:\s*"{re.escape(tool_name)}"', item.get("content", ""))
            for tool_name in FILE_MUTATING_TOOL_NAMES
        )
        for item in history[-10:]
    )


def answer_claims_side_effect_completed(answer):
    return bool(
        re.search(
            r"(?i)\b(?:i|we)\s+(?:have\s+)?(?:created|updated|edited|modified|rewrote|rewritten|converted|generated|made|wrote|saved)\b",
            answer,
        )
        or re.search(
            r"(?i)\b(?:created|updated|edited|modified|rewrote|rewritten|converted|generated|made|wrote|saved)\s+(?:it|them|the|this|these|that|those|[\w.-]+\.[A-Za-z0-9]+)\b",
            answer,
        )
    )


def answer_is_manual_recipe(answer):
    lowered = answer.lower()
    has_code_block = "```" in answer
    has_manual_section = bool(
        re.search(
            r"(?i)\b(?:instructions?|steps?|commands?|run|execute|copy|paste|save|create\s+(?:a\s+)?file|open\s+.*editor)\b",
            answer,
        )
    )
    has_shell_like_command = bool(
        re.search(
            r"(?im)^\s*(?:[\w.-]+\s+){1,6}(?:--?[\w-]+|/[\w?]|[./\\][\w.-]+|[\w.-]+\.[A-Za-z0-9]+)",
            answer,
        )
    )
    return has_code_block or (has_manual_section and has_shell_like_command) or (
        has_manual_section and any(extension in lowered for extension in [".ps1", ".bat", ".cmd", ".py", ".json", ".yaml", ".yml"])
    )


def answer_defers_side_effect(answer):
    return bool(
        re.search(
            r"(?i)\b(?:would you|do you want|should i|shall i|if you want|if you wish|i can|we can)\b.*\b(?:create|write|edit|update|modify|convert|generate|save)\b",
            answer,
        )
    )


def answer_claims_or_defers_file_mutation(answer):
    return (
        answer_claims_side_effect_completed(answer)
        or answer_is_manual_recipe(answer)
        or answer_defers_side_effect(answer)
    )


def needs_file_mutation_retry(history, answer):
    if not prompt_requires_file_mutation(last_user_prompt(history)):
        return False

    if has_recent_file_mutation_result(history):
        return False

    return answer_claims_or_defers_file_mutation(answer)


def asks_about_local_files(message):
    lowered = message.lower()
    if any(word in lowered for word in FILE_QUESTION_WORDS):
        return True

    return bool(
        re.search(
            r"(?i)\b[\w.-]+\.(?:sh|ps1|bat|cmd|py|js|ts|tsx|jsx|json|md|txt|yaml|yml|toml|ini|env)\b",
            message,
        )
    )


def last_user_prompt(history):
    for item in reversed(history):
        if item.get("role") == "user":
            content = item.get("content", "")
            if content.startswith("Tool result:"):
                continue
            return content
    return ""


def is_project_open_prompt(prompt):
    lowered = prompt.lower()
    has_open_word = any(word in lowered for word in PROJECT_OPEN_WORDS)
    has_project_word = any(word in lowered for word in PROJECT_NOUN_WORDS)
    has_target_words = len([word for word in lowered.split() if len(word) >= 4]) >= 2
    return has_open_word and (has_project_word or has_target_words)


def is_current_project_open_prompt(prompt):
    lowered = prompt.lower()
    return any(
        phrase in lowered
        for phrase in [
            "this project",
            "this folder",
            "this directory",
            "current project",
            "current folder",
            "current directory",
        ]
    )


def has_recent_open_vscode_result(history):
    return any(
        "Tool result:" in item.get("content", "")
        and (
            '"tool": "open_in_vscode"' in item.get("content", "")
            or '"tool": "open_project"' in item.get("content", "")
        )
        for item in history[-6:]
    )


def needs_project_open_retry(history, answer):
    if not is_project_open_prompt(last_user_prompt(history)):
        return False

    if has_recent_open_vscode_result(history):
        return False

    lowered = answer.lower()
    return mentions_tool_attempt(answer) or any(phrase in lowered for phrase in FAKE_COMPLETION_PHRASES)


def append_log(state, entry):
    state.setdefault("tool_log", []).append(
        {
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            **entry,
        }
    )


def combine_stats(total, next_stats):
    total["input_tokens"] += next_stats.get("input_tokens", 0)
    total["output_tokens"] += next_stats.get("output_tokens", 0)
    total["eval_seconds"] += next_stats.get("eval_seconds", 0)
    total["elapsed_seconds"] += next_stats.get("elapsed_seconds", 0)
    total["model_calls"] += 1

    eval_seconds = total["eval_seconds"]
    total["tokens_per_second"] = (
        total["output_tokens"] / eval_seconds if eval_seconds > 0 else 0
    )


def tool_progress_label(call):
    tool_name = call.get("tool", "")
    args = call.get("args") or {}
    labels = {
        "dockerize_project": "preparing Docker project files",
        "project_snapshot": "reading project snapshot",
        "project_status": "checking project status",
        "list_dir": "listing files",
        "tree": "reading project tree",
        "search_files": "searching files",
        "read_file": "reading file",
        "read_many_files": "reading files",
        "create_file": "creating file",
        "edit_file": "editing file",
        "replace_in_file": "editing file",
        "write_many_files": "writing files",
        "run_shell": "running command",
        "run_task": "running task",
        "start_project": "starting project",
        "open_project": "opening project",
    }
    label = labels.get(tool_name, f"running {tool_name}")
    target = args.get("path") or args.get("query") or args.get("command") or ""
    if target:
        return f"{label}: {target}"
    return label


def execute_tool_call(call, state, registry, progress=None):
    decision = classify_tool_call(call, state, registry)
    tool_name = call.get("tool")

    if decision.blocked:
        result = {
            "tool": tool_name,
            "ok": False,
            "risk": decision.risk,
            "error": {"type": "PolicyBlocked", "message": decision.reason},
        }
        append_log(state, {**result, "approved": False})
        return result

    if decision.requires_approval and not approve_tool_call(call, decision):
        result = {
            "tool": tool_name,
            "ok": False,
            "risk": decision.risk,
            "error": {"type": "UserDenied", "message": "User denied tool execution."},
        }
        append_log(state, {**result, "approved": False})
        return result

    tool = registry[tool_name]
    args = call.get("args") or {}
    previous_progress = state.get("_progress")

    try:
        if progress:
            progress(tool_progress_label(call))
        if progress:
            state["_progress"] = progress
        output = tool.handler(args, state)
        if progress:
            progress(f"finished {tool_name}")
        if (
            tool_name in {"open_in_vscode", "open_project"}
            and isinstance(output, dict)
            and output.get("exit_code") == 0
            and output.get("path")
        ):
            state["cwd"] = Path(output["path"])
        result = {
            "tool": tool_name,
            "ok": True,
            "risk": decision.risk,
            "result": output,
        }
        append_log(state, {**result, "approved": True})
        return result
    except Exception as error:
        result = {
            "tool": tool_name,
            "ok": False,
            "risk": decision.risk,
            "error": {
                "type": type(error).__name__,
                "message": str(error),
            },
        }
        append_log(state, {**result, "approved": True})
        return result
    finally:
        if progress:
            if previous_progress is None:
                state.pop("_progress", None)
            else:
                state["_progress"] = previous_progress


def is_user_denied_result(result):
    return result.get("error", {}).get("type") == "UserDenied"


def open_vscode_result_message(result):
    if not result.get("ok"):
        message = result.get("error", {}).get("message", "Unknown error.")
        return f"I could not open the project in VS Code: {message}"

    output = result.get("result") or {}
    path = output.get("path", "")
    exit_code = output.get("exit_code", 1)

    if exit_code == 0 and output.get("visible", True):
        return f"Opened {path} in VS Code."

    detail = (output.get("stderr") or output.get("stdout") or "").strip()
    if detail:
        return f"I tried to open {path} in VS Code, but the VS Code CLI exited with code {exit_code}: {detail}"

    return f"I tried to open {path} in VS Code, but the VS Code CLI exited with code {exit_code}."


def print_tool_terminal_result(model, result):
    if result.get("tool") not in {"open_in_vscode", "open_project"}:
        return False

    message = open_vscode_result_message(result)
    output = result.get("result") or {}
    if result.get("ok") and output.get("exit_code") == 0 and output.get("visible", True):
        print(f"\n{success('Opened')} {directory_name(output.get('path', ''))} in VS Code.")
    else:
        print_assistant_message(model, error(message))

    return True


def project_open_tool_call(prompt):
    query = "." if is_current_project_open_prompt(prompt) else prompt
    return {"tool": "open_project", "args": {"query": query}}


def coerce_project_open_call(call, history):
    prompt = last_user_prompt(history)
    if not is_project_open_prompt(prompt):
        return call

    tool_name = call.get("tool")
    args = call.get("args") or {}

    if tool_name == "open_project":
        if not args.get("query"):
            return project_open_tool_call(prompt)
        return call

    if tool_name == "open_in_vscode":
        raw_path = str(args.get("path", "")).strip()
        if is_current_project_open_prompt(prompt):
            return {"tool": "open_project", "args": {"query": "."}}
        if raw_path in {"", ".", "./"}:
            return project_open_tool_call(prompt)
        if not any(separator in raw_path for separator in [":", "/", "\\"]):
            return {"tool": "open_project", "args": {"query": raw_path}}
        return project_open_tool_call(prompt)

    if tool_name == "list_projects":
        return project_open_tool_call(prompt)

    return call


def run_agent_turn(model, history, state, registry, handoff_prompt=None):
    turn_start = time.time()
    cancel_event = Event()
    turn_stats = {
        "input_tokens": 0,
        "output_tokens": 0,
        "eval_seconds": 0,
        "elapsed_seconds": 0,
        "tokens_per_second": 0,
        "model_calls": 0,
    }

    system_prompt = (
        TOOL_SYSTEM_PROMPT
        .replace("__TOOL_MANIFEST__", tool_manifest(registry))
        .replace("__CWD__", str(state["cwd"]))
        .replace("__WORKSPACE__", str(WORKSPACE_DIR))
    )

    if handoff_prompt:
        system_prompt = f"{system_prompt}\n\nAdditional routing instruction:\n{handoff_prompt}"

    execution_instruction = task_execution_instruction(last_user_prompt(history))
    if execution_instruction:
        system_prompt = f"{system_prompt}\n\nExecution contract:\n{execution_instruction}"

    round_number = 0

    with EscapeCancelWatcher(cancel_event):
        while True:
            if cancel_event.is_set():
                final_message = "Stopped by user."
                print_assistant_message(model, final_message)
                turn_stats["elapsed_seconds"] = time.time() - turn_start
                print_message_stats(turn_stats)
                return final_message

            if round_number > 0 and round_number % MAX_TOOL_ROUNDS == 0:
                print(dim(f"Continuing after {round_number} tool rounds. Press Esc to stop."))
                history.append(
                    {
                        "role": "user",
                        "content": (
                            "Continue the task. You reached an internal tool-round checkpoint, "
                            "but you should keep working until the user's request is answered. "
                            "If you already have enough information, give the final answer. "
                            "If more inspection or edits are needed, use the next required tool."
                        ),
                    }
                )

            messages = build_messages(history, system_prompt)
            with WorkingStatus(cancel_event=cancel_event) as status:
                raw_answer, stats = ask_stream_with_stats(
                    model,
                    messages,
                    on_thought=status.update_detail,
                    cancel_event=cancel_event,
                )
            combine_stats(turn_stats, stats)

            if cancel_event.is_set():
                final_message = "Stopped by user."
                print_assistant_message(model, final_message)
                turn_stats["elapsed_seconds"] = time.time() - turn_start
                print_message_stats(turn_stats)
                return final_message

            if stats.get("stop_reason", "").startswith("timeout_"):
                final_message = (
                    "The model timed out before producing a usable answer. "
                    "Try the request again with a stronger model or ask it to use a high-level tool first."
                )
                print_assistant_message(model, final_message)
                turn_stats["elapsed_seconds"] = time.time() - turn_start
                print_message_stats(turn_stats)
                return final_message

            if not raw_answer:
                print_message_stats(turn_stats)
                return ""

            try:
                tool_calls = parse_tool_calls(raw_answer)
            except (json.JSONDecodeError, ValueError):
                tool_calls = None

            if not tool_calls:
                if looks_like_tool_attempt(raw_answer) or needs_project_open_retry(history, raw_answer):
                    history.append({"role": "assistant", "content": raw_answer})
                    history.append(
                        {
                            "role": "user",
                            "content": (
                                "Your previous response tried to use a tool or claimed a project was opened, "
                                "but no valid tool call ran. Return only strict JSON. For opening any project, "
                                'use {"tool":"open_project","args":{"query":"<the user project wording>"}}. '
                                'For opening the current project, use {"tool":"open_project","args":{"query":"."}}. '
                                "For reading files, use valid JSON such as "
                                '{"tool":"read_file","args":{"path":"start.sh"}}. Do not include prose.'
                            ),
                        }
                    )
                    continue

                if needs_named_file_verification_retry(history, raw_answer):
                    filenames = named_files_in_prompt(last_user_prompt(history))
                    history.append({"role": "assistant", "content": raw_answer})
                    history.append(
                        {
                            "role": "user",
                            "content": (
                                "You claimed named files were missing without verifying them correctly. "
                                "Use tools now. First call list_dir on '.', then read each named file if present: "
                                f"{', '.join(filenames)}. Do not answer until you have verified with tool results."
                            ),
                        }
                    )
                    continue

                if needs_file_mutation_retry(history, raw_answer):
                    history.append({"role": "assistant", "content": raw_answer})
                    history.append(
                        {
                            "role": "user",
                            "content": (
                            "The user asked you to create, convert, edit, or update files, but no file-changing "
                            "tool has run. Do not ask the user if they want you to proceed, and do not claim the "
                            "files were converted. Use tools now. Inspect any relevant existing files if needed, "
                            "then call the highest-level appropriate file-changing tool, such as a task-specific "
                            "workflow tool, write_many_files, create_file, edit_file, replace_in_file, or apply_patch. "
                            "The terminal will "
                            "ask for approval before file changes."
                        ),
                    }
                )
                    continue

                if needs_file_inspection_nudge(history, raw_answer):
                    history.append({"role": "assistant", "content": raw_answer})
                    history.append(
                        {
                            "role": "user",
                            "content": (
                                "You answered uncertainly without inspecting local files. "
                                "Use tools to inspect the relevant directory and files, then answer with verified details."
                            ),
                        }
                    )
                    continue

                if needs_local_access_retry(history, raw_answer):
                    history.append({"role": "assistant", "content": raw_answer})
                    history.append(
                        {
                            "role": "user",
                            "content": (
                                "You do have local tools in this terminal. Do not tell me to run tools manually. "
                                "Use the available tools now to inspect the current directory and answer from the results."
                            ),
                        }
                    )
                    continue

                print_assistant_message(model, raw_answer)
                turn_stats["elapsed_seconds"] = time.time() - turn_start
                print_message_stats(turn_stats)
                return raw_answer

            if len(tool_calls) > MAX_TOOL_CALLS_PER_ROUND:
                tool_calls = tool_calls[:MAX_TOOL_CALLS_PER_ROUND]

            history.append({"role": "assistant", "content": raw_answer})

            terminal_tool_result = None

            for call in tool_calls:
                call = coerce_project_open_call(call, history)
                with WorkingStatus(cancel_event=cancel_event) as tool_status:
                    result = execute_tool_call(
                        call,
                        state,
                        registry,
                        progress=tool_status.update_detail,
                    )
                history.append(tool_result_message(result))
                if is_user_denied_result(result):
                    final_message = "Stopped because you denied the requested file-changing action."
                    print_assistant_message(model, final_message)
                    turn_stats["elapsed_seconds"] = time.time() - turn_start
                    print_message_stats(turn_stats)
                    return final_message
                if result.get("tool") in {"open_in_vscode", "open_project"}:
                    terminal_tool_result = result

            round_number += 1

            if terminal_tool_result:
                final_message = open_vscode_result_message(terminal_tool_result)
                print_tool_terminal_result(model, terminal_tool_result)
                turn_stats["elapsed_seconds"] = time.time() - turn_start
                print_message_stats(turn_stats)
                return final_message
