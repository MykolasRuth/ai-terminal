import os
from pathlib import Path

OLLAMA_BASE_URL = (
    os.environ.get("OLLAMA_BASE_URL")
    or os.environ.get("OLLAMA_HOST")
    or "http://localhost:11434"
).rstrip("/")
OLLAMA_URL = f"{OLLAMA_BASE_URL}/api/chat"
TAGS_URL = f"{OLLAMA_BASE_URL}/api/tags"
OLLAMA_CONNECT_TIMEOUT_SECONDS = 5
OLLAMA_READ_TIMEOUT_SECONDS = 60
OLLAMA_ORCHESTRATOR_READ_TIMEOUT_SECONDS = 20
OLLAMA_TAGS_TIMEOUT_SECONDS = 5
OLLAMA_STREAM_STALL_TIMEOUT_SECONDS = int(os.environ.get("DEVSERVER_STREAM_STALL_TIMEOUT", "45"))
OLLAMA_STREAM_MAX_SECONDS = int(os.environ.get("DEVSERVER_STREAM_MAX_SECONDS", "180"))
DEFAULT_ORCHESTRATOR_MODEL = "phi4-mini:latest"

APP_DIR = Path(__file__).resolve().parent.parent
SESSION_START_DIR = Path(os.environ.get("DEVSERVER_START_CWD") or Path.cwd()).resolve()
WORKSPACE_DIR = APP_DIR
PROJECTS_DIR = Path(os.environ.get("DEVSERVER_PROJECTS_DIR") or APP_DIR.parent).resolve()
MAX_FILE_CHARS = 12000
MAX_SEARCH_RESULTS = 50
MAX_TREE_ENTRIES = 500
MAX_TOOL_OUTPUT_CHARS = 20000
MAX_TOOL_ROUNDS = 8
MAX_TOOL_CALLS_PER_ROUND = 5

SKIP_DIRS = {".git", "venv", "__pycache__", ".pytest_cache", "node_modules"}
SECRET_NAME_PARTS = {
    "key",
    "token",
    "secret",
    "password",
    "cookie",
    "credential",
    "private",
    "id_rsa",
    "id_ed25519",
}
