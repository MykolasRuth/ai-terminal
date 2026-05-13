import json
import os
import platform
import re
import shutil
import socket
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from .config import (
    MAX_FILE_CHARS,
    MAX_SEARCH_RESULTS,
    MAX_TOOL_OUTPUT_CHARS,
    MAX_TREE_ENTRIES,
    PROJECTS_DIR,
    SKIP_DIRS,
    WORKSPACE_DIR,
)
from .policy import resolve_path, stringify_command


@dataclass
class Tool:
    name: str
    description: str
    handler: object


def should_skip_path(path):
    return any(part in SKIP_DIRS for part in path.parts)


def normalize_path_name(name):
    return "".join(character for character in name.lower() if character.isalnum())


PATH_FILLER_WORDS = {
    "a",
    "an",
    "codebase",
    "current",
    "folder",
    "in",
    "my",
    "project",
    "repo",
    "repository",
    "the",
    "this",
    "workspace",
}


def normalized_path_candidates(raw_text):
    words = [
        word
        for word in raw_text.replace("-", " ").replace("_", " ").split()
        if word.lower() not in PATH_FILLER_WORDS
    ]
    compact = normalize_path_name(raw_text)
    cleaned = normalize_path_name(" ".join(words))
    return [item for item in [compact, cleaned] if item]


def project_entries(include_hidden=False):
    entries = []

    for path in PROJECTS_DIR.iterdir():
        if not path.is_dir():
            continue
        if not include_hidden and path.name.startswith("."):
            continue
        entries.append(path.resolve())

    return sorted(entries, key=lambda item: item.name.lower())


def project_query_tokens(query):
    words = [
        word.lower()
        for word in query.replace("-", " ").replace("_", " ").split()
        if word.lower() not in PATH_FILLER_WORDS and len(word) > 1
    ]
    return words


def is_current_project_query(query):
    normalized = query.lower()
    return any(
        phrase in normalized
        for phrase in [
            "this project",
            "this folder",
            "this directory",
            "current project",
            "current folder",
            "current directory",
        ]
    )


