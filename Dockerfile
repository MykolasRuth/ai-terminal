FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    OLLAMA_BASE_URL=http://host.docker.internal:11434 \
    DEVSERVER_START_CWD=/projects \
    DEVSERVER_PROJECTS_DIR=/projects

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY ai_terminal ./ai_terminal
COPY ollama_cli.py ./ollama_cli.py
COPY START.md ./START.md
COPY ARCHITECTURE.md ./ARCHITECTURE.md
COPY tools.md ./tools.md

WORKDIR /projects

EXPOSE 8787

ENTRYPOINT ["python", "/app/ollama_cli.py"]
