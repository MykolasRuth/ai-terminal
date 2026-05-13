## AI Terminal Tool Library

This document defines the tools local AI models should be allowed to request from the homelab terminal. The terminal, not the model, is responsible for executing tools, enforcing policy, asking for approval, and logging results.

## User Experience Goal

The user should be able to ask for outcomes in plain English:

```text
start my sports betting project
```

The user should not need to type terminal commands, slash commands, JSON, or tool names. The model turns the request into a plan, requests tools internally, observes the results, and continues until the task is complete or blocked.

The JSON tool format below is an internal protocol between the model and the terminal. It is not the user interface.

## Agent Control Loop

For each normal user message, the terminal should run an agent loop:

1. Send the user's request, conversation history, current working directory, available tools, and tool rules to the selected model.
2. Let the model either answer normally or return one or more JSON tool calls.
3. Validate each tool call against the fixed tool registry.
4. Classify risk and request approval when required.
5. Execute approved tools.
6. Add structured tool results back into conversation history.
7. Ask the model what to do next.
8. Repeat until the model returns a final answer or the loop reaches a configured limit.

Suggested loop limits:

- `max_tool_rounds`: 20
- `max_tool_calls_per_round`: 5
- `max_runtime_minutes`: 30
- `max_output_chars_per_tool`: 20000

The model should maintain momentum without asking the user for commands. It should only ask the user a question when it needs missing information that cannot be discovered safely.

## Tool Call Format

Models must request tools with strict JSON only.

```json
{
  "tool": "read_file",
  "args": {
    "path": "ollama_cli.py"
  }
}
```

Multiple tool calls may be returned as a JSON array.

```json
[
  {
    "tool": "list_dir",
    "args": {
      "path": "."
    }
  },
  {
    "tool": "read_file",
    "args": {
      "path": "ollama_cli.py"
    }
  }
]
```

Natural-language tool requests must not be executed. If the model does not return valid JSON, the terminal treats the response as normal assistant text.

Important: this restriction applies only to model-to-terminal tool calls. User messages are expected to be natural language.

## Assistant Behavior

The model should behave like a local project operator:

- Infer the user's intended project or directory from the request.
- Inspect the workspace before acting.
- Prefer existing scripts, README instructions, package metadata, and project conventions.
- Start services using the project's normal dev command when discoverable.
- Report URLs, process IDs, log locations, and next steps after starting something.
- Continue through routine setup steps without requiring the user to name tools.
- Ask before risky actions, such as installs, external network calls, writes outside the workspace, destructive changes, or starting long-running services.

Example:

User:

```text
start my sports betting project
```

Expected internal flow:

1. Search likely project directories for names such as `sports`, `betting`, or related README/package files.
2. Inspect candidate project metadata.
3. Determine the start command.
4. Ask before installing dependencies if needed.
5. Start the dev server.
6. Read initial logs.
7. Tell the user the project is running and provide the local URL.

## Risk Levels

### Low

Safe read-only actions inside the current workspace. These may run automatically.

### Medium

Read-only access outside the workspace, file writes inside the workspace, environment inspection, and bounded process inspection. These require approval.

### High

Shell commands, network access, package installation, file writes outside the workspace, process termination, service control, and anything that could modify system state. These require approval every time.

### Blocked By Default

Credential theft, secret scraping, destructive recursive deletion, disk formatting, registry-wide edits, browser credential stores, SSH private keys, password vaults, and attempts to bypass approval policy.

## Approval Policy

- Auto-run read-only tools, info tools, navigation, and non-mutating shell commands.
- Ask before tools that write, edit, move, copy, delete, stage, or commit files.
- Ask before shell commands that look like they can change files or project state.
- Block known secret paths and secret-like environment variables by default.
- Show the exact resolved path, command, risk level, and reason before approval.
- Log every tool call with timestamp, model name, tool name, arguments, approval result, exit code, and summarized output.

## Filesystem Tools

### `get_cwd`

Returns the terminal's current working directory.

Risk: Low

Args:

```json
{}
```

### `set_cwd`

