import re
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

from .config import PROJECTS_DIR, WORKSPACE_DIR
from .display import (
    WorkingStatus,
    command_name,
    dim,
    directory_name,
    error as error_text,
    file_name,
    highlight_text,
    success,
)
from .intents import is_project_listing_request, is_project_open_request
from .ollama_client import get_models, ollama_is_reachable
from .tools import dockerize_project, git_status, project_status, read_file, run_task, search_files, list_dir, set_cwd, start_project, tree
from .tools import open_in_vscode
from .tools import is_current_project_query, resolve_project_query, score_project_match
from .web_auth import create_account, delete_account, list_accounts, update_password


DIRECT_SHELL_COMMANDS = {
    "bun",
    "cargo",
    "code",
    "curl",
    "deno",
    "docker",
    "docker-compose",
    "dotnet",
    "gh",
    "git",
    "go",
    "gradle",
    "gradlew",
    "helm",
    "hostname",
    "ipconfig",
    "java",
    "javac",
    "kubectl",
    "mvn",
    "netstat",
    "node",
    "npm",
    "npx",
    "nslookup",
    "pip",
    "pip3",
    "pipx",
    "ping",
    "pnpm",
    "poetry",
    "powershell",
    "psql",
    "pwsh",
    "py",
    "python",
    "python3",
    "rustc",
    "sqlite3",
    "ssh",
    "tasklist",
    "terraform",
    "uv",
    "where",
    "whoami",
    "winget",
    "wget",
    "yarn",
}

DIRECT_SCRIPT_EXTENSIONS = {
    ".bat",
    ".cmd",
    ".ps1",
    ".py",
    ".sh",
}


def resolve_model_choice(choice, models):
    choice = choice.strip()

    if choice.lower().startswith("/model "):
        choice = choice.split(maxsplit=1)[1].strip()

    if choice.lower().startswith("model "):
        choice = choice.split(maxsplit=1)[1].strip()

    if choice.lower().startswith("/orchestrator "):
        choice = choice.split(maxsplit=1)[1].strip()

    if not choice:
        return ""

    if choice.isdigit():
        index = int(choice) - 1
        if 0 <= index < len(models):
            return models[index]
        return ""

    if choice in models:
        return choice

    matches = [model for model in models if choice.lower() in model.lower()]
    if len(matches) == 1:
        return matches[0]

    return ""


def print_model_list(models, active_model=None):
    for index, model in enumerate(models, 1):
        marker = success("*") if model == active_model else dim(" ")
        print(f"  {marker} {dim(str(index).rjust(2) + '.')} {success(model) if model == active_model else model}")


def print_directory_entries(result):
    for entry in result["entries"]:
        if entry["type"] == "directory":
            print(f"  {directory_name(entry['name'] + '/')}")
        else:
            print(f"  {file_name(entry['name'])}")

    print(f"{dim(str(len(result['entries'])) + ' item(s) in')} {directory_name(result['path'])}")


def print_project_entries(projects_root):
    entries = []

    for path in projects_root.iterdir():
        if path.is_dir() and not path.name.startswith("."):
            entries.append(path)

    entries = sorted(entries, key=lambda item: item.name.lower())

    print_project_choice_entries(entries, projects_root)
    return entries


def print_project_choice_entries(entries, projects_root):
    for index, path in enumerate(entries, 1):
        print(f"  {dim(str(index).rjust(2) + '.')} {directory_name(path.name + '/')}")

    print()
    print(f"{dim(str(len(entries)) + ' project(s) in')} {directory_name(str(projects_root))}")
    if entries:
        print(dim("Which project would you like to open?"))


def parse_project_choice(prompt):
    normalized = prompt.lower().strip(" .!?")

    if normalized.isdigit():
        return int(normalized)

    match = re.fullmatch(r"(?:project|open|open project)\s+(\d+)", normalized)
    if match:
        return int(match.group(1))

    return None


def is_pending_project_list_reference(prompt):
    normalized = prompt.lower().strip(" .!?")
    has_reference = any(word in normalized for word in ["them", "those", "projects", "repos", "repositories"])
    has_list_intent = any(phrase in normalized for phrase in ["list", "show", "what are", "again"])
    return has_reference and has_list_intent


def pending_project_match(prompt, state):
    projects = state.get("pending_project_choices") or []
    if not projects:
        return None

    matches = [
        (score_project_match(prompt, project), project)
        for project in projects
    ]
    matches = [(score, project) for score, project in matches if score >= 50]
    if not matches:
        return None

    matches.sort(key=lambda item: (-item[0], item[1].name.lower()))
    best_score = matches[0][0]
    tied = [project for score, project in matches if score == best_score]
    if len(tied) > 1:
        return None

    return matches[0][1]


def is_project_about_question(prompt):
    normalized = prompt.lower().strip(" .!?")
    return any(
        phrase in normalized
        for phrase in [
            "what does",
            "what is",
            "tell me about",
            "explain",
            "describe",
            "what's",
        ]
    )


