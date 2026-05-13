import re
import os
import shutil
import sys
import threading
import time
from pathlib import Path

try:
    import msvcrt
except ImportError:
    msvcrt = None


class Style:
    RESET = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    CYAN = "\033[36m"
    BLUE = "\033[34m"
    GREEN = "\033[32m"
    YELLOW = "\033[33m"
    MAGENTA = "\033[35m"
    RED = "\033[31m"
    WHITE = "\033[97m"


MODEL_LABELS = {
    "phi4-mini": "Phi",
    "phi4-mini:latest": "Phi",
    "llama3": "Llama",
    "llama3:latest": "Llama",
    "llama3.1": "Llama",
    "llama3.1:latest": "Llama",
    "llama3.2": "Llama",
    "llama3.2:latest": "Llama",
    "mistral": "Mistral",
    "mistral:latest": "Mistral",
    "qwen2.5": "Qwen",
    "qwen2.5:latest": "Qwen",
    "deepseek-r1": "DeepSeek",
    "deepseek-r1:latest": "DeepSeek",
    "codellama": "CodeLlama",
    "codellama:latest": "CodeLlama",
}


FILE_EXTENSIONS = {
    ".py",
    ".md",
    ".txt",
    ".json",
    ".toml",
    ".yaml",
    ".yml",
    ".js",
    ".ts",
    ".tsx",
    ".jsx",
    ".html",
    ".css",
    ".env",
}


def color(text, style):
    return f"{style}{text}{Style.RESET}"


def bold(text):
    return color(text, Style.BOLD)


def markdown_bold(text):
    return color(text, Style.BOLD + Style.WHITE)


def dim(text):
    return color(text, Style.DIM)


def file_name(name):
    return color(name, Style.CYAN)


def directory_name(name):
    return color(name, Style.YELLOW + Style.BOLD)


def command_name(name):
    return color(name, Style.MAGENTA)


def success(text):
    return color(text, Style.GREEN)


def warning(text):
    return color(text, Style.YELLOW)


def error(text):
    return color(text, Style.RED)


def model_label(model_name):
    if model_name in MODEL_LABELS:
        return MODEL_LABELS[model_name]

    base = model_name.split(":", 1)[0]
    if base in MODEL_LABELS:
        return MODEL_LABELS[base]

    cleaned = base.replace("-", " ").replace("_", " ").replace(".", " ")
    return "".join(part.capitalize() for part in cleaned.split()) or model_name


def compact_path(path):
    path = Path(path)
    home = Path.home()

    try:
        return f"~\\{path.relative_to(home)}"
    except ValueError:
        return str(path)


def terminal_width(default=100):
    width = shutil.get_terminal_size((default, 24)).columns
    return max(40, width)


def wrap_text(text, width=None):
    if width is None:
        width = max(20, terminal_width() - 2)
    else:
        width = max(8, width)
    wrapped_lines = []

    for raw_line in text.splitlines():
        if not raw_line.strip():
            wrapped_lines.append("")
            continue

        indent = re.match(r"^\s*", raw_line).group(0)
        words = raw_line.strip().split()
        current = indent

        for word in words:
            pending = word if current == indent else f" {word}"

            if len(current) + len(pending) > width and current.strip():
                wrapped_lines.append(current.rstrip())
                current = f"{indent}{word}"
            else:
                current += pending

        wrapped_lines.append(current.rstrip())

    return "\n".join(wrapped_lines)


def highlight_text(text):
    def highlight_token(match):
        token = match.group(0)
        lower = token.lower()

        if token.endswith(("/", "\\")):
            return directory_name(token)

        if any(lower.endswith(extension) for extension in FILE_EXTENSIONS):
            return file_name(token)

        return token

    parts = re.split(r"(`[^`]+`)", text)
    highlighted = []

    for part in parts:
        if part.startswith("`") and part.endswith("`"):
            highlighted.append(file_name(part[1:-1]))
        else:
            bold_parts = re.split(r"(\*\*.+?\*\*)", part)
            for bold_part in bold_parts:
                if bold_part.startswith("**") and bold_part.endswith("**"):
                    highlighted.append(markdown_bold(bold_part[2:-2]))
                else:
                    highlighted.append(
                        re.sub(
                            r"(?<!\w)([\w.\-\\/]+(?:/|\\)?)(?!\w)",
                            highlight_token,
                            bold_part,
                        )
                    )

    return "".join(highlighted)


def print_assistant_message(model_name, content):
    cleaned = content.strip()

    print()
    print(f"{success(model_label(model_name))}:")
    if cleaned:
        print(highlight_text(wrap_text(cleaned)))
    print()