Changes the terminal's working directory.

Risk: Medium if inside workspace, high if outside workspace

Args:

```json
{
  "path": "C:/Users/devserver/Projects/ai-terminal"
}
```

### `list_dir`

Lists files and folders at a path.

Risk: Low inside workspace, medium outside workspace

Args:

```json
{
  "path": ".",
  "include_hidden": false
}
```

### `tree`

Returns a bounded recursive file tree.

Risk: Low inside workspace, medium outside workspace

Args:

```json
{
  "path": ".",
  "max_depth": 4,
  "max_entries": 500
}
```

### `search_files`

Searches filenames and optionally file contents.

Risk: Low inside workspace, medium outside workspace

Args:

```json
{
  "path": ".",
  "query": "def main",
  "include_contents": true,
  "max_results": 50
}
```

### `read_file`

Reads a text file with size limits.

Risk: Low inside workspace, medium outside workspace, blocked for known secret paths by default

Args:

```json
{
  "path": "ollama_cli.py",
  "max_chars": 12000
}
```

### `read_file_metadata`

Returns file size, timestamps, extension, and permissions without reading contents.

Risk: Low inside workspace, medium outside workspace

Args:

```json
{
  "path": "ollama_cli.py"
}
```

### `project_snapshot`

Inspect a project in one call: directory listing plus key config/script files.

Risk: Low inside workspace, medium outside workspace

Args:

```json
{
  "path": ".",
  "max_chars": 3000
}
```

### `project_status`

Summarizes a project in one call: project type signals, key files, detected
ports, start methods, package scripts, Docker availability, Git status, and
built-in runnable validation tasks.

Risk: Low inside workspace, medium outside workspace

Args:

```json
{
  "path": "."
}
```

### `write_file`

Creates or replaces a file.

Risk: Medium inside workspace, high outside workspace

Args:

```json
{
  "path": "notes/example.txt",
  "content": "text to write",
  "create_parent_dirs": false
}
```

### `append_file`

Appends text to a file.

Risk: Medium inside workspace, high outside workspace

Args:

```json
{
  "path": "notes/example.txt",
  "content": "\nmore text"
}
```

### `apply_patch`

Applies a git-compatible unified diff to one or more workspace files.

Risk: Medium inside workspace, high outside workspace

Args:

```json
{
  "patch": "diff --git a/example.txt b/example.txt\n--- a/example.txt\n+++ b/example.txt\n@@ -1 +1 @@\n-old\n+new\n"
}
```

### `copy_path`

Copies a file or directory.

Risk: Medium inside workspace, high outside workspace

Args:

```json
{
  "source": "input.txt",
  "destination": "backup/input.txt",
  "overwrite": false
}
```

### `move_path`

Moves or renames a file or directory.

Risk: Medium inside workspace, high outside workspace

Args:

```json
{
  "source": "old.txt",
  "destination": "new.txt",
  "overwrite": false
}
```

### `delete_path`

Deletes a file or empty directory.

Risk: High

Args:

```json
{
  "path": "old.txt",
  "recursive": false
}
```

Recursive deletion should be blocked by default unless the user explicitly enables destructive tools for the session.

## Terminal Tools

### `run_shell`

Runs a shell command in the current working directory.

Risk: High

Args:

```json
{
  "command": "git status --short",
  "timeout_seconds": 30
}
```

Rules:

- Require approval every time.
- Show the exact command before execution.
- Use a timeout.
- Capture stdout, stderr, and exit code.
- Never auto-approve commands with redirects, pipes, shell substitutions, package installation, network calls, service changes, or destructive operations.

### `run_task`

Runs a named task from a typed allowlist. Each task accepts only its documented
arguments; unknown arguments are rejected.

Risk: Medium or high depending on task

Args:

```json
{
  "task": "test",
  "args": []
}
```

Suggested initial allowlist:

- `git_status`: `git status --short`
- `git_diff`: `git diff`, with optional `path`, `staged`, and `max_chars`
- `python_unittest`: `python -m unittest discover`, with optional `start_dir` and `pattern`
- `python_compile`: `python -m compileall`, with optional `path`
- `docker_compose_config`: `docker compose config`
- `npm_test`: `npm test`, only when `package.json` defines a `test` script