def is_project_start_request(prompt):
    normalized = prompt.lower().strip(" .!?")
    if normalized.startswith(("how ", "how can ", "how do ", "how would ")):
        return False
    if any(word in normalized for word in ["convert", "create", "make", "write", "edit", "rewrite", "file", "files", ".ps1", ".bat", ".cmd"]):
        return False

    has_start = any(word in normalized for word in ["start", "run", "host", "launch", "serve"])
    has_target = any(word in normalized for word in ["project", "app", "site", "that", "this", "it"])
    return has_start and has_target


def is_dockerize_request(prompt):
    normalized = prompt.lower()
    return any(
        phrase in normalized
        for phrase in [
            "dockerize",
            "containerize",
            "make docker",
            "create docker",
            "generate docker",
            "add docker",
            "docker files",
            "docker tools",
            "docker setup",
        ]
    )


def dockerize_target_for_request(prompt, state):
    if is_current_project_query(prompt):
        return Path(state["cwd"]).resolve()

    try:
        project_path, _matches = resolve_project_query(prompt, state)
        return project_path
    except (FileNotFoundError, ValueError):
        return Path(state["cwd"]).resolve()


def print_dockerize_result(result):
    print(f"{success('Docker ready')} {directory_name(result['project'] + '/')}")
    print(f"{dim('Path')} {directory_name(result['path'])}")

    if result.get("services"):
        print()
        print(dim("Services:"))
        for service in result["services"]:
            print(
                f"  {command_name(service['name'])} "
                f"{dim(service['type'] + '/' + service['kind'])} "
                f"{dim(str(service['host_port']) + ':' + str(service['container_port']))}"
            )

    print()
    if result.get("files_written"):
        print(dim("Files written:"))
        for path in result["files_written"]:
            print(f"  {file_name(path)}")
    else:
        print(dim("Docker files already existed; validated the current setup."))

    validation = result.get("validation")
    if validation:
        print()
        if validation.get("exit_code") == 0:
            print(success("docker compose config passed."))
        else:
            print(error_text("docker compose config failed."))
            if validation.get("stderr"):
                print(error_text(validation["stderr"]))

    build = result.get("build")
    if build:
        print()
        if build.get("exit_code") == 0:
            print(success("docker compose build passed."))
        else:
            print(error_text("docker compose build failed."))
            if build.get("stderr"):
                print(error_text(build["stderr"]))

    print()
    print(dim("Next commands:"))
    for command in result.get("next_commands", []):
        print(f"  {command_name(command)}")


def print_project_start_result(result):
    print(f"{success('Started')} {directory_name(result['project'] + '/')} {dim('using')} {command_name(result['script'])}")
    if os.environ.get("DEVSERVER_WEB_TERMINAL") == "1":
        print(f"DEVSERVER_PROJECT_LINK_LABEL: {result['project']}")
    print(f"{dim('PID')} {result['pid']}")
    if result.get("urls"):
        print()
        print(dim("Access:"))
        for url in result["urls"]:
            print(f"  {command_name(url)}")
    else:
        print()
        print(dim("No ports were detected from the start files. Check the logs below for the served URL."))
    print()
    print(dim("Logs:"))
    print(f"  {file_name(result['stdout_log'])}")
    print(f"  {file_name(result['stderr_log'])}")


def project_target_for_request(prompt, state):
    matched = pending_project_match(prompt, state)
    if matched:
        return matched

    if is_current_project_query(prompt):
        return Path(state["cwd"]).resolve()

    normalized = prompt.lower()
    if not re.search(r"\b(?:that|it)\b", normalized):
        try:
            project_path, _matches = resolve_project_query(prompt, state)
            return project_path
        except (FileNotFoundError, ValueError):
            pass

    if re.search(r"\b(?:that|it)\b", normalized) and state.get("last_project_path"):
        path = Path(state["last_project_path"]).resolve()
        if path.exists() and path.is_dir():
            return path

    path = Path(state["cwd"]).resolve()
    if path.exists() and path.is_dir():
        return path

    return None


def print_project_summary(project_path):
    print(f"{directory_name(project_path.name + '/')} {dim(str(project_path))}")
    print()

    entries = []
    for path in sorted(project_path.iterdir(), key=lambda item: item.name.lower()):
        if path.name.startswith("."):
            continue
        entries.append(path)

    readme = next((path for path in entries if path.name.lower() == "readme.md"), None)
    if readme and readme.is_file():
        content = readme.read_text(encoding="utf-8", errors="replace")
        lines = [line.strip(" #") for line in content.splitlines() if line.strip()]
        if lines:
            print(dim("README:"))
            for line in lines[:6]:
                print(f"  {highlight_text(line)}")
            print()

    key_names = {"package.json", "requirements.txt", "pyproject.toml", "Dockerfile", "start.sh", "start.ps1"}
    key_files = [path.name for path in entries if path.is_file() and path.name in key_names]
    directories = [path.name + "/" for path in entries if path.is_dir()][:8]

    if directories:
        print(dim("Folders:"))
        print("  " + "  ".join(directory_name(name) for name in directories))

    if key_files:
        print(dim("Key files:"))
        print("  " + "  ".join(file_name(name) for name in key_files))


