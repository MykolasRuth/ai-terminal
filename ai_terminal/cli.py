import re
import sys

from . import __version__
from .agent import execute_tool_call, run_agent_turn
from .commands import handle_command
from .config import SESSION_START_DIR
from .display import (
    Style,
    WorkingStatus,
    dim,
    format_footer,
    model_label,
    print_assistant_message,
    success,
)
from .input_prompt import create_prompt_session, read_user_prompt
from .model_router import choose_model, get_default_orchestrator, list_models
from .ollama_client import get_models
from .tools import build_registry


DEVSERVER_WORDMARK = [
    " ____             ____",
    "|  _ \\  _____   _/ ___|  ___ _ ____   _____ _ __",
    "| | | |/ _ \\ \\ / /\\___ \\ / _ \\ '__\\ \\ / / _ \\ '__|",
    "| |_| |  __/\\ V /  ___) |  __/ |   \\ V /  __/ |",
    "|____/ \\___| \\_/  |____/ \\___|_|    \\_/ \\___|_|",
]


SMALL_TALK_RESPONSES = {
    "hi": "Hi. What would you like to work on?",
    "hello": "Hi. What would you like to work on?",
    "hey": "Hey. What would you like to work on?",
    "yo": "Hey. What would you like to work on?",
    "thanks": "You're welcome.",
    "thank you": "You're welcome.",
}


def simple_chat_response(prompt):
    normalized = prompt.lower().strip(" \t\r\n.!?:")
    return SMALL_TALK_RESPONSES.get(normalized)


def sanitize_user_prompt(prompt):
    prompt = re.sub(r"\x1b\[[0-9;?]*[ -/]*[@-~]", "", prompt)
    prompt = prompt.replace("^[[32m", "").replace("^[[0m", "")
    cleaned_lines = []

    for raw_line in prompt.splitlines():
        line = raw_line.strip()

        if not line:
            continue

        if re.fullmatch(r"[A-Za-z0-9_.-]+\s+default\s+[-\u00b7]\s+.+", line):
            continue

        if re.fullmatch(r"[A-Za-z0-9_.-]+\s*:", line):
            continue

        if line.startswith(("\u203a", ">")):
            line = line[1:].strip()
            if not line:
                continue

        cleaned_lines.append(line)

    return "\n".join(cleaned_lines).strip()


def select_turn_model(decision, active_model):
    if decision.get("action") == "handoff":
        return decision["model"], True

    return active_model, False


def print_startup_banner(orchestrator_model, cwd):
    width = 82
    line = "-" * width
    commands = "/tools  /models  /model  /orchestrator  /account  /doctor  /clear  /history  /tool-log  /bye"

    print()
    print(f"{Style.CYAN}{line}{Style.RESET}")
    for row in DEVSERVER_WORDMARK:
        print(f"{Style.BOLD}{Style.CYAN}{row}{Style.RESET}")
    print(f"{Style.CYAN}{line}{Style.RESET}")
    print()
    print(f"{Style.BOLD}  AI Terminal{Style.RESET} {Style.DIM}for local models, projects, and tools{Style.RESET}")
    print()
    print(f"  {Style.DIM}Orchestrator{Style.RESET}  {Style.GREEN}{model_label(orchestrator_model)}{Style.RESET} {Style.DIM}({orchestrator_model}){Style.RESET}")
    print(f"  {Style.DIM}Directory   {Style.RESET}  {cwd}")
    print(f"  {Style.DIM}Mode        {Style.RESET}  conversational agent with guarded tool access")
    print()
    print(f"  {Style.MAGENTA}Ask in plain English.{Style.RESET} Example: start my sports betting project")
    print(f"  {Style.DIM}{commands}{Style.RESET}")
    print()


def help_text():
    return f"""DevServer AI Terminal {__version__}

Usage:
  python ollama_cli.py [web]
  python ollama_cli.py --help
  python ollama_cli.py --version

Interactive commands:
  /tools          Show available tools
  /models         Show local Ollama models
  /model          Switch the active model
  /orchestrator   Switch the routing model
  /account        Manage browser login accounts
  /doctor         Diagnose local DevServer workflow
  /clear          Clear chat history
  /history        Show chat history
  /tool-log       Show tool activity
  /bye            Exit

Plain English examples:
  list files in this folder
  show git status
  read START.md
  check devserver
"""


def handle_cli_args(argv):
    args = list(argv)
    if not args:
        return None

    if args in (["-h"], ["--help"], ["help"]):
        print(help_text())
        return 0

    if args == ["--version"]:
        print(__version__)
        return 0

    unknown = " ".join(args)
    print(f"Unknown option: {unknown}", file=sys.stderr)
    print("Run `python ollama_cli.py --help` for usage.", file=sys.stderr)
    return 2


def main(argv=None):
    cli_result = handle_cli_args([] if argv is None else argv)
    if cli_result is not None:
        return cli_result

    try:
        registry = build_registry()
        models = list_models(get_models)

        if not models:
            return

        orchestrator_model = get_default_orchestrator(models)

        state = {
            "orchestrator_model": orchestrator_model,
            "active_model": orchestrator_model,
            "models": models,
            "history": [],
            "cwd": SESSION_START_DIR,
            "notes": {},
            "tool_log": [],
        }

        model_helpers = {
            "list_models": lambda: list_models(get_models),
            "execute_tool_call": lambda call: execute_tool_call(call, state, registry),
        }

        print_startup_banner(orchestrator_model, state["cwd"])
        prompt_session = create_prompt_session()

        while True:
            print(format_footer(state["active_model"], state["cwd"]))
            prompt = sanitize_user_prompt(read_user_prompt(state["active_model"], prompt_session))

            if not prompt:
                continue

            command_result = handle_command(prompt, state, registry, model_helpers)

            if command_result == "exit":
                break

            if command_result == "handled":
                continue

            simple_response = simple_chat_response(prompt)
            if simple_response:
                state["history"].append({"role": "user", "content": prompt})
                state["history"].append({"role": "assistant", "content": simple_response})
                print_assistant_message(state["active_model"], simple_response)
                continue

            state["history"].append({"role": "user", "content": prompt})
            with WorkingStatus():
                decision = choose_model(
                    state["orchestrator_model"],
                    state["models"],
                    state["history"],
                )

            selected_model, is_handoff = select_turn_model(decision, state["active_model"])

            handoff_prompt = None
            if is_handoff:
                reason = decision.get("reason", "better suited for this turn")
                print(f"\n{dim('Routing')} {success(model_label(selected_model))}: {reason}")
                handoff_prompt = decision.get("handoff_prompt", "")

            answer = run_agent_turn(
                selected_model,
                state["history"],
                state,
                registry,
                handoff_prompt,
            )

            if answer:
                state["history"].append({"role": "assistant", "content": answer})
    except KeyboardInterrupt:
        print("\nExiting...")
    except EOFError:
        print("\nExiting...")

    return 0