def score_project_match(query, project_path):
    query_candidates = normalized_path_candidates(query)
    project_name = normalize_path_name(project_path.name)
    tokens = project_query_tokens(query)

    if project_name in query_candidates:
        return 100

    score = 0
    for candidate in query_candidates:
        if candidate and candidate == project_name:
            score = max(score, 100)
        elif candidate and candidate in project_name:
            score = max(score, 80)
        elif candidate and project_name in candidate:
            score = max(score, 70)

    project_name_lower = project_path.name.lower().replace("-", " ").replace("_", " ")
    matched_tokens = [token for token in tokens if token in project_name_lower]
    if tokens and len(matched_tokens) == len(tokens):
        score = max(score, 90)
    elif matched_tokens:
        score = max(score, 20 + (50 * len(matched_tokens) // len(tokens)))

    return score


def resolve_project_query(query, state):
    query = str(query or "").strip()

    if not query or query in {".", "./"} or is_current_project_query(query):
        path = Path(state["cwd"]).resolve()
        if not path.exists() or not path.is_dir():
            raise FileNotFoundError(f"{path} does not exist.")
        return path, []

    projects = project_entries()
    scored = [
        (score_project_match(query, project), project)
        for project in projects
    ]
    matches = [(score, project) for score, project in scored if score > 0]
    matches.sort(key=lambda item: (-item[0], item[1].name.lower()))

    if not matches:
        available = ", ".join(project.name for project in projects) or "none"
        raise FileNotFoundError(f"No project matched '{query}'. Available projects: {available}")

    best_score, best_project = matches[0]
    tied = [project for score, project in matches if score == best_score]
    if len(tied) > 1:
        names = ", ".join(project.name for project in tied)
        raise ValueError(f"Project name is ambiguous for '{query}'. Matches: {names}")

    return best_project, [
        {"name": project.name, "path": str(project), "score": score}
        for score, project in matches[:5]
    ]


def find_loose_directory_match(raw_path, base_dir):
    raw_text = str(raw_path).strip().strip("\"'")

    if not raw_text or any(separator in raw_text for separator in ["/", "\\"]):
        return None

    candidates = normalized_path_candidates(raw_text)
    if not candidates:
        return None

    search_roots = []
    for root in [base_dir, PROJECTS_DIR, WORKSPACE_DIR]:
        root = Path(root).resolve()
        if root not in search_roots:
            search_roots.append(root)

    for root in search_roots:
        if not root.exists() or not root.is_dir():
            continue

        for child in root.iterdir():
            if child.is_dir() and normalize_path_name(child.name) in candidates:
                return child.resolve()

    matches = []
    for root in search_roots:
        if not root.exists() or not root.is_dir():
            continue

        for child in root.iterdir():
            if not child.is_dir():
                continue

            normalized_child = normalize_path_name(child.name)
            if any(candidate in normalized_child or normalized_child in candidate for candidate in candidates):
                matches.append(child.resolve())

    unique_matches = []
    for match in matches:
        if match not in unique_matches:
            unique_matches.append(match)

    if len(unique_matches) == 1:
        return unique_matches[0]

    return None


def truncate(text, max_chars=MAX_TOOL_OUTPUT_CHARS):
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "\n\n[Output truncated.]"


def get_cwd(args, state):
    return {"cwd": str(state["cwd"])}


def set_cwd(args, state):
    raw_path = args["path"]
    path = resolve_path(raw_path, state["cwd"])

    if not path.exists():
        loose_match = find_loose_directory_match(raw_path, state["cwd"])
        if loose_match:
            path = loose_match

    if not path.exists():
        raise FileNotFoundError(f"{path} does not exist.")

    if not path.is_dir():
        raise ValueError(f"{path} is not a directory.")

    state["cwd"] = path
    return {"cwd": str(path)}


def list_dir(args, state):
    root = resolve_path(args.get("path", "."), state["cwd"])
    include_hidden = bool(args.get("include_hidden", False))

    if not root.exists():
        raise FileNotFoundError(f"{root} does not exist.")

    if not root.is_dir():
        raise ValueError(f"{root} is not a directory.")

    entries = []
    for path in root.iterdir():
        if should_skip_path(path):
            continue
        if not include_hidden and path.name.startswith("."):
            continue

        entries.append(
            {
                "name": path.name,
                "path": str(path),
                "type": "directory" if path.is_dir() else "file",
            }
        )

    return {"path": str(root), "entries": sorted(entries, key=lambda item: item["name"].lower())}


def list_projects(args, state):
    include_hidden = bool(args.get("include_hidden", False))
    entries = []

    for path in project_entries(include_hidden):
        entries.append(
            {
                "name": path.name,
                "path": str(path),
                "type": "directory",
            }
        )

    return {"path": str(PROJECTS_DIR), "entries": entries}


def tree(args, state):
    root = resolve_path(args.get("path", "."), state["cwd"])
    max_depth = int(args.get("max_depth", 4))
    max_entries = int(args.get("max_entries", MAX_TREE_ENTRIES))

    if not root.exists():
        raise FileNotFoundError(f"{root} does not exist.")

    if not root.is_dir():
        raise ValueError(f"{root} is not a directory.")

    entries = []

    def walk(current, depth):
        if len(entries) >= max_entries or depth > max_depth:
            return

        for path in sorted(current.iterdir(), key=lambda item: item.name.lower()):
            if should_skip_path(path):
                continue

            entries.append(
                {
                    "path": str(path.relative_to(root)),
                    "type": "directory" if path.is_dir() else "file",
                }
            )

            if len(entries) >= max_entries:
                return

            if path.is_dir():
                walk(path, depth + 1)

    walk(root, 1)
    return {"path": str(root), "entries": entries, "truncated": len(entries) >= max_entries}


def search_files(args, state):
    root = resolve_path(args.get("path", "."), state["cwd"])
    query = args["query"]
    include_contents = bool(args.get("include_contents", True))
    max_results = int(args.get("max_results", MAX_SEARCH_RESULTS))

    if not root.exists():
        raise FileNotFoundError(f"{root} does not exist.")

    if not root.is_dir():
        raise ValueError(f"{root} is not a directory.")

    results = []
    query_lower = query.lower()

    for path in root.rglob("*"):
        if should_skip_path(path):
            continue

        relative = path.relative_to(root)

        if query_lower in str(relative).lower():
            results.append({"path": str(relative), "match": "filename"})

        if include_contents and path.is_file():
            try:
                lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
            except OSError:
                lines = []

            for line_number, line in enumerate(lines, 1):
                if query_lower in line.lower():
                    results.append(
                        {
                            "path": str(relative),
                            "line": line_number,
                            "text": line.strip(),
                        }
                    )

                if len(results) >= max_results:
                    return {"path": str(root), "results": results, "truncated": True}

        if len(results) >= max_results:
            return {"path": str(root), "results": results, "truncated": True}

    return {"path": str(root), "results": results, "truncated": False}


def read_file(args, state):
    path = resolve_path(args["path"], state["cwd"])
    max_chars = int(args.get("max_chars", MAX_FILE_CHARS))

    if not path.exists():
        raise FileNotFoundError(f"{path} does not exist.")

    if not path.is_file():
        raise ValueError(f"{path} is not a file.")

    content = path.read_text(encoding="utf-8", errors="replace")
    truncated = len(content) > max_chars

    return {
        "path": str(path),
        "content": content[:max_chars],
        "truncated": truncated,
    }


def read_many_files(args, state):
    paths = args.get("paths") or []
    max_chars = int(args.get("max_chars", MAX_FILE_CHARS))

    if not paths:
        raise ValueError("paths is required.")

    files = []
    for raw_path in paths:
        try:
            files.append(read_file({"path": raw_path, "max_chars": max_chars}, state))
        except Exception as error:
            files.append(
                {
                    "path": str(resolve_path(raw_path, state["cwd"])),
                    "ok": False,
                    "error": {"type": type(error).__name__, "message": str(error)},
                }
            )

    return {"cwd": str(state["cwd"]), "files": files}


def project_snapshot(args, state):
    root = resolve_path(args.get("path", "."), state["cwd"])
    max_chars = int(args.get("max_chars", 3000))

    if not root.exists():
        raise FileNotFoundError(f"{root} does not exist.")

    if not root.is_dir():
        raise ValueError(f"{root} is not a directory.")

    entries = list_dir({"path": str(root), "include_hidden": False}, {"cwd": state["cwd"]})["entries"]
    key_names = {
        "README.md",
        "package.json",
        "requirements.txt",
        "pyproject.toml",
        "Pipfile",
        "start.sh",
        "end.sh",
        "start.ps1",
        "end.ps1",
        "Dockerfile",
        "docker-compose.yml",
    }
    key_files = []

    for entry in entries:
        if entry["type"] != "file" or entry["name"] not in key_names:
            continue
        try:
            key_files.append(read_file({"path": entry["path"], "max_chars": max_chars}, {"cwd": root}))
        except Exception as error:
            key_files.append(
                {
                    "path": entry["path"],
                    "ok": False,
                    "error": {"type": type(error).__name__, "message": str(error)},
                }
            )

    return {
        "path": str(root),
        "entries": entries,
        "key_files": key_files,
    }


def project_status(args, state):
    root = resolve_path(args.get("path", "."), state["cwd"])

    if not root.exists():
        raise FileNotFoundError(f"{root} does not exist.")

    if not root.is_dir():
        raise ValueError(f"{root} is not a directory.")

    key_files = []
    for name in [
        "README.md",
        "START.md",
        "ARCHITECTURE.md",
        "package.json",
        "requirements.txt",
        "pyproject.toml",
        "Dockerfile",
        "docker-compose.yml",
        "compose.yml",
        "start.sh",
        "start.ps1",
    ]:
        path = root / name
        if path.exists():
            key_files.append({"name": name, "path": str(path), "type": "directory" if path.is_dir() else "file"})

    package_scripts = package_json_scripts(root)
    project_types = []
    if (root / "package.json").exists():
        project_types.append("node")
    if (root / "requirements.txt").exists() or (root / "pyproject.toml").exists():
        project_types.append("python")
    if any((root / name).exists() for name in ["docker-compose.yml", "docker-compose.yaml", "compose.yml", "compose.yaml", "Dockerfile"]):
        project_types.append("docker")

    start_methods = []
    if any((root / name).exists() for name in ["docker-compose.yml", "docker-compose.yaml", "compose.yml", "compose.yaml"]):
        start_methods.append({"type": "docker_compose", "command": "docker compose up"})
    if (root / "Dockerfile").exists():
        start_methods.append({"type": "dockerfile", "command": "docker build -t <image> ."})
    for script_name in ["start.ps1", "start.sh", "start.bat", "start.cmd"]:
        if (root / script_name).exists():
            start_methods.append({"type": "script", "command": script_name})
    if "dev" in package_scripts:
        start_methods.append({"type": "npm_script", "command": "npm run dev"})
    if "start" in package_scripts:
        start_methods.append({"type": "npm_script", "command": "npm start"})

    task_hints = []
    if (root / "tests").exists():
        task_hints.extend(["python_unittest", "python_compile"])
    elif "python" in project_types:
        task_hints.append("python_compile")
    if any((root / name).exists() for name in ["docker-compose.yml", "docker-compose.yaml", "compose.yml", "compose.yaml"]):
        task_hints.append("docker_compose_config")
    if "test" in package_scripts:
        task_hints.append("npm_test")

    git = run_git({"max_chars": 6000}, {"cwd": root}, ["status", "--short"])

    return {
        "path": str(root),
        "workspace": str(WORKSPACE_DIR),
        "projects_dir": str(PROJECTS_DIR),
        "project_types": project_types,
        "key_files": key_files,
        "ports": detect_project_ports(root),
        "start_methods": start_methods,
        "docker_available": bool(shutil.which("docker")),
        "package_scripts": sorted(package_scripts),
        "available_tasks": [
            {
                "task": name,
                "description": RUN_TASKS[name]["description"],
                "args_schema": RUN_TASKS[name]["args_schema"],
            }
            for name in sorted(set(["git_status", *task_hints]))
            if name in RUN_TASKS
        ],
        "git": git,
    }


def read_file_metadata(args, state):
    path = resolve_path(args["path"], state["cwd"])

    if not path.exists():
        raise FileNotFoundError(f"{path} does not exist.")

    stat = path.stat()
    return {
        "path": str(path),
        "type": "directory" if path.is_dir() else "file",
        "size": stat.st_size,
        "modified": stat.st_mtime,
        "suffix": path.suffix,
    }


def write_file(args, state):
    path = resolve_path(args["path"], state["cwd"])

    if args.get("create_parent_dirs", False):
        path.parent.mkdir(parents=True, exist_ok=True)

    path.write_text(args.get("content", ""), encoding="utf-8")
    return {"path": str(path), "bytes": path.stat().st_size}


def create_file(args, state):
    path = resolve_path(args["path"], state["cwd"])
    overwrite = bool(args.get("overwrite", False))

    if path.exists() and not overwrite:
        raise FileExistsError(f"{path} already exists. Use overwrite=true to replace it.")

    if args.get("create_parent_dirs", True):
        path.parent.mkdir(parents=True, exist_ok=True)

    path.write_text(args.get("content", ""), encoding="utf-8")
    return {"path": str(path), "bytes": path.stat().st_size, "created": True}


def edit_file(args, state):
    path = resolve_path(args["path"], state["cwd"])

    if not path.exists():
        raise FileNotFoundError(f"{path} does not exist.")

    if not path.is_file():
        raise ValueError(f"{path} is not a file.")

    expected = args.get("expected_content")
    current = path.read_text(encoding="utf-8", errors="replace")
    if expected is not None and current != expected:
        raise ValueError("File content did not match expected_content; read the file again before editing.")

    path.write_text(args.get("content", ""), encoding="utf-8")
    return {"path": str(path), "bytes": path.stat().st_size, "edited": True}


def replace_in_file(args, state):
    path = resolve_path(args["path"], state["cwd"])
    old_text = args["old_text"]
    new_text = args.get("new_text", "")
    count = int(args.get("count", 1))

    if not path.exists():
        raise FileNotFoundError(f"{path} does not exist.")

    if not path.is_file():
        raise ValueError(f"{path} is not a file.")

    content = path.read_text(encoding="utf-8", errors="replace")
    occurrences = content.count(old_text)
    if occurrences == 0:
        raise ValueError("old_text was not found in the file.")

    if count <= 0:
        updated = content.replace(old_text, new_text)
        replacements = occurrences
    else:
        updated = content.replace(old_text, new_text, count)
        replacements = min(count, occurrences)

    path.write_text(updated, encoding="utf-8")
    return {"path": str(path), "replacements": replacements, "bytes": path.stat().st_size}


def write_many_files(args, state):
    files = args.get("files") or []
    default_overwrite = bool(args.get("overwrite", False))
    create_parent_dirs = bool(args.get("create_parent_dirs", True))

    if not files:
        raise ValueError("files is required.")

    planned = []
    for item in files:
        path = resolve_path(item["path"], state["cwd"])
        overwrite = bool(item.get("overwrite", default_overwrite))
        if path.exists() and not overwrite:
            raise FileExistsError(f"{path} already exists. Use overwrite=true to replace it.")
        planned.append((path, item.get("content", "")))

    written = []
    for path, content in planned:
        if create_parent_dirs:
            path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        written.append({"path": str(path), "bytes": path.stat().st_size})

    return {"written": written}


def append_file(args, state):
    path = resolve_path(args["path"], state["cwd"])

    with path.open("a", encoding="utf-8") as file:
        file.write(args.get("content", ""))

    return {"path": str(path), "bytes": path.stat().st_size}


def apply_patch_tool(args, state):
    patch = args["patch"]
    result = subprocess.run(
        ["git", "apply", "--whitespace=nowarn", "-"],
        cwd=state["cwd"],
        text=True,
        input=patch,
        capture_output=True,
        timeout=30,
    )

    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "git apply failed.")

    return {
        "exit_code": result.returncode,
        "stdout": truncate(result.stdout),
        "stderr": truncate(result.stderr),
    }


def copy_path(args, state):
    source = resolve_path(args["source"], state["cwd"])
    destination = resolve_path(args["destination"], state["cwd"])
    overwrite = bool(args.get("overwrite", False))

    if destination.exists() and not overwrite:
        raise FileExistsError(f"{destination} already exists.")

    if source.is_dir():
        shutil.copytree(source, destination, dirs_exist_ok=overwrite)
    else:
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)

    return {"source": str(source), "destination": str(destination)}