def parse_account_command(prompt):
    try:
        parts = shlex.split(prompt, posix=False)
    except ValueError as error:
        return "error", [], str(error)

    cleaned = [part.strip("\"'") for part in parts]
    if not cleaned or cleaned[0].lower() != "/account":
        return None, [], ""

    if len(cleaned) == 1:
        return "help", [], ""

    return cleaned[1].lower(), cleaned[2:], ""


def shell_command_head(prompt):
    stripped = prompt.strip()
    if not stripped or stripped.startswith(("/", "!")):
        return ""
    if "\n" in stripped:
        return ""

    try:
        parts = shlex.split(stripped, posix=False)
    except ValueError:
        parts = stripped.split()

    if not parts:
        return ""

    return parts[0].strip("\"'").lower()


def is_local_script_command(head):
    normalized = head.replace("/", "\\")
    if not normalized.startswith((".\\", "..\\")):
        return False
    return Path(normalized).suffix.lower() in DIRECT_SCRIPT_EXTENSIONS


def is_direct_shell_command(prompt):
    head = shell_command_head(prompt)
    return head in DIRECT_SHELL_COMMANDS or is_local_script_command(head)


def print_shell_result(result):
    if not isinstance(result, dict):
        print(result)
        return

    if not result.get("ok", False):
        message = result.get("error", {}).get("message") or str(result)
        print(error_text(message))
        return

    shell_result = result.get("result")
    if not isinstance(shell_result, dict):
        print(result)
        return

    stdout = str(shell_result.get("stdout") or "").rstrip()
    stderr = str(shell_result.get("stderr") or "").rstrip()
    exit_code = shell_result.get("exit_code", 0)

    if stdout:
        print(highlight_text(stdout))
    if stderr:
        print(error_text(stderr))
    if exit_code not in (0, None):
        print(error_text(f"Exit code {exit_code}"))


def print_account_help():
    print(success("Web account commands"))
    print()
    print(f"  {command_name('/account')} {dim('Show this help menu')}")
    print(f"  {command_name('/account list')} {dim('List browser login accounts')}")
    print(f"  {command_name('/account create \"username\" \"password\"')} {dim('Create a browser login account')}")
    print(f"  {command_name('/account delete \"username\"')} {dim('Delete a browser login account')}")
    print(f"  {command_name('/account passwd \"username\" \"new-password\"')} {dim('Change a browser login password')}")


def handle_account_command(prompt):
    action, args, parse_error = parse_account_command(prompt)
    if action is None:
        return False

    if action == "error":
        print(error_text(f"Invalid /account command: {parse_error}"))
        return True

    try:
        if action in {"help", "-h", "--help"}:
            print_account_help()
            return True

        if action == "list":
            accounts = list_accounts()
            if not accounts:
                print(dim("No web accounts configured."))
            else:
                for username in accounts:
                    print(f"  {success(username)}")
            return True

        if action == "create":
            if len(args) != 2:
                print(error_text('Usage: /account create "username" "password"'))
                return True
            create_account(args[0], args[1])
            print(f"{success('Created web account')} {args[0]}")
            return True

        if action == "delete":
            if len(args) != 1:
                print(error_text('Usage: /account delete "username"'))
                return True
            delete_account(args[0])
            print(f"{success('Deleted web account')} {args[0]}")
            return True

        if action in {"passwd", "password", "update"}:
            if len(args) != 2:
                print(error_text('Usage: /account passwd "username" "new-password"'))
                return True
            update_password(args[0], args[1])
            print(f"{success('Updated web password')} {args[0]}")
            return True

        print(error_text(f"Unknown /account action: {action}"))
        print_account_help()
        return True
    except ValueError as error:
        print(error_text(str(error)))
        return True


def open_project_choice(choice, state):
    projects = state.get("pending_project_choices") or []
    if not projects:
        return False

    if choice < 1 or choice > len(projects):
        print(error_text(f"Project {choice} is not in the list."))
        return True

    project_path = projects[choice - 1]
    open_path_in_vscode(project_path, state, make_active=True)
    state.pop("pending_project_choices", None)
    return True


def use_project_path(project_path, state):
    state["cwd"] = project_path.resolve()
    state.pop("pending_project_choices", None)
    print(f"{dim('Directory')} {directory_name(str(state['cwd']))}")
    return True


