"""Local Ollama AI terminal with guarded tool access."""

import os
import re
from pathlib import Path

__version__ = "0.2.0"


def _load_dotenv(path: Path) -> None:
    """Settings that shouldn't be public go in .env at the repo root (gitignored; see
    .env.example). Variables already set in the environment win over the file."""
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        match = re.match(r"\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*?)\s*$", line)
        if not match or line.lstrip().startswith("#"):
            continue
        key, value = match.groups()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        os.environ.setdefault(key, value)


_load_dotenv(Path(__file__).resolve().parent.parent / ".env")
