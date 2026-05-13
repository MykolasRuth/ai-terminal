import json
import re


TOOL_NAMES = {
    "get_cwd",
    "set_cwd",
    "list_dir",
    "list_projects",
    "open_project",
    "tree",
    "search_files",
    "read_file",
    "read_many_files",
    "read_file_metadata",
    "project_snapshot",
    "project_status",
    "create_file",
    "edit_file",
    "replace_in_file",
    "write_many_files",
    "windows_launcher_scripts",
    "dockerize_project",
    "write_file",
    "append_file",
    "apply_patch",
    "copy_path",
    "move_path",
    "delete_path",
    "run_shell",
    "run_task",
    "open_in_vscode",
    "git_status",
    "git_diff",
    "git_log",
    "git_show",
    "git_add",
    "git_commit",
    "list_processes",
    "system_info",
    "env_get",
    "session_note",
    "session_recall",
}


def normalize_tool_call(call):
    if not isinstance(call, dict) or "tool" not in call:
        return None

    tool = call.get("tool")
    args = call.get("args")

    if isinstance(tool, list) and len(tool) == 2:
        tool_name, tool_args = tool
        if isinstance(tool_name, str) and isinstance(tool_args, dict):
            return {"tool": tool_name, "args": tool_args}

    if isinstance(tool, str):
        if args is None:
            args = {
                key: value
                for key, value in call.items()
                if key not in {"tool", "args"}
            }
        if isinstance(args, dict):
            return {"tool": tool, "args": args}

    return None


def parse_json_value(text):
    text = text.strip()

    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()

    if not text:
        raise ValueError("empty response")

    decoder = json.JSONDecoder()

    try:
        value, end = decoder.raw_decode(text)
    except json.JSONDecodeError:
        start = min(
            index for index in [text.find("{"), text.find("[")] if index != -1
        )
        value, end = decoder.raw_decode(text[start:])

    return value


def parse_tool_calls(text):
    value = parse_json_value(text)

    if isinstance(value, dict):
        normalized = normalize_tool_call(value)
        return [normalized] if normalized else None

    if isinstance(value, list):
        normalized_calls = [normalize_tool_call(item) for item in value]
        if all(normalized_calls):
            return normalized_calls

    return None


def looks_like_tool_attempt(text):
    stripped = text.strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        stripped = "\n".join(lines[1:-1]).strip()

    starts_like_json = stripped.startswith("{") or stripped.startswith("[")
    mentions_tool = (
        '"tool"' in stripped
        or "'tool'" in stripped
        or '"tool":' in text
        or "'tool':" in text
    )
    pseudo_tool_command = any(
        re.search(rf"(?i)(^|\s|`){re.escape(tool_name)}\s+[\w.-]+\s*=", stripped)
        for tool_name in TOOL_NAMES
    )
    return (starts_like_json and mentions_tool) or pseudo_tool_command


def mentions_tool_attempt(text):
    lowered = text.lower()
    return (
        '"tool"' in text
        or "'tool'" in text
        or '"tool":' in text
        or "'tool':" in text
        or any(tool_name in lowered for tool_name in TOOL_NAMES)
    )


def tool_result_message(result):
    return {
        "role": "user",
        "content": "Tool result:\n" + json.dumps(result, indent=2, ensure_ascii=False),
    }