def open_path_in_vscode(path, state, make_active=False):
    try:
        result = open_in_vscode({"path": str(path)}, state)
        if result["exit_code"] == 0:
            print(f"{success('Opened')} {directory_name(result['path'])} {dim('in VS Code.')}")
            if make_active:
                state["cwd"] = Path(result["path"])
                print(f"{dim('Directory')} {directory_name(result['path'])}")
        else:
            print(error_text(result["stderr"].strip() or f"VS Code exited with code {result['exit_code']}."))
    except (FileNotFoundError, ValueError) as error:
        print(error_text(str(error)))


def parse_list_dir_call(prompt):
    match = re.fullmatch(r"\s*list_dir\((.*)\)\s*", prompt)
    if not match:
        return None

    raw_arg = match.group(1).strip()
    if not raw_arg:
        return "."

    raw_arg = raw_arg.strip("\"'")
    return raw_arg or "."


def parse_git_clone_command(prompt):
    match = re.search(r"(?i)\bgit\s+clone\s+(https?://\S+|git@\S+)", prompt)
    if not match:
        return None

    url = clean_git_url(match.group(1))
    remainder = prompt[match.end():].strip()
    return url, parse_clone_destination(remainder)


def clean_git_url(url):
    return url.strip().rstrip(".,);]")


def extract_git_url(prompt):
    match = re.search(r"(?i)(https?://\S+?\.git|git@\S+?\.git)(?=\s|$|[.,);]])", prompt)
    if not match:
        match = re.search(r"(?i)(https?://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)(?=\s|$|[.,);]])", prompt)

    if not match:
        return ""

    return clean_git_url(match.group(1))


def parse_clone_destination(remainder):
    if not remainder:
        return ""

    remainder = remainder.strip()
    quoted = re.fullmatch(r"['\"]([^'\"]+)['\"]", remainder)
    single_token = re.fullmatch(r"([A-Za-z0-9_.-]+)", remainder)
    natural_language = re.match(
        r"(?i)^(is|this|that|these|those|repo|repository|the|for|please|to|into|in|make|name|same|as|can|clone)\b",
        remainder,
    )

    if quoted:
        return quoted.group(1).strip()

    if single_token and not natural_language:
        return single_token.group(1).strip()

    return ""


def parse_git_clone_request(prompt):
    explicit = parse_git_clone_command(prompt)
    if explicit:
        return explicit

    normalized = prompt.lower()
    if "clone" not in normalized:
        return None

    url = extract_git_url(prompt)
    if not url:
        return None

    return url, ""


def clone_repository(prompt, state):
    parsed = parse_git_clone_request(prompt)
    if not parsed:
        return False

    url, destination = parsed
    target_dir = PROJECTS_DIR
    command = ["git", "clone", url]

    if destination:
        command.append(destination)

    print()
    print(f"{dim('Clone into')} {directory_name(str(target_dir))}")
    answer = input(f"{command_name('git clone')} {dim('will create files. Continue? [y/N]')} ").strip().lower()

    if answer not in {"y", "yes"}:
        print(dim("Clone cancelled."))
        return True

    try:
        result = subprocess.run(
            command,
            cwd=target_dir,
            text=True,
            capture_output=True,
            timeout=300,
        )
    except OSError as error:
        print(error_text(str(error)))
        return True

    if result.stdout:
        print(highlight_text(result.stdout.strip()))

    if result.stderr:
        formatter = error_text if result.returncode else dim
        print(formatter(result.stderr.strip()))

    if result.returncode == 0:
        print(f"{success('Cloned')} {file_name(url)} {dim('into')} {directory_name(str(target_dir))}")
    else:
        print(error_text(f"git clone exited with code {result.returncode}."))

    return True


def is_natural_directory_listing(prompt):
    if should_defer_to_agent(prompt):
        return False

    normalized = prompt.lower().strip(" .!?")
    has_list_word = any(word in normalized for word in ["list", "show", "what are", "all the files", "what files"])
    has_file_word = any(word in normalized for word in ["file", "files", "folder", "folders", "directory", "dir"])
    current_dir = any(phrase in normalized for phrase in ["current directory", "this directory", "this folder", "here", "my directory"])
    return has_list_word and has_file_word and current_dir


def natural_project_listing_request(prompt):
    return is_project_listing_request(prompt)


def natural_tree_path(prompt):
    if should_defer_to_agent(prompt):
        return None

    normalized = prompt.lower().strip(" .!?")
    if not any(word in normalized for word in ["tree", "structure", "layout"]):
        return None

    if any(phrase in normalized for phrase in ["current directory", "this directory", "this folder", "here", "project"]):
        return "."

    return None


def natural_pwd_request(prompt):
    normalized = prompt.lower().strip(" .!?")
    return normalized in {
        "where am i",
        "what directory am i in",
        "what folder am i in",
        "current directory",
        "show current directory",
        "show pwd",
    }


def natural_git_status_request(prompt):
    normalized = prompt.lower().strip(" .!?")
    return normalized in {
        "git status",
        "show git status",
        "what changed",
        "what files changed",
        "show changed files",
        "show repo status",
    }


