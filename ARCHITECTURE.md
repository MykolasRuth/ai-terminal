## DevServer AI Terminal Architecture

The app is split into small modules so the CLI, model calls, tool execution, and safety policy stay separate.

```text
ollama_cli.py
    -> ai_terminal.cli.main()

ai_terminal/
    |-- cli.py
    |     |-- starts the terminal
    |     |-- prints the startup banner
    |     |-- handles the input loop
    |     `-- routes normal messages into the agent loop
    |
    |-- agent.py
    |     |-- run_agent_turn()
    |     |-- parses model tool requests
    |     |-- executes approved tools
    |     |-- feeds tool results back to the model
    |     `-- prints final assistant responses
    |
    |-- tools.py
    |     |-- build_registry()
    |     |-- filesystem tools
    |     |     |-- get_cwd()
    |     |     |-- set_cwd()
    |     |     |-- list_dir()
    |     |     |-- tree()
    |     |     |-- search_files()
    |     |     |-- read_file()
    |     |     |-- project_status()
    |     |     |-- write_file()
    |     |     |-- append_file()
    |     |     |-- copy_path()
    |     |     |-- move_path()
    |     |     `-- delete_path()
    |     |-- terminal tools
    |     |     |-- run_shell()
    |     |     `-- run_task()
    |     |-- git tools
    |     |     |-- git_status()
    |     |     |-- git_diff()
    |     |     |-- git_log()
    |     |     |-- git_show()
    |     |     |-- git_add()
    |     |     `-- git_commit()
    |     `-- system/session tools
    |           |-- list_processes()
    |           |-- system_info()
    |           |-- env_get()
    |           |-- session_note()
    |           `-- session_recall()
    |
    |-- policy.py
    |     |-- classifies tool risk
    |     |-- blocks secret-like paths
    |     |-- detects outside-workspace access
    |     `-- asks for approval on risky actions
    |
    |-- tool_protocol.py
    |     |-- parses strict JSON tool calls
    |     `-- formats tool results for chat history
    |
    |-- ollama_client.py
    |     |-- talks to the Ollama chat API
    |     |-- streams or fetches model responses
    |     `-- lists installed local models
    |
    |-- model_router.py
    |     |-- chooses the active model
    |     `-- handles orchestrator handoff decisions
    |
    |-- commands.py
    |     |-- handles manual commands like cd, dir, /tools, /models
    |     `-- keeps direct user commands separate from model tool calls
    |
    |-- display.py
    |     |-- friendly model names
    |     |-- colored chat output
    |     |-- file and directory highlighting
    |     `-- assistant message formatting
    |
    |-- prompts.py
    |     |-- orchestrator system prompt
    |     `-- tool-using agent system prompt
    |
    `-- config.py
          |-- Ollama URLs
          |-- workspace defaults
          |-- output limits
          `-- skip/secret path settings
```

## Dependency Direction

Keep dependencies flowing in one direction:

```text
cli.py
    -> commands.py
    -> model_router.py
    -> agent.py

agent.py
    -> ollama_client.py
    -> tool_protocol.py
    -> policy.py
    -> tools.py
    -> display.py

tools.py
    -> config.py
    -> policy.resolve_path()
```

Avoid putting tool execution, model calls, approval policy, and UI rendering in the same file.

## Adding A New Tool

1. Add the handler function in `ai_terminal/tools.py`.
2. Register it in `build_registry()`.
3. Add its risk level in `ai_terminal/policy.py`.
4. Document it in `tools.md`.
5. Keep approval behavior in `policy.py`, not inside the model prompt.

## Architecture Rule

The model can request actions, but only the terminal executes them.

```text
user request
    -> model plan/tool JSON
    -> terminal validates policy
    -> terminal executes tool
    -> terminal returns result
    -> model continues or answers
```
