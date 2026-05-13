import os

from .display import format_prompt

try:
    from prompt_toolkit import PromptSession
    from prompt_toolkit.key_binding import KeyBindings
except ImportError:
    PromptSession = None
    KeyBindings = None


def create_prompt_session():
    if os.environ.get("DEVSERVER_PLAIN_INPUT") == "1":
        return None

    if PromptSession is None or KeyBindings is None:
        return None

    bindings = KeyBindings()

    @bindings.add("escape", "enter")
    def _(event):
        event.current_buffer.insert_text("\n")

    @bindings.add("escape", "j")
    def _(event):
        event.current_buffer.insert_text("\n")

    @bindings.add("enter")
    def _(event):
        event.current_buffer.validate_and_handle()

    try:
        return PromptSession(multiline=True, key_bindings=bindings)
    except Exception:
        return None


def read_user_prompt(model_name, session=None):
    prompt_text = format_prompt(model_name)

    if session is None:
        return input(prompt_text).strip()

    return session.prompt(prompt_text).strip()