def natural_start_script_request(prompt):
    if should_defer_to_agent(prompt):
        return False

    normalized = prompt.lower().strip(" .!?")
    return "start.sh" in normalized and any(
        phrase in normalized
        for phrase in [
            "how can i start",
            "how do i start",
            "how to start",
            "start this project",
            "run start.sh",
            "use start.sh",
        ]
    )


def natural_doctor_request(prompt):
    normalized = prompt.lower().strip(" .!?")
    return normalized in {
        "/doctor",
        "doctor",
        "devserver doctor",
        "diagnose devserver",
        "debug devserver",
        "fix devserver",
        "fix yourself",
        "check devserver",
        "why is devserver broken",
        "why is this broken",
    }


def natural_read_path(prompt):
    if should_defer_to_agent(prompt):
        return None

    stripped = prompt.strip()
    match = re.fullmatch(r"(?i)(?:read|open|show|display|cat)(?: me)?(?: the)? file\s+(.+)", stripped)
    if not match:
        match = re.fullmatch(r"(?i)(?:read|open|show|display|cat)\s+(.+\.[A-Za-z0-9_]+)", stripped)

    if not match:
        return None

    return match.group(1).strip().strip("\"'")


def natural_search_query(prompt):
    if should_defer_to_agent(prompt):
        return None

    stripped = prompt.strip()
    patterns = [
        r"(?i)search for\s+(.+)",
        r"(?i)(?:search|find|look for)\s+(.+)",
        r"(?i)find files containing\s+(.+)",
    ]

    for pattern in patterns:
        match = re.fullmatch(pattern, stripped)
        if match:
            query = match.group(1).strip().strip("\"'")
            if query and not query.lower().startswith(("the file", "file ")):
                return query

    return None


AGENT_TASK_WORDS = {
    "analyze",
    "check",
    "compare",
    "convert",
    "create",
    "debug",
    "edit",
    "fix",
    "generate",
    "make",
    "modify",
    "review",
    "rewrite",
    "update",
    "windows",
    "work",
}


def mentioned_file_count(prompt):
    return len(
        re.findall(
            r"(?i)\b[\w.-]+\.(?:sh|ps1|bat|cmd|py|js|ts|tsx|jsx|json|md|txt|yaml|yml|toml|ini|env)\b",
            prompt,
        )
    )


def should_defer_to_agent(prompt):
    normalized = prompt.lower()
    has_task_word = any(re.search(rf"\b{re.escape(word)}\b", normalized) for word in AGENT_TASK_WORDS)
    has_multiple_files = mentioned_file_count(prompt) >= 2
    has_conjunction = any(word in normalized for word in [" and ", " then ", " if not", " otherwise "])
    asks_for_output_file = any(phrase in normalized for phrase in ["new file", "another file", ".ps1", ".bat", ".cmd"])
    asks_for_artifact_type = bool(
        re.search(
            r"(?i)\b(?:ps1|bat|cmd|toml|ya?ml|json|py|js|ts|tsx|jsx|md|txt|ini|env)\s+(?:files?|scripts?|launchers?|wrappers?)\b",
            prompt,
        )
    )
    asks_for_transformation = bool(
        re.search(
            r"(?i)\b(?:into|to|as)\s+(?:[\w.-]+\s+){0,3}(?:files?|scripts?|launchers?|wrappers?)\b",
            prompt,
        )
    )

    return (
        (has_task_word and (has_multiple_files or has_conjunction or asks_for_output_file))
        or (has_multiple_files and (asks_for_artifact_type or asks_for_transformation))
    )


def print_tree_entries(result):
    for entry in result["entries"]:
        formatter = directory_name if entry["type"] == "directory" else file_name
        print(f"  {formatter(entry['path'])}")
    print(f"{dim(str(len(result['entries'])) + ' item(s).')}")


def print_search_results(result):
    for item in result["results"]:
        path = file_name(item.get("path", ""))
        if "line" in item:
            print(f"  {path}{dim(':' + str(item['line']))} {item.get('text', '')}")
        else:
            print(f"  {path} {dim(item.get('match', ''))}")
    print(f"{dim(str(len(result['results'])) + ' result(s).')}")


def print_file_content(result):
    print(f"{dim('File')} {file_name(result['path'])}")
    print()
    print(highlight_text(result["content"]))
    if result.get("truncated"):
        print(dim("[File truncated.]"))


def print_start_script_help(state):
    try:
        result = read_file({"path": "start.sh", "max_chars": 4000}, state)
    except (FileNotFoundError, ValueError) as error:
        print(error_text(str(error)))
        return

    content = result["content"]
    first_lines = [line.strip() for line in content.splitlines() if line.strip() and not line.strip().startswith("#")]
    command_hint = "./start.sh"

    if "bash" in content.lower():
        command_hint = "bash start.sh"

    print(f"{dim('File')} {file_name(result['path'])}")
    print()
    print(f"  {command_name(command_hint)}")

    if first_lines:
        print()
        print(dim("What it appears to do:"))
        for line in first_lines[:6]:
            print(f"  {highlight_text(line)}")

    print()
    print(dim("If permission is denied, run:"))
    print(f"  {command_name('chmod +x start.sh')}")


