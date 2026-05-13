from dataclasses import dataclass
from pathlib import Path

from .config import SECRET_NAME_PARTS, WORKSPACE_DIR
from .display import command_name, dim, directory_name, error, warning


@dataclass
class RiskDecision:
    risk: str
    requires_approval: bool
    blocked: bool
    reason: str
    resolved_paths: list[str]


LOW_TOOLS = {
    "get_cwd",
    "list_dir",
    "list_projects",
    "tree",
    "search_files",
    "read_file",
    "read_many_files",
    "read_file_metadata",
    "project_snapshot",
    "project_status",
    "open_in_vscode",
    "git_status",
    "git_diff",
    "git_log",
    "git_show",
    "session_note",
    "session_recall",
}

MEDIUM_TOOLS = {
    "set_cwd",
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
    "git_add",
    "list_processes",
    "system_info",
    "env_get",
    "run_task",
}

HIGH_TOOLS = {
    "run_shell",
    "delete_path",
    "git_commit",
    "kill_process",
    "http_request",
}

FILE_MUTATING_TOOLS = {
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
    "git_add",
    "git_commit",
}

SHELL_FILE_MUTATION_WORDS = {
    "set-content",
    "add-content",
    "out-file",
    "new-item",
    "remove-item",
    "move-item",
    "copy-item",
    "rename-item",
    "del",
    "erase",
    "rm",
    "rmdir",
    "mkdir",
    "md",
    "ni",
    ">",
    ">>",
    "git add",
    "git clone",
    "git commit",
    "git checkout",
    "git switch",
    "git reset",
    "git clean",
    "npm install",
    "pip install",
}


def resolve_path(raw_path, base_dir):
    path = Path(raw_path).expanduser()

    if not path.is_absolute():
        path = base_dir / path

    return path.resolve()


def is_inside(path, root):
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def is_secret_path(path):
    lowered = str(path).lower()
    return any(part in lowered for part in SECRET_NAME_PARTS)


def stringify_command(command):
    if isinstance(command, list):
        return " ".join(str(part) for part in command)

    if command is None:
        return ""

    return str(command)


def shell_may_change_files(command):
    lowered = f" {stringify_command(command).lower()} "
    return any(word in lowered for word in SHELL_FILE_MUTATION_WORDS)


def requires_file_change_approval(tool_name, args):
    if tool_name in FILE_MUTATING_TOOLS:
        return True

    if tool_name == "run_shell":
        return shell_may_change_files(args.get("command", ""))

    return False


def classify_tool_call(call, state, registry):
    tool_name = call.get("tool")
    args = call.get("args") or {}
    resolved_paths = []

    if tool_name not in registry:
        return RiskDecision("blocked", False, True, f"Unknown tool: {tool_name}", [])

    base_risk = "low"
    if tool_name in HIGH_TOOLS:
        base_risk = "high"
    elif tool_name in MEDIUM_TOOLS:
        base_risk = "medium"

    path_keys = ["path", "source", "destination"]
    outside_workspace = False
    secret_path = False

    for key in path_keys:
        if key not in args or args[key] in (None, ""):
            continue

        path = resolve_path(args[key], state["cwd"])
        resolved_paths.append(str(path))

        if not is_inside(path, WORKSPACE_DIR):
            outside_workspace = True

        if is_secret_path(path):
            secret_path = True

    if secret_path and tool_name in {"read_file", "search_files", "env_get"}:
        return RiskDecision(
            "blocked",
            False,
            True,
            "Secret-like paths and names are blocked by default.",
            resolved_paths,
        )

    if outside_workspace and base_risk == "low":
        base_risk = "medium"
    elif outside_workspace and base_risk == "medium":
        base_risk = "high"

    requires_approval = requires_file_change_approval(tool_name, args)
    reason = "Approval required because this can change files." if requires_approval else "No file changes requested."

    return RiskDecision(
        base_risk,
        requires_approval,
        False,
        reason,
        resolved_paths,
    )


def approve_tool_call(call, decision):
    risk_formatter = error if decision.risk == "high" else warning

    print()
    print(warning("Approval required"))
    print(f"  {dim('Tool')}   {command_name(call.get('tool'))}")
    print(f"  {dim('Risk')}   {risk_formatter(decision.risk)}")
    print(f"  {dim('Reason')} {decision.reason}")

    args = call.get("args") or {}
    if args:
        print(f"  {dim('Args')}   {args}")

    for path in decision.resolved_paths:
        print(f"  {dim('Path')}   {directory_name(path)}")

    answer = input(f"{warning('Run this tool?')} {dim('[y/N]')} ").strip().lower()
    return answer in {"y", "yes"}