def move_path(args, state):
    source = resolve_path(args["source"], state["cwd"])
    destination = resolve_path(args["destination"], state["cwd"])

    if destination.exists() and not args.get("overwrite", False):
        raise FileExistsError(f"{destination} already exists.")

    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(source), str(destination))
    return {"source": str(source), "destination": str(destination)}


def delete_path(args, state):
    path = resolve_path(args["path"], state["cwd"])
    recursive = bool(args.get("recursive", False))

    if path.is_dir():
        if recursive:
            raise PermissionError("Recursive deletion is blocked by default.")
        path.rmdir()
    else:
        path.unlink()

    return {"path": str(path), "deleted": True}


def run_shell(args, state):
    command = stringify_command(args.get("command", "")).strip()
    timeout = int(args.get("timeout_seconds", 30))

    if not command:
        raise ValueError("command is required.")

    env = os.environ.copy()
    python_dir = str(Path(sys.executable).resolve().parent)
    env["PATH"] = python_dir + os.pathsep + env.get("PATH", "")

    result = subprocess.run(
        [*shell_command_prefix(), command],
        cwd=state["cwd"],
        text=True,
        capture_output=True,
        timeout=timeout,
        env=env,
    )

    return {
        "command": command,
        "cwd": str(state["cwd"]),
        "exit_code": result.returncode,
        "stdout": truncate(result.stdout),
        "stderr": truncate(result.stderr),
    }