def run_diagnostics(state):
    print(success("DevServer diagnostics"))
    print()
    print(f"  {dim('Workspace')} {directory_name(str(WORKSPACE_DIR))}")
    print(f"  {dim('Current dir')} {directory_name(str(state['cwd']))}")
    print(f"  {dim('Python')} {sys.version.split()[0]}")
    print(f"  {dim('Projects dir')} {directory_name(str(PROJECTS_DIR))}")

    try:
        __import__("ai_terminal")
        print(f"  {dim('Imports')} {success('ok')}")
    except ImportError as error:
        print(f"  {dim('Imports')} {error_text(str(error))}")

    ollama_ok = ollama_is_reachable()
    if ollama_ok:
        print(f"  {dim('Ollama')} {success('reachable')}")
        try:
            models = get_models()
            model_text = ", ".join(models[:5]) if models else "no models installed"
            print(f"  {dim('Models')} {success(model_text) if models else error_text(model_text)}")
        except OSError as error:
            print(f"  {dim('Models')} {error_text(str(error))}")
    else:
        print(f"  {dim('Ollama')} {error_text('offline at http://localhost:11434')}")

    code_command = shutil.which("code") or shutil.which("code.cmd")
    if code_command:
        print(f"  {dim('VS Code CLI')} {success(code_command)}")
    else:
        print(f"  {dim('VS Code CLI')} {error_text('not found on PATH')}")

    docker_command = shutil.which("docker")
    if docker_command:
        print(f"  {dim('Docker')} {success(docker_command)}")
    else:
        print(f"  {dim('Docker')} {error_text('not found on PATH')}")

    accounts = list_accounts()
    if accounts:
        print(f"  {dim('Web auth')} {success(str(len(accounts)) + ' account(s) configured')}")
    elif os.environ.get("DEVSERVER_WEB_USER") and os.environ.get("DEVSERVER_WEB_PASSWORD"):
        print(f"  {dim('Web auth')} {success('configured from environment')}")
    else:
        print(f"  {dim('Web auth')} {error_text('no browser accounts configured')}")

    print()
    print(dim("Running tests..."))
    test_result = run_task({"task": "python_unittest", "timeout_seconds": 90}, {"cwd": WORKSPACE_DIR})

    if test_result["exit_code"] == 0:
        print(f"  {success('Tests passed')}")
    else:
        print(f"  {error_text('Tests failed')}")
        output = "\n".join(
            line for line in (test_result["stdout"] + "\n" + test_result["stderr"]).splitlines()[-30:]
            if line.strip()
        )
        if output:
            print()
            print(highlight_text(output))

    print()
    try:
        status = git_status({}, state)
        if status.get("stdout"):
            print(dim("Git changes:"))
            print(highlight_text(status["stdout"].strip()))
        elif status.get("stderr"):
            print(error_text(status["stderr"].strip()))
        else:
            print(dim("Git changes: none"))
    except Exception as error:
        print(error_text(f"Git status failed: {error}"))

    print()
    try:
        current_status = project_status({}, state)
        project_types = ", ".join(current_status.get("project_types") or ["unknown"])
        print(dim("Project status:"))
        print(f"  {dim('Type')} {highlight_text(project_types)}")
        tasks = ", ".join(task["task"] for task in current_status.get("available_tasks", []))
        print(f"  {dim('Tasks')} {highlight_text(tasks or 'none detected')}")
        starts = ", ".join(item["command"] for item in current_status.get("start_methods", []))
        print(f"  {dim('Start')} {highlight_text(starts or 'none detected')}")
        ports = ", ".join(str(port) for port in current_status.get("ports", []))
        print(f"  {dim('Ports')} {highlight_text(ports or 'none detected')}")
    except Exception as error:
        print(error_text(f"Project status failed: {error}"))

    if any((WORKSPACE_DIR / name).exists() for name in ["docker-compose.yml", "docker-compose.yaml", "compose.yml", "compose.yaml"]):
        try:
            compose_result = run_task({"task": "docker_compose_config", "timeout_seconds": 60}, {"cwd": WORKSPACE_DIR})
            if compose_result["exit_code"] == 0:
                print(f"  {success('Docker Compose config passed')}")
            else:
                print(f"  {error_text('Docker Compose config failed')}")
        except Exception as error:
            print(f"  {error_text('Docker Compose config skipped: ' + str(error))}")

    print()
    if test_result["exit_code"] == 0 and ollama_ok:
        print(success("No DevServer code failure detected."))
    elif test_result["exit_code"] == 0:
        print(dim("DevServer tests pass. Start Ollama if model chats are failing."))
    else:
        print(dim("The failing test output above is the next thing to fix."))