Use `run_shell` only when no typed task fits.

## Git Tools

### `git_status`

Returns working tree status.

Risk: Low

Args:

```json
{}
```

### `git_diff`

Returns a bounded diff.

Risk: Low

Args:

```json
{
  "path": ".",
  "staged": false,
  "max_chars": 20000
}
```

### `git_log`

Returns recent commits.

Risk: Low

Args:

```json
{
  "max_count": 10
}
```

### `git_show`

Shows a specific commit or object.

Risk: Low

Args:

```json
{
  "ref": "HEAD",
  "max_chars": 20000
}
```

### `git_add`

Stages files.

Risk: Medium

Args:

```json
{
  "paths": ["ollama_cli.py"]
}
```

### `git_commit`

Creates a commit.

Risk: High

Args:

```json
{
  "message": "Add model tool access"
}
```

No push tool should be enabled by default.

## Process And System Tools

### `list_processes`

Lists running processes with bounded output.

Risk: Medium

Args:

```json
{
  "filter": "python",
  "max_results": 50
}
```

### `kill_process`

Terminates a process by PID.

Risk: High

Args:

```json
{
  "pid": 1234
}
```

### `system_info`

Returns OS, CPU, memory, disk summary, and Python version.

Risk: Medium

Args:

```json
{}
```

### `env_get`

Returns selected environment variables by name.

Risk: Medium, blocked for secret-like names by default

Args:

```json
{
  "names": ["PATH"]
}
```

Block names containing `KEY`, `TOKEN`, `SECRET`, `PASSWORD`, `COOKIE`, `CREDENTIAL`, or `PRIVATE`.

## Network Tools

### `http_request`

Makes a bounded HTTP request.

Risk: High

Args:

```json
{
  "method": "GET",
  "url": "https://example.com",
  "headers": {},
  "body": null,
  "timeout_seconds": 20,
  "max_response_chars": 20000
}
```

Rules:

- Require approval every time.
- Block local metadata addresses by default.
- Redact authorization headers from logs.
- Do not send local file contents unless the user explicitly approves the exact payload.

## Memory Tools

### `session_note`

Stores a note in the current terminal session only.

Risk: Low

Args:

```json
{
  "key": "project_goal",
  "value": "Add local tool access to the AI terminal"
}
```

### `session_recall`

Reads a note from the current terminal session.

Risk: Low

Args:

```json
{
  "key": "project_goal"
}
```

Persistent memory should require explicit user approval.

## Recommended Initial Tool Set

Start with these tools:

- `get_cwd`
- `list_dir`
- `tree`
- `search_files`
- `read_file`
- `read_file_metadata`
- `project_status`
- `apply_patch`
- `run_task`
- `run_shell`
- `git_status`
- `git_diff`

Delay these until the approval and logging system is solid:

- `write_file`
- `append_file`
- `delete_path`
- `move_path`
- `copy_path`
- `http_request`
- `kill_process`
- `git_commit`

## Tool Result Format

Every tool result should be added back into chat history as structured JSON.

```json
{
  "role": "tool",
  "tool": "read_file",
  "ok": true,
  "result": {
    "path": "C:/Users/devserver/Projects/ai-terminal/ollama_cli.py",
    "truncated": false,
    "content": "..."
  }
}
```

Errors should also be structured.

```json
{
  "role": "tool",
  "tool": "read_file",
  "ok": false,
  "error": {
    "type": "FileNotFoundError",
    "message": "missing.txt does not exist"
  }
}
```

## Implementation Notes

- Validate tool names against a fixed registry.
- Validate args against a schema before execution.
- Resolve paths before showing approval prompts.
- Track whether each path is inside or outside the workspace.
- Use timeouts for all shell and network operations.
- Limit output size for every tool.
- Store full logs locally, but only return bounded summaries to the model.
- Do not expose secret file contents or secret environment variables by default.