RUN_TASKS = {
    "git_status": {
        "description": "Show git status for the current project.",
        "timeout": 30,
        "args_schema": {},
    },
    "git_diff": {
        "description": "Show a bounded git diff for the current project.",
        "timeout": 30,
        "args_schema": {"path": "optional path", "staged": "optional boolean", "max_chars": "optional integer"},
    },
    "python_unittest": {
        "description": "Run Python unittest discovery.",
        "timeout": 120,
        "args_schema": {"start_dir": "optional directory, default tests", "pattern": "optional file glob"},
    },
    "python_compile": {
        "description": "Compile a Python package or directory.",
        "timeout": 60,
        "args_schema": {"path": "optional package/directory, default ai_terminal"},
    },
    "docker_compose_config": {
        "description": "Validate docker compose configuration.",
        "timeout": 60,
        "args_schema": {},
    },
    "npm_test": {
        "description": "Run npm test when package.json defines a test script.",
        "timeout": 120,
        "args_schema": {},
    },
}


def safe_git_command(state, git_args):
    return ["git", "-c", "safe.directory=*", *git_args]


def safe_task_path(raw_path, state, default):
    raw_path = str(raw_path or default).strip()
    if not raw_path:
        raw_path = default
    path = resolve_path(raw_path, state["cwd"])
    if not path.exists():
        raise FileNotFoundError(f"{path} does not exist.")
    return path


def reject_unknown_task_args(task, provided, allowed):
    unknown = sorted(set(provided) - set(allowed) - {"task", "timeout_seconds"})
    if unknown:
        raise ValueError(f"{task} does not accept argument(s): {', '.join(unknown)}")


def package_json_scripts(root):
    package_json = root / "package.json"
    data = read_json_file(package_json) if package_json.exists() else {}
    scripts = data.get("scripts") if isinstance(data, dict) else {}
    return scripts if isinstance(scripts, dict) else {}


def build_task_command(task, args, state):
    if task == "git_status":
        reject_unknown_task_args(task, args, [])
        return safe_git_command(state, ["status", "--short"])

    if task == "git_diff":
        reject_unknown_task_args(task, args, ["path", "staged", "max_chars"])
        command = ["diff"]
        if bool(args.get("staged", False)):
            command.append("--staged")
        if args.get("path"):
            command.extend(["--", str(args["path"])])
        return safe_git_command(state, command)

    if task == "python_unittest":
        reject_unknown_task_args(task, args, ["start_dir", "pattern"])
        start_dir = safe_task_path(args.get("start_dir"), state, "tests")
        if not start_dir.is_dir():
            raise ValueError(f"{start_dir} is not a directory.")
        command = [sys.executable, "-m", "unittest", "discover", "-s", str(start_dir)]
        pattern = str(args.get("pattern", "")).strip()
        if pattern:
            if any(part in pattern for part in ["/", "\\", ":", ".."]):
                raise ValueError("pattern must be a file glob such as test_*.py.")
            command.extend(["-p", pattern])
        return command

    if task == "python_compile":
        reject_unknown_task_args(task, args, ["path"])
        path = safe_task_path(args.get("path"), state, "ai_terminal")
        return [sys.executable, "-m", "compileall", str(path)]

    if task == "docker_compose_config":
        reject_unknown_task_args(task, args, [])
        if not any((Path(state["cwd"]) / name).exists() for name in ["docker-compose.yml", "docker-compose.yaml", "compose.yml", "compose.yaml"]):
            raise FileNotFoundError("No Docker Compose file found in the current directory.")
        return ["docker", "compose", "config"]

    if task == "npm_test":
        reject_unknown_task_args(task, args, [])
        scripts = package_json_scripts(Path(state["cwd"]))
        if "test" not in scripts:
            raise FileNotFoundError("package.json does not define a test script.")
        return ["npm", "test"]

    raise ValueError(f"Unknown task '{task}'.")


def run_command_list(command, state, timeout, max_chars=MAX_TOOL_OUTPUT_CHARS):
    result = subprocess.run(
        command,
        cwd=state["cwd"],
        text=True,
        capture_output=True,
        timeout=timeout,
    )

    return {
        "command": " ".join(str(part) for part in command),
        "cwd": str(state["cwd"]),
        "exit_code": result.returncode,
        "stdout": truncate(result.stdout, max_chars),
        "stderr": truncate(result.stderr, max_chars),
    }


def run_task(args, state):
    task = str(args.get("task", "")).strip()
    if not task:
        return {
            "available_tasks": [
                {
                    "task": name,
                    "description": config["description"],
                    "args_schema": config["args_schema"],
                }
                for name, config in RUN_TASKS.items()
            ]
        }

    if task not in RUN_TASKS:
        available = ", ".join(sorted(RUN_TASKS))
        raise ValueError(f"Unknown task '{task}'. Available tasks: {available}")

    command = build_task_command(task, args, state)
    config = RUN_TASKS[task]
    timeout = int(args.get("timeout_seconds", config["timeout"]))
    max_chars = int(args.get("max_chars", MAX_TOOL_OUTPUT_CHARS))
    return {
        "task": task,
        "description": config["description"],
        **run_command_list(command, state, timeout, max_chars=max_chars),
    }


def detect_project_ports(root):
    ports = set()
    candidate_names = {
        "start.ps1",
        "start.bat",
        "start.cmd",
        "start.sh",
        "docker-compose.yml",
        "docker-compose.yaml",
        "compose.yml",
        "compose.yaml",
        "package.json",
        "vite.config.js",
        "vite.config.ts",
        ".env",
    }

    for path in root.iterdir():
        if not path.is_file() or path.name not in candidate_names:
            continue

        try:
            content = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue

        for match in re.findall(r"(?<!\d)([1-9]\d{3,4})(?!\d)", content):
            port = int(match)
            if 1024 <= port <= 65535:
                ports.add(port)

    return sorted(ports)


def read_json_file(path):
    try:
        return json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, json.JSONDecodeError):
        return {}


def first_existing_file(root, names):
    for name in names:
        path = root / name
        if path.exists() and path.is_file():
            return path
    return None


def text_file_contains(path, patterns):
    try:
        content = path.read_text(encoding="utf-8", errors="replace").lower()
    except OSError:
        return False
    return any(pattern in content for pattern in patterns)


def detect_python_entry(root):
    candidates = [
        path
        for path in root.glob("*.py")
        if path.is_file() and path.name not in {"setup.py", "config.py"}
    ]

    for path in candidates:
        try:
            content = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        lowered = content.lower()
        module = path.stem
        if "fastapi(" in lowered:
            return {"kind": "fastapi", "module": module, "port": 8000}
        if "flask(" in lowered:
            return {"kind": "flask", "module": module, "port": 5000}
        if "streamlit" in lowered:
            return {"kind": "streamlit", "module": module, "port": 8501}

    entry = first_existing_file(root, ["app.py", "main.py", "server.py", "desktop.py"])
    if entry:
        return {"kind": "python", "module": entry.name, "port": 8000}

    return {"kind": "python", "module": "", "port": 8000}