def handle_command(prompt, state, registry, model_helpers):
    command = prompt.lower().strip()

    if command in ["exit", "quit", "/bye"]:
        return "exit"

    if command == "/account" or command.startswith("/account "):
        handle_account_command(prompt)
        return "handled"

    if os.environ.get("DEVSERVER_WEB_TERMINAL") == "1" and is_project_open_request(prompt):
        print(error_text('Projects cannot be opened from the web terminal. From the web terminal, ask me to "start", "host", or "run" the project instead.'))
        return "handled"

    project_choice = parse_project_choice(prompt)
    if project_choice is not None and state.get("pending_project_choices"):
        open_project_choice(project_choice, state)
        return "handled"

    if state.get("pending_project_choices") and is_pending_project_list_reference(prompt):
        print_project_choice_entries(state["pending_project_choices"], PROJECTS_DIR)
        return "handled"

    matched_pending_project = pending_project_match(prompt, state)
    if is_dockerize_request(prompt):
        project_path = matched_pending_project or dockerize_target_for_request(prompt, state)
        try:
            build = any(word in command for word in ["build", "rebuild"])
            overwrite = any(word in command for word in ["overwrite", "replace", "regenerate"])
            with WorkingStatus() as status:
                result = dockerize_project(
                    {
                        "path": str(project_path),
                        "overwrite": overwrite,
                        "validate": True,
                        "build": build,
                    },
                    state,
                )
            state["last_project_path"] = Path(result["path"])
            print_dockerize_result(result)
        except Exception as error:
            print(error_text(str(error)))
        return "handled"

    if is_project_start_request(prompt):
        project_path = project_target_for_request(prompt, state)
        if project_path:
            try:
                result = start_project({"path": str(project_path)}, state)
                state["last_project_path"] = Path(result["path"])
                print_project_start_result(result)
            except Exception as error:
                print(error_text(str(error)))
            return "handled"

    if matched_pending_project and is_project_about_question(prompt):
        try:
            print_project_summary(matched_pending_project)
            state["last_project_path"] = matched_pending_project.resolve()
        except OSError as error:
            print(error_text(str(error)))
        return "handled"

    if matched_pending_project and not is_project_listing_request(prompt):
        state["last_project_path"] = matched_pending_project.resolve()
        use_project_path(matched_pending_project, state)
        return "handled"

    if project_choice is None and state.get("pending_project_choices"):
        state.pop("pending_project_choices", None)

    if command == "/models":
        print_model_list(state["models"], state.get("active_model"))
        return "handled"

    if command == "/model" or command.startswith("/model "):
        models = state.get("models", [])

        if not models:
            models = model_helpers["list_models"]()
            state["models"] = models

        choice = prompt.split(maxsplit=1)[1].strip() if command.startswith("/model ") else ""

        if not choice:
            print_model_list(models, state.get("active_model"))
            choice = input(f"{dim('Switch to model number/name:')} ").strip()

        new_model = resolve_model_choice(choice, models)

        if not new_model:
            print(error_text("Model not found. Use /models to see available models."))
            return "handled"

        state["active_model"] = new_model
        print(f"{dim('Active model')} {success(new_model)}")

        return "handled"

    if command == "/orchestrator" or command.startswith("/orchestrator "):
        models = state.get("models", [])

        if not models:
            models = model_helpers["list_models"]()
            state["models"] = models

        choice = prompt.split(maxsplit=1)[1].strip() if command.startswith("/orchestrator ") else ""

        if not choice:
            print_model_list(models, state.get("orchestrator_model"))
            choice = input(f"{dim('Switch orchestrator to number/name:')} ").strip()

        new_model = resolve_model_choice(choice, models)

        if not new_model:
            print(error_text("Model not found. Use /models to see available models."))
            return "handled"

        state["orchestrator_model"] = new_model
        print(f"{dim('Orchestrator')} {success(new_model)}")

        return "handled"

    if command == "/clear":
        state["history"].clear()
        print(success("Cleared chat history."))
        return "handled"

    if command == "/history":
        for item in state["history"]:
            print(f"{item['role']}: {item['content']}")

        return "handled"

    if command == "/tools":
        for tool in registry.values():
            print(f"  {command_name(tool.name)} {dim(tool.description)}")
        return "handled"

    if natural_doctor_request(prompt):
        try:
            run_diagnostics(state)
        except subprocess.TimeoutExpired:
            print(error_text("Diagnostics timed out while running tests."))
        except OSError as error:
            print(error_text(str(error)))
        return "handled"

    if command == "/tool-log":
        for entry in state.get("tool_log", []):
            print(entry)
        return "handled"

    if prompt.startswith("!"):
        call = {"tool": "run_shell", "args": {"command": prompt[1:]}}
        result = model_helpers["execute_tool_call"](call)
        print_shell_result(result)
        return "handled"

    if parse_git_clone_request(prompt):
        clone_repository(prompt, state)
        return "handled"

    if is_direct_shell_command(prompt):
        call = {"tool": "run_shell", "args": {"command": prompt}}
        result = model_helpers["execute_tool_call"](call)
        print_shell_result(result)
        return "handled"

    if command in {"pwd", "cd"}:
        print(directory_name(str(state["cwd"])))
        return "handled"

    if natural_pwd_request(prompt):
        print(directory_name(str(state["cwd"])))
        return "handled"

    if natural_project_listing_request(prompt):
        projects_root = PROJECTS_DIR
        try:
            state["pending_project_choices"] = print_project_entries(projects_root)
        except OSError as error:
            print(error_text(str(error)))
        return "handled"

    if natural_start_script_request(prompt):
        print_start_script_help(state)
        return "handled"

    if command.startswith("cd "):
        try:
            result = set_cwd({"path": prompt[3:].strip().strip('"')}, state)
            print(f"{dim('Directory')} {directory_name(result['cwd'])}")
            state["history"].append(
                {
                    "role": "system",
                    "content": f"Current directory changed to: {result['cwd']}",
                }
            )
        except (FileNotFoundError, ValueError) as error:
            print(error_text(str(error)))
        return "handled"

    if command in {"dir", "ls"} or command.startswith("dir ") or command.startswith("ls "):
        if command in {"dir", "ls"}:
            raw_path = "."
        else:
            raw_path = prompt.split(maxsplit=1)[1].strip()

        try:
            result = list_dir({"path": raw_path}, state)
            print_directory_entries(result)
        except (FileNotFoundError, ValueError) as error:
            print(error_text(str(error)))
        return "handled"

    list_dir_path = parse_list_dir_call(prompt)
    if list_dir_path is not None or is_natural_directory_listing(prompt):
        raw_path = list_dir_path if list_dir_path is not None else "."
        try:
            result = list_dir({"path": raw_path}, state)
            print_directory_entries(result)
        except (FileNotFoundError, ValueError) as error:
            print(error_text(str(error)))
        return "handled"

    tree_path = natural_tree_path(prompt)
    if tree_path is not None:
        try:
            result = tree({"path": tree_path}, state)
            print_tree_entries(result)
        except (FileNotFoundError, ValueError) as error:
            print(error_text(str(error)))
        return "handled"

    read_path = natural_read_path(prompt)
    if read_path is not None:
        try:
            result = read_file({"path": read_path}, state)
            print_file_content(result)
        except (FileNotFoundError, ValueError) as error:
            print(error_text(str(error)))
        return "handled"

    search_query = natural_search_query(prompt)
    if search_query is not None:
        try:
            result = search_files({"query": search_query, "path": "."}, state)
            print_search_results(result)
        except (FileNotFoundError, ValueError) as error:
            print(error_text(str(error)))
        return "handled"

    if natural_git_status_request(prompt):
        result = git_status({}, state)
        if result.get("stdout"):
            print(highlight_text(result["stdout"].strip()))
        else:
            print(dim("No git changes."))
        if result.get("stderr"):
            print(error_text(result["stderr"].strip()))
        return "handled"

    if command.startswith("/files"):
        try:
            result = list_dir({"path": prompt[6:].strip() or "."}, state)
            print_directory_entries(result)
        except (FileNotFoundError, ValueError) as error:
            print(error_text(str(error)))
        return "handled"

    if command.startswith("/tree"):
        try:
            result = tree({"path": prompt[5:].strip() or "."}, state)
            print_tree_entries(result)
        except (FileNotFoundError, ValueError) as error:
            print(error_text(str(error)))
        return "handled"

    if command.startswith("/search "):
        parts = prompt.split(maxsplit=2)
        if len(parts) < 2:
            print(error_text("Usage: /search <text> [directory]"))
            return "handled"

        query = parts[1]
        raw_dir = parts[2] if len(parts) == 3 else "."
        try:
            result = search_files({"query": query, "path": raw_dir}, state)
            print_search_results(result)
        except (FileNotFoundError, ValueError) as error:
            print(error_text(str(error)))
        return "handled"

    if command == "/search":
        print(error_text("Usage: /search <text> [directory]"))
        return "handled"

    if command.startswith("/read "):
        raw_path = prompt[6:].strip()
        if not raw_path:
            print(error_text("Usage: /read <path>"))
            return "handled"

        try:
            result = read_file({"path": raw_path}, state)
        except (FileNotFoundError, ValueError) as error:
            print(error_text(str(error)))
            return "handled"

        state["history"].append(
            {
                "role": "system",
                "content": f"File loaded: {result['path']}\n\n```text\n{result['content']}\n```",
            }
        )
        print(f"{success('Loaded')} {file_name(result['path'])} {dim('into chat context.')}")
        return "handled"

    if command == "/read":
        print(error_text("Usage: /read <path>"))
        return "handled"

    return None
