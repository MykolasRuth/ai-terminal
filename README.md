# DevServer AI Terminal

A self-hosted AI coding terminal that runs entirely on local models. It connects [Ollama](https://ollama.com) models (Qwen, Gemma and others) to your shell, Git and project files through a small JSON tool protocol. Use it in a terminal, or in a browser from another device over a private network such as Tailscale.

The rule the design follows: **the model can request actions, but only the terminal executes them.**

```text
user request -> model plans and asks for a tool (JSON) -> terminal checks the safety policy
             -> terminal runs the tool -> result goes back to the model -> model continues or answers
```

## What's in it

- **Agent loop** (`ai_terminal/agent.py`): parses the model's tool requests, runs the approved ones, feeds the results back, and prints the final answer.
- **Tools** (`ai_terminal/tools.py`): filesystem (list, tree, search, read, write, copy, move, delete), terminal (run shell commands and tasks), Git (status, diff, log, show, add, commit), and system and session notes.
- **Safety policy** (`ai_terminal/policy.py`): rates each tool call's risk, blocks secret-looking paths, flags access outside the workspace, and asks before risky actions.
- **Tool protocol** (`ai_terminal/tool_protocol.py`): strict JSON tool calls in, formatted results back into the chat history.
- **Model routing** (`ai_terminal/model_router.py`): picks the active model and handles hand-offs from the orchestrator model.
- **Browser mode** (`ai_terminal/web_server.py`, `web_auth.py`): FastAPI and WebSocket UI behind a login, with passwords stored as salted PBKDF2 hashes.
- **Deployment**: Docker Compose, plus Windows scripts that start the browser server at login.

More detail: [ARCHITECTURE.md](ARCHITECTURE.md) (modules and dependency direction), [tools.md](tools.md) (every tool), [START.md](START.md) (setup and running).

## Quick start

Copy `.env.example` to `.env` and adjust it (`.env` is gitignored).

```bash
pip install -r requirements.txt   # Python 3, with Ollama running and a model pulled
python ollama_cli.py              # terminal mode
python -m ai_terminal.web_auth    # set a browser login, then:
python ollama_cli.py web          # browser mode on http://127.0.0.1:8787
```

Run the tests with `python -m unittest`.

## Safety

It runs shell commands and edits files on the machine it runs on. Browser mode listens on localhost by default; if you bind it to a network, keep it behind Tailscale or another private network, never the public internet.