def detect_node_entry(root):
    package = read_json_file(root / "package.json")
    scripts = package.get("scripts") if isinstance(package, dict) else {}
    scripts_text = json.dumps(scripts).lower() if isinstance(scripts, dict) else ""

    if "vite" in scripts_text or (root / "vite.config.ts").exists() or (root / "vite.config.js").exists():
        return {"kind": "vite", "port": 5173, "script": "dev"}

    if "next" in scripts_text or (root / "next.config.js").exists() or (root / "next.config.mjs").exists():
        return {"kind": "next", "port": 3000, "script": "dev"}

    if isinstance(scripts, dict) and "start" in scripts:
        return {"kind": "node", "port": 3000, "script": "start"}

    if isinstance(scripts, dict) and "dev" in scripts:
        return {"kind": "node", "port": 3000, "script": "dev"}

    return {"kind": "node", "port": 3000, "script": "start"}


def detect_service(root):
    ports = detect_project_ports(root)
    if (root / "package.json").exists():
        info = detect_node_entry(root)
        info["type"] = "node"
        info["port"] = ports[0] if ports else info["port"]
        return info

    if (root / "requirements.txt").exists() or (root / "pyproject.toml").exists() or first_existing_file(root, ["app.py", "main.py", "server.py"]):
        info = detect_python_entry(root)
        info["type"] = "python"
        info["port"] = ports[0] if ports else info["port"]
        return info

    if list(root.glob("*.csproj")):
        return {"type": "dotnet", "kind": "dotnet", "port": ports[0] if ports else 8080}

    return None


def service_name_for(path, root):
    if path == root:
        return "app"
    name = normalize_path_name(path.name)
    return name or "app"


def discover_docker_services(root):
    services = []
    root_info = detect_service(root)
    if root_info:
        services.append({"name": service_name_for(root, root), "path": root, **root_info})

    if services and (root / "package.json").exists():
        return services

    for child in sorted(root.iterdir(), key=lambda item: item.name.lower()):
        if not child.is_dir() or should_skip_path(child):
            continue
        info = detect_service(child)
        if info:
            services.append({"name": service_name_for(child, root), "path": child, **info})

    unique = []
    seen = set()
    for service in services:
        key = str(service["path"])
        if key not in seen:
            seen.add(key)
            unique.append(service)
    return unique


def dockerignore_content():
    return "\n".join(
        [
            ".git",
            ".venv",
            "venv",
            "__pycache__",
            "*.pyc",
            "node_modules",
            "dist",
            "build",
            ".next",
            ".env",
            "*.log",
            "data/logs",
            "",
        ]
    )


def python_dockerfile_content(service):
    port = service["port"]
    kind = service["kind"]
    module = service.get("module") or "app.py"

    if kind == "fastapi":
        command = f"python -m uvicorn {module}:app --host 0.0.0.0 --port {port}"
    elif kind == "flask":
        command = f"python -m flask --app {module} run --host 0.0.0.0 --port {port}"
    elif kind == "streamlit":
        command = f"python -m streamlit run {module}.py --server.address 0.0.0.0 --server.port {port}"
    else:
        command = f"python {module}"

    return "\n".join(
        [
            "FROM python:3.12-slim",
            "",
            "WORKDIR /app",
            "",
            "ENV PYTHONDONTWRITEBYTECODE=1",
            "ENV PYTHONUNBUFFERED=1",
            "",
            "COPY . .",
            "RUN if [ -f requirements.txt ]; then pip install --no-cache-dir -r requirements.txt; elif [ -f pyproject.toml ]; then pip install --no-cache-dir .; fi",
            "",
            f"EXPOSE {port}",
            f'CMD ["sh", "-c", "{command}"]',
            "",
        ]
    )


def node_dockerfile_content(service):
    port = service["port"]
    kind = service["kind"]
    script = service.get("script", "start")

    if kind == "vite":
        command = "npm run dev -- --host 0.0.0.0"
    elif script == "start":
        command = "npm start"
    else:
        command = f"npm run {script}"

    return "\n".join(
        [
            "FROM node:20-alpine",
            "",
            "WORKDIR /app",
            "",
            "COPY package*.json ./",
            "RUN if [ -f package-lock.json ]; then npm ci; else npm install; fi",
            "",
            "COPY . .",
            "",
            f"EXPOSE {port}",
            f'CMD ["sh", "-c", "{command}"]',
            "",
        ]
    )


def dotnet_dockerfile_content(service):
    port = service["port"]
    return "\n".join(
        [
            "FROM mcr.microsoft.com/dotnet/sdk:8.0",
            "",
            "WORKDIR /app",
            "COPY . .",
            "RUN dotnet restore",
            "",
            f"EXPOSE {port}",
            f'CMD ["sh", "-c", "dotnet run --urls http://0.0.0.0:{port}"]',
            "",
        ]
    )


def dockerfile_content_for(service):
    if service["type"] == "node":
        return node_dockerfile_content(service)
    if service["type"] == "python":
        return python_dockerfile_content(service)
    if service["type"] == "dotnet":
        return dotnet_dockerfile_content(service)
    raise ValueError(f"Unsupported service type: {service['type']}")


def compose_content(root, services):
    lines = ["services:"]
    used_ports = set()
    for service in services:
        rel = service["path"].relative_to(root).as_posix() if service["path"] != root else "."
        host_port = int(service["port"])
        while host_port in used_ports:
            host_port += 1
        used_ports.add(host_port)
        service["host_port"] = host_port

        lines.extend(
            [
                f"  {service['name']}:",
                "    build:",
                f"      context: ./{rel}" if rel != "." else "      context: .",
                "    ports:",
                f'      - "{host_port}:{service["port"]}"',
                "    restart: unless-stopped",
            ]
        )
    lines.append("")
    return "\n".join(lines)