def print_message_stats(stats):
    if not stats:
        return

    elapsed = stats.get("elapsed_seconds", 0)
    input_tokens = stats.get("input_tokens", 0)
    output_tokens = stats.get("output_tokens", 0)
    tokens_per_second = stats.get("tokens_per_second", 0)
    model_calls = stats.get("model_calls", 1)

    parts = [
        f"{tokens_per_second:.2f} tok/s",
        f"{input_tokens} in",
        f"{output_tokens} out",
        f"{elapsed:.2f}s",
    ]

    if model_calls > 1:
        parts.append(f"{model_calls} calls")

    print(f"{dim('|')} {dim(' | '.join(parts))} {dim('|')}")
    print()


class EscapeCancelWatcher:
    def __init__(self, cancel_event=None):
        self.cancel_event = cancel_event
        self.stop_event = threading.Event()
        self.thread = None

    def __enter__(self):
        if self.cancel_event is not None and msvcrt is not None:
            self.thread = threading.Thread(target=self._run, daemon=True)
            self.thread.start()
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=1)

    def _run(self):
        while not self.stop_event.is_set():
            if msvcrt.kbhit():
                key = msvcrt.getwch()
                if key == "\x1b":
                    self.cancel_event.set()
                    return
            time.sleep(0.05)


class WorkingStatus:
    FRAMES = ["working", "working.", "working..", "working..."]

    def __init__(self, cancel_event=None):
        self.stop_event = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.detail = ""
        self.detail_seen = set()
        self.rendered_lines = 0
        self.lock = threading.Lock()
        self.cancel_event = cancel_event
        self.web_terminal = os.environ.get("DEVSERVER_WEB_TERMINAL") == "1"
        self.web_rendered = False

    def __enter__(self):
        if self.web_terminal:
            self.render("working", "")
            return self
        else:
            self.thread.start()
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        if self.web_terminal:
            print("DEVSERVER_STATUS_CLEAR", flush=True)
            return
        self.stop_event.set()
        self.thread.join(timeout=1)
        self.clear()

    def update_detail(self, detail):
        cleaned = " ".join(detail.strip().split())
        if not cleaned:
            return
        should_render_web = False

        with self.lock:
            if cleaned in self.detail_seen:
                return

            self.detail_seen.add(cleaned)

            if self.detail and cleaned.startswith(self.detail):
                self.detail = cleaned
            elif self.detail and cleaned not in self.detail:
                separator = "" if self.detail.endswith((" ", "\n")) else " "
                self.detail = f"{self.detail}{separator}{cleaned}"
            else:
                self.detail = cleaned

            should_render_web = self.web_terminal

        if should_render_web:
            self.render("working", cleaned)
    def _run(self):
        index = 0
        while not self.stop_event.is_set():
            frame = self.FRAMES[index % len(self.FRAMES)]
            with self.lock:
                detail = self.detail
            self.render(frame, detail)
            index += 1
            time.sleep(0.45)

    def render(self, frame, detail):
        if self.web_terminal:
            if self.web_rendered and not detail:
                return

            message = detail or frame
            print(f"DEVSERVER_STATUS: {message}", flush=True)
            self.web_rendered = True
            return

        self.clear()
        lines = [dim(frame)]
        if self.cancel_event is not None:
            lines.append(dim("  Esc to stop"))

        if detail:
            wrapped = self.wrap_detail(detail)
            lines.extend(dim(f"  {line}") for line in wrapped)

        print("\n".join(lines), end="", flush=True)
        self.rendered_lines = len(lines)

    def clear(self):
        if self.rendered_lines <= 0:
            print("\r" + " " * 80 + "\r", end="", flush=True)
            sys.stdout.flush()
            return

        for line_index in range(self.rendered_lines):
            if line_index:
                print("\033[F", end="")
            print("\r\033[2K", end="")

        sys.stdout.flush()
        self.rendered_lines = 0

    @staticmethod
    def wrap_detail(detail):
        width = max(30, terminal_width() - 4)
        words = detail.split()
        lines = []
        current = ""

        for word in words:
            if len(current) + len(word) + 1 > width:
                if current:
                    lines.append(current)
                current = word
            else:
                current = f"{current} {word}".strip()

        if current:
            lines.append(current)

        return lines or [detail[:width]]


def format_prompt(model_name):
    return "> "


def format_footer(model_name, cwd):
    return f"{dim(model_label(model_name).lower() + ' default - ' + compact_path(cwd))}"


def print_status(label, value):
    print(f"  {dim(label):<20} {value}")