def dockerize_project(args, state):
    root = resolve_path(args.get("path", "."), state["cwd"])
    overwrite = bool(args.get("overwrite", False))
    build = bool(args.get("build", False))
    validate = bool(args.get("validate", True))
    max_files = int(args.get("max_files", 500))
    progress = state.get("_progress") or (lambda _message: None)

    progress("resolving project path")
    if not root.exists():
        loose_match = find_loose_directory_match(args.get("path", "."), state["cwd"])
        if loose_match:
            root = loose_match
    if not root.exists() or not root.is_dir():
        raise FileNotFoundError(f"{root} does not exist or is not a directory.")

    progress("scanning project files")
    scanned_files = []
    for path in sorted(root.rglob("*"), key=lambda item: str(item).lower()):
        if should_skip_path(path):
            continue
        if path.is_file():
            scanned_files.append(str(path.relative_to(root)))
        if len(scanned_files) >= max_files:
            break

    progress("detecting application services")
    services = discover_docker_services(root)
    if not services:
        raise ValueError("No supported application services were detected. Supported projects include Node, Python, and .NET.")

    progress("generating Docker files")
    files = []
    for service in services:
        dockerfile = service["path"] / "Dockerfile"
        if overwrite or not dockerfile.exists():
            files.append({"path": dockerfile, "content": dockerfile_content_for(service)})

        ignore_file = service["path"] / ".dockerignore"
        if overwrite or not ignore_file.exists():
            files.append({"path": ignore_file, "content": dockerignore_content()})

    compose_file = root / "docker-compose.yml"
    if overwrite or not compose_file.exists():
        files.append({"path": compose_file, "content": compose_content(root, services)})

    existing = [item["path"] for item in files if item["path"].exists() and not overwrite]
    if existing:
        names = ", ".join(str(path.relative_to(root)) for path in existing)
        raise FileExistsError(f"Refusing to overwrite existing Docker files: {names}")

    progress("writing Docker files")
    for item in files:
        item["path"].parent.mkdir(parents=True, exist_ok=True)
        item["path"].write_text(item["content"], encoding="utf-8")

    validation = None
    if validate and shutil.which("docker"):
        progress("validating docker compose config")
        command = [shutil.which("docker"), "compose", "config"]
        result = subprocess.run(command, cwd=root, text=True, capture_output=True, timeout=60)
        validation = {
            "command": "docker compose config",
            "exit_code": result.returncode,
            "stdout": truncate(result.stdout),
            "stderr": truncate(result.stderr),
        }

    build_result = None
    if build and shutil.which("docker"):
        progress("building Docker images")
        command = [shutil.which("docker"), "compose", "build"]
        result = subprocess.run(command, cwd=root, text=True, capture_output=True, timeout=int(args.get("build_timeout_seconds", 900)))
        build_result = {
            "command": "docker compose build",
            "exit_code": result.returncode,
            "stdout": truncate(result.stdout),
            "stderr": truncate(result.stderr),
        }

    progress("Docker project files ready")
    return {
        "project": root.name,
        "path": str(root),
        "scanned_file_count": len(scanned_files),
        "scanned_files_truncated": len(scanned_files) >= max_files,
        "services": [
            {
                "name": service["name"],
                "path": str(service["path"].relative_to(root)),
                "type": service["type"],
                "kind": service["kind"],
                "container_port": service["port"],
                "host_port": service.get("host_port", service["port"]),
            }
            for service in services
        ],
        "files_written": [str(item["path"].relative_to(root)) for item in files],
        "validation": validation,
        "build": build_result,
        "next_commands": ["docker compose up -d --build", "docker compose logs -f"],
    }


def tailscale_ip():
    command = shutil.which("tailscale")
    if not command:
        return ""

    try:
        result = subprocess.run(
            [command, "ip", "-4"],
            text=True,
            capture_output=True,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""

    return result.stdout.strip().splitlines()[0] if result.returncode == 0 and result.stdout.strip() else ""


def project_start_command(root):
    docker = shutil.which("docker")

    if not docker:
        raise FileNotFoundError("Docker was not found on PATH. DevServer only starts projects through Docker.")

    compose_files = ["docker-compose.yml", "docker-compose.yaml", "compose.yml", "compose.yaml"]
    for compose_file in compose_files:
        if (root / compose_file).exists():
            return compose_file, [docker, "compose", "up"]

    dockerfiles = sorted(root.glob("Dockerfile*"))
    if dockerfiles:
        image_name = f"devserver-{normalize_path_name(root.name)}"
        return dockerfiles[0].name, [
            docker,
            "build",
            "-t",
            image_name,
            "-f",
            str(dockerfiles[0]),
            str(root),
        ]

    raise FileNotFoundError("No Docker start method found. Add docker-compose.yml, compose.yml, or a Dockerfile before starting this project.")


def start_project(args, state):
    raw_path = args.get("path") or args.get("project") or "."
    root = resolve_path(raw_path, state["cwd"])

    if not root.exists():
        loose_match = find_loose_directory_match(raw_path, state["cwd"])
        if loose_match:
            root = loose_match

    if not root.exists():
        raise FileNotFoundError(f"{root} does not exist.")

    if not root.is_dir():
        raise ValueError(f"{root} is not a directory.")

    script_name, command = project_start_command(root)
    log_dir = root / "data" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    stdout_path = log_dir / "devserver-start.out.log"
    stderr_path = log_dir / "devserver-start.err.log"

    stdout_file = stdout_path.open("a", encoding="utf-8")
    stderr_file = stderr_path.open("a", encoding="utf-8")
    process = subprocess.Popen(
        command,
        cwd=root,
        stdout=stdout_file,
        stderr=stderr_file,
        text=True,
    )
    stdout_file.close()
    stderr_file.close()

    time.sleep(float(args.get("settle_seconds", 2)))

    ports = detect_project_ports(root)
    ts_ip = tailscale_ip()
    urls = []
    for port in ports:
        urls.append(f"http://127.0.0.1:{port}")
        if ts_ip:
            urls.append(f"http://{ts_ip}:{port}")

    return {
        "project": root.name,
        "path": str(root),
        "script": script_name,
        "pid": process.pid,
        "urls": urls,
        "ports": ports,
        "stdout_log": str(stdout_path),
        "stderr_log": str(stderr_path),
    }


def shell_command_prefix():
    if os.name == "nt":
        shell = shutil.which("powershell") or shutil.which("pwsh")
        if shell:
            return [shell, "-NoProfile", "-Command"]

    pwsh = shutil.which("pwsh")
    if pwsh:
        return [pwsh, "-NoProfile", "-Command"]

    return ["/bin/sh", "-lc"]


def is_plain_ssh_session():
    is_ssh = bool(os.environ.get("SSH_CONNECTION") or os.environ.get("SSH_CLIENT"))
    is_vscode_remote = bool(os.environ.get("VSCODE_IPC_HOOK_CLI") or os.environ.get("VSCODE_AGENT_FOLDER"))
    return is_ssh and not is_vscode_remote


def open_in_vscode(args, state):
    if os.environ.get("DEVSERVER_WEB_TERMINAL") == "1":
        return {
            "path": str(resolve_path(args.get("path", "."), state["cwd"])),
            "command": "open_in_vscode",
            "exit_code": 2,
            "stdout": "",
            "stderr": (
                "Projects cannot be opened in VS Code from the web terminal. "
                "From the web terminal, projects can only be hosted or started."
            ),
            "visible": False,
        }

    raw_path = args.get("path", ".")
    path = resolve_path(raw_path, state["cwd"])

    if not path.exists():
        loose_match = find_loose_directory_match(raw_path, state["cwd"])
        if loose_match:
            path = loose_match

    if not path.exists():
        raise FileNotFoundError(f"{path} does not exist.")

    if not path.is_dir():
        raise ValueError(f"{path} is not a directory.")

    if is_plain_ssh_session():
        return {
            "path": str(path),
            "command": "code",
            "exit_code": 2,
            "stdout": "",
            "stderr": (
                "This is a plain SSH session. Running VS Code on the server cannot open a "
                "window on your laptop. Open this host from VS Code using Remote-SSH, then "
                "use `code .` in the VS Code remote terminal."
            ),
            "visible": False,
        }

    code_command = shutil.which("code") or shutil.which("code.cmd")
    if not code_command:
        raise FileNotFoundError("VS Code CLI command 'code' was not found on PATH.")

    result = subprocess.run(
        [code_command, str(path)],
        cwd=state["cwd"],
        text=True,
        capture_output=True,
        timeout=15,
    )

    return {
        "path": str(path),
        "command": code_command,
        "exit_code": result.returncode,
        "stdout": truncate(result.stdout),
        "stderr": truncate(result.stderr),
        "visible": True,
    }


def open_project(args, state):
    query = args.get("query") or args.get("name") or args.get("project") or ""
    project_path, matches = resolve_project_query(query, state)
    result = open_in_vscode({"path": str(project_path)}, state)

    return {
        **result,
        "query": query,
        "selected_project": project_path.name,
        "selected_path": str(project_path),
        "matches": matches,
    }


def powershell_string(value):
    return "'" + str(value).replace("'", "''") + "'"


def windows_launcher_scripts(args, state):
    root = resolve_path(args.get("path", "."), state["cwd"])
    app_entry = args.get("entry") or "app.py"
    port = int(args.get("port", 5555))
    overwrite = bool(args.get("overwrite", False))

    if not root.exists():
        raise FileNotFoundError(f"{root} does not exist.")

    if not root.is_dir():
        raise ValueError(f"{root} is not a directory.")

    start_shell = root / "start.sh"
    end_shell = root / "end.sh"
    existing_scripts = {}
    for script in [start_shell, end_shell]:
        if script.exists() and script.is_file():
            existing_scripts[script.name] = script.read_text(encoding="utf-8", errors="replace")

    has_requirements = (root / "requirements.txt").exists()
    entry_path = root / app_entry
    if not entry_path.exists():
        py_files = sorted(path.name for path in root.glob("*.py"))
        app_entry = "app.py" if "app.py" in py_files else (py_files[0] if py_files else app_entry)

    requirements_block = ""
    if has_requirements:
        requirements_block = """
if (-not (Test-Path $Marker)) {
    & $PythonExe -m pip install --upgrade pip
    & $PythonExe -m pip install -r (Join-Path $Root 'requirements.txt')
    Set-Content -LiteralPath $Marker -Value (Get-Date).ToString('s')
}
"""

    start_content = f"""param(
    [switch]$Browser,
    [switch]$Window,
    [int]$Port = {port}
)

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root

$LogDir = Join-Path $Root 'logs'
$PidFile = Join-Path $Root '.devserver-start.pid'
$Marker = Join-Path $Root '.setup-complete'
$Venv = Join-Path $Root '.venv'
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

function Get-PythonCommand {{
    foreach ($Command in @('py', 'python', 'python3')) {{
        $Found = Get-Command $Command -ErrorAction SilentlyContinue
        if ($Found) {{ return $Found.Source }}
    }}
    throw 'Python was not found. Install Python or add it to PATH.'
}}

$SystemPython = Get-PythonCommand
if (-not (Test-Path $Venv)) {{
    & $SystemPython -m venv $Venv
}}

$PythonExe = Join-Path $Venv 'Scripts\\python.exe'
if (-not (Test-Path $PythonExe)) {{
    throw "Virtual environment Python was not found at $PythonExe"
}}
{requirements_block}
$ArgsList = @({powershell_string(app_entry)})
if ($Browser) {{ $ArgsList += '--browser' }}
if ($Window) {{ $ArgsList += '--window' }}
if ($Port) {{ $ArgsList += @('--port', [string]$Port) }}

$Process = Start-Process -FilePath $PythonExe `
    -ArgumentList $ArgsList `
    -WorkingDirectory $Root `
    -RedirectStandardOutput (Join-Path $LogDir 'app.out.log') `
    -RedirectStandardError (Join-Path $LogDir 'app.err.log') `
    -PassThru

Set-Content -LiteralPath $PidFile -Value $Process.Id
Write-Host "Started project with PID $($Process.Id)"
Write-Host "Logs: $LogDir"
"""

    end_content = """$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root

$PidFile = Join-Path $Root '.devserver-start.pid'
if (-not (Test-Path $PidFile)) {
    Write-Host 'No .devserver-start.pid file found. Nothing to stop.'
    exit 0
}

$PidValue = (Get-Content -LiteralPath $PidFile -ErrorAction Stop | Select-Object -First 1).Trim()
if (-not $PidValue) {
    Remove-Item -LiteralPath $PidFile -Force
    Write-Host 'PID file was empty. Removed it.'
    exit 0
}

$Process = Get-Process -Id ([int]$PidValue) -ErrorAction SilentlyContinue
if ($Process) {
    Stop-Process -Id $Process.Id -Force
    Write-Host "Stopped process $PidValue"
} else {
    Write-Host "Process $PidValue was not running"
}

Remove-Item -LiteralPath $PidFile -Force
"""

    result = write_many_files(
        {
            "files": [
                {"path": str(root / "start.ps1"), "content": start_content, "overwrite": overwrite},
                {"path": str(root / "end.ps1"), "content": end_content, "overwrite": overwrite},
            ],
            "create_parent_dirs": True,
        },
        state,
    )

    return {
        **result,
        "source_scripts_found": sorted(existing_scripts),
        "entry": app_entry,
        "port": port,
    }


def run_git(args, state, git_args):
    max_chars = int(args.get("max_chars", MAX_TOOL_OUTPUT_CHARS))
    result = subprocess.run(
        safe_git_command(state, git_args),
        cwd=state["cwd"],
        text=True,
        capture_output=True,
        timeout=30,
    )

    return {
        "command": "git " + " ".join(str(part) for part in git_args),
        "cwd": str(state["cwd"]),
        "exit_code": result.returncode,
        "stdout": truncate(result.stdout, max_chars),
        "stderr": truncate(result.stderr, max_chars),
    }


def git_status(args, state):
    return run_git(args, state, ["status", "--short"])


def git_diff(args, state):
    git_args = ["diff"]

    if args.get("staged", False):
        git_args.append("--staged")

    path = args.get("path")
    if path:
        git_args.extend(["--", path])

    return run_git(args, state, git_args)


def git_log(args, state):
    max_count = str(int(args.get("max_count", 10)))
    return run_git(args, state, ["log", f"--max-count={max_count}", "--oneline"])


def git_show(args, state):
    ref = args.get("ref", "HEAD")
    return run_git(args, state, ["show", "--stat", "--patch", ref])


def git_add(args, state):
    paths = args.get("paths") or []
    if not paths:
        raise ValueError("paths is required.")
    return run_git(args, state, ["add", *paths])


def git_commit(args, state):
    message = args["message"]
    return run_git(args, state, ["commit", "-m", message])


def list_processes(args, state):
    filter_text = (args.get("filter") or "").lower()
    max_results = int(args.get("max_results", 50))

    if os.name == "nt" or shutil.which("powershell") or shutil.which("pwsh"):
        result = subprocess.run(
            [
                *shell_command_prefix(),
                "Get-Process | Select-Object Id,ProcessName,CPU,WorkingSet",
            ],
            text=True,
            capture_output=True,
            timeout=15,
        )
    else:
        result = subprocess.run(
            ["ps", "-eo", "pid,comm,%cpu,rss"],
            text=True,
            capture_output=True,
            timeout=15,
        )

    lines = result.stdout.splitlines()
    if filter_text:
        lines = [line for line in lines if filter_text in line.lower()]

    return {"processes": lines[:max_results], "truncated": len(lines) > max_results}


def system_info(args, state):
    return {
        "platform": platform.platform(),
        "python": platform.python_version(),
        "cwd": str(state["cwd"]),
        "workspace": str(WORKSPACE_DIR),
    }


def env_get(args, state):
    names = args.get("names") or []
    values = {}

    for name in names:
        lowered = name.lower()
        if any(part in lowered for part in ["key", "token", "secret", "password", "cookie", "credential", "private"]):
            values[name] = "[blocked]"
        else:
            values[name] = os.environ.get(name)

    return {"values": values}


def session_note(args, state):
    state.setdefault("notes", {})[args["key"]] = args.get("value", "")
    return {"key": args["key"], "stored": True}


def session_recall(args, state):
    return {"key": args["key"], "value": state.setdefault("notes", {}).get(args["key"])}


def tool_manifest(registry):
    lines = []
    for tool in registry.values():
        lines.append(f"- {tool.name}: {tool.description}")
    return "\n".join(lines)


def build_registry():
    tools = [
        Tool("get_cwd", "Return the current working directory. Args: {}", get_cwd),
        Tool("set_cwd", "Change working directory. Args: {path}", set_cwd),
        Tool("list_dir", "List files and folders. Args: {path, include_hidden}", list_dir),
        Tool("list_projects", "List project directories in the Projects folder. Args: {include_hidden}", list_projects),
        Tool("open_project", "Resolve a project request against the Projects folder and open it in VS Code. Args: {query}", open_project),
        Tool("start_project", "Start or host a project using its start script. Args: {path, settle_seconds}", start_project),
        Tool("project_snapshot", "Inspect a project in one call: directory listing plus key config/script files. Args: {path, max_chars}", project_snapshot),
        Tool("project_status", "Summarize current project readiness: project types, key files, ports, start methods, Git status, and runnable tasks. Args: {path}", project_status),
        Tool("tree", "Return a bounded recursive file tree. Args: {path, max_depth, max_entries}", tree),
        Tool("search_files", "Search filenames and optionally contents. Args: {path, query, include_contents, max_results}", search_files),
        Tool("read_file", "Read a text file with limits. Args: {path, max_chars}", read_file),
        Tool("read_many_files", "Read several text files in one call and report per-file errors. Args: {paths, max_chars}", read_many_files),
        Tool("read_file_metadata", "Return file metadata. Args: {path}", read_file_metadata),
        Tool("create_file", "Create a new file. Refuses to overwrite unless overwrite=true. Args: {path, content, overwrite, create_parent_dirs}", create_file),
        Tool("edit_file", "Replace a whole existing file. Use after read_file; optional expected_content guards against stale edits. Args: {path, content, expected_content}", edit_file),
        Tool("replace_in_file", "Replace exact text inside an existing file. Use for focused edits. Args: {path, old_text, new_text, count}", replace_in_file),
        Tool("write_many_files", "Create or replace multiple files in one approved action. Refuses existing files unless overwrite=true. Args: {files, overwrite, create_parent_dirs}", write_many_files),
        Tool("windows_launcher_scripts", "Create Windows PowerShell start.ps1 and end.ps1 launchers for the current project from project conventions. Args: {path, entry, port, overwrite}", windows_launcher_scripts),
        Tool("dockerize_project", "High-level workflow: scan a whole project, detect services, generate Dockerfiles/.dockerignore/docker-compose.yml, validate with docker compose config, and optionally build. Args: {path, overwrite, validate, build, max_files, build_timeout_seconds}", dockerize_project),
        Tool("write_file", "Create or replace a file. Prefer create_file or edit_file for user-facing work. Args: {path, content, create_parent_dirs}", write_file),
        Tool("append_file", "Append to a file. Args: {path, content}", append_file),
        Tool("apply_patch", "Apply a git-compatible unified diff. Args: {patch}", apply_patch_tool),
        Tool("copy_path", "Copy a file or directory. Args: {source, destination, overwrite}", copy_path),
        Tool("move_path", "Move or rename a file or directory. Args: {source, destination, overwrite}", move_path),
        Tool("delete_path", "Delete a file or empty directory. Args: {path, recursive}", delete_path),
        Tool("run_shell", "Run a shell command in the current directory. Uses PowerShell on Windows and sh/pwsh elsewhere. Args: {command, timeout_seconds}", run_shell),
        Tool("run_task", "Run a typed built-in task such as git_status, git_diff, python_unittest, python_compile, docker_compose_config, or npm_test. Args: {task, timeout_seconds, task-specific args}", run_task),
        Tool("open_in_vscode", "Open an exact folder path in VS Code through the available code CLI. For project names, use open_project instead. Args: {path}", open_in_vscode),
        Tool("git_status", "Return git status --short. Args: {}", git_status),
        Tool("git_diff", "Return bounded git diff. Args: {path, staged, max_chars}", git_diff),
        Tool("git_log", "Return recent commits. Args: {max_count}", git_log),
        Tool("git_show", "Show a git commit/object. Args: {ref, max_chars}", git_show),
        Tool("git_add", "Stage files. Args: {paths}", git_add),
        Tool("git_commit", "Create a commit. Args: {message}", git_commit),
        Tool("list_processes", "List running processes. Args: {filter, max_results}", list_processes),
        Tool("system_info", "Return basic system information. Args: {}", system_info),
        Tool("env_get", "Read selected non-secret environment variables. Args: {names}", env_get),
        Tool("session_note", "Store a session-only note. Args: {key, value}", session_note),
        Tool("session_recall", "Read a session-only note. Args: {key}", session_recall),
    ]

    return {tool.name: tool for tool in tools}
